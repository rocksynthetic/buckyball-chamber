"""Interactive setup wizard: picks audio devices/channels, gathers SFTP and
timing/retention settings, tests the choices, and writes config.yaml.

Run with: python -m client.setup_wizard [--out config.yaml]
"""
from __future__ import annotations

import argparse
import sys

import numpy as np
import sounddevice as sd

from client.audio import list_devices
from client.config import save_config
from client.sftp_client import SftpClient, SftpConnectionError
from client.config import SftpConfig


def _prompt(msg: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default is not None else ""
    while True:
        value = input(f"{msg}{suffix}: ").strip()
        if value:
            return value
        if default is not None:
            return default
        print("A value is required.")


def _prompt_int(msg: str, default: int) -> int:
    while True:
        raw = _prompt(msg, str(default))
        try:
            return int(raw)
        except ValueError:
            print("Please enter a whole number.")


def _prompt_float(msg: str, default: float) -> float:
    while True:
        raw = _prompt(msg, str(default))
        try:
            return float(raw)
        except ValueError:
            print("Please enter a number.")


def _print_devices(devices: list[dict]) -> None:
    print(f"{'idx':>4}  {'in':>3}  {'out':>3}  name")
    for d in devices:
        print(f"{d['index']:>4}  {d['max_input_channels']:>3}  {d['max_output_channels']:>3}  {d['name']}")


def _choose_device(devices: list[dict], kind: str) -> dict:
    channel_key = f"max_{kind}_channels"
    candidates = [d for d in devices if d[channel_key] > 0]
    if not candidates:
        print(f"No devices with {kind} channels were found.")
        sys.exit(1)
    while True:
        idx = _prompt_int(f"Select the {kind} device index", candidates[0]["index"])
        match = next((d for d in devices if d["index"] == idx), None)
        if match is None or match[channel_key] <= 0:
            print("That index doesn't have any {} channels; try again.".format(kind))
            continue
        return match


def _test_playback(device_index: int, channels: int, samplerate: int) -> None:
    duration = 1.0
    t = np.linspace(0, duration, int(samplerate * duration), endpoint=False)
    tone = (0.2 * np.sin(2 * np.pi * 440 * t)).astype("float32")
    data = np.tile(tone[:, None], (1, channels))
    print(f"Playing a 1s test tone on {channels} channel(s)...")
    sd.play(data, samplerate=samplerate, device=device_index)
    sd.wait()


def _test_recording(device_index: int, channels: int, samplerate: int) -> None:
    duration = 3.0
    print(f"Recording {duration}s from {channels} channel(s) -- make some noise...")
    rec = sd.rec(int(duration * samplerate), samplerate=samplerate, channels=channels, device=device_index)
    sd.wait()
    peaks = np.abs(rec).max(axis=0)
    for ch, peak in enumerate(peaks):
        bar = "#" * int(peak * 40)
        print(f"  ch{ch}: peak={peak:.3f} {bar}")
    if peaks.max() < 0.01:
        print("  Warning: very low signal detected on all channels -- check the mic/gain.")


def run(out_path: str) -> None:
    print("=== Buckyball chamber client setup wizard ===\n")

    devices = list_devices()
    _print_devices(devices)

    output_device = _choose_device(devices, "output")
    output_channels = _prompt_int(
        "Output channels", min(2, output_device["max_output_channels"])
    )
    if output_channels > output_device["max_output_channels"]:
        print("Requested more output channels than the device supports.")
        sys.exit(1)

    same_device = _prompt("Use the same device for input (mic)? [y/n]", "y").lower().startswith("y")
    if same_device:
        input_device = output_device
    else:
        input_device = _choose_device(devices, "input")

    input_channels = _prompt_int(
        "Input channels (e.g. 4 for a first-order ambisonic mic)",
        min(4, input_device["max_input_channels"]) or 1,
    )
    if input_channels > input_device["max_input_channels"]:
        print("Requested more input channels than the device supports.")
        sys.exit(1)

    samplerate = _prompt_int("Sample rate", 48000)

    if _prompt("Run a live audio test now? [y/n]", "y").lower().startswith("y"):
        _test_playback(output_device["index"], output_channels, samplerate)
        _test_recording(input_device["index"], input_channels, samplerate)
        if not _prompt("Did playback/recording look correct? [y/n]", "y").lower().startswith("y"):
            print("Re-run the wizard and pick different devices/channels if needed.")

    tail_seconds = _prompt_float("Recording tail seconds (extra time after playback ends)", 2.0)

    print("\n-- SFTP settings --")
    host = _prompt("SFTP host")
    port = _prompt_int("SFTP port", 22)
    username = _prompt("SFTP username")
    private_key_path = _prompt("Path to SSH private key", "~/.ssh/id_ed25519")
    remote_base_dir = _prompt("Remote base directory", "/data")
    connect_timeout = _prompt_float("Connect timeout seconds", 15.0)
    backoff_base = _prompt_float("Backoff base seconds", 5.0)
    backoff_cap = _prompt_float("Backoff cap seconds", 300.0)

    sftp_config = SftpConfig(
        host=host,
        port=port,
        username=username,
        private_key_path=private_key_path,
        remote_base_dir=remote_base_dir,
        connect_timeout_seconds=connect_timeout,
        backoff_base_seconds=backoff_base,
        backoff_cap_seconds=backoff_cap,
    )
    print("Testing SFTP connection...")
    client = SftpClient(sftp_config)
    try:
        client.connect()
        print("SFTP connection succeeded.")
    except SftpConnectionError as exc:
        print(f"SFTP connection failed: {exc}")
        if not _prompt("Save the config anyway? [y/n]", "n").lower().startswith("y"):
            sys.exit(1)
    finally:
        client.close()

    print("\n-- Client settings --")
    local_state_dir = _prompt("Local state directory", "./state")
    poll_interval = _prompt_float("Poll interval seconds", 10.0)
    max_retries = _prompt_int("Max job retries before giving up", 5)
    failed_retention_days = _prompt_int("Failed-job local file retention (days)", 7)
    temp_file_ttl_days = _prompt_int("Orphaned temp-file TTL (days)", 1)
    min_free_disk_mb = _prompt_int("Minimum free disk space to keep (MB)", 500)

    print("\n-- Logging --")
    log_dir = _prompt("Log directory", "./logs")
    log_level = _prompt("Log level", "INFO")

    config_dict = {
        "sftp": {
            "host": host,
            "port": port,
            "username": username,
            "private_key_path": private_key_path,
            "remote_base_dir": remote_base_dir,
            "connect_timeout_seconds": connect_timeout,
            "backoff_base_seconds": backoff_base,
            "backoff_cap_seconds": backoff_cap,
        },
        "audio": {
            "output_device_name": output_device["name"],
            "input_device_name": input_device["name"],
            "output_channels": output_channels,
            "input_channels": input_channels,
            "samplerate": samplerate,
            "tail_seconds": tail_seconds,
        },
        "client": {
            "local_state_dir": local_state_dir,
            "poll_interval_seconds": poll_interval,
            "max_job_retries": max_retries,
            "failed_job_retention_days": failed_retention_days,
            "temp_file_ttl_days": temp_file_ttl_days,
            "min_free_disk_mb": min_free_disk_mb,
        },
        "logging": {
            "log_dir": log_dir,
            "level": log_level,
        },
    }
    save_config(config_dict, out_path)
    print(f"\nWrote {out_path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Chamber client setup wizard")
    parser.add_argument("--out", default="config.yaml", help="where to write the config")
    args = parser.parse_args(argv)
    run(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
