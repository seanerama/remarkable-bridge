# Contract: response-publish

- **Status:** frozen v1
- **Owner:** `publish` module.

## Exposes

```
publish(result: AgentResult, doc: TabletDoc, when: date) -> PublishedDoc
```

Turns an `AgentResult` (contract `agent-invocation`) into a PDF and uploads it to the
tablet. Returns:

```
PublishedDoc = {
  pdf_path: Path,          # local artifact under out/
  visible_name: str,       # see naming rule
  tablet_parent: str,      # resolved uuid of /Claude/Responses
  uploaded: bool,
}
```

## Consumes

- **Markdown → PDF:** `markdown` → HTML → **WeasyPrint** with a print CSS tuned for
  e-ink (single column, generous margins, high-contrast) — ADR-0001.
- **Upload:** the `TabletClient` write path over SSH (ADR-0003): ensure the
  `/Claude/Responses` folder exists (create the collection if missing — writes are
  SSH-mode; the tablet UI restarts after write, which is expected), then write the PDF
  document tree under `xochitl/`.

## Schema / wire

- **Naming (frozen):** `visible_name = "Re: <doc.visible_name> (<YYYY-MM-DD>)"`.
- **Destination (frozen):** folder path `/Claude/Responses` on the tablet. Created if
  absent; never deletes or overwrites prior responses — each run is a new document
  (append-only history on the device).
- **Status surfacing:** the PDF's first line reflects `result.status` so a
  `needs_clarification` / `timed_out` / `failed` outcome is visible on the tablet, not
  hidden — the pipeline **always** publishes something for a processed doc.
- **Never delete.** This contract exposes create-only tablet writes. No update/delete
  of existing tablet documents (safety rail, ADR-0003/0004).
- A run is only recorded as processed in `bridge-state` **after** `uploaded == true`,
  so a failed upload retries next cycle rather than being lost.

## Versioning

Frozen at **v1**. Changes are **additive only** — a breaking change is a NEW contract,
not an edit (framework-spec §4.3). Changing the naming template or the destination
folder in a way existing consumers rely on is a new contract; adding optional metadata
(e.g. tags on the response doc) is additive.
