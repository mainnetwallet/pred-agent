"""Central log redaction filter.

Canary secrets (keys, JWT, refresh token, L2 secret/passphrase, signatures,
Authorization, PRED_* headers, Telegram token) are never written to logs.
Tested with canary strings in tests/test_redaction.py.
"""

from __future__ import annotations

import logging
import re
from typing import Iterable

# Patterns that must never appear in log output.
# - Private key: 0x + 64 hex chars (32 bytes)
# - EIP-712 signature: 0x + 130 hex chars (65 bytes: r, s, v)
# Match 64 to 130 hex chars so both private keys and full signatures are redacted.
CANARY_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"PRED_PARTNER_KEY=\S+"),
    re.compile(r"0x[a-fA-F0-9]{64,130}"),           # private key or EIP-712 signature
    re.compile(r"eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+"),  # JWT-like
    re.compile(r"PRED_[A-Z_]+"),                    # PRED_* headers
    re.compile(r"Authorization:\s*Bearer\s+\S+"),
    re.compile(r"X-API-Key:\s*\S+"),
    re.compile(r"X-Aggregator-API-Key:\s*\S+"),
    re.compile(r"TELEGRAM_BOT_TOKEN=\S+"),
    re.compile(r"LLM_API_KEY=\S+"),
    re.compile(r"PRIVATE_KEY=\S+"),
]

REDACTION_TOKEN = "[REDACTED]"  # noqa: S105 - placeholder text, not a secret


def redact(text: str) -> str:
    """Return text with all known secret patterns replaced."""
    for pattern in CANARY_PATTERNS:
        text = pattern.sub(REDACTION_TOKEN, text)
    return text


def contains_secret(text: str) -> bool:
    """True if any canary secret pattern is present."""
    return any(pattern.search(text) for pattern in CANARY_PATTERNS)


class RedactionFilter(logging.Filter):
    """logging.Filter that redacts secrets from every LogRecord."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.msg is not None:
            record.msg = redact(str(record.msg))
        if record.args:
            record.args = tuple(redact(str(a)) for a in record.args)
        return True


def install_redaction(logger_names: Iterable[str] | None = None) -> None:
    """Attach RedactionFilter to the given loggers (default: root + pred_agent)."""
    import logging as _logging

    names = list(logger_names or ["", "pred_agent"])
    for name in names:
        logger = _logging.getLogger(name)
        if not any(isinstance(f, RedactionFilter) for f in logger.filters):
            logger.addFilter(RedactionFilter())
