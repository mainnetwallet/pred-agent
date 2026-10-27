"""Login for the website and mobile app.

One fixed operator account. Username and password live in env
(``ADMIN_USERNAME`` / ``ADMIN_PASSWORD``), never in code. A successful login
returns a signed, expiring bearer token (HMAC-SHA256). Tokens are stateless, so
they survive nothing more than the signing secret: set ``SESSION_SECRET`` on
Render to keep sessions valid across redeploys.

If ``ADMIN_PASSWORD`` is unset, login is disabled (fail closed).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

_MAX_FAILS = 5
_LOCK_SECONDS = 300


class LoginManager:
    def __init__(
        self,
        *,
        username: str,
        password: str | None,
        secret: str | None,
        ttl_hours: int = 24,
    ) -> None:
        self._username = username
        self._password = password
        self._ttl = max(1, ttl_hours) * 3600
        if secret:
            key_src = secret.encode()
        elif password:
            # Stable across restarts without a separate secret.
            key_src = hashlib.sha256(b"pred-agent-session|" + password.encode()).digest()
        else:
            key_src = secrets.token_bytes(32)
        self._key = hashlib.sha256(b"pred-agent-hmac|" + key_src).digest()
        self._fails: dict[str, tuple[int, float]] = {}

    @property
    def enabled(self) -> bool:
        return bool(self._password)

    # -- brute-force lockout, per client key (IP) -------------------------
    def locked_for(self, client: str) -> int:
        count, until = self._fails.get(client, (0, 0.0))
        if count >= _MAX_FAILS and time.time() < until:
            return int(until - time.time()) + 1
        return 0

    def _record_fail(self, client: str) -> None:
        count, until = self._fails.get(client, (0, 0.0))
        if until and time.time() >= until:
            count = 0
        count += 1
        self._fails[client] = (count, time.time() + _LOCK_SECONDS)

    # -- login / tokens ---------------------------------------------------
    def login(self, username: str, password: str, client: str = "?") -> str | None:
        if not self.enabled or self.locked_for(client):
            return None
        user_ok = hmac.compare_digest(username.encode(), self._username.encode())
        pass_ok = hmac.compare_digest(password.encode(), (self._password or "").encode())
        if not (user_ok and pass_ok):
            self._record_fail(client)
            return None
        self._fails.pop(client, None)
        return self._issue()

    def _sign(self, payload: str) -> str:
        return hmac.new(self._key, payload.encode(), hashlib.sha256).hexdigest()

    def _issue(self) -> str:
        body = {"u": self._username, "exp": int(time.time()) + self._ttl, "n": secrets.token_hex(8)}
        payload = base64.urlsafe_b64encode(json.dumps(body).encode()).decode().rstrip("=")
        return f"s1.{payload}.{self._sign(payload)}"

    def verify(self, token: str) -> str | None:
        """Return the username if the token is valid and unexpired."""
        if not self.enabled:
            return None
        parts = token.split(".")
        if len(parts) != 3 or parts[0] != "s1":
            return None
        _, payload, sig = parts
        if not hmac.compare_digest(sig, self._sign(payload)):
            return None
        try:
            padded = payload + "=" * (-len(payload) % 4)
            body = json.loads(base64.urlsafe_b64decode(padded))
        except (ValueError, json.JSONDecodeError):
            return None
        if not isinstance(body, dict) or int(body.get("exp", 0)) < time.time():
            return None
        user = body.get("u")
        return user if isinstance(user, str) else None
