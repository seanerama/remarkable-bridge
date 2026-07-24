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
