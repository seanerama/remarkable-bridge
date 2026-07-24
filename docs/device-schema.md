# Verified reMarkable on-device schema (2026-07-24)

Read-only SSH inspection of the user's device (`ssh remarkable`, xochitl =
`/home/root/.local/share/remarkable/xochitl/`, 3 documents). This closes the
`tablet-document` contract's **empirical-verification gate**. The frozen contract is NOT
edited — the reader is reconciled to what the device actually stores, additively.

> **Empirical gate CLOSED (2026-07-24):** confirmed tag locations = **top-level `tags[]` +
> top-level `pageTags[]`**; dual **`formatVersion` 1 and 2 coexist** on one device; tag
> **element shape pending a tagged sample** (both arrays were empty on capture — no tagged
> docs exist yet).

## `.content` — tags live in TWO flat, top-level arrays

Both `tags` (document-level) and `pageTags` (page-level) are **flat arrays at the top level**
of `.content` — NOT nested under `cPages.pages[].tags[]`:

```jsonc
{
  "formatVersion": 2,
  "tags": [],       // document-level, top-level
  "pageTags": [],   // page-level, top-level
  "cPages": { "pages": [ { "id": "...", "template": "Blank" } ] }
}
```

Both arrays were **empty** on every device doc (no tagged documents). The element shape is
therefore unconfirmed. The reader (`read_tags`) reads all three plausible locations
defensively — top-level `tags`, top-level `pageTags`, and the contract's expected
`cPages.pages[].tags[]` — with **top-level `tags` + `pageTags` as the primary path**, and
accepts **both bare strings and `{"name": ...}` objects** in every array. Unknown element
shapes are ignored, never fatal.

## `.content` — two `formatVersion`s coexist, different page shapes

| formatVersion | page shape | example |
|---|---|---|
| **2** | `cPages.pages[]` = **objects**, each `{ "id", "template", "idx", ... }` | `b2258fba…` (notebook) |
| **1** | `pages` = **flat array of id strings** + sibling `redirectionPageMap` | `e49bb79e…` (pdf) |

`page_ids(content)` returns `[page_id, ...]` for both shapes and yields `[]` (never raises)
on anything unexpected.

## `.metadata`

- `createdTime` / `lastModified` / `lastOpened` are **strings of ms-epoch** (e.g.
  `"1784918546774"`). `parse_doc` coerces `lastModified` via `int(...)`.
- `lastOpenedPage` is an int.
- `parent`: `""` = root, `"trash"` = trashed. **No `deleted` key on live docs** — trash is
  inferred from `parent == "trash"`.
- `type` is `"DocumentType"` or `"CollectionType"`.

## Fixtures

Captured samples live in `tests/fixtures/real/` (see its README) and back the reader tests
for both real `formatVersion`s. All access was strictly read-only; zero writes to the device.
