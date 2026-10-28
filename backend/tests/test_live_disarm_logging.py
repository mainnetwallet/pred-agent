"""disarm_live logs loudly once per distinct reason, then quietly while the retry loop repeats it."""

import logging

from pred_agent.config import Settings
from pred_agent.control_api.controller import AgentController


def _controller() -> AgentController:
    return AgentController(Settings(live_trading=False).freeze())


def test_disarm_logs_error_once_then_debug(caplog):
    c = _controller()
    caplog.set_level(logging.DEBUG)
    c.disarm_live("HTTP 401 aggregator_key_required")
    c.disarm_live("HTTP 401 aggregator_key_required")
    levels = [r.levelno for r in caplog.records if "NOT ready" in r.message]
    assert levels == [logging.ERROR, logging.DEBUG]
    assert c.live_execution_ready is False
    assert c.live_not_ready_reason == "HTTP 401 aggregator_key_required"


def test_new_reason_logs_error_again(caplog):
    c = _controller()
    caplog.set_level(logging.DEBUG)
    c.disarm_live("reason A")
    c.disarm_live("reason B")
    levels = [r.levelno for r in caplog.records if "NOT ready" in r.message]
    assert levels == [logging.ERROR, logging.ERROR]
