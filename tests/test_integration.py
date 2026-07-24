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

from bridge.clock import FixedClock
from bridge.publish import RESPONSES_FOLDER, ReportLabRenderer
from bridge.state import State
from bridge.watcher import Config, run_once

from .fakes import FakeTabletClient, StubAgentRunner

FIXED_DAY = date(2026, 7, 24)
DOC_ID = "3f2a9c10-1111-4a2b-8c3d-000000000001"
EXPECTED_NAME = "Re: Q3 Roadmap Notes (2026-07-24)"


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
