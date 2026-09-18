"""Per-trade risk and mandatory-exit checks."""

from __future__ import annotations

from decimal import Decimal

from domain.models import Direction, OrderIntent
from execution.broker_adapter import BrokerAccountState, InstrumentMetadata, MarketQuote
from risk.sizing import risk_amount


def validate_order_risk(
    order: OrderIntent,
    *,
    contract_size: Decimal = Decimal("1"),
    pip_value: Decimal = Decimal("1"),
) -> None:
    """Reject an order whose actual stop risk exceeds one percent."""
    actual = risk_amount(
        quantity=order.quantity,
        entry_price=order.entry_price,
        stop_loss_price=order.stop_loss_price,
        contract_size=contract_size,
        pip_value=pip_value,
    )
    if actual > order.account_equity * Decimal("0.01"):
        raise ValueError("actual order risk exceeds one percent")


def calculate_atr_exits(
    *,
    reference_price: Decimal,
    atr: Decimal,
    direction: Direction,
    stop_multiplier: Decimal = Decimal("1"),
    target_multiplier: Decimal = Decimal("2"),
) -> tuple[Decimal, Decimal]:
    """Create hard ATR-based stop-loss and take-profit levels."""
    if (
        not reference_price.is_finite()
        or not atr.is_finite()
        or atr <= 0
        or stop_multiplier <= 0
        or target_multiplier <= 0
    ):
        raise ValueError("ATR exit inputs must be finite and positive")
    stop_distance = atr * stop_multiplier
    target_distance = atr * target_multiplier
    if direction == "LONG":
        return reference_price - stop_distance, reference_price + target_distance
    return reference_price + stop_distance, reference_price - target_distance


def validate_directional_exits(
    *,
    direction: str,
    entry_price: Decimal,
    stop_loss_price: Decimal,
    take_profit_price: Decimal,
) -> None:
    """Require both positive-distance exits with direction-correct prices."""
    if min(entry_price, stop_loss_price, take_profit_price) <= 0:
        raise ValueError("prices must be positive")
    if direction == "LONG" and stop_loss_price < entry_price < take_profit_price:
        return
    if direction == "SHORT" and take_profit_price < entry_price < stop_loss_price:
        return
    raise ValueError("exits are inconsistent with direction")


def validate_broker_order_risk(
    order: OrderIntent,
    *,
    account: BrokerAccountState,
    quote: MarketQuote,
    metadata: InstrumentMetadata,
    max_spread: Decimal,
    required_margin: Decimal = Decimal("0"),
) -> None:
    """Revalidate an order against the current broker snapshot."""
    if order.instrument != quote.instrument or order.instrument != metadata.instrument:
        raise ValueError("broker risk inputs use different instruments")
    if order.account_equity != account.equity:
        raise ValueError("order equity is stale")
    if max_spread < 0 or (max_spread > 0 and quote.spread > max_spread):
        raise ValueError("current spread exceeds configured maximum")
    executable_price = quote.ask if order.direction == "LONG" else quote.bid
    if order.entry_price != executable_price:
        raise ValueError("order entry price is not the executable quote")
    stop_distance = abs(order.entry_price - order.stop_loss_price)
    minimum_distance = max(metadata.stop_level, metadata.freeze_level)
    if stop_distance < minimum_distance:
        raise ValueError("stop distance violates broker stop/freeze constraints")
    if required_margin < 0 or required_margin > account.balance - account.margin:
        raise ValueError("order margin exceeds available margin")
    validate_order_risk(
        order,
        contract_size=metadata.contract_size,
        pip_value=metadata.tick_value / metadata.tick_size,
    )
