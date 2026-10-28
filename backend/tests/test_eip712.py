"""Unit tests for EIP-712 signing.

Grounded in kit/02_PRED_API_SPEC.md sections 3, 4, 5, 8 and live docs.
Tests use fixed vectors and verify: canonical 0x<r><v> (132 hex), low-s, v in {27,28}.
"""

from __future__ import annotations

from pred_agent.signer.eip712 import (
    CURVE_HALF,
    is_low_s,
    private_to_address,
    safe_tx_digest,
    sign_clob_auth,
    sign_create_proxy,
    sign_order,
    sign_safe_tx,
    signature_v,
)

FIXED_PRIVATE_KEY = "0x" + "01" * 32
FIXED_PRIVATE_KEY2 = "0x" + "02" * 32
CHAIN_ID = 8453
CREATE_PROXY_FACTORY = "0xA077e7aaBF2e3c0155F83789e95f46602D03F7f0"


# ---------------------------------------------------------------------------
# Signature format checks
# ---------------------------------------------------------------------------

def _assert_valid_signature(sig: str) -> None:
    assert sig.startswith("0x")
    assert len(sig) == 132, f"signature length {len(sig)}, expected 132"
    assert is_low_s(sig), "signature s must be <= curveOrderHalf"
    v = signature_v(sig)
    assert v in (27, 28), f"v must be 27 or 28, got {v}"


def test_sign_create_proxy_format():
    sig = sign_create_proxy(CHAIN_ID, CREATE_PROXY_FACTORY, FIXED_PRIVATE_KEY)
    _assert_valid_signature(sig)
    # CreateProxy signs constants — same input must give same output
    sig2 = sign_create_proxy(CHAIN_ID, CREATE_PROXY_FACTORY, FIXED_PRIVATE_KEY)
    assert sig == sig2


def test_sign_create_proxy_diff_key_diff_sig():
    sig1 = sign_create_proxy(CHAIN_ID, CREATE_PROXY_FACTORY, FIXED_PRIVATE_KEY)
    sig2 = sign_create_proxy(CHAIN_ID, CREATE_PROXY_FACTORY, FIXED_PRIVATE_KEY2)
    assert sig1 != sig2


def test_private_to_address():
    addr = private_to_address(FIXED_PRIVATE_KEY)
    assert addr.startswith("0x")
    assert len(addr) == 42


# ---------------------------------------------------------------------------
# ClobAuth
# ---------------------------------------------------------------------------

def test_sign_clob_auth_format():
    sig = sign_clob_auth(
        CHAIN_ID, private_to_address(FIXED_PRIVATE_KEY), "1700000000", 0, FIXED_PRIVATE_KEY
    )
    _assert_valid_signature(sig)


def test_sign_clob_auth_timestamp_changes_sig():
    sig1 = sign_clob_auth(CHAIN_ID, private_to_address(FIXED_PRIVATE_KEY), "1700000000", 0, FIXED_PRIVATE_KEY)
    sig2 = sign_clob_auth(CHAIN_ID, private_to_address(FIXED_PRIVATE_KEY), "1700000001", 0, FIXED_PRIVATE_KEY)
    assert sig1 != sig2


# ---------------------------------------------------------------------------
# Order signing
# ---------------------------------------------------------------------------

ORDER_CONTRACT = "0x1234567890123456789012345678901234567890"
QUESTION_ID = "0x" + "ab" * 32


def test_sign_order_format():
    sig = sign_order(
        CHAIN_ID, ORDER_CONTRACT,
        salt=12345,
        maker=private_to_address(FIXED_PRIVATE_KEY),
        signer=private_to_address(FIXED_PRIVATE_KEY),
        price=550000,       # 55 cents
        quantity=100_000_000,  # 100 shares
        expiration=0,
        nonce=0,
        question_id=QUESTION_ID,
        fee_rate_bps=125,   # target 250 at 50c
        intent=0,           # long
        signature_type=2,
        private_key=FIXED_PRIVATE_KEY,
    )
    _assert_valid_signature(sig)


def test_sign_order_price_scaling():
    """55 cents -> 550000 in EIP-712 (NOT 55 * 1e6 = 55000000)."""
    # Just verify the price value we pass is correct; signing doesn't re-scale
    assert 55 * 10_000 == 550_000


# ---------------------------------------------------------------------------
# SafeTx digest
# ---------------------------------------------------------------------------

