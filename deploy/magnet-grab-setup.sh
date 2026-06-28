#!/usr/bin/env bash
# magnet-grab-setup.sh — one-command setup for magnet-grab ON THE VPS.
#
# Run this from the find-a-home checkout on the VPS:
#
#     ./deploy/magnet-grab-setup.sh                       # install + start the service
#     ./deploy/magnet-grab-setup.sh "magnet:?xt=urn:..."  # install, start, AND trigger a download now
#
# It is idempotent: safe to re-run. It installs aria2, fills in any missing
# magnet-grab settings in .env (generating a token if needed), installs and
# starts the systemd service, verifies Telegram, and — if you pass a magnet —
# kicks off that download immediately so you get the Telegram links.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO_DIR/.env"
SERVICE_NAME="magnet-grab"
RUN_USER="${SUDO_USER:-$(id -un)}"
PORT_DEFAULT="8800"

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }

# --- 1. python + aria2 -------------------------------------------------------
PYTHON_BIN="$REPO_DIR/venv/bin/python"
[ -x "$PYTHON_BIN" ] || PYTHON_BIN="$(command -v python3)"
log "Using Python: $PYTHON_BIN"

if ! command -v aria2c >/dev/null 2>&1; then
  log "Installing aria2 (needs sudo)…"
  sudo apt-get update -qq && sudo apt-get install -y aria2
else
  log "aria2c already installed: $(aria2c --version | head -1)"
fi

# --- 2. .env settings --------------------------------------------------------
touch "$ENV_FILE"

ensure_env() {  # ensure_env KEY DEFAULT_VALUE
  local key="$1" default="$2"
  if grep -qE "^${key}=" "$ENV_FILE"; then
    return
  fi
  printf '%s=%s\n' "$key" "$default" >>"$ENV_FILE"
  log "Added ${key} to .env"
}

if ! grep -qE '^TELEGRAM_BOT_TOKEN=' "$ENV_FILE" || ! grep -qE '^TELEGRAM_CHAT_ID=' "$ENV_FILE"; then
  warn "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not found in $ENV_FILE."
  warn "magnet-grab reuses find-a-home's bot — make sure those two are set."
fi

if ! grep -qE '^MAGNET_GRAB_TOKEN=' "$ENV_FILE"; then
  TOKEN="$("$PYTHON_BIN" -c 'import secrets; print(secrets.token_urlsafe(24))')"
  printf 'MAGNET_GRAB_TOKEN=%s\n' "$TOKEN" >>"$ENV_FILE"
  log "Generated MAGNET_GRAB_TOKEN and saved it to .env"
fi

if ! grep -qE '^MAGNET_GRAB_PUBLIC_URL=' "$ENV_FILE"; then
  GUESS_IP="$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || echo YOUR_VPS_IP)"
  ensure_env MAGNET_GRAB_PUBLIC_URL "http://${GUESS_IP}:${PORT_DEFAULT}"
  warn "Set MAGNET_GRAB_PUBLIC_URL in .env to a URL your phone can reach (guessed: http://${GUESS_IP}:${PORT_DEFAULT})."
fi
ensure_env MAGNET_GRAB_PORT "$PORT_DEFAULT"
ensure_env MAGNET_GRAB_DOWNLOAD_DIR "$REPO_DIR/downloads"

# --- 3. systemd service ------------------------------------------------------
log "Installing systemd unit '$SERVICE_NAME' (user=$RUN_USER, dir=$REPO_DIR)…"
sudo tee "/etc/systemd/system/${SERVICE_NAME}.service" >/dev/null <<UNIT
[Unit]
Description=magnet-grab — magnet link downloader with Telegram notifications
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${REPO_DIR}
EnvironmentFile=${ENV_FILE}
ExecStart=${PYTHON_BIN} -m magnet_grab serve
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable --now "$SERVICE_NAME"
sleep 1
sudo systemctl --no-pager --lines=5 status "$SERVICE_NAME" || true

# --- 4. verify Telegram ------------------------------------------------------
log "Verifying Telegram…"
( cd "$REPO_DIR" && "$PYTHON_BIN" -m magnet_grab telegram-test ) || \
  warn "Telegram test failed — check TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID in .env."

# --- 5. optional: trigger a download now ------------------------------------
if [ "${1:-}" != "" ]; then
  log "Triggering download for the supplied magnet…"
  ( cd "$REPO_DIR" && "$PYTHON_BIN" -m magnet_grab add "$1" )
fi

TOKEN_VALUE="$(grep -E '^MAGNET_GRAB_TOKEN=' "$ENV_FILE" | head -1 | cut -d= -f2-)"
URL_VALUE="$(grep -E '^MAGNET_GRAB_PUBLIC_URL=' "$ENV_FILE" | head -1 | cut -d= -f2-)"
log "Done. Bookmark this on your phone:"
printf '    %s/?token=%s\n' "$URL_VALUE" "$TOKEN_VALUE"
warn "Make sure port ${PORT_DEFAULT} is open in your firewall / security group."
