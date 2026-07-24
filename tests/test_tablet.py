"""Unit tests for the tablet-document contract reader (tag shapes + routing + hashing)."""

from __future__ import annotations

import json
from pathlib import Path

from bridge.tablet import (
    RawDoc,
    _parse_cat_stream,
    _parse_stat_lines,
    compute_content_hash,
    is_routable,
    page_ids,
    parse_doc,
    read_tags,
    route_for,
)

REAL_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "real"
FV2_ID = "b2258fba-865f-45cd-b94a-8d6abe83ef10"  # formatVersion 2, notebook
FV1_ID = "e49bb79e-ffc1-4b5f-8290-f64628e396cd"  # formatVersion 1, pdf


def _raw(metadata: dict, content: dict, pages: dict[str, bytes] | None = None) -> RawDoc:
    return RawDoc(
        doc_id="u1",
        metadata=json.dumps(metadata).encode(),
        content=json.dumps(content).encode(),
        pages=pages or {},
    )


# ---- tag reader variant shapes (contract: tolerate documented variants) ------
def test_v6_document_level_tag():
    content = {"tags": [{"name": "Review", "timestamp": 1}]}
    assert read_tags(content) == ["review"]  # lowercased


def test_v6_page_level_tag():
    content = {"cPages": {"pages": [{"id": "p1", "tags": [{"name": "execute"}]}]}}
    assert read_tags(content) == ["execute"]


def test_bare_string_list_shape():
    assert read_tags({"tags": ["Review", "todo"]}) == ["review", "todo"]


def test_legacy_pagetags_shape():
    assert read_tags({"pageTags": [{"name": "review"}]}) == ["review"]


def test_union_and_dedup_document_and_page():
    content = {
        "tags": [{"name": "review"}],
        "cPages": {"pages": [{"id": "p1", "tags": [{"name": "review"}, {"name": "idea"}]}]},
    }
    assert read_tags(content) == ["review", "idea"]


def test_unknown_fields_ignored():
    content = {"tags": [{"name": "review", "color": "red", "extra": 1}], "mystery": {"x": 1}}
    assert read_tags(content) == ["review"]


def test_missing_tags_is_empty():
    assert read_tags({"formatVersion": 2}) == []


# ---- parse_doc + routing -----------------------------------------------------
def test_parse_doc_normalizes_metadata():
    doc = parse_doc(
        _raw(
            {
                "visibleName": "My Doc",
                "type": "DocumentType",
                "parent": "",
                "deleted": False,
                "lastModified": "1690000000000",
            },
            {"tags": [{"name": "review"}]},
        )
    )
    assert doc.visible_name == "My Doc"
    assert doc.last_modified == 1690000000000
    assert doc.deleted is False
    assert doc.tags == ["review"]


def test_trash_parent_counts_as_deleted():
    doc = parse_doc(_raw({"visibleName": "X", "parent": "trash"}, {"tags": [{"name": "review"}]}))
    assert doc.deleted is True
    assert is_routable(doc, {"review"}) is False


def test_deleted_flag_excluded_from_routing():
    doc = parse_doc(_raw({"visibleName": "X", "deleted": True}, {"tags": [{"name": "review"}]}))
    assert is_routable(doc, {"review"}) is False


def test_routable_case_insensitive():
    doc = parse_doc(_raw({"visibleName": "X", "type": "DocumentType"}, {"tags": [{"name": "REVIEW"}]}))
    assert is_routable(doc, {"review"}) is True
    assert route_for(doc, {"review"}) == "review"


def test_collection_type_not_routable():
    doc = parse_doc(_raw({"visibleName": "Folder", "type": "CollectionType"}, {"tags": [{"name": "review"}]}))
    assert is_routable(doc, {"review"}) is False


def test_untagged_doc_not_routable():
    doc = parse_doc(_raw({"visibleName": "X", "type": "DocumentType"}, {"tags": []}))
    assert is_routable(doc, {"review"}) is False


# ---- content hash drives dedup ----------------------------------------------
def test_content_hash_changes_when_page_edited():
    base_meta = {"visibleName": "X"}
    base_content = {"tags": [{"name": "review"}]}
    h1 = compute_content_hash(_raw(base_meta, base_content, {"p1": b"strokes-v1"}))
    h2 = compute_content_hash(_raw(base_meta, base_content, {"p1": b"strokes-v2"}))
    assert h1 != h2


def test_content_hash_changes_when_retagged():
    meta = {"visibleName": "X"}
    h1 = compute_content_hash(_raw(meta, {"tags": []}, {"p1": b"s"}))
    h2 = compute_content_hash(_raw(meta, {"tags": [{"name": "review"}]}, {"p1": b"s"}))
    assert h1 != h2


