from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from client.audio import AudioDeviceError, AudioEndpoint, play_and_record
from client.config import AudioConfig


def _write_wav(path: Path, samplerate: int, channels: int = 2, duration: float = 0.1) -> None:
    frames = int(duration * samplerate)
    data = np.zeros((frames, channels), dtype="float32")
    sf.write(str(path), data, samplerate)


@pytest.fixture
def audio_config(monkeypatch) -> AudioConfig:
    # These tests isolate file-rate behavior; USB negotiation has its own suite.
    monkeypatch.setattr("client.audio.resolve_endpoint", lambda device, **kwargs:
                        AudioEndpoint(device, kwargs["channels"]))
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
        return np.zeros((len(data), audio_config.input_channels), dtype="float32"), 0

    monkeypatch.setattr("client.audio._playrec_with_latency", fake_playrec)
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


@pytest.mark.parametrize("samplerate", [44100, 96000])
def test_device_settles_in_same_stream_without_changing_audio_files(
    tmp_path, audio_config, monkeypatch, samplerate,
):
    source = tmp_path / "source.wav"
    destination = tmp_path / "recording.wav"
    signal = np.tile(np.array([0.1, -0.2], dtype="float32"), (127, 1))
    sf.write(source, signal, samplerate, subtype="FLOAT")
    original_bytes = source.read_bytes()
    settle_frames = round(audio_config.tail_seconds * samplerate)
    tail_frames = round(audio_config.tail_seconds * samplerate)
    monkeypatch.setattr("client.audio.find_device", lambda *a, **k: 0)
    monkeypatch.setattr("client.audio.sd.check_output_settings", lambda **k: None)
    monkeypatch.setattr("client.audio.sd.check_input_settings", lambda **k: None)
    events = []

    def fake_playrec(data, **kwargs):
        events.append("open_and_play")
        assert kwargs["samplerate"] == samplerate
        assert len(data) == settle_frames + len(signal) + tail_frames
        assert not np.any(data[:settle_frames])
        np.testing.assert_array_equal(data[settle_frames:settle_frames + len(signal)], signal)
        assert not np.any(data[settle_frames + len(signal):])
        captured = np.full((len(data), 4), 0.125, dtype="float32")
        # Distinct initialization artifact must not appear in the saved WAV.
        captured[:settle_frames] = 0.75
        return captured, 0

    monkeypatch.setattr("client.audio._playrec_with_latency", fake_playrec)
    monkeypatch.setattr("client.audio.sd.wait", lambda: events.append("wait"))
    play_and_record(source, destination, audio_config)
    result, rate = sf.read(destination, always_2d=True)
    assert events == ["open_and_play"]
    assert rate == samplerate
    assert result.shape == (len(signal) + tail_frames, 4)
    np.testing.assert_array_equal(result, 0.125)
    assert source.read_bytes() == original_bytes
