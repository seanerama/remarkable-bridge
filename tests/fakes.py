"""Test doubles for the two external boundaries (TabletClient, AgentRunner).

These are the ONLY things faked in the walking-skeleton integration test — every other
module (tablet reader, extract, publish, state, watcher) runs for real.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from bridge.agents import AgentJob, AgentResult
from bridge.tablet import RawDoc

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def load_raw_docs(fixtures_dir: Path = FIXTURES_DIR) -> list[RawDoc]:
    """Load checked-in fixtures into RawDoc records, exactly as the real scan would."""
    docs: list[RawDoc] = []
    for meta_path in sorted(fixtures_dir.glob("*.metadata")):
        doc_id = meta_path.name[: -len(".metadata")]
        content_path = fixtures_dir / f"{doc_id}.content"
        pages: dict[str, bytes] = {}
        page_dir = fixtures_dir / doc_id
        if page_dir.is_dir():
            for rm_path in sorted(page_dir.glob("*.rm")):
                pages[rm_path.name[: -len(".rm")]] = rm_path.read_bytes()
        docs.append(
            RawDoc(
                doc_id=doc_id,
                metadata=meta_path.read_bytes(),
                content=content_path.read_bytes() if content_path.exists() else b"{}",
                pages=pages,
            )
        )
    return docs


@dataclass
class UploadRecord:
    doc_id: str
    visible_name: str
    parent: str
    pdf_bytes: bytes


class FakeTabletClient:
    """Serves fixture docs and records uploads. Has NO delete/overwrite method (mirrors
    the real interface's structural safety rail)."""

    def __init__(self, raw_docs: list[RawDoc] | None = None) -> None:
        self._raw_docs = raw_docs if raw_docs is not None else load_raw_docs()
        self.folders: dict[str, str] = {}  # path -> synthetic uuid
        self.uploads: list[UploadRecord] = []

    def scan_raw(self) -> list[RawDoc]:
        return list(self._raw_docs)

    def ensure_folder(self, path: str) -> str:
        return self.folders.setdefault(path, f"folder-uuid::{path}")

    def upload(self, *, doc_id: str, visible_name: str, parent: str, pdf_bytes: bytes) -> None:
        self.uploads.append(
            UploadRecord(doc_id=doc_id, visible_name=visible_name, parent=parent, pdf_bytes=pdf_bytes)
        )


@dataclass
class StubAgentRunner:
    """Returns canned Markdown — no model, no network (contract: agent-invocation)."""

    markdown: str = (
        "## Review\n\nThe roadmap notes are clear. The three proposals are well ordered; "
        "consider adding acceptance criteria to proposal 1.\n"
    )
    status: str = "ok"
    calls: list[AgentJob] = field(default_factory=list)

    def run(self, job: AgentJob) -> AgentResult:
        self.calls.append(job)
        log_path = job.workspace / "agent.log"
        log_path.write_text("stub agent run\n")
        return AgentResult(
            status=self.status,  # type: ignore[arg-type]
            markdown=self.markdown,
            raw_log_path=log_path,
            exit_code=0,
        )
