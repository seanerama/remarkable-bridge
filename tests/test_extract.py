"""Focused test: real rmscene v6 typed-text extraction from the checked-in fixture."""

from __future__ import annotations

from bridge.extract import extract

from .fakes import load_raw_docs

DOC_ID = "3f2a9c10-1111-4a2b-8c3d-000000000001"


def test_extract_typed_text_from_fixture():
    raw = next(d for d in load_raw_docs() if d.doc_id == DOC_ID)
    result = extract(raw)
    assert result.text is not None
    assert "proposals" in result.text.lower()
    assert "review route" in result.text.lower()
    assert result.page_images == []  # handwriting render is a later stage


def test_extract_empty_pages_yields_none():
    from bridge.tablet import RawDoc

    result = extract(RawDoc(doc_id="x", metadata=b"{}", content=b"{}", pages={}))
    assert result.text is None
