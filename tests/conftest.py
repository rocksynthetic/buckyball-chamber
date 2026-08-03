from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from client.config import AudioConfig, ClientConfig, Config, LoggingConfig, SftpConfig


class FakeSftp:
    """Stands in for SftpClient in tests: same method surface, backed by a
    plain local directory instead of a real SFTP connection."""

    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def connect(self) -> None:
        pass

    def close(self) -> None:
        pass

    def remote_path(self, *parts: str) -> str:
        return str(self.base_dir.joinpath(*parts))

    def ensure_dir(self, remote_dir: str) -> None:
        Path(remote_dir).mkdir(parents=True, exist_ok=True)

    def listdir(self, remote_dir: str) -> list[str]:
        return [p.name for p in Path(remote_dir).iterdir()]

    def stat_size(self, remote_path: str) -> int:
        return Path(remote_path).stat().st_size

    def exists(self, remote_path: str) -> bool:
        return Path(remote_path).exists()

    def rename(self, old_remote_path: str, new_remote_path: str) -> None:
        Path(old_remote_path).rename(new_remote_path)

    def upload_atomic(self, local_path: Path, remote_dir: str, remote_name: str) -> str:
        final = Path(remote_dir) / remote_name
        temp = Path(remote_dir) / f"{remote_name}.part"
        shutil.copy(local_path, temp)
        temp.rename(final)
        return str(final)

    def download_atomic(self, remote_path: str, local_path: Path) -> None:
        expected_size = self.stat_size(remote_path)
        temp_local = local_path.with_suffix(local_path.suffix + ".tmp")
        local_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(remote_path, temp_local)
        actual_size = temp_local.stat().st_size
        if actual_size != expected_size:
            temp_local.unlink(missing_ok=True)
            raise RuntimeError("incomplete download")
        temp_local.replace(local_path)


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config(
        sftp=SftpConfig(
            host="test",
            port=22,
            username="test",
            private_key_path="~/.ssh/id_ed25519",
            remote_base_dir="/data",
            connect_timeout_seconds=1,
            backoff_base_seconds=1,
            backoff_cap_seconds=10,
        ),
        audio=AudioConfig(
            output_device_name="fake",
            input_device_name="fake",
            output_channels=2,
            input_channels=4,
            samplerate=48000,
            tail_seconds=0.1,
        ),
        client=ClientConfig(
            local_state_dir=str(tmp_path / "state"),
            poll_interval_seconds=0,
            max_job_retries=3,
            failed_job_retention_days=7,
            temp_file_ttl_days=1,
            min_free_disk_mb=0,
        ),
        logging=LoggingConfig(log_dir=str(tmp_path / "logs"), level="INFO"),
    )


@pytest.fixture
def fake_sftp(tmp_path: Path) -> FakeSftp:
    return FakeSftp(tmp_path / "remote")
