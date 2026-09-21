"""Typed, deterministic news-event loading for the runtime sentiment gate."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ElementTree
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import ValidationError

from domain.models import NewsEvent


class NewsFeedError(ValueError):
    """Raised when configured news input is malformed or unreadable."""


class JsonNewsFeed:
    """Load a bounded, operator-supplied JSON news snapshot."""

    def __init__(self, path: Path | None, *, max_age_seconds: float = 3600.0) -> None:
        if max_age_seconds <= 0:
            raise ValueError("news max age must be positive")
        self._path = path
        self._max_age_seconds = max_age_seconds

    def load(self, *, instrument: str, current_time: datetime) -> tuple[NewsEvent, ...]:
        """Return fresh events relevant to the active instrument."""
        if self._path is None:
            raise NewsFeedError("NEWS_EVENTS_PATH is unavailable")
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise NewsFeedError("configured news feed could not be read") from exc
        if not isinstance(payload, list):
            raise NewsFeedError("configured news feed must be a JSON array")

        now = self._utc(current_time)
        events: list[NewsEvent] = []
        seen: set[str] = set()
        for raw in payload:
            if not isinstance(raw, dict):
                raise NewsFeedError("news feed contains a non-object event")
            try:
                event = NewsEvent.model_validate(raw)
            except ValidationError as exc:
                raise NewsFeedError("news feed contains an invalid event") from exc
            if event.event_id in seen:
                continue
            seen.add(event.event_id)
            if event.instrument not in {None, instrument}:
                continue
            retrieved_age = (now - self._utc(event.retrieved_at)).total_seconds()
            if retrieved_age < -self._max_age_seconds:
                raise NewsFeedError("news feed timestamp is too far ahead")
            if retrieved_age > self._max_age_seconds:
                continue
            events.append(event)
        return tuple(events)

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class ForexFactoryNewsSource:
    """Fetch and normalize the public Forex Factory calendar for XAUUSDm."""

    _ALLOWED_HOST = "nfs.faireconomy.media"
    _SOURCE = "forex_factory"

    def __init__(self, url: str, *, timeout_seconds: float = 10.0) -> None:
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != self._ALLOWED_HOST
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("news URL is not an approved HTTPS calendar endpoint")
        if timeout_seconds <= 0:
            raise ValueError("news timeout must be positive")
        self._url = url
        self._timeout_seconds = timeout_seconds

    def fetch(
        self,
        *,
        instrument: str,
        current_time: datetime,
        max_bytes: int = 2_000_000,
    ) -> tuple[NewsEvent, ...]:
        """Fetch, validate, and normalize relevant calendar events."""
        if max_bytes <= 0:
            raise ValueError("news response limit must be positive")
        request = Request(
            self._url, headers={"Accept": "application/json, application/xml"}
        )
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:  # noqa: S310
                raw_body = response.read(max_bytes)
        except OSError as exc:
            raise NewsFeedError("approved news calendar request failed") from exc

        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return self._parse_xml(
                raw_body, instrument=instrument, current_time=current_time
            )
        if not isinstance(payload, list):
            raise NewsFeedError("approved news calendar must be a JSON array")
        return self._parse_json(
            payload, instrument=instrument, current_time=current_time
        )

    def _parse_json(
        self,
        payload: list[object],
        *,
        instrument: str,
        current_time: datetime,
    ) -> tuple[NewsEvent, ...]:
        events: list[NewsEvent] = []
        retrieved_at = self._utc(current_time)
        for raw in payload:
            if not isinstance(raw, dict):
                raise NewsFeedError("approved news calendar contains a non-object")
            country = raw.get("country")
            if country not in {"USD", "All"}:
                continue
            title = raw.get("title")
            date_value = raw.get("date")
            impact_value = raw.get("impact")
            if not isinstance(title, str) or not title.strip():
                raise NewsFeedError("approved news calendar contains an invalid title")
            if not isinstance(date_value, str) or not date_value:
                raise NewsFeedError("approved news calendar contains an invalid date")
            if not isinstance(impact_value, str):
                raise NewsFeedError("approved news calendar contains an invalid impact")
            try:
                published_at = datetime.fromisoformat(date_value)
            except ValueError as exc:
                raise NewsFeedError(
                    "approved news calendar contains an invalid date"
                ) from exc
            events.append(
                self._event(
                    title=title,
                    country=country,
                    published_at=published_at,
                    impact=impact_value,
                    instrument=instrument,
                    retrieved_at=retrieved_at,
                    event_key=date_value,
                )
            )
        return tuple(events)

    def _parse_xml(
        self,
        raw_body: bytes,
        *,
        instrument: str,
        current_time: datetime,
    ) -> tuple[NewsEvent, ...]:
        try:
            root = ElementTree.fromstring(raw_body)
        except ElementTree.ParseError as exc:
            raise NewsFeedError(
                "approved news calendar response was not valid JSON or XML"
            ) from exc
        retrieved_at = self._utc(current_time)
        events: list[NewsEvent] = []
        for raw in root.findall("./event"):
            country = (raw.findtext("country") or "").strip()
            if country not in {"USD", "All"}:
                continue
            title = (raw.findtext("title") or "").strip()
            date_value = (raw.findtext("date") or "").strip()
            time_value = (raw.findtext("time") or "").strip()
            impact_value = (raw.findtext("impact") or "").strip()
            try:
                local_time = datetime.strptime(
                    f"{date_value} {time_value}", "%m-%d-%Y %I:%M%p"
                ).replace(tzinfo=ZoneInfo("America/New_York"))
            except (ValueError, ZoneInfoNotFoundError) as exc:
                raise NewsFeedError(
                    "approved news calendar contains an invalid timestamp"
                ) from exc
            events.append(
                self._event(
                    title=title,
                    country=country,
                    published_at=local_time,
                    impact=impact_value,
                    instrument=instrument,
                    retrieved_at=retrieved_at,
                    event_key=f"{date_value}:{time_value}",
                )
            )
        return tuple(events)

    def _event(
        self,
        *,
        title: str,
        country: str,
        published_at: datetime,
        impact: str,
        instrument: str,
        retrieved_at: datetime,
        event_key: str,
    ) -> NewsEvent:
        if not title:
            raise NewsFeedError("approved news calendar contains an invalid title")
        normalized_impact = impact.upper()
        if normalized_impact == "LOW":
            typed_impact: Literal["LOW", "MEDIUM", "HIGH"] = "LOW"
        elif normalized_impact == "MEDIUM":
            typed_impact = "MEDIUM"
        elif normalized_impact == "HIGH":
            typed_impact = "HIGH"
        else:
            raise NewsFeedError("approved news calendar contains an unknown impact")
        return NewsEvent(
            event_id=f"{self._SOURCE}:{country}:{event_key}:{title}",
            source=self._SOURCE,
            headline=title,
            published_at=published_at,
            instrument=instrument,
            currency=None if country == "All" else country,
            impact=typed_impact,
            retrieved_at=retrieved_at,
        )

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
