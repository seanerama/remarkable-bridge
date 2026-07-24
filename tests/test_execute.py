"""Stage 4 — execute route + per-document sandbox safety rails.

Two flavors of test:

* **Unit / safety** — the frozen allowlist and argv scoping (contract: agent-invocation):
  the execute allowlist is EXACTLY ``Read Write Edit Bash Grep Glob``, no tablet-write tool
  is in ANY allowlist, and an execute job's ``--add-dir`` points ONLY at that doc's sandbox
  dir — nothing broader. The review path is asserted unchanged.

* **Pipeline** — an ``execute`` job with the flag ON runs through the real watcher/publish/
  state path with a FAKE runner (never the real ``claude`` CLI, never the real tablet): the
  agent's artifact lands in ``workspace/<doc>/``, a report PDF is uploaded to
  ``/Claude/Responses``; a simulated timeout still publishes a "timed out" report; and with
  the flag OFF the execute doc is skipped entirely (no processing, no upload).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from bridge.agents import (
    ALLOWED_TOOLS,
    AgentJob,
    AgentResult,
    build_argv,
)
from bridge.clock import FixedClock
from bridge.publish import RESPONSES_FOLDER, ReportLabRenderer
from bridge.state import State
from bridge.tablet import RawDoc, TabletDoc
from bridge.watcher import Config, run_once

from .fakes import FakeTabletClient

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "bridge" / "prompts"
FIXED_DAY = date(2026, 7, 24)
EXEC_DOC_ID = "e0000000-0000-4000-8000-000000000004"

# Tools that would let an agent reach the tablet/device. None may EVER appear in an
# allowlist — the pipeline (not the agent) publishes back to the tablet.
_TABLET_WRITE_TOOLS = {"Upload", "Ssh", "Scp", "Tablet", "Publish", "Delete", "Rm"}


# --------------------------------------------------------------------------- #
# Unit: the frozen allowlist (contract: agent-invocation)
# --------------------------------------------------------------------------- #
def test_execute_allowlist_is_exactly_the_specified_set():
    assert ALLOWED_TOOLS["execute"] == ["Read", "Write", "Edit", "Bash", "Grep", "Glob"]


def test_review_allowlist_unchanged_read_only():
    assert ALLOWED_TOOLS["review"] == ["Read", "Grep", "Glob"]
    # review can never write/edit/run shell.
    assert not ({"Write", "Edit", "Bash"} & set(ALLOWED_TOOLS["review"]))


def test_no_tablet_write_tool_in_any_allowlist():
    for route, tools in ALLOWED_TOOLS.items():
        assert not (_TABLET_WRITE_TOOLS & set(tools)), f"{route} allowlist leaks a tablet tool"


# --------------------------------------------------------------------------- #
# Safety: execute argv scopes filesystem reach to EXACTLY the sandbox dir
# --------------------------------------------------------------------------- #
def _exec_job(workspace: Path, *, text="do the thing") -> AgentJob:
    doc = TabletDoc(
        id=EXEC_DOC_ID, visible_name="Task", parent="", type="DocumentType",
        last_modified=0, deleted=False, tags=["execute"], content_hash="h",
    )
    return AgentJob(route="execute", doc=doc, text=text, workspace=workspace, page_images=[])


def test_execute_argv_add_dir_points_only_at_sandbox(tmp_path):
    sandbox = tmp_path / "workspace" / EXEC_DOC_ID
    argv = build_argv(_exec_job(sandbox), PROMPTS_DIR)

    # Exactly one --add-dir, and it is the sandbox — nothing broader (no parent, no $HOME).
    assert argv.count("--add-dir") == 1
    assert argv[argv.index("--add-dir") + 1] == str(sandbox)
    # No broader path (the sandbox's parent) is ever granted.
    assert str(sandbox.parent) not in argv
    assert str(Path.home()) not in argv

    # It targets the right prompt + the execute allowlist verbatim.
    assert str(PROMPTS_DIR / "execute.md") in argv
    tools_start = argv.index("--allowedTools") + 1
    assert argv[tools_start : tools_start + 6] == ["Read", "Write", "Edit", "Bash", "Grep", "Glob"]


def test_execute_argv_has_no_tablet_or_delete_flags(tmp_path):
    """The runner is given no tablet access — publishing is the pipeline's job, not the
    agent's. Argv carries no ssh host / tablet path / delete verb."""
    argv = build_argv(_exec_job(tmp_path / "ws"), PROMPTS_DIR)
    joined = " ".join(argv).lower()
    # No transport verbs, no tablet filesystem path, no publish destination, no delete.
    assert "ssh" not in joined and "scp" not in joined
    assert "xochitl" not in joined and "/home/root" not in joined
    assert "/claude/responses" not in joined
    assert "delete" not in joined and "--rm" not in joined


# --------------------------------------------------------------------------- #
# Pipeline: fake runners (never the real `claude` CLI, never the real tablet)
# --------------------------------------------------------------------------- #
@dataclass
class SandboxWritingRunner:
    """Fake execute agent: writes an artifact INTO the job's sandbox (as the real agent
    would via Write), then returns an ok report. Records the jobs it saw."""

    filename: str = "fib.py"
    contents: str = "print([0, 1, 1, 2, 3, 5, 8])\n"
    calls: list[AgentJob] = field(default_factory=list)

    def run(self, job: AgentJob) -> AgentResult:
        self.calls.append(job)
        (job.workspace / self.filename).write_text(self.contents)
        log_path = job.workspace / "agent.log"
        log_path.write_text("fake execute run\n")
        return AgentResult(
            status="ok",
            markdown=f"## Executed\n\nCreated `{self.filename}` in the sandbox.\n",
            raw_log_path=log_path,
            exit_code=0,
        )


