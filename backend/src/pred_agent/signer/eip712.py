"""EIP-712 signing for PRED.

Grounded in kit/02_PRED_API_SPEC.md sections 3, 4, 5, 8 and the live docs
(Authentication, Builder / Aggregator Onboarding, Trading Enablement, Orders).

All signatures: ``0x<r><v>`` (132 hex chars), canonical low-s,
``v`` in {27, 28}.

Only this module reads ``PRIVATE_KEY``.
"""

from __future__ import annotations

from eth_account import Account
from eth_utils import keccak, to_bytes
from hexbytes import HexBytes

# secp256k1 curve order / half (for low-s canonicalization)
CURVE_ORDER = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
CURVE_HALF = CURVE_ORDER // 2

# ---------------------------------------------------------------------------
# EIP-712 type definitions (field order is critical)
# ---------------------------------------------------------------------------

CREATE_PROXY_DOMAIN = {
    "name": "EIP712Domain",
    "type": "EIP712Domain(string name,uint256 chainId,address verifyingContract)",
}
CREATE_PROXY_TYPE = "CreateProxy"
CREATE_PROXY_TYPES = {
    "EIP712Domain": [
        {"name": "name", "type": "string"},
        {"name": "chainId", "type": "uint256"},
        {"name": "verifyingContract", "type": "address"},
    ],
    "CreateProxy": [
        {"name": "paymentToken", "type": "address"},
        {"name": "payment", "type": "uint256"},
        {"name": "paymentReceiver", "type": "address"},
    ],
}

CLOB_AUTH_DOMAIN = {
    "name": "EIP712Domain",
    "type": "EIP712Domain(string name,string version,uint256 chainId)",
}
CLOB_AUTH_TYPE = "ClobAuth"
CLOB_AUTH_TYPES = {
    "EIP712Domain": [
        {"name": "name", "type": "string"},
        {"name": "version", "type": "string"},
        {"name": "chainId", "type": "uint256"},
    ],
    "ClobAuth": [
        {"name": "address", "type": "address"},
        {"name": "timestamp", "type": "string"},
        {"name": "nonce", "type": "uint256"},
        {"name": "message", "type": "string"},
    ],
}

ORDER_DOMAIN = {
    "name": "EIP712Domain",
    "type": "EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)",
}
ORDER_TYPE = "Order"
ORDER_TYPES = {
    "EIP712Domain": [
        {"name": "name", "type": "string"},
        {"name": "version", "type": "string"},
        {"name": "chainId", "type": "uint256"},
        {"name": "verifyingContract", "type": "address"},
    ],
    "Order": [
        {"name": "salt", "type": "uint256"},
        {"name": "maker", "type": "address"},
        {"name": "signer", "type": "address"},
        {"name": "taker", "type": "address"},
        {"name": "price", "type": "uint256"},
        {"name": "quantity", "type": "uint256"},
        {"name": "expiration", "type": "uint256"},
        {"name": "nonce", "type": "uint256"},
        {"name": "questionId", "type": "bytes32"},
        {"name": "feeRateBps", "type": "uint256"},
        {"name": "intent", "type": "uint8"},
        {"name": "signatureType", "type": "uint8"},
    ],
}

SAFE_TX_DOMAIN = {
    "name": "EIP712Domain",
    "type": "EIP712Domain(uint256 chainId,address verifyingContract)",
}
SAFE_TX_TYPE = "SafeTx"
SAFE_TX_TYPES = {
    "EIP712Domain": [
        {"name": "chainId", "type": "uint256"},
        {"name": "verifyingContract", "type": "address"},
    ],
    "SafeTx": [
        {"name": "to", "type": "address"},
        {"name": "value", "type": "uint256"},
        {"name": "data", "type": "bytes"},
        {"name": "operation", "type": "uint8"},
        {"name": "safeTxGas", "type": "uint256"},
        {"name": "baseGas", "type": "uint256"},
        {"name": "gasPrice", "type": "uint256"},
        {"name": "gasToken", "type": "address"},
        {"name": "refundReceiver", "type": "address"},
        {"name": "nonce", "type": "uint256"},
    ],
}


# ---------------------------------------------------------------------------
# EIP-712 helpers
# ---------------------------------------------------------------------------

