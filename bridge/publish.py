"""Publish an ``AgentResult`` as a PDF and upload it to the tablet (contract: response-publish).

Markdown → HTML → PDF. PDF rendering sits behind a small ``PdfRenderer`` seam:

* ``WeasyPrintRenderer`` — **production** (ADR-0001). Clean e-ink print CSS, but needs
  native cairo/pango/gobject libs (provisioned on the dev server + in CI). Imported
  lazily so the module loads even where those libs are absent.
* ``ReportLabRenderer`` — pure-Python fallback, **no native deps** (the fallback ADR-0001
  names). Used by the local test path so ``pytest`` is green without the native libs.

Uploads are **create-only** via ``TabletClient`` — each run is a new document; prior
responses are never deleted or overwritten (append-only history on the device).
"""

from __future__ import annotations

import html as html_lib
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol, runtime_checkable
from uuid import uuid4

import markdown as markdown_lib

from .agents import AgentResult
from .tablet import TabletClient, TabletDoc

RESPONSES_FOLDER = "/Claude/Responses"

# e-ink-friendly print CSS: single column, generous margins, high contrast (ADR-0001).
PRINT_CSS = """
@page { size: A4; margin: 2.2cm 2cm; }
body { font-family: Georgia, 'Times New Roman', serif; font-size: 12pt;
       line-height: 1.5; color: #000; max-width: 100%; }
h1, h2, h3 { color: #000; line-height: 1.25; margin-top: 1.2em; }
h1 { font-size: 20pt; } h2 { font-size: 16pt; } h3 { font-size: 13pt; }
code, pre { font-family: 'DejaVu Sans Mono', monospace; font-size: 10.5pt; }
pre { background: #f2f2f2; padding: 0.6em; white-space: pre-wrap; word-wrap: break-word; }
blockquote { border-left: 3px solid #000; margin-left: 0; padding-left: 1em; color: #222; }
.status { font-weight: bold; margin-bottom: 1em; }
"""


@dataclass
class PublishedDoc:
    pdf_path: Path
    visible_name: str
    tablet_parent: str
    uploaded: bool


@runtime_checkable
class PdfRenderer(Protocol):
    def render(self, html: str, pdf_path: Path) -> None:
        ...


class WeasyPrintRenderer:
    """Production renderer (ADR-0001). Needs native cairo/pango/gobject libs."""

    def render(self, html: str, pdf_path: Path) -> None:
        import weasyprint  # lazy: native libs only touched when actually rendering

        weasyprint.HTML(string=html).write_pdf(str(pdf_path))


class ReportLabRenderer:
    """Pure-Python fallback renderer — no native deps (ADR-0001 documented fallback).

    Renders a lightweight subset of the report markup so local tests produce a real,
    valid PDF without cairo/pango/gobject. Production keeps WeasyPrint for typography.
    """

    def render(self, html: str, pdf_path: Path) -> None:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import cm
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

        styles = getSampleStyleSheet()
        doc = SimpleDocTemplate(
            str(pdf_path),
            pagesize=A4,
            leftMargin=2 * cm,
            rightMargin=2 * cm,
            topMargin=2.2 * cm,
            bottomMargin=2.2 * cm,
        )
        flow = []
        for block in _html_to_blocks(html):
            style = styles["Heading1"] if block.startswith("§H§") else styles["BodyText"]
            text = block[3:] if block.startswith("§H§") else block
            flow.append(Paragraph(html_lib.escape(text), style))
            flow.append(Spacer(1, 6))
        if not flow:
            flow.append(Paragraph(" ", styles["BodyText"]))
        doc.build(flow)


def _html_to_blocks(html: str) -> list[str]:
    """Crude HTML → list of plain text blocks for the reportlab fallback."""
    # Headings get a marker so the renderer can style them.
    html = re.sub(r"<h[1-6][^>]*>(.*?)</h[1-6]>", r"\n§H§\1\n", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", "", html)
    text = html_lib.unescape(text)
    return [line.strip() for line in text.splitlines() if line.strip()]


def _status_banner(status: str) -> str:
    return {
        "ok": "Review",
        "needs_clarification": "Needs clarification",
        "timed_out": "Timed out",
        "failed": "Failed",
    }.get(status, status)


def render_html(result: AgentResult, doc: TabletDoc) -> str:
    """Markdown → HTML with the status banner on the first line (contract: response-publish)."""
    body_html = markdown_lib.markdown(
        result.markdown, extensions=["fenced_code", "tables"]
    )
    banner = html_lib.escape(_status_banner(result.status))
    return (
        f"<html><head><meta charset='utf-8'><style>{PRINT_CSS}</style></head>"
        f"<body><p class='status'>{banner}</p>{body_html}</body></html>"
    )


def build_visible_name(doc: TabletDoc, when: date) -> str:
    """Frozen naming rule: ``Re: <doc.visible_name> (YYYY-MM-DD)``."""
    return f"Re: {doc.visible_name} ({when.isoformat()})"


def publish(
    result: AgentResult,
    doc: TabletDoc,
    when: date,
    client: TabletClient,
    renderer: PdfRenderer | None = None,
    out_dir: Path = Path("out"),
) -> PublishedDoc:
    """Render the report to PDF and upload it (create-only) to ``/Claude/Responses``."""
    renderer = renderer or WeasyPrintRenderer()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    visible_name = build_visible_name(doc, when)
    pdf_path = out_dir / f"{visible_name}.pdf"

    html = render_html(result, doc)
    renderer.render(html, pdf_path)

    parent = client.ensure_folder(RESPONSES_FOLDER)
    new_doc_id = str(uuid4())
    client.upload(
        doc_id=new_doc_id,
        visible_name=visible_name,
        parent=parent,
        pdf_bytes=pdf_path.read_bytes(),
    )

    return PublishedDoc(
        pdf_path=pdf_path,
        visible_name=visible_name,
        tablet_parent=parent,
        uploaded=True,
    )
