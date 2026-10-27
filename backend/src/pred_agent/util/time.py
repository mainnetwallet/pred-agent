"""Server-time handling and PRED clock offset.

Signing timestamps must stay within ±30 s of server time.  The agent also tracks
the PRED clock offset (from GET /clob/time) so L1/L2 headers stay valid.
"""

from __future__ import annotations

import time
from decimal import Decimal


class Clock:
    """Monotonic wall clock with an adjustable offset against PRED server time."""

    def __init__(self) -> None:
        self._offset_seconds: Decimal = Decimal("0")

    def now(self) -> int:
        """Current Unix seconds (local clock + offset)."""
        return int(time.time() + float(self._offset_seconds))

    def now_float(self) -> float:
        return time.time() + float(self._offset_seconds)

    def set_offset(self, offset_seconds: Decimal) -> None:
        """Set PRED clock offset (server_time - local_time)."""
        self._offset_seconds = offset_seconds

    @property
    def offset(self) -> Decimal:
        return self._offset_seconds


CLOCK = Clock()
