"""Live quote polling, candle aggregation, and safe CSV persistence."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable, Literal

from domain.models import MarketCandle
from execution.broker_adapter import (
    BrokerConnectionError,
    MarketQuote,
    StaleMarketDataError,
)
from strategy.market_data import load_csv_candles
from trading_bot.broker.mt5 import QuoteSource

Timeframe = Literal["15m", "1h"]


class SimulatedQuoteSource:
    """Create deterministic bid/ask quotes from the local paper CSV feed."""

    def __init__(
        self,
        data_path: Path,
        *,
        spread: Decimal = Decimal("0.0002"),
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if not spread.is_finite() or spread <= 0:
            raise ValueError("simulated spread must be finite and positive")
        self.data_path = data_path
        self.spread = spread
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._cursor_timestamp: datetime | None = None

    def poll_quote(self, instrument: str) -> MarketQuote:
        candles = load_csv_candles(self.data_path, instrument=instrument)
        latest = candles[-1]
        midpoint = latest.close
        if self._cursor_timestamp is None:
            self._cursor_timestamp = latest.timestamp
        else:
            self._cursor_timestamp += timedelta(minutes=15)
        half_spread = self.spread / Decimal("2")
        return MarketQuote(
            instrument=instrument,
            bid=midpoint - half_spread,
            ask=midpoint + half_spread,
            observed_at=self._cursor_timestamp.astimezone(timezone.utc),
        )


@dataclass(frozen=True)
class BarUpdate:
    """Current in-progress bar and the completed bar, if a bucket changed."""

    current: MarketCandle
    completed: MarketCandle | None = None


class BarAggregator:
    """Aggregate midpoint quotes into regular UTC OHLCV bars."""

    def __init__(
        self,
        *,
        timeframe: Timeframe = "15m",
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.timeframe = timeframe
        self._interval = timedelta(minutes=15 if timeframe == "15m" else 60)
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._current: MarketCandle | None = None

    @property
    def current(self) -> MarketCandle | None:
        return self._current

    def add_quote(self, quote: MarketQuote) -> BarUpdate:
        if not quote.bid.is_finite() or not quote.ask.is_finite():
            raise BrokerConnectionError("quote prices must be finite")
        if quote.ask <= quote.bid:
            raise BrokerConnectionError("quote ask must exceed bid")
        observed_at = quote.observed_at.astimezone(timezone.utc)
        midpoint = (quote.bid + quote.ask) / Decimal("2")
        bucket = self._bucket_start(observed_at)
        if self._current is not None and bucket < self._current.timestamp:
            raise StaleMarketDataError("quote timestamp moved backwards")
        if self._current is None or bucket > self._current.timestamp:
            completed = self._current
            self._current = MarketCandle(
                instrument=quote.instrument,
                timeframe=self.timeframe,
                timestamp=bucket,
                open=midpoint,
                high=midpoint,
                low=midpoint,
                close=midpoint,
                volume=Decimal("1"),
            )
            return BarUpdate(current=self._current, completed=completed)

        current = self._current
        self._current = MarketCandle(
            instrument=current.instrument,
            timeframe=current.timeframe,
            timestamp=current.timestamp,
            open=current.open,
            high=max(current.high, midpoint),
            low=min(current.low, midpoint),
            close=midpoint,
            volume=(current.volume or Decimal("0")) + Decimal("1"),
        )
        return BarUpdate(current=self._current)

    def _bucket_start(self, timestamp: datetime) -> datetime:
        interval_seconds = int(self._interval.total_seconds())
        epoch_seconds = int(timestamp.timestamp())
        bucket_seconds = epoch_seconds - epoch_seconds % interval_seconds
        return datetime.fromtimestamp(bucket_seconds, tz=timezone.utc)


class MarketDataWriter:
    """Upsert live bars without combining incompatible historical gaps."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def upsert(self, candle: MarketCandle) -> None:
        rows = self._read_rows()
        timestamp = candle.timestamp.astimezone(timezone.utc)
        row = self._row(candle)
        if not rows:
            rows = [row]
        else:
            last_timestamp = self._parse_timestamp(rows[-1]["timestamp"])
            expected = timedelta(minutes=15 if candle.timeframe == "15m" else 60)
            if timestamp == last_timestamp:
                rows[-1] = row
            elif timestamp > last_timestamp and timestamp - last_timestamp == expected:
                rows.append(row)
            else:
                # Stale seed/history cannot be joined to a live stream safely.
                rows = [row]
        with self.path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["timestamp", "open", "high", "low", "close", "volume"],
            )
            writer.writeheader()
            writer.writerows(rows)

    def _read_rows(self) -> list[dict[str, str]]:
        if not self.path.exists():
            return []
        with self.path.open(newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))

    @staticmethod
    def _parse_timestamp(value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
            timezone.utc
        )

    @staticmethod
    def _row(candle: MarketCandle) -> dict[str, str]:
        return {
            "timestamp": candle.timestamp.astimezone(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "open": str(candle.open),
            "high": str(candle.high),
            "low": str(candle.low),
            "close": str(candle.close),
            "volume": str(candle.volume or Decimal("0")),
        }


class LiveMarketDataGateway:
    """Poll quotes, aggregate bars, and expose recent strategy input candles."""

    def __init__(
        self,
        source: QuoteSource,
        *,
        data_path: Path,
        instrument: str = "EUR_USD",
        timeframe: Timeframe = "15m",
        max_history: int = 256,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if max_history <= 0:
            raise ValueError("max_history must be positive")
        self.source = source
        self.instrument = instrument
        self._aggregator = BarAggregator(timeframe=timeframe, now=now)
        self._writer = MarketDataWriter(data_path)
        try:
            existing = load_csv_candles(data_path, instrument=instrument)
        except (OSError, ValueError):
            existing = ()
        self._history: list[MarketCandle] = list(existing[-max_history:])
        self._max_history = max_history

    @property
    def data_path(self) -> Path:
        return self._writer.path

    def poll(self) -> tuple[MarketCandle, ...]:
        """Poll one quote and return recent bars including the current bar."""
        quote = self.source.poll_quote(self.instrument)
        update = self._aggregator.add_quote(quote)
        if update.completed is not None:
            self._append_history(update.completed)
        self._upsert_history(update.current)
        self._writer.upsert(update.current)
        return tuple(self._history)

    def _append_history(self, candle: MarketCandle) -> None:
        if self._history and candle.timestamp <= self._history[-1].timestamp:
            return
        self._history.append(candle)
        self._history = self._history[-self._max_history :]

    def _upsert_history(self, candle: MarketCandle) -> None:
        if self._history:
            last = self._history[-1]
            if candle.timestamp == last.timestamp:
                self._history[-1] = candle
                return
            expected = timedelta(minutes=15 if candle.timeframe == "15m" else 60)
            if (
                candle.timestamp <= last.timestamp
                or candle.timestamp - last.timestamp != expected
            ):
                self._history = []
        self._append_history(candle)
