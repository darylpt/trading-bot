from decimal import Decimal

import pytest
from pydantic import ValidationError

from config.settings import Settings


def env(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> None:
    values = {
        "PAPER_TRADING": "true",
        "LIVE_TRADING": "false",
        "BROKER_ENV": "demo",
        "BROKER_PROVIDER": "exness_mt5",
        "BROKER_ENDPOINT": "https://demo.exness-mt5.local",
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


def test_settings_allow_local_demo_bridge_and_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env(
        monkeypatch,
        TRADING_MODE="BROKER_DEMO",
        BROKER_ENDPOINT="https://localhost:18812",
        EXNESS_BRIDGE_HOST="localhost",
        EXNESS_BRIDGE_PORT="18812",
        EXNESS_LOGIN="463948680",
        EXNESS_SERVER="Exness-MT5Trial17",
        EXNESS_PASSWORD="test-token",
        INSTRUMENT="XAUUSDm",
        EXNESS_SYMBOL_SUFFIX="m",
    )

    settings = Settings()

    assert settings.trading_mode == "BROKER_DEMO"
    assert str(settings.broker_endpoint) == "https://localhost:18812/"
    assert settings.broker_account_id == "463948680"


def test_settings_reject_non_allowlisted_demo_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env(
        monkeypatch,
        TRADING_MODE="BROKER_DEMO",
        BROKER_ENDPOINT="https://localhost:18812",
        EXNESS_LOGIN="not-allowlisted",
        EXNESS_SERVER="Exness-MT5Trial17",
        EXNESS_PASSWORD="test-token",
        INSTRUMENT="XAUUSDm",
        EXNESS_SYMBOL_SUFFIX="m",
    )

    with pytest.raises(ValidationError, match="allowlisted"):
        Settings()


def test_settings_allow_missing_broker_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env(monkeypatch)
    monkeypatch.delenv("BROKER_ENDPOINT")
    settings = Settings(_env_file=None)

    assert settings.broker_endpoint is None


def test_settings_reject_live_mode_and_non_demo_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env(monkeypatch, LIVE_TRADING="true")
    with pytest.raises((ValidationError, ValueError)):
        Settings()
    env(
        monkeypatch,
        LIVE_TRADING="false",
        BROKER_ENDPOINT="https://live.exness-mt5.local",
    )
    with pytest.raises((ValidationError, ValueError)):
        Settings()


def test_settings_reject_unknown_environment_variables(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "PAPER_TRADING=true\n"
        "LIVE_TRADING=false\n"
        "BROKER_ENV=demo\n"
        "DAILY_DRAWDOWN_LIMIT=0.02\n"
        "TRADING_BOT_UNEXPECTED=true\n",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        Settings(_env_file=env_file)


def test_legacy_environment_schema_maps_to_canonical_settings(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "session_metrics.db"))
    env_file = tmp_path / ".env"
    env_file.write_text(
        "PAPER_TRADING=true\n"
        "LIVE_TRADING=false\n"
        "TRADING_MODE=SIMULATED\n"
        "BROKER_ENV=demo\n"
        "BROKER_TYPE=exness_mt5\n"
        "EXNESS_SYMBOL_SUFFIX=m\n"
        "DEFAULT_SYMBOL=XAUUSDm\n"
        "MAX_SPREAD_TOLERANCE=0.35\n"
        "MAX_SLIPPAGE_PIPS=2.0\n"
        f"SQLITE_DB_PATH={tmp_path / 'session_metrics.db'}\n",
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)

    assert settings.provider == "exness_mt5"
    assert settings.instrument == "XAUUSDm"
    assert settings.max_spread == Decimal("0.35")
    assert settings.session_database_path == tmp_path / "session_metrics.db"
