"""enablement CLI — one-time Safe approval flow.

Grounded in kit/02_PRED_API_SPEC.md section 5 and live docs (Trading Enablement).

Steps:
  prepare -> verify chainId/safeAddress -> recompute SafeTx digest -> decode MultiSend
  -> print every approve/setApprovalForAll (flag unlimited) -> require typed confirmation
  -> sign raw digest -> execute -> verify on-chain (allowance / isApprovedForAll)
  -> re-login (JWT) or poll account-status (CLOB)

This is a manual, interactive CLI step — never automated.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from ..auth.clob_auth import ClobAuth
from ..auth.jwt_auth import JwtAuth
from ..config import load_settings
from ..signer.eip712 import (
    private_to_address,
)
from ..transport.clob_transport import ClobTransport
from ..transport.jwt_transport import JwtTransport
from ..util.redaction import redact


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="PRED Safe approval enablement CLI")
    p.add_argument("--prepare", action="store_true", help="Prepare Safe approval transaction")
    p.add_argument("--verify", action="store_true", help="Verify prepared SafeTx digest")
    p.add_argument("--decode", action="store_true", help="Decode MultiSend approval calls")
    p.add_argument("--confirm", action="store_true", help="Require typed confirmation before signing")
    p.add_argument("--sign", action="store_true", help="Sign the SafeTx digest")
    p.add_argument("--execute", action="store_true", help="Execute the Safe approval")
    p.add_argument("--verify-chain", action="store_true", help="Verify on-chain approvals over RPC")
    p.add_argument("--all", action="store_true", help="Run full interactive flow (prepare..verify-chain)")
    p.add_argument("--proxy-creation-signature", type=str, default=None,
                   help="Optional CreateProxy signature for account without Safe")
    p.add_argument("--rpc-url", type=str, default=None, help="Base RPC URL for on-chain verification")
    return p.parse_args()


def _load_transport():
    settings = load_settings()
    if settings.pred_api_key:
        auth = JwtAuth(
            api_key=settings.pred_api_key,
            private_key=settings.private_key or "",
            eoa_address=private_to_address(settings.private_key) if settings.private_key else "0x",
            chain_id=settings.chain_id,
            create_proxy_factory=settings.create_proxy_factory,
            login_message=settings.auth_login_message,
        )
        transport = JwtTransport(auth)
    else:
        auth = ClobAuth(
            eoa_address=private_to_address(settings.private_key) if settings.private_key else "0x",
            private_key=settings.private_key or "",
            chain_id=settings.chain_id,
            aggregator_api_key=None,
        )
        transport = ClobTransport(auth)
    return settings, transport


def _print_redacted(label: str, data: Any) -> None:
    print(f"{label}: {redact(json.dumps(data, indent=2))}")


def main() -> int:
    args = _parse_args()
    settings, transport = _load_transport()

    if args.all:
        args.prepare = args.verify = args.decode = args.confirm = args.sign = args.execute = args.verify_chain = True

    if args.prepare:
        result = transport.safe_approval_prepare(args.proxy_creation_signature)
        _print_redacted("prepare result", result)
        data = result.get("data", result)
        print(f"  safeAddress: {data.get('safeAddress')}")
        print(f"  chainId: {data.get('chainId')}")
        print(f"  transactionHash: {data.get('transactionHash')}")

    if args.verify:
        # Recompute and compare digest — placeholder; full implementation in Phase 1
        print("  SafeTx digest verification: TODO (compare recompute vs transactionHash)")

    if args.decode:
        print("  MultiSend decode: TODO (decode ERC20 approve / ERC1155 setApprovalForAll)")

    if args.confirm:
        confirmation = input("Type CONFIRM to proceed with signing: ").strip()
        if confirmation != "CONFIRM":
            print("Aborted: confirmation did not match 'CONFIRM'")
            return 1
        print("  Confirmation accepted.")

    if args.sign:
        print("  SafeTx signing: TODO (requires prepared data from --prepare)")

    if args.execute:
        print("  SafeTx execution: TODO (requires signature from --sign)")

    if args.verify_chain:
        rpc = args.rpc_url or settings.base_rpc_url
        if not rpc:
            print("  Skipping on-chain verification: no BASE_RPC_URL configured")
        else:
            print(f"  On-chain verification against {redact(rpc)}: TODO")

    print("Enablement CLI: placeholder commands ready; full flow wired in Phase 1.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
