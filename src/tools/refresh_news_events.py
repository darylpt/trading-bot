"""Refresh the paper-trading news snapshot from the approved public calendar."""

from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import datetime, timezone

from config.settings import Settings
from sentiment.news import ForexFactoryNewsSource, NewsFeedError

LOGGER = logging.getLogger("news_refresh")


def refresh_once(settings: Settings) -> int:
    """Fetch relevant calendar events and atomically replace the snapshot."""
    if settings.news_events_path is None:
        raise NewsFeedError("NEWS_EVENTS_PATH is unavailable")
    now = datetime.now(timezone.utc)
    source = ForexFactoryNewsSource(str(settings.news_feed_url))
    events = source.fetch(instrument=settings.instrument, current_time=now)
    target = settings.news_events_path
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    payload = [event.model_dump(mode="json") for event in events]
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)
    LOGGER.info("news snapshot refreshed events=%d", len(events))
    return len(events)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--once",
        action="store_true",
        help="refresh once and exit instead of running continuously",
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    settings = Settings()
    while True:
        try:
            refresh_once(settings)
        except (OSError, NewsFeedError, ValueError) as exc:
            LOGGER.warning("news refresh rejected reason=%s", type(exc).__name__)
            if args.once:
                return 1
        if args.once:
            return 0
        time.sleep(float(settings.news_refresh_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
