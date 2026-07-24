"""Extract content from a pulled document.

Two content kinds come off a v6 ``.rm`` page (ADR-0001):

* **Typed text** — parsed with ``rmscene`` (``RootTextBlock`` → ``TextDocument``). This is
  the Stage 1 path and is always on.
* **Handwriting** — v6 ``.rm`` is *strokes*, not a PDF page, so there is nothing for
  PyMuPDF to rasterize directly. We walk the rmscene scene tree for live ``Line`` items,
  draw their polylines onto a PyMuPDF (``fitz``) page, and rasterize that to a monochrome
  PNG. The agent then *reads the handwriting straight off the image* (Claude reads
  handwriting from images — there is deliberately NO separate OCR pipeline). Approach (a)
  from the stage spec: rmscene stroke geometry → vector page → raster PNG. It needs no
  base PDF, so it works for pure-notebook pages, and pixel-perfect fidelity is not the
  goal — a legible raster is.

The handwriting render is gated by the ``render_handwriting`` flag (dark-launch
``EXTRACT_HANDWRITING``, default OFF). With it OFF, ``extract`` behaves byte-for-byte like
Stage 1/2: typed text only, ``page_images`` empty.
"""

from __future__ import annotations

import io
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from rmscene import RootTextBlock, read_blocks, read_tree
from rmscene.scene_items import Line
from rmscene.text import TextDocument

from . import tablet
from .tablet import RawDoc

logger = logging.getLogger(__name__)

# Rendering knobs. rM stroke coordinates are in device units; a 2x raster zoom keeps the
# handwriting legible to the model without ballooning the PNG. Margin/width are in the
# same device units. None of this needs to be pixel-perfect (stage spec).
_RENDER_ZOOM = 2.0
_RENDER_MARGIN = 24.0
_STROKE_WIDTH = 1.5


@dataclass
class ExtractResult:
    text: str | None  # extracted typed text, or None if the page has none
    page_images: list[Path] = field(default_factory=list)  # rendered handwriting PNGs, in order


# --------------------------------------------------------------------------- #
# Typed text (Stage 1 path — unchanged behavior)
# --------------------------------------------------------------------------- #
def _extract_rm_text(rm_bytes: bytes) -> str:
    """Pull typed text out of one v6 ``.rm`` blob using rmscene. Empty string if none."""
    paragraphs: list[str] = []
    try:
        blocks = read_blocks(io.BytesIO(rm_bytes))
        for block in blocks:
            if isinstance(block, RootTextBlock):
                doc = TextDocument.from_scene_item(block.value)
                for para in doc.contents:
                    paragraphs.append("".join(str(span) for span in para.contents))
    except Exception:
        # A page we cannot parse for typed text yields no text (not a crash); the agent
        # still gets a job and publishes a report (contract: agent-invocation).
        return ""
    return "\n".join(paragraphs).strip()


# --------------------------------------------------------------------------- #
# Page ordering + page-level tag routing (contract: tablet-document)
# --------------------------------------------------------------------------- #
def _parse_content(raw: RawDoc) -> dict:
    try:
        return json.loads(raw.content.decode("utf-8")) if raw.content else {}
    except (ValueError, UnicodeDecodeError):
        return {}


def _ordered_page_ids(raw: RawDoc, content: dict) -> list[str]:
    """Page ids in *document order* (the ``.content`` page list), restricted to pages we
    actually pulled. Any pulled page missing from the content list is appended
    deterministically so nothing is silently dropped. Single-page docs => the one page,
    identical to the old ``sorted(raw.pages)`` behavior.
    """
    order = [pid for pid in tablet.page_ids(content) if pid in raw.pages]
    order += [pid for pid in sorted(raw.pages) if pid not in order]
    return order


def _tagged_page_ids(content: dict, route_tags: set[str] | None) -> set[str]:
    """Page ids carrying a page-level route tag, read from the top-level ``pageTags`` array
    (real device schema — ``docs/device-schema.md``).

    The element shape is conventionally ``{"pageId", "name"}`` but the device was captured
    with empty tag arrays, so the wire shape is unconfirmed — we are defensive: only dict
    entries that expose BOTH a matching ``name`` and a ``pageId`` string can target a page.
    A bare-string page tag carries no page id, so it cannot be mapped and is treated as
    document-level (=> render the whole doc). Returns an empty set when nothing maps to a
    specific page.
    """
    if not route_tags:
        return set()
    matched: set[str] = set()
    page_tags = content.get("pageTags")
    if isinstance(page_tags, list):
        for entry in page_tags:
            if not isinstance(entry, dict):
                continue  # bare string => no pageId => document-level, handled by caller
            name = entry.get("name")
            page_id = entry.get("pageId")
            if (
                isinstance(name, str)
                and name.strip().lower() in route_tags
                and isinstance(page_id, str)
                and page_id
            ):
                matched.add(page_id)
    return matched


