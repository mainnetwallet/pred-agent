"""Frozen application settings.

Loads `.env` (secrets) and `config.yaml` (non-secret settings), validates with
pydantic, and freezes at startup.  `LIVE_TRADING` defaults to False — real
orders are impossible unless the operator manually sets it true on the server.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class LimitsConfig(BaseModel):
    max_trade_usd: float = Field(ge=0, default=2.0)
    daily_loss_usd: float = Field(ge=0, default=5.0)
    max_open_positions: int = Field(ge=0, default=8)


class LLMConfig(BaseModel):
    provider: str = "openai"
    model: str | None = None
    daily_budget_usd: float = Field(ge=0, default=5.0)


class ControlAPIConfig(BaseModel):
    bind: str | None = None


class TelegramConfig(BaseModel):
    allowed_user_id: str | None = None


class Settings(BaseSettings):
    # PRED
    host: str = "https://www.pred.app"
    chain_id: int = 8453
    usdc_address: str = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
    create_proxy_factory: str = "0xA077e7aaBF2e3c0155F83789e95f46602D03F7f0"

    # Secrets (from .env)
    pred_api_key: str | None = None
    private_key: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    llm_base_url: str = "https://api.openai.com/v1"
    llm_daily_max_calls: int = 200
    telegram_bot_token: str | None = None
    telegram_allowed_user_id: str | None = None
    odds_api_key: str | None = None
    sports_api_key: str | None = None
    base_rpc_url: str | None = None

    # App / website login (fixed credentials come from env, never from code)
    admin_username: str = "admin"
    admin_password: str | None = None
    session_secret: str | None = None
    session_ttl_hours: int = 24

    # Operational flags
    live_trading: bool = False
    control_api_bind: str | None = None
    database_url: str | None = None

    # Timing
    auth_login_message: str = "Sign in to PRED Trading Platform"
    refresh_threshold_seconds: int = 300

    # Scan / market data
    scan_interval_seconds: int = 30
    discover_limit: int = 50
    discover_max_rps: int = 10
    omit_inactive_with_redeemed_on_before: str | None = None

    # Strategy / risk
    min_edge_pp: float = 3.0
    kelly_fraction: float = 0.25
    max_trade_usd: float = 2.0
    per_fixture_pct: float = 0.04
    total_open_exposure_pct: float = 0.15
    daily_loss_pct: float = 0.05
    max_open_positions: int = 8
    min_opening_notional_usd: float = 0.90
    max_spread_cents: float = 6.0
    max_slippage_pp: float = 1.5
    proposal_ttl_seconds: int = 300
    research_max_age_min: int = 90
    book_max_age_s: int = 60
    auto_stop_loss: bool = False
    league_allowlist: list[str] = []

    # Limits (hard upper bounds in code)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)

    # LLM
    llm: LLMConfig = Field(default_factory=LLMConfig)

    # Control API / Telegram
    control_api: ControlAPIConfig = Field(default_factory=ControlAPIConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("private_key")
    @classmethod
    def _validate_private_key(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if v and not (v.startswith("0x") and len(v) == 66):
            raise ValueError("PRIVATE_KEY must be 0x + 64 hex chars (32 bytes)")
        return v

    @model_validator(mode="after")
    def _check_live_trading_requires_keys(self) -> "Settings":
        if self.live_trading and not self.private_key:
            raise ValueError("PRIVATE_KEY is required when LIVE_TRADING=true")
        return self

    def freeze(self) -> "Settings":
        """Mark settings as frozen — no further mutation allowed."""
        self._frozen = True
        return self

    def __setattr__(self, name: str, value: Any) -> None:
        if getattr(self, "_frozen", False) and name != "_frozen":
            raise AttributeError(f"Settings are frozen; cannot set {name!r}")
        super().__setattr__(name, value)


def load_settings(env_file: str | None = None) -> Settings:
    """Load and freeze settings. Call once at startup."""
    settings = Settings(env_file=env_file) if env_file else Settings()
    return settings.freeze()


def load_config_yaml(path: str | Path = "config.yaml") -> dict[str, Any]:
    """Load non-secret settings from config.yaml (no secrets here)."""
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}
