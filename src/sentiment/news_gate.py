"""Fail-closed blackout checks for high-impact macroeconomic news."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Protocol, runtime_checkable

from domain.models import NewsEvent


@runtime_checkable
class NewsEventRecord(Protocol):
    """Minimum event shape accepted by the blackout gate."""

    published_at: datetime
    impact: str | None


NewsEventInput = NewsEvent | NewsEventRecord | Mapping[str, object]


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _event_time(event: NewsEventInput) -> datetime | None:
    if isinstance(event, NewsEvent):
        return event.published_at
    if isinstance(event, NewsEventRecord):
        return event.published_at
    for key in ("event_time", "timestamp", "published_at", "time", "datetime"):
        candidate = event.get(key)
        if isinstance(candidate, datetime):
            return candidate
    return None


def _impact(event: NewsEventInput) -> str | None:
    if isinstance(event, NewsEvent):
        return event.impact
    if isinstance(event, NewsEventRecord):
        return event.impact
    value = event.get("impact")
    return value if isinstance(value, str) else None


def is_in_news_blackout(
    current_time: datetime,
    news_events: Sequence[NewsEventInput],
    buffer_minutes: int = 30,
) -> bool:
    """Return true inside the inclusive buffer around any high-impact event."""
    if buffer_minutes < 0:
        raise ValueError("buffer_minutes must be non-negative")
    current = _utc(current_time)
    buffer = timedelta(minutes=buffer_minutes)
    for event in news_events:
        event_time = _event_time(event)
        impact = _impact(event)
        if event_time is None or impact is None or impact.upper() != "HIGH":
            continue
        event_utc = _utc(event_time)
        if event_utc - buffer <= current <= event_utc + buffer:
            return True
    return False
