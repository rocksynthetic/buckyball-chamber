from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import FakeSftp
from uploader.upload_job import (
    _load_last_job,
    _save_last_job,
    recording_remote_name,
    source_remote_name,
    wait_for_recording,
)


def test_save_and_load_last_job_roundtrip(tmp_path: Path):
    config_path = str(tmp_path / "config.yaml")
    _save_last_job(config_path, "job-1", "track.wav")
    assert _load_last_job(config_path) == ("job-1", "track.wav")


def test_load_last_job_missing_raises(tmp_path: Path):
    config_path = str(tmp_path / "config.yaml")
    with pytest.raises(FileNotFoundError):
        _load_last_job(config_path)


def test_wait_for_recording_downloads_immediately_if_already_present(fake_sftp: FakeSftp, tmp_path: Path):
    recordings_dir = Path(fake_sftp.remote_path("recordings"))
    recordings_dir.mkdir(parents=True)
    name = recording_remote_name("job-1", "track.wav")
    (recordings_dir / name).write_bytes(b"recorded-bytes")

    out_path = tmp_path / "out.wav"
    destination = wait_for_recording(
        lambda: fake_sftp, "job-1", "track.wav", str(out_path), poll_interval=0.01
    )
    assert destination == out_path
    assert out_path.read_bytes() == b"recorded-bytes"


def test_wait_for_recording_polls_until_available(fake_sftp: FakeSftp, tmp_path: Path):
    recordings_dir = Path(fake_sftp.remote_path("recordings"))
    recordings_dir.mkdir(parents=True)
    name = recording_remote_name("job-1", "track.wav")

    attempts = {"count": 0}
    real_exists = fake_sftp.exists

    def flaky_exists(remote_path):
        attempts["count"] += 1
        if attempts["count"] < 3:
            return False
        return real_exists(remote_path)

    fake_sftp.exists = flaky_exists
    (recordings_dir / name).write_bytes(b"recorded-bytes")

    out_path = tmp_path / "out.wav"
    destination = wait_for_recording(
        lambda: fake_sftp, "job-1", "track.wav", str(out_path), poll_interval=0.01
    )
    assert destination == out_path
    assert attempts["count"] >= 3


def test_wait_for_recording_raises_if_job_failed(fake_sftp: FakeSftp, tmp_path: Path):
    failed_dir = Path(fake_sftp.remote_path("failed"))
    failed_dir.mkdir(parents=True)
    (failed_dir / source_remote_name("job-1", "track.wav")).write_bytes(b"x")

    with pytest.raises(RuntimeError, match="failed in the chamber"):
        wait_for_recording(lambda: fake_sftp, "job-1", "track.wav", None, poll_interval=0.01)


def test_wait_for_recording_times_out(fake_sftp: FakeSftp):
    with pytest.raises(TimeoutError):
        wait_for_recording(
            lambda: fake_sftp, "job-1", "track.wav", None, poll_interval=0.01, timeout=0.05
        )
