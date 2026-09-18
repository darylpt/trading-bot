import json
import logging
import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from persistence import __main__ as database_cli
from observability.logging import sanitize_fields
from persistence.sqlite import SQLiteRepository, TradeRecord
from trading_bot import __main__ as application


def test_sqlite_trade_write_is_idempotent(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "trades.sqlite")
    record = TradeRecord(
        client_order_id="persist-1",
        instrument="EUR_USD",
        direction="LONG",
        quantity=Decimal("20"),
        entry_price=Decimal("1.1"),
        stop_loss_price=Decimal("1.095"),
        take_profit_price=Decimal("1.11"),
        account_equity=Decimal("10000"),
        risk_fraction=Decimal("0.01"),
        status="ACCEPTED",
        environment="PAPER",
    )
    repository.save_trade(record)
    repository.save_trade(record)
    assert repository.trade_count() == 1
    repository.close()


def test_session_metrics_accumulate_latency_and_blackouts(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "metrics.sqlite")
    session_date = date(2026, 9, 10)

    repository.record_session_metrics(
        session_date, broker_latency_ms=120, news_blackout_hit=True
    )
    repository.record_session_metrics(session_date, broker_latency_ms=80)

    metrics = repository.get_session_metrics(session_date)
    latest = repository.get_latest_metrics(limit=1)
    repository.close()

    assert metrics is not None
    assert latest == [metrics]
    assert metrics.broker_latency_total_ms == 200
    assert metrics.broker_latency_samples == 2
    assert metrics.news_blackout_hits == 1


def test_database_cli_dumps_latest_metrics_as_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database_path = tmp_path / "metrics.sqlite"
    repository = SQLiteRepository(database_path)
    repository.record_session_metrics(
        date(2026, 9, 10), broker_latency_ms=120, news_blackout_hit=True
    )
    repository.close()

    assert (
        database_cli.main(["--database", str(database_path), "--query", "--limit", "1"])
        == 0
    )

    output = json.loads(capsys.readouterr().out)
    assert output["database"] == str(database_path)
    assert output["session_metrics"][0]["session_date"] == "2026-09-10"
    assert output["session_metrics"][0]["broker_latency_total_ms"] == 120


def test_database_cli_initializes_paths_with_spaces(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database_path = tmp_path / "folder with spaces" / "session_metrics.db"

    assert database_cli.main(["--database", str(database_path)]) == 0

    output = json.loads(capsys.readouterr().out)
    assert output == {"status": "initialized", "database": str(database_path)}
    with sqlite3.connect(database_path) as connection:
        table = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name = 'session_metrics'"
        ).fetchone()
    assert table == ("session_metrics",)


def test_application_startup_initializes_session_metrics_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PAPER_TRADING", "true")
    monkeypatch.setenv("LIVE_TRADING", "false")
    monkeypatch.setenv("BROKER_ENV", "demo")
    monkeypatch.setenv("BROKER_TOKEN", "demo-token")
    monkeypatch.setenv("DAILY_DRAWDOWN_LIMIT", "0.02")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TICK_INTERVAL_SECONDS", "7.5")

    class StopStartup(Exception):
        pass

    sleep_intervals: list[float] = []

    def stop_runtime(seconds: float) -> None:
        sleep_intervals.append(seconds)
        raise StopStartup

    monkeypatch.setattr(application.time, "sleep", stop_runtime)

    with pytest.raises(StopStartup):
        application.main([])
    assert sleep_intervals == [7.5]

    database_path = tmp_path / "session_metrics.db"
    with sqlite3.connect(database_path) as connection:
        table = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name = 'session_metrics'"
        ).fetchone()
    assert table == ("session_metrics",)


def test_application_once_runs_tick_and_exits_without_sleep(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("PAPER_TRADING", "true")
    monkeypatch.setenv("LIVE_TRADING", "false")
    monkeypatch.setenv("BROKER_ENV", "demo")
    monkeypatch.setenv("BROKER_TOKEN", "demo-token")
    monkeypatch.setenv("DAILY_DRAWDOWN_LIMIT", "0.02")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MARKET_DATA_PATH", raising=False)
    monkeypatch.setenv("MAX_DATA_AGE_SECONDS", "999999999")

    def unexpected_sleep(_: float) -> None:
        raise AssertionError("--once must not enter the sleep loop")

    monkeypatch.setattr(application.time, "sleep", unexpected_sleep)
    caplog.set_level(logging.WARNING, logger=application.LOGGER.name)

    application.main(["--once"])
    assert (tmp_path / "market_data.csv").is_file()
    assert not [
        record for record in caplog.records if record.levelno >= logging.WARNING
    ]

    repository = SQLiteRepository(tmp_path / "session_metrics.db")
    latest = repository.get_latest_metrics(limit=1)
    repository.close()
    assert len(latest) == 1


def test_once_uses_safe_defaults_for_missing_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in (
        "PAPER_TRADING",
        "LIVE_TRADING",
        "BROKER_ENV",
        "BROKER_TOKEN",
        "BROKER_ENDPOINT",
        "DAILY_DRAWDOWN_LIMIT",
        "TICK_INTERVAL_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    application.main(["--once"])

    repository = SQLiteRepository(tmp_path / "session_metrics.db")
    assert len(repository.get_latest_metrics(limit=1)) == 1
    repository.close()


def test_run_tick_opens_risk_approved_paper_position(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "session_metrics.db")

    application.run_tick(
        repository,
        data_dir=tmp_path,
        account_equity=Decimal("10000"),
        session_start_equity=Decimal("10000"),
        stop_distance=Decimal("0.0010"),
        daily_drawdown_limit=Decimal("0.05"),
    )

    assert repository.execution_log_count() == 2
    assert len(repository.get_open_positions()) == 1
    repository.close()


def test_observability_redacts_sensitive_keys_and_values() -> None:
    safe = sanitize_fields(
        {"rejection_reason": "timeout", "api_key": "secret", "message": "token=secret"},
        secrets=("secret",),
    )
    assert safe["rejection_reason"] == "timeout"
    assert safe["api_key"] == "[REDACTED]"
    assert safe["message"] == "[REDACTED]"
