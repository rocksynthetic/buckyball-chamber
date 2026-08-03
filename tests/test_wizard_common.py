from __future__ import annotations

from client.wizard_common import prompt_sftp_config, sftp_config_dict


def test_prompt_sftp_config_uses_defaults_on_blank_input(monkeypatch, capsys):
    answers = iter(["sftp.example.com", "", "", "", "", "", "", ""])
    monkeypatch.setattr("builtins.input", lambda _msg: next(answers))

    config = prompt_sftp_config()

    assert config.host == "sftp.example.com"
    assert config.port == 22
    assert config.username == "chamber"
    assert config.private_key_path == "~/.ssh/id_ed25519"
    assert config.remote_base_dir == "/data"
    assert config.connect_timeout_seconds == 15.0
    assert config.backoff_base_seconds == 5.0
    assert config.backoff_cap_seconds == 300.0


def test_sftp_config_dict_roundtrips_all_fields(monkeypatch):
    answers = iter(["sftp.example.com", "2222", "bob", "~/.ssh/bob_key", "/queue", "10", "3", "120"])
    monkeypatch.setattr("builtins.input", lambda _msg: next(answers))

    config = prompt_sftp_config()
    as_dict = sftp_config_dict(config)

    assert as_dict == {
        "host": "sftp.example.com",
        "port": 2222,
        "username": "bob",
        "private_key_path": "~/.ssh/bob_key",
        "remote_base_dir": "/queue",
        "connect_timeout_seconds": 10.0,
        "backoff_base_seconds": 3.0,
        "backoff_cap_seconds": 120.0,
    }
