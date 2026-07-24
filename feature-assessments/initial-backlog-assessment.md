# Intake assessment — initial backlog (Mode A)

- **Date:** 2026-07-24
- **Source:** the locked architecture (`docs/adr/0001–0005`, `contracts/`,
  `docs/walking-skeleton.md`) from `/verity:architect`.
- **Decision:** ACCEPT as a thin, dependency-ordered backlog of 5 stages. No new
  contract (every seam is covered additively by the four frozen contracts). No new ADR
  (no architecture-affecting change surfaced).

## Claim/reality verification (anti-hallucination)

| Claim | Reality | Verdict |
|---|---|---|
| Greenfield — skeleton builds from zero | `git ls-files` → no source files | ✅ |
| Python 3.11+ | 3.12.3 | ✅ |
| uv/uvx present | uv 0.11.21, uvx present | ✅ |
| claude CLI present | present, v2.1.219 (flags architect-verified) | ✅ |
| 4 contracts frozen | `verity contract list` → all 4 | ✅ |
| `remarkable-mcp`/`rmscene` installable | not verified here | ⚠ deferred to in-stage install acceptance (Stages 3, 5) |

## Decomposition rationale

Walking-skeleton-first (stack guide): the spine is **one vertical stage**, not a
horizontal layer cake — avoids the "9 stages done before CI ran green" failure. Later
stages **deepen each seam** additively rather than adding faked layers.

- **Stage 1 — Walking skeleton (chore, no deps).** Thinnest vertical slice: `review`
  route, typed-text/single-page subset, real one-cycle deploy + one green integration
  test with the two external boundaries (`TabletClient`, `AgentRunner`) faked in CI
  only. Reference implementation of all four contracts. **Blocks everything.**
- **Stage 2 — Robust tablet transport + v6 tag verification (chore, dep 1).** Closes
  the architecture's *empirical gate* (verify real `.content` tag shape on-device) and
  makes scanning efficient + unreachable-aware. SPLIT from Stage 1 to keep the skeleton
  thin.
- **Stage 3 — Handwriting extraction (feature, dep 1).** Adds rmscene text + PyMuPDF
  page-render so `review` works on real handwriting; dark-launch flag. SPLIT from the
  skeleton (skeleton is typed-text only).
- **Stage 4 — Execute route + sandbox (feature, dep 1; soft-after 3).** The
  second, highest-risk route; behind rails + `EXECUTE_ENABLED` off by default. Its
  route/allowlist is already named in the frozen `agent-invocation` contract → additive.
- **Stage 5 — Interactive Part 1 / remarkable-mcp (feature, no deps).** Independent
  parallel track (ADR-0002): setup + verification docs, no owned contract. Can run
  alongside Stages 1–4.

## Deferred (born later, Mode B — not in this thin backlog)

- Ops hardening: full unreachable backoff, systemd hardening, launchd/workstation
  fallback variant, DHCP/hostname stability for the tablet WiFi IP.
- README/service-template polish and the mature `deploy.sh` (owned by `/verity:ship`).
- Multi-page/large-notebook performance, OCR-quality tuning.

Rationale for deferral: none blocks the spine or the two routes; specifying them now
would be a giant upfront plan. They enter the stream once Stages 1–4 exist and real
usage shows what matters.

## Rejected

- **helper-bot** drop-in feature — declined at architecture (no web UI to host it).
