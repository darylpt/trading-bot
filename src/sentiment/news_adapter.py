"""Normalize provider news records into a bounded prompt context."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Mapping


_NEWS_FIELDS = (
    "headline",
    "source",
    "published_at",
    "currency",
    "instrument",
    "impact",
)
_SIGNAL_FIELDS = (
    "instrument",
    "direction",
    "reference_price",
    "signal_timestamp",
    "rsi",
    "moving_average_fast",
    "moving_average_slow",
)


def _text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value if isinstance(value, str) else str(value)


def _headline(article: Mapping[str, object]) -> str:
    value = article.get("headline", article.get("title"))
    return _text(value) or ""


def format_news_payload(
    headlines: list[dict[str, object]], technical_signal: dict[str, object]
) -> str:
    """Return deterministic JSON context containing only relevant fields."""
    normalized_news = [
        {
            field: (
                _headline(article) if field == "headline" else _text(article.get(field))
            )
            for field in _NEWS_FIELDS
        }
        for article in headlines
    ]
    normalized_signal = {
        field: _text(technical_signal.get(field)) for field in _SIGNAL_FIELDS
    }
    return json.dumps(
        {"technical_signal": normalized_signal, "news": normalized_news},
        indent=2,
        sort_keys=True,
    )
