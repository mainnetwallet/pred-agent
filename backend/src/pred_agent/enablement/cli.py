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
from pathlib import Path
from typing import Any, Protocol

from ..auth.clob_auth import ClobAuth
from ..auth.jwt_auth import JwtAuth
from ..auth.mode import JWT, resolve_auth_mode, warn_if_api_key_ignored
from ..config import Settings, load_settings
from ..signer.eip712 import (
    is_low_s,
    private_to_address,
    sign_safe_tx_digest,
    signature_v,
)
from ..transport.clob_transport import ClobTransport
from ..transport.jwt_transport import JwtTransport
from ..transport.protocol import PredTransport
from ..util.redaction import redact
from .safe import (
    ApprovalCall,
    EnablementError,
    check_calls_safe,
    decode_calls,
    extract_prepared,
    verify_onchain,
    verify_prepared,
)


class _ApprovalTransport(PredTransport, Protocol):
    """PredTransport plus the Safe-approval calls both JWT and CLOB transports implement."""

    def safe_approval_prepare(self, proxy_creation_signature: str | None = None) -> dict[str, Any]: ...

    def safe_approval_execute(self, signature: str, data: dict[str, Any]) -> dict[str, Any]: ...


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
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
    p.add_argument("--state-file", type=str, default=".enablement_state.json",
                   help="Where prepared data / signature are kept between steps (chmod 600)")
    p.add_argument("--safe-address", type=str, default=None,
                   help="Expected Safe (proxy_wallet_addr); digest check fails if prepare returns another")
    return p.parse_args(argv)


def _load_transport() -> tuple[Settings, _ApprovalTransport]:
    """Build the transport for the configured auth mode (PRED_AUTH_MODE, default clob).

    PRED_API_KEY alone never selects JWT. In CLOB mode the L2 credentials are derived/issued here
    for the EXISTING account (never a new one, never with a partner key) — without them no CLOB
    call (including safe-approval) can be signed.
    """
    settings = load_settings()
    transport: _ApprovalTransport
    if resolve_auth_mode(settings) == JWT:
        if not settings.pred_api_key:
            raise EnablementError("PRED_AUTH_MODE=jwt needs PRED_API_KEY")
        jwt_auth = JwtAuth(
            api_key=settings.pred_api_key,
            private_key=settings.private_key or "",
            eoa_address=private_to_address(settings.private_key) if settings.private_key else "0x",
            chain_id=settings.chain_id,
            create_proxy_factory=settings.create_proxy_factory,
            login_message=settings.auth_login_message,
        )
        transport = JwtTransport(jwt_auth)
    else:
        if not settings.private_key:
            raise EnablementError("PRIVATE_KEY not configured")
        warn_if_api_key_ignored(settings)
        clob_auth = ClobAuth(
            eoa_address=private_to_address(settings.private_key),
            private_key=settings.private_key,
            chain_id=settings.chain_id,
            base_url=settings.host,
        )
        clob_auth.ensure_credentials(0)
        transport = ClobTransport(clob_auth, base_url=settings.host)
    return settings, transport


def _print_redacted(label: str, data: Any) -> None:
    print(f"{label}: {redact(json.dumps(data, indent=2))}")


def _load_state(path: Path) -> dict[str, Any]:
    if path.exists():
        loaded: dict[str, Any] = json.loads(path.read_text())
        return loaded
    return {}


def _save_state(path: Path, state: dict[str, Any]) -> None:
    path.write_text(json.dumps(state, indent=2))
    path.chmod(0o600)


