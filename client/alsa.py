"""Rate-dependent USB ALSA channels and explicit PortAudio device settings."""
from __future__ import annotations

import ctypes
import functools
import re
from pathlib import Path

import sounddevice as sd


class _PaAlsaStreamInfo(ctypes.Structure):
    # Public ABI from PortAudio's pa_linux_alsa.h (paALSA = 8).
    _fields_ = [
        ("size", ctypes.c_ulong),
        ("hostApiType", ctypes.c_int),
        ("version", ctypes.c_ulong),
        ("deviceString", ctypes.c_char_p),
    ]


class AlsaSettings:
    """sounddevice extra_settings adapter retaining the native structure."""

    def __init__(self, device: str):
        self._device = device.encode()
        self._info = _PaAlsaStreamInfo(
            ctypes.sizeof(_PaAlsaStreamInfo), 8, 1, self._device
        )
        # sounddevice's host-specific settings use this CFFI pointer protocol.
        self._streaminfo = sd._ffi.cast("void *", ctypes.addressof(self._info))
        _install_sounddevice_bridge()


def _install_sounddevice_bridge() -> None:
    """Supply PortAudio's -2 device sentinel for our ALSA settings only.

    sounddevice has no public ALSA settings adapter and needs a real device
    index to look up latency before constructing parameters. Keep that lookup,
    then replace the native index with paUseHostApiSpecificDeviceSpecification.
    This narrowly scoped compatibility bridge also covers play/rec/playrec and
    check_*_settings, so preflight and the actual stream take the same path.
    """
    original = sd._get_stream_parameters
    if getattr(original, "_chamber_alsa_bridge", False):
        return

    @functools.wraps(original)
    def parameters(kind, device, channels, dtype, latency, extra_settings, samplerate):
        result = original(kind, device, channels, dtype, latency, extra_settings, samplerate)
        settings = sd._select_input_or_output(extra_settings, kind)
        if isinstance(settings, AlsaSettings):
            result[0].device = -2
        return result

    parameters._chamber_alsa_bridge = True
    sd._get_stream_parameters = parameters


def usb_channel_counts(text: str, kind: str, samplerate: int) -> list[int]:
    """Read advertised alternate settings, separately for playback/capture."""
    section_name = "Playback" if kind == "output" else "Capture"
    section = re.search(
        rf"^{section_name}:\s*\n(.*?)(?=^(?:Playback|Capture):|\Z)",
        text, re.MULTILINE | re.DOTALL,
    )
    if not section:
        return []
    counts = set()
    for block in re.split(r"^\s*Altset\s+\d+\s*$", section[1], flags=re.MULTILINE)[1:]:
        channels = re.search(r"^\s*Channels:\s*(\d+)", block, re.MULTILINE)
        rates = re.search(r"^\s*Rates:\s*([^\n]+)", block, re.MULTILINE)
        if channels and rates and samplerate in [int(r) for r in re.findall(r"\d+", rates[1])]:
            counts.add(int(channels[1]))
    return sorted(counts)


def usb_stream_text(card: int, device: int) -> str | None:
    # USB PCM device N corresponds to streamN; do not merge unrelated endpoints.
    try:
        return Path(f"/proc/asound/card{card}/stream{device}").read_text()
    except OSError:
        return None
