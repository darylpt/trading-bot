"""The single rejecting broker submission wrapper."""

from __future__ import annotations

from typing import Literal

from domain.models import ExecutionResult, OrderIntent
from execution.payloads import payload_from_intent
from execution.protocols import BrokerGateway
from execution.broker_adapter import BrokerConnectionError
from risk.limits import validate_order_risk


def _execution_environment(value: str) -> Literal["PAPER", "DEMO"] | None:
    if value == "PAPER":
        return "PAPER"
    if value == "DEMO":
        return "DEMO"
    return None


class ExecutionGate:
    """Revalidate and submit one safe paper/demo order at most once."""

    def __init__(self, gateway: BrokerGateway, *, environment: str) -> None:
        self._gateway = gateway
        self._environment = environment

    def submit(self, order: OrderIntent) -> ExecutionResult:
        environment = _execution_environment(self._environment.upper())
        if environment is None:
            return ExecutionResult(
                client_order_id=order.client_order_id,
                status="REJECTED",
                rejection_reason="unsafe execution environment",
                environment="PAPER",
            )
        try:
            validate_order_risk(order)
            payload = payload_from_intent(order, environment=environment)
        except ValueError as exc:
            return ExecutionResult(
                client_order_id=order.client_order_id,
                status="REJECTED",
                rejection_reason=str(exc),
                environment=environment,
            )
        try:
            return self._gateway.submit_order(payload)
        except BrokerConnectionError as exc:
            return ExecutionResult(
                client_order_id=order.client_order_id,
                status="REJECTED",
                rejection_reason=type(exc).__name__,
                environment=environment,
            )
        except (TimeoutError, ConnectionError) as exc:
            return ExecutionResult(
                client_order_id=order.client_order_id,
                status="UNKNOWN",
                rejection_reason=type(exc).__name__,
                environment=environment,
            )

    def reconcile(self, client_order_id: str) -> ExecutionResult | None:
        """Reconcile an unknown client ID; never blindly retry submission."""
        return self._gateway.reconcile_order(client_order_id)
