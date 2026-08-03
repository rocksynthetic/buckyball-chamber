from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from client.config import LoggingConfig


def setup_logging(config: LoggingConfig) -> None:
    log_dir = Path(config.log_dir).expanduser()
    log_dir.mkdir(parents=True, exist_ok=True)

    level = getattr(logging, config.level.upper(), logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
    )

    file_handler = RotatingFileHandler(
        log_dir / "chamber-client.log", maxBytes=5 * 1024 * 1024, backupCount=5
    )
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(file_handler)
    root.addHandler(console_handler)