def _encode_type(primary_type: str, types: dict) -> str:
    """Encode an EIP-712 type string with dependencies sorted alphabetically."""
    deps: list[str] = []
    seen = {primary_type}
    stack = [primary_type]
    while stack:
        t = stack.pop()
        for member in types[t]:
            mtype = member["type"]
            base = mtype[: mtype.index("[")] if "[" in mtype else mtype
            if base not in seen and base in types:
                seen.add(base)
                stack.append(base)
                deps.append(base)
    deps.sort()
    ordered = [primary_type] + deps
    parts = []
    for t in ordered:
        members = types[t]
        params = ",".join(f"{m['type']} {m['name']}" for m in members)
        parts.append(f"{t}({params})")
    return "".join(parts)


def _type_hash(primary_type: str, types: dict) -> bytes:
    return keccak(text=_encode_type(primary_type, types))


def _encode_value(mtype: str, value, types: dict) -> bytes:
    """EIP-712 encoding of a single value (returns 32 bytes)."""
    if mtype == "string":
        return keccak(text=value)
    if mtype == "bytes":
        return keccak(value if isinstance(value, (bytes, bytearray)) else to_bytes(hexstr=value))
    if mtype == "bool":
        return int.to_bytes(1 if value else 0, 32, "big")
    if "[" in mtype:
        # arrays are not used by our types; guard against misuse
        raise ValueError(f"array type not supported in encoder: {mtype}")
    if mtype in types:
        return _hash_struct(mtype, types, value)
    # uint*, int*, address, bytes32
    if isinstance(value, int):
        return int.to_bytes(value, 32, "big")
    return to_bytes(hexstr=value)


def _hash_struct(primary_type: str, types: dict, message: dict) -> bytes:
    """hashStruct = keccak(encodeType(primaryType) ++ encodeValues)."""
    encoded = _type_hash(primary_type, types)
    for member in types[primary_type]:
        encoded += _encode_value(member["type"], message[member["name"]], types)
    return keccak(encoded)


def _hash_domain(domain: dict, types: dict) -> bytes:
    """EIP-712 domain separator."""
    domain_type = types["EIP712Domain"]
    encoded = _type_hash("EIP712Domain", types)
    for member in domain_type:
        encoded += _encode_value(member["type"], domain[member["name"]], types)
    return keccak(encoded)


def _eip712_digest(domain: dict, types: dict, primary_type: str, message: dict) -> bytes:
    """Raw EIP-712 digest = keccak(domain_separator || struct_hash)."""
    domain_separator = _hash_domain(domain, types)
    struct_hash = _hash_struct(primary_type, types, message)
    return keccak(domain_separator + struct_hash)


def _eip712_digest_with_prefix(domain: dict, types: dict, primary_type: str, message: dict) -> bytes:
    """Standard Ethereum signed-message hash = keccak(\\x19\\x01 || raw_digest)."""
    return keccak(b"\x19\x01" + _eip712_digest(domain, types, primary_type, message))


def _canonical_sig(r, s, v: int) -> str:
    """Return 0x<r><v> with canonical low-s and v in {27,28}."""
    if isinstance(r, int):
        r = int.to_bytes(r, 32, "big")
    if isinstance(s, int):
        s = int.to_bytes(s, 32, "big")
    s_int = int.from_bytes(s, "big")
    v_int = int(v)
    if s_int > CURVE_HALF:
        s_int = CURVE_ORDER - s_int
        v_int = 27 if v_int == 28 else 28
    s_bytes = s_int.to_bytes(32, "big")
    return "0x" + r.hex() + s_bytes.hex() + format(v_int, "02x")


def _sign_digest(digest: bytes, private_key: str) -> str:
    """Sign a raw 32-byte digest (no EIP-191 prefix), canonical low-s."""
    signed = Account.unsafe_sign_hash(digest, private_key=private_key)
    return _canonical_sig(signed.r, signed.s, signed.v)


def _sign_typed(domain: dict, types: dict, primary_type: str, message: dict,
                private_key: str) -> str:
    """Sign standard EIP-712 typed data (with \\x19\\x01 prefix)."""
    digest = _eip712_digest_with_prefix(domain, types, primary_type, message)
    return _sign_digest(digest, private_key)


# ---------------------------------------------------------------------------
# Public signing API
# ---------------------------------------------------------------------------

