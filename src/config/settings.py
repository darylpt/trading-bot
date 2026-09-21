"""Fail-closed runtime settings for paper/demo operation."""

from __future__ import annotations

import os
from datetime import time
from decimal import Decimal
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import AliasChoices, AnyHttpUrl, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from trading_bot.config import (
    DEFAULT_INSTRUMENT,
    DEMO_ACCOUNT_ID,
    DEMO_BRIDGE_HOSTS,
    validate_instrument,
)


def validate_demo_endpoint(value: AnyHttpUrl | None) -> None:
    """Require an explicitly allowlisted HTTPS demo bridge endpoint."""
    if value is None:
        return
    parsed = urlparse(str(value))
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or not (
            parsed.port in {None, 443}
            or (hostname in DEMO_BRIDGE_HOSTS and parsed.port is not None)
        )
        or hostname not in {"demo.exness-mt5.local", *DEMO_BRIDGE_HOSTS}
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("BROKER_ENDPOINT is not an allowlisted HTTPS demo endpoint")


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="forbid",
        hide_input_in_errors=True,
        populate_by_name=True,
    )

    paper_trading: bool = Field(default=True, validation_alias="PAPER_TRADING")
    live_trading: bool = Field(default=False, validation_alias="LIVE_TRADING")
    trading_mode: Literal["SIMULATED", "BROKER_DEMO", "LIVE"] = Field(
        default="SIMULATED", validation_alias="TRADING_MODE"
    )
    broker_environment: Literal["paper", "demo"] = Field(
        default="demo", validation_alias="BROKER_ENV"
    )
    broker_endpoint: AnyHttpUrl | None = Field(
        default=None, validation_alias="BROKER_ENDPOINT"
    )
    exness_bridge_host: str | None = Field(
        default=None, validation_alias="EXNESS_BRIDGE_HOST"
    )
    exness_bridge_port: int = Field(
        default=18812, validation_alias="EXNESS_BRIDGE_PORT", ge=1, le=65535
    )
    provider: Literal["exness_mt5"] = Field(
        default="exness_mt5", validation_alias="BROKER_PROVIDER"
    )
    broker_type: Literal["exness_mt5"] = Field(
        default="exness_mt5", validation_alias="BROKER_TYPE"
    )
    exness_symbol_suffix: str = Field(
        default="m", validation_alias="EXNESS_SYMBOL_SUFFIX"
    )
    broker_account: str | None = Field(default=None, validation_alias="BROKER_ACCOUNT")
    broker_account_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("BROKER_ACCOUNT_ID", "EXNESS_LOGIN"),
    )
    broker_server: str | None = Field(
        default=None,
        validation_alias=AliasChoices("BROKER_SERVER", "EXNESS_SERVER"),
    )
    broker_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices("BROKER_TOKEN", "EXNESS_PASSWORD"),
    )
    exness_login: str | None = Field(default=None, validation_alias="EXNESS_LOGIN")
    exness_server: str | None = Field(default=None, validation_alias="EXNESS_SERVER")
    exness_password: str | None = Field(
        default=None, validation_alias="EXNESS_PASSWORD"
    )
    exness_demo_smoke_confirm: str | None = Field(
        default=None, validation_alias="EXNESS_DEMO_SMOKE_CONFIRM"
    )
    exness_smoke_direction: Literal["LONG", "SHORT"] = Field(
        default="LONG", validation_alias="EXNESS_SMOKE_DIRECTION"
    )
    exness_smoke_stop_distance: Decimal | None = Field(
        default=None,
        validation_alias="EXNESS_SMOKE_STOP_DISTANCE",
        gt=Decimal("0"),
    )
    exness_smoke_take_profit_distance: Decimal | None = Field(
        default=None,
        validation_alias="EXNESS_SMOKE_TAKE_PROFIT_DISTANCE",
        gt=Decimal("0"),
    )
    daily_drawdown_limit: Decimal = Field(
        default=Decimal("0.01"),
        validation_alias="DAILY_DRAWDOWN_LIMIT",
        gt=Decimal("0"),
        le=Decimal("1"),
    )
    default_symbol: str = Field(
        default=DEFAULT_INSTRUMENT, validation_alias="DEFAULT_SYMBOL"
    )
    max_spread_tolerance: Decimal = Field(
        default=Decimal("0.35"), validation_alias="MAX_SPREAD_TOLERANCE", ge=0
    )
    max_slippage_pips: Decimal = Field(
        default=Decimal("2.0"), validation_alias="MAX_SLIPPAGE_PIPS", gt=0
    )
    instrument: str = Field(default=DEFAULT_INSTRUMENT, validation_alias="INSTRUMENT")
    database_url: str | None = Field(default=None, validation_alias="DATABASE_URL")
    data_dir: Path = Field(default=Path("data"), validation_alias="DATA_DIR")
    sqlite_db_path: Path = Field(
        default=Path("data/session_metrics.db"), validation_alias="SQLITE_DB_PATH"
    )
    log_dir: Path = Field(default=Path("logs"), validation_alias="LOG_DIR")
    tick_interval_seconds: Decimal = Field(
        default=Decimal("60"), validation_alias="TICK_INTERVAL_SECONDS", gt=0
    )
    forward_test_enabled: bool = Field(
        default=False, validation_alias="FORWARD_TEST_ENABLED"
    )
    forward_test_window_start_utc: time = Field(
        default=time(13, 0), validation_alias="FORWARD_TEST_WINDOW_START_UTC"
    )
    forward_test_window_end_utc: time = Field(
        default=time(16, 0), validation_alias="FORWARD_TEST_WINDOW_END_UTC"
    )
    alert_route: Literal["structured_log", "webhook"] = Field(
        default="structured_log", validation_alias="ALERT_ROUTE"
    )
    alert_webhook_url: AnyHttpUrl | None = Field(
        default=None, validation_alias="ALERT_WEBHOOK_URL"
    )
    alert_webhook_timeout_seconds: Decimal = Field(
        default=Decimal("5"),
        validation_alias="ALERT_WEBHOOK_TIMEOUT_SECONDS",
        gt=0,
        le=30,
    )
    account_equity: Decimal | None = Field(
        default=None, validation_alias="ACCOUNT_EQUITY", gt=0
    )
    session_start_equity: Decimal | None = Field(
        default=None, validation_alias="SESSION_START_EQUITY", gt=0
    )
    risk_stop_distance: Decimal | None = Field(
        default=None, validation_alias="RISK_STOP_DISTANCE", gt=0
    )
    max_data_age_seconds: Decimal = Field(
        default=Decimal("300"), validation_alias="MAX_DATA_AGE_SECONDS", gt=0
    )
    max_clock_drift_seconds: Decimal = Field(
        default=Decimal("5"), validation_alias="MAX_CLOCK_DRIFT_SECONDS", gt=0
    )
    max_spread: Decimal = Field(
        default=Decimal("0"), validation_alias="MAX_SPREAD", ge=0
    )
    sentiment_provider: Literal["openai", "ollama"] = Field(
        default="openai", validation_alias="SENTIMENT_PROVIDER"
    )
    sentiment_interval_seconds: Decimal = Field(
        default=Decimal("900"),
        validation_alias="SENTIMENT_INTERVAL_SECONDS",
        gt=0,
    )
    sentiment_threshold: Decimal = Field(
        default=Decimal("0.50"),
        validation_alias="SENTIMENT_THRESHOLD",
        gt=0,
        lt=1,
    )
    news_events_path: Path | None = Field(
        default=None, validation_alias="NEWS_EVENTS_PATH"
    )
    news_feed_url: str = Field(
        default="https://nfs.faireconomy.media/ff_calendar_thisweek.xml",
        validation_alias="NEWS_FEED_URL",
    )
    news_refresh_seconds: Decimal = Field(
        default=Decimal("900"),
        validation_alias="NEWS_REFRESH_SECONDS",
        gt=0,
    )
    news_max_age_seconds: Decimal = Field(
        default=Decimal("3600"),
        validation_alias="NEWS_MAX_AGE_SECONDS",
        gt=0,
    )
    historical_calendar_provider: Literal["eodhd"] = Field(
        default="eodhd", validation_alias="HISTORICAL_CALENDAR_PROVIDER"
    )
    historical_calendar_url: AnyHttpUrl = Field(
        default=AnyHttpUrl("https://eodhd.com/api/economic-events"),
        validation_alias="HISTORICAL_CALENDAR_URL",
    )
    historical_calendar_api_key: str | None = Field(
        default=None, validation_alias="HISTORICAL_CALENDAR_API_KEY"
    )
    historical_calendar_archive_path: Path = Field(
        default=Path("data/historical_calendar"),
        validation_alias="HISTORICAL_CALENDAR_ARCHIVE_PATH",
    )
    openai_api_key: str | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    ollama_base_url: AnyHttpUrl | None = Field(
        default=None, validation_alias="OLLAMA_BASE_URL"
    )
    ollama_model: str = Field(
        default="llama3.1", validation_alias="OLLAMA_MODEL", min_length=1
    )
    postgres_db: str | None = Field(default=None, validation_alias="POSTGRES_DB")
    postgres_user: str | None = Field(default=None, validation_alias="POSTGRES_USER")
    postgres_password: str | None = Field(
        default=None, validation_alias="POSTGRES_PASSWORD"
    )

    @property
    def session_database_path(self) -> Path:
        """Return the canonical SQLite path with legacy compatibility."""
        if (
            "sqlite_db_path" in self.model_fields_set
            and "data_dir" not in self.model_fields_set
        ):
            return self.sqlite_db_path
        return self.data_dir / "session_metrics.db"

    @model_validator(mode="after")
    def enforce_safe_runtime(self) -> Settings:
        """Reject missing safety guarantees rather than inferring a mode."""
        process_login = os.getenv("BROKER_ACCOUNT_ID") or os.getenv("EXNESS_LOGIN")
        process_server = os.getenv("BROKER_SERVER") or os.getenv("EXNESS_SERVER")
        process_password = os.getenv("BROKER_TOKEN") or os.getenv("EXNESS_PASSWORD")
        if process_login:
            self.broker_account_id = process_login
        if process_server:
            self.broker_server = process_server
        if process_password:
            self.broker_token = process_password
        fields_set = self.model_fields_set
        if self.broker_type != self.provider:
            raise ValueError("BROKER_TYPE and BROKER_PROVIDER disagree")
        if "default_symbol" in fields_set:
            if "instrument" in fields_set and self.instrument != self.default_symbol:
                raise ValueError("DEFAULT_SYMBOL and INSTRUMENT disagree")
            if "instrument" not in fields_set:
                self.instrument = self.default_symbol
        if "exness_symbol_suffix" in fields_set and not self.instrument.endswith(
            self.exness_symbol_suffix
        ):
            raise ValueError("EXNESS_SYMBOL_SUFFIX does not match INSTRUMENT")
        if "max_spread_tolerance" in fields_set:
            if (
                "max_spread" in fields_set
                and self.max_spread != self.max_spread_tolerance
            ):
                raise ValueError("MAX_SPREAD and MAX_SPREAD_TOLERANCE disagree")
            if "max_spread" not in fields_set:
                self.max_spread = self.max_spread_tolerance
        if "sqlite_db_path" in fields_set and "data_dir" not in fields_set:
            self.data_dir = self.sqlite_db_path.parent
        if not self.paper_trading or self.live_trading:
            raise ValueError(
                "paper trading must be true and live trading must be false"
            )
        if self.trading_mode == "LIVE":
            raise ValueError("live trading mode is unavailable")
        if self.broker_environment not in {"paper", "demo"}:
            raise ValueError("broker environment must be paper or demo")
        validate_demo_endpoint(self.broker_endpoint)
        if self.exness_bridge_host is not None:
            host = self.exness_bridge_host.lower().rstrip(".")
            if host not in DEMO_BRIDGE_HOSTS:
                raise ValueError(
                    "EXNESS_BRIDGE_HOST is not an allowlisted demo endpoint host"
                )
        if self.trading_mode == "BROKER_DEMO":
            if self.broker_environment != "demo":
                raise ValueError("BROKER_DEMO requires BROKER_ENV=demo")
            if self.broker_endpoint is None and self.exness_bridge_host is None:
                raise ValueError("BROKER_DEMO endpoint is missing")
            if not self.broker_token:
                raise ValueError("BROKER_DEMO token is missing")
            account_id = self.broker_account or self.broker_account_id
            if not account_id:
                raise ValueError("BROKER_DEMO account is missing")
            if account_id != DEMO_ACCOUNT_ID:
                raise ValueError("BROKER_DEMO account is not allowlisted")
        if self.forward_test_window_start_utc >= self.forward_test_window_end_utc:
            raise ValueError("forward-test window must end after it starts")
        if self.forward_test_enabled and self.trading_mode != "BROKER_DEMO":
            raise ValueError("FORWARD_TEST_ENABLED requires BROKER_DEMO")
        if self.alert_route == "webhook" and self.alert_webhook_url is None:
            raise ValueError("ALERT_ROUTE=webhook requires ALERT_WEBHOOK_URL")
        if self.alert_webhook_url is not None:
            parsed_alert_url = urlparse(str(self.alert_webhook_url))
            if (
                parsed_alert_url.scheme != "https"
                or parsed_alert_url.username is not None
                or parsed_alert_url.password is not None
                or parsed_alert_url.hostname is None
            ):
                raise ValueError(
                    "ALERT_WEBHOOK_URL must be an HTTPS URL without userinfo"
                )
        if self.daily_drawdown_limit <= 0:
            raise ValueError("daily drawdown limit must be positive")
        validate_instrument(self.instrument)
        return self
