from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture(autouse=True)
def isolate_local_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep developer .env mode from changing deterministic test semantics."""
    monkeypatch.setenv("TRADING_MODE", "SIMULATED")
    monkeypatch.setenv("INSTRUMENT", "XAUUSDm")
    monkeypatch.setenv("DEFAULT_SYMBOL", "XAUUSDm")
    monkeypatch.setenv("EXNESS_SYMBOL_SUFFIX", "m")
