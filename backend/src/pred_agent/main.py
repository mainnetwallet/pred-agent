"""PRED Agent — main entry point.

Grounded in kit/03_ARCHITECTURE.md section 1 (Big picture).
One asyncio process: scanner loop, book feed, research workers, proposal sweeper,
position monitor, Telegram polling, Control API server, daily reports.
"""

from __future__ import annotations

import os
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

    # One asyncio process: scanner loop + rules monitor run inside the app
    # lifespan (see control_api/background.py); uvicorn serves the Control API.
    import uvicorn

    from pred_agent.control_api.app import create_app

    # Render (and most PaaS) inject PORT and require binding 0.0.0.0.
    # Elsewhere, honour CONTROL_API_BIND (Tailscale IP) or stay on localhost.
    port = int(os.environ.get("PORT", "8000"))
    if os.environ.get("PORT"):
        bind = "0.0.0.0"  # noqa: S104
    else:
        bind = settings.control_api_bind or "127.0.0.1"

    print(f"Control API listening on {bind}:{port}")
    uvicorn.run(create_app(background=True), host=bind, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
