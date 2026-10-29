"""scripts/whoami.py — prints mode, EOA, Safe, is_enabled_trading, USDC balance.

No secrets are printed. Run after Phase 1 enablement is complete.
"""

from __future__ import annotations

import sys

from pred_agent.auth.clob_auth import ClobAuth
from pred_agent.auth.jwt_auth import JwtAuth
from pred_agent.config import load_settings
from pred_agent.signer.eip712 import private_to_address
from pred_agent.transport.clob_transport import ClobTransport
from pred_agent.transport.jwt_transport import JwtTransport


def main() -> int:
    settings = load_settings()
    mode = "unknown"
    eoa = "unknown"
    safe = "unknown"
    trading = False

    if settings.pred_api_key:
        mode = "jwt"
        auth = JwtAuth(
            api_key=settings.pred_api_key,
            private_key=settings.private_key or "",
            eoa_address=private_to_address(settings.private_key) if settings.private_key else "0x",
            chain_id=settings.chain_id,
            create_proxy_factory=settings.create_proxy_factory,
            login_message=settings.auth_login_message,
        )
        try:
            tokens = auth.login()
            eoa = tokens.wallet_address
            safe = tokens.proxy_wallet_addr
            trading = tokens.is_enabled_trading
        except Exception as exc:  # noqa: BLE001 — CLI reports error
            print(f"ERROR: login failed: {exc}")
            return 1
        transport = JwtTransport(auth)
    elif settings.private_key:
        mode = "clob"
        eoa = private_to_address(settings.private_key)
        auth = ClobAuth(
            eoa_address=eoa,
            private_key=settings.private_key,
            chain_id=settings.chain_id,
        )
        try:
            auth.sync_clock()
            auth.ensure_credentials(nonce=0)
            status = transport.account_status() if (transport := ClobTransport(auth)) else {}
            safe = status.get("proxy_wallet_address", "unknown")
            trading = bool(status.get("is_enabled_trading", False))
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR: CLOB setup failed: {exc}")
            return 1
    else:
        mode = "read-only"
        print("Mode: READ-ONLY (no PRED_API_KEY and no PRIVATE_KEY)")
        print("Paper mode only; no PRED write access.")
        return 0

    print(f"Mode: {mode.upper()}")
    print(f"EOA: {eoa}")
    print(f"Safe: {safe}")
    print(f"Trading enabled: {trading}")
    print(f"Live trading: {settings.live_trading}")
    if settings.base_rpc_url:
        print("Base RPC: configured")
    return 0


if __name__ == "__main__":
    sys.exit(main())
