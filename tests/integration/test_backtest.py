from datetime import datetime, timedelta, timezone
from decimal import Decimal

from domain.models import MarketCandle
from strategy.backtest import run_backtest


def test_backtest_repeats_identically_without_network() -> None:
    start = datetime(2026, 1, 5, tzinfo=timezone.utc)
    candles = [
        MarketCandle(
            instrument="EUR_USD",
            timeframe="15m",
            timestamp=start + timedelta(minutes=15 * i),
            open=Decimal("1.1"),
            high=Decimal("1.102"),
            low=Decimal("1.098"),
            close=Decimal("1.1") + Decimal(i % 3) / Decimal("1000"),
        )
        for i in range(30)
    ]
    assert run_backtest(candles) == run_backtest(candles)


def test_backtest_exposes_account_metrics_and_equity_curve() -> None:
    start = datetime(2026, 1, 5, tzinfo=timezone.utc)
    candles = [
        MarketCandle(
            instrument="EUR_USD",
            timeframe="15m",
            timestamp=start + timedelta(minutes=15 * i),
            open=Decimal("1.1"),
            high=Decimal("1.102"),
            low=Decimal("1.098"),
            close=Decimal("1.1") + Decimal(i % 3) / Decimal("1000"),
        )
        for i in range(30)
    ]

    result = run_backtest(candles)

    assert result.metrics.initial_equity == Decimal("10000")
    assert result.metrics.equity_curve[0] == Decimal("10000")
    assert result.metrics.final_equity >= Decimal("0")
    assert result.metrics.max_drawdown >= Decimal("0")
    assert Decimal("0") <= result.metrics.win_rate <= Decimal("1")
    assert result.metrics.total_friction_cost >= Decimal("0")
