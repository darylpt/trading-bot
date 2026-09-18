from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from persistence.sqlite import ExecutionLogRecord, SQLiteRepository
from trading_bot import __main__ as application
from trading_bot.runtime_config import RuntimeConfig


def test_runtime_config_persists_active_instrument(tmp_path: Path) -> None:
    path = tmp_path / "session_metrics.db"
    config = RuntimeConfig(path)

    assert config.get_active_instrument() == "BTC_USD"
    config.set_active_instrument("ETH_USD")

    assert RuntimeConfig(path).get_active_instrument() == "ETH_USD"
    with pytest.raises(ValueError):
        config.set_active_instrument("NOT_SUPPORTED")


def test_runtime_config_persists_and_switches_active_strategy(tmp_path: Path) -> None:
    config = RuntimeConfig(tmp_path / "session_metrics.db")

    assert config.get_active_strategy() == "ema_crossover"
    config.set_active_strategy("breakout_channel")
    assert config.get_active_strategy() == "breakout_channel"
    with pytest.raises(ValueError):
        config.set_active_strategy("not_registered")


def test_run_tick_reads_updated_instrument_on_next_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = SQLiteRepository(tmp_path / "session_metrics.db")
    observed: list[str] = []

    def fake_fetch(
        data_dir: Path, *, instrument: str, minimum_history: int
    ) -> tuple[object, ...]:
        observed.append(instrument)
        return ()

    monkeypatch.setattr(application, "_fetch_market_data", fake_fetch)
    runtime_config = RuntimeConfig(repository.path)
    runtime_config.set_active_instrument("ETH_USD")
    application.run_tick(
        repository,
        data_dir=tmp_path,
        account_equity=Decimal("10000"),
        session_start_equity=Decimal("10000"),
        current_time=datetime(2026, 1, 5, tzinfo=timezone.utc),
    )
    runtime_config.set_active_instrument("BTC_USD")
    application.run_tick(
        repository,
        data_dir=tmp_path,
        account_equity=Decimal("10000"),
        session_start_equity=Decimal("10000"),
        current_time=datetime(2026, 1, 5, 0, 15, tzinfo=timezone.utc),
    )

    assert observed == ["ETH_USD", "BTC_USD"]
    repository.close()


def test_run_tick_reads_active_strategy_each_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = SQLiteRepository(tmp_path / "session_metrics.db")
    runtime_config = RuntimeConfig(repository.path)
    observed: list[str] = []

    def fake_fetch(
        data_dir: Path, *, instrument: str, minimum_history: int
    ) -> tuple[object, ...]:
        return ()

    def fake_evaluate(
        name: str,
        candles: tuple[object, ...],
        *,
        current_time: datetime,
        instrument: str,
    ) -> object:
        observed.append(name)
        return application.Signal(
            action="HOLD",
            timestamp=current_time,
            instrument=instrument,
            strategy_name=name,
            rationale=f"{name} evaluated",
        )

    monkeypatch.setattr(application, "_fetch_market_data", fake_fetch)
    monkeypatch.setattr(application, "evaluate_strategy", fake_evaluate)
    for strategy in ("rsi_mean_reversion", "breakout_channel"):
        runtime_config.set_active_strategy(strategy)
        application.run_tick(
            repository,
            data_dir=tmp_path,
            account_equity=Decimal("10000"),
            session_start_equity=Decimal("10000"),
            current_time=datetime(2026, 1, 5, tzinfo=timezone.utc),
        )

    assert observed == ["rsi_mean_reversion", "breakout_channel"]
    repository.close()


def test_execution_status_log_persists_instrument(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "session_metrics.db")
    repository.save_execution_log(
        ExecutionLogRecord(
            client_order_id="tick-1",
            instrument="BTC_USD",
            event_type="STRATEGY_HOLD",
            provider="strategy",
            error_class=None,
            message="Fast EMA (77.245) < Slow EMA (77.310)",
            decision_rationale="Fast EMA (77.245) < Slow EMA (77.310) — No bullish crossover",
            reference_price=Decimal("77.280"),
            fast_average=Decimal("77.245"),
            slow_average=Decimal("77.310"),
            distance_to_crossover=Decimal("-0.065"),
        )
    )

    row = repository.connection.execute(
        """SELECT instrument, decision_rationale, reference_price, fast_average,
                  slow_average, distance_to_crossover
           FROM execution_logs WHERE client_order_id = ?""",
        ("tick-1",),
    ).fetchone()
    assert row is not None
    assert row[0] == "BTC_USD"
    assert "No bullish crossover" in row[1]
    assert row[2] == 77.28
    assert row[3] == 77.245
    assert row[4] == 77.31
    assert row[5] == -0.065
    repository.close()
