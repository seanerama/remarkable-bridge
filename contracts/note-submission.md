# Contract: note-submission

- **Status:** frozen v1
- **Owner:** the reMarkable **watcher** (`bridge/watch.py`) produces it; the
  nightshift-assistant **`note-ingest` job type** consumes it. Cross-repo seam — both
  sides depend on this exact shape.

## Exposes

When the watcher detects a new note in the tablet's `/Outbound` folder, it submits it to
nightshift by shelling:

```
nightshift submit --type note-ingest --params '<JSON>'
```

## Consumes

- `TabletDoc` fields (contract `tablet-document`) for `note_id` / `doc_name`.
- The extraction output (contract-internal): typed `text` + ordered handwriting page PNGs.
- nightshift's `nightshift submit` CLI (control-api v1) — the transport; this contract
  fixes only the `--type` and `--params` payload.

## Schema / wire

`--type` is exactly `note-ingest`. `--params` is a JSON object, frozen v1:

```jsonc
{
  "note_id":       "<uuid>",      // tablet document uuid (stable per note)
  "doc_name":      "<string>",    // visibleName of the note
  "source_folder": "/Outbound",   // where it was picked up
  "text":          "<string>",    // extracted typed text; "" if none
  "images_dir":    "<abs path>"   // dir holding ordered page-NN.png handwriting renders;
                                  // absent/"" if the note had no rendered pages
}
```

Rules (frozen):

- `images_dir`, when present, contains zero-padded `page-00.png`, `page-01.png`, … in page
  order. The note-ingest worker is granted read access to this dir and reads them as the
  handwritten note content.
- At least one of `text` (non-empty) or `images_dir` (with ≥1 image) is present; a note with
  neither is not submitted (nothing to act on).
- `note_id` is the dedup identity on the watcher side (contract `bridge-state`); nightshift
  MAY use it for its own idempotency but is not required to.
- The worker interprets the note and chooses delivery **by content**: reply in Webex
  (`nightshift deliver`) and/or push a result to the tablet — that behavior is nightshift's,
  not fixed here.

## Versioning

Frozen at **v1**. Changes are **additive only** — a breaking change is a NEW contract, not
an edit (framework-spec §4.3). New optional `--params` fields are additive; renaming/removing
a field or changing `--type` is a new contract. Both the watcher and the `note-ingest` job
type are pinned to this shape.
