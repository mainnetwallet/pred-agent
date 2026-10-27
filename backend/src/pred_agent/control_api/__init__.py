"""Control API package — private FastAPI API for the Expo mobile app.

Grounded in kit/07_CONTROL_API.md.
Never exposed to the public internet; bound to Tailscale interface or localhost.
"""

from .app import create_app

__all__ = ["create_app"]
