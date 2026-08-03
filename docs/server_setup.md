# SFTP server setup

An SFTP-only chroot user on a small VPS is all this needs -- no managed
SFTP service required at this volume.

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

Then reload sshd:

```
sudo systemctl reload sshd
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
