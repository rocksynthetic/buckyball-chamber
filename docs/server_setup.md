# SFTP server setup

An SFTP-only chroot user on a small VPS is all this needs -- no managed
SFTP service required at this volume.

## Quick setup (recommended)

`scripts/setup_server.sh` does steps 1-3 below for you, idempotently (safe
to re-run -- e.g. if a later `scp` of `authorized_keys` clobbers ownership
again, re-running it puts things back). Run it as root on the SFTP server:

```
sudo ./scripts/setup_server.sh <pubkey-file-or-string> [<pubkey-file-or-string> ...]
```

Pass one public key per machine that needs access (the on-site chamber
client, and any machine running `uploader/upload_job.py`) -- either a path
to a `.pub` file or the raw key string. Then skip to step 4 below.

The rest of this doc describes what the script does manually, for reference
or if you'd rather not run a script as root.

## 0. On each client machine, generate a keypair

**Uploader machines**: `scripts/setup_uploader.sh` does this automatically
-- its wizard generates a dedicated keypair under the install directory
(`~/buckyball-chamber/keys/id_ed25519`, not `~/.ssh`), prints the public
half, and copies it to the clipboard (macOS) so you can paste it straight
into an email/Slack message to whoever runs `setup_server.sh`. Nothing
below is needed for an uploader machine set up this way -- skip to step 2.

**Chamber machine**, or anything set up manually instead of via the script
above: do this yourself, on that machine -- *not* on the server. Each
machine gets its own keypair; nothing needs to be synced between them
except the public halves, which get appended to the server's
`authorized_keys` (step 2 below):

```
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N ""
```

- `-N ""` gives the key no passphrase. This isn't optional for the chamber
  client: it runs unattended as a systemd daemon with nothing able to type a
  passphrase in, so a passphrase-protected key just hangs paramiko's connect
  call forever.
- The default path (`~/.ssh/id_ed25519`) matches `sftp.private_key_path`'s
  default in `config.yaml` / the setup wizard's prompt, so you normally
  don't need to change that setting afterward.
- If you're using the dedicated `chamber` system-user layout described in
  `systemd/README.md` (instead of `scripts/setup_client.sh`'s normal-user
  layout), generate the key *as* that user so it ends up under its home
  directory (`/opt/buckyball-chamber/.ssh/`), where the daemon actually
  looks for it at runtime (`private_key_path`'s `~` expands using the
  service's own `$HOME`, i.e. the account in the unit's `User=`):
  ```
  sudo -u chamber ssh-keygen -t ed25519 -f /opt/buckyball-chamber/.ssh/id_ed25519 -N ""
  ```
- Print the public key to hand to whoever runs `setup_server.sh` (or to
  paste into `authorized_keys` yourself in step 2):
  ```
  cat ~/.ssh/id_ed25519.pub
  ```

This is a separate keypair from any GitHub deploy key used to `git clone`
this (private) repo onto the chamber machine -- that one only grants read
access to the repo and has nothing to do with authenticating to the SFTP
server.

## 1. Create the user and directory tree

```
sudo useradd --system --home /srv/sftp/chamber --shell /usr/sbin/nologin chamber

# The chroot dir (and every parent of it) must be owned by root and not
# writable by anyone else, or sshd will refuse to start the session.
sudo mkdir -p /srv/sftp/chamber
sudo chown root:root /srv/sftp/chamber
sudo chmod 755 /srv/sftp/chamber

# The actual job queue lives in a writable subdirectory inside the chroot.
sudo mkdir -p /srv/sftp/chamber/data/{incoming,processing,recordings,done,failed}
sudo chown -R chamber:chamber /srv/sftp/chamber/data
```

`remote_base_dir` in `config.yaml` should be `/data` (i.e. relative to the
chroot root, not `/srv/sftp/chamber/data`).

## 2. Key-based auth only

Take the public keys generated on each client machine in step 0 and add
both to this server-side `chamber` user's `authorized_keys` (note: this is
the SFTP login user on the *server* -- an unrelated namesake of the
optional dedicated `chamber` system user on the on-site Raspberry Pi
described in `systemd/README.md`):

```
sudo mkdir -p /srv/sftp/chamber/.ssh
sudo touch /srv/sftp/chamber/.ssh/authorized_keys
# paste both public keys, one per line, into authorized_keys
sudo chown -R chamber:chamber /srv/sftp/chamber/.ssh
sudo chmod 700 /srv/sftp/chamber/.ssh
sudo chmod 600 /srv/sftp/chamber/.ssh/authorized_keys
```

One shared SFTP user for both the client and the uploader is fine for this
MVP -- don't build out per-actor accounts/permissions.

**Gotcha:** `.ssh` and `authorized_keys` must be owned by `chamber`, not
`root` -- even though the chroot dir itself must be root-owned (see above).
OpenSSH's privilege-separated child re-opens `authorized_keys` *as the
authenticating user* (a hardening measure against symlink/trust attacks),
so a root-owned file is `Permission denied` to it regardless of mode bits.
This is easy to reintroduce accidentally -- e.g. `scp`-ing a new
`authorized_keys` file to the server as `root` recreates it as root-owned
and silently breaks logins again. If auth starts failing after editing keys
this way, re-run the chown above (or `scripts/setup_server.sh`).

## 3. sshd_config

Add to `/etc/ssh/sshd_config` (or a drop-in under `/etc/ssh/sshd_config.d/`):

```
Match User chamber
    ChrootDirectory /srv/sftp/chamber
    ForceCommand internal-sftp
    AllowTcpForwarding no
    X11Forwarding no
    PasswordAuthentication no
```

Then reload the SSH daemon. The systemd unit is named `ssh` on Debian/Ubuntu
and `sshd` on RHEL/CentOS/Fedora -- check which one you have first:

```
systemctl list-unit-files | grep -i ssh
sudo systemctl reload ssh    # Debian/Ubuntu
sudo systemctl reload sshd   # RHEL/CentOS/Fedora
```

## 4. Trust the host key before first connect

The client and uploader use `paramiko`'s default reject-unknown-host-key
behavior (no auto-trust, to avoid silently accepting a man-in-the-middle).
Before running the client or uploader for the first time, the server's host
key must be added to the machine's `~/.ssh/known_hosts` once.

**Both `client/setup_wizard.py` and `uploader/setup_wizard.py` do this
automatically now** (right after you enter the SFTP host/port, before the
connection test), so nothing manual is needed if you set things up via
`scripts/setup_client.sh` / `scripts/setup_uploader.sh`. It's idempotent --
safe to run again, and skips silently if the host is already trusted.

If you're setting things up another way (skipped the wizard, or a fresh
`client.main`/`upload_job` run on a machine that never ran it), do this
manually instead:

```
ssh-keyscan -t ed25519 <sftp-host> >> ~/.ssh/known_hosts
```

Do this on both the on-site chamber machine and any machine that will run
`uploader/upload_job.py`. As with step 0, if the chamber client runs as a
dedicated `chamber` system user, run this as that user (`sudo -u chamber
ssh-keyscan ...`) so `known_hosts` lands in its home directory, since
`paramiko.SSHClient.load_system_host_keys()` reads the *running process's*
`~/.ssh/known_hosts` -- not whichever user happened to run this command.

## 5. No retention/cleanup policy in MVP

Files accumulate in `recordings/`, `done/`, and `failed/` indefinitely --
there's no automated deletion. Clean up manually as needed.
