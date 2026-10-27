"""PRED Agent — main entry point.

Grounded in kit/03_ARCHITECTURE.md section 1 (Big picture).
One asyncio process: scanner loop, book feed, research workers, proposal sweeper,
position monitor, Telegram polling, Control API server, daily reports.
"""

from __future__ import annotations

import asyncio
import signal
import sys

from pred_agent.config import load_settings
from pred_agent.util.redaction import install_redaction


def main() -> int:
    """Entry point for the pred-agent process."""
    install_redaction(["", "pred_agent"])
    settings = load_settings()

    print("pred-agent starting...")
    print(f"Mode: {'LIVE' if settings.live_trading else 'PAPER'}")
    print(f"Host: {settings.host}")
    print(f"Chain ID: {settings.chain_id}")

    # TODO: wire scanner, realtime, execution, telegram, control_api here.
    # This is the process entry point; phase modules are imported and started
    # from this main loop.

    # Graceful shutdown on SIGINT/SIGTERM
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    stop = asyncio.Event()

    def _handle_signal() -> None:
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except NotImplementedError:
            # Windows may not support add_signal_handler
            pass

    print("pred-agent running. Press Ctrl+C to stop.")
    try:
        asyncio.get_event_loop().run_until_complete(stop.wait())
    finally:
        print("pred-agent stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
