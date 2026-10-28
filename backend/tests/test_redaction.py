"""Unit tests for central log redaction filter.

Canary secrets must never appear in logs, Telegram messages, API responses,
or audit output.
"""

from __future__ import annotations

import logging

from pred_agent.util.redaction import RedactionFilter, contains_secret, redact

CANARY_SECRET = "0xdeadbeef" + "01" * 30  # 64-hex private-key-like
CANARY_JWT = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.fake.signature"


def test_redact_private_key():
    assert contains_secret(CANARY_SECRET)
    clean = redact(f"key={CANARY_SECRET}")
    assert CANARY_SECRET not in clean
    assert "[REDACTED]" in clean


def test_redact_jwt():
    assert contains_secret(CANARY_JWT)
    clean = redact(f"Authorization: Bearer {CANARY_JWT}")
    assert CANARY_JWT not in clean


def test_redact_pred_headers():
    assert contains_secret("PRED_API_KEY=abc")
    clean = redact("PRED_API_KEY=abc PRED_SIGNATURE=xyz")
    assert "PRED_API_KEY" not in clean
    assert "PRED_SIGNATURE" not in clean


def test_redact_legacy_partner_key_value():
    clean = redact("PRED_PARTNER_KEY=legacy-secret")
    assert "legacy-secret" not in clean


def test_redact_telegram_token():
    clean = redact("TELEGRAM_BOT_TOKEN=123456:ABC")
    assert "TELEGRAM_BOT_TOKEN" not in clean


def test_redact_llm_key():
    clean = redact("LLM_API_KEY=sk-12345")
    assert "LLM_API_KEY" not in clean


def test_redaction_filter():
    logger = logging.getLogger("test_redaction_logger")
    logger.addFilter(RedactionFilter())
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0,
        msg=f"secret={CANARY_SECRET}", args=(), exc_info=None,
    )
    result = logger.filter(record)
    assert result
    assert CANARY_SECRET not in record.msg
    assert "[REDACTED]" in record.msg
