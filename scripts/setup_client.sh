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

install_portaudio() {
  if command -v apt-get &>/dev/null; then
    sudo apt-get update -qq
    sudo apt-get install -y libportaudio2
  elif command -v dnf &>/dev/null; then
    sudo dnf install -y portaudio
  elif command -v yum &>/dev/null; then
    sudo yum install -y portaudio
  elif command -v pacman &>/dev/null; then
    sudo pacman -Sy --noconfirm portaudio
  elif command -v apk &>/dev/null; then
    sudo apk add --no-cache portaudio
  elif command -v zypper &>/dev/null; then
    sudo zypper install -y portaudio
  else
    return 1
  fi
}

echo "== System audio library =="
if install_portaudio; then
  echo "PortAudio installed"
else
  echo "WARNING: could not auto-install PortAudio (no supported package manager found," >&2
  echo "         or the install failed) -- install it manually for your distro" \
       "(sounddevice's Linux wheel links against the system libportaudio2)." >&2
fi

add_to_audio_group() {
  if id -nG "$USER" | grep -qw audio; then
    echo "$USER is already in the audio group"
    return 0
  fi
  if command -v usermod &>/dev/null; then
    sudo usermod -aG audio "$USER"
  elif command -v gpasswd &>/dev/null; then
    sudo gpasswd -a "$USER" audio
  elif command -v addgroup &>/dev/null; then
    sudo addgroup "$USER" audio
  else
    return 1
  fi
  echo "added $USER to the audio group -- log out/in (or reboot) for this to take effect"
}

echo "== Audio group membership =="
if ! add_to_audio_group; then
  echo "WARNING: could not add $USER to the audio group automatically" \
       "(no usermod/gpasswd/addgroup found) -- add it manually if the client" \
       "can't access the audio device." >&2
fi

echo "== PulseAudio check =="
if command -v dpkg &>/dev/null && dpkg -l pulseaudio 2>/dev/null | grep -q '^ii'; then
  if ! pactl info &>/dev/null; then
    echo "WARNING: PulseAudio is installed but not reachable (no server running)." >&2
    echo "         Debian/Raspberry Pi OS's libportaudio2 has a known bug where it" >&2
    echo "         fails to initialize *entirely* -- not just falls back to ALSA --" >&2
    echo "         if it can't connect to a PulseAudio server. This breaks both the" >&2
    echo "         setup wizard and the systemd service, since a headless/daemon" >&2
    echo "         process has no PulseAudio session to connect to." >&2
    echo "         If this machine doesn't need PulseAudio for anything else, remove" >&2
    echo "         it so PortAudio falls back to ALSA directly:" >&2
    echo "           sudo apt-get remove --purge -y pulseaudio pulseaudio-utils" >&2
  fi
fi

echo "== Python environment =="
if [[ -d .venv && ! -x .venv/bin/pip ]]; then
  echo ".venv exists but is missing pip (likely from an interrupted previous run) -- recreating it"
  rm -rf .venv
fi
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

if [[ "$INSTALL_SERVICE" == true ]] && ! command -v systemctl &>/dev/null; then
  echo "WARNING: --install-service was requested but systemctl isn't available on this" >&2
  echo "         machine (no systemd -- expected if you're testing on macOS/non-Linux)." >&2
  echo "         Skipping service install; falling back to foreground-run instructions." >&2
  INSTALL_SERVICE=false
fi

if [[ "$INSTALL_SERVICE" == true ]]; then
  echo "== systemd lingering =="
  # libportaudio2 on Debian/Raspberry Pi OS hard-depends on libpulse0, and
  # PortAudio fails to initialize entirely if it can't reach a PulseAudio
  # server. That server normally only runs during an interactive login
  # session, which a systemd daemon doesn't have. Lingering tells systemd to
  # start $USER's user session (and its PulseAudio socket) at boot instead.
  sudo loginctl enable-linger "$USER"
  echo "enabled lingering for $USER"

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
Environment=XDG_RUNTIME_DIR=/run/user/%U
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/.venv/bin/python -m client.main --config $INSTALL_DIR/config.yaml
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
  sudo systemctl daemon-reload
  sudo systemctl enable chamber-client
  # restart (not just enable --now) so re-running with --reconfigure picks up
  # config.yaml / unit file changes on an already-running service
  sudo systemctl restart chamber-client
  echo "installed and (re)started chamber-client.service (as $USER)"
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
