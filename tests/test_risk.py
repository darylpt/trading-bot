"""Compatibility coverage for the current typed risk contracts."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from domain.models import OrderIntent


def test_legacy_order_contract_rejects_excess_risk() -> None:
    with pytest.raises(ValidationError):
        OrderIntent(
            client_order_id="legacy",
            instrument="EUR_USD",
            direction="LONG",
            quantity=Decimal("1"),
            entry_price=Decimal("1.1"),
            stop_loss_price=Decimal("1.0"),
            take_profit_price=Decimal("1.2"),
            account_equity=Decimal("100"),
            risk_fraction=Decimal("0.02"),
            signal_timestamp=datetime.now(timezone.utc),
        )
