from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from config.settings import Settings
from execution.broker_adapter import BrokerAdapter
from observability.readiness import check_forward_test_readiness


class AccountTransport:
    def __init__(self, account_payload: dict[str, object]) -> None:
        self.account_payload = account_payload
        self.authorization: str | None = None

    def request(self, method: str, url: str, **kwargs: object) -> object:
        headers = kwargs["headers"]
        assert isinstance(headers, dict)
        self.authorization = headers.get("Authorization")
        assert method == "GET"
        assert url.endswith("/v3/accounts/paper-account/summary")
        return {"account": self.account_payload}


def _settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    values = {
        "PAPER_TRADING": "true",
        "LIVE_TRADING": "false",
        "BROKER_ENV": "demo",
        "BROKER_PROVIDER": "oanda",
        "BROKER_ENDPOINT": "https://api-fxpractice.oanda.com",
        "BROKER_TOKEN": "demo-token",
        "DAILY_DRAWDOWN_LIMIT": "0.02",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return Settings()


def _broker(transport: AccountTransport, checked_at: datetime) -> BrokerAdapter:
    return BrokerAdapter(
        transport,
        provider="oanda",
        base_url="https://api-fxpractice.oanda.com",
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
            "provider": "oanda",
            "timestamp": checked_at.isoformat(),
        }
    ]
