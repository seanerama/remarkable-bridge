# Contract: bridge-state

- **Status:** frozen v1
- **Owner:** `state` module.

## Exposes

A local JSON state file (default `~/.local/state/remarkable-bridge/state.json`) that
makes the pipeline idempotent across poll cycles and restarts. Interface:

```
State.seen(doc: TabletDoc) -> bool          # already processed at this content_hash?
State.record(doc, route, result_status)     # atomic commit after a published run
State.load() / State.save()                  # save = write-temp + fsync + rename
```

## Consumes

- `TabletDoc.id` and `TabletDoc.content_hash` (contract `tablet-document`) as the
  dedup key.
- The result of `publish(...)` (contract `response-publish`) — a document is only
  recorded **after** a successful upload.

## Schema / wire

```jsonc
{
  "version": 1,
  "documents": {
    "<uuid>": {
      "last_hash": "<sha256>",       // content_hash at last successful processing
      "last_mtime": 1690000000000,   // TabletDoc.last_modified
      "route": "review" | "execute",
      "status": "ok" | "needs_clarification" | "timed_out" | "failed",
      "processed_at": "2026-07-24T19:40:00Z"
    }
  }
}
```

Rules (frozen):

- **Dedup key is `(uuid, last_hash)`.** A doc is reprocessed only when its
  `content_hash` changes (re-tag or edit) — not merely because it was seen. Re-running
  the watcher with no tablet changes reprocesses **nothing** (acceptance test #3).
- **Record only after publish succeeds.** A crash mid-run leaves the doc un-recorded →
  retried next cycle (never lost, never double-published a completed run).
- **Atomic writes:** temp file in the same dir → `fsync` → `os.replace`. A crash
  mid-write cannot corrupt the state file (acceptance: state survives a kill).
- Dedup is keyed on content hash, **not** on writing a `done` tag back to the tablet —
  no tablet write is required for correctness (works even if tag-write is unavailable).

## Versioning

Frozen at **v1**. Changes are **additive only** — a breaking change is a NEW contract,
not an edit (framework-spec §4.3). `version` gates the on-disk format; a reader must
migrate forward from `1`. New optional per-document fields are additive; changing the
dedup key semantics is a new contract.
