"""Persistent runtime settings shared by the daemon and dashboard."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Final

from trading_bot.config import DEFAULT_INSTRUMENT, validate_instrument

SUPPORTED_INSTRUMENTS: Final[tuple[str, ...]] = (
    "BTC_USD",
    "ETH_USD",
    "XAUUSDm",
    "EUR_USD",
    "EURUSDm",
)
SUPPORTED_STRATEGIES: Final[tuple[str, ...]] = (
    "ema_crossover",
    "rsi_mean_reversion",
    "breakout_channel",
)
DEFAULT_STRATEGY: Final[str] = "ema_crossover"


def validate_strategy(strategy: str) -> str:
    if strategy not in SUPPORTED_STRATEGIES:
        raise ValueError(f"unsupported strategy: {strategy}")
    return strategy


class RuntimeConfig:
    """Read and update active instrument and strategy in SQLite."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS runtime_settings (
                    setting_key TEXT PRIMARY KEY,
                    setting_value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            now = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"
            connection.execute(
                f"""INSERT OR IGNORE INTO runtime_settings
                    (setting_key, setting_value, updated_at)
                    VALUES ('active_instrument', ?, {now})""",
                (DEFAULT_INSTRUMENT,),
            )
            connection.execute(
                f"""INSERT OR IGNORE INTO runtime_settings
                    (setting_key, setting_value, updated_at)
                    VALUES ('active_strategy', ?, {now})""",
                (DEFAULT_STRATEGY,),
            )

    def get_active_instrument(self) -> str:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT setting_value FROM runtime_settings WHERE setting_key = 'active_instrument'"
            ).fetchone()
        instrument = DEFAULT_INSTRUMENT if row is None else str(row[0])
        return validate_instrument(instrument)

    def set_active_instrument(self, instrument: str) -> None:
        validate_instrument(instrument)
        self._set("active_instrument", instrument)

    def get_active_strategy(self) -> str:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT setting_value FROM runtime_settings WHERE setting_key = 'active_strategy'"
            ).fetchone()
        strategy = DEFAULT_STRATEGY if row is None else str(row[0])
        return validate_strategy(strategy)

    def set_active_strategy(self, strategy: str) -> None:
        validate_strategy(strategy)
        self._set("active_strategy", strategy)

    def _set(self, key: str, value: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO runtime_settings (setting_key, setting_value, updated_at)
                   VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                   ON CONFLICT(setting_key) DO UPDATE SET
                     setting_value = excluded.setting_value,
                     updated_at = excluded.updated_at""",
                (key, value),
            )
