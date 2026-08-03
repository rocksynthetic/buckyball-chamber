from __future__ import annotations

import logging
import posixpath
from pathlib import Path

import paramiko

from client.config import SftpConfig

logger = logging.getLogger(__name__)


class SftpConnectionError(RuntimeError):
    """Raised when a connection attempt to the SFTP server fails."""


class SftpClient:
    """Thin paramiko wrapper providing atomic put/get and job-queue helpers.

    Connection retry/backoff is deliberately left to the caller (the state
    machine's main loop) so that a failed connection never blocks local-only
    work like playback/recording.
    """

    def __init__(self, config: SftpConfig):
        self._config = config
        self._ssh: paramiko.SSHClient | None = None
        self._sftp: paramiko.SFTPClient | None = None

    @property
    def is_connected(self) -> bool:
        return self._sftp is not None

    def connect(self) -> None:
        """Attempt a single connection. Raises SftpConnectionError on failure."""
        try:
            ssh = paramiko.SSHClient()
            # Reject-by-default (paramiko's built-in RejectPolicy): the host key
            # must already be trusted via the system/user known_hosts file. See
            # docs/server_setup.md for the one-time `ssh-keyscan` step -- we
            # deliberately do not auto-trust unknown host keys, since that would
            # accept a man-in-the-middle silently.
            ssh.load_system_host_keys()
            ssh.connect(
                hostname=self._config.host,
                port=self._config.port,
                username=self._config.username,
                key_filename=str(Path(self._config.private_key_path).expanduser()),
                timeout=self._config.connect_timeout_seconds,
                allow_agent=False,
                look_for_keys=False,
            )
            sftp = ssh.open_sftp()
            self._ssh = ssh
            self._sftp = sftp
        except Exception as exc:  # noqa: BLE001 - normalize all connect failures
            self.close()
            raise SftpConnectionError(str(exc)) from exc

    def close(self) -> None:
        if self._sftp is not None:
            try:
                self._sftp.close()
            except Exception:  # noqa: BLE001
                pass
            self._sftp = None
        if self._ssh is not None:
            try:
                self._ssh.close()
            except Exception:  # noqa: BLE001
                pass
            self._ssh = None

    def __enter__(self) -> "SftpClient":
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def _sftp_or_raise(self) -> paramiko.SFTPClient:
        if self._sftp is None:
            raise SftpConnectionError("not connected")
        return self._sftp

    def remote_path(self, *parts: str) -> str:
        return posixpath.join(self._config.remote_base_dir, *parts)

    def ensure_dir(self, remote_dir: str) -> None:
        sftp = self._sftp_or_raise()
        parts = remote_dir.strip("/").split("/")
        current = ""
        for part in parts:
            current = f"{current}/{part}"
            try:
                sftp.stat(current)
            except FileNotFoundError:
                sftp.mkdir(current)

    def listdir(self, remote_dir: str) -> list[str]:
        sftp = self._sftp_or_raise()
        return sftp.listdir(remote_dir)

    def stat_size(self, remote_path: str) -> int:
        sftp = self._sftp_or_raise()
        attrs = sftp.stat(remote_path)
        if attrs.st_size is None:
            raise SftpConnectionError(f"no size reported for {remote_path}")
        return attrs.st_size

    def rename(self, old_remote_path: str, new_remote_path: str) -> None:
        sftp = self._sftp_or_raise()
        sftp.posix_rename(old_remote_path, new_remote_path)

    def upload_atomic(self, local_path: Path, remote_dir: str, remote_name: str) -> str:
        """Upload local_path into remote_dir as remote_name, atomically.

        Uploads to a `.part` temp name first, then performs a server-side
        rename, so a partially-uploaded file is never visible under its
        final name to anything else polling remote_dir.
        """
        sftp = self._sftp_or_raise()
        final_path = posixpath.join(remote_dir, remote_name)
        temp_path = f"{final_path}.part"
        sftp.put(str(local_path), temp_path)
        sftp.posix_rename(temp_path, final_path)
        return final_path

    def download_atomic(self, remote_path: str, local_path: Path) -> None:
        """Download remote_path to local_path, verifying size before finalizing.

        Downloads to a local `.tmp` sibling, checks its size against the
        remote file's reported size, and only then moves it into place -- so
        a truncated download from a dropped connection is never mistaken for
        a complete local copy.
        """
        sftp = self._sftp_or_raise()
        expected_size = self.stat_size(remote_path)
        temp_local = local_path.with_suffix(local_path.suffix + ".tmp")
        local_path.parent.mkdir(parents=True, exist_ok=True)
        sftp.get(remote_path, str(temp_local))
        actual_size = temp_local.stat().st_size
        if actual_size != expected_size:
            temp_local.unlink(missing_ok=True)
            raise SftpConnectionError(
                f"incomplete download for {remote_path}: "
                f"expected {expected_size} bytes, got {actual_size}"
            )
        temp_local.replace(local_path)
