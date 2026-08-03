#!/usr/bin/env bash
# Sets up the "chamber" SFTP-only chroot user, directory tree, and sshd
# config on the SFTP server. Idempotent -- safe to re-run (e.g. after an
# scp/manual edit clobbers ownership on authorized_keys again).
#
# Run as root ON THE SFTP SERVER:
#   sudo ./setup_server.sh <pubkey-file-or-string> [<pubkey-file-or-string> ...]
#
# Each argument is either a path to a .pub file or a raw
# "ssh-ed25519 AAAA... comment" string. Add one key per machine that needs
# access (the on-site chamber client, and any machine running upload_job.py).
set -euo pipefail

CHAMBER_USER="chamber"
CHAMBER_HOME="/srv/sftp/chamber"
SSH_DIR="$CHAMBER_HOME/.ssh"
AUTHORIZED_KEYS="$SSH_DIR/authorized_keys"
DATA_DIR="$CHAMBER_HOME/data"
SSHD_CONFIG="/etc/ssh/sshd_config"
SSHD_DROPIN_DIR="/etc/ssh/sshd_config.d"
SSHD_DROPIN="$SSHD_DROPIN_DIR/chamber-sftp.conf"

if [[ "$EUID" -ne 0 ]]; then
  echo "Must be run as root, e.g.: sudo $0 <pubkey-file-or-string> ..." >&2
  exit 1
fi

if [[ $# -eq 0 ]]; then
  echo "Usage: $0 <pubkey-file-or-string> [<pubkey-file-or-string> ...]" >&2
  exit 1
fi

echo "== User =="
if ! id "$CHAMBER_USER" &>/dev/null; then
  useradd --system --home "$CHAMBER_HOME" --shell /usr/sbin/nologin "$CHAMBER_USER"
  echo "created user $CHAMBER_USER"
else
  echo "user $CHAMBER_USER already exists"
fi

echo "== Chroot directory tree =="
# sshd's ChrootDirectory validation requires every component of this path
# to be root-owned and not writable by group/other -- not just the leaf dir.
mkdir -p "$CHAMBER_HOME"
chown root:root "$CHAMBER_HOME"
chmod 755 "$CHAMBER_HOME"

mkdir -p "$DATA_DIR"/{incoming,processing,recordings,done,failed}
chown -R "$CHAMBER_USER:$CHAMBER_USER" "$DATA_DIR"
echo "data tree ready under $DATA_DIR (exposed to sftp clients as /data)"

echo "== .ssh / authorized_keys =="
mkdir -p "$SSH_DIR"
touch "$AUTHORIZED_KEYS"
# Must be owned by the chamber user, NOT root: sshd's privilege-separated
# child re-opens authorized_keys as the authenticating user (a hardening
# measure against symlink/trust attacks), so a root-owned file is Permission
# Denied to it even though root nominally "owns" it.
chown -R "$CHAMBER_USER:$CHAMBER_USER" "$SSH_DIR"
chmod 700 "$SSH_DIR"
chmod 600 "$AUTHORIZED_KEYS"

echo "== Public keys =="
for key_arg in "$@"; do
  if [[ -f "$key_arg" ]]; then
    key_content="$(cat "$key_arg")"
  else
    key_content="$key_arg"
  fi
  if grep -qF "$key_content" "$AUTHORIZED_KEYS" 2>/dev/null; then
    echo "  already present: ${key_content:0:44}..."
  else
    echo "$key_content" >> "$AUTHORIZED_KEYS"
    echo "  added: ${key_content:0:44}..."
  fi
done
chown "$CHAMBER_USER:$CHAMBER_USER" "$AUTHORIZED_KEYS"
chmod 600 "$AUTHORIZED_KEYS"

echo "== sshd config =="
match_block=$(cat <<EOF
Match User $CHAMBER_USER
    ChrootDirectory $CHAMBER_HOME
    ForceCommand internal-sftp
    AllowTcpForwarding no
    X11Forwarding no
    PasswordAuthentication no
EOF
)

if grep -qE '^\s*Include\s+/etc/ssh/sshd_config\.d/\*\.conf' "$SSHD_CONFIG"; then
  mkdir -p "$SSHD_DROPIN_DIR"
  printf '%s\n' "$match_block" > "$SSHD_DROPIN"
  echo "wrote $SSHD_DROPIN"
else
  # No drop-in include in the main config -- fall back to appending directly,
  # but only once.
  if grep -q "Match User $CHAMBER_USER" "$SSHD_CONFIG"; then
    echo "Match block for $CHAMBER_USER already present in $SSHD_CONFIG, leaving as-is"
  else
    printf '\n%s\n' "$match_block" >> "$SSHD_CONFIG"
    echo "appended Match block to $SSHD_CONFIG"
  fi
fi

echo "== Reloading sshd =="
if systemctl list-unit-files 2>/dev/null | grep -q '^ssh\.service'; then
  systemctl reload ssh
  echo "reloaded ssh.service"
elif systemctl list-unit-files 2>/dev/null | grep -q '^sshd\.service'; then
  systemctl reload sshd
  echo "reloaded sshd.service"
else
  echo "WARNING: could not find an ssh/sshd systemd unit; reload it manually." >&2
fi

cat <<EOF

Done.
  - config.yaml's sftp.remote_base_dir should be: /data
  - config.yaml's sftp.username should be: $CHAMBER_USER
  - Test from a client machine with:
      ssh-keyscan -t ed25519 <this-host> >> ~/.ssh/known_hosts   # once, to trust the host key
      sftp -i <matching-private-key> $CHAMBER_USER@<this-host>
EOF
