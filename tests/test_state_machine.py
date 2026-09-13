from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from client.config import Config
from client.job_store import JobState
from client.state_machine import ChamberClient
from tests.conftest import FakeSftp


def _drop_incoming_job(fake_sftp: FakeSftp, job_id: str, name: str, content: bytes = b"fake-wav-bytes") -> None:
    incoming = Path(fake_sftp.remote_path("incoming"))
    incoming.mkdir(parents=True, exist_ok=True)
    (incoming / f"{job_id}__{name}").write_bytes(content)


def _fake_record(monkeypatch, recorded_calls: list[str] | None = None):
    """Patch out real audio I/O: get_duration_seconds returns a constant, and
    play_and_record just writes a dummy recording file."""

    def fake_duration(path):
        return 1.0

    def fake_play_and_record(playback_path, recording_path, audio_config):
        if recorded_calls is not None:
            recorded_calls.append(str(playback_path))
        Path(recording_path).parent.mkdir(parents=True, exist_ok=True)
        Path(recording_path).write_bytes(b"fake-recording-bytes")

    monkeypatch.setattr("client.state_machine.get_duration_seconds", fake_duration)
    monkeypatch.setattr("client.state_machine.play_and_record", fake_play_and_record)


def test_full_lifecycle_success(config: Config, fake_sftp: FakeSftp, monkeypatch):
    _fake_record(monkeypatch)
    client = ChamberClient(config)
    client._ensure_remote_dirs(fake_sftp)
    _drop_incoming_job(fake_sftp, "job-1", "track.wav")

    client._discover_and_claim(fake_sftp)
    assert client.store.get("job-1").state == JobState.CLAIMED
    assert not Path(fake_sftp.remote_path("incoming", "job-1__track.wav")).exists()
    assert Path(fake_sftp.remote_path("processing", "job-1__track.wav")).exists()

    client._download_pending(fake_sftp)
    assert client.store.get("job-1").state == JobState.DOWNLOADED
    assert client._local_input_path("job-1").exists()

    client._record_pending()
    assert client.store.get("job-1").state == JobState.RECORDED
    assert client._local_recording_path("job-1").exists()

    client._upload_pending(fake_sftp)
    assert client.store.get("job-1").state == JobState.DONE
    assert Path(fake_sftp.remote_path("recordings", "job-1__track__recording.wav")).exists()
    assert Path(fake_sftp.remote_path("done", "job-1__track.wav")).exists()
    # Local copies are deleted once the job is confirmed done.
    assert not client._local_input_path("job-1").exists()
    assert not client._local_recording_path("job-1").exists()


def test_reconcile_recovers_orphaned_processing_job(config: Config, fake_sftp: FakeSftp):
    """Simulates a crash between the claim-rename and the DB write (or total
    loss of the local DB): the file is already in processing/ with no local
    record of it at all."""
    client = ChamberClient(config)
    client._ensure_remote_dirs(fake_sftp)
    processing = Path(fake_sftp.remote_path("processing"))
    processing.mkdir(parents=True, exist_ok=True)
    (processing / "job-1__track.wav").write_bytes(b"fake-wav-bytes")

    assert client.store.get("job-1") is None
    client._reconcile(fake_sftp)
    assert client.store.get("job-1").state == JobState.CLAIMED


def test_crash_after_recording_before_state_update_does_not_rerecord(
    config: Config, fake_sftp: FakeSftp, monkeypatch
):
    """If the recording file was written but the DB update didn't persist
    before a crash, resuming must not call play_and_record again."""
    recorded_calls: list[str] = []
    _fake_record(monkeypatch, recorded_calls)
    client = ChamberClient(config)
    client.store.upsert_discovered("job-1", "track.wav")
    client.store.set_state("job-1", JobState.DOWNLOADED)
    client._local_input_path("job-1").parent.mkdir(parents=True, exist_ok=True)
    client._local_input_path("job-1").write_bytes(b"fake-wav-bytes")

    client._record_pending()
    assert len(recorded_calls) == 1
    assert client.store.get("job-1").state == JobState.RECORDED

    # Simulate the crash: state update didn't persist, but the recording
    # file is already there from before.
    client.store.set_state("job-1", JobState.DOWNLOADED)
    client._record_pending()
    assert len(recorded_calls) == 1  # not called again
    assert client.store.get("job-1").state == JobState.RECORDED


