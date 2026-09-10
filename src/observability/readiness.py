"""Startup checks for safe daily paper/demo forward tests."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from config.settings import Settings
from execution.broker_adapter import BrokerAdapter, BrokerConnectionError
from observability.logging import log_session_startup


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
    broker: BrokerAdapter,
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
