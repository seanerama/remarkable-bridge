# Part 1 — live verification checklist (operator runs on-device)

Run these **after** registering `remarkable-mcp` (see `docs/part1-mcp-setup.md`). They
exercise the server's 7-tool surface against the **real tablet** inside a `claude`
session. **CI cannot run any of this** — it needs a physical tablet and an interactive
MCP session; the only automatable check is the command/doc lint
(`tests/test_part1_docs.py`).

## Preconditions

- `claude mcp list` shows **`remarkable`**.
- The tablet is docked and `ssh remarkable 'echo ok'` succeeds (USB `10.11.99.1`).
- You are in an interactive `claude` session (just run `claude`).

> How to run each check: ask Claude in plain language to call the named tool, or let it
> choose the tool from the request. The **tool call** column is the underlying MCP tool
> that should fire.

## The 7-tool checklist

| # | Ask Claude to… | Tool call | Pass when |
|---|---|---|---|
| 1 | check the tablet connection / status | `remarkable_status` | Reports connected; SSH reachable, no error. |
| 2 | list the root of my tablet | `remarkable_browse("/")` | Returns the top-level folders/documents (matches what's on the device). |
| 3 | show my most recently modified documents | `remarkable_recent` | Returns a recency-ordered list; the top item is the doc you edited most recently. |
| 4 | read my most recent **typed** notebook | `remarkable_read` (typed) | Returns the typed text of the document. |
| 5 | read my most recent **handwritten** page with OCR | `remarkable_read(include_ocr=True)` | Returns a plausible transcription of the handwriting (sampling OCR). |
| 6 | render / show me an image of that page | `remarkable_image` | Returns a page PNG that visually matches the page (needs `rmscene >= 0.8.0` on new firmware). |
| 7 | upload this PDF to my tablet | `remarkable_upload` | Server accepts the upload in **write mode**; a new document appears on the tablet. |

If **6** or **5** return blank/garbled on the Paper Pro (new firmware), confirm the
resolved `rmscene >= 0.8.0` (see setup doc) and try the image-based fallback.

## Part 1 acceptance criteria (must both pass live)

- [ ] **Summarize-recent-page:** in a `claude` session, ask *"summarize my most recent
      notebook page"* — Claude reads the page (typed or via OCR) and returns a **correct,
      on-topic** summary of what is actually on that page.
- [ ] **Upload-a-PDF:** ask Claude to **upload a PDF** to the tablet — the PDF **appears
      on the tablet** afterward (confirm on the device). This proves `--write` mode works
      end-to-end.

## Kill-switch check (optional)

- `claude mcp remove remarkable`, then `claude mcp list` — `remarkable` is gone and the
  tools are no longer available. Registration is user-scoped and reversible; the tablet
  is unchanged by removal.
