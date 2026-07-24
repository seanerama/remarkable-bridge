# Stage 3: Handwriting extraction: rmscene text + PyMuPDF page render

- **Type:** feature
- **Depends on:** 1

## Objectives

Extend `extract` beyond Stage 1's typed-text-only path to the real reMarkable content:
parse **typed text** via `rmscene` and, for **handwriting**, render the page to a PNG
that the agent reads directly (Claude reads handwriting from images — no separate OCR
pipeline). This makes `review` work on genuinely handwritten pages (acceptance test #1,
full).

## What to build

- `extract.extract(doc) -> {text: str|None, page_images: [Path]}` per the `AgentJob`
  fields in the `agent-invocation` contract.
- **Typed text:** parse v6 typed blocks via `rmscene`.
- **Handwriting:** render each tagged page's strokes to a PNG via `PyMuPDF` (or
  rmscene→SVG→raster), at a resolution legible to the model; multi-page docs produce
  ordered images.
- **Route only the tagged page(s)** when a page-level tag is present; whole doc when the
  tag is document-level.
- A dark-launch flag `EXTRACT_HANDWRITING` (default **OFF** → typed-text-only, i.e.
  Stage 1 behavior) so the feature ships dark and is enabled by config.

## Interface contracts

- **Consumes (frozen):** `tablet-document` (which pages/tags), `agent-invocation`
  (produces the `text` + `page_images` the runner already expects). No new contract —
  these output fields are already in `AgentJob`.
- **Exposes:** the real `extract` used by both `review` and (later) `execute`.

## Testing requirements

- Unit tests over checked-in `.rm` fixtures: a typed-text page → expected text; a
  handwritten page → a non-empty PNG of expected dimensions (pixel-exact not required;
  assert file is a valid image + page count/order).
- Integration: the Stage 1 review test re-run with a **handwritten** fixture and flag
  ON still yields a PDF (stub `AgentRunner` echoes it saw N images).
- UI-smoke: with the flag ON, a real handwritten `review` page yields a coherent PDF
  summary on the tablet (Operator runs post-deploy).

## Acceptance conditions

- [ ] Kill-switch `EXTRACT_HANDWRITING` (default OFF) gates the net-new render path.
- [ ] UI-smoke authored: real handwritten `review` page → sensible PDF on the tablet.
- [ ] Additive only — typed-text path (Stage 1) unchanged when flag OFF.
- [ ] Existing suite stays green; CI all-green.

## Pipeline test: NO
