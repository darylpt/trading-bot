from decimal import Decimal
from pathlib import Path

from persistence.sqlite import SQLiteRepository
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
    assert repository.execution_log_count() == 2
    repository.close()
