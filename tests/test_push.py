"""Unit tests for the ``remarkable-bridge push`` CLI (bridge/push.py + bridge/cli.py).

No rmapi, no cloud, no network: the exec seam is a fake that captures the argv. Markdown
rendering uses the no-native-dep ReportLab renderer so pytest is green without cairo/pango.
"""

from __future__ import annotations

import pytest

from bridge import cli
from bridge.publish import ReportLabRenderer
from bridge.push import DEFAULT_FOLDER, PushError, push


def _ok_run(calls):
    def fake_run(argv, *, cwd=None):
        calls.append(argv)
        return {"code": 0, "stdout": "uploaded", "stderr": ""}

    return fake_run


def test_push_existing_file_builds_correct_argv(tmp_path):
    calls: list[list[str]] = []
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF-1.4 stub")

    result = push(f, run=_ok_run(calls), rmapi_bin="rmapi")

    assert calls == [["rmapi", "put", str(f), DEFAULT_FOLDER]]
    assert result.folder == DEFAULT_FOLDER
    assert result.pdf_path == f


def test_push_honors_folder(tmp_path):
    calls: list[list[str]] = []
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF-1.4 stub")

    push(f, folder="/Other", run=_ok_run(calls), rmapi_bin="rmapi")

    assert calls == [["rmapi", "put", str(f), "/Other"]]


def test_push_markdown_renders_valid_pdf_then_uploads(tmp_path):
    calls: list[list[str]] = []
    md = tmp_path / "note.md"
    md.write_text("# Hello\n\nSome **body** text.\n", encoding="utf-8")

    result = push(
        md,
        is_markdown=True,
        title="My Note",
        run=_ok_run(calls),
        renderer=ReportLabRenderer(),
        rmapi_bin="rmapi",
        out_dir=tmp_path,
    )

    # A real, valid PDF was produced.
    assert result.pdf_path.read_bytes().startswith(b"%PDF")
    # Title honored: the uploaded file is named after the title.
    assert result.visible_name == "My Note"
    assert result.pdf_path.name == "My Note.pdf"
    # argv points at the generated PDF, correct folder.
    assert calls == [["rmapi", "put", str(result.pdf_path), DEFAULT_FOLDER]]


def test_push_markdown_default_title_from_filename(tmp_path):
    calls: list[list[str]] = []
    md = tmp_path / "grocery-list.md"
    md.write_text("- milk\n- eggs\n", encoding="utf-8")

    result = push(
        md,
        is_markdown=True,
        run=_ok_run(calls),
        renderer=ReportLabRenderer(),
        out_dir=tmp_path,
    )

    assert result.visible_name == "grocery-list"
    assert result.pdf_path.read_bytes().startswith(b"%PDF")


def test_nonzero_rmapi_exit_raises_push_error(tmp_path):
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF-1.4 stub")

    def fail_run(argv, *, cwd=None):
        return {"code": 1, "stdout": "", "stderr": "refresh token expired"}

    with pytest.raises(PushError) as exc:
        push(f, run=fail_run)
    assert "refresh token expired" in str(exc.value)


def test_missing_rmapi_binary_raises_push_error(tmp_path):
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF-1.4 stub")

    def missing_run(argv, *, cwd=None):
        raise FileNotFoundError("rmapi")

    with pytest.raises(PushError):
        push(f, run=missing_run)


def test_missing_input_file_raises_push_error(tmp_path):
    with pytest.raises(PushError):
        push(tmp_path / "nope.pdf", run=_ok_run([]))


def test_missing_markdown_input_raises_push_error(tmp_path):
    with pytest.raises(PushError):
        push(tmp_path / "nope.md", is_markdown=True, run=_ok_run([]),
             renderer=ReportLabRenderer())


def test_rmapi_bin_from_env(tmp_path, monkeypatch):
    calls: list[list[str]] = []
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF-1.4 stub")
    monkeypatch.setenv("RMAPI_BIN", "/opt/rmapi")

    push(f, run=_ok_run(calls))

    assert calls[0][0] == "/opt/rmapi"


# --- CLI dispatcher ---------------------------------------------------------


def test_cli_main_importable_and_dispatchable(tmp_path, monkeypatch):
    """The entry point (bridge.cli:main) dispatches `push` and returns exit 0."""
    calls: list[list[str]] = []
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF-1.4 stub")

    def fake_push(source, **kwargs):
        calls.append([str(source), kwargs.get("folder")])
        from bridge.push import PushResult
        from pathlib import Path

        return PushResult(Path(source), kwargs.get("folder"), "doc", ["rmapi", "put"])

    monkeypatch.setattr(cli, "push", fake_push)
    rc = cli.main(["push", str(f)])

    assert rc == 0
    assert calls == [[str(f), DEFAULT_FOLDER]]


def test_cli_push_nonzero_exit_on_failure(tmp_path, monkeypatch):
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF-1.4 stub")

    def boom(source, **kwargs):
        raise PushError("rmapi put failed (exit 1): nope")

    monkeypatch.setattr(cli, "push", boom)
    assert cli.main(["push", str(f)]) == 1


def test_cli_push_requires_exactly_one_source(tmp_path):
    # Neither file nor --md.
    assert cli.main(["push"]) == 2
