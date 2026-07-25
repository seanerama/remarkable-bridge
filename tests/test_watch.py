"""Unit tests for the Outbound watcher (bridge/watch.py).

No cloud, no rmapi, no nightshift: a fake cloud client serves real device fixtures and the
submit boundary is a recording stub. Asserts the exact frozen ``note-submission`` argv/JSON,
dedup, submit-failure retry, empty-note skip, CloudUnreachable graceful handling, and that
``[watch] enabled`` OFF makes the daemon inert.
"""

from __future__ import annotations

import json
from pathlib import Path

from bridge.cloud import CloudEntry, CloudUnreachable
from bridge.state import State
from bridge.tablet import RawDoc, parse_doc
from bridge.watch import WatchConfig, main, run_once

REAL = Path(__file__).resolve().parent / "fixtures" / "real"
NOTEBOOK_UUID = "8352580e-521a-4084-ba0c-cd62bbd915f5"  # text + 2 handwriting images
ERASED_UUID = "b2258fba-865f-45cd-b94a-8d6abe83ef10"  # no text, no images


def _load_raw(uuid: str) -> RawDoc:
    pages = {}
    page_dir = REAL / uuid
    if page_dir.is_dir():
        for rm in sorted(page_dir.glob("*.rm")):
            pages[rm.name[: -len(".rm")]] = rm.read_bytes()
    return RawDoc(
        doc_id=uuid,
        metadata=(REAL / f"{uuid}.metadata").read_bytes(),
        content=(REAL / f"{uuid}.content").read_bytes(),
        pages=pages,
    )


class FakeCloudClient:
    """Serves fixture RawDocs by cloud path; no rmapi, no network."""

    def __init__(self, docs: dict[str, RawDoc], *, unreachable: bool = False):
        # docs: name -> RawDoc
        self._docs = docs
        self.unreachable = unreachable
        self.get_calls: list[str] = []

    def list_folder(self, folder: str) -> list[CloudEntry]:
        if self.unreachable:
            raise CloudUnreachable("cloud down")
        return [
            CloudEntry(name=name, type="document", path=f"{folder}/{name}")
            for name in self._docs
        ]

    def get(self, doc_path: str) -> RawDoc:
        self.get_calls.append(doc_path)
        name = doc_path.rsplit("/", 1)[-1]
        return self._docs[name]


class SubmitStub:
    """Records nightshift submit argv; returns a configurable exit code."""

    def __init__(self, code: int = 0):
        self.code = code
        self.calls: list[list[str]] = []

    def __call__(self, argv, *, cwd=None):
        self.calls.append(argv)
        return {"code": self.code, "stdout": "", "stderr": "boom" if self.code else ""}


def _config(tmp_path: Path, *, enabled: bool = True) -> WatchConfig:
    return WatchConfig(
        enabled=enabled,
        folder="/Outbound",
        rmapi_bin="rmapi",
        nightshift_bin="nightshift",
        staging_dir=tmp_path / "notes",
        poll_interval=1,
        state_path=tmp_path / "state.json",
    )


# --------------------------------------------------------------------------- #
# (a) a new note is extracted + submitted with the exact note-submission JSON
# --------------------------------------------------------------------------- #
def test_new_note_submitted_with_frozen_note_submission_json(tmp_path):
    client = FakeCloudClient({"Learn the basics": _load_raw(NOTEBOOK_UUID)})
    submit = SubmitStub(code=0)
    state = State(path=tmp_path / "state.json")
    config = _config(tmp_path)

    report = run_once(client=client, state=state, config=config, submit=submit)

    assert report.submitted == [NOTEBOOK_UUID]
    assert len(submit.calls) == 1
    argv = submit.calls[0]

    # Exact argv shape (contract note-submission).
    assert argv[0] == "nightshift"
    assert argv[1] == "submit"
    assert argv[2:4] == ["--type", "note-ingest"]
    assert argv[4] == "--params"

    params = json.loads(argv[5])
    assert set(params) == {"note_id", "doc_name", "source_folder", "text", "images_dir"}
    assert params["note_id"] == NOTEBOOK_UUID
    assert params["doc_name"] == "Learn the basics"
    assert params["source_folder"] == "/Outbound"
    assert params["text"]  # non-empty typed text
    # images_dir points at the per-note staging dir and holds ordered page-NN.png files.
    images_dir = Path(params["images_dir"])
    assert images_dir == config.staging_dir / NOTEBOOK_UUID
    pngs = sorted(p.name for p in images_dir.glob("*.png"))
    assert pngs == ["page-00.png", "page-01.png"]

    # State recorded after the successful submit.
    assert state.seen(parse_doc(_load_raw(NOTEBOOK_UUID)))


