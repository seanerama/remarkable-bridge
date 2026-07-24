"""Stage 5 / Part 1 — doc + command lint (the only CI-automatable check).

CI cannot reach a tablet or drive an interactive MCP session, so these tests only
assert that the Part-1 setup/verify docs exist and that the documented
`claude mcp add remarkable ...` registration command is well-formed:

- parses cleanly with ``shlex`` (a real, copy-pasteable shell command),
- registers globally (``--scope user``),
- enables write mode so PDFs can be uploaded (``--write``),
- passes the OCR backend as an env var (``-e REMARKABLE_OCR_BACKEND=...``),
- uses the ``--`` separator before the ``uvx remarkable-mcp`` launch command.

It does NOT register anything, start the server, or touch the tablet — it is pure
static analysis of the checked-in docs.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest

DOCS = Path(__file__).resolve().parent.parent / "docs"
SETUP_DOC = DOCS / "part1-mcp-setup.md"
VERIFY_DOC = DOCS / "part1-verify.md"


def test_part1_docs_exist() -> None:
    assert SETUP_DOC.is_file(), f"missing setup doc: {SETUP_DOC}"
    assert VERIFY_DOC.is_file(), f"missing verify doc: {VERIFY_DOC}"


def _extract_mcp_add_command() -> str:
    """Return the single documented `claude mcp add remarkable ...` command line."""
    text = SETUP_DOC.read_text(encoding="utf-8")
    matches = [
        line.strip()
        for line in text.splitlines()
        if re.match(r"^\s*claude mcp add remarkable\b", line)
    ]
    assert matches, "no `claude mcp add remarkable` command found in the setup doc"
    assert len(matches) == 1, f"expected exactly one registration command, got {matches}"
    return matches[0]


@pytest.fixture
def add_command() -> str:
    return _extract_mcp_add_command()


def test_add_command_parses_with_shlex(add_command: str) -> None:
    tokens = shlex.split(add_command)  # raises ValueError if the command is malformed
    assert tokens[:4] == ["claude", "mcp", "add", "remarkable"]


def test_add_command_is_user_scoped(add_command: str) -> None:
    tokens = shlex.split(add_command)
    assert "--scope" in tokens, "registration must set a scope"
    assert tokens[tokens.index("--scope") + 1] == "user", "must be --scope user (global)"


def test_add_command_enables_write_mode(add_command: str) -> None:
    tokens = shlex.split(add_command)
    sep = tokens.index("--")
    # --write is a SERVER flag: it must come after the `--` separator.
    assert "--write" in tokens[sep + 1:], "--write must follow the -- separator"


def test_add_command_sets_ocr_backend_env(add_command: str) -> None:
    tokens = shlex.split(add_command)
    sep = tokens.index("--")
    # -e KEY=VALUE is a `claude mcp add` flag: it must come BEFORE the `--` separator.
    env_flags = [
        tokens[i + 1]
        for i, tok in enumerate(tokens[:sep])
        if tok == "-e" and i + 1 < sep
    ]
    assert any(
        v.startswith("REMARKABLE_OCR_BACKEND=") and v.split("=", 1)[1]
        for v in env_flags
    ), f"expected -e REMARKABLE_OCR_BACKEND=<value> before --, got env flags {env_flags}"


def test_add_command_separator_precedes_uvx(add_command: str) -> None:
    tokens = shlex.split(add_command)
    assert "--" in tokens, "the launch command must be introduced by a -- separator"
    sep = tokens.index("--")
    launch = tokens[sep + 1:]
    assert launch[:2] == ["uvx", "remarkable-mcp"], (
        f"the -- separator must be immediately followed by `uvx remarkable-mcp`, "
        f"got {launch}"
    )
