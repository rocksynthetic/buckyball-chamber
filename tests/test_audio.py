from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from client.audio import AudioDeviceError, play_and_record
from client.config import AudioConfig


def _write_wav(path: Path, samplerate: int, channels: int = 2, duration: float = 0.1) -> None:
    frames = int(duration * samplerate)
    data = np.zeros((frames, channels), dtype="float32")
    sf.write(str(path), data, samplerate)


@pytest.fixture
def audio_config() -> AudioConfig:
    return AudioConfig(
        output_device_name="fake",
        input_device_name="fake",
        output_channels=2,
        input_channels=4,
        samplerate=48000,  # deliberately different from the test files below
        tail_seconds=0.05,
    )


def test_play_and_record_uses_files_own_samplerate_not_config(
    tmp_path: Path, audio_config: AudioConfig, monkeypatch
):
    playback_path = tmp_path / "in.wav"
    recording_path = tmp_path / "out.wav"
    _write_wav(playback_path, samplerate=44100, channels=2)

    monkeypatch.setattr("client.audio.find_device", lambda *a, **k: 0)
    monkeypatch.setattr("client.audio.sd.check_output_settings", lambda **k: None)
    monkeypatch.setattr("client.audio.sd.check_input_settings", lambda **k: None)

    captured = {}

    def fake_playrec(data, **kwargs):
        captured["samplerate"] = kwargs["samplerate"]
        captured["frames"] = len(data)
        return np.zeros((len(data), audio_config.input_channels), dtype="float32")

    monkeypatch.setattr("client.audio.sd.playrec", fake_playrec)
    monkeypatch.setattr("client.audio.sd.wait", lambda: None)

    play_and_record(playback_path, recording_path, audio_config)

    assert captured["samplerate"] == 44100  # the file's rate, not audio_config.samplerate (48000)
    assert sf.info(str(recording_path)).samplerate == 44100


def test_play_and_record_raises_clear_error_for_unsupported_rate(
    tmp_path: Path, audio_config: AudioConfig, monkeypatch
):
    playback_path = tmp_path / "in.wav"
    recording_path = tmp_path / "out.wav"
    _write_wav(playback_path, samplerate=192000, channels=2)

    monkeypatch.setattr("client.audio.find_device", lambda *a, **k: 0)

    def unsupported(**kwargs):
        raise RuntimeError("Invalid sample rate")

    monkeypatch.setattr("client.audio.sd.check_output_settings", lambda **k: unsupported(**k))

    with pytest.raises(AudioDeviceError, match="192000"):
        play_and_record(playback_path, recording_path, audio_config)
