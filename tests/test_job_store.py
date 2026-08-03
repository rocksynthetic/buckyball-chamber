from __future__ import annotations

from pathlib import Path

from client.job_store import JobState, JobStore


def test_upsert_discovered_is_idempotent(tmp_path: Path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    job1 = store.upsert_discovered("job-1", "track.wav")
    job2 = store.upsert_discovered("job-1", "track.wav")
    assert job1 == job2
    assert job1.state == JobState.DISCOVERED
    assert job1.retry_count == 0


def test_set_state_transitions(tmp_path: Path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    store.upsert_discovered("job-1", "track.wav")
    store.set_state("job-1", JobState.CLAIMED)
    assert store.get("job-1").state == JobState.CLAIMED


def test_record_failure_exhausts_retry_budget(tmp_path: Path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    store.upsert_discovered("job-1", "track.wav")
    assert store.record_failure("job-1", "boom", max_retries=3) is False
    assert store.record_failure("job-1", "boom", max_retries=3) is False
    assert store.record_failure("job-1", "boom", max_retries=3) is True
    assert store.get("job-1").retry_count == 3


def test_active_job_ids_excludes_terminal_states(tmp_path: Path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    store.upsert_discovered("job-1", "a.wav")
    store.upsert_discovered("job-2", "b.wav")
    store.set_state("job-2", JobState.DONE)
    assert store.active_job_ids() == {"job-1"}
