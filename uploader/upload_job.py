"""Small CLI to upload a playback job to the chamber's SFTP queue.

Writes the file to incoming/<job_id>__<name>.wav.part, then issues a
separate atomic rename to drop the .part suffix -- the chamber client only
ever treats extensionless files in incoming/ as fully arrived, so this
two-step upload is what keeps a half-uploaded file invisible to it.

Usage:
    python -m uploader.upload_job my_track.wav --config config.yaml
"""
from __future__ import annotations

import argparse
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from client.config import SftpConfig
from client.sftp_client import SftpClient, SftpConnectionError


def generate_job_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{secrets.token_hex(2)}"


def load_sftp_config(path: str) -> SftpConfig:
    with open(path, "r") as f:
        raw = yaml.safe_load(f)
    return SftpConfig(**raw["sftp"])


def upload(file_path: str, config_path: str) -> tuple[str, str]:
    local_path = Path(file_path).expanduser()
    if not local_path.exists():
        raise FileNotFoundError(local_path)
    if local_path.suffix.lower() != ".wav":
        raise ValueError(f"only .wav files are supported in this MVP, got {local_path.suffix!r}")

    job_id = generate_job_id()
    remote_name = f"{job_id}__{local_path.name}"

    sftp_config = load_sftp_config(config_path)
    client = SftpClient(sftp_config)
    client.connect()
    try:
        client.ensure_dir(client.remote_path("incoming"))
        final_path = client.upload_atomic(local_path, client.remote_path("incoming"), remote_name)
    finally:
        client.close()

    return job_id, final_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Upload a playback job to the chamber's SFTP queue")
    parser.add_argument("file", help="path to a .wav file to play in the chamber")
    parser.add_argument("--config", default="config.yaml", help="path to a config.yaml with an sftp: section")
    args = parser.parse_args(argv)

    try:
        job_id, final_path = upload(args.file, args.config)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except SftpConnectionError as exc:
        print(f"Could not connect to the SFTP server: {exc}", file=sys.stderr)
        return 1

    print(f"Uploaded as {final_path}")
    print(f"Job ID: {job_id}")
    print("Recording will appear (once processed) under the same job ID in recordings/.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
