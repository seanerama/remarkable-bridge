# Contract: tablet-document

- **Status:** frozen v1
- **Owner:** `tablet` + `watcher` modules (the reMarkable on-disk format is owned by
  reMarkable; this contract freezes **the subset our reader depends on**).

> ⚠️ **Empirical-verification gate.** The v6 tag shape below is the expected layout and
> MUST be confirmed against a real tagged document on the user's device during the
> walking skeleton (Stage 0), by inspecting one `.content` file. If it differs, adjust
> the reader and this schema **before** freezing consumers on it. Freeze is "v1 of our
> reader," not a claim to own reMarkable's format.

## Exposes

A normalized `TabletDoc` record the rest of the pipeline consumes, produced by scanning
`/home/root/.local/share/remarkable/xochitl/` over SSH:

```
TabletDoc = {
  id: str,                 # <uuid>
  visible_name: str,       # from .metadata visibleName
  parent: str,             # .metadata parent (uuid | "" root | "trash")
  type: "DocumentType" | "CollectionType",
  last_modified: int,      # ms epoch, from .metadata lastModified
  deleted: bool,           # .metadata deleted / trash → excluded from routing
  tags: [str],             # union of document-level + page-level tag names (lowercased)
  content_hash: str,       # sha256 over the .content + per-page .rm bytes (dedup key)
}
```

## Consumes

Read-only SSH access to the tablet filesystem (the `TabletClient` interface, ADR-0003).
Per document UUID:

- `<uuid>.metadata` — JSON: `visibleName`, `parent`, `type`, `lastModified`,
  `deleted`, `pinned`.
- `<uuid>.content` — JSON: format version + tag arrays.
- `<uuid>/<page-id>.rm` — v6 binary strokes (consumed by `extract`, not parsed here).

## Schema / wire

**Tag location (v6 — verify empirically).** Document-level tags and page-level tags in
the `.content` JSON, expected shape:

```jsonc
{
  "formatVersion": 2,
  "tags": [ { "name": "review", "timestamp": 1690000000000 } ],   // document-level
  "cPages": {
    "pages": [
      { "id": "<page-uuid>",
        "tags": [ { "name": "execute", "timestamp": 1690000000000 } ] }  // page-level
    ]
  }
}
```

Reader rules (frozen):

- A document is **routable** if any document-level OR page-level tag name matches a
  configured route tag (case-insensitive), and `deleted == false`.
- Tag names are compared **lowercased, exact** (`review`, `execute`). The leading `#`
  in the spec's prose is display sugar — on disk the `name` has no `#`.
- Older/alternate shapes (bare `["review"]`, `pageTags`) MUST be tolerated by the
  reader; unknown fields are ignored (forward-compatible).
- `content_hash` covers `.content` + all page `.rm` bytes so that re-tagging or editing
  a page changes the hash (drives dedup, contract `bridge-state`).

## Versioning

Frozen at **v1**. Changes are **additive only** — a breaking change is a NEW contract,
not an edit (framework-spec §4.3). New optional `TabletDoc` fields are additive; a
different on-disk format version that breaks the reader is a new contract
(`tablet-document-v7` or similar), never an edit here.
