# Contract: agent-invocation

- **Status:** frozen v1
- **Owner:** `agents` module (the `AgentRunner` interface, ADR-0004).

## Exposes

A single entry point the `watcher` calls per routed document:

```
AgentRunner.run(job: AgentJob) -> AgentResult
```

```
AgentJob = {
  route: "review" | "execute",
  doc: TabletDoc,                  # from contract tablet-document
  text: str | None,               # extracted typed text, if any
  page_images: [Path],            # rendered PNGs for handwriting (contract via extract)
  workspace: Path,                # per-doc sandbox dir (execute) or throwaway cwd (review)
}

AgentResult = {
  status: "ok" | "needs_clarification" | "timed_out" | "failed",
  markdown: str,                  # the report body (always present; failure → why)
  raw_log_path: Path,             # full stream-json/text transcript in logs/
  exit_code: int | None,
}
```

## Consumes

The installed **Claude Code CLI** (verified **v2.1.219**) as a subprocess. Frozen flag
envelope (verified against `claude --help`, not memory):

- `claude -p` — non-interactive print mode (required).
- `--output-format json` — final result parsed as `markdown`; `stream-json` may be
  used and tee'd to `raw_log_path`.
- `--append-system-prompt-file prompts/<route>.md` — the route's system prompt.
- `--allowedTools <tools...>` — **allowlist, per route** (never a denylist):
  - `review`  → `Read Grep Glob` (read-only; no `Write`/`Edit`/`Bash`).
  - `execute` → `Read Write Edit Bash Grep Glob`.
- `--add-dir <workspace>` — for `execute`, scopes filesystem access to the sandbox.
- `--model <model>` — optional, from config.
- Invoked with `cwd = workspace`, a per-run **timeout** (default 600s), and the doc's
  extracted content passed as the prompt stdin/argument.

Tablet write tools are **never** in any allowlist — the agent cannot touch the device.

## Schema / wire

- **Success:** exit 0 + parseable final message → `status="ok"`, `markdown` = message.
- **Ambiguous/low-confidence input** (bad OCR, empty extraction): the system prompt
  instructs the agent to emit a report beginning with a `needs-clarification` marker;
  the runner maps it to `status="needs_clarification"`. The `execute` route must NOT
  guess when instructions are unreadable.
- **Timeout:** process killed → `status="timed_out"`, `markdown` = timeout notice.
- **Non-zero exit / unparseable output:** `status="failed"`, `markdown` = captured
  stderr summary. Never silently dropped — every job yields a published report.
- Every invocation (argv minus secrets, full transcript, timing, exit) is logged.

## Versioning

Frozen at **v1**. Changes are **additive only** — a breaking change is a NEW contract,
not an edit (framework-spec §4.3). A new route (e.g. `triage`) = a new prompt + a new
allowlist row (additive). Changing the `AgentResult` shape or the meaning of an
existing route's allowlist is a new contract.
