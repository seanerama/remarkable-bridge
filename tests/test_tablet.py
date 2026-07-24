"""Unit tests for the tablet-document contract reader (tag shapes + routing + hashing)."""

from __future__ import annotations

import json

from bridge.tablet import (
    RawDoc,
    compute_content_hash,
    is_routable,
    parse_doc,
    read_tags,
    route_for,
)


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
