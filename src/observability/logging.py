"""Sanitize and emit structured execution diagnostics."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from datetime import datetime
from decimal import Decimal
from typing import Literal
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field

_SENSITIVE_NAMES = ("key", "token", "secret", "password", "authorization", "account_id")

LifecycleSignal = str | None
NewsBlackoutStatus = Literal["BLOCKED", "CLEAR", "NOT_CHECKED"]
LLMDecision = Literal["CONFIRM", "REJECT", "ADJUST_RISK"] | None
ExecutionStatus = Literal[
    "ACCEPTED",
    "FILLED",
    "PARTIALLY_FILLED",
    "REJECTED",
    "CANCELLED",
    "EXPIRED",
    "ORDER_NOT_FOUND",
    "UNKNOWN",
    "NOT_ATTEMPTED",
]


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


AlertWebhookSender = Callable[[str, bytes, float], None]


def _post_alert_webhook(url: str, payload: bytes, timeout: float) -> None:
    """Send one sanitized JSON alert without exposing the destination in logs."""
    request = Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=timeout):  # noqa: S310
        return


def _valid_alert_webhook_url(url: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.scheme == "https"
        and parsed.hostname is not None
        and parsed.username is None
        and parsed.password is None
    )


def emit_alert(
    logger: logging.Logger,
    condition: str,
    *,
    message: str,
    fields: Mapping[str, str] | None = None,
    secrets: tuple[str, ...] = (),
    webhook_url: str | None = None,
    webhook_timeout_seconds: float = 5.0,
    webhook_sender: AlertWebhookSender | None = None,
) -> dict[str, str]:
    """Emit a sanitized alert and optionally deliver it to an HTTPS webhook."""
    safe = sanitize_fields(
        {"condition": condition, "message": message, **dict(fields or {})},
        secrets=secrets,
    )
    logger.warning("alert=%s", json.dumps(safe, sort_keys=True))
    if webhook_url is None:
        return safe
    if (
        not _valid_alert_webhook_url(webhook_url)
        or webhook_timeout_seconds <= 0
        or webhook_timeout_seconds > 30
    ):
        logger.error("alert_delivery_failed route=webhook error=invalid_configuration")
        return safe
    payload = json.dumps(
        {"text": f"[{condition}] {safe['message']}", "alert": safe},
        sort_keys=True,
    ).encode("utf-8")
    try:
        (webhook_sender or _post_alert_webhook)(
            webhook_url, payload, webhook_timeout_seconds
        )
    except (OSError, ValueError):
        logger.error("alert_delivery_failed route=webhook error=transport")
    return safe
