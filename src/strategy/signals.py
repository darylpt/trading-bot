"""RSI and moving-average crossover signal generation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from domain.models import TechnicalSignal
from strategy.indicators import IndicatorFrame


@dataclass(frozen=True)
class SignalConfig:
    long_rsi_max: Decimal = Decimal("70")
    short_rsi_min: Decimal = Decimal("30")


def generate_signal(
    indicators: IndicatorFrame,
    timestamp: datetime | None = None,
    *,
    config: SignalConfig | None = None,
) -> TechnicalSignal | None:
    """Emit at most one signal on a completed bullish or bearish crossover."""
    latest = indicators.latest
    previous = indicators.previous
    if latest is None or previous is None or not latest.ready or not previous.ready:
        return None
    selected = config or SignalConfig()
    assert latest.rsi is not None
    assert previous.moving_average_fast is not None
    assert previous.moving_average_slow is not None
    assert latest.moving_average_fast is not None
    assert latest.moving_average_slow is not None
    if (
        previous.moving_average_fast <= previous.moving_average_slow
        and latest.moving_average_fast > latest.moving_average_slow
        and latest.rsi <= selected.long_rsi_max
    ):
        direction: Literal["LONG", "SHORT"] = "LONG"
    elif (
        previous.moving_average_fast >= previous.moving_average_slow
        and latest.moving_average_fast < latest.moving_average_slow
        and latest.rsi >= selected.short_rsi_min
    ):
        direction = "SHORT"
    else:
        return None
    return TechnicalSignal(
        instrument="EUR_USD",
        direction=direction,
        signal_timestamp=timestamp or latest.timestamp,
        reference_price=latest.close,
        rsi=latest.rsi,
        moving_average_fast=latest.moving_average_fast,
        moving_average_slow=latest.moving_average_slow,
    )
