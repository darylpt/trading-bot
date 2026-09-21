"""Shared instrument and risk defaults for the paper-trading runtime."""

from __future__ import annotations

from decimal import Decimal
from typing import Final

DEMO_BRIDGE_HOSTS: Final[frozenset[str]] = frozenset(
    {"host.docker.internal", "localhost", "127.0.0.1"}
)
DEMO_BRIDGE_PORT: Final[int] = 18812
DEMO_ACCOUNT_ID: Final[str] = "463948680"


DEFAULT_INSTRUMENT: Final[str] = "BTC_USD"
ACTIVE_INSTRUMENTS: Final[frozenset[str]] = frozenset(
    {"BTC_USD", "BTCUSD", "ETH_USD", "XAUUSDm", "EUR_USD", "EURUSDm"}
)
DAILY_DRAWDOWN_LIMIT: Final[Decimal] = Decimal("0.01")


def validate_instrument(instrument: str) -> str:
    """Return an enabled instrument or reject an unsupported symbol."""
    if instrument not in ACTIVE_INSTRUMENTS:
        raise ValueError(f"unsupported instrument: {instrument}")
    return instrument
