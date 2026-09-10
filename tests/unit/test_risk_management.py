from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from domain.models import OrderIntent


def base_order(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "client_order_id": "order-1",
        "instrument": "EUR_USD",
        "direction": "LONG",
        "quantity": Decimal("100"),
        "entry_price": Decimal("1.1000"),
        "stop_loss_price": Decimal("1.0950"),
        "take_profit_price": Decimal("1.1100"),
        "account_equity": Decimal("10000"),
        "risk_fraction": Decimal("0.01"),
        "signal_timestamp": datetime.now(timezone.utc),
    }
    values.update(overrides)
    return values


def test_order_intent_caps_risk_at_one_percent() -> None:
    OrderIntent(**base_order())
    with pytest.raises(ValidationError):
        OrderIntent(**base_order(risk_fraction=Decimal("0.0101")))


def test_directional_exits_are_mandatory_and_rechecked() -> None:
    with pytest.raises(ValidationError):
        OrderIntent(**base_order(stop_loss_price=Decimal("1.1050")))
    with pytest.raises(ValidationError):
        OrderIntent(**base_order(direction="SHORT"))
