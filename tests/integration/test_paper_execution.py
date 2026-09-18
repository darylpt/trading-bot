from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from domain.models import MarketCandle
from persistence.sqlite import SQLiteRepository
from execution.broker_adapter import BrokerConnectionError
from trading_bot import __main__ as application
from trading_bot.market_data_seed import ensure_default_market_data


def test_run_tick_opens_then_closes_paper_position_on_stop(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    ensure_default_market_data(data_dir)
    repository = SQLiteRepository(tmp_path / "session_metrics.db")

    first_metrics = application.run_tick(
        repository,
        data_dir=data_dir,
        account_equity=Decimal("10000"),
        session_start_equity=Decimal("10000"),
        stop_distance=Decimal("0.0100"),
    )
    assert first_metrics.closed_trades == 0
    assert len(repository.get_open_positions()) == 1

    second_metrics = application.run_tick(
        repository,
        data_dir=data_dir,
        account_equity=Decimal("10000"),
        session_start_equity=Decimal("10000"),
        stop_distance=Decimal("0.0100"),
    )

    assert repository.get_open_positions() == []
    assert second_metrics.closed_trades == 1
    assert second_metrics.losing_trades == 1
    assert second_metrics.realized_pnl < 0
    assert repository.execution_log_count() == 4
    repository.close()


def test_live_market_data_failure_does_not_revert_to_stale_csv(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    ensure_default_market_data(data_dir)
    repository = SQLiteRepository(tmp_path / "session_metrics.db")

    class FailingGateway:
        def poll(self) -> tuple[object, ...]:
            raise BrokerConnectionError("quote feed unavailable")

    application.run_tick(
        repository,
        data_dir=data_dir,
        account_equity=Decimal("10000"),
        session_start_equity=Decimal("10000"),
        stop_distance=Decimal("0.0100"),
        market_data_gateway=FailingGateway(),
    )

    assert repository.get_open_positions() == []
    assert repository.execution_log_count() == 2
    repository.close()


def test_stale_market_data_halts_entries_and_persists_status(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "session_metrics.db")
    now = datetime(2026, 9, 12, tzinfo=timezone.utc)
    stale = MarketCandle(
        instrument="BTC_USD",
        timeframe="15m",
        timestamp=now - timedelta(seconds=301),
        open=Decimal("60000"),
        high=Decimal("60100"),
        low=Decimal("59900"),
        close=Decimal("60050"),
        volume=Decimal("10"),
    )

    class Gateway:
        def poll(self) -> tuple[MarketCandle, ...]:
            return (stale,)

    application.run_tick(
        repository,
        current_time=now,
        account_equity=Decimal("10000"),
        session_start_equity=Decimal("10000"),
        stop_distance=Decimal("100"),
        market_data_gateway=Gateway(),
        max_data_age_seconds=300,
    )

    status = repository.get_circuit_breaker()
    assert status is not None
    assert status.status == "HALTED"
    assert "stale" in status.reason
    assert repository.get_open_positions() == []
    repository.close()


def test_emergency_halt_blocks_entries_but_keeps_tick_running(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    ensure_default_market_data(data_dir)
    repository = SQLiteRepository(tmp_path / "session_metrics.db")
    repository.set_trading_halt(True, reason="operator emergency halt")

    application.run_tick(
        repository,
        data_dir=data_dir,
        account_equity=Decimal("10000"),
        session_start_equity=Decimal("10000"),
        stop_distance=Decimal("0.0100"),
    )

    assert repository.get_trading_halt() == (True, "operator emergency halt")
    assert repository.get_open_positions() == []
    repository.close()
