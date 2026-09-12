"""Prompt helpers shared by client/setup_wizard.py and uploader/setup_wizard.py."""
from __future__ import annotations

import subprocess
from pathlib import Path

from client.config import SftpConfig
from client.sftp_client import SftpClient, SftpConnectionError


def prompt(msg: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default is not None else ""
    while True:
        value = input(f"{msg}{suffix}: ").strip()
        if value:
            return value
        if default is not None:
            return default
        print("A value is required.")


def prompt_int(msg: str, default: int) -> int:
    while True:
        raw = prompt(msg, str(default))
        try:
            return int(raw)
        except ValueError:
            print("Please enter a whole number.")


def prompt_float(msg: str, default: float) -> float:
    while True:
        raw = prompt(msg, str(default))
        try:
            return float(raw)
        except ValueError:
            print("Please enter a number.")


def prompt_sftp_config(default_private_key_path: str = "~/.ssh/id_ed25519") -> SftpConfig:
    print("-- SFTP settings --")
    return SftpConfig(
        host=prompt("SFTP host", "sftp.buckyball.space"),
        port=prompt_int("SFTP port", 22),
        username=prompt("SFTP username", "chamber"),
        private_key_path=prompt("Path to SSH private key", default_private_key_path),
        remote_base_dir=prompt("Remote base directory", "/data"),
        connect_timeout_seconds=prompt_float("Connect timeout seconds", 15.0),
        backoff_base_seconds=prompt_float("Backoff base seconds", 5.0),
        backoff_cap_seconds=prompt_float("Backoff cap seconds", 300.0),
    )


def sftp_config_dict(sftp_config: SftpConfig) -> dict:
    return {
        "host": sftp_config.host,
        "port": sftp_config.port,
        "username": sftp_config.username,
        "private_key_path": sftp_config.private_key_path,
        "remote_base_dir": sftp_config.remote_base_dir,
        "connect_timeout_seconds": sftp_config.connect_timeout_seconds,
        "backoff_base_seconds": sftp_config.backoff_base_seconds,
        "backoff_cap_seconds": sftp_config.backoff_cap_seconds,
    }


def ensure_host_key_trusted(host: str, port: int) -> None:
    """Add host's SSH host key to ~/.ssh/known_hosts if it isn't there yet.

    paramiko's SSHClient.load_system_host_keys() (the reject-unknown-host-key
    default this project relies on -- see docs/server_setup.md step 4) reads
    that file, so without this the first connection always fails with
    "Server '<host>' not found in known_hosts".
    """
    known_hosts = Path.home() / ".ssh" / "known_hosts"
    known_hosts.parent.mkdir(parents=True, exist_ok=True)
    known_hosts.parent.chmod(0o700)
    known_hosts.touch(exist_ok=True)

    already_trusted = subprocess.run(
        ["ssh-keygen", "-F", host, "-f", str(known_hosts)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if already_trusted.returncode == 0:
        return

    print(f"Adding {host}'s SSH host key to {known_hosts} ...")
    scan = subprocess.run(
        ["ssh-keyscan", "-t", "ed25519", "-p", str(port), host],
        capture_output=True,
        text=True,
    )
    if not scan.stdout.strip():
        print(
            f"  Warning: couldn't fetch a host key for {host}:{port} "
            f"(unreachable, or wrong host/port?) -- SFTP connections will fail "
            f"until this is resolved."
        )
        return
    with known_hosts.open("a") as f:
        f.write(scan.stdout)
    print(f"  Trusted {host}'s host key.")


def test_sftp_connection(sftp_config: SftpConfig) -> bool:
    print("Testing SFTP connection...")
    client = SftpClient(sftp_config)
    try:
        client.connect()
        print("SFTP connection succeeded.")
        return True
    except SftpConnectionError as exc:
        print(f"SFTP connection failed: {exc}")
        return False
    finally:
        client.close()
