from datetime import datetime, timezone
from decimal import Decimal

import pytest

from trading_bot.risk import evaluate_signal
from trading_bot.strategy import Signal, SignalAction


def signal(action: SignalAction = "BUY") -> Signal:
    return Signal(
        action=action,
        timestamp=datetime(2026, 1, 5, tzinfo=timezone.utc),
        reference_price=Decimal("1.1000"),
    )


def test_risk_approves_sized_signal_below_drawdown_limit() -> None:
    decision = evaluate_signal(
        signal(),
        account_equity=Decimal("10000"),
        session_start_equity=Decimal("10000"),
        stop_distance=Decimal("0.0100"),
    )

    assert decision.approved is True
    assert decision.position_size == Decimal("10000.00")
    assert decision.stop_loss_price == Decimal("1.0900")
    assert decision.risk_amount == Decimal("100.0000")


def test_risk_rejects_daily_drawdown_limit() -> None:
    decision = evaluate_signal(
        signal(),
        account_equity=Decimal("9500"),
        session_start_equity=Decimal("10000"),
        stop_distance=Decimal("0.0100"),
        daily_drawdown_limit=Decimal("0.05"),
    )

    assert decision.approved is False
    assert decision.reason == "DAILY_DRAWDOWN_LIMIT_REACHED"
    assert decision.drawdown_fraction == Decimal("0.05")


@pytest.mark.parametrize(
    ("candidate", "expected_reason"),
    [
        (signal("HOLD"), "HOLD_SIGNAL"),
        (signal("BUY"), "ACCOUNT_EQUITY_UNAVAILABLE"),
    ],
)
def test_risk_rejects_non_tradable_signals(
    candidate: Signal, expected_reason: str
) -> None:
    decision = evaluate_signal(
        candidate,
        account_equity=None,
        session_start_equity=None,
        stop_distance=None,
    )

    assert decision.approved is False
    assert decision.reason == expected_reason


def test_risk_rejects_risk_fraction_above_one_percent() -> None:
    decision = evaluate_signal(
        signal(),
        account_equity=Decimal("10000"),
        session_start_equity=Decimal("10000"),
        stop_distance=Decimal("0.0100"),
        risk_fraction=Decimal("0.02"),
    )

    assert decision.approved is False
    assert decision.reason == "RISK_FRACTION_EXCEEDS_ONE_PERCENT"


def test_risk_rejects_non_positive_entry_and_stop_prices() -> None:
    invalid_entry = Signal(
        action="BUY",
        timestamp=datetime(2026, 1, 5, tzinfo=timezone.utc),
        reference_price=Decimal("0"),
    )
    entry_decision = evaluate_signal(
        invalid_entry,
        account_equity=Decimal("10000"),
        session_start_equity=Decimal("10000"),
        stop_distance=Decimal("0.0100"),
    )
    assert entry_decision.reason == "ENTRY_PRICE_INVALID"

    stop_decision = evaluate_signal(
        signal(),
        account_equity=Decimal("10000"),
        session_start_equity=Decimal("10000"),
        stop_distance=Decimal("2"),
    )
    assert stop_decision.reason == "STOP_LOSS_INVALID"
