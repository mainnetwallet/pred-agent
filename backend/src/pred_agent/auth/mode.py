"""Single place that decides CLOB vs JWT.

Rules (docs: Authentication, Builder and Aggregator Onboarding, Overview):
- CLOB (L1 EIP-712 -> per-user L2 HMAC) needs only the wallet key. It does NOT use the ordinary
  login ``X-API-Key`` (``PRED_API_KEY``); the keys PRED documents are not interchangeable.
- JWT needs ``PRED_API_KEY``.
- Live orders always go through CLOB in this agent.

The mode comes from ``PRED_AUTH_MODE`` (default ``clob``). The mere presence of ``PRED_API_KEY``
never changes it, so a leftover key cannot silently route the Safe-approval flow, the status
page, or live arming through a different mode than the one that actually places orders.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

CLOB = "clob"
JWT = "jwt"


def resolve_auth_mode(settings: Any) -> str:
    mode = str(getattr(settings, "pred_auth_mode", CLOB) or CLOB).lower()
    return JWT if mode == JWT else CLOB


def warn_if_api_key_ignored(settings: Any) -> None:
    """Log that a configured PRED_API_KEY is not used in CLOB mode."""
    if resolve_auth_mode(settings) == CLOB and getattr(settings, "pred_api_key", None):
        log.info("PRED_API_KEY is set but ignored: PRED_AUTH_MODE=clob does not use it")
