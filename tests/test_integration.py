"""The one walking-skeleton integration test.

Drives the whole review pipeline against checked-in fixtures with only the two external
boundaries faked (FakeTabletClient + StubAgentRunner) and a FixedClock. Real modules:
tablet reader, extract (real rmscene v6 parse), publish (reportlab fallback renderer),
state, watcher.

Asserts the four acceptance conditions:
  (a) a PDF named `Re: <name> (YYYY-MM-DD)` is produced,
  (b) the fake client received an upload to /Claude/Responses,
  (c) state.json records the doc,
  (d) a SECOND cycle with unchanged fixtures processes NOTHING (dedup, acceptance #3).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from bridge.clock import FixedClock
from bridge.publish import RESPONSES_FOLDER, ReportLabRenderer
from bridge.state import State
from bridge.tablet import RawDoc, TabletUnreachable
from bridge.watcher import Config, run_once

from .fakes import FakeTabletClient, StubAgentRunner

FIXED_DAY = date(2026, 7, 24)
DOC_ID = "3f2a9c10-1111-4a2b-8c3d-000000000001"
EXPECTED_NAME = "Re: Q3 Roadmap Notes (2026-07-24)"

REAL_DIR = Path(__file__).resolve().parent / "fixtures" / "real"
HANDWRITTEN_SRC_DOC = "8352580e-521a-4084-ba0c-cd62bbd915f5"
HANDWRITTEN_PAGES = [
    "869a62cc-6246-4d72-b37d-1498cbf9c06c",
    "c7418c51-56bf-4a7a-b79a-6312c8fc66bd",
]


def _config(tmp_path: Path) -> Config:
    cfg = Config.load()  # loads bridge/config.toml (real config)
    # Redirect all writable paths into the tmp dir so the test is hermetic.
    cfg.state_path = tmp_path / "state" / "state.json"
    cfg.out_dir = tmp_path / "out"
    cfg.logs_dir = tmp_path / "logs"
    cfg.workspace_root = tmp_path / "workspace"
    return cfg


def test_review_pipeline_end_to_end_and_dedup(tmp_path):
    client = FakeTabletClient()
    runner = StubAgentRunner()
    clock = FixedClock(FIXED_DAY)
    config = _config(tmp_path)
    state = State.load(config.state_path)
    renderer = ReportLabRenderer()  # headless, no native deps (WeasyPrint runs in CI/prod)

    # ---- Cycle 1: processes the review-tagged fixture end to end -------------
    report = run_once(
        client=client, runner=runner, clock=clock, state=state, config=config, renderer=renderer
    )

    assert report.processed == [EXPECTED_NAME]
    assert not report.errors

    # (a) a PDF with the frozen name is produced and is a real PDF.
    pdf_path = config.out_dir / f"{EXPECTED_NAME}.pdf"
    assert pdf_path.exists(), "PDF was not written"
    assert pdf_path.name == f"{EXPECTED_NAME}.pdf"
    assert pdf_path.read_bytes().startswith(b"%PDF")

    # (b) the fake client received an upload to /Claude/Responses.
    assert len(client.uploads) == 1
    upload = client.uploads[0]
    assert upload.visible_name == EXPECTED_NAME
    assert client.folders[RESPONSES_FOLDER] == upload.parent
    assert upload.pdf_bytes.startswith(b"%PDF")

    # (c) state.json records the doc (persisted atomically).
    assert config.state_path.exists()
    persisted = json.loads(config.state_path.read_text())
    assert persisted["version"] == 1
    assert DOC_ID in persisted["documents"]
    entry = persisted["documents"][DOC_ID]
    assert entry["route"] == "review"
    assert entry["status"] == "ok"
    assert entry["last_hash"]

    # The stub agent was actually invoked with the extracted typed text.
    assert len(runner.calls) == 1
    assert runner.calls[0].text is not None
    assert "proposals" in runner.calls[0].text.lower()

    # ---- Cycle 2: unchanged fixtures → dedup, nothing processed -------------
    state2 = State.load(config.state_path)
    report2 = run_once(
        client=client, runner=runner, clock=clock, state=state2, config=config, renderer=renderer
    )

    assert report2.processed == []
    assert report2.skipped_seen == [DOC_ID]
    assert len(client.uploads) == 1  # no new upload
    assert len(runner.calls) == 1  # agent not called again


def _handwritten_review_raw() -> RawDoc:
    """A review-tagged, handwritten document built from real device stroke pages.

    The real onboarding notebook carries no tag, so we wrap its real ``.rm`` stroke bytes
    in a doc that carries a document-level ``review`` tag — exercising the full route.
    """
    content = {
        "formatVersion": 2,
        "tags": [{"name": "review"}],  # document-level route tag
        "pageTags": [],
        "cPages": {"pages": [{"id": pid} for pid in HANDWRITTEN_PAGES]},
    }
    metadata = {
        "visibleName": "Handwritten Meeting Notes",
        "type": "DocumentType",
        "parent": "",
        "lastModified": "1784918546774",
    }
    pages = {
        pid: (REAL_DIR / HANDWRITTEN_SRC_DOC / f"{pid}.rm").read_bytes()
        for pid in HANDWRITTEN_PAGES
    }
    return RawDoc(
        doc_id="hw-review-0001",
        metadata=json.dumps(metadata).encode("utf-8"),
        content=json.dumps(content).encode("utf-8"),
        pages=pages,
    )


def test_review_pipeline_with_handwriting_flag_on(tmp_path):
    """Acceptance: a handwritten review doc + EXTRACT_HANDWRITING ON -> the agent job
    carries N rendered page images, and a PDF is still published."""
    client = FakeTabletClient(raw_docs=[_handwritten_review_raw()])
    runner = StubAgentRunner()
    config = _config(tmp_path)
    config.extract_handwriting = True  # flag ON
    state = State.load(config.state_path)

    report = run_once(
        client=client,
        runner=runner,
        clock=FixedClock(FIXED_DAY),
        state=state,
        config=config,
        renderer=ReportLabRenderer(),
    )

    assert not report.errors
    assert report.processed == ["Re: Handwritten Meeting Notes (2026-07-24)"]

    # The stub AgentRunner observed the rendered handwriting: N page images, all real PNGs.
    assert len(runner.calls) == 1
    images = runner.calls[0].page_images
    assert len(images) == len(HANDWRITTEN_PAGES)
    for img in images:
        assert img.exists()
        assert img.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")

    # A report PDF was still produced and uploaded.
    assert len(client.uploads) == 1
    assert client.uploads[0].pdf_bytes.startswith(b"%PDF")


def test_handwriting_flag_off_yields_no_images(tmp_path):
    """Regression: same handwritten doc with the flag OFF -> zero page_images (Stage 1)."""
    client = FakeTabletClient(raw_docs=[_handwritten_review_raw()])
    runner = StubAgentRunner()
    config = _config(tmp_path)
    assert config.extract_handwriting is False  # default OFF from config.toml
    state = State.load(config.state_path)

    run_once(
        client=client,
        runner=runner,
        clock=FixedClock(FIXED_DAY),
        state=state,
        config=config,
        renderer=ReportLabRenderer(),
    )

    assert len(runner.calls) == 1
    assert runner.calls[0].page_images == []


class _UnreachableClient(FakeTabletClient):
    """A client whose scan fails the way an offline tablet does (SSH transport error)."""

    def scan_raw(self):  # type: ignore[override]
        raise TabletUnreachable("ssh to 'remarkable' failed: Connection refused")


def test_unreachable_tablet_surfaces_typed_signal_without_state_mutation(tmp_path):
    """Acceptance #4 (partial): unreachable tablet -> TabletUnreachable, no state written,
    no reprocessing. The watcher's main loop catches this and retries next cycle."""
    config = _config(tmp_path)
    state = State.load(config.state_path)

    with pytest.raises(TabletUnreachable):
        run_once(
            client=_UnreachableClient(raw_docs=[]),
            runner=StubAgentRunner(),
            clock=FixedClock(FIXED_DAY),
            state=state,
            config=config,
            renderer=ReportLabRenderer(),
        )

    # No state mutation: nothing recorded in memory, nothing persisted to disk.
    assert state.documents == {}
    assert not config.state_path.exists()

    # TabletUnreachable is an Exception, so watcher.main's guard degrades gracefully
    # (log + retry next cycle) rather than crashing the daemon.
    assert issubclass(TabletUnreachable, Exception)
