# Stage 6: reMarkable cloud engine: Outbound watcher (rmapi transport) → nightshift note-ingest

- **Type:** feature
- **Depends on:** none (reuses stages 1–3 modules: extract, state; NOT the retired review/execute dispatcher)

## Objectives

The **INBOX** half of the reMarkable ↔ assistant bridge, engine side. A watcher polls the
tablet's **`/Outbound`** folder via the reMarkable **cloud API** (works off-network),
extracts each new note (typed text + rendered handwriting PNGs — reuse Stage 3), and hands
it to the nightshift-assistant by shelling **`nightshift submit --type note-ingest`**. Runs
on the NSAF box (same host as nightshift, so it can reach the loopback control API). The
note-ingest job type + the worker's delivery (Webex/tablet) is nightshift's side (separate
stage). Ships **dark** (unit installed, not started, until enabled).

Scope note: this stage delivers the inbound path to *submission*. Tablet **push-back** of a
result (a `remarkable-bridge push` md→PDF→cloud CLI for the note worker) is a fast-follow
stage — not here.

## What to build

- **`bridge/cloud.py`** — a `CloudTabletClient` (implements the same read side as the
  `tablet-document` contract): shells **`rmapi`** (path from `RMAPI_BIN`, config on the host
  at `~/.config/rmapi/rmapi.conf`) to `ls <folder>` and `get <doc>` (download), unpacking
  the downloaded doc into a `RawDoc` (`.metadata`/`.content`/page `.rm` bytes) so the
  existing `extract()` works unchanged. The `rmapi` exec is an **injected seam** (tests
  never shell rmapi or hit the cloud). No delete method (safety rail, ADR-0003 posture).
- **`bridge/watch.py`** — the poll loop:
  - list `/Outbound` via `CloudTabletClient`; for each doc, compute the dedup key and skip
    already-processed docs (**reuse `bridge/state.py`**, dedup on `(uuid, content_hash)`);
  - for a new/changed doc: download, `extract(render_handwriting=True)` → text + page PNGs
    staged into a per-note workdir (e.g. `~/.local/share/remarkable-bridge/notes/<id>/`);
  - **submit to nightshift**: shell `nightshift submit --type note-ingest --params <json>`
    (the submit exec is an **injected seam**). Record state only **after** a successful
    submit (so a failure retries next cycle; never double-submits).
  - Graceful: cloud/rmapi unreachable or nightshift-submit failure → log, no state change,
    retry next cycle (reuse the Stage 2 `TabletUnreachable`-style handling).
- **`config.toml`** additions: `[cloud]` folder (`/Outbound`), `rmapi_bin`, poll interval,
  staging dir, `nightshift_bin` (path to the `nightshift` CLI); `[watch] enabled` flag.
- **`deploy/remarkable-bridge-watch.service`** — a systemd **user** unit template for the
  NSAF box (`ExecStart=… -m bridge.watch`), dark-safe (only start when enabled).
- Retire/keep-dark the old review/execute dispatcher (`watcher.py`): it's superseded by
  this + nightshift. Leave its code + tests intact (do not break the suite) but it is no
  longer the product entry point; note this in the README.

## Interface contracts

- **Consumes (frozen):** `tablet-document` (the `.metadata`/`.content`/tag shapes — cloud
  download yields the same files), `bridge-state` (dedup + atomic writes). The `agent-invocation`/`response-publish` contracts are NOT used here (no local agent; nightshift is the brain).
- **Exposes — the watcher→nightshift submission seam (freeze as a NEW contract
  `note-submission`):** `nightshift submit --type note-ingest --params` with JSON
  `{ note_id, doc_name, source_folder, text, images_dir }` (images_dir holds ordered
  `page-NN.png`). nightshift's `note-ingest` job type MUST consume exactly this shape. Run
  `verity contract new note-submission` and fill it; additive, no existing contract reopened.

## Testing requirements

- Unit (no cloud, no rmapi, no nightshift): `CloudTabletClient` parses a **fixture** rmapi
  `ls`/`get` output into `RawDoc`s (use a checked-in sample download); `watch` cycle over a
  fake cloud client + stub submit → asserts (a) a new note is extracted + a submit is issued
  with the frozen `note-submission` JSON, (b) a second cycle with no changes submits nothing
  (dedup), (c) a submit failure leaves state unrecorded (retry).
- Both external boundaries (rmapi, `nightshift submit`) injected/faked — runs in CI, no host.
- UI-smoke (operator, post-deploy): drop a real note in `/Outbound` → within one poll cycle a
  `note-ingest` job appears in nightshift (and, once nightshift's side lands, a Webex reply).

## Acceptance conditions

- [ ] Kill-switch `[watch] enabled` (default **OFF**) — unit installed but the loop is inert until enabled.
- [ ] UI-smoke authored: note in `/Outbound` → note-ingest submitted.
- [ ] New `note-submission` contract created (frozen v1); no existing frozen contract reopened; additive only.
- [ ] Dedup: unchanged `/Outbound` reprocesses nothing; submit-failure retries (no double-submit).
- [ ] `CloudTabletClient` has no delete method; existing suite stays green; CI all-green.

## Ops / deploy prerequisite (ship step, not this stage's code)

- remarkable-bridge deployed to the NSAF box (Python + uv + deps rmscene/PyMuPDF/reportlab).
  `rmapi` + `~/.config/rmapi/rmapi.conf` already present (from PUSH). The `nightshift` CLI is
  on the box PATH. Install `remarkable-bridge-watch.service`; enable only when going live.

## Pipeline test: NO
