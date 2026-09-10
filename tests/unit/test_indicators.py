from datetime import datetime, timedelta, timezone
from decimal import Decimal

from domain.models import MarketCandle
from strategy.indicators import calculate_indicators


def series(size: int) -> list[MarketCandle]:
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


def test_indicator_warmup_is_explicit() -> None:
    frame = calculate_indicators(
        series(5), rsi_period=2, fast_period=2, slow_period=3, atr_period=2
    )
    assert frame.points[0].rsi is None
    assert frame.points[1].moving_average_fast is not None
    assert frame.points[1].moving_average_slow is None
    assert frame.points[-1].atr is not None
