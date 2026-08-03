from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf

from client.config import AudioConfig

logger = logging.getLogger(__name__)


class AudioDeviceError(RuntimeError):
    pass


def list_devices() -> list[dict]:
    """Return sounddevice's device list as plain dicts (name, max in/out channels)."""
    devices = sd.query_devices()
    return [
        {
            "index": i,
            "name": d["name"],
            "max_input_channels": d["max_input_channels"],
            "max_output_channels": d["max_output_channels"],
            "default_samplerate": d["default_samplerate"],
        }
        for i, d in enumerate(devices)
    ]


def find_device(name_substring: str, *, kind: str, required_channels: int) -> int:
    """Find a device index by case-insensitive name substring match.

    kind is 'input' or 'output'. Raises AudioDeviceError if no device matches,
    or if the best match doesn't report enough channels of the requested kind.
    """
    channel_key = f"max_{kind}_channels"
    needle = name_substring.lower()
    matches = [d for d in list_devices() if needle in d["name"].lower() and d[channel_key] > 0]
    if not matches:
        raise AudioDeviceError(
            f"no {kind} device found matching {name_substring!r}. "
            f"Run the setup wizard to see available devices."
        )
    if len(matches) > 1:
        logger.warning(
            "multiple %s devices match %r; using the first: %s",
            kind, name_substring, matches[0]["name"],
        )
    device = matches[0]
    if device[channel_key] < required_channels:
        raise AudioDeviceError(
            f"device {device['name']!r} only supports {device[channel_key]} "
            f"{kind} channels, but {required_channels} were requested"
        )
    return device["index"]


def get_duration_seconds(path: str | Path) -> float:
    info = sf.info(str(path))
    return info.frames / info.samplerate


def _match_channels(data: np.ndarray, target_channels: int) -> np.ndarray:
    """Adapt a (frames, source_channels) array to exactly target_channels
    by duplicating the last channel (upmix) or truncating (downmix)."""
    source_channels = data.shape[1]
    if source_channels == target_channels:
        return data
    if source_channels < target_channels:
        pad = np.repeat(data[:, -1:], target_channels - source_channels, axis=1)
        logger.warning(
            "playback file has %d channel(s), output device wants %d; "
            "duplicating last channel to fill the rest",
            source_channels, target_channels,
        )
        return np.concatenate([data, pad], axis=1)
    logger.warning(
        "playback file has %d channels, output device wants %d; truncating",
        source_channels, target_channels,
    )
    return data[:, :target_channels]


def play_and_record(
    playback_path: str | Path,
    recording_path: str | Path,
    audio_config: AudioConfig,
) -> None:
    """Play playback_path through the output device while simultaneously
    recording from the input device, for the playback's duration plus a
    fixed tail (to capture reverb/decay), then write the recording to disk.

    Supports an arbitrary number of input channels (e.g. a 4+ channel
    ambisonic microphone) independent of the output channel count.
    """
    output_device = find_device(
        audio_config.output_device_name, kind="output", required_channels=audio_config.output_channels
    )
    input_device = find_device(
        audio_config.input_device_name, kind="input", required_channels=audio_config.input_channels
    )

    data, file_samplerate = sf.read(str(playback_path), dtype="float32", always_2d=True)
    if file_samplerate != audio_config.samplerate:
        raise AudioDeviceError(
            f"playback file samplerate {file_samplerate} != configured samplerate "
            f"{audio_config.samplerate}; resampling is not supported in this MVP"
        )
    data = _match_channels(data, audio_config.output_channels)

    tail_frames = int(round(audio_config.tail_seconds * audio_config.samplerate))
    silence = np.zeros((tail_frames, audio_config.output_channels), dtype="float32")
    padded = np.concatenate([data, silence], axis=0)

    logger.info(
        "playing %s (%.2fs) + %.2fs tail, recording %d channel(s)",
        playback_path, len(data) / audio_config.samplerate, audio_config.tail_seconds,
        audio_config.input_channels,
    )

    recorded = sd.playrec(
        padded,
        samplerate=audio_config.samplerate,
        channels=audio_config.input_channels,
        device=(input_device, output_device),
        dtype="float32",
    )
    sd.wait()

    Path(recording_path).parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(recording_path), recorded, audio_config.samplerate)
    logger.info("wrote recording to %s", recording_path)
