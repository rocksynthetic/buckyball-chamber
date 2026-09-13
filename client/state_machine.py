from __future__ import annotations

import logging
import shutil
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from client.audio import get_duration_seconds, play_and_record
from client.config import Config
from client.job_store import Job, JobState, JobStore
from client.sftp_client import SftpClient, SftpConnectionError

logger = logging.getLogger(__name__)

REMOTE_DIRS = ("incoming", "processing", "recordings", "done", "failed")

# A lock file older than this is assumed to belong to a crashed process
# (systemd/NSSM will have already restarted us) rather than a live instance.
LOCK_STALE_SECONDS = 60


class JobFilenameError(ValueError):
    pass


def parse_job_filename(filename: str) -> tuple[str, str]:
    job_id, sep, original_name = filename.partition("__")
    if not sep:
        raise JobFilenameError(f"filename {filename!r} does not contain a job_id separator '__'")
    return job_id, original_name


def source_filename(job: Job) -> str:
    return f"{job.job_id}__{job.original_name}"


def recording_filename(job: Job) -> str:
    stem = Path(job.original_name).stem
    return f"{job.job_id}__{stem}__recording.wav"


class ChamberClient:
    def __init__(self, config: Config):
        self.config = config
        self.config.state_dir.mkdir(parents=True, exist_ok=True)
        self.config.downloads_dir.mkdir(parents=True, exist_ok=True)
        self.config.recordings_dir.mkdir(parents=True, exist_ok=True)
        self.store = JobStore(self.config.db_path)
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    # -- single-instance lock -------------------------------------------------

    def _acquire_lock(self) -> None:
        lock_path = self.config.lock_path
        if lock_path.exists():
            age = time.time() - lock_path.stat().st_mtime
            if age < LOCK_STALE_SECONDS:
                raise RuntimeError(
                    f"lock file {lock_path} was updated {age:.0f}s ago; "
                    "another instance appears to be running"
                )
            logger.warning("stale lock file (%.0fs old) found; assuming a prior crash", age)
        lock_path.write_text(str(time.time()))

    def _release_lock(self) -> None:
        self.config.lock_path.unlink(missing_ok=True)

    def _heartbeat_lock(self) -> None:
        self.config.lock_path.write_text(str(time.time()))

    # -- local file helpers ---------------------------------------------------

    def _local_input_path(self, job_id: str) -> Path:
        return self.config.downloads_dir / f"{job_id}.wav"

    def _local_recording_path(self, job_id: str) -> Path:
        return self.config.recordings_dir / f"{job_id}.wav"

    def _delete_local_files(self, job_id: str) -> None:
        for path in (self._local_input_path(job_id), self._local_recording_path(job_id)):
            path.unlink(missing_ok=True)

    def _free_disk_mb(self) -> float:
        usage = shutil.disk_usage(self.config.state_dir)
        return usage.free / (1024 * 1024)

    # -- failure handling -------------------------------------------------------

    def _fail_or_retry(self, job_id: str, error: str, sftp: SftpClient | None = None) -> None:
        exceeded = self.store.record_failure(job_id, error, self.config.client.max_job_retries)
        logger.warning("job %s failed: %s (retry budget exceeded=%s)", job_id, error, exceeded)
        if exceeded:
            self.store.set_state(job_id, JobState.FAILED)
            if sftp is not None:
                self._tidy_failed_job(sftp, job_id)

    def _tidy_failed_job(self, sftp: SftpClient, job_id: str) -> None:
        """Best-effort: move a FAILED job's remote file out of processing/
        into failed/ for operator visibility, and so the uploader's
        download/send --wait stops polling instead of waiting forever. Not
        required for correctness -- the local DB state is what actually
        stops reprocessing."""
        job = self.store.get(job_id)
        if job is None:
            return
        old = sftp.remote_path("processing", source_filename(job))
        if not sftp.exists(old):
            return  # already tidied (or never claimed remotely)
        try:
            new = sftp.remote_path("failed", source_filename(job))
            sftp.rename(old, new)
        except Exception as exc:  # noqa: BLE001
            logger.debug("could not tidy failed job %s remotely: %s", job_id, exc)

    def _tidy_pending_failures(self, sftp: SftpClient) -> None:
        """Catch up on remote tidying for jobs that reached FAILED with no
        sftp connection on hand at the time -- e.g. a playback/recording
        error from _record_pending, which is intentionally connectivity-free."""
        for job in self.store.jobs_in_state(JobState.FAILED):
            self._tidy_failed_job(sftp, job.job_id)

    # -- remote-dependent steps -------------------------------------------------

    def _ensure_remote_dirs(self, sftp: SftpClient) -> None:
        for name in REMOTE_DIRS:
            sftp.ensure_dir(sftp.remote_path(name))

    def _reconcile(self, sftp: SftpClient) -> None:
        """Adopt any remote processing/ entries not reflected in local state,
        recovering from a crash between claim-rename and the DB write, or
        from total loss of the local DB."""
        for filename in sftp.listdir(sftp.remote_path("processing")):
            try:
                job_id, original_name = parse_job_filename(filename)
            except JobFilenameError:
                logger.warning("skipping unrecognized file in processing/: %s", filename)
                continue
            job = self.store.get(job_id)
            if job is None:
                self.store.upsert_discovered(job_id, original_name)
                self.store.set_state(job_id, JobState.CLAIMED)
                logger.info("reconciled orphaned processing/ job %s", job_id)
            elif job.state == JobState.DISCOVERED:
                self.store.set_state(job_id, JobState.CLAIMED)
                logger.info("reconciled job %s stuck in DISCOVERED", job_id)

    def _discover_and_claim(self, sftp: SftpClient) -> None:
        for filename in sftp.listdir(sftp.remote_path("incoming")):
            if filename.endswith(".part"):
                continue
            try:
                job_id, original_name = parse_job_filename(filename)
            except JobFilenameError:
                logger.warning("skipping unrecognized file in incoming/: %s", filename)
                continue
            job = self.store.upsert_discovered(job_id, original_name)
            if job.state != JobState.DISCOVERED:
                continue
            try:
                sftp.rename(sftp.remote_path("incoming", filename), sftp.remote_path("processing", filename))
                self.store.set_state(job_id, JobState.CLAIMED)
                logger.info("claimed job %s (%s)", job_id, original_name)
            except Exception as exc:  # noqa: BLE001
                logger.warning("failed to claim job %s: %s", job_id, exc)

    def _download_pending(self, sftp: SftpClient) -> None:
        for job in self.store.jobs_in_state(JobState.CLAIMED):
            local_input = self._local_input_path(job.job_id)
            if local_input.exists():
                self.store.set_state(job.job_id, JobState.DOWNLOADED)
                continue
            free_mb = self._free_disk_mb()
            if free_mb < self.config.client.min_free_disk_mb:
                logger.warning(
                    "low disk space (%.0fMB free, min %dMB); deferring downloads",
                    free_mb, self.config.client.min_free_disk_mb,
                )
                return
            try:
                remote_path = sftp.remote_path("processing", source_filename(job))
                sftp.download_atomic(remote_path, local_input)
                self.store.set_state(job.job_id, JobState.DOWNLOADED)
                logger.info("downloaded job %s", job.job_id)
            except Exception as exc:  # noqa: BLE001
                self._fail_or_retry(job.job_id, f"download failed: {exc}", sftp)

    def _record_pending(self) -> None:
        """Playback + recording -- purely local, no connectivity required."""
        for job in self.store.jobs_in_state(JobState.DOWNLOADED):
            local_input = self._local_input_path(job.job_id)
            local_recording = self._local_recording_path(job.job_id)
            if local_recording.exists():
                self.store.set_state(job.job_id, JobState.RECORDED)
                continue
            try:
                get_duration_seconds(local_input)  # fail fast on a corrupt/unreadable file
                play_and_record(local_input, local_recording, self.config.audio)
                self.store.set_state(job.job_id, JobState.RECORDED)
                logger.info("recorded job %s", job.job_id)
            except Exception as exc:  # noqa: BLE001
                # No sftp connection available/needed here; a FAILED job's
                # remote file will be tidied into failed/ next time we're
                # connected and happen to revisit it (best effort only).
                self._fail_or_retry(job.job_id, f"playback/recording failed: {exc}")

    def _upload_pending(self, sftp: SftpClient) -> None:
        for job in self.store.jobs_in_state(JobState.RECORDED):
            local_recording = self._local_recording_path(job.job_id)
            try:
                sftp.upload_atomic(
                    local_recording, sftp.remote_path("recordings"), recording_filename(job)
                )
                self.store.set_state(job.job_id, JobState.UPLOADED)
                sftp.rename(
                    sftp.remote_path("processing", source_filename(job)),
                    sftp.remote_path("done", source_filename(job)),
                )
                self.store.set_state(job.job_id, JobState.DONE)
                self._delete_local_files(job.job_id)
                logger.info("job %s done", job.job_id)
            except Exception as exc:  # noqa: BLE001
                self._fail_or_retry(job.job_id, f"upload failed: {exc}", sftp)

    # -- local cleanup (no connectivity required) --------------------------------

    def _cleanup_local(self) -> None:
        now = datetime.now(timezone.utc)

        failed_cutoff = (
            now - timedelta(days=self.config.client.failed_job_retention_days)
        ).isoformat()
        for job in self.store.jobs_older_than(JobState.FAILED, failed_cutoff):
            self._delete_local_files(job.job_id)

        tmp_cutoff = now - timedelta(days=self.config.client.temp_file_ttl_days)
        for directory in (self.config.downloads_dir, self.config.recordings_dir):
            for tmp_file in directory.glob("*.tmp"):
                mtime = datetime.fromtimestamp(tmp_file.stat().st_mtime, tz=timezone.utc)
                if mtime < tmp_cutoff:
                    tmp_file.unlink(missing_ok=True)

    # -- main loop ----------------------------------------------------------------

    def run_forever(self) -> None:
        self._acquire_lock()
        try:
            self._run_loop()
        finally:
            self._release_lock()

    def _run_loop(self) -> None:
        backoff = self.config.sftp.backoff_base_seconds

        while not self._stop_event.is_set():
            self._heartbeat_lock()
            connected = False
            sftp = SftpClient(self.config.sftp)
            try:
                sftp.connect()
                connected = True
            except SftpConnectionError as exc:
                logger.warning("sftp connection failed: %s", exc)

            if connected:
                try:
                    self._ensure_remote_dirs(sftp)
                    self._reconcile(sftp)
                    self._discover_and_claim(sftp)
                    self._download_pending(sftp)
                    self._upload_pending(sftp)
                    self._tidy_pending_failures(sftp)
                    backoff = self.config.sftp.backoff_base_seconds
                except SftpConnectionError as exc:
                    logger.warning("lost sftp connection mid-pass: %s", exc)
                    connected = False
                finally:
                    sftp.close()

            # Playback/recording never depends on connectivity.
            self._record_pending()
            self._cleanup_local()

            if connected:
                sleep_seconds = self.config.client.poll_interval_seconds
            else:
                sleep_seconds = backoff
                backoff = min(backoff * 2, self.config.sftp.backoff_cap_seconds)

            # Event.wait (rather than time.sleep) so stop() wakes us
            # immediately instead of leaving systemd to wait out a full
            # poll interval or backoff (up to backoff_cap_seconds) before
            # SIGKILL-ing an unresponsive process.
            self._stop_event.wait(sleep_seconds)
