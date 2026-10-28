"""Safe approval enablement: digest verification, MultiSend decoding, on-chain check, CLI flow."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_utils import to_checksum_address

from pred_agent.config import Settings
from pred_agent.enablement import cli
from pred_agent.enablement.safe import (
    MAX_UINT256,
    EnablementError,
    check_calls_safe,
    decode_calls,
    extract_prepared,
    verify_onchain,
    verify_prepared,
)
from pred_agent.signer.eip712 import is_low_s, safe_tx_digest, sign_safe_tx_digest

KEY = "0x" + "11" * 32
SAFE = to_checksum_address("0x" + "ab" * 20)
USDC = to_checksum_address("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913")
CTF = to_checksum_address("0x" + "cd" * 20)
SPENDER = to_checksum_address("0x" + "ef" * 20)
MULTISEND = to_checksum_address("0x" + "99" * 20)


def _word(addr: str) -> bytes:
    return bytes(12) + bytes.fromhex(addr[2:])


def _entry(to: str, data: bytes, op: int = 0, value: int = 0) -> bytes:
    return (bytes([op]) + bytes.fromhex(to[2:]) + value.to_bytes(32, "big")
            + len(data).to_bytes(32, "big") + data)


def _approve(amount: int) -> bytes:
    return bytes.fromhex("095ea7b3") + _word(SPENDER) + amount.to_bytes(32, "big")


def _set_all() -> bytes:
    return bytes.fromhex("a22cb465") + _word(SPENDER) + (1).to_bytes(32, "big")


def _multisend(entries: list[bytes]) -> bytes:
    packed = b"".join(entries)
    pad = (-len(packed)) % 32
    return (bytes.fromhex("8d80ff0a") + (32).to_bytes(32, "big")
            + len(packed).to_bytes(32, "big") + packed + bytes(pad))


def _prepared(data: bytes, nonce: int = 3) -> dict[str, Any]:
    msg = {"to": MULTISEND, "value": 0, "data": data, "operation": 1, "safeTxGas": 0,
           "baseGas": 0, "gasPrice": 0, "gasToken": "0x" + "00" * 20,
           "refundReceiver": "0x" + "00" * 20, "nonce": nonce}
    digest = safe_tx_digest(8453, SAFE, msg)
    return {"safeAddress": SAFE, "chainId": "8453", "transactionHash": "0x" + digest.hex(),
            "to": MULTISEND, "value": "0", "data": "0x" + data.hex(), "operation": 1,
            "safeTxGas": "0", "baseGas": "0", "gasPrice": "0", "gasToken": msg["gasToken"],
            "refundReceiver": msg["refundReceiver"], "nonce": str(nonce), "extraField": "keep-me"}


def _good() -> dict[str, Any]:
    return _prepared(_multisend([_entry(USDC, _approve(MAX_UINT256)), _entry(CTF, _set_all())]))


def test_digest_matches_independent_eth_account() -> None:
    p = _good()
    typed = {
        "types": {
            "EIP712Domain": [{"name": "chainId", "type": "uint256"},
                             {"name": "verifyingContract", "type": "address"}],
            "SafeTx": [{"name": n, "type": t} for n, t in [
                ("to", "address"), ("value", "uint256"), ("data", "bytes"), ("operation", "uint8"),
                ("safeTxGas", "uint256"), ("baseGas", "uint256"), ("gasPrice", "uint256"),
                ("gasToken", "address"), ("refundReceiver", "address"), ("nonce", "uint256")]],
        },
        "primaryType": "SafeTx",
        "domain": {"chainId": 8453, "verifyingContract": SAFE},
        "message": {"to": p["to"], "value": 0, "data": bytes.fromhex(p["data"][2:]), "operation": 1,
                    "safeTxGas": 0, "baseGas": 0, "gasPrice": 0, "gasToken": p["gasToken"],
                    "refundReceiver": p["refundReceiver"], "nonce": 3},
    }
    signed = Account.sign_message(encode_typed_data(full_message=typed), KEY)
    assert verify_prepared(p) == bytes.fromhex(p["transactionHash"][2:])
    # signing the verified digest raw gives the same signature as signing the typed data
    raw = sign_safe_tx_digest(verify_prepared(p), KEY)
    assert raw.lower() == "0x" + signed.signature.hex().removeprefix("0x").lower()
    assert is_low_s(raw)


def test_verify_rejects_tampered_and_wrong_chain_or_safe() -> None:
    p = _good()
    p["nonce"] = "4"
    with pytest.raises(EnablementError, match="digest mismatch"):
        verify_prepared(p)
    p = _good()
    p["chainId"] = 1
    with pytest.raises(EnablementError, match="chainId"):
        verify_prepared(p)
    with pytest.raises(EnablementError, match="does not match expected Safe"):
        verify_prepared(_good(), expected_safe="0x" + "01" * 20)


def test_decode_multisend_and_flags() -> None:
    approvals = check_calls_safe(decode_calls(_good()))
    assert [a.kind for a in approvals] == ["erc20_approve", "erc1155_set_approval_for_all"]
    assert approvals[0].unlimited and approvals[0].target == SPENDER and approvals[0].token == USDC
    assert approvals[1].target == SPENDER and approvals[1].amount == 1


def test_unsafe_calls_rejected() -> None:
    delegate = _prepared(_multisend([_entry(USDC, _approve(1), op=1)]))
    with pytest.raises(EnablementError, match="delegatecall"):
        check_calls_safe(decode_calls(delegate))
    value = _prepared(_multisend([_entry(USDC, _approve(1), value=5)]))
    with pytest.raises(EnablementError, match="native value"):
        check_calls_safe(decode_calls(value))
    unknown = _prepared(_multisend([_entry(USDC, bytes.fromhex("a9059cbb") + bytes(64))]))
    with pytest.raises(EnablementError, match="unrecognized"):
        check_calls_safe(decode_calls(unknown))


def test_extract_prepared_shapes() -> None:
    p = _good()
    assert extract_prepared({"data": {"success": True, "message": "ok", "data": p}}) is p
    assert extract_prepared(p) is p
    with pytest.raises(EnablementError, match="prepare failed"):
        extract_prepared({"data": {"success": False, "message": "nope", "data": None}})


def _rpc_client(allowance: int, approved: bool) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        call = json.loads(request.content)["params"][0]
        if call["data"].startswith("0xdd62ed3e"):
            val = allowance
        else:
            val = 1 if approved else 0
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": "0x" + val.to_bytes(32, "big").hex()})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_verify_onchain() -> None:
    approvals = check_calls_safe(decode_calls(_good()))
    ok = verify_onchain("http://rpc", SAFE, approvals, client=_rpc_client(MAX_UINT256, True))
    assert all(r for _, r in ok)
    bad = verify_onchain("http://rpc", SAFE, approvals, client=_rpc_client(0, False))
    assert not any(r for _, r in bad)


class _FakeTransport:
    def __init__(self, prepared: dict[str, Any]) -> None:
        self.prepared = prepared
        self.executed: list[tuple[str, dict[str, Any]]] = []

    def safe_approval_prepare(self, proxy_creation_signature: str | None = None) -> dict[str, Any]:
        return {"data": {"success": True, "message": "ok", "data": self.prepared}}

    def safe_approval_execute(self, signature: str, data: dict[str, Any]) -> dict[str, Any]:
        self.executed.append((signature, data))
        return {"data": {"success": True, "message": "ok", "data": None, "is_enabled_trading": True}}


def _patch(monkeypatch: pytest.MonkeyPatch, transport: _FakeTransport, answer: str) -> None:
    settings = Settings(private_key=KEY, base_rpc_url=None)
    monkeypatch.setattr(cli, "_load_transport", lambda: (settings, transport))
    monkeypatch.setattr("builtins.input", lambda _="": answer)


def test_cli_full_flow_executes_with_unmodified_prepared(monkeypatch: pytest.MonkeyPatch,
                                                         tmp_path: Path) -> None:
    t = _FakeTransport(_good())
    _patch(monkeypatch, t, "CONFIRM")
    state = str(tmp_path / "s.json")
    assert cli.main(["--prepare", "--sign", "--execute", "--state-file", state, "--safe-address", SAFE]) == 0
    sig, data = t.executed[0]
    assert data == t.prepared and data["extraField"] == "keep-me"
    assert len(sig) == 132 and is_low_s(sig)
    assert Account.recover_message(
        encode_typed_data(full_message=_typed(t.prepared)), signature=sig) == Account.from_key(KEY).address


def _typed(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "types": {
            "EIP712Domain": [{"name": "chainId", "type": "uint256"},
                             {"name": "verifyingContract", "type": "address"}],
            "SafeTx": [{"name": n, "type": ty} for n, ty in [
                ("to", "address"), ("value", "uint256"), ("data", "bytes"), ("operation", "uint8"),
                ("safeTxGas", "uint256"), ("baseGas", "uint256"), ("gasPrice", "uint256"),
                ("gasToken", "address"), ("refundReceiver", "address"), ("nonce", "uint256")]],
        },
        "primaryType": "SafeTx",
        "domain": {"chainId": 8453, "verifyingContract": p["safeAddress"]},
        "message": {"to": p["to"], "value": 0, "data": bytes.fromhex(p["data"][2:]), "operation": 1,
                    "safeTxGas": 0, "baseGas": 0, "gasPrice": 0, "gasToken": p["gasToken"],
                    "refundReceiver": p["refundReceiver"], "nonce": int(p["nonce"])},
    }


def test_cli_wrong_confirmation_never_signs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    t = _FakeTransport(_good())
    _patch(monkeypatch, t, "yes")
    state = tmp_path / "s.json"
    assert cli.main(["--prepare", "--sign", "--execute", "--state-file", str(state)]) == 1
    assert not t.executed and "signature" not in json.loads(state.read_text())


def test_cli_refuses_tampered_digest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bad = _good()
    bad["nonce"] = "99"
    t = _FakeTransport(bad)
    _patch(monkeypatch, t, "CONFIRM")
    assert cli.main(["--prepare", "--sign", "--execute", "--state-file", str(tmp_path / "s.json")]) == 1
    assert not t.executed


def test_cli_execute_requires_signature(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    t = _FakeTransport(_good())
    _patch(monkeypatch, t, "CONFIRM")
    state = str(tmp_path / "s.json")
    assert cli.main(["--prepare", "--state-file", state]) == 0
    assert cli.main(["--execute", "--state-file", state]) == 1  # no signature yet: refuse
    assert not t.executed
