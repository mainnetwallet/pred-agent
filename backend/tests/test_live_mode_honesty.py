"""LIVE mode must never be described as paper, and must fail closed while unwired."""
from __future__ import annotations

from pred_agent.llm.chat import SYSTEM_PROMPT, build_system_prompt


def test_paper_prompt_says_paper():
    prompt = build_system_prompt("PAPER")
    assert "PAPER mode" in prompt
    assert "Real money is NOT at risk" in prompt
    assert SYSTEM_PROMPT == prompt


def test_live_prompt_never_claims_paper_or_safe():
    prompt = build_system_prompt("LIVE")
    assert "LIVE mode" in prompt
    assert "REAL-MONEY" in prompt
    assert "PAPER mode" not in prompt
    assert "NOT at risk" not in prompt


def test_unknown_mode_fails_closed_to_live_wording():
    assert "REAL-MONEY" in build_system_prompt("???")
