"""Interactive setup wizard for the uploader CLI: gathers SFTP settings and
writes a minimal config.yaml (just an sftp: section -- upload_job.py doesn't
need the audio/client/logging sections the chamber client's config has).

Run with: python -m uploader.setup_wizard [--out config.yaml]
"""
from __future__ import annotations

import argparse
import sys

from client.config import save_config
from client.wizard_common import prompt, prompt_sftp_config, sftp_config_dict, test_sftp_connection


def run(out_path: str) -> None:
    print("=== Buckyball uploader setup wizard ===\n")
    sftp_config = prompt_sftp_config()
    if not test_sftp_connection(sftp_config):
        if not prompt("Save the config anyway? [y/n]", "n").lower().startswith("y"):
            sys.exit(1)

    save_config({"sftp": sftp_config_dict(sftp_config)}, out_path)
    print(f"\nWrote {out_path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Uploader SFTP config wizard")
    parser.add_argument("--out", default="config.yaml", help="where to write the config")
    args = parser.parse_args(argv)
    run(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
