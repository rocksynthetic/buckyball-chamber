#!/usr/bin/env bash
# One-shot setup for the buckyball chamber client on Linux/Raspberry Pi:
# clones/updates the repo, installs system + Python dependencies, runs the
# device/SFTP setup wizard, and optionally installs it as a systemd service.
# Idempotent -- safe to re-run (e.g. to pull updates or redo config).
#
# Usage:
#   ./setup_client.sh [--dir <path>] [--repo-url <url>] [--reconfigure] [--install-service]
#
# Run as your normal user (NOT root) -- it uses sudo only for the specific
# steps that need it (installing libportaudio2, audio group membership, and
# the systemd unit).
set -euo pipefail

REPO_URL="git@github.com:rocksynthetic/buckyball-chamber.git"
INSTALL_DIR="$HOME/buckyball-chamber"
RECONFIGURE=false
INSTALL_SERVICE=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir) INSTALL_DIR="$2"; shift 2 ;;
    --repo-url) REPO_URL="$2"; shift 2 ;;
    --reconfigure) RECONFIGURE=true; shift ;;
    --install-service) INSTALL_SERVICE=true; shift ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

if [[ "$EUID" -eq 0 ]]; then
  echo "Run this as your normal user, not root -- it uses sudo only where needed." >&2
  exit 1
fi

echo "== Repository =="
if [[ -d "$INSTALL_DIR/.git" ]]; then
  echo "updating existing clone at $INSTALL_DIR"
  git -C "$INSTALL_DIR" pull --ff-only
else
  echo "cloning $REPO_URL into $INSTALL_DIR"
  git clone "$REPO_URL" "$INSTALL_DIR"
fi
cd "$INSTALL_DIR"

echo "== System audio library =="
if command -v apt-get &>/dev/null; then
  sudo apt-get update -qq
  sudo apt-get install -y libportaudio2
else
  echo "WARNING: apt-get not found; install your distro's PortAudio package manually" \
       "(sounddevice's Linux wheel links against the system libportaudio2)." >&2
fi

echo "== Audio group membership =="
if id -nG "$USER" | grep -qw audio; then
  echo "$USER is already in the audio group"
else
  sudo usermod -aG audio "$USER"
  echo "added $USER to the audio group -- log out/in (or reboot) for this to take effect"
fi

echo "== Python environment =="
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

echo "== Configuration =="
if [[ -f config.yaml && "$RECONFIGURE" != true ]]; then
  echo "config.yaml already exists, skipping the wizard (pass --reconfigure to redo it)"
else
  .venv/bin/python -m client.setup_wizard --out config.yaml
fi

if [[ "$INSTALL_SERVICE" == true ]]; then
  echo "== systemd service =="
  UNIT_PATH="/etc/systemd/system/chamber-client.service"
  sudo tee "$UNIT_PATH" >/dev/null <<EOF
[Unit]
Description=Buckyball chamber playback/recording client
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/.venv/bin/python -m client.main --config $INSTALL_DIR/config.yaml
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
  sudo systemctl daemon-reload
  sudo systemctl enable --now chamber-client
  echo "installed and started chamber-client.service (as $USER)"
  echo "check status with: sudo systemctl status chamber-client"
  echo "check logs with:   sudo journalctl -u chamber-client -f"
else
  cat <<EOF

Setup done. To test it in the foreground before installing as a service:
  cd $INSTALL_DIR && .venv/bin/python -m client.main --config config.yaml

To install it as an auto-restarting systemd service, re-run with --install-service,
or see systemd/README.md for the manual steps.
EOF
fi
