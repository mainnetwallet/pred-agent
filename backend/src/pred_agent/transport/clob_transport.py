"""CLOB transport — authenticated PRED CLOB REST client (L2 HMAC).

Grounded in kit/02_PRED_API_SPEC.md section 4 and live docs (Builder / Aggregator Onboarding).

Read-only + account-setup calls in this phase; write methods (place/cancel) added
in Phase 4. No Ably realtime in CLOB mode — polling is used instead.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from ..auth.clob_auth import ClobAuth
from .protocol import PredTransport, PredTransportError, TransportCaps

log = logging.getLogger(__name__)


class ClobTransport(PredTransport):
    """PRED transport using CLOB L2 HMAC authentication."""

    # CLOB trading routes live under /clob (docs: Builder / Aggregator CLOB).
    order_prefix = "/clob"

    def __init__(self, auth: ClobAuth, base_url: str = "https://www.pred.app",
                 http_client: httpx.Client | None = None) -> None:
        self.auth = auth
        self._base_url = base_url.rstrip("/")
        self._http = http_client or httpx.Client(timeout=httpx.Timeout(30.0))
        self.caps = TransportCaps(
            has_realtime=False,
            has_parent_open_orders=False,
            has_earnings=False,
        )
        # True once any L2 request got past PRED's L2 check (any reply except 401 invalid_l2):
        # from then on the signature encoding is known to be accepted and is never changed again.
        self._l2_verified = False

    # ------------------------------------------------------------------ #
    # PredTransport protocol
    # ------------------------------------------------------------------ #

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Authenticated GET. Read-only, so it is the one place an L2 encoding retry is safe."""
        try:
            _, body, _ = self._request("GET", path, params=params)
            return body
        except PredTransportError as exc:
            if self._l2_verified or exc.status != 401 or "invalid_l2" not in (exc.raw_body or ""):
                raise
        # PRED's docs do not say whether the base64url HMAC output keeps '=' padding. The first
        # L2 call was refused with invalid_l2, so retry this read ONCE with the other encoding
        # (and a fresh timestamp/HMAC, as the docs require). Orders and cancels never retry here.
        original = self.auth.pad_signature
        self.auth.pad_signature = not original
        try:
            _, body, _ = self._request("GET", path, params=params)
        except PredTransportError:
            self.auth.pad_signature = original
            raise
        log.warning("PRED accepted L2 signatures with pad_signature=%s", self.auth.pad_signature)
        return body

    def post(self, path: str, json: dict[str, Any] | None = None) -> dict[str, Any]:
        status, body, _ = self._request("POST", path, json=json)
        return body

    def delete(self, path: str, json: dict[str, Any] | None = None) -> dict[str, Any]:
        status, body, _ = self._request("DELETE", path, json=json)
        return body

    def raw_get(self, path: str, params: dict[str, Any] | None = None) -> tuple[int, str, dict[str, Any]]:
        return self._raw_request("GET", path, params=params)

    def health_check(self) -> bool:
        """Reachability: /clob/time is unauthenticated, so this works before credentials exist."""
        try:
            self.auth.sync_clock()
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------ #
    # Read-only / account-setup helpers (Phase 1)
    # ------------------------------------------------------------------ #

    def clob_time(self) -> int:
        """GET /clob/time (unauthenticated; no L2 headers, so it works without credentials)."""
        return self.auth.server_time()

    def account_status(self) -> dict[str, Any]:
        """GET /clob/api/v1/user/account-status — top-level fields, no data wrapper.

        Raises ``PredTransportError`` on any HTTP error: an error body (e.g. 401 invalid_l2) must
        never be mistaken for an account that simply has no Safe yet.
        """
        return self.get("/clob/api/v1/user/account-status")

    def activate_user(self, activate_user: bool = True,
                      referral_code: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"activate_user": activate_user}
        if referral_code:
            body["referral_code"] = referral_code
        _, result, _ = self._request("POST", "/clob/api/v1/user/activate-user", json=body)
        return result

    def create_proxy_wallet(self, signature: str) -> dict[str, Any]:
        _, result, _ = self._request("POST", "/clob/api/v1/user/create-proxy-wallet",
                                     json={"signature": signature})
        return result

    def safe_approval_prepare(self, proxy_creation_signature: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if proxy_creation_signature:
            body["proxy_creation_signature"] = proxy_creation_signature
        _, result, _ = self._request("POST", "/clob/api/v1/user/safe-approval/prepare", json=body)
        return result

    def safe_approval_execute(self, signature: str, data: dict[str, Any]) -> dict[str, Any]:
        _, result, _ = self._request("POST", "/clob/api/v1/user/safe-approval/execute",
                                     json={"signature": signature, "data": data})
        return result

    def get_balance(self) -> dict[str, Any]:
        """GET /clob/api/v1/portfolio/balance — top-level success/usdc_balance/position_balance."""
        return self.get("/clob/api/v1/portfolio/balance")

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
        # canonicalPath excludes /clob and query string
        canonical_path = path
        if canonical_path.startswith("/clob"):
            canonical_path = canonical_path[len("/clob"):]
        # strip query string
        if "?" in canonical_path:
            canonical_path = canonical_path[: canonical_path.index("?")]

        raw_body = b""
        if json is not None:
            import json as _json
            raw_body = _json.dumps(json, separators=(",", ":")).encode("utf-8")

        headers = self.auth.l2_headers(method.upper(), canonical_path, raw_body)
        resp = self._http.request(
            method, f"{self._base_url}{path}", params=params, content=raw_body or None,
            headers={**headers, "Content-Type": "application/json"},
        )
        if resp.status_code != 401:
            self._l2_verified = True
        return resp.status_code, resp.text, dict(resp.headers)
