from __future__ import annotations

import ctypes
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

from client import audio, setup_wizard
from client.alsa import AlsaSettings, _PaAlsaStreamInfo, usb_channel_counts
from client.config import AudioConfig


DESCRIPTORS = """Scarlett USB Audio
Playback:
  Status: Stop
  Interface 1
    Altset 1
    Format: S32_LE
    Channels: 26
    Rates: 44100, 48000
  Interface 1
    Altset 2
    Format: S32_LE
    Channels: 18
    Rates: 88200, 96000
  Interface 1
    Altset 3
    Format: S32_LE
    Channels: 10
    Rates: 176400, 192000
Capture:
  Status: Stop
  Interface 2
    Altset 1
    Format: S32_LE
    Channels: 26
    Rates: 44100, 48000
  Interface 2
    Altset 2
    Format: S32_LE
    Channels: 18
    Rates: 88200, 96000
  Interface 2
    Altset 3
    Format: S32_LE
    Channels: 10
    Rates: 176400, 192000
"""


@pytest.fixture
def scarlett(monkeypatch):
    monkeypatch.setattr(audio, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(audio.sd, "query_devices", lambda *a: {
        "name": "Scarlett 18i20 4th Gen: USB Audio (hw:0,0)", "hostapi": 0,
    })
    monkeypatch.setattr(audio.sd, "query_hostapis", lambda *a: {"name": "ALSA"})
    monkeypatch.setattr(audio, "usb_stream_text", lambda *a: DESCRIPTORS)
    monkeypatch.setattr(audio, "find_device", lambda *a, **k: 0)
    monkeypatch.setattr(audio.sd, "wait", lambda: None)


@pytest.mark.parametrize("kind", ["input", "output"])
@pytest.mark.parametrize("rate,count", [(44100, 26), (48000, 26), (88200, 18),
                                       (96000, 18), (176400, 10), (192000, 10)])
def test_resolves_rate_dependent_channels(scarlett, kind, rate, count):
    endpoint = audio.resolve_endpoint(0, kind=kind, channels=4, samplerate=rate)
    assert endpoint.channels == count
    assert endpoint.extra_settings._info.deviceString == b"hw:0,0"


def test_descriptors_keep_directions_separate():
    text = DESCRIPTORS.split("Capture:")[0]
    text += "Capture:\n  Altset 1\n  Channels: 4\n  Rates: 96000\n"
    assert usb_channel_counts(text, "input", 96000) == [4]
    assert usb_channel_counts(text, "output", 96000) == [18]


@pytest.mark.parametrize("rate,channels", [(32000, 2), (96000, 20)])
def test_rejects_unadvertised_combination(scarlett, rate, channels):
    with pytest.raises(audio.AudioDeviceError, match="USB hardware advertises"):
        audio.resolve_endpoint(0, kind="output", channels=channels, samplerate=rate)


def test_no_descriptors_preserves_standard_path(scarlett, monkeypatch):
    monkeypatch.setattr(audio, "usb_stream_text", lambda *a: None)
    endpoint = audio.resolve_endpoint(0, kind="input", channels=4, samplerate=96000)
    assert endpoint.channels == 4
    assert endpoint.extra_settings is None


def test_non_alsa_device_preserves_standard_path(scarlett, monkeypatch):
    monkeypatch.setattr(audio.sd, "query_hostapis", lambda *a: {"name": "PulseAudio"})
    endpoint = audio.resolve_endpoint(0, kind="input", channels=4, samplerate=96000)
    assert endpoint.channels == 4
    assert endpoint.extra_settings is None


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_other_platforms_do_not_use_alsa(scarlett, monkeypatch, platform):
    monkeypatch.setattr(audio, "sys", SimpleNamespace(platform=platform))

    def unexpected(*args):
        pytest.fail("Native platform must not query ALSA hardware or settings")

    monkeypatch.setattr(audio.sd, "query_devices", unexpected)
    monkeypatch.setattr(audio, "usb_stream_text", unexpected)
    for kind, channels in (("output", 2), ("input", 4)):
        endpoint = audio.resolve_endpoint(0, kind=kind, channels=channels, samplerate=96000)
        assert endpoint.channels == channels
        assert endpoint.extra_settings is None


def test_native_stream_info_layout_and_lifetime():
    settings = AlsaSettings("hw:0,0")
    address = int(audio.sd._ffi.cast("uintptr_t", settings._streaminfo))
    native = _PaAlsaStreamInfo.from_address(address)
    assert native.size == ctypes.sizeof(_PaAlsaStreamInfo)
    assert native.hostApiType == 8
    assert native.version == 1
    assert native.deviceString == b"hw:0,0"


@pytest.mark.parametrize("rate,hardware_channels", [(48000, 26), (96000, 18), (192000, 10)])
def test_playrec_silences_extra_outputs_and_retains_logical_inputs(
    scarlett, monkeypatch, tmp_path, rate, hardware_channels,
):
    cfg = AudioConfig("Scarlett", "Scarlett", 2, 4, 48000, 0.01)
    source = tmp_path / "source.wav"
    target = tmp_path / "recording.wav"
    signal = np.tile(np.array([0.1, 0.2], dtype="float32"), (100, 1))
    sf.write(source, signal, rate, subtype="FLOAT")
    checks = []

    def check(**kwargs):
        # Model the cached-minimum bug: without native settings, even an
        # explicit request for 18 channels would be forced up to 26.
        assert kwargs["channels"] == hardware_channels
        assert kwargs["samplerate"] == rate
        assert kwargs["extra_settings"]._info.deviceString == b"hw:0,0"
        checks.append(kwargs)

    monkeypatch.setattr(audio.sd, "check_output_settings", check)
    monkeypatch.setattr(audio.sd, "check_input_settings", check)

    def playrec(data, **kwargs):
        settle_frames = round(cfg.tail_seconds * rate)
        assert data.shape == (settle_frames + 100 + round(cfg.tail_seconds * rate), hardware_channels)
        assert not np.any(data[:settle_frames])
        np.testing.assert_array_equal(data[settle_frames:settle_frames + 100, :2], signal)
        assert not np.any(data[:, 2:])
        assert not np.any(data[settle_frames + 100:])
        assert kwargs["channels"] == hardware_channels
        assert all(isinstance(s, AlsaSettings) for s in kwargs["extra_settings"])
        return np.tile(np.arange(kwargs["logical_channels"], dtype="float32") / 100,
                       (len(data), 1)), 0

    monkeypatch.setattr(audio, "_playrec_with_latency", playrec)
    audio.play_and_record(source, target, cfg)
    assert len(checks) == 2
    result, result_rate = sf.read(target, always_2d=True)
    assert result_rate == rate
    assert len(result) == 100 + round(cfg.tail_seconds * rate)
    assert result.shape[1] == 4
    np.testing.assert_allclose(result[0], np.arange(4) / 100, atol=1 / 32768)


def test_wizard_uses_same_hardware_settings(scarlett, monkeypatch, capsys):
    def check(**kwargs):
        assert kwargs["channels"] == 18
        assert isinstance(kwargs["extra_settings"], AlsaSettings)

    monkeypatch.setattr(audio.sd, "check_output_settings", check)
    monkeypatch.setattr(audio.sd, "check_input_settings", check)

    def play(data, **kwargs):
        assert data.shape == (96000, 18)
        assert np.any(data[:, :2])
        assert not np.any(data[:, 2:])
        assert isinstance(kwargs["extra_settings"], AlsaSettings)

    def rec(frames, **kwargs):
        assert kwargs["channels"] == 18
        assert isinstance(kwargs["extra_settings"], AlsaSettings)
        return np.full((frames, 18), 0.1, dtype="float32")

    monkeypatch.setattr(audio.sd, "play", play)
    monkeypatch.setattr(audio.sd, "rec", rec)
    setup_wizard._test_playback(0, 2, 96000)
    setup_wizard._test_recording(0, 4, 96000)
    output = capsys.readouterr().out
    assert "ch3:" in output
    assert "ch4:" not in output


def test_sounddevice_bridge_uses_native_sentinel_only_for_alsa(monkeypatch):
    # Exercise sounddevice's actual parameter builder, including native ABI.
    monkeypatch.setattr(audio.sd, "query_devices", lambda *a: {
        "max_input_channels": 26, "max_output_channels": 26,
        "default_low_input_latency": 0.01, "default_low_output_latency": 0.01,
        "default_samplerate": 44100,
    })
    settings = AlsaSettings("hw:0,0")
    for kind in ("input", "output"):
        params, _, _, rate = audio.sd._get_stream_parameters(
            kind, 0, 18, "float32", "low", (settings, settings), 96000,
        )
        assert params.device == -2
        assert params.channelCount == 18
        assert params.hostApiSpecificStreamInfo == settings._streaminfo
        assert rate == 96000
        native, _, _, _ = audio.sd._get_stream_parameters(
            kind, 0, 2, "float32", "low", None, 96000,
        )
        assert native.device == 0
        assert native.hostApiSpecificStreamInfo == audio.sd._ffi.NULL


def test_wizard_saves_logical_channels_and_original_device_name(monkeypatch, tmp_path):
    import yaml

    device = {"index": 0, "name": "Scarlett USB Audio (hw:0,0)",
              "max_input_channels": 26, "max_output_channels": 26}
    monkeypatch.setattr(setup_wizard, "list_devices", lambda: [device])
    monkeypatch.setattr(setup_wizard, "prompt", lambda text, default: default)
    monkeypatch.setattr(setup_wizard, "prompt_int", lambda text, default: default)
    monkeypatch.setattr(setup_wizard, "prompt_float", lambda text, default: default)
    monkeypatch.setattr(setup_wizard, "prompt_sftp_config", lambda: SimpleNamespace(host="test", port=22))
    monkeypatch.setattr(setup_wizard, "ensure_host_key_trusted", lambda *a: None)
    monkeypatch.setattr(setup_wizard, "test_sftp_connection", lambda *a: True)
    monkeypatch.setattr(setup_wizard, "sftp_config_dict", lambda *a: {})
    calls = []
    monkeypatch.setattr(setup_wizard, "_test_playback", lambda *a: calls.append(a))
    monkeypatch.setattr(setup_wizard, "_test_recording", lambda *a: calls.append(a))
    path = tmp_path / "config.yaml"
    setup_wizard.run(str(path))
    saved = yaml.safe_load(path.read_text())["audio"]
    assert saved["output_device_name"] == device["name"]
    assert saved["input_device_name"] == device["name"]
    assert saved["output_channels"] == 2
    assert saved["input_channels"] == 4
    assert calls == [(0, 2, 48000), (0, 4, 48000)]
