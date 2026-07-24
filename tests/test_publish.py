"""Publish-layer tests: naming rule, status banner, and both PDF renderers.

The ReportLab fallback runs everywhere (no native deps). The WeasyPrint production
renderer runs where its native libs exist (always in CI, which apt-installs them);
it skips only on a host lacking cairo/pango/gobject.
"""

from __future__ import annotations

from datetime import date

import pytest

from bridge.agents import AgentResult
from bridge.publish import (
    ReportLabRenderer,
    WeasyPrintRenderer,
    build_visible_name,
    render_html,
)
from bridge.tablet import TabletDoc


def _doc(name="Q3 Roadmap Notes") -> TabletDoc:
    return TabletDoc(
        id="d1", visible_name=name, parent="", type="DocumentType",
        last_modified=1, deleted=False, tags=["review"], content_hash="h",
    )


def _result(status="ok", markdown="## Review\n\nLooks good.") -> AgentResult:
    from pathlib import Path

    return AgentResult(status=status, markdown=markdown, raw_log_path=Path("x.log"), exit_code=0)


def test_visible_name_frozen_format():
    assert build_visible_name(_doc(), date(2026, 7, 24)) == "Re: Q3 Roadmap Notes (2026-07-24)"


def test_status_banner_surfaced_in_html():
    html = render_html(_result(status="needs_clarification"), _doc())
    assert "Needs clarification" in html
    html2 = render_html(_result(status="failed"), _doc())
    assert "Failed" in html2


def test_reportlab_renderer_writes_valid_pdf(tmp_path):
    pdf = tmp_path / "r.pdf"
    ReportLabRenderer().render(render_html(_result(), _doc()), pdf)
    assert pdf.read_bytes().startswith(b"%PDF")


def test_weasyprint_renderer_writes_valid_pdf(tmp_path):
    """Production renderer — exercised in CI (native deps provisioned)."""
    pdf = tmp_path / "w.pdf"
    try:
        WeasyPrintRenderer().render(render_html(_result(), _doc()), pdf)
    except OSError as exc:  # native cairo/pango/gobject libs absent on this host
        pytest.skip(f"WeasyPrint native libs unavailable: {exc}")
    assert pdf.read_bytes().startswith(b"%PDF")
