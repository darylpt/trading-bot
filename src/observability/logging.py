"""Sanitize structured diagnostics before logging or persistence."""

from __future__ import annotations

import logging
from collections.abc import Mapping

_SENSITIVE_NAMES = ("key", "token", "secret", "password", "authorization", "account_id")


def sanitize_fields(
    fields: Mapping[str, str], *, secrets: tuple[str, ...] = ()
) -> dict[str, str]:
    """Return safe fields with credential-like keys and values redacted."""
    result: dict[str, str] = {}
    for name, value in fields.items():
        lowered = name.lower()
        if any(marker in lowered for marker in _SENSITIVE_NAMES) or any(
            secret and secret in value for secret in secrets
        ):
            result[name] = "[REDACTED]"
        else:
            result[name] = value
    return result


def record_event(
    logger: logging.Logger,
    event_type: str,
    fields: Mapping[str, str],
    *,
    secrets: tuple[str, ...] = (),
) -> dict[str, str]:
    """Log and return only sanitized event fields."""
    safe = sanitize_fields(fields, secrets=secrets)
    logger.info("event=%s fields=%s", event_type, safe)
    return safe
