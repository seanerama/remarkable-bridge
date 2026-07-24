# Real-device fixtures

Genuine `.content` + `.metadata` files pulled **read-only** from the user's reMarkable
(`ssh remarkable`, xochitl dir `/home/root/.local/share/remarkable/xochitl/`) on
**2026-07-24** to close the `tablet-document` empirical-verification gate.

Provenance: captured via read-only `scp` FROM the device. **Zero writes** were made to
the tablet (no deletes, no uploads, no `touch`, no state-changing commands).

| uuid | formatVersion | type | fileType | note |
|------|---------------|------|----------|------|
| `b2258fba-865f-45cd-b94a-8d6abe83ef10` | 2 | DocumentType | notebook | pages are OBJECTS under `cPages.pages[]` |
| `e49bb79e-ffc1-4b5f-8290-f64628e396cd` | 1 | DocumentType | pdf | `pages` is a FLAT array of id strings + `redirectionPageMap` |
| `8352580e-521a-4084-ba0c-cd62bbd915f5` | 2 | DocumentType | pdf | "Learn the basics" — 7 pages; several carry REAL handwriting |

## Stroke pages (Stage 3 handwriting render)

Genuine v6 `.rm` stroke pages, pulled read-only on **2026-07-24**, back the handwriting
render tests (`tests/test_extract.py`, `tests/test_integration.py`):

| doc / page `.rm` | live strokes | used for |
|---|---|---|
| `8352580e…/869a62cc-6246-4d72-b37d-1498cbf9c06c.rm` | 91 | render → PNG, multi-page order |
| `8352580e…/c7418c51-56bf-4a7a-b79a-6312c8fc66bd.rm` | 143 | render → PNG, page-tag routing |
| `b2258fba…/ebb2c10a-20e2-40d4-bd1d-a8245583a2cd.rm`  | 0 (all erased) | robustness: no strokes → no image, no crash |

The `b2258fba` page is a real newer-firmware v6 file whose strokes are all CRDT deletion
tombstones (`deleted_length > 0`, no value subblock) — rmscene 0.8.0 parses it without
error and it correctly renders **no** image. The two `8352580e` pages have live `Line`
items whose points rmscene reads, which the render path draws to a legible monochrome PNG.

> **Deviation note:** the stage brief suggested `b2258fba…/ebb2c10a….rm` as the "real
> stroke page". On pull it turned out to have **no live strokes** (all erased), so it is
> kept here only as the blank/erased robustness fixture. The two `8352580e…` pages were
> pulled (still read-only, same device) to provide genuine handwriting for the non-empty
> PNG + multi-page tests.

Both real `formatVersion`s coexist on one device; the reader (`page_ids`, `read_tags`,
`parse_doc`) handles both without crashing.

## Tag arrays were EMPTY on capture

Both `tags` and `pageTags` (flat top-level arrays in `.content`) were `[]` on every doc —
the device had **no tagged documents**, so the tag-element wire shape is confirmed only as
"two top-level arrays", not its element JSON. The element convention (`{"name","timestamp"}`
for `tags`, `{"pageId","name"}` for `pageTags`) is NOT device-confirmed; the reader
therefore accepts both bare strings and `{"name": ...}` objects and never hard-fails on an
unknown element shape. See `docs/device-schema.md`.

No structural redaction was applied; page arrays were already small so nothing was truncated.
`.metadata` `visibleName`s are the stock reMarkable onboarding docs.
