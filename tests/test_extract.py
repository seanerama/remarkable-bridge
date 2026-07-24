"""Extraction tests: rmscene typed-text (Stage 1, unchanged) + Stage 3 handwriting render.

The handwriting tests run against REAL device pages pulled read-only into
``tests/fixtures/real/`` (see that dir's README): a genuinely handwritten notebook page
(live strokes) and a blank/erased page (only CRDT deletion tombstones remain).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bridge.extract import _ordered_page_ids, _parse_content, _tagged_page_ids, extract
from bridge.tablet import RawDoc

from .fakes import load_raw_docs

DOC_ID = "3f2a9c10-1111-4a2b-8c3d-000000000001"

REAL_DIR = Path(__file__).resolve().parent / "fixtures" / "real"
# "Learn the basics" (formatVersion 2, 7 pages); two pages carry real handwriting.
HANDWRITTEN_DOC = "8352580e-521a-4084-ba0c-cd62bbd915f5"
PAGE_EARLY = "869a62cc-6246-4d72-b37d-1498cbf9c06c"  # doc index 4, 91 strokes
PAGE_LATE = "c7418c51-56bf-4a7a-b79a-6312c8fc66bd"  # doc index 5, 143 strokes
# A real page whose strokes were all erased -> parses fine, renders no image.
ERASED_DOC = "b2258fba-865f-45cd-b94a-8d6abe83ef10"
ERASED_PAGE = "ebb2c10a-20e2-40d4-bd1d-a8245583a2cd"

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _real_rm(doc: str, page: str) -> bytes:
    return (REAL_DIR / doc / f"{page}.rm").read_bytes()


def _handwritten_raw(pages: list[str]) -> RawDoc:
    """A RawDoc for the real handwritten doc, carrying the named pages' real ``.rm`` bytes."""
    return RawDoc(
        doc_id=HANDWRITTEN_DOC,
        metadata=(REAL_DIR / f"{HANDWRITTEN_DOC}.metadata").read_bytes(),
        content=(REAL_DIR / f"{HANDWRITTEN_DOC}.content").read_bytes(),
        pages={p: _real_rm(HANDWRITTEN_DOC, p) for p in pages},
    )


def _is_valid_png(path: Path) -> bool:
    import fitz

    data = path.read_bytes()
    if not data.startswith(PNG_MAGIC):
        return False
    pix = fitz.Pixmap(str(path))  # raises if not a real image
    return pix.width > 0 and pix.height > 0


# --------------------------------------------------------------------------- #
# Typed text (Stage 1) — behavior unchanged
# --------------------------------------------------------------------------- #
def test_extract_typed_text_from_fixture():
    raw = next(d for d in load_raw_docs() if d.doc_id == DOC_ID)
    result = extract(raw)
    assert result.text is not None
    assert "proposals" in result.text.lower()
    assert "review route" in result.text.lower()
    assert result.page_images == []  # flag OFF by default -> no render


def test_extract_empty_pages_yields_none():
    result = extract(RawDoc(doc_id="x", metadata=b"{}", content=b"{}", pages={}))
    assert result.text is None
    assert result.page_images == []


# --------------------------------------------------------------------------- #
# Handwriting render (Stage 3) — flag ON
# --------------------------------------------------------------------------- #
def test_handwriting_render_produces_valid_png(tmp_path):
    """A real handwritten page + flag ON -> a valid, non-empty PNG the agent can read."""
    raw = _handwritten_raw([PAGE_LATE])
    result = extract(raw, render_handwriting=True, out_dir=tmp_path, route_tags={"review"})

    assert len(result.page_images) == 1
    png = result.page_images[0]
    assert png.exists()
    assert png.stat().st_size > 1000  # a real raster, not an empty stub
    assert _is_valid_png(png)


