"""CLOB authentication (L1 credential create/derive + L2 HMAC).

Grounded in the official PRED docs: "Builder and Aggregator Onboarding" and "Orders".

- L1: GET /clob/time (unauthenticated); sign ClobAuth (EIP-712); recover already-issued
  credentials with GET /clob/auth/derive-api-key, or issue them for an EXISTING eligible
  account with POST /clob/auth/api-key. Per the docs, an existing eligible user can do this
  "with L1 headers and a fresh nonce, without a partner key" and it does not consume PRED's
  account-creation quota — only a brand-new EOA needs the partner key, and that path (and the
  partner key itself) is not implemented here at all, so this client cannot create a new
  account under any configuration.
- L2: headers PRED_ADDRESS/API_KEY/PASSPHRASE/TIMESTAMP/SIGNATURE
  PRED_SIGNATURE = base64url(HMAC_SHA256(base64url_decode(secret), timestamp + METHOD + canonicalPath + rawBody))
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass

import httpx

from ..signer.eip712 import sign_clob_auth

log = logging.getLogger(__name__)

# derive-api-key statuses that mean "no credential yet for this EOA/nonce": fall through to create.
_DERIVE_FALLTHROUGH = (400, 401, 403, 404)
# PRED error codes (docs: Errors and retries) that mean the EOA is not an eligible existing account.
_ELIGIBILITY_CODES = ("aggregator_key_required", "invalid_auth")


def _response_error_code(response: httpx.Response) -> str | None:
    """Extract only a structured, non-secret PRED error code for branching/diagnostics."""
    try:
        data = response.json()
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    error = data.get("error") or data.get("code")
    if isinstance(error, dict):
        error = error.get("code") or error.get("message")
    return str(error) if isinstance(error, str) else None


class ClobAccessBlocked(httpx.HTTPStatusError):
    """PRED refused to issue CLOB credentials to this EOA (derive AND create were both refused).

    Per the docs a brand-new EOA needs a PRED-issued partner key (which would open a NEW
    account — not implemented here); only an existing eligible account whose *primary wallet*
    is this EOA can get credentials without one, and market-maker/administrator accounts are
    not eligible. The refusal is made by PRED's server, not by the client, so nothing here can
    fix it automatically.
    """

    def __init__(
        self,
        *,
        eoa: str,
        request: httpx.Request,
        response: httpx.Response,
        derive_status: int,
        derive_code: str | None,
    ) -> None:
        self.eoa = eoa
        self.derive_status = derive_status
        self.derive_code = derive_code or ""
        self.create_status = response.status_code
        self.create_code = _response_error_code(response) or ""
        super().__init__(self.explain(), request=request, response=response)

    def explain(self) -> str:
        return (
            f"CLOB access blocked by PRED for EOA {self.eoa}: "
            f"derive-api-key -> HTTP {self.derive_status} {self.derive_code or '?'}; "
            f"create-api-key (no partner key) -> HTTP {self.create_status} {self.create_code or '?'}. "
            "PRED does not treat this EOA as an existing, eligible CLOB account. Per PRED's docs the "
            "EOA must be the account's PRIMARY wallet (a linked-only login wallet is refused), and "
            "market-maker/administrator accounts are not eligible. This is decided by PRED, so it "
            "cannot be fixed in code: ask PRED to enable CLOB access for this EOA or to tell you the "
            "account's primary wallet. No new account or Safe is ever created automatically."
        )


@dataclass
class ClobCredentials:
    api_key: str
    secret: str  # base64url-encoded HMAC secret
    passphrase: str


class ClobAuth:
    """CLOB L1/L2 authentication manager."""

    def __init__(
        self,
        *,
        eoa_address: str,
        private_key: str,
        chain_id: int = 8453,
        base_url: str = "https://www.pred.app",
        http_client: httpx.Client | None = None,
        pad_signature: bool = False,
    ) -> None:
        self._eoa_address = eoa_address
        self._private_key = private_key
        self._chain_id = chain_id
        self._base_url = base_url.rstrip("/")
        self._http = http_client or httpx.Client(timeout=httpx.Timeout(30.0))
        self._credentials: ClobCredentials | None = None
        self._clock_offset_seconds: float = 0.0  # server - local
        self._clock_synced = False
        # The docs do not state whether the base64url HMAC output keeps '=' padding; this
        # client has always stripped it (matches the original, still the default). ClobTransport
        # flips this once, on a read-only GET only, if PRED ever answers invalid_l2.
        self.pad_signature = pad_signature

    @property
    def eoa_address(self) -> str:
        return self._eoa_address

    # ------------------------------------------------------------------ #
    # Clock: PRED rejects L1/L2 timestamps more than 30 s from server time
    # ------------------------------------------------------------------ #

    def server_time(self) -> int:
        """GET /clob/time (unauthenticated). Docs: Unix seconds as a plain JSON integer."""
        resp = self._http.get(f"{self._base_url}/clob/time")
        resp.raise_for_status()
        value = resp.json()
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise ValueError(f"unexpected /clob/time reply: {value!r}")
        return int(value)

    def sync_clock(self) -> None:
        """Track PRED clock offset so L1/L2 timestamps stay within ±30 s."""
        server = self.server_time()
        local = int(time.time())
        self._clock_offset_seconds = server - local
        self._clock_synced = True

    def _ensure_clock(self) -> None:
        """Sync once before the first L1 call so a skewed host clock cannot cause invalid_l1."""
        if self._clock_synced:
            return
        try:
            self.sync_clock()
        except Exception as exc:  # noqa: BLE001 - fall back to the local clock, but say so
            log.warning("could not read PRED /clob/time (%s); using the local clock", type(exc).__name__)

    def _now(self) -> int:
        return int(time.time() + self._clock_offset_seconds)

    # ------------------------------------------------------------------ #
    # L1: create or derive credentials
    # ------------------------------------------------------------------ #

    def _clob_auth_signature(self, timestamp: int, nonce: int) -> str:
        return sign_clob_auth(
            self._chain_id, self._eoa_address, str(timestamp), nonce, self._private_key
        )

    def _l1_headers(self, nonce: int) -> dict[str, str]:
        timestamp = self._now()
        return {
            "PRED_ADDRESS": self._eoa_address,
            "PRED_SIGNATURE": self._clob_auth_signature(timestamp, nonce),
            "PRED_TIMESTAMP": str(timestamp),
            "PRED_NONCE": str(nonce),
        }

    def ensure_credentials(self, nonce: int = 0) -> ClobCredentials:
        """Get L2 credentials for an EXISTING, eligible PRED account. Never creates one.

        Docs: derive only *recovers* credentials that were already issued; an account made on
        the PRED site has none yet. An existing eligible user can issue them with L1 headers and
        a fresh nonce, with no partner key and no account-creation quota. So: derive first, and
        if PRED answers 4xx, create with the same nonce (still no partner key, ever). If PRED
        still refuses because it does not recognise this EOA at all, raise ``ClobAccessBlocked``
        — a PRED-side eligibility decision that cannot be worked around here.
        """
        self._ensure_clock()
        try:
            return self.derive_credentials(nonce)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in _DERIVE_FALLTHROUGH:
                raise
            derive_status = exc.response.status_code
            derive_code = _response_error_code(exc.response)
        try:
            return self.create_credentials(nonce)
        except httpx.HTTPStatusError as exc:
            if (
                exc.response.status_code in (401, 403)
                and (_response_error_code(exc.response) or "") in _ELIGIBILITY_CODES
            ):
                raise ClobAccessBlocked(
                    eoa=self._eoa_address,
                    request=exc.request,
                    response=exc.response,
                    derive_status=derive_status,
                    derive_code=derive_code,
                ) from exc
            raise

    def create_credentials(self, nonce: int = 0) -> ClobCredentials:
        """L1: POST /clob/auth/api-key for an already eligible EOA. Never sends a partner key."""
        self._ensure_clock()
        resp = self._http.post(f"{self._base_url}/clob/auth/api-key", headers=self._l1_headers(nonce))
        if resp.status_code == 400 and _response_error_code(resp) == "nonce_already_used":
            # nonce already used — derive with same nonce, fresh signature
            return self.derive_credentials(nonce)
        resp.raise_for_status()
        return self._store(resp.json())

    def derive_credentials(self, nonce: int) -> ClobCredentials:
        """GET /clob/auth/derive-api-key with same nonce + fresh L1 headers."""
        self._ensure_clock()
        resp = self._http.get(
            f"{self._base_url}/clob/auth/derive-api-key", headers=self._l1_headers(nonce)
        )
        resp.raise_for_status()
        return self._store(resp.json())

    def _store(self, data: dict[str, object]) -> ClobCredentials:
        creds = ClobCredentials(
            api_key=str(data["apiKey"]), secret=str(data["secret"]), passphrase=str(data["passphrase"])
        )
        self._credentials = creds
        return creds

    # ------------------------------------------------------------------ #
    # L2: HMAC headers for all other CLOB calls
    # ------------------------------------------------------------------ #

    def _b64url_decode(self, value: str) -> bytes:
        # base64url may have padding ('=') — decode accordingly
        padded = value + "=" * (-len(value) % 4)
        return urlsafe_b64decode(padded)

    def l2_headers(
        self,
        method: str,
        canonical_path: str,
        raw_body: bytes = b"",
    ) -> dict[str, str]:
        """Build L2 HMAC headers for a CLOB request.

        canonicalPath excludes /clob and the query string.
        rawBody is the exact transmitted bytes ("" when there is none; "{}" is different).
        A fresh timestamp and HMAC are produced on every call (retries included).
        """
        if self._credentials is None:
            raise RuntimeError("CLOB credentials not initialized; run ensure_credentials() first")
        timestamp = str(self._now())
        message = timestamp + method.upper() + canonical_path + raw_body.decode("utf-8", errors="replace")
        secret = self._b64url_decode(self._credentials.secret)
        mac = hmac.new(secret, message.encode("utf-8"), hashlib.sha256)
        signature = urlsafe_b64encode(mac.digest()).decode("ascii")
        if not self.pad_signature:
            signature = signature.rstrip("=")
        return {
            "PRED_ADDRESS": self._eoa_address,
            "PRED_API_KEY": self._credentials.api_key,
            "PRED_PASSPHRASE": self._credentials.passphrase,
            "PRED_TIMESTAMP": timestamp,
            "PRED_SIGNATURE": signature,
        }

    @property
    def credentials(self) -> ClobCredentials | None:
        return self._credentials
