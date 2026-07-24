"""Unit tests for the bridge-state contract: atomic write + (uuid, content_hash) dedup."""

from __future__ import annotations

import json

from bridge.state import State
from bridge.tablet import TabletDoc


def _doc(doc_id="d1", content_hash="hashA", mtime=1690000000000) -> TabletDoc:
    return TabletDoc(
        id=doc_id,
        visible_name="Notes",
        parent="",
        type="DocumentType",
        last_modified=mtime,
        deleted=False,
        tags=["review"],
        content_hash=content_hash,
    )


def test_record_and_seen_roundtrip(tmp_path):
    path = tmp_path / "state.json"
    state = State.load(path)
    doc = _doc()

    assert state.seen(doc) is False
    state.record(doc, "review", "ok")
    state.save()

    reloaded = State.load(path)
    assert reloaded.seen(doc) is True


def test_dedup_key_is_uuid_and_hash(tmp_path):
    """Same uuid but a new content_hash (re-tag/edit) is NOT seen → reprocessed."""
    state = State.load(tmp_path / "state.json")
    original = _doc(content_hash="hashA")
    state.record(original, "review", "ok")

    assert state.seen(_doc(content_hash="hashA")) is True
    assert state.seen(_doc(content_hash="hashB")) is False  # edited → hash changed


def test_saved_schema_matches_contract(tmp_path):
    path = tmp_path / "state.json"
    state = State.load(path)
    state.record(_doc(), "review", "ok")
    state.save()

    data = json.loads(path.read_text())
    assert data["version"] == 1
    entry = data["documents"]["d1"]
    assert set(entry) == {"last_hash", "last_mtime", "route", "status", "processed_at"}
    assert entry["processed_at"].endswith("Z")


def test_atomic_write_survives_and_leaves_no_temp(tmp_path):
    path = tmp_path / "state.json"
    state = State.load(path)
    state.record(_doc(), "review", "ok")
    state.save()
    state.record(_doc(doc_id="d2", content_hash="hashC"), "review", "failed")
    state.save()

    # No leftover temp files from the temp+fsync+os.replace dance.
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "state.json"]
    assert leftovers == []

    data = json.loads(path.read_text())
    assert set(data["documents"]) == {"d1", "d2"}


def test_load_missing_file_is_empty(tmp_path):
    state = State.load(tmp_path / "does-not-exist.json")
    assert state.documents == {}
    assert state.seen(_doc()) is False