# ---- tag shapes: verified device layout (top-level tags[] + pageTags[]) -------
def test_top_level_pagetags_bare_strings():
    # pageTags element shape is device-unconfirmed; accept bare strings too.
    assert read_tags({"pageTags": ["Execute", "review"]}) == ["execute", "review"]


def test_top_level_pagetags_objects_with_pageid():
    # Convention (not device-confirmed): {"pageId","name"} — extra keys ignored.
    content = {"pageTags": [{"pageId": "p1", "name": "Execute"}]}
    assert read_tags(content) == ["execute"]


def test_doc_level_tags_only():
    assert read_tags({"tags": [{"name": "review"}], "pageTags": []}) == ["review"]


def test_page_level_via_pagetags_only():
    assert read_tags({"tags": [], "pageTags": [{"name": "execute"}]}) == ["execute"]


def test_union_top_level_tags_and_pagetags():
    content = {"tags": [{"name": "review"}], "pageTags": [{"name": "execute"}]}
    assert read_tags(content) == ["review", "execute"]


def test_unknown_element_shape_does_not_hard_fail():
    # Numbers / nested lists / null elements are ignored, not fatal.
    content = {"tags": [123, None, ["x"], {"noname": 1}], "pageTags": [{"name": "review"}]}
    assert read_tags(content) == ["review"]


# ---- page enumeration across BOTH real formatVersions ------------------------
def test_page_ids_format_version_2_objects():
    content = {"cPages": {"pages": [{"id": "p1", "template": "Blank"}, {"id": "p2"}]}}
    assert page_ids(content) == ["p1", "p2"]


def test_page_ids_format_version_1_flat_strings():
    content = {"pages": ["p1", "p2", "p3"], "redirectionPageMap": [0, 1, 2]}
    assert page_ids(content) == ["p1", "p2", "p3"]


def test_page_ids_unknown_shape_is_empty_not_crash():
    assert page_ids({"formatVersion": 2}) == []
    assert page_ids({"pages": "not-a-list"}) == []


# ---- REAL device fixtures: both formatVersions parse without crashing ---------
def _real_raw(doc_id: str) -> RawDoc:
    return RawDoc(
        doc_id=doc_id,
        metadata=(REAL_FIXTURES / f"{doc_id}.metadata").read_bytes(),
        content=(REAL_FIXTURES / f"{doc_id}.content").read_bytes(),
    )


def test_real_format_version_2_notebook_parses():
    raw = _real_raw(FV2_ID)
    content = json.loads(raw.content)
    assert content["formatVersion"] == 2
    doc = parse_doc(raw)  # must not crash
    assert doc.type == "DocumentType"
    assert doc.parent == ""  # root
    assert doc.deleted is False
    assert doc.last_modified == 1784918759384  # string ms-epoch coerced to int
    assert doc.tags == []  # tag arrays empty on capture
    assert page_ids(content) == ["ebb2c10a-20e2-40d4-bd1d-a8245583a2cd"]  # cPages objects


def test_real_format_version_1_pdf_parses():
    raw = _real_raw(FV1_ID)
    content = json.loads(raw.content)
    assert content["formatVersion"] == 1
    doc = parse_doc(raw)  # must not crash on the flat-array shape
    assert doc.type == "DocumentType"
    assert doc.tags == []
    ids = page_ids(content)  # flat array of id strings
    assert len(ids) == 5
    assert all(isinstance(i, str) for i in ids)


# ---- pure transport-parse helpers (no SSH) -----------------------------------
def test_parse_cat_stream_roundtrip():
    stream = b"\x1ea.metadata\x1e" + b'{"x":1}' + b"\x1ea.content\x1e" + b'{"y":2}'
    out = _parse_cat_stream(stream)
    assert out == {"a.metadata": b'{"x":1}', "a.content": b'{"y":2}'}


def test_parse_cat_stream_missing_file_is_empty_bytes():
    stream = b"\x1ea.metadata\x1e" + b"{}" + b"\x1eb.content\x1e"  # b had no bytes
    out = _parse_cat_stream(stream)
    assert out["a.metadata"] == b"{}"
    assert out["b.content"] == b""


def test_parse_stat_lines_selects_only_newer_and_advances_marker():
    out = (
        b"100 /xo/aaa.metadata\n"
        b"250 /xo/bbb.content\n"
        b"175 /xo/ccc.metadata\n"
    )
    changed, max_epoch = _parse_stat_lines(out, marker=150)
    assert changed == {"bbb", "ccc"}  # 100 <= 150 excluded
    assert max_epoch == 250


def test_parse_stat_lines_first_run_marker_zero_selects_all():
    out = b"100 /xo/aaa.metadata\n50 /xo/aaa.content\n"
    changed, max_epoch = _parse_stat_lines(out, marker=0)
    assert changed == {"aaa"}
    assert max_epoch == 100
