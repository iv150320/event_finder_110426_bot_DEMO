#!/usr/bin/env python3
"""Main entrypoint — runs Telegram bot + ICS Feed server in parallel."""

import logging
import signal
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

_ics_server = None


def run_ics_server():
    """Run ICS feed server in a separate thread."""
    global _ics_server
    from ics_server import create_server

    try:
        _ics_server = create_server()
        _ics_server.serve_forever()
    except Exception as e:
        logger.error(f"ICS server error: {e}")


def _shutdown(signum, frame):
    """Graceful shutdown handler."""
    logger.info(f"Received signal {signum}, shutting down...")
    if _ics_server:
        _ics_server.shutdown()
    sys.exit(0)


def main():
    logger.info("Starting Event Finder v9.2.0 (Bot + ICS Feed)")

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    ics_thread = threading.Thread(target=run_ics_server, daemon=True, name="ics-server")
    ics_thread.start()
    logger.info("ICS Feed Server thread started")

    from bot import main as run_bot

    try:
        run_bot()
    except Exception as e:
        logger.error(f"Bot failed to start: {e}")
        logger.info("ICS Feed Server is still running on its thread")
        while True:
            import time as _time
            _time.sleep(60)


if __name__ == "__main__":
    main()
