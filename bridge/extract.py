"""Extract content from a pulled document.

Stage 1 scope: **typed text only** from v6 ``.rm`` pages via ``rmscene`` (ADR-0001).
Handwriting page rendering (PyMuPDF → PNG) is a later stage — the ``page_images`` field
is present in the result shape so the ``agent-invocation`` contract stays stable, but it
is empty here.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path

from rmscene import RootTextBlock, read_blocks
from rmscene.text import TextDocument

from .tablet import RawDoc


@dataclass
class ExtractResult:
    text: str | None  # extracted typed text, or None if the page has none
    page_images: list[Path] = field(default_factory=list)  # rendered PNGs (later stage)


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


def extract(raw: RawDoc) -> ExtractResult:
    """Extract typed text across all pages of a document, in page order."""
    chunks: list[str] = []
    for page_id in sorted(raw.pages):
        page_text = _extract_rm_text(raw.pages[page_id])
        if page_text:
            chunks.append(page_text)
    text = "\n\n".join(chunks).strip()
    return ExtractResult(text=text or None)
