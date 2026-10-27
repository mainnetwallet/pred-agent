"""JWT transport — authenticated PRED REST client.

Grounded in kit/02_PRED_API_SPEC.md section 3 and live docs (Authentication).

Read-only GET helpers in this phase; write methods (place/cancel) are added in
Phase 4. Account-setup calls (safe-approval prepare/execute) are exposed for
the enablement CLI.
"""

from __future__ import annotations

from typing import Any

import httpx

from ..auth.jwt_auth import JwtAuth
from .protocol import PredTransport, PredTransportError, TransportCaps


class JwtTransport(PredTransport):
    """PRED transport using JWT authentication."""

    def __init__(self, auth: JwtAuth, base_url: str = "https://www.pred.app",
                 http_client: httpx.Client | None = None) -> None:
        self.auth = auth
        self._base_url = base_url.rstrip("/")
        self._http = http_client or httpx.Client(timeout=httpx.Timeout(30.0))
        self.caps = TransportCaps(
            has_realtime=True,
            has_parent_open_orders=True,
            has_earnings=True,
        )

    # ------------------------------------------------------------------ #
    # PredTransport protocol
    # ------------------------------------------------------------------ #

    def get(self, path: str, params: dict | None = None) -> dict:
        _, body, _ = self._request("GET", path, params=params)
        return body

    def post(self, path: str, json: dict | None = None) -> dict:
        _, body, _ = self._request("POST", path, json=json)
        return body

    def delete(self, path: str, json: dict | None = None) -> dict:
        _, body, _ = self._request("DELETE", path, json=json)
        return body

    def raw_get(self, path: str, params: dict | None = None) -> tuple[int, str, dict]:
        return self._raw_request("GET", path, params=params)

    def health_check(self) -> bool:
        try:
            self._raw_request("GET", "/api/v1/market-discovery/discover",
                              params={"verbose": "true", "limit": "1"})
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------ #
    # Read-only helpers (Phase 1)
    # ------------------------------------------------------------------ #

    def account_status(self) -> dict:
        """CLOB-style account status (JWT: portfolio endpoints need auth headers).
        For JWT, we use /api/v1/auth/login response data instead; this is a
        placeholder kept for interface symmetry.
        """
        raise NotImplementedError("JWT account status comes from login response")

    def safe_approval_prepare(self, proxy_creation_signature: str | None = None) -> dict:
        body: dict[str, Any] = {}
        if proxy_creation_signature:
            body["proxy_creation_signature"] = proxy_creation_signature
        _, result, _ = self._request("POST", "/api/v1/user/safe-approval/prepare", json=body)
        return result

    def safe_approval_execute(self, signature: str, data: dict) -> dict:
        _, result, _ = self._request("POST", "/api/v1/user/safe-approval/execute",
                                     json={"signature": signature, "data": data})
        return result

    def ably_token(self) -> dict:
        """POST /api/v1/auth/ably — raw Ably TokenDetails."""
        _, result, _ = self._request("POST", "/api/v1/auth/ably")
        return result

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _request(self, method: str, path: str, params: dict | None = None,
                 json: dict | None = None) -> tuple[int, dict, dict]:
        status, raw_body, headers = self._raw_request(method, path, params=params, json=json)
        if status >= 400:
            raise PredTransportError(status, f"{method} {path} failed", raw_body=raw_body)
        import json as _json
        try:
            parsed = _json.loads(raw_body) if raw_body else {}
        except _json.JSONDecodeError:
            parsed = {}
        return status, parsed, dict(headers)

    def _raw_request(self, method: str, path: str, params: dict | None = None,
                     json: dict | None = None) -> tuple[int, str, dict]:
        self.auth._ensure_valid()
        assert self.auth._tokens is not None
        proxy = self.auth._tokens.proxy_wallet_addr
        headers = self.auth.access_headers(proxy)
        resp = self._http.request(
            method, f"{self._base_url}{path}", params=params, json=json, headers=headers
        )
        return resp.status_code, resp.text, dict(resp.headers)
