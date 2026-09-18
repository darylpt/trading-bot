from datetime import datetime, timedelta, timezone
from pathlib import Path
from decimal import Decimal

import pytest

from domain.models import MarketCandle
from strategy.market_data import MarketDataError, load_csv_candles, validate_candles


def candle(index: int) -> MarketCandle:
    return MarketCandle(
        instrument="EUR_USD",
        timeframe="15m",
        timestamp=datetime(2026, 1, 5, tzinfo=timezone.utc)
        + timedelta(minutes=15 * index),
        open=Decimal("1.1"),
        high=Decimal("1.102"),
        low=Decimal("1.098"),
        close=Decimal("1.101"),
    )


def test_validate_candles_accepts_chronological_series() -> None:
    assert len(validate_candles([candle(0), candle(1)], minimum_history=2)) == 2


def test_load_csv_candles_returns_typed_records(tmp_path: Path) -> None:
    csv_path = tmp_path / "eur-usd.csv"
    csv_path.write_text(
        "timestamp,open,high,low,close,volume\n"
        "2026-01-05T00:00:00Z,1.1000,1.1020,1.0980,1.1010,100\n"
        "2026-01-05T00:15:00Z,1.1010,1.1030,1.0990,1.1020,120\n",
        encoding="utf-8",
    )
    loaded = load_csv_candles(csv_path, minimum_history=2)
    assert loaded[0].instrument == "EUR_USD"
    assert loaded[1].volume == Decimal("120")


def test_validate_candles_rejects_gaps_and_insufficient_history() -> None:
    with pytest.raises(MarketDataError):
        validate_candles([candle(0), candle(2)])
    with pytest.raises(MarketDataError):
        validate_candles([candle(0)], minimum_history=2)


def test_validate_candles_allows_explicit_broker_session_gaps() -> None:
    assert len(validate_candles([candle(0), candle(2)], allow_gaps=True)) == 2
