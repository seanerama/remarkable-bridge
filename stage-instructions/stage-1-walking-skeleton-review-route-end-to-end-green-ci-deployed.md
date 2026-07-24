# Stage 1: Walking skeleton: review route end-to-end, green CI, deployed

- **Type:** chore
- **Depends on:** none

## Objectives

Prove the spine. The thinnest **vertical** slice: a `review`-tagged document on the
tablet becomes a PDF in `/Claude/Responses`, end to end — real (but minimal) in
production, faked at two boundaries in CI. Blocks every later stage (walking-skeleton
guide). Done means: green CI **and** one real cycle deployed on the target.

Scope discipline: handle the **simplest real document** only — a single page with
**typed text** (no handwriting render yet) and a document-level `review` tag. Depth
(handwriting, robust scanning, execute route) is later stages — but no boundary is
*faked in production*: the deployed daemon does a genuine tablet→claude→tablet cycle.

## What to build

- **Project skeleton:** `pyproject.toml` (Python 3.11+), `uv.lock` committed, package
  `bridge/` with `__init__.py`, `watcher.py`, `tablet.py`, `extract.py`, `agents.py`,
  `publish.py`, `state.py`, plus `config.toml` and `prompts/review.md`.
- **Injected interfaces** (the seams that make CI hardware-free):
  `TabletClient` (SSH scan/pull/upload — **no delete method**), `AgentRunner`
  (`claude -p` wrapper), `Clock`. Real impls for production; fakes for tests.
- **The review pipeline, one cycle:** `watcher.run_once()` → `tablet.scan()` →
  detect a `review`-tagged, un-seen doc → `extract` typed text → `agents.run(review)`
  → `publish` Markdown→PDF→upload → `state.record()`.
- **CI test lane:** extend `.github/workflows/ci.yml` with a `test` job (`uv sync` +
  `pytest`) alongside the existing hygiene gates. Provision WeasyPrint native deps
  (cairo/pango/gobject) in that job.
- **Minimal `deploy.sh`** to the NSAF dev server (ADR-0005): `uv sync`, provision
  native deps, install+enable a systemd unit, restart. (Mature deploy is `/verity:ship`.)
- Checked-in **fixtures**: sample `<uuid>.metadata` + `.content` (tagged `review`) +
  one typed-text page, and a canned `AgentRunner` reply.

## Interface contracts

- **Consumes (frozen — must not break):** `tablet-document` (scan → `TabletDoc`),
  `agent-invocation` (`AgentRunner.run`), `response-publish` (`publish`),
  `bridge-state` (dedup). This stage is the **reference implementation** of all four.
- **Exposes:** the `bridge` package + the three injected interfaces for later stages to
  deepen. No new contract (additive only).

## Testing requirements

- **One real integration test** (the walking-skeleton test): drive the whole pipeline
  against fixtures with a **fake `TabletClient`** + **stub `AgentRunner`** + fixed
  `Clock`. Assert: (a) a PDF named `Re: <name> (YYYY-MM-DD)` is produced; (b) the fake
  client received an upload to `/Claude/Responses`; (c) `state.json` records the doc;
  (d) a **second** cycle with unchanged fixtures processes **nothing** (dedup /
  acceptance test #3). Real modules exercised; only the two external boundaries faked.
- Runs in CI with no device and no model.

## Acceptance conditions

- [ ] `review`-tagged typed-text page → PDF appears in `/Claude/Responses` on the real
      tablet in one real cycle on the deploy target (acceptance test #1, typed subset).
- [ ] Re-run with no changes reprocesses nothing (dedup, acceptance test #3).
- [ ] The one integration test passes; CI all-green (hygiene + new `test` job).
- [ ] `uv.lock` committed; `deploy.sh` installs+enables the systemd unit on the dev server.
- [ ] `TabletClient` exposes no delete/overwrite of tablet docs (structural safety rail).

## Pipeline test: NO
