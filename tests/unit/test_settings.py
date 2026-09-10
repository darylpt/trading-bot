from decimal import Decimal

import pytest
from pydantic import ValidationError

from config.settings import Settings


def env(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> None:
    values = {
        "PAPER_TRADING": "true",
        "LIVE_TRADING": "false",
        "BROKER_ENV": "demo",
        "BROKER_ENDPOINT": "https://api-fxpractice.oanda.com",
        "DAILY_DRAWDOWN_LIMIT": "0.02",
    }
    values.update(overrides)
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def test_settings_require_safe_demo_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    env(monkeypatch)
    settings = Settings()
    assert settings.paper_trading is True
    assert settings.live_trading is False
    assert settings.daily_drawdown_limit == Decimal("0.02")


def test_settings_reject_live_mode_and_non_demo_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env(monkeypatch, LIVE_TRADING="true")
    with pytest.raises((ValidationError, ValueError)):
        Settings()
    env(
        monkeypatch,
        LIVE_TRADING="false",
        BROKER_ENDPOINT="https://api-fxtrade.oanda.com",
    )
    with pytest.raises((ValidationError, ValueError)):
        Settings()
