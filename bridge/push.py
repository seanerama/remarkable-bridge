"""``remarkable-bridge push`` — deliver a document to a tablet folder via the cloud.

Two forms (see :func:`push`):

* push an existing file (e.g. a PDF) straight to a tablet folder, or
* render **markdown → PDF** (reusing Stage 1's :class:`~bridge.publish.PdfRenderer`
  seam — WeasyPrint in production, ReportLab where the native libs are absent) and push
  the result.

Upload shells ``rmapi put <pdf> <folder>`` through the **injected exec seam** reused from
:mod:`bridge.cloud` (``run(argv, cwd=...) -> {code, stdout, stderr}``) so tests never shell
``rmapi`` or touch the cloud. The binary defaults to the ``RMAPI_BIN`` env var, else
``rmapi``.

The upload is **create-only** — ``rmapi put`` mints a *new* document; nothing here deletes
or overwrites anything on the device (safety rail, mirroring ADR-0003).
"""

from __future__ import annotations

import html as html_lib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import markdown as markdown_lib

from .cloud import DEFAULT_RMAPI_BIN, ExecFn, _default_run
from .publish import PRINT_CSS, PdfRenderer, WeasyPrintRenderer

DEFAULT_FOLDER = "/NS-Inbox"


class PushError(Exception):
    """A push could not be completed: missing input, render failure, or a nonzero
    ``rmapi`` exit / missing binary. The CLI turns this into a nonzero exit + message."""


@dataclass(frozen=True)
class PushResult:
    """What a successful push produced — handy for the CLI and for tests."""

    pdf_path: Path  # the file actually uploaded
    folder: str  # tablet parent folder
    visible_name: str  # the name the tablet doc takes (rmapi uses the basename)
    argv: list[str]  # the exact rmapi argv that ran (captured for assertions)


def _md_to_html(md_text: str, title: str | None) -> str:
    """Markdown → e-ink-friendly HTML (reuses the print CSS from ``publish``)."""
    body = markdown_lib.markdown(md_text, extensions=["fenced_code", "tables"])
    head_title = html_lib.escape(title or "")
    return (
        f"<html><head><meta charset='utf-8'><title>{head_title}</title>"
        f"<style>{PRINT_CSS}</style></head><body>{body}</body></html>"
    )


def render_markdown(
    md_path: Path,
    *,
    title: str | None = None,
    renderer: PdfRenderer | None = None,
    out_dir: Path | None = None,
) -> tuple[Path, str]:
    """Render ``md_path`` to a PDF, returning ``(pdf_path, visible_name)``.

    The PDF is named ``<title or filename stem>.pdf`` so the tablet document takes that
    name (``rmapi put`` uses the uploaded file's basename as the visible name). Production
    defaults to :class:`WeasyPrintRenderer`; tests inject the no-native-dep ReportLab one.
    """
    md_path = Path(md_path)
    if not md_path.is_file():
        raise PushError(f"input file not found: {md_path}")

    visible_name = (title or md_path.stem).strip() or md_path.stem
    renderer = renderer or WeasyPrintRenderer()
    out_dir = Path(out_dir) if out_dir is not None else Path(tempfile.mkdtemp(prefix="rmapi-push-"))
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = out_dir / f"{visible_name}.pdf"

    html = _md_to_html(md_path.read_text(encoding="utf-8"), visible_name)
    try:
        renderer.render(html, pdf_path)
    except Exception as exc:  # render failure surfaces as a push error
        raise PushError(f"markdown render failed: {exc}") from exc
    return pdf_path, visible_name


def push(
    source: Path | str,
    *,
    folder: str = DEFAULT_FOLDER,
    is_markdown: bool = False,
    title: str | None = None,
    run: ExecFn | None = None,
    renderer: PdfRenderer | None = None,
    rmapi_bin: str | None = None,
    out_dir: Path | None = None,
) -> PushResult:
    """Upload ``source`` (a file, or markdown rendered to PDF) to a tablet ``folder``.

    Create-only: shells ``rmapi put <pdf> <folder>`` through the injected ``run`` seam.
    Raises :class:`PushError` on a missing input, render failure, missing ``rmapi`` binary,
    or a nonzero ``rmapi`` exit.
    """
    source = Path(source)
    run = run or _default_run
    rmapi_bin = rmapi_bin or os.environ.get("RMAPI_BIN") or DEFAULT_RMAPI_BIN

    if is_markdown:
        pdf_path, visible_name = render_markdown(
            source, title=title, renderer=renderer, out_dir=out_dir
        )
    else:
        if not source.is_file():
            raise PushError(f"input file not found: {source}")
        pdf_path = source
        visible_name = title or source.stem

    argv = [rmapi_bin, "put", str(pdf_path), folder]
    try:
        res = run(argv)
    except FileNotFoundError as exc:
        raise PushError(f"rmapi unavailable: {exc}") from exc

    if res["code"] != 0:
        raise PushError(
            f"rmapi put failed (exit {res['code']}): {res.get('stderr', '').strip()}"
        )

    return PushResult(
        pdf_path=pdf_path, folder=folder, visible_name=visible_name, argv=argv
    )
