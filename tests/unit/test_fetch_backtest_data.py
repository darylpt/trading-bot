from __future__ import annotations

import csv
from pathlib import Path

import tools.fetch_backtest_data as fetcher


class _FakeMT5:
    TIMEFRAME_M1 = 1
    TIMEFRAME_M5 = 5

    def __init__(self) -> None:
        self.initialized = False
        self.shutdown_calls = 0
        self.requested_timeframe: int | None = None

    def initialize(self) -> bool:
        self.initialized = True
        return True

    def shutdown(self) -> None:
        self.shutdown_calls += 1

    def symbol_select(self, symbol: str, enable: bool) -> bool:
        return symbol == "EURUSDm" and enable

    def copy_rates_from_pos(
        self, symbol: str, timeframe: int, start_pos: int, count: int
    ) -> list[dict[str, object]]:
        self.requested_timeframe = timeframe
        return [
            {
                "time": 1_700_000_000,
                "open": 1.1,
                "high": 1.2,
                "low": 1.0,
                "close": 1.15,
                "tick_volume": 42,
                "spread": 8,
            }
        ][:count]

    def last_error(self) -> tuple[int, str]:
        return (0, "")


def test_fetch_m5_writes_schema_and_shuts_down(
    monkeypatch: object, tmp_path: Path
) -> None:
    fake = _FakeMT5()
    monkeypatch.setattr(fetcher, "_load_mt5", lambda: fake)
    output = tmp_path / "EURUSD_M5_historical.csv"

    count = fetcher.fetch_candles(
        symbol="EURUSDm", timeframe="5m", count=1, output=output
    )

    with output.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    assert count == 1
    assert rows[0] == [
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "tick_volume",
        "spread",
    ]
    assert rows[1][0] == "2023-11-14T22:13:20+00:00"
    assert fake.requested_timeframe == fake.TIMEFRAME_M5
    assert fake.shutdown_calls == 1
