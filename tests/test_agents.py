"""Production agent-invocation path: page images must reach `claude -p` (contract:
agent-invocation). Covers argv (--add-dir scoping) and the composed prompt text."""

from __future__ import annotations

from pathlib import Path

from bridge.agents import AgentJob, build_argv, build_prompt
from bridge.tablet import TabletDoc

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "bridge" / "prompts"


def _doc() -> TabletDoc:
    return TabletDoc(
        id="doc-1",
        visible_name="Notes",
        parent="",
        type="DocumentType",
        last_modified=0,
        deleted=False,
        tags=["review"],
        content_hash="h",
    )


def _job(*, text, page_images, workspace) -> AgentJob:
    return AgentJob(
        route="review",
        doc=_doc(),
        text=text,
        workspace=workspace,
        page_images=page_images,
    )


# --------------------------------------------------------------------------- #
# Production argv: --add-dir is added for review ONLY when images exist
# --------------------------------------------------------------------------- #
def test_review_with_images_adds_pages_dir_and_keeps_readonly_allowlist(tmp_path):
    pages_dir = tmp_path / "pages"
    images = [pages_dir / "page-00.png", pages_dir / "page-01.png"]
    argv = build_argv(_job(text=None, page_images=images, workspace=tmp_path), PROMPTS_DIR)

    # The images' dir is granted so the read-only agent can open them.
    assert "--add-dir" in argv
    assert str(pages_dir) in argv
    assert argv[argv.index("--add-dir") + 1] == str(pages_dir)

    # Allowlist is unchanged — review stays read-only (no Write/Edit/Bash).
    tools_start = argv.index("--allowedTools") + 1
    tools = argv[tools_start : tools_start + 3]
    assert tools == ["Read", "Grep", "Glob"]
    assert "Write" not in argv and "Edit" not in argv and "Bash" not in argv


def test_review_text_only_adds_no_add_dir(tmp_path):
    """Regression: a text-only review job gets NO --add-dir (unchanged behavior)."""
    argv = build_argv(_job(text="hello", page_images=[], workspace=tmp_path), PROMPTS_DIR)
    assert "--add-dir" not in argv


# --------------------------------------------------------------------------- #
# Production prompt: image paths + read instruction reach the agent
# --------------------------------------------------------------------------- #
def test_prompt_lists_image_paths_and_instructs_reading(tmp_path):
    pages_dir = tmp_path / "pages"
    images = [pages_dir / "page-00.png", pages_dir / "page-01.png"]
    prompt = build_prompt(_job(text=None, page_images=images, workspace=tmp_path))

    # Paths are listed relative to the run cwd (= workspace) so the agent can Read them.
    assert "pages/page-00.png" in prompt
    assert "pages/page-01.png" in prompt
    assert "Read" in prompt  # instructed to read the images


def test_prompt_includes_typed_text_alongside_images(tmp_path):
    images = [tmp_path / "pages" / "page-00.png"]
    prompt = build_prompt(_job(text="typed context", page_images=images, workspace=tmp_path))
    assert "typed context" in prompt
    assert "pages/page-00.png" in prompt


def test_prompt_text_only_is_unchanged(tmp_path):
    """Flag-OFF / no-images path returns exactly job.text (byte-for-byte Stage 1)."""
    assert build_prompt(_job(text="just text", page_images=[], workspace=tmp_path)) == "just text"
    assert build_prompt(_job(text=None, page_images=[], workspace=tmp_path)) == ""
