"""Pure RSI, moving-average, and ATR calculations."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime
from typing import Sequence

from domain.models import MarketCandle


@dataclass(frozen=True)
class IndicatorPoint:
    timestamp: datetime
    close: Decimal
    rsi: Decimal | None
    moving_average_fast: Decimal | None
    moving_average_slow: Decimal | None
    atr: Decimal | None

    @property
    def ready(self) -> bool:
        return all(
            value is not None
            for value in (
                self.rsi,
                self.moving_average_fast,
                self.moving_average_slow,
                self.atr,
            )
        )


@dataclass(frozen=True)
class IndicatorFrame:
    points: tuple[IndicatorPoint, ...]
    rsi_period: int
    fast_period: int
    slow_period: int
    atr_period: int

    @property
    def latest(self) -> IndicatorPoint | None:
        return self.points[-1] if self.points else None

    @property
    def previous(self) -> IndicatorPoint | None:
        return self.points[-2] if len(self.points) > 1 else None

    @property
    def ready(self) -> bool:
        return self.latest is not None and self.latest.ready


def _sma(values: Sequence[Decimal], period: int, index: int) -> Decimal | None:
    if index + 1 < period:
        return None
    return sum(values[index + 1 - period : index + 1], Decimal(0)) / Decimal(period)


def _rsi(closes: Sequence[Decimal], period: int, index: int) -> Decimal | None:
    if index < period:
        return None
    gains = [
        max(closes[position] - closes[position - 1], Decimal(0))
        for position in range(index - period + 1, index + 1)
    ]
    losses = [
        max(closes[position - 1] - closes[position], Decimal(0))
        for position in range(index - period + 1, index + 1)
    ]
    average_gain = sum(gains, Decimal(0)) / Decimal(period)
    average_loss = sum(losses, Decimal(0)) / Decimal(period)
    if average_loss == 0:
        return Decimal(100) if average_gain > 0 else Decimal(50)
    return Decimal(100) - (Decimal(100) / (Decimal(1) + average_gain / average_loss))


def _atr(candles: Sequence[MarketCandle], period: int, index: int) -> Decimal | None:
    if index < period:
        return None
    true_ranges: list[Decimal] = []
    for position in range(index - period + 1, index + 1):
        previous_close = candles[position - 1].close
        candle = candles[position]
        true_ranges.append(
            max(
                candle.high - candle.low,
                abs(candle.high - previous_close),
                abs(candle.low - previous_close),
            )
        )
    return sum(true_ranges, Decimal(0)) / Decimal(period)


def calculate_indicators(
    candles: Sequence[MarketCandle],
    *,
    rsi_period: int = 14,
    fast_period: int = 5,
    slow_period: int = 20,
    atr_period: int = 14,
) -> IndicatorFrame:
    """Return one point per candle without fabricated warm-up values."""
    if min(rsi_period, fast_period, slow_period, atr_period) <= 0:
        raise ValueError("indicator periods must be positive")
    closes = [candle.close for candle in candles]
    points = tuple(
        IndicatorPoint(
            timestamp=candle.timestamp,
            close=candle.close,
            rsi=_rsi(closes, rsi_period, index),
            moving_average_fast=_sma(closes, fast_period, index),
            moving_average_slow=_sma(closes, slow_period, index),
            atr=_atr(candles, atr_period, index),
        )
        for index, candle in enumerate(candles)
    )
    return IndicatorFrame(points, rsi_period, fast_period, slow_period, atr_period)