# --------------------------------------------------------------------------- #
# Handwriting render: rmscene strokes -> PyMuPDF vector page -> PNG
# --------------------------------------------------------------------------- #
def _page_strokes(rm_bytes: bytes) -> list[list[tuple[float, float]]]:
    """Live stroke polylines ``[[(x, y), ...], ...]`` from one ``.rm`` page.

    Robust by construction: an unparseable page, or a page whose strokes were all erased
    (only CRDT deletion tombstones remain — as on a genuinely blank page), yields ``[]``
    rather than raising. rmscene logs (not raises) when a newer firmware writes fields it
    does not model; those extra bytes do not affect the stroke geometry we read here.
    """
    try:
        tree = read_tree(io.BytesIO(rm_bytes))
    except Exception:
        return []
    strokes: list[list[tuple[float, float]]] = []
    for item in tree.walk():
        if isinstance(item, Line) and item.points:
            strokes.append([(p.x, p.y) for p in item.points])
    return strokes


def _render_page_png(rm_bytes: bytes, dest: Path) -> Path | None:
    """Render one page's strokes to a monochrome PNG at ``dest``.

    Returns ``dest`` on success, or ``None`` when the page has no strokes (a blank/erased
    page yields no image — never a crash). Approach (a): draw the stroke polylines onto a
    PyMuPDF page sized to their bounding box, then rasterize.
    """
    strokes = _page_strokes(rm_bytes)
    if not strokes:
        return None

    import fitz  # lazy import: the typed-text-only path must not require PyMuPDF at all

    xs = [x for stroke in strokes for (x, _) in stroke]
    ys = [y for stroke in strokes for (_, y) in stroke]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    width = max((max_x - min_x) + 2 * _RENDER_MARGIN, 1.0)
    height = max((max_y - min_y) + 2 * _RENDER_MARGIN, 1.0)

    doc = fitz.open()
    try:
        page = doc.new_page(width=width, height=height)
        shape = page.new_shape()
        for stroke in strokes:
            poly = [
                fitz.Point(x - min_x + _RENDER_MARGIN, y - min_y + _RENDER_MARGIN)
                for (x, y) in stroke
            ]
            if len(poly) == 1:
                poly = poly * 2  # a single-point stroke (a dot) needs two vertices to draw
            shape.draw_polyline(poly)
        shape.finish(color=(0, 0, 0), width=_STROKE_WIDTH)
        shape.commit()
        pix = page.get_pixmap(matrix=fitz.Matrix(_RENDER_ZOOM, _RENDER_ZOOM))
        dest.parent.mkdir(parents=True, exist_ok=True)
        pix.save(str(dest))
    finally:
        doc.close()
    return dest


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def extract(
    raw: RawDoc,
    *,
    render_handwriting: bool = False,
    out_dir: Path | None = None,
    route_tags: set[str] | None = None,
) -> ExtractResult:
    """Extract typed text (always) and, when enabled, rendered handwriting PNGs.

    Typed text is joined across all pages in document order. When ``render_handwriting`` is
    on and ``out_dir`` is given, each rendered page becomes an ordered ``page-NN.png``:

    * If a **page-level** route tag targets specific pages (top-level ``pageTags``), only
      those pages are rendered.
    * Otherwise (document-level tag, or none) the whole document is rendered.

    Blank/erased pages produce no image (and no crash), so the resulting ``page_images``
    list is contiguous and in document order. With ``render_handwriting`` OFF this is
    byte-for-byte the Stage 1/2 result: typed text only, ``page_images`` empty.
    """
    content = _parse_content(raw)
    order = _ordered_page_ids(raw, content)

    chunks: list[str] = []
    for page_id in order:
        page_text = _extract_rm_text(raw.pages[page_id])
        if page_text:
            chunks.append(page_text)
    text = "\n\n".join(chunks).strip() or None

    page_images: list[Path] = []
    if render_handwriting and out_dir is not None:
        targeted = _tagged_page_ids(content, route_tags)
        pages_to_render = [p for p in order if p in targeted] if targeted else order
        seq = 0
        for page_id in pages_to_render:
            dest = Path(out_dir) / f"page-{seq:02d}.png"
            try:
                rendered = _render_page_png(raw.pages[page_id], dest)
            except Exception as exc:  # a bad page must never sink the whole job
                logger.warning("handwriting render failed for page %s: %r", page_id, exc)
                rendered = None
            if rendered is not None:
                page_images.append(rendered)
                seq += 1

    return ExtractResult(text=text, page_images=page_images)
