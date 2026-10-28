"""CLOB authentication (L1 credential creation + L2 HMAC).

Grounded in kit/02_PRED_API_SPEC.md section 4 and live docs
(Builder and Aggregator Onboarding).

- L1: GET /clob/time; sign ClobAuth; derive or issue credentials for an existing eligible EOA
- L2: headers PRED_ADDRESS/API_KEY/PASSPHRASE/TIMESTAMP/SIGNATURE
  PRED_SIGNATURE = base64url(HMAC_SHA256(base64url_decode(secret), timestamp + METHOD + canonicalPath + rawBody))
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass

import httpx

from ..signer.eip712 import sign_clob_auth

log = logging.getLogger(__name__)


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
    ) -> None:
        self._eoa_address = eoa_address
        self._private_key = private_key
        self._chain_id = chain_id
        self._base_url = base_url.rstrip("/")
        self._http = http_client or httpx.Client(timeout=httpx.Timeout(30.0))
        self._credentials: ClobCredentials | None = None
        self._clock_offset_seconds: float = 0.0  # server - local

    # ------------------------------------------------------------------ #
    # L1: create or derive credentials
    # ------------------------------------------------------------------ #

    def server_time(self) -> int:
        resp = self._http.get(f"{self._base_url}/clob/time")
        resp.raise_for_status()
        return int(resp.json())

    def sync_clock(self) -> None:
        """Track PRED clock offset so L1/L2 timestamps stay within ±30 s."""
        server = self.server_time()
        local = int(time.time())
        self._clock_offset_seconds = server - local

    def _now(self) -> int:
        return int(time.time() + self._clock_offset_seconds)

    def _clob_auth_signature(self, timestamp: int, nonce: int) -> str:
        return sign_clob_auth(
            self._chain_id, self._eoa_address, str(timestamp), nonce, self._private_key
        )

    def ensure_credentials(self, nonce: int = 0) -> ClobCredentials:
        """Get L2 credentials for an EXISTING PRED account.

        Derive credentials first. If an eligible existing account has not yet been issued L2
        credentials, the documented L1 endpoint can issue them without an aggregator key.
        Never send an aggregator key or retry with one: that path creates a new account/Safe
        when the EOA is not already eligible.
        """
        try:
            return self.derive_credentials(nonce)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in (400, 401, 403, 404):
                raise
        return self.create_credentials(nonce)

    def create_credentials(self, nonce: int = 0) -> ClobCredentials:
        """L1: POST /clob/auth/api-key for an already eligible EOA."""
        timestamp = self._now()
        signature = self._clob_auth_signature(timestamp, nonce)
        headers = {
            "PRED_ADDRESS": self._eoa_address,
            "PRED_SIGNATURE": signature,
            "PRED_TIMESTAMP": str(timestamp),
            "PRED_NONCE": str(nonce),
        }
        resp = self._http.post(f"{self._base_url}/clob/auth/api-key", headers=headers)
        if resp.status_code == 400 and "nonce_already_used" in resp.text:
            # nonce already used — derive with same nonce, fresh signature
            return self.derive_credentials(nonce)
        resp.raise_for_status()
        data = resp.json()
        creds = ClobCredentials(
            api_key=data["apiKey"],
            secret=data["secret"],
            passphrase=data["passphrase"],
        )
        self._credentials = creds
        return creds

    def derive_credentials(self, nonce: int) -> ClobCredentials:
        """GET /clob/auth/derive-api-key with same nonce + fresh L1 headers."""
        timestamp = self._now()
        signature = self._clob_auth_signature(timestamp, nonce)
        headers = {
            "PRED_ADDRESS": self._eoa_address,
            "PRED_SIGNATURE": signature,
            "PRED_TIMESTAMP": str(timestamp),
            "PRED_NONCE": str(nonce),
        }
        resp = self._http.get(
            f"{self._base_url}/clob/auth/derive-api-key", headers=headers
        )
        resp.raise_for_status()
        data = resp.json()
        creds = ClobCredentials(
            api_key=data["apiKey"],
            secret=data["secret"],
            passphrase=data["passphrase"],
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
        rawBody is the exact transmitted bytes.
        """
        if self._credentials is None:
            raise RuntimeError("CLOB credentials not initialized; run create_credentials() first")
        timestamp = str(self._now())
        message = timestamp + method.upper() + canonical_path + raw_body.decode("utf-8", errors="replace")
        secret = self._b64url_decode(self._credentials.secret)
        mac = hmac.new(secret, message.encode("utf-8"), hashlib.sha256)
        signature = urlsafe_b64encode(mac.digest()).rstrip(b"=").decode("ascii")
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