def test_handwriting_flag_off_is_byte_for_byte_stage1(tmp_path):
    """Regression: flag OFF -> page_images empty and no files written, even with out_dir."""
    raw = _handwritten_raw([PAGE_LATE])
    default = extract(raw)
    off = extract(raw, render_handwriting=False, out_dir=tmp_path, route_tags={"review"})

    assert default.page_images == []
    assert off.page_images == []
    assert default.text == off.text
    assert list(tmp_path.iterdir()) == []  # nothing rendered when the kill-switch is off


def test_erased_page_yields_no_image_no_crash(tmp_path):
    """A real page whose strokes were all erased renders no image (and must not crash)."""
    raw = RawDoc(
        doc_id=ERASED_DOC,
        metadata=(REAL_DIR / f"{ERASED_DOC}.metadata").read_bytes(),
        content=(REAL_DIR / f"{ERASED_DOC}.content").read_bytes(),
        pages={ERASED_PAGE: _real_rm(ERASED_DOC, ERASED_PAGE)},
    )
    result = extract(raw, render_handwriting=True, out_dir=tmp_path, route_tags={"review"})
    assert result.page_images == []
    assert list(tmp_path.iterdir()) == []


def test_multi_page_render_is_ordered_and_contiguous(tmp_path):
    """A multi-page doc -> ordered page-NN.png images following DOCUMENT order."""
    raw = _handwritten_raw([PAGE_EARLY, PAGE_LATE])
    result = extract(raw, render_handwriting=True, out_dir=tmp_path, route_tags={"review"})

    assert [p.name for p in result.page_images] == ["page-00.png", "page-01.png"]
    assert all(_is_valid_png(p) for p in result.page_images)


def test_page_order_follows_content_not_sorted_ids():
    """Ordering comes from the .content page list, not sorted page ids (synthetic case)."""
    raw = _handwritten_raw([PAGE_EARLY, PAGE_LATE])
    content = _parse_content(raw)
    order = _ordered_page_ids(raw, content)
    # In the real doc, PAGE_EARLY precedes PAGE_LATE; sorted ids happen to agree, so
    # force a reversed content list and confirm the reader honors it.
    reversed_content = {"formatVersion": 2, "cPages": {"pages": [{"id": PAGE_LATE}, {"id": PAGE_EARLY}]}}
    reversed_order = _ordered_page_ids(raw, reversed_content)
    assert order == [PAGE_EARLY, PAGE_LATE]
    assert reversed_order == [PAGE_LATE, PAGE_EARLY]


def test_page_level_tag_routes_only_tagged_page(tmp_path):
    """A page-level tag (top-level pageTags -> pageId) renders ONLY that page."""
    raw = _handwritten_raw([PAGE_EARLY, PAGE_LATE])
    # Inject a page-level tag targeting just the late page.
    content = _parse_content(raw)
    content["pageTags"] = [{"pageId": PAGE_LATE, "name": "review"}]
    raw.content = json.dumps(content).encode("utf-8")

    result = extract(raw, render_handwriting=True, out_dir=tmp_path, route_tags={"review"})
    assert len(result.page_images) == 1  # only the tagged page, not both


def test_tagged_page_ids_is_defensive():
    """pageTags reader tolerates bare strings / missing pageId (=> document-level)."""
    # Bare-string page tag: no pageId -> maps to nothing -> whole-doc (empty set).
    assert _tagged_page_ids({"pageTags": ["review"]}, {"review"}) == set()
    # dict with matching name + pageId -> mapped.
    assert _tagged_page_ids(
        {"pageTags": [{"pageId": "p1", "name": "Review"}]}, {"review"}
    ) == {"p1"}
    # non-matching name ignored; junk ignored.
    assert _tagged_page_ids(
        {"pageTags": [{"pageId": "p1", "name": "other"}, 42, {"name": "review"}]}, {"review"}
    ) == set()
    # no route tags -> never targets a page.
    assert _tagged_page_ids({"pageTags": [{"pageId": "p1", "name": "review"}]}, None) == set()
