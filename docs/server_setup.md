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

Generate a keypair for the chamber client (if it doesn't have one yet) and
one for whoever uploads jobs, then add both public keys:

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
Before running the client or uploader for the first time, add the server's
host key to the machine's known_hosts once:

```
ssh-keyscan -t ed25519 <sftp-host> >> ~/.ssh/known_hosts
```

Do this on both the on-site chamber machine and any machine that will run
`uploader/upload_job.py`.

## 5. No retention/cleanup policy in MVP

Files accumulate in `recordings/`, `done/`, and `failed/` indefinitely --
there's no automated deletion. Clean up manually as needed.