def sign_create_proxy(chain_id: int, verifying_contract: str, private_key: str) -> str:
    """CreateProxy login signature (raw digest, no EIP-191 prefix).

    Domain: EIP712Domain(string name,uint256 chainId,address verifyingContract)
    name: "Pred Contract Proxy Factory"
    Message: CreateProxy(0x0, 0, 0x0) — constants, never changes per EOA.
    """
    domain = {
        "name": "Pred Contract Proxy Factory",
        "chainId": chain_id,
        "verifyingContract": verifying_contract,
    }
    message = {
        "paymentToken": "0x0000000000000000000000000000000000000000",
        "payment": 0,
        "paymentReceiver": "0x0000000000000000000000000000000000000000",
    }
    digest = _eip712_digest(domain, CREATE_PROXY_TYPES, CREATE_PROXY_TYPE, message)
    return _sign_digest(digest, private_key)


def sign_clob_auth(chain_id: int, address: str, timestamp: str, nonce: int,
                   private_key: str) -> str:
    """ClobAuth L1 signature (standard EIP-712 with prefix).

    Domain: EIP712Domain(string name,string version,uint256 chainId)
    name: "ClobAuthDomain", version: "1"
    Message: ClobAuth(address, timestamp, nonce, "This message attests that I control the given wallet")
    """
    domain = {"name": "ClobAuthDomain", "version": "1", "chainId": chain_id}
    message = {
        "address": address,
        "timestamp": timestamp,
        "nonce": nonce,
        "message": "This message attests that I control the given wallet",
    }
    return _sign_typed(domain, CLOB_AUTH_TYPES, CLOB_AUTH_TYPE, message, private_key)


def sign_order(chain_id: int, verifying_contract: str, salt: int, maker: str, signer: str,
               price: int, quantity: int, expiration: int, nonce: int, question_id: str,
               fee_rate_bps: int, intent: int, signature_type: int,
               private_key: str) -> str:
    """PRED EIP-712 Order signature (standard EIP-712 with prefix).

    price = cents * 10_000 (e.g. 55c -> 550000)
    quantity = shares * 1_000_000
    intent: long=0, short=1
    signatureType: 2
    """
    domain = {"name": "Pred CTF Exchange", "version": "1", "chainId": chain_id,
              "verifyingContract": verifying_contract}
    message = {
        "salt": salt,
        "maker": maker,
        "signer": signer,
        "taker": "0x0000000000000000000000000000000000000000",
        "price": price,
        "quantity": quantity,
        "expiration": expiration,
        "nonce": nonce,
        "questionId": question_id,
        "feeRateBps": fee_rate_bps,
        "intent": intent,
        "signatureType": signature_type,
    }
    return _sign_typed(domain, ORDER_TYPES, ORDER_TYPE, message, private_key)


def sign_safe_tx_digest(digest: bytes, private_key: str) -> str:
    """Sign a SafeTx EIP-712 digest raw (no EIP-191 prefix).

    The caller computes the SafeTx digest (see safe_tx_digest()). Signs raw.
    """
    return _sign_digest(digest, private_key)


def safe_tx_digest(chain_id: int, safe_address: str, safe_tx_data: dict) -> bytes:
    """Compute the raw SafeTx EIP-712 digest (no prefix) for verification/signing.

    Domain: EIP712Domain(uint256 chainId,address verifyingContract=safeAddress)
    Type: SafeTx(address to,uint256 value,bytes data,uint8 operation,uint256 safeTxGas,
                 uint256 baseGas,uint256 gasPrice,address gasToken,address refundReceiver,uint256 nonce)
    """
    domain = {"chainId": chain_id, "verifyingContract": safe_address}
    return _eip712_digest(domain, SAFE_TX_TYPES, SAFE_TX_TYPE, safe_tx_data)


def sign_safe_tx(chain_id: int, safe_address: str, safe_tx_data: dict,
                 private_key: str) -> str:
    """Sign a SafeTx (raw digest, no EIP-191 prefix), canonical low-s."""
    digest = safe_tx_digest(chain_id, safe_address, safe_tx_data)
    return _sign_digest(digest, private_key)


# ---------------------------------------------------------------------------
# Helpers for callers
# ---------------------------------------------------------------------------

def is_low_s(signature: str) -> bool:
    """True if signature's s value is <= curveOrderHalf."""
    sig = HexBytes(signature)
    if len(sig) != 65:
        return False
    s_int = int.from_bytes(sig[32:64], "big")
    return s_int <= CURVE_HALF


def signature_v(signature: str) -> int:
    return int(signature[-2:], 16)


def private_to_address(private_key: str) -> str:
    return Account.from_key(private_key).address
