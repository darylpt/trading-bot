"""CSV candle loading and validation."""

from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal, Sequence

from domain.models import MarketCandle


class MarketDataError(ValueError):
    """Raised when historical market data violates the input contract."""


def _required(row: dict[str, str | None], name: str) -> str:
    value = row.get(name)
    if value is None or value == "":
        raise MarketDataError(f"CSV contains an incomplete {name} value")
    return value


def _optional(row: dict[str, str | None], name: str) -> str | None:
    value = row.get(name)
    return None if value in (None, "") else value


def _parse_decimal(value: str, field_name: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise MarketDataError(f"invalid {field_name}") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise MarketDataError(f"{field_name} must be finite and positive")
    return parsed


def _parse_timestamp(value: str) -> datetime:
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MarketDataError("invalid timestamp") from exc
    if timestamp.tzinfo is None:
        raise MarketDataError("timestamp must include timezone")
    return timestamp.astimezone(timezone.utc)


def validate_candles(
    candles: Sequence[MarketCandle], *, minimum_history: int = 1
) -> tuple[MarketCandle, ...]:
    """Validate chronology, timeframe spacing, and available history."""
    if len(candles) < minimum_history:
        raise MarketDataError("insufficient candle history")
    if not candles:
        return ()
    expected = timedelta(minutes=15 if candles[0].timeframe == "15m" else 60)
    for previous, current in zip(candles, candles[1:]):
        if (
            current.instrument != candles[0].instrument
            or current.timeframe != candles[0].timeframe
        ):
            raise MarketDataError("all candles must share instrument and timeframe")
        if current.timestamp <= previous.timestamp:
            raise MarketDataError("candles must be chronological")
        if current.timestamp - previous.timestamp != expected:
            raise MarketDataError("market-data gap detected")
    return tuple(candles)


def load_csv_candles(
    path: str | Path,
    *,
    instrument: str = "EUR_USD",
    timeframe: Literal["15m", "1h"] = "15m",
    minimum_history: int = 1,
) -> tuple[MarketCandle, ...]:
    """Load typed OHLC records from a CSV with timestamp/OHLC headers."""
    if timeframe not in {"15m", "1h"}:
        raise MarketDataError("unsupported timeframe")
    rows: list[MarketCandle] = []
    try:
        with Path(path).open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            required = {"timestamp", "open", "high", "low", "close"}
            if not required.issubset(reader.fieldnames or set()):
                raise MarketDataError("CSV is missing required OHLC columns")
            for row in reader:
                rows.append(
                    MarketCandle(
                        instrument=instrument,
                        timeframe=timeframe,
                        timestamp=_parse_timestamp(_required(row, "timestamp")),
                        open=_parse_decimal(_required(row, "open"), "open"),
                        high=_parse_decimal(_required(row, "high"), "high"),
                        low=_parse_decimal(_required(row, "low"), "low"),
                        close=_parse_decimal(_required(row, "close"), "close"),
                        volume=(
                            None
                            if _optional(row, "volume") is None
                            else _parse_decimal(
                                _optional(row, "volume") or "", "volume"
                            )
                        ),
                    )
                )
    except OSError as exc:
        raise MarketDataError(f"unable to read market data: {path}") from exc
    return validate_candles(rows, minimum_history=minimum_history)
