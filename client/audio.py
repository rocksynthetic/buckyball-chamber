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


def _check_device_supports_rate(device_index: int, *, kind: str, channels: int, samplerate: int) -> None:
    """Pre-flight check, so an unsupported rate fails with a clear message
    instead of a raw PortAudioError from deep inside sd.playrec."""
    check_fn = sd.check_output_settings if kind == "output" else sd.check_input_settings
    try:
        check_fn(device=device_index, channels=channels, samplerate=samplerate, dtype="float32")
    except Exception as exc:  # noqa: BLE001 - sd raises its own PortAudioError type
        raise AudioDeviceError(
            f"{kind} device does not support {samplerate}Hz at {channels} channel(s): {exc}"
        ) from exc


def play_and_record(
    playback_path: str | Path,
    recording_path: str | Path,
    audio_config: AudioConfig,
) -> None:
    """Play playback_path through the output device while simultaneously
    recording from the input device, for the playback's duration plus a
    fixed tail (to catch reverb/decay), then write the recording to disk.

    The stream is opened at the playback file's own sample rate rather than
    a fixed configured rate: since the client has exclusive access to the
    device (no OS mixer resampling everything to one shared rate), each job
    can just reconfigure the device to whatever rate its source file uses,
    instead of requiring every upload to match a single fixed rate.

    Supports an arbitrary number of input channels (e.g. a 4+ channel
    ambisonic microphone) independent of the output channel count.
    """
    output_device = find_device(
        audio_config.output_device_name, kind="output", required_channels=audio_config.output_channels
    )
    input_device = find_device(
        audio_config.input_device_name, kind="input", required_channels=audio_config.input_channels
    )

    data, samplerate = sf.read(str(playback_path), dtype="float32", always_2d=True)
    data = _match_channels(data, audio_config.output_channels)

    _check_device_supports_rate(
        output_device, kind="output", channels=audio_config.output_channels, samplerate=samplerate
    )
    _check_device_supports_rate(
        input_device, kind="input", channels=audio_config.input_channels, samplerate=samplerate
    )

    tail_frames = int(round(audio_config.tail_seconds * samplerate))
    silence = np.zeros((tail_frames, audio_config.output_channels), dtype="float32")
    padded = np.concatenate([data, silence], axis=0)

    logger.info(
        "playing %s (%.2fs @ %dHz) + %.2fs tail, recording %d channel(s)",
        playback_path, len(data) / samplerate, samplerate, audio_config.tail_seconds,
        audio_config.input_channels,
    )

    recorded = sd.playrec(
        padded,
        samplerate=samplerate,
        channels=audio_config.input_channels,
        device=(input_device, output_device),
        dtype="float32",
    )
    sd.wait()

    Path(recording_path).parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(recording_path), recorded, samplerate)
    logger.info("wrote recording to %s (%dHz)", recording_path, samplerate)
