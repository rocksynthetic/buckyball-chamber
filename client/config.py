from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class SftpConfig:
    host: str
    port: int
    username: str
    private_key_path: str
    remote_base_dir: str
    connect_timeout_seconds: float
    backoff_base_seconds: float
    backoff_cap_seconds: float


@dataclass
class AudioConfig:
    output_device_name: str
    input_device_name: str
    output_channels: int
    input_channels: int
    samplerate: int
    tail_seconds: float


@dataclass
class ClientConfig:
    local_state_dir: str
    poll_interval_seconds: float
    max_job_retries: int
    failed_job_retention_days: int
    temp_file_ttl_days: int
    min_free_disk_mb: int


@dataclass
class LoggingConfig:
    log_dir: str
    level: str


@dataclass
class Config:
    sftp: SftpConfig
    audio: AudioConfig
    client: ClientConfig
    logging: LoggingConfig

    @property
    def state_dir(self) -> Path:
        return Path(self.client.local_state_dir).expanduser().resolve()

    @property
    def downloads_dir(self) -> Path:
        return self.state_dir / "downloads"

    @property
    def recordings_dir(self) -> Path:
        return self.state_dir / "recordings"

    @property
    def db_path(self) -> Path:
        return self.state_dir / "jobs.sqlite3"

    @property
    def lock_path(self) -> Path:
        return self.state_dir / "chamber-client.lock"


def load_config(path: str | Path) -> Config:
    path = Path(path).expanduser()
    with path.open("r") as f:
        raw = yaml.safe_load(f)

    return Config(
        sftp=SftpConfig(**raw["sftp"]),
        audio=AudioConfig(**raw["audio"]),
        client=ClientConfig(**raw["client"]),
        logging=LoggingConfig(**raw["logging"]),
    )


def save_config(config_dict: dict, path: str | Path) -> None:
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        yaml.safe_dump(config_dict, f, sort_keys=False)
