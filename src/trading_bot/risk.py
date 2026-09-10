"""Fail-closed signal approval and position sizing guardrails."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from risk.sizing import calculate_position_size, risk_amount
from trading_bot.strategy import Signal

MAX_RISK_FRACTION = Decimal("0.01")


@dataclass(frozen=True)
class RiskDecision:
    """Observable risk approval result for one strategy signal."""

    approved: bool
    reason: str
    drawdown_fraction: Decimal = Decimal("0")
    position_size: Decimal | None = None
    stop_loss_price: Decimal | None = None
    risk_amount: Decimal = Decimal("0")


def _rejected(
    reason: str, *, drawdown_fraction: Decimal = Decimal("0")
) -> RiskDecision:
    return RiskDecision(
        approved=False,
        reason=reason,
        drawdown_fraction=drawdown_fraction,
    )


def evaluate_signal(
    signal: Signal,
    *,
    account_equity: Decimal | None,
    session_start_equity: Decimal | None,
    stop_distance: Decimal | None,
    daily_drawdown_limit: Decimal = Decimal("0.05"),
    risk_fraction: Decimal = MAX_RISK_FRACTION,
    contract_size: Decimal = Decimal("1"),
    pip_value: Decimal = Decimal("1"),
    minimum_size: Decimal = Decimal("0"),
    maximum_size: Decimal | None = None,
    quantity_step: Decimal = Decimal("0.01"),
) -> RiskDecision:
    """Approve a signal only when equity, drawdown, exits, and size are valid."""
    if signal.action == "HOLD":
        return _rejected("HOLD_SIGNAL")
    if not daily_drawdown_limit.is_finite() or not (
        Decimal("0") < daily_drawdown_limit <= Decimal("1")
    ):
        return _rejected("INVALID_DAILY_DRAWDOWN_LIMIT")
    if not risk_fraction.is_finite() or not (
        Decimal("0") < risk_fraction <= MAX_RISK_FRACTION
    ):
        return _rejected("RISK_FRACTION_EXCEEDS_ONE_PERCENT")
    if account_equity is None or session_start_equity is None:
        return _rejected("ACCOUNT_EQUITY_UNAVAILABLE")
    if not account_equity.is_finite() or not session_start_equity.is_finite():
        return _rejected("ACCOUNT_EQUITY_INVALID")
    if account_equity <= 0 or session_start_equity <= 0:
        return _rejected("ACCOUNT_EQUITY_INVALID")

    drawdown_fraction = max(
        (session_start_equity - account_equity) / session_start_equity,
        Decimal("0"),
    )
    if drawdown_fraction >= daily_drawdown_limit:
        return _rejected(
            "DAILY_DRAWDOWN_LIMIT_REACHED",
            drawdown_fraction=drawdown_fraction,
        )
    if signal.reference_price is None or not signal.reference_price.is_finite():
        return _rejected("ENTRY_PRICE_UNAVAILABLE", drawdown_fraction=drawdown_fraction)
    if stop_distance is None or not stop_distance.is_finite() or stop_distance <= 0:
        return _rejected("STOP_DISTANCE_INVALID", drawdown_fraction=drawdown_fraction)

    stop_loss_price = (
        signal.reference_price - stop_distance
        if signal.action == "BUY"
        else signal.reference_price + stop_distance
    )
    try:
        position_size = calculate_position_size(
            account_equity=account_equity,
            entry_price=signal.reference_price,
            stop_loss_price=stop_loss_price,
            risk_fraction=risk_fraction,
            contract_size=contract_size,
            pip_value=pip_value,
            minimum_size=minimum_size,
            maximum_size=maximum_size,
            quantity_step=quantity_step,
        )
        actual_risk = risk_amount(
            quantity=position_size,
            entry_price=signal.reference_price,
            stop_loss_price=stop_loss_price,
            contract_size=contract_size,
            pip_value=pip_value,
        )
    except ValueError as exc:
        return _rejected(
            f"POSITION_SIZE_REJECTED: {exc}",
            drawdown_fraction=drawdown_fraction,
        )

    return RiskDecision(
        approved=True,
        reason="APPROVED",
        drawdown_fraction=drawdown_fraction,
        position_size=position_size,
        stop_loss_price=stop_loss_price,
        risk_amount=actual_risk,
    )
