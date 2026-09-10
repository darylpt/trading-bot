"""Explicit spread, slippage, and overnight swap calculations."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from domain.models import Direction


@dataclass(frozen=True)
class FrictionAssumptions:
    spread: Decimal
    slippage: Decimal
    overnight_swap: Decimal = Decimal("0")
    open_close_spread_multiplier: Decimal = Decimal("1")

    def __post_init__(self) -> None:
        if (
            self.spread < 0
            or self.slippage < 0
            or self.open_close_spread_multiplier < 1
        ):
            raise ValueError("friction values must be non-negative and multiplier >= 1")


def effective_spread(
    assumptions: FrictionAssumptions, *, session_expanded: bool
) -> Decimal:
    return assumptions.spread * (
        assumptions.open_close_spread_multiplier if session_expanded else Decimal(1)
    )


def execution_price(
    reference_price: Decimal,
    direction: Direction,
    assumptions: FrictionAssumptions,
    *,
    session_expanded: bool = False,
) -> Decimal:
    """Apply half-spread and adverse slippage to an entry price."""
    cost = effective_spread(assumptions, session_expanded=session_expanded) / Decimal(2)
    cost += assumptions.slippage
    return reference_price + cost if direction == "LONG" else reference_price - cost


def holding_cost(assumptions: FrictionAssumptions, held_hours: int) -> Decimal:
    if held_hours < 0:
        raise ValueError("held hours cannot be negative")
    return assumptions.overnight_swap * Decimal(held_hours // 24)
