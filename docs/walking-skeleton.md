# Walking Skeleton (Stage 0)

> The thinnest end-to-end slice that **compiles, runs, passes one real test, goes green
> in CI, and deploys.** It proves the spine before any feature work and blocks all
> feature stages (stack guide: *walking skeleton first*). Handed to `/verity:plan` to
> decompose.

## The one vertical slice

**A `review`-tagged document becomes a PDF in `/Claude/Responses`, end to end — with
every external boundary injectable so CI needs no device and no live model.**

Concretely, one poll cycle:

1. `watcher` runs one cycle → `tablet.scan()` returns changed docs (contract
   `tablet-document`).
2. Detect a doc tagged `review` not already in `state` (contract `bridge-state`).
3. `extract` pulls its content → typed text and/or a rendered page PNG.
4. `agents.run(review-job)` invokes `claude -p` (contract `agent-invocation`) → Markdown.
5. `publish` renders Markdown → PDF, uploads to `/Claude/Responses` (contract
   `response-publish`).
6. `state.record(...)` commits atomically **after** upload.

Only the **`review`** route is in Stage 0. `execute` (and its sandbox safety rails) is
a later stage. Publishing always emits *something*, including `failed`/`needs_clarification`.

## The seams (all injected — this is what makes it testable)

- `TabletClient` — SSH transport (ADR-0003). **Fake** in tests; no delete method.
- `AgentRunner` — `claude -p` wrapper (ADR-0004). **Stub** in tests returns canned
  Markdown; no network, no model.
- `Clock` — injected date for the `Re: … (YYYY-MM-DD)` name (tests are deterministic).

## The one real test (runs in CI, no hardware)

Drive the whole pipeline against **checked-in fixtures**: a sample `.metadata` +
`.content` (tagged `review`) + a `.rm` page, a fake `TabletClient` serving them, and a
stub `AgentRunner`. Assert: (a) a PDF is produced with the frozen name, (b) the fake
client received an upload to `/Claude/Responses`, (c) `state.json` records the doc, and
(d) a second cycle with unchanged fixtures processes **nothing** (dedup). This is a
genuine integration test of the real modules — only the two external boundaries are
faked. It joins the hygiene gate; CI must be green.

## Empirical gate (do this inside Stage 0, on the user's device)

- **Verify the v6 tag JSON shape** by inspecting one real tagged `.content` file and,
  if it differs from contract `tablet-document`, fix the reader **before** freezing.
- **Prove dev-server → tablet WiFi SSH reachability** (ADR-0005 prerequisite) so the
  deploy step is real, not theoretical.

> **Empirical gate CLOSED (Stage 2, 2026-07-24):** confirmed tag locations = top-level
> `tags[]` + `pageTags[]` (flat, NOT nested under `cPages.pages[].tags[]`); dual
> `formatVersion` 1 and 2 coexist on one device (fv2 = page objects under `cPages.pages[]`,
> fv1 = flat array of page-id strings + `redirectionPageMap`); tag **element** shape pending
> a tagged sample (both arrays empty on capture — no tagged docs exist yet). Reader
> reconciled additively; frozen contract untouched. Full detail + real fixtures:
> `docs/device-schema.md`, `tests/fixtures/real/`.

## Deploy (proves the target, ADR-0005)

Install + enable the systemd unit on the NSAF dev server via a minimal `deploy.sh`
(`/verity:ship` owns the mature version): `uv sync`, provision WeasyPrint native deps,
enable the unit, one live cycle against the real tablet lands a PDF on the device.

## Done = the spine is green

CI green (hygiene + the one integration test), the daemon runs one real cycle on the
deploy target, and a real `review`-tagged page yields a PDF on the tablet. Everything
after this is additive, contract-compatible stages.

## Explicitly NOT in Stage 0 (later stages, for `/verity:plan`)

- `execute` route + per-document sandbox + safety rails.
- systemd unit hardening, unreachable-tablet backoff, launchd/workstation variant.
- Handwriting-OCR quality tuning, multi-page/large-notebook handling.
- Interactive Part 1 (`remarkable-mcp` registration) — a separate, parallel track that
  shares only the tablet and the `tablet-document` contract (ADR-0002).
