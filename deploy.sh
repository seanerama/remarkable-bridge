#!/usr/bin/env bash
#
# deploy.sh — minimal first-cut deploy of remarkable-bridge to the NSAF dev server
# (ADR-0005). Syncs the repo, provisions WeasyPrint native deps, installs + enables the
# systemd unit, and restarts. The MATURE deploy is owned later by /verity:ship — this is
# just enough to prove the target during the walking skeleton.
#
# Prerequisites (see .verity/deploy-access.md — gitignored, locations only):
#   * SSH reach to the dev server:      ssh smahoney@100.110.222.42   (Tailscale)
#   * An authenticated `claude` CLI + `uv` already present on the box (ADR-0005).
#   * The dev server can reach the tablet over WiFi SSH via the `remarkable` host alias
#     (NOT the USB 10.11.99.1 address) — prove this BEFORE first deploy (ADR-0005 gate).
#
# Usage:  ./deploy.sh [user@host]     (default: smahoney@100.110.222.42)
set -euo pipefail

TARGET="${1:-smahoney@100.110.222.42}"
APP_DIR="remarkable-bridge"          # relative to the service user's home on the box
UNIT="remarkable-bridge.service"

echo ">> Deploying remarkable-bridge to ${TARGET}"

# 1. Ship the repo (create-only on the box; excludes VCS + local state/artifacts).
echo ">> Syncing repo"
rsync -az --delete \
  --exclude '.git' --exclude 'out' --exclude 'logs' --exclude 'workspace' \
  --exclude '.venv' --exclude '__pycache__' \
  ./ "${TARGET}:${APP_DIR}/"

# 2. Provision + sync on the box, install the user systemd unit, (re)start.
echo ">> Provisioning native deps, syncing deps, installing systemd unit"
ssh "${TARGET}" APP_DIR="${APP_DIR}" UNIT="${UNIT}" 'bash -s' <<'REMOTE'
set -euo pipefail
cd "${HOME}/${APP_DIR}"

# WeasyPrint native deps (cairo/pango/gobject — ADR-0001). Non-interactive; skip if
# apt/sudo is unavailable (a box provisioned out-of-band still deploys).
if command -v apt-get >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
  sudo apt-get update -y
  sudo apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangocairo-1.0-0 libcairo2 \
    libgdk-pixbuf-2.0-0 libffi-dev libharfbuzz0b fonts-dejavu-core
else
  echo "!! Skipping apt provisioning (no sudo/apt) — ensure WeasyPrint native deps exist"
fi

# Reproducible install from the committed lockfile.
uv sync --frozen

# Install + enable the systemd USER unit (per ADR-0005: systemd, not nohup).
mkdir -p "${HOME}/.config/systemd/user"
cp "deploy/${UNIT}" "${HOME}/.config/systemd/user/${UNIT}"
systemctl --user daemon-reload
systemctl --user enable "${UNIT}"
systemctl --user restart "${UNIT}"
# Let the unit linger so the poll loop runs 24/7 without an active login session.
loginctl enable-linger "$(whoami)" || true

echo ">> Service status:"
systemctl --user --no-pager status "${UNIT}" || true
REMOTE

echo ">> Deploy complete. Tail logs with:"
echo "   ssh ${TARGET} journalctl --user -u ${UNIT} -f"
