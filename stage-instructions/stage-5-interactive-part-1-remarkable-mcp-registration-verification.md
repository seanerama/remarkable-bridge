# Stage 5: Interactive Part 1: remarkable-mcp registration + verification

- **Type:** feature
- **Depends on:** none (independent parallel track — shares only the tablet and the
  `tablet-document` understanding; ADR-0002)

## Objectives

Deliver Part 1 of the spec: wire the **external** `remarkable-mcp` server into Claude
Code so a human in a `claude` session can browse/read/render/write the tablet. This is
independent of the Part-2 watcher (no shared code) and can proceed in parallel.

## What to build

Not application code — **repeatable setup + verification**, captured in the repo:

- `docs/part1-mcp-setup.md`: the vetted steps —
  - ensure SSH key auth + the `remarkable` host alias (USB `10.11.99.1`; WiFi variant
    noted);
  - `uvx remarkable-mcp --ssh` smoke test (pin a known-good version; note the
    `rmscene >= 0.8.0` rendering requirement for newest firmware);
  - `claude mcp add remarkable --scope user -e REMARKABLE_OCR_BACKEND=sampling -- uvx remarkable-mcp --ssh --write`
    (write mode enabled so it can upload PDFs);
  - fallback notes: image-based reading if `sampling` OCR is unreliable; optional
    `GOOGLE_VISION_API_KEY`/`REMARKABLE_ROOT_PATH`.
- A **verification checklist** (script or doc) exercising: `remarkable_status`,
  `remarkable_browse("/")`, `remarkable_recent`, `remarkable_read` (typed),
  `remarkable_read(include_ocr=True)`, `remarkable_image`, `remarkable_upload`.

## Interface contracts

- **Consumes:** the external `remarkable-mcp` tool surface (tracked, versioned — not a
  frozen contract we own) and the shared understanding of the on-device format
  (`tablet-document`). **Owns/exposes no contract** — it's an external dependency
  (ADR-0002).
- Introduces **no new contract** and touches none of the frozen four.

## Testing requirements

- CI cannot reach a tablet or run MCP interactively → **no live CI test**. Instead:
  a lint/parse check that the documented `claude mcp add` command and env vars are
  well-formed, and the checklist file exists.
- UI-smoke (Operator, live): in a `claude` session, "summarize my most recent notebook
  page" returns a correct answer, and Claude uploads a PDF that appears on the tablet
  (Part 1 acceptance criteria).

## Acceptance conditions

- [ ] Kill-switch: registration is user-scoped and reversible (`claude mcp remove`);
      documented. (No app flag — this is external tooling.)
- [ ] UI-smoke authored: summarize-recent-page + upload-a-PDF both verified live.
- [ ] Additive only; independent of Part 2 (no shared code).
- [ ] `claude mcp list` shows `remarkable`; the 7-tool checklist passes on-device.
- [ ] Existing suite stays green; CI all-green.

## Pipeline test: NO
