# Part 1 — `remarkable-mcp` setup (interactive tablet access in Claude Code)

Wire the **external** `remarkable-mcp` server into Claude Code so a human in a `claude`
session can browse / read / render / **write** the reMarkable tablet. This is the
interactive half of the project (ADR-0002 — Part 1). It is **independent** of the Part-2
watcher daemon and shares no code with it.

`remarkable-mcp` is an **external dependency we consume, not code we own** (ADR-0002).
Upstream: <https://github.com/SamMorrowDrums/remarkable-mcp> (Python; built on `rmscene`
+ PyMuPDF). We pin the version we register and track its tool/flag surface, but it never
enters our image set, CI matrix, or `deploy.sh`.

> **This document is instructions, not automation.** Registration is the **operator's
> live step** — running `claude mcp add` mutates your global `~/.claude.json`. Nothing in
> this repo runs it for you, and CI never touches a tablet.

Interactive Part 1 lives on the **workstation**, where the tablet is proven reachable
over **USB** (`10.11.99.1`) via the `remarkable` SSH alias (`.verity/deploy-access.md`).

## Prerequisites

- **Claude Code CLI** (verified with `v2.1.219`). Check: `claude --version`.
- **`uv` / `uvx`** on `PATH` (verified with `uv 0.11.21`). Check: `uvx --version`.
  `uvx` fetches and runs `remarkable-mcp` in an ephemeral environment — no manual
  `pip install` needed.

### 1. SSH: key auth + the `remarkable` host alias

`remarkable-mcp` reaches the tablet by shelling out to `ssh remarkable`, so that alias
must already work non-interactively (key-based, no password prompt).

Add to `~/.ssh/config` on the workstation (USB path — the primary Part-1 path):

```sshconfig
Host remarkable
    HostName 10.11.99.1        # USB address; only exists while the tablet is docked
    User root
    IdentityFile ~/.ssh/id_ed25519
    # Optional convenience for the on-device host key (it changes across OS updates):
    # StrictHostKeyChecking accept-new
```

Install your public key on the tablet once so auth is key-based (survives OS updates —
`/home/root/.ssh/authorized_keys` persists):

```bash
ssh-copy-id -i ~/.ssh/id_ed25519.pub remarkable
```

Verify (must succeed with **no password prompt**):

```bash
ssh remarkable 'echo ok && ls /home/root/.local/share/remarkable/xochitl/ | head'
```

- **The on-device SSH password and IP** are shown on the tablet under
  **Settings → Help → About → Copyright and licenses** (below "GPLv3 Compliance").
  Do **not** record the password in the repo.
- **WiFi variant (not used here):** a WLAN SSH endpoint exists via
  `ssh remarkable 'rm-ssh-over-wlan on'`, pointing the `remarkable` alias at the tablet's
  LAN/tailnet IP instead of `10.11.99.1`. That is the **Part-2 dev-server** path
  (ADR-0005). **Leave it OFF for interactive Part 1** — the USB alias is enough and is
  already proven.

### 2. Smoke test: `uvx remarkable-mcp --ssh`

Before registering, confirm the server starts and can reach the tablet. Run it directly
(it speaks MCP over stdio; it will block waiting for a client — start it, confirm it
launches without an SSH/import error, then `Ctrl-C`):

```bash
uvx remarkable-mcp --ssh
```

- **Pin a known-good version** for reproducibility, e.g.
  `uvx remarkable-mcp@<known-good-version> --ssh`. Record the exact version you verified
  here once chosen (upstream is a live external dep — pinning insulates the operator from
  a breaking upstream release).
- **Rendering / newest firmware:** rendering `.rm` pages requires **`rmscene >= 0.8.0`**.
  The reMarkable **Paper Pro is on new firmware**, whose `.rm` format only renders
  correctly with `rmscene >= 0.8.0`. If page rendering (`remarkable_image` /
  `remarkable_read(include_ocr=True)`) returns blank or errors, confirm the resolved
  `rmscene` version meets that floor.

## Registration (operator runs this live)

This is the **one live mutation** — it edits your global (`--scope user`) `~/.claude.json`:

```bash
claude mcp add remarkable --scope user -e REMARKABLE_OCR_BACKEND=sampling -- uvx remarkable-mcp --ssh --write
```

Flag-by-flag:

- **`--scope user`** — register **globally** for your user, not just this project
  directory. The server is available in every `claude` session.
- **`-e REMARKABLE_OCR_BACKEND=sampling`** — OCR uses the **client model** (Claude itself,
  via MCP sampling) to read handwriting. **No Google API key required.**
- **`--` separator** — everything after `--` is the **server launch command**
  (`uvx remarkable-mcp ...`), keeping the server's flags from being parsed by
  `claude mcp add`.
- **`--write`** (a server flag, after `--`) — enables **write mode** so the server can
  **upload PDFs** to the tablet. Without it the server is read-only and
  `remarkable_upload` is unavailable.

Confirm it registered:

```bash
claude mcp list        # should show: remarkable
```

Then run the on-device checklist: **`docs/part1-verify.md`** (or
`scripts/part1_verify.sh`).

### Fallbacks and optional env

- **Sampling OCR unreliable → image-based reading.** If `sampling` OCR misreads
  handwriting, fall back to reading the **rendered page image** directly:
  `remarkable_image` (get the PNG) or `remarkable_read(include_ocr=False)` for typed
  text, and let Claude read the image in-session rather than relying on server-side OCR.
- **`GOOGLE_VISION_API_KEY`** (optional) — set instead of `sampling` to use Google Cloud
  Vision OCR (`-e REMARKABLE_OCR_BACKEND=google -e GOOGLE_VISION_API_KEY=...`). Requires a
  Google key; only worth it if sampling OCR is consistently poor.
- **`REMARKABLE_ROOT_PATH`** (optional) — override the xochitl document root if your
  tablet stores documents somewhere other than the default
  `/home/root/.local/share/remarkable/xochitl/`.

## Reversal / kill-switch

Registration is **user-scoped and fully reversible**. To remove it:

```bash
claude mcp remove remarkable
```

This unregisters the server from `~/.claude.json`. There is **no app flag** to toggle —
this is external tooling, so add/remove *is* the kill-switch. Nothing is installed
system-wide (`uvx` runs the server ephemerally), and nothing on the tablet is changed by
removing the registration.
