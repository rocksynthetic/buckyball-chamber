from __future__ import annotations

import argparse
import logging
import signal
import sys

from client.config import load_config
from client.logging_config import setup_logging
from client.state_machine import ChamberClient

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Buckyball chamber client daemon")
    parser.add_argument("--config", default="config.yaml", help="path to config.yaml")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    setup_logging(config.logging)

    client = ChamberClient(config)

    def _handle_signal(signum, _frame):
        logger.info("received signal %s, shutting down", signum)
        client.stop()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    logger.info("chamber client starting (state dir: %s)", config.state_dir)
    try:
        client.run_forever()
    except RuntimeError as exc:
        logger.error(str(exc))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
