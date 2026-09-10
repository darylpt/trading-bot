"""Simple moving-average crossover strategy for paper-trading ticks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Literal

from domain.models import MarketCandle
from strategy.market_data import load_csv_candles

SignalAction = Literal["BUY", "SELL", "HOLD"]


@dataclass(frozen=True)
class Signal:
    """Typed strategy decision and the moving-average values behind it."""

    action: SignalAction
    timestamp: datetime
    instrument: str = "EUR_USD"
    reference_price: Decimal | None = None
    fast_average: Decimal | None = None
    slow_average: Decimal | None = None


def _moving_average(values: tuple[Decimal, ...], period: int) -> Decimal | None:
    if len(values) < period:
        return None
    return sum(values[-period:], Decimal(0)) / Decimal(period)


def moving_average_signal(
    candles: tuple[MarketCandle, ...],
    *,
    fast_period: int = 5,
    slow_period: int = 20,
    current_time: datetime | None = None,
) -> Signal:
    """Return BUY/SELL only on a fresh fast/slow moving-average crossover."""
    if fast_period <= 0 or slow_period <= 0 or fast_period >= slow_period:
        raise ValueError("moving-average periods must be positive and fast < slow")

    timestamp = current_time or (
        candles[-1].timestamp if candles else datetime.now(timezone.utc)
    )
    instrument = candles[-1].instrument if candles else "EUR_USD"
    reference_price = candles[-1].close if candles else None
    closes = tuple(candle.close for candle in candles)
    previous_closes = closes[:-1]
    previous_fast = _moving_average(previous_closes, fast_period)
    previous_slow = _moving_average(previous_closes, slow_period)
    latest_fast = _moving_average(closes, fast_period)
    latest_slow = _moving_average(closes, slow_period)

    if (
        previous_fast is None
        or previous_slow is None
        or latest_fast is None
        or latest_slow is None
    ):
        return Signal(
            action="HOLD",
            timestamp=timestamp,
            instrument=instrument,
            reference_price=reference_price,
            fast_average=latest_fast,
            slow_average=latest_slow,
        )

    action: SignalAction = "HOLD"
    if previous_fast <= previous_slow and latest_fast > latest_slow:
        action = "BUY"
    elif previous_fast >= previous_slow and latest_fast < latest_slow:
        action = "SELL"
    return Signal(
        action=action,
        timestamp=timestamp,
        instrument=instrument,
        reference_price=reference_price,
        fast_average=latest_fast,
        slow_average=latest_slow,
    )


def evaluate_market_data(
    path: str | Path,
    *,
    fast_period: int = 5,
    slow_period: int = 20,
    current_time: datetime | None = None,
) -> Signal:
    """Load OHLCV CSV data and evaluate its latest moving-average crossover."""
    candles = load_csv_candles(path, minimum_history=slow_period + 1)
    return moving_average_signal(
        candles,
        fast_period=fast_period,
        slow_period=slow_period,
        current_time=current_time,
    )