def test_safe_tx_digest():
    safe_tx_data = {
        "to": "0x" + "11" * 20,
        "value": 0,
        "data": "0x",
        "operation": 0,
        "safeTxGas": 100000,
        "baseGas": 0,
        "gasPrice": 0,
        "gasToken": "0x0000000000000000000000000000000000000000",
        "refundReceiver": "0x" + "22" * 20,
        "nonce": 1,
    }
    digest = safe_tx_digest(CHAIN_ID, private_to_address(FIXED_PRIVATE_KEY), safe_tx_data)
    assert len(digest) == 32
    sig = sign_safe_tx(CHAIN_ID, private_to_address(FIXED_PRIVATE_KEY), safe_tx_data, FIXED_PRIVATE_KEY)
    _assert_valid_signature(sig)


# ---------------------------------------------------------------------------
# Curve half sanity
# ---------------------------------------------------------------------------

def test_curve_half():
    assert CURVE_HALF == 0x7FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF5D576E7357A4501DDFE92F46681B20A0


# ---------------------------------------------------------------------------
# Reference check: digests/signatures must equal the standard EIP-712 encoding
# (eth_account is an independent implementation) and recover to the signer.
# ---------------------------------------------------------------------------

from eth_account import Account  # noqa: E402
from eth_account.messages import encode_typed_data  # noqa: E402

from pred_agent.signer import eip712 as _e  # noqa: E402

_ADDR = "0x" + "ab" * 20
_ZERO = "0x" + "00" * 20


def _recovers(domain, types, primary, message, sig) -> bool:
    signable = encode_typed_data(
        full_message={"types": types, "primaryType": primary, "domain": domain, "message": message}
    )
    return bool(Account.recover_message(signable, signature=sig) == private_to_address(FIXED_PRIVATE_KEY))


def test_clob_auth_matches_reference_eip712():
    me = private_to_address(FIXED_PRIVATE_KEY)
    sig = sign_clob_auth(CHAIN_ID, me, "1700000000", 0, FIXED_PRIVATE_KEY)
    domain = {"name": "ClobAuthDomain", "version": "1", "chainId": CHAIN_ID}
    msg = {"address": me, "timestamp": "1700000000", "nonce": 0,
           "message": "This message attests that I control the given wallet"}
    assert _recovers(domain, _e.CLOB_AUTH_TYPES, "ClobAuth", msg, sig)


def test_create_proxy_matches_reference_eip712():
    sig = sign_create_proxy(CHAIN_ID, CREATE_PROXY_FACTORY, FIXED_PRIVATE_KEY)
    domain = {"name": "Pred Contract Proxy Factory", "chainId": CHAIN_ID,
              "verifyingContract": CREATE_PROXY_FACTORY}
    msg = {"paymentToken": _ZERO, "payment": 0, "paymentReceiver": _ZERO}
    assert _recovers(domain, _e.CREATE_PROXY_TYPES, "CreateProxy", msg, sig)


def test_order_matches_reference_eip712():
    me = private_to_address(FIXED_PRIVATE_KEY)
    qid = "0x" + "cd" * 32
    sig = sign_order(CHAIN_ID, _ADDR, 12345, _ADDR, me, 550_000, 100_000_000, 0, 0, qid, 170, 0, 2,
                     FIXED_PRIVATE_KEY)
    domain = {"name": "Pred CTF Exchange", "version": "1", "chainId": CHAIN_ID, "verifyingContract": _ADDR}
    msg = {"salt": 12345, "maker": _ADDR, "signer": me, "taker": _ZERO, "price": 550_000,
           "quantity": 100_000_000, "expiration": 0, "nonce": 0, "questionId": qid,
           "feeRateBps": 170, "intent": 0, "signatureType": 2}
    assert _recovers(domain, _e.ORDER_TYPES, "Order", msg, sig)


def test_safe_tx_digest_matches_reference_and_signs():
    from eth_utils.crypto import keccak

    safe = "0x" + "11" * 20
    tx = {"to": "0x" + "22" * 20, "value": 0, "data": "0x1234", "operation": 0, "safeTxGas": 0,
          "baseGas": 0, "gasPrice": 0, "gasToken": _ZERO, "refundReceiver": _ZERO, "nonce": 3}
    signable = encode_typed_data(full_message={
        "types": _e.SAFE_TX_TYPES, "primaryType": "SafeTx",
        "domain": {"chainId": CHAIN_ID, "verifyingContract": safe}, "message": tx})
    expected = keccak(b"\x19" + signable.version + signable.header + signable.body)
    assert safe_tx_digest(CHAIN_ID, safe, tx) == expected
    sig = sign_safe_tx(CHAIN_ID, safe, tx, FIXED_PRIVATE_KEY)
    _assert_valid_signature(sig)
    assert _recovers({"chainId": CHAIN_ID, "verifyingContract": safe}, _e.SAFE_TX_TYPES, "SafeTx", tx, sig)
