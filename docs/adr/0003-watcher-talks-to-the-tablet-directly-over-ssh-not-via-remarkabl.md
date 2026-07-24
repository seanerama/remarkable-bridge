# 0003. Watcher talks to the tablet directly over SSH, not via remarkable-mcp as a library

- **Status:** Accepted
- **Date:** 2026-07-24

## Context

Part 2's watcher needs three tablet operations: (1) **scan** metadata/content files to
detect changed, tagged documents; (2) **pull** a document's `.rm`/`.content` for
extraction; (3) **upload** a finished PDF into `/Claude/Responses`. The spec allows
either reusing `remarkable-mcp`'s Python package as a library, or talking to the
tablet directly.

The tablet is a plain Linux box: notebook data lives at
`/home/root/.local/share/remarkable/xochitl/` as `<uuid>.metadata` (JSON),
`<uuid>.content` (JSON), and `<uuid>/<page>.rm` (v6 binary). All three operations
above are ordinary file reads/writes over SSH. Contracts-first says: **define the
seam, freeze it, keep it stable so later stages and other agents extend safely.**

## Decision

**Part 2 talks to the tablet directly over SSH/SCP**, behind a single `TabletClient`
interface (the `tablet` module). It does **not** import `remarkable_mcp` internals and
does **not** run the MCP server as a subprocess for the autonomous path.

- **Change detection:** one `ssh remarkable 'find <xochitl> -name "*.metadata" -o -name "*.content" -newer <marker>'` + batched `cat`, parsed per the `tablet-document` contract — not an rsync of everything.
- **Extraction** uses `rmscene`/`PyMuPDF` locally on pulled files (ADR-0001).
- **Upload** writes the doc tree directly under `xochitl/` over SSH (the SSH-mode
  write path; the tablet UI restarts to show new files — expected).
- `TabletClient` is an **injected interface** with a fake implementation for tests, so
  CI never needs a live tablet (see the walking-skeleton definition).

## Alternatives considered

- **Import `remarkable_mcp` as a library** — reuses upstream extraction/upload code,
  but binds our daemon to another project's *private* internals (no stability
  guarantee), its dependency pins, and its assumptions. A minor upstream refactor
  could silently break our pipeline. Rejected — the reuse we *do* want (the v6 parser)
  is available first-hand as `rmscene`, which `remarkable-mcp` itself sits on.
- **Drive the MCP server as a second MCP client from the watcher** — pulls a full
  JSON-RPC/stdio stack and the model-sampling OCR path into an unattended daemon that
  needs neither. Heavy and fragile for headless use. Rejected.
- **`rmapi`/cloud API** — depends on reMarkable's cloud + account auth and wouldn't
  see local-only or unsynced content. Rejected; SSH is direct and offline.

## Consequences

- **Two independent tablet code paths** (interactive via MCP, autonomous via our SSH
  client) that must each track the on-disk format. The `tablet-document` contract is
  the shared source of truth that keeps them honest; its exact shape must be
  **verified empirically against a real tagged document** during the walking skeleton.
- We own SSH concerns (reachability, retries, the UI-restart-on-write behavior) —
  captured in the `tablet` module and the `response-publish` contract.
- Testability win: because `TabletClient` is injected, the whole pipeline runs in CI
  against checked-in fixtures with no device and no network.
- **Never delete on the tablet.** The `TabletClient` interface exposes no delete
  operation at all — the safety rail is structural, not a convention.
