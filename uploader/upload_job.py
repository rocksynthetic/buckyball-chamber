"""Small CLI to upload a playback job to the chamber's SFTP queue, and to
fetch the resulting recording back.

Upload writes the file to incoming/<job_id>__<name>.wav.part, then issues a
separate atomic rename to drop the .part suffix -- the chamber client only
ever treats extensionless files in incoming/ as fully arrived, so this
two-step upload is what keeps a half-uploaded file invisible to it.

Download looks for recordings/<job_id>__<stem>__recording.wav, waiting and
polling for it if it isn't there yet (and bailing out if the job shows up in
failed/ instead). If no job is specified it defaults to the last job this
tool uploaded (tracked in a small local state file next to the config).

Usage:
    python -m uploader.upload_job upload my_track.wav --config config.yaml
    python -m uploader.upload_job download --config config.yaml
    python -m uploader.upload_job download --job-id 20260803T101500Z-ab12 --name my_track.wav
"""
from __future__ import annotations

import argparse
import json
import secrets
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

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


def recording_remote_name(job_id: str, original_name: str) -> str:
    return f"{job_id}__{Path(original_name).stem}__recording.wav"


def source_remote_name(job_id: str, original_name: str) -> str:
    return f"{job_id}__{original_name}"


def _state_file_path(config_path: str) -> Path:
    return Path(config_path).expanduser().parent / ".uploader_last_job.json"


def _save_last_job(config_path: str, job_id: str, original_name: str) -> None:
    state = {"job_id": job_id, "original_name": original_name}
    _state_file_path(config_path).write_text(json.dumps(state))


def _load_last_job(config_path: str) -> tuple[str, str]:
    path = _state_file_path(config_path)
    if not path.exists():
        raise FileNotFoundError(
            f"no record of a previous upload at {path}; pass --job-id and --name explicitly"
        )
    state = json.loads(path.read_text())
    return state["job_id"], state["original_name"]


def upload(file_path: str, config_path: str) -> tuple[str, str]:
    local_path = Path(file_path).expanduser()
    if not local_path.exists():
        raise FileNotFoundError(local_path)
    if local_path.suffix.lower() != ".wav":
        raise ValueError(f"only .wav files are supported in this MVP, got {local_path.suffix!r}")

    job_id = generate_job_id()
    remote_name = source_remote_name(job_id, local_path.name)

    sftp_config = load_sftp_config(config_path)
    client = SftpClient(sftp_config)
    client.connect()
    try:
        client.ensure_dir(client.remote_path("incoming"))
        final_path = client.upload_atomic(local_path, client.remote_path("incoming"), remote_name)
    finally:
        client.close()

    _save_last_job(config_path, job_id, local_path.name)
    return job_id, final_path


def _connect(sftp_config: SftpConfig) -> SftpClient:
    client = SftpClient(sftp_config)
    client.connect()
    return client


def wait_for_recording(
    make_client: Callable[[], SftpClient],
    job_id: str,
    original_name: str,
    out_path: str | None,
    poll_interval: float,
    timeout: float | None = None,
) -> Path:
    """Poll for the recording, downloading it as soon as it appears.

    Also watches failed/ so a chamber-side failure doesn't wait forever.
    Each iteration gets a fresh client (via make_client) so a dropped
    connection is retried rather than fatal.
    """
    recording_name = recording_remote_name(job_id, original_name)
    failed_name = source_remote_name(job_id, original_name)
    destination = Path(out_path) if out_path else Path.cwd() / recording_name

    deadline = time.monotonic() + timeout if timeout else None
    announced = False
    while True:
        if deadline is not None and time.monotonic() > deadline:
            raise TimeoutError(f"timed out waiting for job {job_id}'s recording")

        try:
            client = make_client()
        except SftpConnectionError as exc:
            print(f"  connection issue, will retry: {exc}")
            time.sleep(poll_interval)
            continue

        try:
            if client.exists(client.remote_path("recordings", recording_name)):
                client.download_atomic(client.remote_path("recordings", recording_name), destination)
                return destination
            if client.exists(client.remote_path("failed", failed_name)):
                raise RuntimeError(
                    f"job {job_id} failed in the chamber (see failed/{failed_name} on the server)"
                )
            if not announced:
                print(f"Recording not ready yet; waiting (polling every {poll_interval:.0f}s, Ctrl+C to stop)...")
                announced = True
        finally:
            client.close()

        time.sleep(poll_interval)


def download_recording(
    config_path: str,
    job_id: str,
    original_name: str,
    out_path: str | None,
    poll_interval: float,
    timeout: float | None = None,
) -> Path:
    sftp_config = load_sftp_config(config_path)
    return wait_for_recording(
        lambda: _connect(sftp_config), job_id, original_name, out_path, poll_interval, timeout
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Upload/download chamber playback jobs over SFTP")
    subparsers = parser.add_subparsers(dest="mode", required=True)

    upload_parser = subparsers.add_parser("upload", help="upload a .wav file to be played in the chamber")
    upload_parser.add_argument("file", help="path to a .wav file to play in the chamber")
    upload_parser.add_argument("--config", default="config.yaml", help="path to a config.yaml with an sftp: section")

    download_parser = subparsers.add_parser(
        "download", help="download a completed recording, waiting for it if it isn't ready yet"
    )
    download_parser.add_argument("--config", default="config.yaml", help="path to a config.yaml with an sftp: section")
    download_parser.add_argument("--job-id", help="job id to download (defaults to the last upload)")
    download_parser.add_argument("--name", help="original filename for that job (required alongside --job-id unless it matches the last upload)")
    download_parser.add_argument("--out", help="where to save the recording (default: ./<job_id>__<name>__recording.wav)")
    download_parser.add_argument("--poll-interval", type=float, default=10.0, help="seconds between checks while waiting")
    download_parser.add_argument("--timeout", type=float, default=None, help="give up after this many seconds (default: wait indefinitely)")

    args = parser.parse_args(argv)

    if args.mode == "upload":
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
        print("Run `python -m uploader.upload_job download` once it's ready to fetch the recording.")
        return 0

    # download mode
    if args.job_id:
        job_id = args.job_id
        original_name = args.name
        if original_name is None:
            try:
                last_job_id, last_name = _load_last_job(args.config)
            except FileNotFoundError:
                print(
                    "Error: --job-id given without --name, and no local record to look up the original filename.",
                    file=sys.stderr,
                )
                return 1
            if last_job_id != job_id:
                print("Error: --job-id doesn't match the last recorded upload; pass --name explicitly.", file=sys.stderr)
                return 1
            original_name = last_name
    else:
        try:
            job_id, original_name = _load_last_job(args.config)
        except FileNotFoundError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

    try:
        destination = download_recording(
            args.config, job_id, original_name, args.out, args.poll_interval, args.timeout
        )
    except (SftpConnectionError, RuntimeError, TimeoutError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nStopped waiting.", file=sys.stderr)
        return 1

    print(f"Downloaded to {destination}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
