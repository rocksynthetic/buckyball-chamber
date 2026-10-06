from pathlib import Path
import lzma

import pytest

from client.transfer import download_audio, upload_compressed
from client.state_machine import ChamberClient
from client.job_store import JobState
from uploader.upload_job import upload, wait_for_recording


def test_compressed_end_to_end(config, fake_sftp, tmp_path, monkeypatch):
    source = tmp_path / 'track.wav'
    original = bytes(range(256)) * 1000
    source.write_bytes(original)
    monkeypatch.setattr('uploader.upload_job.SftpClient', lambda config: fake_sftp)
    monkeypatch.setattr('uploader.upload_job.load_sftp_config', lambda path: config.sftp)
    job_id, remote = upload(str(source), str(tmp_path / 'config.yaml'))
    assert remote.endswith('__track.wav.xz')
    assert lzma.decompress(Path(remote).read_bytes()) == original
    client = ChamberClient(config)
    client._ensure_remote_dirs(fake_sftp)
    client._discover_and_claim(fake_sftp)
    client._download_pending(fake_sftp)
    assert client._local_input_path(job_id).read_bytes() == original
    recorded = b'recording bytes' * 10000
    def record(playback, destination, audio):
        assert Path(playback).read_bytes() == original
        Path(destination).write_bytes(recorded)
    monkeypatch.setattr('client.state_machine.get_duration_seconds', lambda path: 1)
    monkeypatch.setattr('client.state_machine.play_and_record', record)
    client._record_pending()
    client._upload_pending(fake_sftp)
    assert client.store.get(job_id).state == JobState.DONE
    destination = tmp_path / 'result.wav'
    wait_for_recording(lambda: fake_sftp, job_id, 'track.wav', str(destination), .01)
    assert destination.read_bytes() == recorded
    assert not list(tmp_path.glob('chamber-xz-*'))


@pytest.mark.parametrize('damage', ['truncated', 'corrupt'])
def test_bad_xz_never_publishes_wav(fake_sftp, tmp_path, damage):
    encoded = lzma.compress(b'audio' * 10000)
    if damage == 'truncated':
        encoded = encoded[:-10]
    else:
        encoded = encoded[:30] + bytes([encoded[30] ^ 255]) + encoded[31:]
    remote = fake_sftp.base_dir / 'bad.wav.xz'
    remote.write_bytes(encoded)
    destination = tmp_path / 'out.wav'
    destination.write_bytes(b'existing')
    with pytest.raises((lzma.LZMAError, EOFError)):
        download_audio(fake_sftp, str(remote), destination)
    assert destination.read_bytes() == b'existing'
    assert not list(tmp_path.glob('chamber-xz-*'))


def test_compressed_failed_job_stops_waiting(fake_sftp):
    fake_sftp.ensure_dir(fake_sftp.remote_path('failed'))
    Path(fake_sftp.remote_path('failed', 'job__track.wav.xz')).write_bytes(b'x')
    with pytest.raises(RuntimeError, match='failed in the chamber'):
        wait_for_recording(lambda: fake_sftp, 'job', 'track.wav', None, .01)
