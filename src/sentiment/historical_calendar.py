"""Historical economic-calendar ingestion and immutable archive contracts."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal, Sequence
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from pydantic import Field, ValidationError

from domain.models import Impact, StrictModel


class HistoricalCalendarError(ValueError):
    """Raised when historical calendar data cannot be trusted or archived."""


class HistoricalCalendarEvent(StrictModel):
    """Normalized historical calendar occurrence used for replay."""

    event_id: str = Field(min_length=1)
    source: Literal["eodhd"]
    headline: str = Field(min_length=1)
    published_at: datetime
    instrument: str = Field(min_length=1)
    currency: str = Field(min_length=1)
    impact: Impact
    retrieved_at: datetime
    country: str | None = None
    period: str | None = None
    actual: str | None = None
    forecast: str | None = None
    previous: str | None = None


class HistoricalCalendarArchive(StrictModel):
    """Immutable normalized calendar archive metadata and events."""

    schema_version: Literal[1] = 1
    provider: Literal["eodhd"]
    instrument: str = Field(min_length=1)
    from_date: date
    to_date: date
    fetched_at: datetime
    archive_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    events: list[HistoricalCalendarEvent]


class EODHDHistoricalCalendarSource:
    """Fetch bounded historical economic events from the EODHD API."""

    _HOSTS = {"eodhd.com", "www.eodhd.com"}
    _DEFAULT_URL = "https://eodhd.com/api/economic-events"
    _MAX_BYTES = 10_000_000

    def __init__(
        self,
        api_key: str,
        *,
        url: str = _DEFAULT_URL,
        timeout_seconds: float = 30.0,
    ) -> None:
        if not api_key.strip():
            raise HistoricalCalendarError("HISTORICAL_CALENDAR_API_KEY is required")
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in self._HOSTS
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in {None, 443}
            or parsed.path != "/api/economic-events"
            or parsed.query
            or parsed.fragment
        ):
            raise HistoricalCalendarError("historical calendar URL is not allowlisted")
        if timeout_seconds <= 0:
            raise HistoricalCalendarError(
                "historical calendar timeout must be positive"
            )
        self._api_key = api_key
        self._url = url
        self._timeout_seconds = timeout_seconds

    def fetch(
        self,
        *,
        from_date: date,
        to_date: date,
        instrument: str,
        fetched_at: datetime | None = None,
    ) -> tuple[HistoricalCalendarEvent, ...]:
        """Fetch and normalize one bounded historical date range."""
        if to_date < from_date:
            raise HistoricalCalendarError("historical calendar range is reversed")
        if not instrument.strip():
            raise HistoricalCalendarError("instrument is required")
        retrieved_at = self._utc(fetched_at or datetime.now(timezone.utc))
        query = urlencode(
            {
                "api_token": self._api_key,
                "from": from_date.isoformat(),
                "to": to_date.isoformat(),
                "fmt": "json",
            }
        )
        request = Request(
            f"{self._url}?{query}",
            headers={
                "Accept": "application/json",
                "User-Agent": "trading-bot-historical-calendar/1.0",
            },
        )
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:  # noqa: S310
                payload_bytes = response.read(self._MAX_BYTES + 1)
        except OSError as exc:
            raise HistoricalCalendarError("historical calendar request failed") from exc
        if len(payload_bytes) > self._MAX_BYTES:
            raise HistoricalCalendarError("historical calendar response is too large")
        try:
            payload = json.loads(payload_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HistoricalCalendarError(
                "historical calendar response is invalid JSON"
            ) from exc
        raw_events = self._event_list(payload)
        normalized: dict[str, HistoricalCalendarEvent] = {}
        for raw in raw_events:
            event = self._normalize(
                raw, instrument=instrument, retrieved_at=retrieved_at
            )
            if event is not None:
                normalized[event.event_id] = event
        return tuple(
            sorted(
                normalized.values(),
                key=lambda event: (event.published_at, event.event_id),
            )
        )

    @staticmethod
    def _event_list(payload: object) -> list[dict[str, object]]:
        candidate: object = payload
        if isinstance(payload, dict):
            candidate = payload.get("data", payload.get("events", payload))
        if not isinstance(candidate, list):
            raise HistoricalCalendarError("historical calendar events are not a list")
        if not all(isinstance(item, dict) for item in candidate):
            raise HistoricalCalendarError(
                "historical calendar contains a non-object event"
            )
        return [item for item in candidate if isinstance(item, dict)]

    def _normalize(
        self,
        raw: dict[str, object],
        *,
        instrument: str,
        retrieved_at: datetime,
    ) -> HistoricalCalendarEvent | None:
        country = self._text(raw.get("country"))
        currency = self._currency(raw, country)
        if not self._relevant_currency(currency, instrument):
            return None
        headline = self._text(raw.get("event")) or self._text(raw.get("name"))
        if headline is None:
            headline = self._text(raw.get("headline"))
        timestamp = self._timestamp(
            raw.get("date", raw.get("datetime", raw.get("timestamp")))
        )
        if headline is None or timestamp is None:
            raise HistoricalCalendarError(
                "historical event lacks headline or timezone timestamp"
            )
        impact = self._impact(raw.get("importance", raw.get("impact")))
        if impact is None:
            raise HistoricalCalendarError("historical event has unknown impact")
        event_id = self._text(raw.get("id")) or self._text(raw.get("event_id"))
        if event_id is None:
            event_id = hashlib.sha256(
                f"{timestamp.isoformat()}|{country}|{currency}|{headline}".encode(
                    "utf-8"
                )
            ).hexdigest()[:32]
        return HistoricalCalendarEvent(
            event_id=event_id,
            source="eodhd",
            headline=headline,
            published_at=timestamp,
            instrument=instrument,
            currency=currency,
            impact=impact,
            retrieved_at=retrieved_at,
            country=country,
            period=self._text(raw.get("period")),
            actual=self._value_text(raw.get("actual")),
            forecast=self._value_text(raw.get("forecast")),
            previous=self._value_text(raw.get("previous")),
        )

    @staticmethod
    def _text(value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @classmethod
    def _value_text(cls, value: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, (dict, list)):
            return json.dumps(value, sort_keys=True, separators=(",", ":"))
        return cls._text(value)

    @classmethod
    def _currency(cls, raw: dict[str, object], country: str | None) -> str:
        currency = cls._text(raw.get("currency"))
        if currency is not None:
            return currency.upper()
        if country is not None and country.upper() in {"US", "USA", "UNITED STATES"}:
            return "USD"
        if country is not None and country.upper() == "ALL":
            return "ALL"
        return country.upper() if country is not None else "UNKNOWN"

    @staticmethod
    def _relevant_currency(currency: str, instrument: str) -> bool:
        normalized = instrument.upper().replace("_", "")
        if normalized.startswith("XAUUSD"):
            return currency in {"USD", "ALL"}
        currencies = (
            {normalized[:3], normalized[-3:]} if len(normalized) >= 6 else set()
        )
        return currency in currencies or currency == "ALL"

    @classmethod
    def _impact(cls, value: object) -> Impact | None:
        text = cls._text(value)
        if text is None:
            return None
        normalized = text.upper()
        if normalized in {"HIGH", "3"}:
            return "HIGH"
        if normalized in {"MEDIUM", "MED", "2"}:
            return "MEDIUM"
        if normalized in {"LOW", "1"}:
            return "LOW"
        return None

    @staticmethod
    def _timestamp(value: object) -> datetime | None:
        if not isinstance(value, str):
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


def write_calendar_archive(
    events: Sequence[HistoricalCalendarEvent],
    *,
    instrument: str,
    from_date: date,
    to_date: date,
    fetched_at: datetime,
    archive_dir: Path,
) -> Path:
    """Write one content-addressed archive without overwriting existing data."""
    if to_date < from_date:
        raise HistoricalCalendarError("historical calendar range is reversed")
    normalized_events = sorted(
        events, key=lambda event: (event.published_at, event.event_id)
    )
    fingerprint = {
        "provider": "eodhd",
        "instrument": instrument,
        "from_date": from_date.isoformat(),
        "to_date": to_date.isoformat(),
        "fetched_at": fetched_at.isoformat(),
        "events": [event.model_dump(mode="json") for event in normalized_events],
    }
    fingerprint_bytes = json.dumps(
        fingerprint, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    archive_sha256 = hashlib.sha256(fingerprint_bytes).hexdigest()
    archive = HistoricalCalendarArchive(
        provider="eodhd",
        instrument=instrument,
        from_date=from_date,
        to_date=to_date,
        fetched_at=fetched_at,
        archive_sha256=archive_sha256,
        events=normalized_events,
    )
    content = (
        json.dumps(archive.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    archive_dir.mkdir(parents=True, exist_ok=True)
    target = archive_dir / f"eodhd_{from_date}_{to_date}_{archive_sha256[:16]}.json"
    if target.exists():
        if target.read_bytes() != content:
            raise HistoricalCalendarError("immutable calendar archive already differs")
        return target
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_bytes(content)
    temporary.replace(target)
    return target


def load_calendar_archive(path: Path) -> HistoricalCalendarArchive:
    """Load and validate one normalized immutable archive."""
    try:
        return HistoricalCalendarArchive.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError, ValidationError) as exc:
        raise HistoricalCalendarError("historical calendar archive is invalid") from exc
