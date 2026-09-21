"""Import a bounded EODHD historical economic-calendar archive."""

from __future__ import annotations

import argparse
import logging
from datetime import date, datetime, timezone

from config.settings import Settings
from sentiment.historical_calendar import (
    EODHDHistoricalCalendarSource,
    HistoricalCalendarError,
    write_calendar_archive,
)

LOGGER = logging.getLogger("historical_calendar_import")


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-date", required=True, type=_date)
    parser.add_argument("--to-date", required=True, type=_date)
    parser.add_argument("--instrument", default=None)
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    try:
        settings = Settings()
        if settings.historical_calendar_provider != "eodhd":
            raise HistoricalCalendarError("unsupported historical calendar provider")
        if settings.historical_calendar_api_key is None:
            raise HistoricalCalendarError(
                "HISTORICAL_CALENDAR_API_KEY is required for historical import"
            )
        instrument = args.instrument or settings.instrument
        fetched_at = datetime.now(timezone.utc)
        source = EODHDHistoricalCalendarSource(
            settings.historical_calendar_api_key,
            url=str(settings.historical_calendar_url),
        )
        events = source.fetch(
            from_date=args.from_date,
            to_date=args.to_date,
            instrument=instrument,
            fetched_at=fetched_at,
        )
        archive = write_calendar_archive(
            events,
            instrument=instrument,
            from_date=args.from_date,
            to_date=args.to_date,
            fetched_at=fetched_at,
            archive_dir=settings.historical_calendar_archive_path,
        )
    except (HistoricalCalendarError, OSError, ValueError) as exc:
        LOGGER.error(
            "historical calendar import rejected reason=%s", type(exc).__name__
        )
        return 1
    LOGGER.info(
        "historical calendar archived events=%d path=%s",
        len(events),
        archive,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
