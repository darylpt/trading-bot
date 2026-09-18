"""Seed small valid paper-trading market-data samples when none exists."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from trading_bot.config import DEFAULT_INSTRUMENT, validate_instrument


def _forex_data(now: datetime) -> str:
    rows = ["timestamp,open,high,low,close,volume"]
    start = _seed_start(now)
    for index in range(24):
        timestamp = start + timedelta(minutes=15 * index)
        close = "1.1100" if index == 23 else "1.1000"
        high = "1.1110" if index == 23 else "1.1010"
        rows.append(
            f"{timestamp.isoformat().replace('+00:00', 'Z')},1.1000,{high},1.0990,{close},{100 + index * 5}"
        )
    return "\n".join(rows) + "\n"


def _bitcoin_data(now: datetime) -> str:
    rows = ["timestamp,open,high,low,close,volume"]
    start = _seed_start(now)
    closes = ("60000", "60180", "59920", "60350", "60200", "60550", "60400", "60800")
    for index in range(24):
        timestamp = start + timedelta(minutes=15 * index)
        close = closes[index % len(closes)]
        opening = closes[(index - 1) % len(closes)]
        close_value = int(close)
        open_value = int(opening)
        high = max(close_value, open_value) + 180
        low = min(close_value, open_value) - 180
        rows.append(
            f"{timestamp.isoformat().replace('+00:00', 'Z')},{open_value},{high},{low},{close_value},{1000 + index * 25}"
        )
    return "\n".join(rows) + "\n"


def _seed_start(now: datetime) -> datetime:
    runtime = (
        now.replace(tzinfo=timezone.utc)
        if now.tzinfo is None
        else now.astimezone(timezone.utc)
    )
    bucket = runtime.replace(
        minute=runtime.minute - runtime.minute % 15,
        second=0,
        microsecond=0,
    )
    return bucket - timedelta(minutes=15 * 23)


def _runtime_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_default_market_data(
    data_dir: Path,
    *,
    instrument: str = DEFAULT_INSTRUMENT,
    now: Callable[[], datetime] | None = None,
    refresh: bool = False,
) -> Path:
    """Create or refresh the current UTC-aligned default CSV."""
    validate_instrument(instrument)
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "market_data.csv"
    if path.exists() and not refresh:
        return path
    runtime_now = (now or _runtime_now)()
    content = (
        _bitcoin_data(runtime_now)
        if instrument in {"BTC_USD", "BTCUSD"}
        else _forex_data(runtime_now)
    )
    if refresh:
        path.write_text(content, encoding="utf-8", newline="")
        return path
    try:
        with path.open("x", encoding="utf-8", newline="") as handle:
            handle.write(content)
    except FileExistsError:
        pass
    return path
