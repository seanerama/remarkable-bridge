# 0002. Topology: modular-monolith watcher; remarkable-mcp is an external dependency, not an owned service

- **Status:** Accepted
- **Date:** 2026-07-24

## Context

The system has two user-facing halves:

1. **Part 1 (interactive):** `remarkable-mcp` registered into Claude Code so a human
   in a `claude` session can browse/read/render/write the tablet.
2. **Part 2 (autonomous):** a watcher that polls the tablet for tagged pages and
   dispatches them to `claude -p` agents, publishing PDF results back.

The stack guide's default: **start as a modular monolith; split a service out only
for a real reason (independent scaling, ownership boundary, different runtime). Every
service multiplies the CI matrix, image set, and deploy surface, and extends the slug
`ghcr.io/<owner>/<slug>-<service>`.**

## Decision

- **`remarkable-bridge` (Part 2) is a single modular-monolith Python package** — one
  long-running watcher process. Internal seams are modules, not services:
  `watcher` (poll loop + change detection + tag routing), `tablet` (SSH transport),
  `extract` (`.rm` → text/PNG), `agents` (build prompt, run `claude -p`), `publish`
  (Markdown → PDF → upload), `state` (dedup file). The `claude -p` agents are
  **short-lived subprocesses**, not services we host.
- **`remarkable-mcp` is an EXTERNAL dependency we consume, not a service we own or
  deploy.** It exists only to serve the interactive Part-1 path (Claude Code speaks
  MCP to it over stdio). Part 2 does **not** route through it (see ADR-0003). We pin
  the version we tell users to `uvx`/register, and we track its tool/flag surface, but
  it never appears in our image set, CI matrix, or `deploy.sh`.

## Alternatives considered

- **Multi-service split** (separate watcher / extractor / publisher processes, e.g.
  over a queue) — guide-discouraged and unjustified: single operator, single tablet,
  60s poll cadence, no independent-scaling or ownership boundary. Rejected.
- **Build Part 2 *on top of* the MCP server** (watcher acts as a second MCP client, or
  imports `remarkable_mcp` internals) — couples our daemon to another project's
  private internals and its stdio lifecycle. Rejected here; see ADR-0003.
- **Fold Part 1's interactive access into our own code too** — pointless duplication;
  `remarkable-mcp` already does interactive access well. We reuse it as-is.

## Consequences

- One image, one CI lane, one systemd unit to deploy — matches a solo-operator tool.
- **A clear ownership line:** upstream `remarkable-mcp` changes can affect the
  *interactive* experience (Part 1) but cannot break the *autonomous* pipeline
  (Part 2), because Part 2 shares no code with it — only the tablet and the
  `tablet-document` contract's understanding of the on-disk format.
- If a genuine reason to split ever appears (e.g. a hosted multi-tablet service), that
  is a new ADR + per-service slug, not a refactor smuggled into a feature stage.
