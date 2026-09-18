"""Typed broker gateway protocol."""

from typing import Protocol, runtime_checkable

from domain.models import BrokerOrderPayload, ExecutionResult
from execution.broker_adapter import BrokerAccountState


@runtime_checkable
class BrokerGateway(Protocol):
    """The only provider interface the execution gate may invoke."""

    def submit_order(self, payload: BrokerOrderPayload) -> ExecutionResult: ...

    def reconcile_order(self, client_order_id: str) -> ExecutionResult | None: ...


@runtime_checkable
class AccountReconciliationGateway(Protocol):
    """Optional broker capability for restart position reconciliation."""

    def get_account_snapshot(self) -> BrokerAccountState: ...
