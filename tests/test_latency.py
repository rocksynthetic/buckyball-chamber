from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

from client import audio
from client.config import AudioConfig


@pytest.mark.parametrize("samplerate", [44100, 96000])
@pytest.mark.parametrize("tail_seconds", [0, 0.013])
def test_compensates_opened_stream_latency_preserves_acoustic_delay_and_tail(
    monkeypatch, tmp_path, samplerate, tail_seconds,
):
    config = AudioConfig("fake", "fake", 2, 4, 48000, tail_seconds)
    signal = np.tile(np.array([0.25, -0.125], dtype="float32"), (127, 1))
    source = tmp_path / "source.wav"
    target = tmp_path / "recording.wav"
    sf.write(source, signal, samplerate, subtype="FLOAT")
    source_bytes = source.read_bytes()
    tail_frames = round(tail_seconds * samplerate)
    latencies = (0.0031, 0.0042)
    delay_frames = round(sum(latencies) * samplerate)
    acoustic_delay = 7
    total_frames = 2 * tail_frames + len(signal) + delay_frames
    expected_output = np.zeros((total_frames, 18), dtype="float32")
    expected_output[tail_frames:tail_frames + len(signal), :2] = signal
    captured_signal = np.tile(np.array([0.25, -0.125, 0.0625, -0.03125], dtype="float32"),
                              (len(signal), 1))
    instances = []

    class FakeStream:
        latency = latencies

        def __init__(self, **kwargs):
            assert kwargs["samplerate"] == samplerate
            assert kwargs["channels"] == (18, 18)
            self.callback = kwargs["callback"]
            self.finished = kwargs["finished_callback"]
            self.closed = False
            instances.append(self)

        def start(self):
            cursor = 0
            sizes = (63, 127, 191)
            block_index = 0
            while True:
                frames = sizes[block_index % len(sizes)]
                outdata = np.full((frames, 18), np.nan, dtype="float32")
                indata = np.zeros((frames, 18), dtype="float32")
                # Simulated physical capture: electronic delay plus a distinct
                # speaker/microphone propagation delay that must be retained.
                indices = np.arange(cursor, cursor + frames) - tail_frames - delay_frames - acoustic_delay
                valid = (indices >= 0) & (indices < len(signal))
                indata[valid, :4] = captured_signal[indices[valid]]
                try:
                    self.callback(indata, outdata, frames, SimpleNamespace(), False)
                except audio.sd.CallbackStop:
                    final = True
                else:
                    final = False
                count = min(frames, total_frames - cursor)
                np.testing.assert_array_equal(outdata[:count], expected_output[cursor:cursor + count])
                assert not np.any(outdata[count:])
                cursor += count
                block_index += 1
                if final:
                    assert cursor == total_frames
                    self.finished()
                    return

        def stop(self):
            pass

        def close(self):
            self.closed = True

    monkeypatch.setattr(audio, "find_device", lambda *a, **k: 0)
    monkeypatch.setattr(audio, "resolve_endpoint", lambda device, **kwargs: audio.AudioEndpoint(0, 18))
    monkeypatch.setattr(audio.sd, "check_output_settings", lambda **k: None)
    monkeypatch.setattr(audio.sd, "check_input_settings", lambda **k: None)
    monkeypatch.setattr(audio.sd, "Stream", FakeStream)
    audio.play_and_record(source, target, config)
    result, rate = sf.read(target, always_2d=True)
    expected = np.zeros((len(signal) + tail_frames, 4), dtype="float32")
    retained = min(len(signal), len(expected) - acoustic_delay)
    expected[acoustic_delay:acoustic_delay + retained] = captured_signal[:retained]
    np.testing.assert_array_equal(result, expected)
    assert rate == samplerate
    assert source.read_bytes() == source_bytes
    assert len(instances) == 1
    assert instances[0].closed


@pytest.mark.parametrize("failure", ["invalid_latency", "dropout", "early_finish", "callback_error"])
def test_invalid_timing_fails_and_closes_stream(monkeypatch, failure):
    instances = []

    class FakeStream:
        latency = (float("nan"), 0.01) if failure == "invalid_latency" else (0.01, 0.01)

        def __init__(self, **kwargs):
            self.callback = kwargs["callback"]
            self.finished = kwargs["finished_callback"]
            self.closed = False
            instances.append(self)

        def start(self):
            if failure == "early_finish":
                self.finished()
                return
            indata = np.zeros((32, 2), dtype="float32")
            if failure == "callback_error":
                indata = None
            outdata = np.ones((32, 2), dtype="float32")
            try:
                self.callback(indata, outdata, 32, SimpleNamespace(),
                              "output underflow" if failure == "dropout" else False)
            except audio.sd.CallbackAbort:
                self.finished()
            else:
                pytest.fail("Invalid callback timing must abort the stream")

        def close(self):
            self.closed = True

    monkeypatch.setattr(audio.sd, "Stream", FakeStream)
    with pytest.raises(audio.AudioDeviceError):
        audio._playrec_with_latency(np.zeros((100, 2), dtype="float32"), samplerate=44100,
                                   channels=2, logical_channels=2, device=(0, 0))
    assert instances[0].closed
