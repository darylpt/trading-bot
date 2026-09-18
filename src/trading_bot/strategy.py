"""Simple moving-average crossover strategy for paper-trading ticks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable, Literal, Protocol

from domain.models import MarketCandle
from strategy.market_data import load_csv_candles

SignalAction = Literal["BUY", "SELL", "HOLD"]


@dataclass(frozen=True)
class Signal:
    """Typed strategy decision, rationale, and indicator values."""

    action: SignalAction
    timestamp: datetime
    instrument: str = "EUR_USD"
    strategy_name: str = "ema_crossover"
    reference_price: Decimal | None = None
    fast_average: Decimal | None = None
    slow_average: Decimal | None = None
    rationale: str = ""

    @property
    def fast_ema(self) -> Decimal | None:
        """Expose the fast indicator using the dashboard/API terminology."""
        return self.fast_average

    @property
    def slow_ema(self) -> Decimal | None:
        """Expose the slow indicator using the dashboard/API terminology."""
        return self.slow_average

    @property
    def distance_to_crossover(self) -> Decimal | None:
        """Return fast-minus-slow distance; zero means the averages meet."""
        if self.fast_average is None or self.slow_average is None:
            return None
        return self.fast_average - self.slow_average


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
    instrument: str = "EUR_USD",
) -> Signal:
    """Return BUY/SELL only on a fresh fast/slow moving-average crossover."""
    if fast_period <= 0 or slow_period <= 0 or fast_period >= slow_period:
        raise ValueError("moving-average periods must be positive and fast < slow")

    timestamp = current_time or (
        candles[-1].timestamp if candles else datetime.now(timezone.utc)
    )
    instrument = candles[-1].instrument if candles else instrument
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
        available = (
            f"Fast EMA ({latest_fast})"
            if latest_fast is not None
            else "Fast EMA unavailable"
        )
        rationale = f"{available} — Insufficient data for crossover evaluation"
        return Signal(
            action="HOLD",
            timestamp=timestamp,
            instrument=instrument,
            reference_price=reference_price,
            fast_average=latest_fast,
            slow_average=latest_slow,
            rationale=rationale,
        )

    action: SignalAction = "HOLD"
    if previous_fast <= previous_slow and latest_fast > latest_slow:
        action = "BUY"
        rationale = (
            f"Fast EMA ({latest_fast}) > Slow EMA ({latest_slow}) — Bullish crossover"
        )
    elif previous_fast >= previous_slow and latest_fast < latest_slow:
        action = "SELL"
        rationale = (
            f"Fast EMA ({latest_fast}) < Slow EMA ({latest_slow}) — Bearish crossover"
        )
    elif latest_fast < latest_slow:
        rationale = (
            f"Fast EMA ({latest_fast}) < Slow EMA ({latest_slow}) "
            "— No bullish crossover"
        )
    elif latest_fast > latest_slow:
        rationale = (
            f"Fast EMA ({latest_fast}) > Slow EMA ({latest_slow}) "
            "— No bearish crossover"
        )
    else:
        rationale = (
            f"Fast EMA ({latest_fast}) = Slow EMA ({latest_slow}) — No crossover"
        )
    return Signal(
        action=action,
        timestamp=timestamp,
        instrument=instrument,
        reference_price=reference_price,
        fast_average=latest_fast,
        slow_average=latest_slow,
        rationale=rationale,
    )


class Strategy(Protocol):
    """Common interface implemented by every registered strategy."""

    name: str

    def evaluate(
        self,
        candles: tuple[MarketCandle, ...],
        *,
        current_time: datetime | None = None,
        instrument: str = "EUR_USD",
    ) -> Signal: ...


class EmaCrossoverStrategy:
    name = "ema_crossover"

    def evaluate(
        self,
        candles: tuple[MarketCandle, ...],
        *,
        current_time: datetime | None = None,
        instrument: str = "EUR_USD",
    ) -> Signal:
        signal = moving_average_signal(
            candles, current_time=current_time, instrument=instrument
        )
        return Signal(
            action=signal.action,
            timestamp=signal.timestamp,
            instrument=signal.instrument,
            strategy_name=self.name,
            reference_price=signal.reference_price,
            fast_average=signal.fast_average,
            slow_average=signal.slow_average,
            rationale=signal.rationale,
        )


class RsiMeanReversionStrategy:
    name = "rsi_mean_reversion"

    def evaluate(
        self,
        candles: tuple[MarketCandle, ...],
        *,
        current_time: datetime | None = None,
        instrument: str = "EUR_USD",
    ) -> Signal:
        timestamp = current_time or (
            candles[-1].timestamp if candles else datetime.now(timezone.utc)
        )
        instrument = candles[-1].instrument if candles else instrument
        price = candles[-1].close if candles else None
        changes = tuple(
            candles[index].close - candles[index - 1].close
            for index in range(1, len(candles))
        )
        period = 14
        if len(changes) < period:
            rationale = "RSI unavailable — Insufficient data for mean reversion"
            return Signal(
                "HOLD", timestamp, instrument, self.name, price, None, None, rationale
            )
        window = changes[-period:]
        gains = sum((change for change in window if change > 0), Decimal(0))
        losses = sum((-change for change in window if change < 0), Decimal(0))
        rsi = (
            Decimal("100")
            if losses == 0
            else Decimal("100") - (Decimal("100") / (Decimal("1") + gains / losses))
        )
        action: SignalAction = "BUY" if rsi < 30 else "SELL" if rsi > 70 else "HOLD"
        rationale = f"RSI ({rsi:.2f}) — " + (
            "Oversold mean-reversion BUY"
            if action == "BUY"
            else "Overbought mean-reversion SELL"
            if action == "SELL"
            else "Neutral range — HOLD"
        )
        return Signal(
            "HOLD" if price is None else action,
            timestamp,
            instrument,
            self.name,
            price,
            rsi,
            Decimal("50"),
            rationale,
        )


class BreakoutChannelStrategy:
    name = "breakout_channel"

    def evaluate(
        self,
        candles: tuple[MarketCandle, ...],
        *,
        current_time: datetime | None = None,
        instrument: str = "EUR_USD",
    ) -> Signal:
        timestamp = current_time or (
            candles[-1].timestamp if candles else datetime.now(timezone.utc)
        )
        instrument = candles[-1].instrument if candles else instrument
        price = candles[-1].close if candles else None
        window = candles[-21:-1]
        if len(window) < 20 or price is None:
            rationale = "Breakout channel unavailable — Insufficient data"
            return Signal(
                "HOLD", timestamp, instrument, self.name, price, None, None, rationale
            )
        upper = max(candle.high for candle in window)
        lower = min(candle.low for candle in window)
        action: SignalAction = (
            "BUY" if price > upper else "SELL" if price < lower else "HOLD"
        )
        rationale = f"Price ({price}) vs channel [{lower}, {upper}] — " + (
            "Upper breakout BUY"
            if action == "BUY"
            else "Lower breakout SELL"
            if action == "SELL"
            else "Inside channel — HOLD"
        )
        return Signal(
            action, timestamp, instrument, self.name, price, upper, lower, rationale
        )


_STRATEGY_FACTORIES: dict[str, Callable[[], Strategy]] = {
    "ema_crossover": EmaCrossoverStrategy,
    "rsi_mean_reversion": RsiMeanReversionStrategy,
    "breakout_channel": BreakoutChannelStrategy,
}


def get_strategy(name: str) -> Strategy:
    try:
        return _STRATEGY_FACTORIES[name]()
    except KeyError as exc:
        raise ValueError(f"unsupported strategy: {name}") from exc


def evaluate_strategy(
    name: str,
    candles: tuple[MarketCandle, ...],
    *,
    current_time: datetime | None = None,
    instrument: str = "EUR_USD",
) -> Signal:
    return get_strategy(name).evaluate(
        candles, current_time=current_time, instrument=instrument
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
