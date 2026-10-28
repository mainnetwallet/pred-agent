"""JWT transport — authenticated PRED REST client.

Grounded in kit/02_PRED_API_SPEC.md section 3 and live docs (Authentication).

Read-only GET helpers in this phase; write methods (place/cancel) are added in
Phase 4. Account-setup calls (safe-approval prepare/execute) are exposed for
the enablement CLI.
"""

from __future__ import annotations

from typing import Any

import httpx

from ..auth.jwt_auth import JwtAuth, JwtAuthError
from .protocol import PredTransport, PredTransportError, TransportCaps


class JwtTransport(PredTransport):
    """PRED transport using JWT authentication."""

    order_prefix = ""

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

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        _, body, _ = self._request("GET", path, params=params)
        return body

    def post(self, path: str, json: dict[str, Any] | None = None) -> dict[str, Any]:
        _, body, _ = self._request("POST", path, json=json)
        return body

    def delete(self, path: str, json: dict[str, Any] | None = None) -> dict[str, Any]:
        _, body, _ = self._request("DELETE", path, json=json)
        return body

    def raw_get(self, path: str, params: dict[str, Any] | None = None) -> tuple[int, str, dict[str, Any]]:
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

    def account_status(self) -> dict[str, Any]:
        """Account identity + trading eligibility, taken from the REAL login reply.

        Shaped like the CLOB ``account-status`` so ``setup_live`` can treat both paths alike.
        ``next_step == "ready"`` only when PRED itself reported ``is_enabled_trading=true``;
        authentication success alone never implies trading permission. Raises
        ``PredTransportError`` if login fails (never returns an invented status).
        """
        try:
            tokens = self.auth.snapshot() or self.auth.login()
        except JwtAuthError as exc:
            raise PredTransportError(exc.status or 401, str(exc), raw_body=str(exc)) from None
        enabled = bool(tokens.is_enabled_trading)
        return {
            "auth_mode": "jwt",
            "eoa": tokens.wallet_address,
            "proxy_wallet_address": tokens.proxy_wallet_addr,
            "is_enabled_trading": enabled,
            "is_new_user": bool(tokens.is_new_user),
            "next_step": "ready" if enabled else "safe_approval_required",
        }

    def safe_approval_prepare(self, proxy_creation_signature: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if proxy_creation_signature:
            body["proxy_creation_signature"] = proxy_creation_signature
        _, result, _ = self._request("POST", "/api/v1/user/safe-approval/prepare", json=body)
        return result

    def safe_approval_execute(self, signature: str, data: dict[str, Any]) -> dict[str, Any]:
        _, result, _ = self._request("POST", "/api/v1/user/safe-approval/execute",
                                     json={"signature": signature, "data": data})
        return result

    def get_balance(self) -> dict[str, Any]:
        """GET /api/v1/portfolio/balance — top-level success/usdc_balance/position_balance."""
        return self.get("/api/v1/portfolio/balance")

    def ably_token(self) -> dict[str, Any]:
        """POST /api/v1/auth/ably — raw Ably TokenDetails."""
        _, result, _ = self._request("POST", "/api/v1/auth/ably")
        return result

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _request(self, method: str, path: str, params: dict[str, Any] | None = None,
                 json: dict[str, Any] | None = None) -> tuple[int, dict[str, Any], dict[str, Any]]:
        status, raw_body, headers = self._raw_request(method, path, params=params, json=json)
        if status >= 400:
            raise PredTransportError(status, f"{method} {path} failed", raw_body=raw_body)
        import json as _json
        try:
            parsed = _json.loads(raw_body) if raw_body else {}
        except _json.JSONDecodeError:
            parsed = {}
        return status, parsed, dict(headers)

    def _raw_request(self, method: str, path: str, params: dict[str, Any] | None = None,
                     json: dict[str, Any] | None = None) -> tuple[int, str, dict[str, Any]]:
        status, text, headers = self._send(method, path, params, json)
        if status == 401 and method.upper() == "GET":
            # Expired/revoked access token on a READ: drop it and retry once with a fresh login.
            # Never auto-retry writes (place/cancel): a signed order must not be re-sent blindly.
            self.auth.invalidate()
            status, text, headers = self._send(method, path, params, json)
        return status, text, headers

    def _send(self, method: str, path: str, params: dict[str, Any] | None,
              json: dict[str, Any] | None) -> tuple[int, str, dict[str, Any]]:
        try:
            self.auth._ensure_valid()
            tokens = self.auth.snapshot()
            if tokens is None:
                raise JwtAuthError("no JWT session", 401)
            headers = self.auth.access_headers(tokens.proxy_wallet_addr)
        except JwtAuthError as exc:
            raise PredTransportError(exc.status or 401, str(exc), raw_body=str(exc)) from None
        resp = self._http.request(
            method, f"{self._base_url}{path}", params=params, json=json, headers=headers
        )
        return resp.status_code, resp.text, dict(resp.headers)
