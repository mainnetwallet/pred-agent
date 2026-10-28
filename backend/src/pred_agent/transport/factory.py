"""Pick the ONE officially supported PRED auth path for the configured account.

Docs: ordinary JWT clients (``X-API-Key`` + wallet-signature login) and Builder/Aggregator
CLOB (L1/L2 + partner key) are separate programs with separate credentials. They are not
interchangeable, so exactly one transport is built:

* ``jwt``  - PRED_API_KEY + PRIVATE_KEY (an existing PRED account, trades via /api/v1/order/...)
* ``clob`` - PRIVATE_KEY only (L1 derive/create; trades via /clob/api/v1/order/...)
* ``auto`` - jwt when PRED_API_KEY is set, otherwise clob
"""

from __future__ import annotations

from typing import Any

from pred_agent.signer.eip712 import private_to_address


def resolve_auth_mode(settings: Any) -> str:
    mode = str(getattr(settings, "pred_auth_mode", "auto") or "auto").lower()
    if mode == "auto":
        return "jwt" if getattr(settings, "pred_api_key", None) else "clob"
    return mode


def build_transport(settings: Any) -> Any:
    """Build the authenticated transport, or raise ValueError explaining what is missing."""
    private_key = getattr(settings, "private_key", None)
    if not private_key:
        raise ValueError("PRIVATE_KEY is not set")
    mode = resolve_auth_mode(settings)
    eoa = private_to_address(private_key)
    if mode == "jwt":
        api_key = getattr(settings, "pred_api_key", None)
        if not api_key:
            raise ValueError("PRED_AUTH_MODE=jwt requires PRED_API_KEY (ordinary-login X-API-Key)")
        from pred_agent.auth.jwt_auth import JwtAuth
        from pred_agent.transport.jwt_transport import JwtTransport

        auth = JwtAuth(
            api_key=api_key,
            private_key=private_key,
            eoa_address=eoa,
            chain_id=settings.chain_id,
            create_proxy_factory=settings.create_proxy_factory,
            login_message=settings.auth_login_message,
            base_url=settings.host,
            refresh_threshold_seconds=settings.refresh_threshold_seconds,
        )
        return JwtTransport(auth, base_url=settings.host)
    from pred_agent.auth.clob_auth import ClobAuth
    from pred_agent.transport.clob_transport import ClobTransport

    clob = ClobAuth(
        eoa_address=eoa,
        private_key=private_key,
        chain_id=settings.chain_id,
        base_url=settings.host,
        aggregator_api_key=getattr(settings, "pred_partner_key", None),
    )
    return ClobTransport(clob, base_url=settings.host)
