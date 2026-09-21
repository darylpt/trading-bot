from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO

import pytest

from sentiment import news


class _Response:
    def __init__(self, payload: bytes) -> None:
        self._stream = BytesIO(payload)

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, _: int) -> bytes:
        return self._stream.read()


def test_forex_factory_xml_normalizes_relevant_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"""<?xml version=\"1.0\"?>
    <weeklyevents>
      <event><title>US CPI</title><country>USD</country>
        <date>09-21-2026</date><time>8:30am</time><impact>High</impact></event>
      <event><title>ECB Rate</title><country>EUR</country>
        <date>09-21-2026</date><time>8:15am</time><impact>High</impact></event>
    </weeklyevents>"""
    monkeypatch.setattr(news, "urlopen", lambda *args, **kwargs: _Response(payload))

    source = news.ForexFactoryNewsSource(
        "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"
    )
    events = source.fetch(
        instrument="XAUUSDm",
        current_time=datetime(2026, 9, 19, 22, 40, tzinfo=timezone.utc),
    )

    assert len(events) == 1
    assert events[0].headline == "US CPI"
    assert events[0].currency == "USD"
    assert events[0].instrument == "XAUUSDm"
    assert events[0].impact == "HIGH"
    assert events[0].published_at.isoformat() == "2026-09-21T08:30:00-04:00"


def test_forex_factory_xml_rejects_unknown_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"""<weeklyevents><event>
      <title>All Day Event</title><country>USD</country>
      <date>09-21-2026</date><time>All Day</time><impact>High</impact>
    </event></weeklyevents>"""
    monkeypatch.setattr(news, "urlopen", lambda *args, **kwargs: _Response(payload))

    source = news.ForexFactoryNewsSource(
        "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"
    )
    with pytest.raises(news.NewsFeedError, match="invalid timestamp"):
        source.fetch(
            instrument="XAUUSDm",
            current_time=datetime(2026, 9, 19, 22, 40, tzinfo=timezone.utc),
        )
