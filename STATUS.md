# remarkable-bridge — Status & Handoff

> Runtime/ops truth (framework-spec §4.6). Owned by the **Release/Deploy Operator**,
> updated on every deploy. Records secret **locations** only — never values.

**As of:** not yet deployed

## TL;DR

Scaffolded by Verity. Nothing deployed yet.

## Live deployment

- (none)

## Images

- prefix: `ghcr.io/seanerama/remarkable-bridge`
- (no releases yet)

## Secrets

- (none configured) — when set, list NAMES + on-disk LOCATIONS only, never values.

## Coordination notes

- 2026-07-24 — Architecture locked (`/verity:architect`). Stack: Python+uv, subprocess
  SSH, rmscene/PyMuPDF, WeasyPrint (ADR-0001). Modular-monolith watcher; `remarkable-mcp`
  is an external dep, not owned (ADR-0002/0003). Agents = headless `claude -p` behind an
  injected runner (ADR-0004). Deploy target: **NSAF dev server / systemd**, tablet over
  **WiFi SSH** (ADR-0005) — reachability is a first-deploy prerequisite. 4 frozen
  contracts in `contracts/`. Walking skeleton: `docs/walking-skeleton.md`. helper-bot
  feature declined (no web UI). Next: `/verity:plan`.
