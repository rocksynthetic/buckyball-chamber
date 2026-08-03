"""Prompt helpers shared by client/setup_wizard.py and uploader/setup_wizard.py."""
from __future__ import annotations

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


def prompt_sftp_config() -> SftpConfig:
    print("-- SFTP settings --")
    return SftpConfig(
        host=prompt("SFTP host"),
        port=prompt_int("SFTP port", 22),
        username=prompt("SFTP username", "chamber"),
        private_key_path=prompt("Path to SSH private key", "~/.ssh/id_ed25519"),
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
