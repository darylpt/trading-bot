from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from domain.models import MarketCandle
from trading_bot.strategy import evaluate_market_data, moving_average_signal


def _candles(closes: list[str]) -> tuple[MarketCandle, ...]:
    start = datetime(2026, 1, 5, tzinfo=timezone.utc)
    return tuple(
        MarketCandle(
            instrument="EUR_USD",
            timeframe="15m",
            timestamp=start + timedelta(minutes=15 * index),
            open=Decimal(close),
            high=Decimal(close) + Decimal("0.001"),
            low=Decimal(close) - Decimal("0.001"),
            close=Decimal(close),
            volume=Decimal("100"),
        )
        for index, close in enumerate(closes)
    )


def test_moving_average_crossover_emits_buy_sell_and_hold() -> None:
    flat = ["1.1000"] * 20

    buy = moving_average_signal(
        _candles(flat + ["1.1100"]), fast_period=5, slow_period=20
    )
    sell = moving_average_signal(
        _candles(flat + ["1.0900"]), fast_period=5, slow_period=20
    )
    hold = moving_average_signal(
        _candles(flat + ["1.1000"]), fast_period=5, slow_period=20
    )

    assert buy.action == "BUY"
    assert "Bullish crossover" in buy.rationale
    assert buy.fast_ema == buy.fast_average
    assert buy.slow_ema == buy.slow_average
    assert buy.distance_to_crossover == buy.fast_average - buy.slow_average
    assert sell.action == "SELL"
    assert "Bearish crossover" in sell.rationale
    assert hold.action == "HOLD"
    assert "No crossover" in hold.rationale


def test_moving_average_strategy_holds_during_warmup() -> None:
    signal = moving_average_signal(
        _candles(["1.1000"] * 4), fast_period=2, slow_period=5
    )

    assert signal.action == "HOLD"
    assert signal.fast_average is not None
    assert signal.slow_average is None


def test_strategy_reads_ohlcv_csv(tmp_path: Path) -> None:
    path = tmp_path / "market_data.csv"
    rows = [
        "timestamp,open,high,low,close,volume",
        *[
            f"2026-01-05T00:{index * 15:02d}:00Z,1.1000,1.1010,1.0990,1.1000,100"
            for index in range(3)
        ],
        "2026-01-05T00:45:00Z,1.1000,1.1110,1.0990,1.1100,100",
    ]
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")

    signal = evaluate_market_data(path, fast_period=2, slow_period=3)

    assert signal.action == "BUY"
