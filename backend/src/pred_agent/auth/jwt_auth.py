"""JWT authentication for PRED (ordinary login flow).

Grounded in kit/02_PRED_API_SPEC.md section 3 and live docs (Authentication).

- Login: POST /api/v1/auth/login-with-signature with X-API-Key header
- Refresh: POST /api/v1/auth/refresh/token, token rotation via Set-Cookie
- Single-flight lock for refresh; persist new token before dropping old
- Refresh when under 5 minutes remain; re-login on failure
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from ..signer.eip712 import sign_create_proxy


@dataclass
class JwtTokens:
    access_token: str
    refresh_token: str
    proxy_wallet_addr: str
    wallet_address: str
    user_id: str
    is_enabled_trading: bool
    expires_at: datetime | None = None  # decoded from JWT exp, if available


class JwtAuth:
    """JWT session manager. Thread-safe; refresh uses a single-flight lock."""

    def __init__(
        self,
        *,
        api_key: str,
        private_key: str,
        eoa_address: str,
        chain_id: int = 8453,
        create_proxy_factory: str = "0xA077e7aaBF2e3c0155F83789e95f46602D03F7f0",
        login_message: str = "Sign in to PRED Trading Platform",
        base_url: str = "https://www.pred.app",
        refresh_threshold_seconds: int = 300,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._api_key = api_key
        self._private_key = private_key
        self._eoa_address = eoa_address
        self._chain_id = chain_id
        self._create_proxy_factory = create_proxy_factory
        self._login_message = login_message
        self._base_url = base_url.rstrip("/")
        self._refresh_threshold = refresh_threshold_seconds
        self._http = http_client or httpx.Client(timeout=httpx.Timeout(30.0))

        self._lock = threading.RLock()
        self._tokens: JwtTokens | None = None

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def login(self) -> JwtTokens:
        """Perform a full login-with-signature. Returns new tokens."""
        nonce = str(int(__import__("time").time() * 1_000_000_000))  # nanosecond-ish
        timestamp = int(__import__("time").time())
        signature = sign_create_proxy(
            self._chain_id, self._create_proxy_factory, self._private_key
        )
        body = {
            "data": {
                "wallet_address": self._eoa_address,
                "signature": signature,
                "message": self._login_message,
                "nonce": nonce,
                "chain_type": "base",
                "timestamp": timestamp,
            }
        }
        headers = {"X-API-Key": self._api_key}
        resp = self._http.post(
            f"{self._base_url}/api/v1/auth/login-with-signature",
            json=body,
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        tokens = JwtTokens(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token", ""),
            proxy_wallet_addr=data["proxy_wallet_addr"],
            wallet_address=data["wallet_address"],
            user_id=data["data"]["user_id"],
            is_enabled_trading=bool(data["data"].get("is_enabled_trading", False)),
            expires_at=self._decode_exp(data["access_token"]),
        )
        with self._lock:
            self._tokens = tokens
        return tokens

    def refresh(self) -> JwtTokens:
        """Refresh the access token. Single-flight; new refresh from Set-Cookie.

        Refresh tokens rotate and the old one dies after first use, so concurrent callers must
        not each spend it. The lock serialises them; a caller that waited re-checks whether
        another thread already refreshed and reuses those tokens instead of rotating again.
        """
        with self._lock:
            if self._tokens is not None and self._tokens.expires_at is not None:
                remaining = (self._tokens.expires_at - datetime.now(timezone.utc)).total_seconds()
                if remaining >= self._refresh_threshold:
                    return self._tokens
            return self._refresh_locked()

    def _refresh_locked(self) -> JwtTokens:
        if self._tokens is None:
            return self.login()
        refresh_token = self._tokens.refresh_token
        if not refresh_token:
            return self.login()
        headers = {"X-Refresh-Token": refresh_token}
        # Also send current access token as Bearer (docs list both options)
        headers["Authorization"] = f"Bearer {self._tokens.access_token}"
        resp = self._http.post(
            f"{self._base_url}/api/v1/auth/refresh/token",
            headers=headers,
        )
        if resp.status_code == 401:
            # Token already used or invalid — re-login
            return self.login()
        resp.raise_for_status()
        body = resp.json()
        # Docs: {"success", "data": {"access_token", "user_id", "proxy_wallet_address",
        # "is_enabled_trading", "data": {...}}} — access_token is nested under data.
        inner = body.get("data") or {}
        access_token = inner.get("access_token") or body.get("access_token")
        if not access_token:
            raise RuntimeError("refresh response has no access_token")
        nested = inner.get("data") or {}
        enabled = inner.get("is_enabled_trading", nested.get("is_enabled_trading"))
        # New refresh token comes only in Set-Cookie per spec; fall back to body
        new_refresh = (
            self._extract_refresh_cookie(resp)
            or inner.get("refresh_token")
            or body.get("refresh_token", "")
        )
        tokens = JwtTokens(
            access_token=access_token,
            refresh_token=new_refresh or refresh_token,
            proxy_wallet_addr=inner.get("proxy_wallet_address")
            or self._tokens.proxy_wallet_addr,
            wallet_address=inner.get("wallet_address") or self._tokens.wallet_address,
            user_id=inner.get("user_id") or nested.get("user_id") or self._tokens.user_id,
            is_enabled_trading=(
                self._tokens.is_enabled_trading if enabled is None else bool(enabled)
            ),
            expires_at=self._decode_exp(access_token),
        )
        # Persist new token before discarding old (atomic swap under lock)
        self._tokens = tokens
        return tokens

    def access_headers(self, proxy_address: str) -> dict[str, str]:
        """Return headers for authenticated calls: Bearer + wallet + proxy."""
        self._ensure_valid()
        assert self._tokens is not None
        return {
            "Authorization": f"Bearer {self._tokens.access_token}",
            "X-Wallet-Address": self._tokens.wallet_address,
            "X-Proxy-Address": proxy_address,
        }

    def _ensure_valid(self) -> None:
        """Refresh if under threshold or no tokens."""
        with self._lock:
            if self._tokens is None:
                self.login()
                return
            if self._tokens.expires_at is not None:
                remaining = (self._tokens.expires_at - datetime.now(timezone.utc)).total_seconds()
                if remaining < self._refresh_threshold:
                    self._refresh_locked()

    def is_enabled_trading(self) -> bool:
        with self._lock:
            return bool(self._tokens and self._tokens.is_enabled_trading)

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _extract_refresh_cookie(self, resp: httpx.Response) -> str:
        """Extract refresh_token from Set-Cookie header."""
        cookie: str = resp.headers.get("Set-Cookie", "")
        for raw_part in cookie.split(";"):
            part = raw_part.strip()
            if part.startswith("refresh_token="):
                return part.split("=", 1)[1]
        return ""

    @staticmethod
    def _decode_exp(jwt_token: str) -> datetime | None:
        """Best-effort decode of JWT exp claim (no signature verification)."""
        try:
            import base64
            import json

            parts = jwt_token.split(".")
            if len(parts) < 2:
                return None
            payload = parts[1]
            payload += "=" * (-len(payload) % 4)
            data = json.loads(base64.urlsafe_b64decode(payload))
            exp = data.get("exp")
            if exp:
                return datetime.fromtimestamp(exp, tz=timezone.utc)
        except Exception:  # noqa: S110 - malformed token => treat as no expiry
            pass
        return None
