"""Sanitize and emit structured execution diagnostics."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

_SENSITIVE_NAMES = ("key", "token", "secret", "password", "authorization", "account_id")

LifecycleSignal = str | None
NewsBlackoutStatus = Literal["BLOCKED", "CLEAR", "NOT_CHECKED"]
LLMDecision = Literal["CONFIRM", "REJECT", "ADJUST_RISK"] | None
ExecutionStatus = Literal["ACCEPTED", "REJECTED", "UNKNOWN", "NOT_ATTEMPTED"]


class TradeLifecycleEvent(BaseModel):
    """The stable JSON schema for one trade lifecycle observation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    timestamp: datetime = Field(serialization_alias="TIMESTAMP")
    signal: LifecycleSignal = Field(serialization_alias="SIGNAL")
    news_blackout_status: NewsBlackoutStatus = Field(
        serialization_alias="NEWS_BLACKOUT_STATUS"
    )
    llm_decision: LLMDecision = Field(serialization_alias="LLM_DECISION")
    risk_size: Decimal | None = Field(
        default=None, ge=0, serialization_alias="RISK_SIZE"
    )
    broker_latency_ms: int | None = Field(
        default=None, ge=0, serialization_alias="BROKER_LATENCY_MS"
    )
    execution_status: ExecutionStatus = Field(serialization_alias="EXECUTION_STATUS")


def log_trade_lifecycle_event(
    logger: logging.Logger,
    *,
    timestamp: datetime,
    signal: LifecycleSignal,
    news_blackout_status: NewsBlackoutStatus,
    llm_decision: LLMDecision,
    risk_size: Decimal | None,
    broker_latency_ms: int | None,
    execution_status: ExecutionStatus,
) -> TradeLifecycleEvent:
    """Validate and emit one lifecycle event as a JSON log line."""
    event = TradeLifecycleEvent(
        timestamp=timestamp,
        signal=signal,
        news_blackout_status=news_blackout_status,
        llm_decision=llm_decision,
        risk_size=risk_size,
        broker_latency_ms=broker_latency_ms,
        execution_status=execution_status,
    )
    logger.info(event.model_dump_json(by_alias=True))
    return event


def log_session_startup(
    logger: logging.Logger,
    *,
    timestamp: datetime,
    provider: str,
    environment: str,
    account_equity: Decimal,
) -> None:
    """Emit a sanitized JSON record for a forward-test session start."""
    logger.info(
        json.dumps(
            {
                "event": "FORWARD_TEST_SESSION_STARTED",
                "timestamp": timestamp.isoformat(),
                "provider": provider,
                "environment": environment,
                "account_equity": str(account_equity),
            },
            sort_keys=True,
        )
    )


def sanitize_fields(
    fields: Mapping[str, str], *, secrets: tuple[str, ...] = ()
) -> dict[str, str]:
    """Return safe fields with credential-like keys and values redacted."""
    result: dict[str, str] = {}
    for name, value in fields.items():
        lowered = name.lower()
        if any(marker in lowered for marker in _SENSITIVE_NAMES) or any(
            secret and secret in value for secret in secrets
        ):
            result[name] = "[REDACTED]"
        else:
            result[name] = value
    return result


def record_event(
    logger: logging.Logger,
    event_type: str,
    fields: Mapping[str, str],
    *,
    secrets: tuple[str, ...] = (),
) -> dict[str, str]:
    """Log and return only sanitized event fields."""
    safe = sanitize_fields(fields, secrets=secrets)
    logger.info("event=%s fields=%s", event_type, safe)
    return safe
