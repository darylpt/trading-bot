from datetime import datetime, timedelta, timezone
from decimal import Decimal

from domain.models import MarketCandle
from strategy.indicators import calculate_indicators
from strategy.market_data import validate_candles


def make_candles(size: int) -> list[MarketCandle]:
    start = datetime(2026, 1, 5, tzinfo=timezone.utc)
    return [
        MarketCandle(
            instrument="EUR_USD",
            timeframe="15m",
            timestamp=start + timedelta(minutes=15 * i),
            open=Decimal("1.1"),
            high=Decimal("1.102"),
            low=Decimal("1.098"),
            close=Decimal("1.1") + Decimal(i) / Decimal("10000"),
        )
        for i in range(size)
    ]


def test_ac_te_03_incomplete_history_has_no_signal_inputs() -> None:
    frame = calculate_indicators(
        make_candles(3), rsi_period=2, fast_period=2, slow_period=5, atr_period=2
    )
    assert frame.latest is not None
    assert frame.latest.moving_average_slow is None
    assert frame.ready is False


def test_chronological_market_data_is_preserved() -> None:
    loaded = validate_candles(make_candles(3), minimum_history=3)
    assert [item.timestamp for item in loaded] == sorted(
        item.timestamp for item in loaded
    )
