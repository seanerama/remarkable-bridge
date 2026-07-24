# 0001. Tech stack: Python + uv, subprocess SSH, rmscene/PyMuPDF, WeasyPrint

- **Status:** Accepted
- **Date:** 2026-07-24

## Context

`remarkable-bridge` is a host-side daemon that (a) reads the reMarkable Paper Pro's
on-device notebook data, (b) parses the v6 `.rm` stroke format, (c) shells out to
the Claude Code CLI, and (d) renders Markdown to PDF and pushes it back to the
tablet. The ecosystem that already solves (a)/(b) — `rmscene` (the v6 `.rm` parser),
`PyMuPDF`, and the upstream `remarkable-mcp` server — is **Python**. The Claude Code
CLI is invoked the same way from any language. So the tablet-facing half dictates
the language; nothing pulls the other way.

The stack guide's default lean: **boring, well-supported stacks; pin dependencies and
commit the lockfile from day one.**

## Decision

- **Language/runtime:** Python **3.11+** (needs stdlib `tomllib`).
- **Package manager:** **uv** (already a hard prerequisite for `uvx remarkable-mcp`).
  Commit `uv.lock` — reproducible builds from day one.
- **`.rm` parsing + page rendering:** **rmscene** (>= latest published; newest
  firmware needed >= 0.8.0) for typed text and stroke geometry; **PyMuPDF** for
  rasterizing a page to PNG when handwriting must go to the agent as an image.
- **Tablet transport:** the system **`ssh`/`scp` binaries via `subprocess`**, using a
  named host alias from `~/.ssh/config` — not a Python SSH library. (See ADR-0003.)
- **Markdown → PDF:** **WeasyPrint** (`markdown` → HTML → PDF with a print CSS).
- **Config:** **TOML** parsed with stdlib `tomllib`.

## Alternatives considered

- **Node/TypeScript for the whole daemon** — would force reimplementing or FFI-ing
  the `.rm` v6 parser; `rmscene` has no maintained JS equivalent. Rejected: the
  parsing ecosystem is Python.
- **PDF: `reportlab`** — pure-Python, zero native deps (WeasyPrint needs
  cairo/pango/gobject, `apt`-installable on the Ubuntu dev-server target). Rejected
  as the default because Markdown→PDF styling in reportlab is hand-built; WeasyPrint
  gets clean e-ink-friendly typography from print CSS for free. reportlab remains the
  documented fallback if the native deps ever bite.
- **PDF: `pandoc`** — excellent output but a heavyweight external binary (+LaTeX for
  nice PDFs). Rejected: too much to install/pin on the target for a one-column report.
- **Poetry / raw pip+venv** — fine, but `uv` is already required for this project and
  is faster; standardizing on one tool avoids two lockfile formats.

## Consequences

- One language across parse/agent/publish; contributors need only Python + uv.
- **Native deps on the target:** WeasyPrint pulls cairo/pango/gobject — must be in the
  dev-server provisioning and the CI image. If that friction ever exceeds its value,
  ADR-supersede to reportlab; the `publish` seam (contract `response-publish`) hides
  the choice from the rest of the system.
- `tomllib` is read-only and 3.11+; writing config (rare) uses `tomli-w` or hand
  templating. Acceptable — config is human-edited.
- Pinning via `uv.lock` makes the hygiene/test CI reproducible and is a prerequisite
  for the walking skeleton going green.
