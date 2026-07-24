# 0004. Agent runtime: headless claude -p behind an injected AgentRunner with per-route tool allowlists

- **Status:** Accepted
- **Date:** 2026-07-24

## Context

The dispatcher routes a tagged page to an agent: `review` (summarize/critique, no side
effects) or `execute` (may write code/files in a sandbox). Both run **unattended** —
no human to approve tool calls mid-run. Flags were verified against the installed CLI,
**Claude Code v2.1.219** (not from memory):

- `-p, --print` — non-interactive, print and exit.
- `--output-format text|json|stream-json`.
- `--allowedTools` / `--disallowedTools <tools...>`.
- `--append-system-prompt <prompt>` and `--append-system-prompt-file`.
- `--add-dir <dirs...>`, `--permission-mode <mode>`, `--model <model>`,
  `--session-id`, `--max-turns` (print mode).

## Decision

Agents run as **headless `claude -p` subprocesses behind an injected `AgentRunner`
interface**. The concrete runner and its exact flag envelope are frozen in the
`agent-invocation` contract; the rest of the system depends only on the interface.

- **Least privilege per route**, enforced by an explicit **allowlist** (not a
  denylist):
  - `review`: read-only tool set (e.g. `Read`, `Grep`, `Glob`), **no** `Write`/`Edit`/
    `Bash`; run in a throwaway cwd. Output is Markdown only.
  - `execute`: `Read`/`Write`/`Edit`/`Bash` scoped by `--add-dir` to a **per-document
    sandbox** `workspace/<doc>/` and nothing else; system prompt forbids touching the
    tablet or paths outside the sandbox.
- **Output capture:** `--output-format json` (or `stream-json` for logging), parse the
  final assistant message as the Markdown report; full invocation + raw stream logged
  to `logs/`.
- **Guardrails:** per-run **timeout** (default 10 min) → kill + "timed out" report;
  non-zero exit → "failed" report (never a silent drop); low-confidence/ambiguous
  extraction → **"needs clarification"** report instead of guessing.
- The tablet's write tools are **never** in any agent allowlist — publishing is done by
  our own `publish` module after the agent returns, so an agent can never write to the
  device.

## Alternatives considered

- **Claude Agent SDK / direct Messages API in-process** — more control over the loop,
  but reimplements tool execution, sandboxing, and permissioning that the CLI already
  provides, and diverges from "the same Claude Code the user runs interactively."
  Rejected for the walking skeleton; revisit only if the CLI proves limiting.
- **Denylist tools** (`--disallowedTools`) — unsafe by default: a newly added tool is
  implicitly allowed. Allowlist is safe-by-construction. Rejected.
- **One shared agent for both routes** — collapses the privilege boundary between
  read-only review and code-executing execute. Rejected; the boundary is the point.
- **`--permission-mode bypassPermissions` with a wide tool set** — convenient but
  removes the guardrail. Rejected; scope via allowlist + `--add-dir` instead.

## Consequences

- Adding a route later = a new prompt + a new allowlist row, additive under the
  `agent-invocation` contract — no re-architecture.
- The daemon inherits whatever `claude` auth exists on the host → drives the deployment
  choice: the host must have an authenticated CLI (ADR-0005).
- Sandbox confinement rests on `--add-dir` scoping + the system prompt; the execute
  route is the highest-risk surface and gets the most explicit rails and logging.
- Subprocess model = clean per-run isolation and simple timeout/kill semantics.
