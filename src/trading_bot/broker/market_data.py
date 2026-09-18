"""Live quote polling, candle aggregation, and safe CSV persistence."""

from __future__ import annotations
import csv

import math
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Literal

from domain.models import MarketCandle
from execution.broker_adapter import (
    BrokerConnectionError,
    MarketQuote,
    StaleMarketDataError,
)
from strategy.market_data import MarketDataError, load_csv_candles, validate_candles
from trading_bot.broker.mt5 import QuoteSource
from trading_bot.config import validate_instrument

Timeframe = Literal["15m", "1h"]
Motion = Literal["random_walk", "sine_wave"]


class SimulatedQuoteSource:
    """Generate paper quotes from a CSV seed or optional dynamic motion."""

    _BASE_PRICES = {
        "BTC_USD": Decimal("65000"),
        "BTCUSD": Decimal("65000"),
        "ETH_USD": Decimal("3500"),
        "EUR_USD": Decimal("1.08"),
        "XAUUSDm": Decimal("2300"),
    }

    @staticmethod
    def _normalize_instrument(instrument: str) -> str:
        """Normalize broker symbol separators and casing for local lookups."""
        return instrument.strip().upper().replace("/", "_")

    def __init__(
        self,
        data_path: Path,
        *,
        spread: Decimal = Decimal("0.0002"),
        now: Callable[[], datetime] | None = None,
        dynamic: bool = False,
        motion: Motion = "random_walk",
        seed: int | None = None,
        volatility: Decimal = Decimal("0.002"),
    ) -> None:
        if not spread.is_finite() or spread <= 0:
            raise ValueError("simulated spread must be finite and positive")
        if motion not in {"random_walk", "sine_wave"}:
            raise ValueError("simulated motion must be random_walk or sine_wave")
        if not volatility.is_finite() or volatility <= 0:
            raise ValueError("simulated volatility must be finite and positive")
        self.data_path = data_path
        self.spread = spread
        self.dynamic = dynamic
        self.motion = motion
        self.volatility = volatility
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._random = random.Random(seed)
        self._tick = 0
        self._prices: dict[str, Decimal] = {}

    def poll_quote(self, instrument: str) -> MarketQuote:
        runtime = self._now()
        if runtime.tzinfo is None:
            runtime = runtime.replace(tzinfo=timezone.utc)
        runtime = runtime.astimezone(timezone.utc)
        midpoint = self._next_price(instrument)
        half_spread = self.spread / Decimal("2")
        return MarketQuote(
            instrument=instrument,
            bid=midpoint - half_spread,
            ask=midpoint + half_spread,
            observed_at=runtime,
        )

    def _next_price(self, instrument: str) -> Decimal:
        if not self.dynamic:
            candles = load_csv_candles(self.data_path, instrument=instrument)
            return candles[-1].close
        normalized_instrument = self._normalize_instrument(instrument)
        base = self._BASE_PRICES.get(normalized_instrument)
        if base is None:
            raise ValueError(f"no simulated base price for {instrument}")
        previous = self._prices.setdefault(normalized_instrument, base)
        self._tick += 1
        if self.motion == "sine_wave":
            cycle = Decimal(str(math.sin(self._tick / 3)))
            change = self.volatility * cycle
        else:
            change = self.volatility * Decimal(str(self._random.uniform(-1, 1)))
        price = previous * (Decimal("1") + change)
        self._prices[normalized_instrument] = max(price, base * Decimal("0.01"))
        return self._prices[normalized_instrument]


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

    def __init__(self, path: Path, *, allow_gaps: bool = False) -> None:
        self.path = path
        self._allow_gaps = allow_gaps
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
            elif timestamp > last_timestamp and (
                self._allow_gaps or timestamp - last_timestamp == expected
            ):
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
        load_existing: bool = True,
        allow_market_gaps: bool = False,
    ) -> None:
        if max_history <= 0:
            raise ValueError("max_history must be positive")
        validate_instrument(instrument)
        self.source = source
        self.instrument = instrument
        self._timeframe = timeframe
        self._now = now
        self._allow_market_gaps = allow_market_gaps
        self._aggregator = BarAggregator(timeframe=timeframe, now=now)
        self._writer = MarketDataWriter(data_path, allow_gaps=allow_market_gaps)
        if load_existing:
            try:
                existing = load_csv_candles(
                    data_path,
                    instrument=instrument,
                    allow_gaps=allow_market_gaps,
                )
            except (OSError, ValueError):
                existing = ()
        else:
            existing = ()
        self._history: list[MarketCandle] = list(existing[-max_history:])
        self._max_history = max_history

    def set_instrument(self, instrument: str) -> None:
        """Switch targets without carrying bars from the previous instrument."""
        validate_instrument(instrument)
        if instrument == self.instrument:
            return
        self.instrument = instrument
        self._aggregator = BarAggregator(timeframe=self._timeframe, now=self._now)
        try:
            existing = load_csv_candles(
                self.data_path,
                instrument=instrument,
                allow_gaps=self._allow_market_gaps,
            )
        except (OSError, ValueError):
            existing = ()
        self._history = list(existing[-self._max_history :])

    @property
    def last_quote(self) -> MarketQuote | None:
        """Return the quote used by the most recent poll."""
        return getattr(self, "_last_quote", None)

    @property
    def data_path(self) -> Path:
        return self._writer.path

    def backfill(self, candles: Sequence[MarketCandle]) -> tuple[MarketCandle, ...]:
        """Replace in-memory strategy history with validated broker candles."""
        validated = validate_candles(
            candles, minimum_history=1, allow_gaps=self._allow_market_gaps
        )
        if any(
            candle.instrument != self.instrument or candle.timeframe != self._timeframe
            for candle in validated
        ):
            raise MarketDataError("broker backfill does not match gateway target")
        self._history = list(validated[-self._max_history :])
        for candle in self._history:
            self._writer.upsert(candle)
        return tuple(self._history)

    def poll(self) -> tuple[MarketCandle, ...]:
        """Poll one quote and return recent bars including the current bar."""
        quote = self.source.poll_quote(self.instrument)
        self._last_quote = quote
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
            if candle.timestamp <= last.timestamp or (
                not self._allow_market_gaps
                and candle.timestamp - last.timestamp != expected
            ):
                self._history = []
        self._append_history(candle)
