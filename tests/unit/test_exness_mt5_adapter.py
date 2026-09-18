from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from domain.models import BrokerOrderPayload, ExecutionResult, OrderIntent
from execution.exness_mt5_adapter import ExnessMT5BridgeTransport
from execution.executor import ExecutionGate
from execution.broker_adapter import BrokerConnectionError
from persistence.sqlite import SQLiteRepository


class FakeBridgeTransport(ExnessMT5BridgeTransport):
    def __init__(self, payload: object) -> None:
        super().__init__("https://host.docker.internal:8765")
        self.payload = payload

    def request(self, method, url, *, headers, body, timeout):
        return self.payload


def health_payload() -> dict[str, object]:
    return {
        "terminalConnected": True,
        "authorized": True,
        "accountId": "463948680",
        "server": "Exness-MT5Trial17",
        "serverTime": "2026-09-15T12:00:00Z",
    }


def test_bridge_health_requires_terminal_auth_identity_and_clock() -> None:
    bridge = FakeBridgeTransport(health_payload())
    health = bridge.assert_ready(
        expected_server="Exness-MT5Trial17",
        expected_account_id="463948680",
        max_clock_drift_seconds=5,
        now=datetime(2026, 9, 15, 12, 0, 3, tzinfo=timezone.utc),
    )

    assert health.terminal_connected is True
    assert health.authorized is True


def test_bridge_health_rejects_unauthorized_terminal() -> None:
    payload = health_payload()
    payload["authorized"] = False
    bridge = FakeBridgeTransport(payload)

    with pytest.raises(BrokerConnectionError, match="authorization"):
        bridge.assert_ready(
            expected_server="Exness-MT5Trial17",
            expected_account_id="463948680",
            max_clock_drift_seconds=5,
            now=datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc),
        )


def test_demo_transport_error_is_unknown_and_halts_sqlite(tmp_path: Path) -> None:
    class FailingGateway:
        def submit_order(self, payload: BrokerOrderPayload) -> ExecutionResult:
            raise BrokerConnectionError("bridge unavailable")

        def reconcile_order(self, client_order_id: str) -> ExecutionResult:
            return ExecutionResult(
                client_order_id=client_order_id,
                status="UNKNOWN",
                environment="DEMO",
            )

    order = OrderIntent(
        client_order_id="demo-unknown-1",
        instrument="XAUUSDm",
        direction="LONG",
        quantity=Decimal("0.01"),
        entry_price=Decimal("2000"),
        stop_loss_price=Decimal("1990"),
        take_profit_price=Decimal("2020"),
        account_equity=Decimal("10000"),
        risk_fraction=Decimal("0.01"),
        signal_timestamp=datetime.now(timezone.utc),
    )
    repository = SQLiteRepository(tmp_path / "session_metrics.db")
    result = ExecutionGate(
        FailingGateway(), environment="DEMO", repository=repository
    ).submit(order)

    assert result.status == "UNKNOWN"
    assert repository.get_trade(order.client_order_id).status == "UNKNOWN"
    halted, reason = repository.get_trading_halt()
    assert halted is True
    assert "UNKNOWN" in reason
    repository.close()
