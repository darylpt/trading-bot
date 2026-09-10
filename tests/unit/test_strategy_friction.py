from datetime import datetime, timedelta, timezone
from decimal import Decimal

from strategy.friction import FrictionAssumptions, effective_spread, execution_price
from strategy.sessions import can_enter, is_market_open, is_stale


def test_expanded_spread_changes_entry_cost() -> None:
    assumptions = FrictionAssumptions(
        Decimal("0.0001"), Decimal("0.00002"), open_close_spread_multiplier=Decimal("3")
    )
    normal = execution_price(Decimal("1.1"), "LONG", assumptions)
    expanded = execution_price(
        Decimal("1.1"), "LONG", assumptions, session_expanded=True
    )
    assert expanded > normal
    assert effective_spread(assumptions, session_expanded=True) == Decimal("0.0003")


def test_weekend_and_stale_data_block_entries() -> None:
    weekend = datetime(2026, 1, 10, 12, tzinfo=timezone.utc)
    now = datetime(2026, 1, 9, 12, tzinfo=timezone.utc)
    observed = now - timedelta(minutes=21)
    assert is_market_open(weekend) is False
    assert is_stale(observed, now) is True
    assert can_enter(observed_at=now, now=now) is True
