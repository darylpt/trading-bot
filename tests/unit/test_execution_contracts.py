from datetime import datetime, timezone
from decimal import Decimal

from domain.models import OrderIntent
from execution.payloads import payload_from_intent


def test_canonical_payload_retains_protective_exits() -> None:
    order = OrderIntent(
        client_order_id="contract-1",
        instrument="EUR_USD",
        direction="SHORT",
        quantity=Decimal("20"),
        entry_price=Decimal("1.1"),
        stop_loss_price=Decimal("1.105"),
        take_profit_price=Decimal("1.09"),
        account_equity=Decimal("10000"),
        risk_fraction=Decimal("0.01"),
        signal_timestamp=datetime.now(timezone.utc),
    )
    payload = payload_from_intent(order, environment="DEMO")
    assert payload.stop_loss.price == order.stop_loss_price
    assert payload.take_profit.price == order.take_profit_price
