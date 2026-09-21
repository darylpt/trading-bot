"""Fetch deterministic MT5 candles for offline backtesting."""

from __future__ import annotations

import argparse
import csv
import importlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Protocol, Sequence, cast


DEFAULT_OUTPUT = Path("data/XAUUSDm_M1_historical.csv")
DEFAULT_SYMBOL = "XAUUSDm"
DEFAULT_COUNT = 50_000
Timeframe = Literal["1m", "5m"]


class _RateRecord(Protocol):
    def __getitem__(self, key: str) -> object: ...


class _MetaTrader5(Protocol):
    TIMEFRAME_M1: int
    TIMEFRAME_M5: int

    def initialize(self) -> bool: ...

    def shutdown(self) -> None: ...

    def symbol_select(self, symbol: str, enable: bool) -> bool: ...

    def copy_rates_from_pos(
        self, symbol: str, timeframe: int, start_pos: int, count: int
    ) -> Sequence[_RateRecord] | None: ...

    def last_error(self) -> object: ...


def _load_mt5() -> _MetaTrader5:
    try:
        module = importlib.import_module("MetaTrader5")
    except ImportError as exc:  # pragma: no cover - platform dependency
        raise RuntimeError(
            "MetaTrader5 is required on the Windows host running the terminal"
        ) from exc
    return cast(_MetaTrader5, module)


def _timestamp(value: object) -> str:
    try:
        seconds = float(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError("MT5 returned an invalid candle timestamp") from exc
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()


def _default_output(symbol: str, timeframe: Timeframe) -> Path:
    return Path(f"data/{symbol}_{timeframe.upper()}_historical.csv")


def fetch_candles(
    *,
    symbol: str = DEFAULT_SYMBOL,
    timeframe: Timeframe = "1m",
    count: int = DEFAULT_COUNT,
    output: Path = DEFAULT_OUTPUT,
) -> int:
    """Fetch completed M1 or M5 candles and atomically write a CSV."""
    if not symbol:
        raise ValueError("symbol must not be empty")
    if count <= 0:
        raise ValueError("count must be positive")

    mt5 = _load_mt5()
    if not mt5.initialize():
        error = mt5.last_error()
        raise RuntimeError(f"MetaTrader5 initialization failed: {error}")

    try:
        if not mt5.symbol_select(symbol, True):
            error = mt5.last_error()
            raise RuntimeError(f"unable to select {symbol}: {error}")
        mt5_timeframe = mt5.TIMEFRAME_M1 if timeframe == "1m" else mt5.TIMEFRAME_M5
        rates = mt5.copy_rates_from_pos(symbol, mt5_timeframe, 0, count)
        if rates is None or len(rates) == 0:
            error = mt5.last_error()
            raise RuntimeError(f"MT5 returned no {timeframe} candles: {error}")

        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(f".{output.name}.tmp")
        try:
            with temporary.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(
                    [
                        "timestamp",
                        "open",
                        "high",
                        "low",
                        "close",
                        "tick_volume",
                        "spread",
                    ]
                )
                for rate in rates:
                    writer.writerow(
                        [
                            _timestamp(rate["time"]),
                            rate["open"],
                            rate["high"],
                            rate["low"],
                            rate["close"],
                            rate["tick_volume"],
                            rate["spread"],
                        ]
                    )
            temporary.replace(output)
        finally:
            if temporary.exists():
                temporary.unlink()
        return len(rates)
    finally:
        mt5.shutdown()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL)
    parser.add_argument("--timeframe", choices=("1m", "5m"), default="1m")
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    output = args.output or _default_output(args.symbol, args.timeframe)
    count = fetch_candles(
        symbol=args.symbol,
        timeframe=args.timeframe,
        count=args.count,
        output=output,
    )
    print(f"Fetched {count} {args.timeframe} candles for {args.symbol} to {output}")


if __name__ == "__main__":
    main()
