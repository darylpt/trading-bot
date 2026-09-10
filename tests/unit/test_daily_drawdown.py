from datetime import date, datetime, timezone
from decimal import Decimal

from domain.models import DailyDrawdownState
from risk.drawdown import evaluate_daily_drawdown


def test_realized_plus_unrealized_loss_activates_halt() -> None:
    state = DailyDrawdownState(
        trading_day=date(2026, 1, 5),
        baseline_equity=Decimal("10000"),
        realized_pnl=Decimal("-50"),
        unrealized_pnl=Decimal("-50"),
        drawdown_limit=Decimal("0.01"),
    )
    result = evaluate_daily_drawdown(
        state, now=datetime(2026, 1, 5, tzinfo=timezone.utc)
    )
    assert result.halt_active is True
    assert result.halt_reason == "daily drawdown limit reached"
