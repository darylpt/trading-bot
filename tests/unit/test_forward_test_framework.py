from __future__ import annotations

import json
import logging
from datetime import datetime, time, timezone

import pytest
from pydantic import ValidationError

from config.settings import Settings
from observability.logging import emit_alert
from strategy.sessions import is_entry_window


def test_entry_window_is_weekday_half_open_interval() -> None:
    weekday = datetime(2026, 9, 17, 13, 0, tzinfo=timezone.utc)
    assert is_entry_window(weekday) is True
    assert (
        is_entry_window(
            datetime(2026, 9, 17, 16, 0, tzinfo=timezone.utc),
        )
        is False
    )
    assert is_entry_window(datetime(2026, 9, 19, 14, 0, tzinfo=timezone.utc)) is False


def test_entry_window_rejects_inverted_bounds() -> None:
    with pytest.raises(ValueError, match="end after it starts"):
        is_entry_window(
            datetime(2026, 9, 17, 13, 0, tzinfo=timezone.utc),
            start_utc=time(16),
            end_utc=time(13),
        )


def test_settings_reject_forward_test_without_broker_demo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FORWARD_TEST_ENABLED", "true")
    with pytest.raises(ValidationError, match="requires BROKER_DEMO"):
        Settings(_env_file=None)


def test_settings_require_webhook_url_for_webhook_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALERT_ROUTE", "webhook")
    with pytest.raises(ValidationError, match="requires ALERT_WEBHOOK_URL"):
        Settings(_env_file=None)


def test_emit_alert_sends_only_sanitized_payload() -> None:
    delivered: list[tuple[str, bytes, float]] = []

    def sender(url: str, payload: bytes, timeout: float) -> None:
        delivered.append((url, payload, timeout))

    safe = emit_alert(
        logging.getLogger("test-forward-alert"),
        "DRAWDOWN_HALTED",
        message="daily threshold reached provider-secret",
        fields={"token": "provider-secret", "instrument": "XAUUSDm"},
        secrets=("provider-secret",),
        webhook_url="https://alerts.example.invalid/hook",
        webhook_timeout_seconds=5.0,
        webhook_sender=sender,
    )

    assert safe["token"] == "[REDACTED]"
    assert len(delivered) == 1
    url, raw_payload, timeout = delivered[0]
    assert url == "https://alerts.example.invalid/hook"
    assert timeout == 5
    payload = json.loads(raw_payload)
    assert payload["alert"]["token"] == "[REDACTED]"
    assert "provider-secret" not in raw_payload.decode("utf-8")
