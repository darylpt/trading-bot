from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from persistence.sqlite import SQLiteRepository
from trading_bot import __main__ as application
from trading_bot.market_data_seed import ensure_default_market_data
from trading_bot.strategy import Signal


def test_forward_test_window_blocks_new_entry_outside_schedule(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data_dir = tmp_path / "data"
    ensure_default_market_data(data_dir)
    repository = SQLiteRepository(tmp_path / "session_metrics.db")
    monkeypatch.setattr(
        application,
        "_evaluate_strategy",
        lambda *args, **kwargs: Signal(
            action="BUY",
            timestamp=datetime(2026, 9, 17, 10, 0, tzinfo=timezone.utc),
            instrument="XAUUSDm",
            reference_price=Decimal("100"),
            rationale="deterministic test entry",
        ),
    )

    application.run_tick(
        repository,
        data_dir=data_dir,
        current_time=datetime(2026, 9, 17, 10, 0, tzinfo=timezone.utc),
        account_equity=Decimal("10000"),
        session_start_equity=Decimal("10000"),
        stop_distance=Decimal("1"),
        forward_test_enabled=True,
    )

    assert repository.get_open_positions() == []
    row = repository.connection.execute(
        "SELECT decision_rationale FROM execution_logs "
        "WHERE event_type = 'STRATEGY_REJECTED' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    assert row is not None
    assert row[0] == "Forward-test entry window closed"
    repository.close()
