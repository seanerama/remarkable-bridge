# remarkable-bridge

A tag-based dispatcher that routes reMarkable notebook pages to Claude Code agents and pushes PDF results back to the tablet

> Scaffolded by [Verity](https://github.com/seanerama/verity-framework) — prompt to production, proven.

## Status

See [`STATUS.md`](STATUS.md) for live runtime state (deployed version, environments).

## Product entry point (Stage 6+): the Outbound cloud watcher

The current product entry point is the **Outbound watcher** (`bridge/watch.py`, systemd unit
`deploy/remarkable-bridge-watch.service`, `python -m bridge.watch`). It polls the tablet's
**`/Outbound`** folder over the reMarkable **cloud API** (`rmapi`, works off-network),
extracts each new note (typed text + rendered handwriting PNGs), and hands it to the
nightshift-assistant by shelling `nightshift submit --type note-ingest` (frozen contract
[`contracts/note-submission.md`](contracts/note-submission.md)). It ships **dark**:
`[watch] enabled` in `bridge/config.toml` defaults **off**, so the unit can be installed but
stays inert until an operator enables it. `rmapi` is used **read-only** (`ls`/`get`); there is
no delete/overwrite path (ADR-0003 posture).

> **Superseded:** the older `review`/`execute` dispatcher (`bridge/watcher.py`, script
> `remarkable-bridge`) — which ran a local Claude agent on tag-routed tablet pages and pushed
> a PDF back — is **no longer the product entry point**. nightshift is now the brain. That
> code and its tests remain intact (still green) but are not the deployed daemon.

## Project identity

- **slug:** `remarkable-bridge`
- **images:** `ghcr.io/seanerama/remarkable-bridge`
