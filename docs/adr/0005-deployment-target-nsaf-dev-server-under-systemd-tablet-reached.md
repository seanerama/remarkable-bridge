# 0005. Deployment target: NSAF dev server under systemd, tablet reached over WiFi SSH

- **Status:** Accepted
- **Date:** 2026-07-24

## Context

This is **not a web service** — there is no public URL to host. The watcher is a
long-running daemon whose host must satisfy two hard constraints simultaneously:

1. an **authenticated `claude` CLI** (the agent runtime, ADR-0004), and
2. **network reach to the tablet over SSH** (the transport, ADR-0003).

That immediately eliminates most of the operator's catalog: **Cloudflare Pages**
(static/Functions only), **Coolify** (promoted web apps), and **EC2** (remote AWS —
can't see a USB tablet, and provisioning `claude` auth + tablet reach there is a
stretch). The real choice was **local workstation** vs the **NSAF dev server**.

## Decision

**Deploy to the NSAF dev server (`nsaf-dev-server`, Tailscale `100.110.222.42`) as a
`systemd` service**, per the catalog note that new services run under systemd there,
not nohup. The box is always-on and already hosts an authenticated Claude CLI — so a
60-second poll loop runs 24/7 without a laptop being awake.

Because the dev server is **not** the USB host, the tablet is reached over **WiFi SSH**
(`rm-ssh-over-wlan on`), not the USB alias `10.11.99.1`. The daemon uses a
`remarkable` host entry pointing at the tablet's **LAN/tailnet IP**, configured on the
dev server (see `.verity/deploy-access.md`).

**Hard prerequisite (blocks first deploy):** the dev server must actually be able to
reach the tablet's WiFi SSH endpoint — i.e. tablet and dev server share a reachable
network (same LAN, or the tablet's SSH port bridged onto the tailnet). This must be
proven before the walking skeleton can "deploy + run green" on the target.

## Alternatives considered

- **Local workstation (WSL2) as a systemd *user* unit** — USB reach to `10.11.99.1` is
  already proven and needs no new networking; natural "dock the tablet → it processes"
  flow. Rejected as the *primary* target because WSL2 isn't always-on and systemd in
  WSL2 is awkward, so the poll loop wouldn't run unattended. **Kept as the documented
  fallback / dev-loop target** (identical package, different unit + host alias) — and
  it is where interactive Part 1 naturally lives.
- **EC2 (`ec2-primary`)** — always-on and arm64/systemd-capable, but has no line of
  sight to the tablet and no reason to hold `claude` auth. Rejected.
- **Coolify / Cloudflare Pages** — wrong shape (web-app/static hosts) for a
  tablet-tethered daemon. Rejected.

## Consequences

- **Reachability is now a first-class operational dependency.** If the tablet leaves
  the dev server's network, polls fail — the watcher must degrade gracefully (log
  "unreachable", retry next cycle, never crash or reprocess) per the spec's acceptance
  test #4. This shapes the `watcher` module's error handling.
- `deploy.sh` (owned later by `/verity:ship`) targets the dev server over SSH: sync the
  repo, `uv sync`, install/enable the systemd unit, restart. Native WeasyPrint deps
  (ADR-0001) must be provisioned on the box.
- Credential/reach details live in `.verity/deploy-access.md` (gitignored, locations
  only) — the tablet WiFi host entry, the dev-server SSH identity, and where `claude`
  auth lives on the box.
- The tablet's WiFi IP can change (DHCP); the deploy-access file documents how to
  refresh the host entry. A static reservation or hostname is the durable fix.
