"""Typed broker gateway protocol."""

from __future__ import annotations

from typing import Protocol

from domain.models import BrokerOrderPayload, ExecutionResult


class BrokerGateway(Protocol):
    """The only provider interface the execution gate may invoke."""

    def submit_order(self, payload: BrokerOrderPayload) -> ExecutionResult: ...

    def reconcile_order(self, client_order_id: str) -> ExecutionResult | None: ...
