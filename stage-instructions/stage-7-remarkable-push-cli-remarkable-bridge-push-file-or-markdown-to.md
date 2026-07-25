# Stage 7: reMarkable push CLI: remarkable-bridge push (file or markdown → tablet via cloud)

- **Type:** feature
- **Depends on:** 6 (reuses `bridge/cloud.py` transport seam) — reuses `bridge/publish.py` (md→PDF)

## Objectives

A small CLI, `remarkable-bridge push`, that uploads a document to a tablet folder (default
`/NS-Inbox`) via the reMarkable **cloud** (rmapi). Two forms: push an existing file, or
convert **markdown → PDF** (reuse Stage 1's `publish.py` renderer) then push. This is the
**tablet-delivery helper** the nightshift `note-ingest` worker uses to reply *to the tablet*
(the "or both" in content-dependent delivery), and a general-purpose push tool.

## What to build

- **`bridge/push.py`** + a console entry point `remarkable-bridge push` (add a `[project.scripts]`
  entry in `pyproject.toml`, e.g. `remarkable-bridge = "bridge.cli:main"`, or extend an
  existing CLI). Args:
  - `remarkable-bridge push <file.pdf> [--folder /NS-Inbox]` — upload an existing file.
  - `remarkable-bridge push --md <file.md> [--folder /NS-Inbox] [--title "<name>"]` — render
    markdown → PDF via the existing `PdfRenderer` (WeasyPrint prod / ReportLab fallback,
    ADR-0001), then upload. `--title` sets the tablet document name.
  - Uploads via **rmapi** (`rmapi put <pdf> <folder>`) through an **injected exec seam**
    (default shells `RMAPI_BIN`); tests never shell rmapi or hit the cloud.
  - Exit non-zero with a clear message on render/upload failure.
- The upload path is **create-only** (rmapi put mints a new doc); never deletes/overwrites.

## Interface contracts

- **Consumes:** `bridge/cloud.py`'s rmapi exec seam pattern (Stage 6), `bridge/publish.py`
  (md→PDF, `response-publish` renderer). No NEW contract; touches no frozen contract.
- **Exposes:** the `remarkable-bridge push` CLI (used by nightshift's `note-ingest` worker and
  operators). Its arg surface is small and stable.

## Testing requirements

- Unit (no cloud/rmapi): `push <file>` builds the correct `rmapi put <file> <folder>` argv
  (injected seam captures it); `--md` renders a real, valid PDF (assert `%PDF` magic) via the
  no-native-dep ReportLab renderer, then pushes it; non-zero rmapi exit → error surfaced;
  missing file → clear error.
- Runs in CI, no host.

## Acceptance conditions

- [ ] No kill-switch needed (a CLI is inert until invoked); it is additive tooling. (Note this
      explicitly — the feature "ships dark" by simply not being called.)
- [ ] UI-smoke authored: `remarkable-bridge push --md note.md` → doc appears in tablet `/NS-Inbox`.
- [ ] Additive only; create-only upload (no delete/overwrite). Existing suite stays green; CI all-green.

## Pipeline test: NO
