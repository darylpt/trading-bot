from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from domain.models import BrokerOrderPayload, ExitPayload, MarketCandle, OrderIntent


def test_market_candle_rejects_inconsistent_ohlc() -> None:
    with pytest.raises(ValidationError):
        MarketCandle(
            instrument="EUR_USD",
            timeframe="15m",
            timestamp=datetime.now(timezone.utc),
            open=Decimal("1.1"),
            high=Decimal("1.09"),
            low=Decimal("1.08"),
            close=Decimal("1.1"),
        )


def test_order_intent_requires_directional_exits() -> None:
    common = dict(
        client_order_id="order-1",
        instrument="EUR_USD",
        direction="LONG",
        quantity=Decimal("100"),
        entry_price=Decimal("1.1"),
        account_equity=Decimal("10000"),
        risk_fraction=Decimal("0.01"),
        signal_timestamp=datetime.now(timezone.utc),
    )
    with pytest.raises(ValidationError):
        OrderIntent(
            stop_loss_price=Decimal("1.11"), take_profit_price=Decimal("1.12"), **common
        )


def test_broker_payload_preserves_both_exits() -> None:
    payload = BrokerOrderPayload(
        client_order_id="order-1",
        instrument="EUR_USD",
        direction="SHORT",
        quantity=Decimal("100"),
        entry_price=Decimal("1.1"),
        stop_loss=ExitPayload(price=Decimal("1.11")),
        take_profit=ExitPayload(price=Decimal("1.09")),
        account_equity=Decimal("10000"),
        risk_fraction=Decimal("0.01"),
        environment="DEMO",
    )
    assert payload.stop_loss.price > payload.entry_price
    assert payload.take_profit.price < payload.entry_price
