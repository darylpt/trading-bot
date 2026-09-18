"""Startup checks for safe daily paper/demo forward tests."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from config.settings import Settings
from execution.broker_adapter import BaseBroker, BrokerConnectionError
from observability.logging import log_session_startup
from persistence.sqlite import SQLiteRepository


class ForwardTestReadiness(BaseModel):
    """Validated result of the paper broker/session readiness check."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["READY"]
    provider: str = Field(min_length=1)
    environment: Literal["PAPER", "DEMO"]
    account_equity: Decimal = Field(gt=0)
    checked_at: datetime


def check_forward_test_readiness(
    settings: Settings,
    broker: BaseBroker,
    *,
    logger: logging.Logger | None = None,
    checked_at: datetime | None = None,
) -> ForwardTestReadiness:
    """Authenticate, validate account equity, and log a safe session start."""
    if not settings.paper_trading or settings.live_trading:
        raise BrokerConnectionError("forward tests require paper trading only")
    if broker.environment != settings.broker_environment.upper():
        raise BrokerConnectionError("broker environment does not match settings")
    if broker.provider != settings.provider:
        raise BrokerConnectionError("broker provider does not match settings")
    if settings.broker_token is None or not settings.broker_token:
        raise BrokerConnectionError("paper broker credential is missing")
    if broker.api_token != settings.broker_token:
        raise BrokerConnectionError("paper broker credential is not configured")

    account = broker.get_account_snapshot()
    if account.equity <= 0:
        raise BrokerConnectionError("paper broker equity is invalid")
    timestamp = checked_at or datetime.now(timezone.utc)
    result = ForwardTestReadiness(
        status="READY",
        provider=broker.provider,
        environment=account.environment,
        account_equity=account.equity,
        checked_at=timestamp,
    )
    log_session_startup(
        logger or logging.getLogger(__name__),
        timestamp=timestamp,
        provider=result.provider,
        environment=result.environment,
        account_equity=result.account_equity,
    )
    return result


class BrokerDemoReadiness(BaseModel):
    """Complete fail-closed broker-demo readiness result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["READY"]
    mode: Literal["BROKER_DEMO"]
    provider: str = Field(min_length=1)
    environment: Literal["DEMO"]
    instrument: str = Field(min_length=1)
    account_equity: Decimal = Field(gt=0)
    available_margin: Decimal = Field(ge=0)
    session_open: bool
    quote_spread: Decimal = Field(gt=0)
    candle_count: int = Field(gt=0)
    database_writable: bool
    clock_drift_seconds: Decimal = Field(ge=0)
    checked_at: datetime


def check_broker_demo_readiness(
    settings: Settings,
    broker: BaseBroker,
    repository: SQLiteRepository,
    *,
    instrument: str,
    timeframe: Literal["15m", "1h"] = "15m",
    history_limit: int = 256,
    logger: logging.Logger | None = None,
    checked_at: datetime | None = None,
) -> BrokerDemoReadiness:
    """Run every read-only prerequisite before enabling demo entries."""
    if settings.trading_mode != "BROKER_DEMO":
        raise BrokerConnectionError("broker demo readiness requires BROKER_DEMO mode")
    if broker.environment != "DEMO" or broker.provider != settings.provider:
        raise BrokerConnectionError("broker identity does not match settings")
    if not settings.broker_token or broker.api_token != settings.broker_token:
        raise BrokerConnectionError("broker demo credential is not configured")

    account = broker.get_account_snapshot()
    account_reference_time = (
        _utc(checked_at) if checked_at is not None else datetime.now(timezone.utc)
    )
    metadata = broker.get_instrument_metadata(instrument)
    session = broker.get_trading_session(instrument)
    quote = broker.get_market_quote(instrument)
    candles = broker.get_historical_candles(
        instrument, timeframe=timeframe, limit=history_limit
    )
    timestamp = (
        _utc(checked_at) if checked_at is not None else datetime.now(timezone.utc)
    )
    try:
        repository.connection.execute("SELECT 1").fetchone()
    except sqlite3.Error as exc:
        raise BrokerConnectionError("readiness database is not writable") from exc

    candle_period = timedelta(minutes=15 if timeframe == "15m" else 60)
    candle_close_at = _utc(candles[-1].timestamp) + candle_period
    timestamps = (
        account.captured_at,
        metadata.captured_at,
        session.captured_at,
        quote.observed_at,
        candle_close_at,
    )
    max_age = float(settings.max_data_age_seconds)
    max_future_age = float(settings.max_clock_drift_seconds)
    for observed in timestamps:
        age = (timestamp - _utc(observed)).total_seconds()
        if age < -max_future_age or age > max_age:
            raise BrokerConnectionError(
                "broker readiness data is stale or from the future"
            )
    drift = abs((account_reference_time - _utc(account.captured_at)).total_seconds())
    if drift > float(settings.max_clock_drift_seconds):
        raise BrokerConnectionError("broker clock drift exceeds configured limit")
    if not session.is_open:
        raise BrokerConnectionError("instrument trading session is closed")
    if quote.spread <= 0:
        raise BrokerConnectionError("broker quote spread is invalid")
    if metadata.instrument != instrument or session.instrument != instrument:
        raise BrokerConnectionError("broker instrument response does not match request")

    result = BrokerDemoReadiness(
        status="READY",
        mode="BROKER_DEMO",
        provider=broker.provider,
        environment="DEMO",
        instrument=instrument,
        account_equity=account.equity,
        available_margin=max(account.balance - account.margin, Decimal("0")),
        session_open=session.is_open,
        quote_spread=quote.spread,
        candle_count=len(candles),
        database_writable=True,
        clock_drift_seconds=Decimal(str(drift)),
        checked_at=timestamp,
    )
    log_session_startup(
        logger or logging.getLogger(__name__),
        timestamp=timestamp,
        provider=result.provider,
        environment=result.environment,
        account_equity=result.account_equity,
    )
    return result


class TradingReadiness(BaseModel):
    """Separate trading readiness from process liveness."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    state: Literal["NOT_READY", "READY", "HALTED"]
    blockers: tuple[str, ...]
    checked_at: datetime


def evaluate_trading_readiness(
    *,
    mode: str,
    authenticated: bool,
    market_data_fresh: bool,
    account_valid: bool,
    instrument_valid: bool,
    database_writable: bool,
    clock_within_limit: bool,
    risk_configured: bool,
    reconciled: bool,
    halted: bool,
    process_healthy: bool = True,
    checked_at: datetime | None = None,
) -> TradingReadiness:
    """Compute a deterministic fail-closed readiness state from dependency checks."""
    checks = (
        ("MODE", mode in {"SIMULATED", "BROKER_DEMO"}),
        ("AUTHENTICATION", authenticated),
        ("MARKET_DATA", market_data_fresh),
        ("ACCOUNT", account_valid),
        ("INSTRUMENT", instrument_valid),
        ("DATABASE", database_writable),
        ("CLOCK", clock_within_limit),
        ("RISK_CONFIGURATION", risk_configured),
        ("RECONCILIATION", reconciled),
        ("PROCESS", process_healthy),
    )
    blockers = tuple(name for name, passed in checks if not passed)
    state: Literal["NOT_READY", "READY", "HALTED"]
    if halted:
        state = "HALTED"
    elif blockers:
        state = "NOT_READY"
    else:
        state = "READY"
    return TradingReadiness(
        state=state,
        blockers=blockers,
        checked_at=_utc(checked_at or datetime.now(timezone.utc)),
    )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
