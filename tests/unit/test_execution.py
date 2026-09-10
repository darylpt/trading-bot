from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from domain.models import MarketCandle
from persistence.sqlite import SQLiteRepository
from trading_bot.execution import PaperExecutionEngine
from trading_bot.risk import RiskDecision
from trading_bot.strategy import Signal


def candle(
    *,
    close: str = "1.1000",
    high: str | None = None,
    low: str | None = None,
) -> MarketCandle:
    close_price = Decimal(close)
    return MarketCandle(
        instrument="EUR_USD",
        timeframe="15m",
        timestamp=datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc),
        open=close_price,
        high=Decimal(high or close),
        low=Decimal(low or close),
        close=close_price,
        volume=Decimal("100"),
    )


def buy_signal(price: str = "1.1000") -> Signal:
    return Signal(
        action="BUY",
        timestamp=datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc),
        reference_price=Decimal(price),
    )


def approved_buy() -> RiskDecision:
    return RiskDecision(
        approved=True,
        reason="APPROVED",
        position_size=Decimal("1000"),
        stop_loss_price=Decimal("1.0900"),
        risk_amount=Decimal("10"),
    )


def test_paper_engine_opens_and_persists_directional_position(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "paper.sqlite")
    engine = PaperExecutionEngine(repository)

    outcome = engine.process_tick(
        (candle(),),
        signal=buy_signal(),
        risk_decision=approved_buy(),
        account_equity=Decimal("1000"),
    )

    assert outcome.opened_position is not None
    assert outcome.opened_position.direction == "LONG"
    assert outcome.opened_position.take_profit_price == Decimal("1.1200")
    assert repository.get_open_positions() == [outcome.opened_position]
    assert repository.trade_count() == 1
    repository.close()


def test_paper_engine_stop_loss_closes_and_records_loss_metrics(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "paper.sqlite")
    engine = PaperExecutionEngine(repository)
    engine.process_tick(
        (candle(),),
        signal=buy_signal(),
        risk_decision=approved_buy(),
        account_equity=Decimal("1000"),
    )

    outcome = engine.process_tick(
        (candle(close="1.0950", high="1.0960", low="1.0850"),),
        signal=None,
        risk_decision=None,
        account_equity=Decimal("1000"),
    )

    assert len(outcome.closed_positions) == 1
    closed = outcome.closed_positions[0]
    assert closed.exit_reason == "STOP_LOSS"
    assert closed.exit_price == Decimal("1.0900")
    assert closed.realized_pnl == Decimal("-10.0000")
    metrics = repository.get_session_metrics(datetime(2026, 1, 5).date())
    assert metrics is not None
    assert metrics.realized_pnl == Decimal("-10")
    assert metrics.closed_trades == 1
    assert metrics.losing_trades == 1
    repository.close()


def test_paper_engine_counter_signal_closes_without_reversing(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "paper.sqlite")
    engine = PaperExecutionEngine(repository)
    engine.process_tick(
        (candle(),),
        signal=buy_signal(),
        risk_decision=approved_buy(),
        account_equity=Decimal("1000"),
    )
    sell = Signal(
        action="SELL",
        timestamp=datetime(2026, 1, 5, 12, 15, tzinfo=timezone.utc),
        reference_price=Decimal("1.1150"),
    )

    outcome = engine.process_tick(
        (candle(close="1.1150"),),
        signal=sell,
        risk_decision=None,
        account_equity=Decimal("1000"),
    )

    assert len(outcome.closed_positions) == 1
    assert outcome.closed_positions[0].exit_reason == "COUNTER_SIGNAL"
    assert outcome.closed_positions[0].realized_pnl == Decimal("15.0000")
    assert repository.get_open_positions() == []
    repository.close()


def test_paper_engine_take_profit_closes_and_records_win(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "paper.sqlite")
    engine = PaperExecutionEngine(repository)
    engine.process_tick(
        (candle(),),
        signal=buy_signal(),
        risk_decision=approved_buy(),
        account_equity=Decimal("1000"),
    )

    outcome = engine.process_tick(
        (candle(close="1.1200", high="1.1250", low="1.1150"),),
        signal=None,
        risk_decision=None,
        account_equity=Decimal("1000"),
    )

    assert len(outcome.closed_positions) == 1
    assert outcome.closed_positions[0].exit_reason == "TAKE_PROFIT"
    assert outcome.closed_positions[0].exit_price == Decimal("1.1200")
    assert outcome.closed_positions[0].realized_pnl == Decimal("20.0000")
    metrics = repository.get_session_metrics(datetime(2026, 1, 5).date())
    assert metrics is not None
    assert metrics.winning_trades == 1
    repository.close()


def test_paper_engine_rejects_unapproved_signal_without_position(
    tmp_path: Path,
) -> None:
    repository = SQLiteRepository(tmp_path / "paper.sqlite")
    engine = PaperExecutionEngine(repository)
    outcome = engine.process_tick(
        (candle(),),
        signal=buy_signal(),
        risk_decision=RiskDecision(
            approved=False, reason="DAILY_DRAWDOWN_LIMIT_REACHED"
        ),
        account_equity=Decimal("1000"),
    )

    assert outcome.opened_position is None
    assert outcome.rejection_reason == "DAILY_DRAWDOWN_LIMIT_REACHED"
    assert repository.get_open_positions() == []
    assert repository.trade_count() == 0
    repository.close()