def test_recording_failure_moves_to_failed_after_retry_budget(config: Config, monkeypatch):
    def failing_play_and_record(playback_path, recording_path, audio_config):
        raise RuntimeError("device error")

    monkeypatch.setattr("client.state_machine.get_duration_seconds", lambda path: 1.0)
    monkeypatch.setattr("client.state_machine.play_and_record", failing_play_and_record)

    client = ChamberClient(config)
    client.store.upsert_discovered("job-1", "track.wav")
    client.store.set_state("job-1", JobState.DOWNLOADED)
    client._local_input_path("job-1").parent.mkdir(parents=True, exist_ok=True)
    client._local_input_path("job-1").write_bytes(b"fake-wav-bytes")

    for _ in range(config.client.max_job_retries - 1):
        client._record_pending()
        assert client.store.get("job-1").state == JobState.DOWNLOADED

    client._record_pending()
    assert client.store.get("job-1").state == JobState.FAILED


def test_recording_failure_remote_file_tidied_on_next_connected_pass(
    config: Config, fake_sftp: FakeSftp, monkeypatch
):
    """_record_pending fails a job with no sftp connection on hand (by
    design -- playback/recording never depends on connectivity), so its
    remote file is left in processing/ rather than moved to failed/
    immediately. The next connected pass must catch this up, or the
    uploader's download/send --wait polls forever for a recording that will
    never arrive (it only gives up early by noticing failed/)."""

    def failing_play_and_record(playback_path, recording_path, audio_config):
        raise RuntimeError("unsupported sample rate")

    monkeypatch.setattr("client.state_machine.get_duration_seconds", lambda path: 1.0)
    monkeypatch.setattr("client.state_machine.play_and_record", failing_play_and_record)

    client = ChamberClient(config)
    client._ensure_remote_dirs(fake_sftp)
    _drop_incoming_job(fake_sftp, "job-1", "track.wav")
    client._discover_and_claim(fake_sftp)
    client.store.set_state("job-1", JobState.DOWNLOADED)
    client._local_input_path("job-1").parent.mkdir(parents=True, exist_ok=True)
    client._local_input_path("job-1").write_bytes(b"fake-wav-bytes")

    for _ in range(config.client.max_job_retries):
        client._record_pending()
    assert client.store.get("job-1").state == JobState.FAILED

    # Not yet tidied -- _record_pending has no sftp connection to do it with.
    assert Path(fake_sftp.remote_path("processing", "job-1__track.wav")).exists()
    assert not Path(fake_sftp.remote_path("failed", "job-1__track.wav")).exists()

    client._tidy_pending_failures(fake_sftp)

    assert not Path(fake_sftp.remote_path("processing", "job-1__track.wav")).exists()
    assert Path(fake_sftp.remote_path("failed", "job-1__track.wav")).exists()


def test_run_forever_releases_lock_on_stop(config: Config):
    """A stop() requested before the loop body ever runs (simulating SIGTERM
    landing during the first iteration) must still leave no lock file behind
    -- otherwise a fast systemd restart sees a fresh-looking lock and refuses
    to start, thinking a prior instance is still running."""
    client = ChamberClient(config)
    client.stop()
    client.run_forever()
    assert not config.lock_path.exists()


def test_acquire_lock_rejects_recent_lock_but_allows_stale_one(config: Config):
    client = ChamberClient(config)
    client._acquire_lock()
    with pytest.raises(RuntimeError, match="another instance appears to be running"):
        ChamberClient(config)._acquire_lock()

    # Backdate the lock file to simulate one left by a crashed/killed process.
    old_time = time.time() - 9999
    os.utime(config.lock_path, (old_time, old_time))
    ChamberClient(config)._acquire_lock()  # does not raise


def test_download_skipped_when_disk_low(config: Config, fake_sftp: FakeSftp, monkeypatch):
    client = ChamberClient(config)
    client._ensure_remote_dirs(fake_sftp)
    _drop_incoming_job(fake_sftp, "job-1", "track.wav")
    client._discover_and_claim(fake_sftp)

    monkeypatch.setattr(client, "_free_disk_mb", lambda: 0)
    config.client.min_free_disk_mb = 500

    client._download_pending(fake_sftp)
    assert client.store.get("job-1").state == JobState.CLAIMED
    assert not client._local_input_path("job-1").exists()


def test_cleanup_deletes_stale_failed_job_files(config: Config):
    from datetime import datetime, timedelta, timezone

    client = ChamberClient(config)
    client.store.upsert_discovered("job-1", "track.wav")
    client.store.set_state("job-1", JobState.FAILED)
    client._local_input_path("job-1").parent.mkdir(parents=True, exist_ok=True)
    client._local_input_path("job-1").write_bytes(b"x")

    old = (datetime.now(timezone.utc) - timedelta(days=999)).isoformat()
    client.store._conn.execute(
        "UPDATE jobs SET updated_at = ? WHERE job_id = ?", (old, "job-1")
    )

    client._cleanup_local()
    assert not client._local_input_path("job-1").exists()
