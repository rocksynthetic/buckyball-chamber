#!/usr/bin/env bash
# One-shot setup for the uploader CLI (e.g. on your Mac): clones/updates the
# repo, installs the lightweight uploader-only Python dependencies (no
# sounddevice/soundfile -- nothing here plays or records audio), and runs
# the SFTP-only config wizard. Idempotent -- safe to re-run.
#
# Usage:
#   ./setup_uploader.sh [--dir <path>] [--repo-url <url>] [--reconfigure]
set -euo pipefail

REPO_URL="git@github.com:rocksynthetic/buckyball-chamber.git"
INSTALL_DIR="$HOME/buckyball-chamber"
RECONFIGURE=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir) INSTALL_DIR="$2"; shift 2 ;;
    --repo-url) REPO_URL="$2"; shift 2 ;;
    --reconfigure) RECONFIGURE=true; shift ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

echo "== Repository =="
if [[ -d "$INSTALL_DIR/.git" ]]; then
  echo "updating existing clone at $INSTALL_DIR"
  git -C "$INSTALL_DIR" pull --ff-only
else
  echo "cloning $REPO_URL into $INSTALL_DIR"
  git clone "$REPO_URL" "$INSTALL_DIR"
fi
cd "$INSTALL_DIR"

echo "== Python environment =="
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements-uploader.txt

echo "== Configuration =="
if [[ -f config.yaml && "$RECONFIGURE" != true ]]; then
  echo "config.yaml already exists, skipping the wizard (pass --reconfigure to redo it)"
else
  .venv/bin/python -m uploader.setup_wizard --out config.yaml
fi

cat <<EOF

Setup done. From $INSTALL_DIR:
  .venv/bin/python -m uploader.upload_job upload my_track.wav --config config.yaml
  .venv/bin/python -m uploader.upload_job download --config config.yaml
EOF
