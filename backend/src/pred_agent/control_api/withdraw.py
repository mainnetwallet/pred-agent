"""Fund withdrawal — the ONE chat action that always needs a human confirmation.

Flow
----
1. Chat calls ``request_withdraw`` -> :meth:`WithdrawService.create` stores an *intent*
   (amount, recipient, expiry) and returns a confirmation card. Nothing moves.
2. The human confirms through the client (card button or ``/confirm-withdraw <id>``), which
   hits ``POST /v1/withdrawals/{id}/confirm``. In LIVE that endpoint requires ``X-Confirm``.
   The LLM has no tool that can reach step 2.
3. :meth:`WithdrawService.execute` sends USDC from the PRED Safe (trading account) to the
   recipient with a direct Safe ``execTransaction`` on Base, signed by the owner EOA
   (``PRIVATE_KEY``). The EOA pays gas in ETH. Works in CLOB and JWT mode (PRED's own
   cashflow relay is JWT-only and its request shape is not verified here).

Guards: amount > 0 and <= known balance, EIP-55-style address shape, default recipient is the
owner EOA, intents expire (``withdraw_confirm_ttl_seconds``), one execution per intent, and a
failed/uncertain send is reported as such and never re-sent automatically.

NOT verified against mainnet from CI: do a $1 withdrawal first.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Any

import httpx
from eth_account import Account
from eth_utils.address import to_checksum_address

from pred_agent.signer.eip712 import (
    private_to_address,
    safe_tx_digest,
    sign_safe_tx_digest,
)

log = logging.getLogger(__name__)

_ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
ZERO = "0x0000000000000000000000000000000000000000"
SEL_TRANSFER = "a9059cbb"        # transfer(address,uint256)
SEL_SAFE_NONCE = "affed0e0"      # nonce()
SEL_EXEC = "6a761202"            # execTransaction(...)
USDC_DECIMALS = 6


class WithdrawError(Exception):
    pass


@dataclass
class WithdrawIntent:
    id: str
    amount_usd: Decimal
    recipient: str
    safe_address: str | None
    created_at: datetime
    expires_at: datetime
    status: str = "PENDING"        # PENDING | EXECUTING | DONE | FAILED | EXPIRED | CANCELLED
    tx_hash: str | None = None
    note: str | None = None
    via: str = "chat"
    _lock_taken: bool = field(default=False, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "amount_usd": str(self.amount_usd), "recipient": self.recipient,
            "safe_address": self.safe_address, "status": self.status, "tx_hash": self.tx_hash,
            "note": self.note, "expires_at": self.expires_at.isoformat(),
            "created_at": self.created_at.isoformat(),
        }


def _pad32(hex_no_prefix: str) -> str:
    return hex_no_prefix.rjust(64, "0")


def usdc_transfer_calldata(recipient: str, amount_usd: Decimal) -> str:
    units = int((amount_usd * (10 ** USDC_DECIMALS)).to_integral_value(ROUND_DOWN))
    return "0x" + SEL_TRANSFER + _pad32(recipient[2:].lower()) + _pad32(format(units, "x"))


def _rpc(client: httpx.Client, url: str, method: str, params: list[Any]) -> Any:
    resp = client.post(url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    resp.raise_for_status()
    body = resp.json()
    if "error" in body:
        raise WithdrawError(f"rpc {method} error: {str(body['error'])[:200]}")
    return body["result"]


class WithdrawService:
    def __init__(
        self, *, settings: Any, safe_address_provider: Any = None,
        balance_provider: Any = None, http: httpx.Client | None = None,
    ) -> None:
        self.settings = settings
        self._safe_address = safe_address_provider   # () -> str | None
        self._balance = balance_provider             # () -> str | None (decimal string)
        self._http = http
        self._intents: dict[str, WithdrawIntent] = {}

    # ── intents ───────────────────────────────────────────────

    def owner_address(self) -> str | None:
        key = getattr(self.settings, "private_key", None)
        return private_to_address(key) if key else None

    def create(
        self, *, amount_usd: Any, recipient: str | None = None, via: str = "chat",
    ) -> tuple[WithdrawIntent | None, str | None]:
        try:
            amount = Decimal(str(amount_usd)).quantize(Decimal("0.000001"), rounding=ROUND_DOWN)
        except (InvalidOperation, ValueError):
            return None, "amount_usd must be a number"
        if not amount.is_finite() or amount <= 0:
            return None, "amount_usd must be > 0"
        to = (recipient or self.owner_address() or "").strip()
        if not _ADDR_RE.match(to) or to.lower() == ZERO:
            return None, "recipient must be a 0x address (default is your owner wallet)"
        bal_raw = self._balance() if self._balance else None
        if bal_raw is not None:
            try:
                if amount > Decimal(str(bal_raw)):
                    return None, f"amount exceeds available USDC balance ({bal_raw})"
            except InvalidOperation:
                pass
        now = datetime.now(timezone.utc)
        ttl = int(getattr(self.settings, "withdraw_confirm_ttl_seconds", 300))
        intent = WithdrawIntent(
            id=uuid.uuid4().hex[:12], amount_usd=amount, recipient=to,
            safe_address=self._safe_address() if self._safe_address else None,
            created_at=now, expires_at=now + timedelta(seconds=ttl), via=via,
        )
        self._intents[intent.id] = intent
        return intent, None

    def get(self, ref: str) -> WithdrawIntent | None:
        ref = ref.strip().lower()
        if ref in self._intents:
            return self._intents[ref]
        hits = [i for k, i in self._intents.items() if k.startswith(ref)]
        return hits[0] if len(hits) == 1 else None

    def list(self) -> list[WithdrawIntent]:
        self._expire()
        return sorted(self._intents.values(), key=lambda i: i.created_at, reverse=True)

    def cancel(self, ref: str) -> WithdrawIntent | None:
        intent = self.get(ref)
        if intent is None or intent.status != "PENDING":
            return None
        intent.status = "CANCELLED"
        return intent

    def _expire(self) -> None:
        now = datetime.now(timezone.utc)
        for i in self._intents.values():
            if i.status == "PENDING" and now > i.expires_at:
                i.status = "EXPIRED"

    # ── execution (called only after the human confirm) ───────

    def execute(  # noqa: PLR0911
        self, ref: str, *, live: bool,
    ) -> tuple[WithdrawIntent | None, str | None]:
        """Run a confirmed intent. PAPER never moves money. Returns (intent, error)."""
        self._expire()
        intent = self.get(ref)
        if intent is None:
            return None, "withdrawal not found"
        if intent.status != "PENDING":
            return intent, f"withdrawal is already {intent.status}"
        if intent._lock_taken:   # noqa: SLF001 - single-shot guard
            return intent, "withdrawal already executing"
        intent._lock_taken = True   # noqa: SLF001
        intent.status = "EXECUTING"
        if not live:
            intent.status = "DONE"
            intent.note = "PAPER mode: simulated, no funds moved"
            return intent, None
        try:
            intent.tx_hash = self._send_onchain(intent)
            intent.status = "DONE"
            intent.note = "USDC sent from the PRED Safe; confirmed on Base"
            return intent, None
        except WithdrawError as exc:
            intent.status = "FAILED"
            intent.note = str(exc)[:300]
            return intent, intent.note
        except Exception as exc:  # noqa: BLE001
            # Outcome uncertain (timeout etc.): never auto-resend. The user must check the chain.
            intent.status = "FAILED"
            intent.note = (f"{type(exc).__name__}: outcome uncertain - check the transaction "
                           f"{intent.tx_hash or '(no hash)'} / Safe balance before retrying")
            log.error("withdraw %s outcome uncertain", intent.id, exc_info=True)
            return intent, intent.note

    def _send_onchain(self, intent: WithdrawIntent) -> str:  # noqa: PLR0915
        s = self.settings
        if not s.private_key:
            raise WithdrawError("PRIVATE_KEY is not configured")
        rpc_url = getattr(s, "base_rpc_url", None)
        if not rpc_url:
            raise WithdrawError("BASE_RPC_URL is not configured (needed to send the transaction)")
        safe = intent.safe_address or (self._safe_address() if self._safe_address else None)
        if not safe or not _ADDR_RE.match(safe):
            raise WithdrawError("PRED Safe (trading account) address unknown; arm LIVE first")
        usdc = s.usdc_address
        data = usdc_transfer_calldata(intent.recipient, intent.amount_usd)
        own = self.http_client()
        owner = private_to_address(s.private_key)
        chain_id = int(s.chain_id)

        nonce_hex = _rpc(own, rpc_url, "eth_call", [{"to": safe, "data": "0x" + SEL_SAFE_NONCE}, "latest"])
        safe_nonce = int(nonce_hex, 16)
        tx = {
            "to": usdc, "value": 0, "data": data, "operation": 0, "safeTxGas": 0,
            "baseGas": 0, "gasPrice": 0, "gasToken": ZERO, "refundReceiver": ZERO,
            "nonce": safe_nonce,
        }
        digest = safe_tx_digest(chain_id, safe, tx)
        sig = sign_safe_tx_digest(digest, s.private_key)
        sig_bytes = bytes.fromhex(sig[2:])
        if len(sig_bytes) != 65:
            raise WithdrawError("unexpected signature length")

        calldata = self._encode_exec(usdc, data, sig_bytes)
        call = {"from": owner, "to": safe, "data": calldata}
        gas_est = int(_rpc(own, rpc_url, "eth_estimateGas", [call]), 16)   # reverts here = no send
        gas_price = int(_rpc(own, rpc_url, "eth_gasPrice", []), 16)
        priority = min(gas_price, 1_000_000)
        tx_nonce = int(_rpc(own, rpc_url, "eth_getTransactionCount", [owner, "pending"]), 16)
        eth_balance = int(_rpc(own, rpc_url, "eth_getBalance", [owner, "latest"]), 16)
        gas_limit = int(gas_est * 1.3) + 20_000
        max_fee = gas_price * 2
        if eth_balance < gas_limit * max_fee:
            raise WithdrawError(f"owner wallet {owner} has too little ETH on Base for gas")
        signed = Account.sign_transaction({
            "chainId": chain_id, "nonce": tx_nonce, "to": to_checksum_address(safe), "value": 0, "data": calldata,
            "gas": gas_limit, "maxFeePerGas": max_fee, "maxPriorityFeePerGas": priority, "type": 2,
        }, s.private_key)
        raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
        tx_hash = _rpc(own, rpc_url, "eth_sendRawTransaction", ["0x" + bytes(raw).hex()])
        intent.tx_hash = tx_hash
        deadline = time.time() + 90
        while time.time() < deadline:
            rec = _rpc(own, rpc_url, "eth_getTransactionReceipt", [tx_hash])
            if rec:
                if int(rec.get("status", "0x0"), 16) != 1:
                    raise WithdrawError(f"transaction {tx_hash} reverted on chain")
                return str(tx_hash)
            time.sleep(2)
        raise RuntimeError(f"no receipt for {tx_hash} within 90s")

    def http_client(self) -> httpx.Client:
        return self._http or httpx.Client(timeout=20)

    @staticmethod
    def _encode_exec(to: str, data_hex: str, signatures: bytes) -> str:
        """ABI-encode Safe.execTransaction(to,value=0,data,op=0,0,0,0,0x0,0x0,signatures)."""
        data = bytes.fromhex(data_hex[2:])

        def word(n: int) -> bytes:
            return n.to_bytes(32, "big")

        def padded(b: bytes) -> bytes:
            return word(len(b)) + b + b"\x00" * ((32 - len(b) % 32) % 32)

        head_len = 10 * 32
        data_part = padded(data)
        head = (
            bytes.fromhex(_pad32(to[2:].lower())) + word(0) + word(head_len) + word(0)
            + word(0) + word(0) + word(0)
            + bytes.fromhex(_pad32(ZERO[2:])) + bytes.fromhex(_pad32(ZERO[2:]))
            + word(head_len + len(data_part))
        )
        return "0x" + SEL_EXEC + (head + data_part + padded(signatures)).hex()
