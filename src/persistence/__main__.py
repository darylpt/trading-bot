"""Cross-shell CLI for initializing and inspecting the operational SQLite database."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from persistence.sqlite import SQLiteRepository, SessionMetricsRecord


def _metrics_payload(metrics: list[SessionMetricsRecord]) -> list[dict[str, object]]:
    return [
        {
            "session_date": metric.session_date.isoformat(),
            "broker_latency_total_ms": metric.broker_latency_total_ms,
            "broker_latency_samples": metric.broker_latency_samples,
            "news_blackout_hits": metric.news_blackout_hits,
            "updated_at": metric.updated_at.isoformat(),
        }
        for metric in metrics
    ]


def main(argv: Sequence[str] | None = None) -> int:
    """Create the schema and optionally dump recent metrics as JSON."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("data/session_metrics.db"),
        help="SQLite database path (default: data/session_metrics.db)",
    )
    parser.add_argument(
        "--dump",
        "--query",
        dest="dump",
        action="store_true",
        help="print recent session_metrics rows as JSON",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="maximum metrics rows to print with --dump (default: 5)",
    )
    args = parser.parse_args(argv)

    repository = SQLiteRepository(args.database)
    try:
        payload: object
        if args.dump:
            payload = {
                "database": str(repository.path),
                "session_metrics": _metrics_payload(
                    repository.get_latest_metrics(limit=args.limit)
                ),
            }
        else:
            payload = {"status": "initialized", "database": str(repository.path)}
        print(json.dumps(payload, separators=(",", ":")))
    finally:
        repository.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
