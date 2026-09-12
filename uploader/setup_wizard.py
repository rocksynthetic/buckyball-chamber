"""Interactive setup wizard for the uploader CLI: generates an SSH keypair
dedicated to this install, gathers SFTP settings, and writes a minimal
config.yaml (just an sftp: section -- upload_job.py doesn't need the
audio/client/logging sections the chamber client's config has).

Run with: python -m uploader.setup_wizard [--out config.yaml]
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from client.config import save_config
from client.wizard_common import ensure_host_key_trusted, prompt_sftp_config, sftp_config_dict


def _ensure_key(key_path: Path) -> None:
    """Generate a passphrase-less ed25519 keypair at key_path if one isn't
    already there, and surface the public half for adding to the server."""
    if key_path.exists():
        print(f"Using existing keypair at {key_path}")
        return

    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.parent.chmod(0o700)
    print(f"Generating a new SSH keypair at {key_path} ...")
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-f", str(key_path), "-N", ""],
        check=True,
        stdout=subprocess.DEVNULL,
    )

    pubkey_text = key_path.with_suffix(".pub").read_text().strip()
    print("\nPublic key (send this to whoever runs setup_server.sh on the SFTP server):")
    print(f"  {pubkey_text}\n")
    copied = False
    if shutil.which("pbcopy"):
        try:
            subprocess.run(["pbcopy"], input=pubkey_text, text=True, check=True)
            copied = True
        except (subprocess.CalledProcessError, OSError):
            pass
    print("(copied to your clipboard)" if copied else "(copy the line above manually)")


def run(out_path: str) -> None:
    print("=== Buckyball uploader setup wizard ===\n")

    key_path = Path(out_path).expanduser().resolve().parent / "keys" / "id_ed25519"
    _ensure_key(key_path)

    sftp_config = prompt_sftp_config(default_private_key_path=str(key_path))
    ensure_host_key_trusted(sftp_config.host, sftp_config.port)

    save_config({"sftp": sftp_config_dict(sftp_config)}, out_path)
    print(f"\nWrote {out_path}")
    print(
        "\nOnce this install's public key has been added to the server, run "
        "`chamber test` (or `python -m uploader.upload_job test --config "
        f"{out_path}`) to verify the connection."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Uploader SFTP config wizard")
    parser.add_argument("--out", default="config.yaml", help="where to write the config")
    args = parser.parse_args(argv)
    run(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
