from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

from urllib.request import Request

import pytest

from sentiment import historical_calendar
from sentiment.historical_calendar import (
    EODHDHistoricalCalendarSource,
    HistoricalCalendarError,
    HistoricalCalendarEvent,
    load_calendar_archive,
    write_calendar_archive,
)


class _Response:
    def __init__(self, payload: object) -> None:
        self._payload = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, _limit: int = -1) -> bytes:
        return self._payload


def test_eodhd_source_normalizes_and_filters_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = [
        {
            "id": "usd-cpi-1",
            "date": "2026-01-05T13:30:00Z",
            "country": "United States",
            "event": "Consumer Price Index",
            "importance": 3,
            "actual": "3.1",
            "forecast": "3.0",
            "previous": "2.9",
        },
        {
            "id": "eur-pmi-1",
            "date": "2026-01-05T09:00:00Z",
            "country": "Euro Area",
            "event": "PMI",
            "importance": "High",
        },
    ]
    captured: list[str] = []

    def fake_urlopen(request: Request, timeout: float) -> _Response:
        captured.append(request.full_url)
        assert timeout == 30.0
        return _Response(payload)

    monkeypatch.setattr(historical_calendar, "urlopen", fake_urlopen)
    events = EODHDHistoricalCalendarSource("test-key").fetch(
        from_date=date(2026, 1, 1),
        to_date=date(2026, 1, 7),
        instrument="XAUUSDm",
        fetched_at=datetime(2026, 1, 8, tzinfo=timezone.utc),
    )

    assert len(events) == 1
    event = events[0]
    assert event.event_id == "usd-cpi-1"
    assert event.currency == "USD"
    assert event.impact == "HIGH"
    assert event.actual == "3.1"
    assert "api_token=test-key" in captured[0]


def test_eodhd_source_rejects_timezone_free_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        historical_calendar,
        "urlopen",
        lambda *_args, **_kwargs: _Response(
            [
                {
                    "date": "2026-01-05 13:30:00",
                    "country": "United States",
                    "event": "CPI",
                    "importance": 3,
                }
            ]
        ),
    )

    with pytest.raises(HistoricalCalendarError, match="timezone timestamp"):
        EODHDHistoricalCalendarSource("test-key").fetch(
            from_date=date(2026, 1, 1),
            to_date=date(2026, 1, 7),
            instrument="XAUUSDm",
        )


def test_calendar_archive_is_content_addressed_and_immutable(tmp_path: Path) -> None:
    fetched_at = datetime(2026, 1, 8, tzinfo=timezone.utc)
    archive_event = HistoricalCalendarEvent(
        event_id="usd-cpi-1",
        source="eodhd",
        headline="CPI",
        published_at=datetime(2026, 1, 5, 13, 30, tzinfo=timezone.utc),
        instrument="XAUUSDm",
        currency="USD",
        impact="HIGH",
        retrieved_at=fetched_at,
    )
    archive_dir = tmp_path / "archives"
    archive = write_calendar_archive(
        [archive_event],
        instrument="XAUUSDm",
        from_date=date(2026, 1, 1),
        to_date=date(2026, 1, 7),
        fetched_at=fetched_at,
        archive_dir=archive_dir,
    )

    assert archive == write_calendar_archive(
        [archive_event],
        instrument="XAUUSDm",
        from_date=date(2026, 1, 1),
        to_date=date(2026, 1, 7),
        fetched_at=fetched_at,
        archive_dir=archive_dir,
    )
    loaded = load_calendar_archive(archive)
    assert archive.name.endswith(f"{loaded.archive_sha256[:16]}.json")
    assert loaded.events[0].event_id == "usd-cpi-1"
    archive.write_text("tampered", encoding="utf-8")
    with pytest.raises(HistoricalCalendarError, match="immutable"):
        write_calendar_archive(
            [archive_event],
            instrument="XAUUSDm",
            from_date=date(2026, 1, 1),
            to_date=date(2026, 1, 7),
            fetched_at=fetched_at,
            archive_dir=archive_dir,
        )


def test_eodhd_source_rejects_unapproved_url() -> None:
    with pytest.raises(HistoricalCalendarError, match="allowlisted"):
        EODHDHistoricalCalendarSource(
            "test-key", url="https://example.com/api/economic-events"
        )
