"""PredTransport protocol — PRED REST access behind a single interface.

Grounded in kit/02_PRED_API_SPEC.md sections 2-4 and 6-9 and kit/03_ARCHITECTURE.md section 2.

The rest of the code never branches on auth mode except for capability flags
(has_realtime, has_parent_open_orders, has_earnings).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class TransportCaps:
    has_realtime: bool = False          # Ably (JWT only)
    has_parent_open_orders: bool = False  # per-parent real-time open orders (JWT only)
    has_earnings: bool = False          # portfolio/earnings (JWT only)


class PredTransportError(Exception):
    """Transport-level error preserving HTTP status and raw body."""

    def __init__(self, status: int, message: str, raw_body: str | None = None,
                 request_id: str | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.raw_body = raw_body
        self.request_id = request_id


@runtime_checkable
class PredTransport(Protocol):
    """Protocol for PRED REST transport (JWT or CLOB)."""

    caps: TransportCaps

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET request; returns parsed JSON body (data or top-level)."""

    def post(self, path: str, json: dict[str, Any] | None = None) -> dict[str, Any]:
        """POST request; returns parsed JSON body."""

    def delete(self, path: str, json: dict[str, Any] | None = None) -> dict[str, Any]:
        """DELETE request; returns parsed JSON body."""

    def raw_get(self, path: str, params: dict[str, Any] | None = None) -> tuple[int, str, dict[str, Any]]:
        """Returns (status, raw_body, headers) for audit logging."""

    def health_check(self) -> bool:
        """True if the transport can reach PRED (read-only GET)."""
