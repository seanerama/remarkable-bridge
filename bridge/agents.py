"""Agent runtime — headless ``claude -p`` behind an injected ``AgentRunner`` (ADR-0004).

Frozen flag envelope (contract: agent-invocation), verified against the CLI, not memory:

    claude -p --output-format json \
        --append-system-prompt-file prompts/<route>.md \
        --allowedTools <tools...> [--add-dir <workspace>] [--model <model>]

Per-route allowlists (allowlist, never denylist):
    review  -> Read Grep Glob        (read-only; no Write/Edit/Bash)
    execute -> Read Write Edit Bash Grep Glob

The tablet's write tools are never in any allowlist — an agent can never touch the device.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from .tablet import TabletDoc

Route = Literal["review", "execute"]
Status = Literal["ok", "needs_clarification", "timed_out", "failed"]

# Per-route tool allowlists (contract: agent-invocation).
ALLOWED_TOOLS: dict[str, list[str]] = {
    "review": ["Read", "Grep", "Glob"],
    "execute": ["Read", "Write", "Edit", "Bash", "Grep", "Glob"],
}

# The marker the system prompt instructs the agent to lead with when it cannot proceed.
NEEDS_CLARIFICATION_MARKER = "needs-clarification"


@dataclass
class AgentJob:
    route: Route
    doc: TabletDoc
    text: str | None
    workspace: Path
    page_images: list[Path] = field(default_factory=list)


@dataclass
class AgentResult:
    status: Status
    markdown: str
    raw_log_path: Path
    exit_code: int | None


@runtime_checkable
class AgentRunner(Protocol):
    def run(self, job: AgentJob) -> AgentResult:
        ...


def _images_dir(page_images: list[Path]) -> Path:
    """The directory holding the rendered handwriting PNGs (their common parent)."""
    return page_images[0].parent


def build_argv(
    job: AgentJob,
    prompts_dir: Path,
    model: str | None = None,
) -> list[str]:
    """Build the frozen ``claude -p`` argv for a job (contract: agent-invocation)."""
    prompt_file = prompts_dir / f"{job.route}.md"
    argv = [
        "claude",
        "-p",
        "--output-format",
        "json",
        "--append-system-prompt-file",
        str(prompt_file),
        "--allowedTools",
        *ALLOWED_TOOLS[job.route],
    ]
    # execute scopes filesystem access to its per-doc sandbox; review runs read-only.
    if job.route == "execute":
        argv += ["--add-dir", str(job.workspace)]
    elif job.page_images:
        # review with rendered handwriting: grant READ access to the images' dir so the
        # read-only agent can open the page PNGs. This does NOT broaden review's tool
        # allowlist (still Read/Grep/Glob) — --add-dir only scopes filesystem reach.
        argv += ["--add-dir", str(_images_dir(job.page_images))]
    if model:
        argv += ["--model", model]
    return argv


def build_prompt(job: AgentJob) -> str:
    """Compose the ``claude -p`` input text for a job.

    Typed text (if any) is passed through unchanged. When the doc has rendered handwriting
    pages, we append a listing of the image paths (relative to the run cwd = workspace) and
    an instruction to read them as the page content — this is how the handwriting actually
    reaches the agent. With no page images this returns exactly ``job.text or ""`` (Stage
    1/2 behavior, byte-for-byte).
    """
    text = job.text or ""
    if not job.page_images:
        return text

    def _rel(p: Path) -> str:
        try:
            return str(p.relative_to(job.workspace))
        except ValueError:
            return str(p)

    listing = "\n".join(f"- {_rel(p)}" for p in job.page_images)
    block = (
        "The tagged page(s) are HANDWRITTEN and have been rendered to the following image "
        "file(s), in page order:\n"
        f"{listing}\n\n"
        "Read each image (with the Read tool) as the primary page content — it contains "
        "the handwriting to review. Base your review on what the images show."
    )
    parts = [text.strip(), block] if text.strip() else [block]
    return "\n\n".join(parts).strip()


class ClaudeAgentRunner:
    """Production runner: shells out to the Claude Code CLI (ADR-0004)."""

    def __init__(
        self,
        prompts_dir: Path,
        logs_dir: Path,
        model: str | None = None,
        timeout: int = 600,
    ) -> None:
        self.prompts_dir = prompts_dir
        self.logs_dir = logs_dir
        self.model = model
        self.timeout = timeout

    def run(self, job: AgentJob) -> AgentResult:
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        raw_log_path = self.logs_dir / f"{job.doc.id}-{job.route}.log"
        argv = build_argv(job, self.prompts_dir, self.model)
        prompt_text = build_prompt(job)

        # Log the invocation (argv carries no secrets) + timing for observability.
        try:
            proc = subprocess.run(
                argv,
                cwd=str(job.workspace),
                input=prompt_text,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired:
            raw_log_path.write_text(f"$ {' '.join(argv)}\n\nTIMED OUT after {self.timeout}s\n")
            return AgentResult(
                status="timed_out",
                markdown=f"The agent timed out after {self.timeout}s and was stopped.",
                raw_log_path=raw_log_path,
                exit_code=None,
            )

        raw_log_path.write_text(
            f"$ {' '.join(argv)}\n\n--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}\n"
        )

        if proc.returncode != 0:
            stderr_summary = (proc.stderr or "").strip()[:2000]
            return AgentResult(
                status="failed",
                markdown=f"The agent failed (exit {proc.returncode}).\n\n{stderr_summary}",
                raw_log_path=raw_log_path,
                exit_code=proc.returncode,
            )

        markdown = self._parse_markdown(proc.stdout)
        if markdown is None:
            return AgentResult(
                status="failed",
                markdown="The agent produced unparseable output.",
                raw_log_path=raw_log_path,
                exit_code=proc.returncode,
            )

        status: Status = "ok"
        if markdown.lstrip().lower().startswith(NEEDS_CLARIFICATION_MARKER):
            status = "needs_clarification"
        return AgentResult(
            status=status,
            markdown=markdown,
            raw_log_path=raw_log_path,
            exit_code=proc.returncode,
        )

    @staticmethod
    def _parse_markdown(stdout: str) -> str | None:
        """Parse the final assistant message from ``--output-format json``."""
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            return None
        if isinstance(payload, dict):
            result = payload.get("result")
            if isinstance(result, str):
                return result
        return None
