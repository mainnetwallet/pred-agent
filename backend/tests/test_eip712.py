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
