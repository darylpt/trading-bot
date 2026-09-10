from datetime import datetime, timezone
from decimal import Decimal

from strategy.indicators import IndicatorFrame, IndicatorPoint
from strategy.signals import generate_signal


def frame(
    fast: tuple[str, str], slow: tuple[str, str], rsi: tuple[str, str]
) -> IndicatorFrame:
    points = tuple(
        IndicatorPoint(
            datetime(2026, 1, 5, index, tzinfo=timezone.utc),
            Decimal("1.1"),
            Decimal(rsi[index]),
            Decimal(fast[index]),
            Decimal(slow[index]),
            Decimal("0.001"),
        )
        for index in range(2)
    )
    return IndicatorFrame(points, 2, 2, 3, 2)


def test_bullish_crossover_emits_long() -> None:
    signal = generate_signal(
        frame(("1.099", "1.101"), ("1.100", "1.100"), ("40", "45"))
    )
    assert signal is not None
    assert signal.direction == "LONG"


def test_bearish_crossover_emits_short() -> None:
    signal = generate_signal(
        frame(("1.101", "1.099"), ("1.100", "1.100"), ("60", "55"))
    )
    assert signal is not None
    assert signal.direction == "SHORT"
