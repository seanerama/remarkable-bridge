# Manual UI-smoke checks (Operator runs post-deploy)

These are the human-in-the-loop checks CI cannot run: they need the real tablet, a real
Claude Code CLI, and a human reading the result on the device. Run them after each deploy
that touches extraction, the agent, or publish.

## Stage 3 — handwriting review (`EXTRACT_HANDWRITING` ON)

**Goal:** a genuinely *handwritten* `review` page produces a coherent PDF summary back on
the tablet (acceptance test #1, full — the point of Stage 3).

**Preconditions**
- The bridge is deployed and the daemon is running (systemd, ADR-0005).
- `EXTRACT_HANDWRITING` is enabled — either `[extract] handwriting = true` in
  `config.toml`, or the `EXTRACT_HANDWRITING=1` env var on the service. Confirm it is ON
  (this feature ships dark; default is OFF).
- The tablet is reachable over SSH (`ssh remarkable` succeeds).

**Steps**
1. On the tablet, open (or create) a notebook page and **handwrite** a few lines — e.g. a
   short paragraph of notes with one clear question in it. Do not type; use the pen.
2. Tag it for the review route: add the `review` tag (document-level, or page-level on the
   handwritten page — both are supported; a page-level tag routes only that page).
3. Let the tablet sync, then wait one poll interval (~60s) for the daemon to pick it up.
4. On the tablet, open `/Claude/Responses` and find the new `Re: <name> (YYYY-MM-DD)` PDF.

**Expected**
- A new PDF appears in `/Claude/Responses` (create-only; nothing else is modified).
- Its content is a **coherent summary/response to what you actually wrote** — i.e. the
  agent read the handwriting from the rendered page image, not gibberish. If your note
  contained a question, the response should engage with it.
- Multi-page handwritten docs: the response reflects all tagged pages, in order.

**Pass/fail**
- PASS: the summary is on-topic and legibly reflects the handwritten content.
- FAIL (investigate): empty/placeholder PDF, a `needs-clarification` report on clearly
  legible input, or a summary unrelated to the page. Check `logs/<doc>-review.log` and the
  rendered `workspace/<doc>/pages/page-*.png` to see what the agent was actually shown.

**Kill-switch sanity (optional):** set `EXTRACT_HANDWRITING=0` (or `handwriting = false`)
and re-run with a handwritten page — the job should carry **no** page images and behave
exactly like Stage 1/2 (typed-text-only). This confirms the dark-launch default is safe.
