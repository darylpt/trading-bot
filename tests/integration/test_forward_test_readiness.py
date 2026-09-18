from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from config.settings import Settings
from execution.broker_adapter import BaseBroker, ExnessMT5Broker
from observability.readiness import (
    check_broker_demo_readiness,
    check_forward_test_readiness,
)
from persistence.sqlite import SQLiteRepository


class AccountTransport:
    def __init__(self, account_payload: dict[str, object]) -> None:
        self.account_payload = account_payload
        self.authorization: str | None = None

    def request(self, method: str, url: str, **kwargs: object) -> object:
        headers = kwargs["headers"]
        assert isinstance(headers, dict)
        self.authorization = headers.get("Authorization")
        assert method == "GET"
        assert url.endswith("/api/accounts/paper-account")
        return {"account": self.account_payload}


def _settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    values = {
        "PAPER_TRADING": "true",
        "LIVE_TRADING": "false",
        "BROKER_ENV": "demo",
        "BROKER_PROVIDER": "exness_mt5",
        "BROKER_ENDPOINT": "https://demo.exness-mt5.local",
        "BROKER_TOKEN": "demo-token",
        "BROKER_ACCOUNT_ID": "463948680",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return Settings()


def _broker(transport: AccountTransport, checked_at: datetime) -> BaseBroker:
    return ExnessMT5Broker(
        transport,
        base_url="https://demo.exness-mt5.local",
        account_id="paper-account",
        environment="DEMO",
        api_token="demo-token",
        now=lambda: checked_at,
    )


def test_forward_test_readiness_authenticates_and_validates_equity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checked_at = datetime(2026, 9, 10, 13, 30, tzinfo=timezone.utc)
    transport = AccountTransport(
        {
            "balance": "10000",
            "NAV": "10025",
            "marginUsed": "250",
            "timestamp": checked_at.isoformat(),
            "positions": [],
        }
    )

    readiness = check_forward_test_readiness(
        _settings(monkeypatch),
        _broker(transport, checked_at),
        checked_at=checked_at,
    )

    assert readiness.status == "READY"
    assert readiness.account_equity == Decimal("10025")
    assert transport.authorization == "Bearer demo-token"


def test_broker_demo_readiness_uses_closed_candle_end_and_clock_tolerance(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("TRADING_MODE", "BROKER_DEMO")
    settings = _settings(monkeypatch)
    checked_at = datetime(2026, 9, 10, 13, 47, tzinfo=timezone.utc)
    broker = _broker(
        AccountTransport(
            {
                "balance": "10000",
                "NAV": "10025",
                "marginUsed": "0",
                "timestamp": (checked_at + timedelta(seconds=3)).isoformat(),
                "positions": [],
            }
        ),
        checked_at,
    )
    monkeypatch.setattr(
        broker,
        "get_account_snapshot",
        lambda: SimpleNamespace(
            balance=Decimal("10000"),
            equity=Decimal("10025"),
            margin=Decimal("0"),
            captured_at=checked_at + timedelta(seconds=3),
        ),
    )
    monkeypatch.setattr(
        broker,
        "get_instrument_metadata",
        lambda instrument: SimpleNamespace(
            instrument=instrument, captured_at=checked_at + timedelta(seconds=3)
        ),
    )
    monkeypatch.setattr(
        broker,
        "get_trading_session",
        lambda instrument: SimpleNamespace(
            instrument=instrument,
            is_open=True,
            captured_at=checked_at + timedelta(seconds=3),
        ),
    )
    monkeypatch.setattr(
        broker,
        "get_market_quote",
        lambda instrument: SimpleNamespace(
            instrument=instrument,
            spread=Decimal("0.25"),
            observed_at=checked_at + timedelta(seconds=4),
        ),
    )
    monkeypatch.setattr(
        broker,
        "get_historical_candles",
        lambda instrument, *, timeframe, limit: (
            SimpleNamespace(
                timestamp=checked_at - timedelta(minutes=17),
            ),
        ),
    )
    repository = SQLiteRepository(tmp_path / "readiness.db")
    try:
        readiness = check_broker_demo_readiness(
            settings,
            broker,
            repository,
            instrument="XAUUSDm",
            history_limit=1,
            checked_at=checked_at,
        )
    finally:
        repository.close()

    assert readiness.status == "READY"
    assert readiness.candle_count == 1
    assert readiness.clock_drift_seconds == Decimal("3")


def test_forward_test_readiness_emits_structured_startup_log(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    checked_at = datetime(2026, 9, 10, 13, 30, tzinfo=timezone.utc)
    transport = AccountTransport(
        {
            "balance": "10000",
            "NAV": "10025",
            "marginUsed": "0",
            "timestamp": checked_at.isoformat(),
            "positions": [],
        }
    )
    caplog.set_level(logging.INFO)

    check_forward_test_readiness(
        _settings(monkeypatch),
        _broker(transport, checked_at),
        checked_at=checked_at,
    )

    startup_records = [
        json.loads(record.message)
        for record in caplog.records
        if "FORWARD_TEST_SESSION_STARTED" in record.message
    ]
    assert startup_records == [
        {
            "account_equity": "10025",
            "environment": "DEMO",
            "event": "FORWARD_TEST_SESSION_STARTED",
            "provider": "exness_mt5",
            "timestamp": checked_at.isoformat(),
        }
    ]
