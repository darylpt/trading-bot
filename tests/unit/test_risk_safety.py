from datetime import date, datetime, time, timezone
from decimal import Decimal

import pytest

from domain.models import AccountSnapshot, DailyDrawdownState, OrderIntent
from risk.drawdown import evaluate_daily_drawdown, reset_daily_drawdown
from risk.limits import validate_directional_exits, validate_order_risk
from risk.sizing import calculate_position_size


def test_dynamic_size_and_actual_risk_are_capped() -> None:
    size = calculate_position_size(
        account_equity=Decimal("10000"),
        entry_price=Decimal("1.1"),
        stop_loss_price=Decimal("1.095"),
    )
    assert size == Decimal("20000")
    order = OrderIntent(
        client_order_id="risk-1",
        instrument="EUR_USD",
        direction="LONG",
        quantity=size,
        entry_price=Decimal("1.1"),
        stop_loss_price=Decimal("1.095"),
        take_profit_price=Decimal("1.11"),
        account_equity=Decimal("10000"),
        risk_fraction=Decimal("0.01"),
        signal_timestamp=datetime.now(timezone.utc),
    )
    validate_order_risk(order)
    with pytest.raises(ValueError):
        validate_order_risk(order.model_copy(update={"quantity": Decimal("20001")}))


def test_drawdown_halts_at_threshold_and_requires_fresh_reset() -> None:
    state = DailyDrawdownState(
        trading_day=date(2026, 1, 5),
        baseline_equity=Decimal("10000"),
        realized_pnl=Decimal("-100"),
        unrealized_pnl=Decimal("0"),
        drawdown_limit=Decimal("0.01"),
    )
    halted = evaluate_daily_drawdown(
        state, now=datetime(2026, 1, 5, tzinfo=timezone.utc)
    )
    assert halted.halt_active is True
    snapshot = AccountSnapshot(
        account_id="paper",
        equity=Decimal("9900"),
        balance=Decimal("9900"),
        captured_at=datetime(2026, 1, 6, 0, 1, tzinfo=timezone.utc),
        environment="PAPER",
    )
    reset = reset_daily_drawdown(
        halted,
        snapshot,
        now=datetime(2026, 1, 6, 0, 1, tzinfo=timezone.utc),
        boundary=time(0, 0),
    )
    assert reset.halt_active is False


def test_directional_exit_validation_rejects_invalid_prices() -> None:
    validate_directional_exits(
        direction="SHORT",
        entry_price=Decimal("1.1"),
        stop_loss_price=Decimal("1.11"),
        take_profit_price=Decimal("1.09"),
    )
    with pytest.raises(ValueError):
        validate_directional_exits(
            direction="LONG",
            entry_price=Decimal("1.1"),
            stop_loss_price=Decimal("1.11"),
            take_profit_price=Decimal("1.09"),
        )
