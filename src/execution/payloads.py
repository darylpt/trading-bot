"""Canonical broker payload conversion."""

from __future__ import annotations

from typing import Literal

from domain.models import BrokerOrderPayload, ExitPayload, OrderIntent


def _environment(value: str) -> Literal["PAPER", "DEMO"]:
    if value == "PAPER":
        return "PAPER"
    if value == "DEMO":
        return "DEMO"
    raise ValueError("only paper/demo payloads are permitted")


def payload_from_intent(order: OrderIntent, *, environment: str) -> BrokerOrderPayload:
    safe_environment = _environment(environment)
    return BrokerOrderPayload(
        client_order_id=order.client_order_id,
        instrument=order.instrument,
        direction=order.direction,
        quantity=order.quantity,
        entry_price=order.entry_price,
        stop_loss=ExitPayload(price=order.stop_loss_price),
        take_profit=ExitPayload(price=order.take_profit_price),
        account_equity=order.account_equity,
        risk_fraction=order.risk_fraction,
        environment=safe_environment,
    )
