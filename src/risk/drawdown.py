"""Daily drawdown halt state machine."""

from __future__ import annotations

from datetime import datetime, time, timezone
from decimal import Decimal

from domain.models import AccountSnapshot, DailyDrawdownState


def evaluate_daily_drawdown(
    state: DailyDrawdownState, *, now: datetime | None = None
) -> DailyDrawdownState:
    """Activate a permanent-for-the-day halt at or above the configured loss."""
    loss = -(state.realized_pnl + state.unrealized_pnl)
    drawdown_fraction = max(loss, Decimal("0")) / state.baseline_equity
    if drawdown_fraction < state.drawdown_limit or state.halt_active:
        return state
    return state.model_copy(
        update={
            "halt_active": True,
            "halted_at": now or datetime.now(timezone.utc),
            "halt_reason": "daily drawdown limit reached",
        }
    )


def reset_daily_drawdown(
    state: DailyDrawdownState,
    snapshot: AccountSnapshot,
    *,
    now: datetime,
    boundary: time = time(0, 0),
) -> DailyDrawdownState:
    """Reset only after the trading-day boundary and a fresh account snapshot."""
    current = now.astimezone(timezone.utc)
    captured = snapshot.captured_at.astimezone(timezone.utc)
    if current.time() < boundary or current.date() <= state.trading_day:
        raise ValueError("trading-day boundary has not been reached")
    if captured < current:
        raise ValueError("account snapshot is not fresh")
    return DailyDrawdownState(
        trading_day=current.date(),
        baseline_equity=snapshot.equity,
        realized_pnl=Decimal("0"),
        unrealized_pnl=Decimal("0"),
        drawdown_limit=state.drawdown_limit,
    )
