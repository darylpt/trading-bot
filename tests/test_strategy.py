"""Behavior tests for the deterministic strategy foundation."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from domain.models import MarketCandle
from strategy.backtest import run_backtest
from strategy.indicators import calculate_indicators
from strategy.market_data import MarketDataError, validate_candles


def candles(closes: list[str]) -> list[MarketCandle]:
    start = datetime(2026, 1, 5, tzinfo=timezone.utc)
    return [
        MarketCandle(
            instrument="EUR_USD",
            timeframe="15m",
            timestamp=start + timedelta(minutes=15 * index),
            open=Decimal(close) - Decimal("0.0005"),
            high=Decimal(close) + Decimal("0.0010"),
            low=Decimal(close) - Decimal("0.0010"),
            close=Decimal(close),
        )
        for index, close in enumerate(closes)
    ]


def test_indicator_calculation_has_no_fabricated_warmup_values() -> None:
    frame = calculate_indicators(
        candles(["1.1000"] * 5),
        rsi_period=2,
        fast_period=2,
        slow_period=3,
        atr_period=2,
    )
    assert frame.points[0].rsi is None
    assert frame.points[-1].moving_average_slow == Decimal("1.1000")
    assert frame.points[-1].atr == Decimal("0.0020")


def test_malformed_or_disorderly_data_is_rejected() -> None:
    data = candles(["1.1000", "1.1001"])
    data.reverse()
    try:
        validate_candles(data)
    except MarketDataError:
        pass
    else:
        raise AssertionError("disorderly candles must be rejected")


def test_backtest_is_deterministic_and_friction_aware() -> None:
    data = candles(["1.1000", "1.1010", "1.0990", "1.1020", "1.0980", "1.1030"] * 5)
    first = run_backtest(data)
    second = run_backtest(data)
    assert first == second
    assert first.friction.spread > 0
    assert all(event.execution_price > 0 for event in first.events)
