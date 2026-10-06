"""Byte-exact, streaming XZ/LZMA2 transport for WAV files."""
from __future__ import annotations

import logging
import lzma
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

logger = logging.getLogger(__name__)


def wav_name(name: str) -> str:
    return name[:-3] if name.endswith(".xz") else name


def upload_compressed(sftp, source: Path, remote_dir: str, remote_name: str) -> str:
    with TemporaryDirectory(prefix="chamber-xz-") as directory:
        compressed = Path(directory) / "audio.wav.xz"
        with source.open("rb") as src, lzma.open(
            compressed, "wb", preset=6 | lzma.PRESET_EXTREME
        ) as dst:
            shutil.copyfileobj(src, dst)
        logger.info("compressed %s: %d -> %d bytes", source, source.stat().st_size,
                    compressed.stat().st_size)
        return sftp.upload_atomic(compressed, remote_dir, remote_name + ".xz")


def download_audio(sftp, remote_path: str, destination: Path) -> None:
    if not remote_path.endswith(".xz"):
        sftp.download_atomic(remote_path, destination)
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="chamber-xz-", dir=destination.parent) as directory:
        compressed = Path(directory) / "audio.wav.xz"
        decoded = Path(directory) / "audio.wav"
        sftp.download_atomic(remote_path, compressed)
        with lzma.open(compressed, "rb") as src, decoded.open("wb") as dst:
            shutil.copyfileobj(src, dst)
        # Only publish the WAV after the entire stream and checksum validate.
        decoded.replace(destination)
