"""The single rejecting broker submission wrapper."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Literal

from domain.models import ExecutionResult, OrderIntent
from execution.broker_adapter import (
    BrokerAccountState,
    BrokerConnectionError,
    OpenPosition,
)
from execution.payloads import payload_from_intent
from execution.protocols import AccountReconciliationGateway, BrokerGateway
from persistence.sqlite import (
    PositionRecord,
    SQLiteRepository,
    TradeRecord,
)
from risk.limits import validate_order_risk


def _record_from_order(
    order: OrderIntent,
    *,
    environment: Literal["PAPER", "DEMO"],
) -> TradeRecord:
    return TradeRecord(
        client_order_id=order.client_order_id,
        instrument=order.instrument,
        direction=order.direction,
        quantity=order.quantity,
        entry_price=order.entry_price,
        stop_loss_price=order.stop_loss_price,
        take_profit_price=order.take_profit_price,
        account_equity=order.account_equity,
        risk_fraction=order.risk_fraction,
        status="PENDING_SUBMISSION",
        environment=environment,
    )


def _position_from_broker(
    record: TradeRecord,
    broker_position: OpenPosition,
) -> PositionRecord:
    return PositionRecord(
        position_id=record.client_order_id,
        client_order_id=record.client_order_id,
        instrument=broker_position.instrument,
        direction=broker_position.direction,
        quantity=broker_position.quantity,
        entry_price=broker_position.entry_price,
        stop_loss_price=record.stop_loss_price,
        take_profit_price=record.take_profit_price,
        status="OPEN",
        opened_at=datetime.now(timezone.utc),
    )


def _execution_environment(value: str) -> Literal["PAPER", "DEMO"] | None:
    if value == "PAPER":
        return "PAPER"
    if value == "DEMO":
        return "DEMO"
    return None


class ExecutionGate:
    """Revalidate and submit one safe paper/demo order at most once."""

    def __init__(
        self,
        gateway: BrokerGateway,
        *,
        environment: str,
        repository: SQLiteRepository | None = None,
        revalidate: Callable[[OrderIntent], None] | None = None,
    ) -> None:
        self._gateway = gateway
        self._environment = environment
        self._repository = repository
        self._revalidate = revalidate

    def _result(
        self,
        order: OrderIntent,
        *,
        status: Literal["REJECTED", "UNKNOWN"],
        reason: str,
        environment: Literal["PAPER", "DEMO"],
    ) -> ExecutionResult:
        return ExecutionResult(
            client_order_id=order.client_order_id,
            status=status,
            rejection_reason=reason,
            environment=environment,
        )

    def submit(self, order: OrderIntent) -> ExecutionResult:
        environment = _execution_environment(self._environment.upper())
        if environment is None:
            return self._result(
                order,
                status="REJECTED",
                reason="unsafe execution environment",
                environment="PAPER",
            )
        try:
            if self._revalidate is not None:
                self._revalidate(order)
            validate_order_risk(order)
            payload = payload_from_intent(order, environment=environment)
        except ValueError as exc:
            return self._result(
                order,
                status="REJECTED",
                reason=str(exc),
                environment=environment,
            )

        if self._repository is not None:
            existing = self._repository.get_trade(order.client_order_id)
            if existing is not None:
                if existing.status in {"FILLED", "ACCEPTED"}:
                    return self._guard_protection(
                        ExecutionResult(
                            client_order_id=order.client_order_id,
                            status=(
                                "FILLED" if existing.status == "FILLED" else "ACCEPTED"
                            ),
                            environment=environment,
                        )
                    )
                if existing.status in {"PENDING_SUBMISSION", "PENDING", "UNKNOWN"}:
                    reconciled = self._reconcile_order(order.client_order_id)
                    if reconciled is not None:
                        reconciled = self._guard_protection(reconciled)
                    if reconciled is not None and reconciled.status != "UNKNOWN":
                        self._repository.update_trade_status(
                            order.client_order_id,
                            status=reconciled.status,
                            rejection_reason=reconciled.rejection_reason,
                        )
                        return reconciled
                    return self._result(
                        order,
                        status="UNKNOWN",
                        reason="order requires reconciliation",
                        environment=environment,
                    )
                return self._result(
                    order,
                    status="REJECTED",
                    reason=existing.rejection_reason or "previously rejected",
                    environment=environment,
                )
            inserted = self._repository.save_trade(
                _record_from_order(order, environment=environment)
            )
            if not inserted:
                reconciled = self._reconcile_order(order.client_order_id)
                if reconciled is not None:
                    reconciled = self._guard_protection(reconciled)
                if reconciled is not None and reconciled.status != "UNKNOWN":
                    self._repository.update_trade_status(
                        order.client_order_id,
                        status=reconciled.status,
                        rejection_reason=reconciled.rejection_reason,
                    )
                    return reconciled
                return self._result(
                    order,
                    status="UNKNOWN",
                    reason="order intent already exists and requires reconciliation",
                    environment=environment,
                )
        try:
            result = self._gateway.submit_order(payload)
        except (BrokerConnectionError, TimeoutError, ConnectionError) as exc:
            is_demo = self._environment.upper() == "DEMO"
            result = self._result(
                order,
                status="UNKNOWN"
                if is_demo or isinstance(exc, TimeoutError)
                else "REJECTED",
                reason=type(exc).__name__,
                environment=environment,
            )
        result = self._guard_protection(result)
        if self._repository is not None:
            self._repository.update_trade_status(
                order.client_order_id,
                status=result.status,
                rejection_reason=result.rejection_reason,
            )
            if self._environment.upper() == "DEMO" and result.status == "UNKNOWN":
                self._repository.set_trading_halt(
                    True, reason="broker execution state is UNKNOWN"
                )
        return result

    def _guard_protection(self, result: ExecutionResult) -> ExecutionResult:
        if (
            self._environment.upper() == "DEMO"
            and result.status in {"ACCEPTED", "FILLED", "PARTIALLY_FILLED"}
            and result.protection_confirmed is not True
        ):
            if self._repository is not None:
                self._repository.set_trading_halt(
                    True, reason="broker protective exits are unconfirmed"
                )
            return result.model_copy(
                update={
                    "status": "UNKNOWN",
                    "rejection_reason": "broker protective exits are unconfirmed",
                }
            )
        return result

    def _reconcile_order(self, client_order_id: str) -> ExecutionResult | None:
        """Read an order state; never blindly retry submission."""
        try:
            return self._gateway.reconcile_order(client_order_id)
        except (BrokerConnectionError, TimeoutError, ConnectionError):
            if self._repository is not None and self._environment.upper() == "DEMO":
                self._repository.set_trading_halt(
                    True, reason="broker order reconciliation unavailable"
                )
            return None

    def reconcile_pending(self) -> tuple[ExecutionResult, ...]:
        """Reconcile every durable intent left unresolved across a restart."""
        if self._repository is None:
            return ()
        results: list[ExecutionResult] = []
        for record in self._repository.get_unresolved_trades():
            result = self._reconcile_order(record.client_order_id)
            if result is not None:
                result = self._guard_protection(result)
            if result is None:
                continue
            results.append(result)
            if result.status != "UNKNOWN":
                self._repository.update_trade_status(
                    record.client_order_id,
                    status=result.status,
                    rejection_reason=result.rejection_reason,
                )
            elif self._environment.upper() == "DEMO":
                self._repository.set_trading_halt(
                    True, reason="broker order reconciliation is UNKNOWN"
                )
        return tuple(results)

    def reconcile(
        self, client_order_id: str | None = None
    ) -> ExecutionResult | tuple[ExecutionResult, ...] | None:
        """Reconcile one order or all durable orders and account state."""
        if client_order_id is not None:
            reconciled = self._reconcile_order(client_order_id)
            return None if reconciled is None else self._guard_protection(reconciled)
        results = self.reconcile_pending()
        self.reconcile_account()
        return results

    def _reconstruct_positions(self, account: BrokerAccountState) -> None:
        if self._repository is None:
            return
        candidates = self._repository.get_reconcilable_trades()
        open_positions = account.open_positions
        for broker_position in open_positions:
            matches = [
                record
                for record in candidates
                if record.instrument == broker_position.instrument
                and record.direction == broker_position.direction
                and record.quantity == broker_position.quantity
            ]
            if len(matches) != 1:
                continue
            record = matches[0]
            self._repository.update_trade_status(
                record.client_order_id, status="FILLED"
            )
            self._repository.save_position(
                _position_from_broker(record, broker_position)
            )
            candidates.remove(record)

    def reconcile_account(self) -> bool:
        """Reconcile broker positions and reconstruct missing durable positions."""
        if self._repository is None or not isinstance(
            self._gateway, AccountReconciliationGateway
        ):
            return True
        try:
            account = self._gateway.get_account_snapshot()
        except (BrokerConnectionError, TimeoutError, ConnectionError):
            self._repository.set_trading_halt(
                True, reason="broker account reconciliation unavailable"
            )
            return False

        self._reconstruct_positions(account)
        local = {
            (position.instrument, position.direction): position.quantity
            for position in self._repository.get_open_positions()
        }
        broker = {
            (position.instrument, position.direction): position.quantity
            for position in account.open_positions
        }
        if local != broker:
            self._repository.set_trading_halt(
                True,
                reason="broker and durable position state diverged",
            )
            return False
        return True