def _fail(msg: str) -> int:
    print(f"ERROR: {msg}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:  # noqa: PLR0911
    args = _parse_args(argv)
    try:
        settings, transport = _load_transport()
    except EnablementError as exc:
        return _fail(str(exc))
    except Exception as exc:  # noqa: BLE001 - CLOB/JWT auth failures carry PRED's reason
        from ..execution.live_runtime import describe_auth_error

        return _fail(f"could not authenticate with PRED: {describe_auth_error(exc)}")
    state_path = Path(args.state_file)
    state = _load_state(state_path)

    if args.all:
        args.prepare = args.verify = args.decode = args.confirm = args.sign = args.execute = args.verify_chain = True

    # signing / executing always need the verified + decoded + confirmed path in this process
    if args.sign or args.execute:
        args.verify = args.decode = args.confirm = True

    try:
        if args.prepare:
            result = transport.safe_approval_prepare(args.proxy_creation_signature)
            fresh = extract_prepared(result)
            state = {"prepared": fresh}
            _save_state(state_path, state)
            print(f"  safeAddress: {fresh.get('safeAddress')}")
            print(f"  chainId: {fresh.get('chainId')}")
            print(f"  transactionHash: {fresh.get('transactionHash')}")
            print(f"  prepared data saved to {state_path}")

        prepared: dict[str, Any] | None = state.get("prepared")
        if (args.verify or args.decode or args.confirm or args.sign or args.execute) and not prepared:
            return _fail("no prepared transaction; run --prepare first")

        approvals: list[ApprovalCall] = []
        if args.verify:
            assert prepared is not None
            verify_prepared(prepared, expected_safe=args.safe_address)
            print("  SafeTx digest verified: recomputed hash == transactionHash")

        if args.decode:
            assert prepared is not None
            calls = decode_calls(prepared)
            approvals = check_calls_safe(calls)
            print(f"  Prepared transaction contains {len(approvals)} call(s):")
            for ap in approvals:
                flag = "  !! UNLIMITED" if ap.unlimited else ""
                print(f"    - {ap.summary()}{flag}")
            print("  Confirm every token/spender/operator is appropriate for PRED before signing.")

        if args.confirm:
            confirmation = input("Type CONFIRM to proceed with signing: ").strip()
            if confirmation != "CONFIRM":
                print("Aborted: confirmation did not match 'CONFIRM'")
                return 1
            print("  Confirmation accepted.")

        if args.sign:
            assert prepared is not None
            if not settings.private_key:
                return _fail("PRIVATE_KEY not configured")
            signature: str = sign_safe_tx_digest(verify_prepared(prepared, expected_safe=args.safe_address),
                                            settings.private_key)
            if not is_low_s(signature) or len(signature) != 132 or signature_v(signature) not in (27, 28):
                return _fail("produced signature is not canonical (low-s, v in 27/28)")
            state["signature"] = signature
            _save_state(state_path, state)
            print("  SafeTx signed (raw secp256k1, no EIP-191 prefix).")

        if args.execute:
            assert prepared is not None
            stored_sig: str | None = state.get("signature")
            if not stored_sig:
                return _fail("no signature; run --sign first")
            # Submit the signature and the ENTIRE prepared object, unmodified.
            result = transport.safe_approval_execute(stored_sig, prepared)
            _print_redacted("execute result", result)
            body = result.get("data", result) if isinstance(result, dict) else {}
            if isinstance(body, dict) and body.get("success") is False:
                return _fail(f"execute failed: {body.get('message')}")
            print("  Execute accepted (business success, NOT an on-chain receipt).")
            print("  JWT: log in again for a token with is_enabled_trading=true."
                  " CLOB: check account-status. Changes may take up to 30s.")

        if args.verify_chain:
            rpc = args.rpc_url or settings.base_rpc_url
            if not rpc:
                print("  Skipping on-chain verification: no BASE_RPC_URL configured")
            elif not prepared:
                return _fail("no prepared transaction; run --prepare first")
            else:
                approvals = approvals or check_calls_safe(decode_calls(prepared))
                print(f"  On-chain verification against {redact(rpc)}:")
                ok_all = True
                for ap, ok in verify_onchain(rpc, prepared["safeAddress"], approvals):
                    ok_all = ok_all and ok
                    print(f"    [{'OK' if ok else 'MISSING'}] {ap.summary()}")
                if not ok_all:
                    print("  Some approvals are not yet visible on chain: wait or reconcile with PRED"
                          " before repeating execution.")
                    return 2
    except EnablementError as exc:
        return _fail(str(exc))

    return 0


if __name__ == "__main__":
    sys.exit(main())
