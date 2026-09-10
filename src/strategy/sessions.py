"""24/5 session, market-boundary, and stale-data checks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone


@dataclass(frozen=True)
class SessionPolicy:
    friday_close_utc: time = time(22, 0)
    monday_open_utc: time = time(0, 0)
    stale_after: timedelta = timedelta(minutes=20)
    boundary_window: timedelta = timedelta(minutes=30)


def is_market_open(timestamp: datetime, policy: SessionPolicy | None = None) -> bool:
    policy = policy or SessionPolicy()
    current = timestamp.astimezone(timezone.utc)
    if current.weekday() == 5:
        return False
    if current.weekday() == 6:
        return False
    if current.weekday() == 0 and current.time() < policy.monday_open_utc:
        return False
    if current.weekday() == 4 and current.time() >= policy.friday_close_utc:
        return False
    return True


def is_stale(
    observed_at: datetime, now: datetime, policy: SessionPolicy | None = None
) -> bool:
    policy = policy or SessionPolicy()
    return (
        now.astimezone(timezone.utc) - observed_at.astimezone(timezone.utc)
        > policy.stale_after
    )


def is_boundary_window(
    timestamp: datetime, policy: SessionPolicy | None = None
) -> bool:
    policy = policy or SessionPolicy()
    current = timestamp.astimezone(timezone.utc)
    close = datetime.combine(current.date(), policy.friday_close_utc, timezone.utc)
    open_time = datetime.combine(current.date(), policy.monday_open_utc, timezone.utc)
    return (
        current.weekday() == 4 and close - policy.boundary_window <= current < close
    ) or (
        current.weekday() == 0
        and open_time <= current < open_time + policy.boundary_window
    )


def can_enter(
    *, observed_at: datetime, now: datetime, policy: SessionPolicy | None = None
) -> bool:
    policy = policy or SessionPolicy()
    return is_market_open(now, policy) and not is_stale(observed_at, now, policy)