@dataclass
class TimeoutRunner:
    """Fake execute agent that models the runner's timeout branch (contract:
    agent-invocation): the process is killed → a 'timed out' report is still returned."""

    calls: list[AgentJob] = field(default_factory=list)

    def run(self, job: AgentJob) -> AgentResult:
        self.calls.append(job)
        log_path = job.workspace / "agent.log"
        log_path.write_text("TIMED OUT\n")
        return AgentResult(
            status="timed_out",
            markdown="The agent timed out after 600s and was stopped.",
            raw_log_path=log_path,
            exit_code=None,
        )


def _execute_raw() -> RawDoc:
    """An execute-tagged document (typed instructions, no pages needed for the fake)."""
    content = {"formatVersion": 2, "tags": [{"name": "execute"}], "cPages": {"pages": []}}
    metadata = {
        "visibleName": "Fib Script",
        "type": "DocumentType",
        "parent": "",
        "lastModified": "1784918546000",
    }
    return RawDoc(
        doc_id=EXEC_DOC_ID,
        metadata=json.dumps(metadata).encode("utf-8"),
        content=json.dumps(content).encode("utf-8"),
        pages={},
    )


def _config(tmp_path: Path) -> Config:
    cfg = Config.load()  # real bridge/config.toml
    cfg.state_path = tmp_path / "state" / "state.json"
    cfg.out_dir = tmp_path / "out"
    cfg.logs_dir = tmp_path / "logs"
    cfg.workspace_root = tmp_path / "workspace"
    return cfg


def _run(tmp_path, runner, *, execute_enabled):
    client = FakeTabletClient(raw_docs=[_execute_raw()])
    config = _config(tmp_path)
    config.execute_enabled = execute_enabled
    state = State.load(config.state_path)
    report = run_once(
        client=client,
        runner=runner,
        clock=FixedClock(FIXED_DAY),
        state=state,
        config=config,
        renderer=ReportLabRenderer(),
    )
    return client, config, state, report


def test_execute_flag_on_writes_artifact_in_sandbox_and_publishes(tmp_path):
    runner = SandboxWritingRunner()
    client, config, state, report = _run(tmp_path, runner, execute_enabled=True)

    assert not report.errors
    assert report.processed == ["Re: Fib Script (2026-07-24)"]

    # The agent ran with route=execute and a per-doc sandbox = workspace/<doc-id>/.
    assert len(runner.calls) == 1
    job = runner.calls[0]
    assert job.route == "execute"
    assert job.workspace == config.workspace_root / EXEC_DOC_ID

    # The artifact landed INSIDE the sandbox (and only there).
    artifact = config.workspace_root / EXEC_DOC_ID / "fib.py"
    assert artifact.exists()
    assert "print" in artifact.read_text()

    # A report PDF was uploaded to /Claude/Responses (create-only publish path).
    assert len(client.uploads) == 1
    upload = client.uploads[0]
    assert upload.visible_name == "Re: Fib Script (2026-07-24)"
    assert client.folders[RESPONSES_FOLDER] == upload.parent
    assert upload.pdf_bytes.startswith(b"%PDF")

    # State recorded the execute run only after upload (contract: bridge-state).
    persisted = json.loads(config.state_path.read_text())
    assert persisted["documents"][EXEC_DOC_ID]["route"] == "execute"
    assert persisted["documents"][EXEC_DOC_ID]["status"] == "ok"


def test_execute_timeout_still_publishes_timed_out_report(tmp_path):
    runner = TimeoutRunner()
    client, config, state, report = _run(tmp_path, runner, execute_enabled=True)

    assert not report.errors
    assert report.processed == ["Re: Fib Script (2026-07-24)"]

    # A PDF was still produced and uploaded — the pipeline always publishes something.
    assert len(client.uploads) == 1
    assert client.uploads[0].pdf_bytes.startswith(b"%PDF")
    persisted = json.loads(config.state_path.read_text())
    assert persisted["documents"][EXEC_DOC_ID]["status"] == "timed_out"


def test_execute_flag_off_skips_the_doc_entirely(tmp_path):
    """Kill-switch OFF (default): an execute-tagged doc is NOT processed and NOTHING is
    uploaded — behavior identical to before Stage 4."""
    runner = SandboxWritingRunner()
    client, config, state, report = _run(tmp_path, runner, execute_enabled=False)

    assert report.processed == []
    assert report.skipped_seen == []  # skipped by the route gate, not by dedup
    assert runner.calls == []  # the agent never ran
    assert client.uploads == []  # nothing published
    assert not config.state_path.exists()  # no state written
    # The sandbox was never even created for this doc.
    assert not (config.workspace_root / EXEC_DOC_ID).exists()


def test_config_default_execute_enabled_is_off():
    """The shipped config.toml keeps the kill-switch OFF by default."""
    assert Config.load().execute_enabled is False


def test_execute_enabled_env_override(monkeypatch):
    """EXECUTE_ENABLED env var overrides the config default at runtime (like Stage 3)."""
    monkeypatch.setenv("EXECUTE_ENABLED", "true")
    assert Config.load().execute_enabled is True
    monkeypatch.setenv("EXECUTE_ENABLED", "0")
    assert Config.load().execute_enabled is False
