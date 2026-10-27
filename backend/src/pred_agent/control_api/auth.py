"""Device pairing and token auth for Control API.

Grounded in kit/07_CONTROL_API.md section Auth and pairing.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DeviceRecord:
    id: str
    name: str
    secret_hash: str
    created_at: float
    last_seen: float | None = None
    revoked_at: float | None = None
    invalid_attempts: int = 0
    locked_until: float | None = None


class DeviceAuth:
    """Manages device pairing codes, device tokens, and revocation."""

    def __init__(self) -> None:
        self._devices: dict[str, DeviceRecord] = {}
        self._pairing_codes: dict[str, dict[str, Any]] = {}  # code -> {name, public_key, expires_at}
        self._tokens: dict[str, str] = {}  # device_token -> device_id

    def generate_pairing_code(self, name: str) -> str:
        """Generate a one-time pairing code valid 5 minutes."""
        code = secrets.token_urlsafe(12)  # short enough to enter manually
        expires_at = time.time() + 300  # 5 minutes
        self._pairing_codes[code] = {
            "name": name,
            "expires_at": expires_at,
            "used": False,
        }
        return code

    def pair(self, code: str, device_name: str, device_public_key: str) -> str | None:
        """Consume a pairing code and issue a long random device token."""
        entry = self._pairing_codes.get(code)
        if not entry or entry["used"] or time.time() > entry["expires_at"]:
            return None
        entry["used"] = True

        device_id = secrets.token_urlsafe(16)
        device_secret = secrets.token_urlsafe(32)
        secret_hash = hashlib.sha256(device_secret.encode()).hexdigest()

        self._devices[device_id] = DeviceRecord(
            id=device_id,
            name=device_name or entry["name"],
            secret_hash=secret_hash,
            created_at=time.time(),
        )
        device_token = secrets.token_urlsafe(32)
        self._tokens[device_token] = device_id
        return device_token

    def verify_token(self, token: str) -> str | None:
        """Verify a device token. Returns device_id or None."""
        if token not in self._tokens:
            return None
        device_id = self._tokens[token]
        device = self._devices.get(device_id)
        if device is None or device.revoked_at is not None:
            return None
        if device.locked_until is not None and time.time() < device.locked_until:
            return None
        device.last_seen = time.time()
        return device_id

    def revoke_device(self, device_id: str) -> bool:
        device = self._devices.get(device_id)
        if device is None:
            return False
        device.revoked_at = time.time()
        # Invalidate all tokens for this device
        tokens_to_remove = [t for t, d in self._tokens.items() if d == device_id]
        for t in tokens_to_remove:
            del self._tokens[t]
        return True

    def list_devices(self) -> list[dict[str, Any]]:
        return [
            {
                "id": d.id,
                "name": d.name,
                "created_at": d.created_at,
                "last_seen": d.last_seen,
                "revoked_at": d.revoked_at,
                "locked_until": d.locked_until,
            }
            for d in self._devices.values()
        ]

    def record_invalid_attempt(self, device_id: str | None) -> None:
        if device_id is None:
            return
        device = self._devices.get(device_id)
        if device is None:
            return
        device.invalid_attempts += 1
        if device.invalid_attempts >= 5:
            device.locked_until = time.time() + 300  # lock for 5 minutes

    def reset_invalid_attempts(self, device_id: str) -> None:
        device = self._devices.get(device_id)
        if device:
            device.invalid_attempts = 0
            device.locked_until = None