# --------------------------------------------------------------------------- #
# (b) a second cycle with no changes submits nothing (dedup)
# --------------------------------------------------------------------------- #
def test_second_cycle_dedups_and_submits_nothing(tmp_path):
    client = FakeCloudClient({"Learn the basics": _load_raw(NOTEBOOK_UUID)})
    submit = SubmitStub(code=0)
    state = State(path=tmp_path / "state.json")
    config = _config(tmp_path)

    run_once(client=client, state=state, config=config, submit=submit)
    submit.calls.clear()

    report2 = run_once(client=client, state=state, config=config, submit=submit)
    assert submit.calls == []
    assert report2.submitted == []
    assert report2.skipped_seen == [NOTEBOOK_UUID]


# --------------------------------------------------------------------------- #
# (c) a submit non-zero exit leaves state unrecorded (retry, no double-submit)
# --------------------------------------------------------------------------- #
def test_submit_failure_leaves_state_unrecorded_and_retries(tmp_path):
    client = FakeCloudClient({"Learn the basics": _load_raw(NOTEBOOK_UUID)})
    state = State(path=tmp_path / "state.json")
    config = _config(tmp_path)

    failing = SubmitStub(code=2)
    report = run_once(client=client, state=state, config=config, submit=failing)
    assert report.submitted == []
    assert report.errors  # logged
    assert not state.seen(parse_doc(_load_raw(NOTEBOOK_UUID)))  # NOT recorded
    assert not (tmp_path / "state.json").exists()  # nothing saved

    # Next cycle succeeds and submits again (the failed one retried, never lost).
    ok = SubmitStub(code=0)
    report2 = run_once(client=client, state=state, config=config, submit=ok)
    assert report2.submitted == [NOTEBOOK_UUID]
    assert len(ok.calls) == 1


# --------------------------------------------------------------------------- #
# a note with no text and no images is skipped (nothing to act on)
# --------------------------------------------------------------------------- #
def test_note_with_no_text_and_no_images_is_skipped(tmp_path):
    client = FakeCloudClient({"Notebook": _load_raw(ERASED_UUID)})
    submit = SubmitStub(code=0)
    state = State(path=tmp_path / "state.json")
    config = _config(tmp_path)

    report = run_once(client=client, state=state, config=config, submit=submit)
    assert submit.calls == []
    assert report.submitted == []
    assert report.skipped_empty == [ERASED_UUID]
    assert not state.seen(parse_doc(_load_raw(ERASED_UUID)))


# --------------------------------------------------------------------------- #
# CloudUnreachable -> logged, no state change, no crash
# --------------------------------------------------------------------------- #
def test_cloud_unreachable_is_logged_no_state_change(tmp_path):
    client = FakeCloudClient({"Learn the basics": _load_raw(NOTEBOOK_UUID)}, unreachable=True)
    submit = SubmitStub(code=0)
    state = State(path=tmp_path / "state.json")
    config = _config(tmp_path)

    report = run_once(client=client, state=state, config=config, submit=submit)  # no raise
    assert submit.calls == []
    assert report.submitted == []
    assert report.errors  # logged
    assert not (tmp_path / "state.json").exists()


def test_get_unreachable_on_one_doc_does_not_crash_cycle(tmp_path):
    class FlakyGet(FakeCloudClient):
        def get(self, doc_path):
            raise CloudUnreachable("mid-download drop")

    client = FlakyGet({"Learn the basics": _load_raw(NOTEBOOK_UUID)})
    submit = SubmitStub(code=0)
    state = State(path=tmp_path / "state.json")
    report = run_once(client=client, state=state, config=_config(tmp_path), submit=submit)
    assert report.submitted == []
    assert report.errors
    assert submit.calls == []


# --------------------------------------------------------------------------- #
# [watch] enabled OFF -> daemon inert
# --------------------------------------------------------------------------- #
def test_main_inert_when_watch_disabled(tmp_path, capsys):
    submit = SubmitStub(code=0)
    config = _config(tmp_path, enabled=False)

    # Returns immediately (no infinite loop), never submits, writes no state.
    main(config=config, submit=submit)

    assert submit.calls == []
    assert not (tmp_path / "state.json").exists()
    out = capsys.readouterr().out
    assert "disabled" in out.lower()


def test_config_load_defaults_watch_off():
    config = WatchConfig.load()
    assert config.enabled is False
    assert config.folder == "/Outbound"
    assert config.rmapi_bin  # non-empty default
    assert config.nightshift_bin
