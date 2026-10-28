"""Pure helpers for the Safe approval flow (no I/O except the optional JSON-RPC check).

Grounded in the official "Trading Enablement" docs:
  * prepare returns ``{"data": {"success", "message", "data": <SafeTransactionData>}}``
  * the SafeTx digest is EIP-712 with domain ``EIP712Domain(uint256 chainId,address verifyingContract)``
  * ``transactionHash`` must be recomputed and compared before signing
  * the envelope target is usually MultiSend, so the inner approve / setApprovalForAll calls
    must be decoded and shown to the user
  * approvals must be confirmed on chain (allowance / isApprovedForAll) after execution
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from eth_utils import to_checksum_address

from ..signer.eip712 import safe_tx_digest

MAX_UINT256 = 2**256 - 1
EXPECTED_CHAIN_ID = 8453

SEL_MULTISEND = "8d80ff0a"  # multiSend(bytes)
SEL_APPROVE = "095ea7b3"  # approve(address,uint256)
SEL_SET_APPROVAL_FOR_ALL = "a22cb465"  # setApprovalForAll(address,bool)
SEL_ALLOWANCE = "dd62ed3e"  # allowance(address,address)
SEL_IS_APPROVED_FOR_ALL = "e985e9c5"  # isApprovedForAll(address,address)


class EnablementError(Exception):
    """Raised when a prepared transaction fails a safety check."""


@dataclass(frozen=True)
class Call:
    operation: int
    to: str
    value: int
    data: bytes

    def describe(self) -> "ApprovalCall":
        sel = self.data[:4].hex()
        body = self.data[4:]
        if sel == SEL_APPROVE and len(body) == 64:
            spender = _addr(body[0:32])
            amount = int.from_bytes(body[32:64], "big")
            return ApprovalCall("erc20_approve", self.to, spender, amount, True)
        if sel == SEL_SET_APPROVAL_FOR_ALL and len(body) == 64:
            operator = _addr(body[0:32])
            approved = int.from_bytes(body[32:64], "big") != 0
            return ApprovalCall("erc1155_set_approval_for_all", self.to, operator, 1 if approved else 0, True)
        return ApprovalCall("unknown", self.to, "", 0, False)


@dataclass(frozen=True)
class ApprovalCall:
    kind: str  # erc20_approve | erc1155_set_approval_for_all | unknown
    token: str  # contract the call is made on
    target: str  # spender / operator
    amount: int  # erc20 amount, or 1/0 for approved flag
    recognized: bool

    @property
    def unlimited(self) -> bool:
        return self.kind == "erc20_approve" and self.amount >= MAX_UINT256 // 2

    def summary(self) -> str:
        if self.kind == "erc20_approve":
            amt = "UNLIMITED (uint256 max)" if self.unlimited else str(self.amount)
            return f"ERC20 approve   token={self.token} spender={self.target} amount={amt}"
        if self.kind == "erc1155_set_approval_for_all":
            return (f"ERC1155 setApprovalForAll token={self.token} operator={self.target} "
                    f"approved={bool(self.amount)}")
        return f"UNRECOGNIZED call to {self.token}"


def _addr(word: bytes) -> str:
    if any(word[:12]):
        raise EnablementError("malformed address word in call data (non-zero padding)")
    address: str = to_checksum_address(word[12:])
    return address


def _hex_bytes(value: str) -> bytes:
    v = value[2:] if value.startswith(("0x", "0X")) else value
    try:
        return bytes.fromhex(v)
    except ValueError as exc:
        raise EnablementError(f"invalid hex string: {value[:20]}...") from exc


def extract_prepared(response: dict[str, Any]) -> dict[str, Any]:
    """Return the SafeTransactionData object from a prepare response.

    Docs: there is no top-level success field; check ``data.success`` before using ``data.data``.
    A transport may already unwrap one level, so accept both shapes.
    """
    node: Any = response
    for _ in range(3):
        if not isinstance(node, dict):
            break
        if "transactionHash" in node and "safeAddress" in node:
            return node
        if node.get("success") is False:
            raise EnablementError(f"prepare failed: {node.get('message') or node.get('error')}")
        node = node.get("data")
    raise EnablementError("prepare response did not contain a prepared Safe transaction")


def _as_int(value: Any) -> int:
    # Integer strings must be parsed with arbitrary precision (docs).
    return int(value) if not isinstance(value, int) else value


def verify_prepared(prepared: dict[str, Any], *, expected_safe: str | None = None,
                    expected_chain_id: int = EXPECTED_CHAIN_ID) -> bytes:
    """Check chainId / safeAddress and that the recomputed digest equals transactionHash.

    Returns the verified digest. Raises EnablementError on any mismatch.
    """
    chain_id = _as_int(prepared["chainId"])
    if chain_id != expected_chain_id:
        raise EnablementError(f"chainId {chain_id} != expected {expected_chain_id}")
    safe = prepared["safeAddress"]
    if expected_safe and safe.lower() != expected_safe.lower():
        raise EnablementError(f"safeAddress {safe} does not match expected Safe {expected_safe}")

    message = {
        "to": prepared["to"],
        "value": _as_int(prepared["value"]),
        "data": _hex_bytes(prepared["data"]),
        "operation": _as_int(prepared["operation"]),
        "safeTxGas": _as_int(prepared["safeTxGas"]),
        "baseGas": _as_int(prepared["baseGas"]),
        "gasPrice": _as_int(prepared["gasPrice"]),
        "gasToken": prepared["gasToken"],
        "refundReceiver": prepared["refundReceiver"],
        "nonce": _as_int(prepared["nonce"]),
    }
    digest = safe_tx_digest(chain_id, safe, message)
    claimed = _hex_bytes(prepared["transactionHash"])
    if digest != claimed:
        raise EnablementError(
            f"digest mismatch: recomputed 0x{digest.hex()} != transactionHash {prepared['transactionHash']}")
    return digest


def decode_multisend(packed: bytes) -> list[Call]:
    """Decode the packed ``transactions`` argument of MultiSend.

    Each entry: operation(1) | to(20) | value(32) | dataLength(32) | data(dataLength).
    """
    calls: list[Call] = []
    i = 0
    while i < len(packed):
        if i + 85 > len(packed):
            raise EnablementError("truncated MultiSend entry")
        op = packed[i]
        to = to_checksum_address(packed[i + 1:i + 21])
        value = int.from_bytes(packed[i + 21:i + 53], "big")
        length = int.from_bytes(packed[i + 53:i + 85], "big")
        data = packed[i + 85:i + 85 + length]
        if len(data) != length:
            raise EnablementError("MultiSend data length exceeds payload")
        calls.append(Call(op, to, value, data))
        i += 85 + length
    return calls


def decode_calls(prepared: dict[str, Any]) -> list[Call]:
    """Return the underlying calls of a prepared Safe transaction.

    If the envelope is a MultiSend call, its inner calls are returned; otherwise the envelope
    itself is the single call.
    """
    data = _hex_bytes(prepared["data"])
    if data[:4].hex() == SEL_MULTISEND:
        # ABI: selector | offset(32) | length(32) | bytes (padded)
        if len(data) < 4 + 64:
            raise EnablementError("truncated multiSend call data")
        offset = int.from_bytes(data[4:36], "big")
        start = 4 + offset
        length = int.from_bytes(data[start:start + 32], "big")
        packed = data[start + 32:start + 32 + length]
        if len(packed) != length:
            raise EnablementError("multiSend bytes length exceeds call data")
        return decode_multisend(packed)
    return [Call(_as_int(prepared["operation"]), to_checksum_address(prepared["to"]),
                 _as_int(prepared["value"]), data)]


def describe_calls(calls: list[Call]) -> list[ApprovalCall]:
    return [c.describe() for c in calls]


def check_calls_safe(calls: list[Call]) -> list[ApprovalCall]:
    """Describe the calls and reject anything that is not a plain approval.

    A prepared transaction that moves value, delegatecalls, or calls an unrecognized
    function must never be signed by this tool.
    """
    described = describe_calls(calls)
    for call, desc in zip(calls, described, strict=True):
        if call.value != 0:
            raise EnablementError(f"call to {call.to} transfers native value {call.value}")
        if call.operation != 0:
            raise EnablementError(f"call to {call.to} uses operation {call.operation} (delegatecall)")
        if not desc.recognized:
            raise EnablementError(f"unrecognized call to {call.to}: selector 0x{call.data[:4].hex()}")
    return described


# ---------------------------------------------------------------------------
# On-chain verification (read-only JSON-RPC eth_call)
# ---------------------------------------------------------------------------

def _eth_call(rpc_url: str, to: str, data: str, *, client: httpx.Client | None = None) -> bytes:
    payload = {"jsonrpc": "2.0", "id": 1, "method": "eth_call",
               "params": [{"to": to, "data": data}, "latest"]}
    own = client is None
    c = client or httpx.Client(timeout=15.0)
    try:
        resp = c.post(rpc_url, json=payload)
        resp.raise_for_status()
        body = resp.json()
    finally:
        if own:
            c.close()
    if "error" in body:
        raise EnablementError(f"RPC error: {body['error']}")
    return _hex_bytes(body["result"])


def _pad_addr(address: str) -> str:
    return address.lower().removeprefix("0x").rjust(64, "0")


def verify_onchain(rpc_url: str, safe_address: str, approvals: list[ApprovalCall], *,
                   client: httpx.Client | None = None) -> list[tuple[ApprovalCall, bool]]:
    """Check each approval is reflected on chain. Returns (approval, ok) pairs."""
    results: list[tuple[ApprovalCall, bool]] = []
    for ap in approvals:
        if ap.kind == "erc20_approve":
            data = "0x" + SEL_ALLOWANCE + _pad_addr(safe_address) + _pad_addr(ap.target)
            allowance = int.from_bytes(_eth_call(rpc_url, ap.token, data, client=client)[-32:] or b"\0", "big")
            results.append((ap, allowance >= ap.amount))
        elif ap.kind == "erc1155_set_approval_for_all":
            data = "0x" + SEL_IS_APPROVED_FOR_ALL + _pad_addr(safe_address) + _pad_addr(ap.target)
            approved = int.from_bytes(_eth_call(rpc_url, ap.token, data, client=client)[-32:] or b"\0", "big") == 1
            results.append((ap, approved == bool(ap.amount)))
        else:
            results.append((ap, False))
    return results
