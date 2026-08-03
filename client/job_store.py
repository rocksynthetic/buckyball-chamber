from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


class JobState:
    """Job lifecycle states.

    Each state is only ever set once its corresponding on-disk artifact
    (a file of the expected size) has been verified -- this is what makes
    every transition idempotent and safe to resume after a crash.
    """

    DISCOVERED = "DISCOVERED"
    CLAIMED = "CLAIMED"
    DOWNLOADED = "DOWNLOADED"
    RECORDED = "RECORDED"
    UPLOADED = "UPLOADED"
    DONE = "DONE"
    FAILED = "FAILED"

    TERMINAL = {DONE, FAILED}


@dataclass
class Job:
    job_id: str
    original_name: str
    state: str
    retry_count: int
    last_error: str | None
    created_at: str
    updated_at: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStore:
    """SQLite-backed job table. One row per job; every write is committed
    immediately so state survives process crashes and power loss.
    """

    def __init__(self, db_path: str | Path):
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                original_name TEXT NOT NULL,
                state TEXT NOT NULL,
                retry_count INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

    def close(self) -> None:
        self._conn.close()

    def _row_to_job(self, row: sqlite3.Row) -> Job:
        return Job(
            job_id=row["job_id"],
            original_name=row["original_name"],
            state=row["state"],
            retry_count=row["retry_count"],
            last_error=row["last_error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def get(self, job_id: str) -> Job | None:
        row = self._conn.execute(
            "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        return self._row_to_job(row) if row else None

    def upsert_discovered(self, job_id: str, original_name: str) -> Job:
        """Insert a newly-discovered job, or return the existing row unchanged.

        Safe to call repeatedly for the same job_id (e.g. if the client sees
        the same incoming/ listing twice before the claim-rename completes).
        """
        existing = self.get(job_id)
        if existing is not None:
            return existing
        now = _now()
        self._conn.execute(
            """
            INSERT INTO jobs (job_id, original_name, state, retry_count, last_error, created_at, updated_at)
            VALUES (?, ?, ?, 0, NULL, ?, ?)
            """,
            (job_id, original_name, JobState.DISCOVERED, now, now),
        )
        return self.get(job_id)  # type: ignore[return-value]

    def set_state(self, job_id: str, state: str) -> None:
        self._conn.execute(
            "UPDATE jobs SET state = ?, updated_at = ? WHERE job_id = ?",
            (state, _now(), job_id),
        )

    def record_failure(self, job_id: str, error: str, max_retries: int) -> bool:
        """Record a failed attempt. Returns True if the job should now be
        moved to the terminal FAILED state (retry budget exhausted)."""
        job = self.get(job_id)
        if job is None:
            raise KeyError(job_id)
        retry_count = job.retry_count + 1
        self._conn.execute(
            "UPDATE jobs SET retry_count = ?, last_error = ?, updated_at = ? WHERE job_id = ?",
            (retry_count, error, _now(), job_id),
        )
        return retry_count >= max_retries

    def jobs_in_state(self, state: str) -> list[Job]:
        rows = self._conn.execute(
            "SELECT * FROM jobs WHERE state = ? ORDER BY created_at", (state,)
        ).fetchall()
        return [self._row_to_job(r) for r in rows]

    def active_job_ids(self) -> set[str]:
        """Job IDs not yet in a terminal state -- used for startup reconciliation."""
        rows = self._conn.execute(
            "SELECT job_id FROM jobs WHERE state NOT IN (?, ?)",
            (JobState.DONE, JobState.FAILED),
        ).fetchall()
        return {r["job_id"] for r in rows}

    def jobs_older_than(self, state: str, cutoff_iso: str) -> list[Job]:
        rows = self._conn.execute(
            "SELECT * FROM jobs WHERE state = ? AND updated_at < ? ORDER BY updated_at",
            (state, cutoff_iso),
        ).fetchall()
        return [self._row_to_job(r) for r in rows]
