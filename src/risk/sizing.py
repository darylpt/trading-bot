"""Dynamic one-percent position sizing."""

from __future__ import annotations

from decimal import Decimal

from execution.broker_adapter import InstrumentMetadata


def calculate_position_size(
    *,
    account_equity: Decimal,
    entry_price: Decimal,
    stop_loss_price: Decimal,
    risk_fraction: Decimal = Decimal("0.01"),
    contract_size: Decimal = Decimal("1"),
    pip_value: Decimal = Decimal("1"),
    minimum_size: Decimal = Decimal("0"),
    maximum_size: Decimal | None = None,
    quantity_step: Decimal = Decimal("0.01"),
) -> Decimal:
    """Calculate size and round down to a broker-safe quantity step."""
    if not quantity_step.is_finite() or quantity_step <= 0:
        raise ValueError("quantity step must be finite and positive")
    values = (
        account_equity,
        entry_price,
        stop_loss_price,
        risk_fraction,
        contract_size,
        pip_value,
    )
    if any(not value.is_finite() for value in values):
        raise ValueError("sizing inputs must be finite")
    if account_equity <= 0 or not (Decimal("0") < risk_fraction <= Decimal("0.01")):
        raise ValueError("equity and risk fraction are invalid")
    distance = abs(entry_price - stop_loss_price)
    if distance <= 0 or contract_size <= 0 or pip_value <= 0:
        raise ValueError("stop distance and instrument multipliers must be positive")
    raw_size = account_equity * risk_fraction / (distance * contract_size * pip_value)
    size = (raw_size // quantity_step) * quantity_step
    if size <= 0 or size < minimum_size:
        raise ValueError("calculated size violates broker minimum")
    if maximum_size is not None and (maximum_size <= 0 or size > maximum_size):
        raise ValueError("calculated size violates broker maximum")
    return size


def calculate_broker_position_size(
    *,
    account_equity: Decimal,
    entry_price: Decimal,
    stop_loss_price: Decimal,
    metadata: InstrumentMetadata,
    risk_fraction: Decimal = Decimal("0.01"),
) -> Decimal:
    """Size a position using the broker's tick and quantity constraints."""
    if metadata.tick_size <= 0 or metadata.tick_value <= 0:
        raise ValueError("broker tick metadata must be positive")
    pip_value = metadata.tick_value / metadata.tick_size
    return calculate_position_size(
        account_equity=account_equity,
        entry_price=entry_price,
        stop_loss_price=stop_loss_price,
        risk_fraction=risk_fraction,
        contract_size=metadata.contract_size,
        pip_value=pip_value,
        minimum_size=metadata.minimum_quantity,
        maximum_size=metadata.maximum_quantity,
        quantity_step=metadata.quantity_step,
    )


def risk_amount(
    *,
    quantity: Decimal,
    entry_price: Decimal,
    stop_loss_price: Decimal,
    contract_size: Decimal = Decimal("1"),
    pip_value: Decimal = Decimal("1"),
) -> Decimal:
    """Return worst-case stop risk for a proposed quantity."""
    if quantity <= 0 or contract_size <= 0 or pip_value <= 0:
        raise ValueError("quantity and multipliers must be positive")
    return quantity * abs(entry_price - stop_loss_price) * contract_size * pip_value
