from datetime import datetime, timezone
from decimal import Decimal

from domain.models import BrokerOrderPayload, ExecutionResult, OrderIntent
from execution.executor import ExecutionGate


class FakeGateway:
    def __init__(
        self, result: ExecutionResult | None = None, error: Exception | None = None
    ) -> None:
        self.calls = 0
        self.result = result
        self.error = error

    def submit_order(self, payload: BrokerOrderPayload) -> ExecutionResult:
        self.calls += 1
        if self.error is not None:
            raise self.error
        assert payload.stop_loss.price < payload.entry_price
        assert payload.take_profit.price > payload.entry_price
        assert payload.environment == "PAPER"
        return self.result or ExecutionResult(
            client_order_id=payload.client_order_id,
            status="ACCEPTED",
            environment="PAPER",
        )

    def reconcile_order(self, client_order_id: str) -> ExecutionResult | None:
        return ExecutionResult(
            client_order_id=client_order_id, status="UNKNOWN", environment="PAPER"
        )


def order() -> OrderIntent:
    return OrderIntent(
        client_order_id="exec-1",
        instrument="EUR_USD",
        direction="LONG",
        quantity=Decimal("20"),
        entry_price=Decimal("1.1"),
        stop_loss_price=Decimal("1.095"),
        take_profit_price=Decimal("1.11"),
        account_equity=Decimal("10000"),
        risk_fraction=Decimal("0.01"),
        signal_timestamp=datetime.now(timezone.utc),
    )


def test_invalid_environment_makes_zero_provider_calls() -> None:
    gateway = FakeGateway()
    result = ExecutionGate(gateway, environment="LIVE").submit(order())
    assert result.status == "REJECTED"
    assert gateway.calls == 0


def test_timeout_is_unknown_and_not_retried() -> None:
    gateway = FakeGateway(error=TimeoutError())
    result = ExecutionGate(gateway, environment="PAPER").submit(order())
    assert result.status == "UNKNOWN"
    assert gateway.calls == 1
    assert ExecutionGate(gateway, environment="PAPER").reconcile("exec-1") is not None
