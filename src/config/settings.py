"""Fail-closed runtime settings for paper/demo operation."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import AnyHttpUrl, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    paper_trading: bool = Field(validation_alias="PAPER_TRADING")
    live_trading: bool = Field(validation_alias="LIVE_TRADING")
    broker_environment: Literal["paper", "demo"] = Field(validation_alias="BROKER_ENV")
    broker_endpoint: AnyHttpUrl | None = Field(
        default=None, validation_alias="BROKER_ENDPOINT"
    )
    daily_drawdown_limit: Decimal = Field(
        validation_alias="DAILY_DRAWDOWN_LIMIT", gt=Decimal("0"), le=Decimal("1")
    )
    database_url: str | None = Field(default=None, validation_alias="DATABASE_URL")
    data_dir: Path = Field(default=Path("data"), validation_alias="DATA_DIR")
    log_dir: Path = Field(default=Path("logs"), validation_alias="LOG_DIR")
    provider: Literal["oanda", "mt5"] = Field(
        default="oanda", validation_alias="BROKER_PROVIDER"
    )
    sentiment_provider: Literal["openai", "ollama"] = Field(
        default="openai", validation_alias="SENTIMENT_PROVIDER"
    )
    openai_api_key: str | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    ollama_base_url: AnyHttpUrl | None = Field(
        default=None, validation_alias="OLLAMA_BASE_URL"
    )
    broker_token: str | None = Field(default=None, validation_alias="BROKER_TOKEN")

    @model_validator(mode="after")
    def enforce_safe_runtime(self) -> Settings:
        """Reject missing safety guarantees rather than inferring a mode."""
        if not self.paper_trading or self.live_trading:
            raise ValueError(
                "paper trading must be true and live trading must be false"
            )
        if self.broker_environment not in {"paper", "demo"}:
            raise ValueError("broker environment must be paper or demo")
        if self.broker_endpoint is not None:
            hostname = (urlparse(str(self.broker_endpoint)).hostname or "").lower()
            if not any(
                marker in hostname
                for marker in ("practice", "demo", "paper", "localhost")
            ):
                raise ValueError(
                    "broker endpoint must be a demo, practice, paper, or localhost endpoint"
                )
        if self.daily_drawdown_limit <= 0:
            raise ValueError("daily drawdown limit must be positive")
        return self
