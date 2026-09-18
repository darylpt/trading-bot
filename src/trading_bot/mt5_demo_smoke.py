"""Opt-in, single-pass Exness MT5 demo order lifecycle smoke check."""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Sequence

from pydantic import ValidationError

from config.settings import Settings
from domain.models import ExecutionResult, OrderIntent
from execution.broker_adapter import BrokerConnectionError, OpenPosition
from execution.executor import ExecutionGate
from persistence.sqlite import PositionRecord, SQLiteRepository
from observability.readiness import check_broker_demo_readiness
from trading_bot.__main__ import _create_market_data_gateway
from trading_bot.broker.mt5 import MT5BrokerAdapter

LOGGER = logging.getLogger(__name__)
_CONFIRMATION = "I_UNDERSTAND_DEMO_ORDER"


def run_smoke() -> dict[str, object]:
    """Run one authenticated, protected, reconciled, and closed demo order."""
    settings = Settings()
    if settings.exness_demo_smoke_confirm != _CONFIRMATION:
        raise ValueError(
            "set EXNESS_DEMO_SMOKE_CONFIRM=I_UNDERSTAND_DEMO_ORDER to authorize one demo order"
        )
    if settings.trading_mode != "BROKER_DEMO":
        raise ValueError("demo smoke requires TRADING_MODE=BROKER_DEMO")
    if settings.instrument != "XAUUSDm":
        raise ValueError("demo smoke requires INSTRUMENT=XAUUSDm")
    if not settings.broker_server:
        raise ValueError("EXNESS_SERVER is required")
    account_id = settings.broker_account or settings.broker_account_id
    if not account_id:
        raise ValueError("EXNESS_LOGIN is required")
    if (
        settings.exness_smoke_stop_distance is None
        or settings.exness_smoke_take_profit_distance is None
    ):
        raise ValueError(
            "EXNESS_SMOKE_STOP_DISTANCE and "
            "EXNESS_SMOKE_TAKE_PROFIT_DISTANCE are required"
        )

    quantity = Decimal("0.01")
    stop_distance = settings.exness_smoke_stop_distance
    target_distance = settings.exness_smoke_take_profit_distance
    direction = settings.exness_smoke_direction

    timestamp = datetime.now(timezone.utc)
    repository = SQLiteRepository(settings.session_database_path)
    opened_position: PositionRecord | None = None
    try:
        gateway = _create_market_data_gateway(settings)
        if not isinstance(gateway.source, MT5BrokerAdapter):
            raise BrokerConnectionError("broker-demo source did not initialize")
        source = gateway.source
        if source.mode != "BROKER_DEMO":
            raise BrokerConnectionError(
                source.fallback_reason or "broker-demo source did not initialize"
            )
        broker = source.broker
        if broker is None:
            raise BrokerConnectionError("broker-demo broker is unavailable")
        health = source.health_check(
            expected_server=settings.broker_server,
            expected_account_id=account_id,
            max_clock_drift_seconds=float(settings.max_clock_drift_seconds),
        )
        check_broker_demo_readiness(
            settings,
            broker,
            repository,
            instrument="XAUUSDm",
            history_limit=32,
        )
        account = broker.get_account_snapshot()
        quote = broker.get_market_quote("XAUUSDm")
        metadata = broker.get_instrument_metadata("XAUUSDm")
        session = broker.get_trading_session("XAUUSDm")
        if not session.is_open:
            raise BrokerConnectionError("XAUUSDm trading session is closed")
        if quote.instrument != "XAUUSDm" or metadata.instrument != "XAUUSDm":
            raise BrokerConnectionError("broker symbol verification failed")
        entry = quote.ask if direction == "LONG" else quote.bid
        stop = entry - stop_distance if direction == "LONG" else entry + stop_distance
        target = (
            entry + target_distance if direction == "LONG" else entry - target_distance
        )
        order = OrderIntent(
            client_order_id=f"mt5-smoke-{timestamp.strftime('%Y%m%dT%H%M%S%fZ')}",
            instrument="XAUUSDm",
            direction=direction,
            quantity=quantity,
            entry_price=entry,
            stop_loss_price=stop,
            take_profit_price=target,
            account_equity=account.equity,
            risk_fraction=Decimal("0.01"),
            signal_timestamp=timestamp,
        )
        result = ExecutionGate(
            broker,
            environment="DEMO",
            repository=repository,
        ).submit(order)
        if result.status == "ACCEPTED":
            reconciled = ExecutionGate(
                broker,
                environment="DEMO",
                repository=repository,
            ).reconcile(order.client_order_id)
            if isinstance(reconciled, ExecutionResult):
                result = reconciled
        if result.status != "FILLED" or result.protection_confirmed is not True:
            raise BrokerConnectionError(
                f"demo order was not a protected fill ({result.status})"
            )
        refreshed = broker.get_account_snapshot()
        matching = next(
            (
                position
                for position in refreshed.open_positions
                if position.instrument == order.instrument
                and position.direction == order.direction
                and position.quantity == order.quantity
            ),
            None,
        )
        if matching is None or matching.position_id is None:
            raise BrokerConnectionError("filled demo position was not reconciled")
        opened_position = PositionRecord(
            position_id=order.client_order_id,
            client_order_id=order.client_order_id,
            instrument=order.instrument,
            direction=order.direction,
            quantity=matching.quantity,
            entry_price=matching.entry_price,
            stop_loss_price=order.stop_loss_price,
            take_profit_price=order.take_profit_price,
            status="OPEN",
            opened_at=timestamp,
        )
        repository.save_position(opened_position)
        close_result = broker.close_position(
            OpenPosition(
                instrument=matching.instrument,
                direction=matching.direction,
                quantity=matching.quantity,
                entry_price=matching.entry_price,
                position_id=matching.position_id,
            )
        )
        if close_result.status not in {"FILLED", "ACCEPTED"}:
            raise BrokerConnectionError(
                f"demo position close was not acknowledged ({close_result.status})"
            )
        after_close = broker.get_account_snapshot()
        if any(
            position.instrument == "XAUUSDm"
            and position.direction == direction
            and position.quantity == quantity
            for position in after_close.open_positions
        ):
            raise BrokerConnectionError("demo position remained open after close")
        repository.close_position(
            opened_position.position_id,
            exit_price=close_result.fill_price or entry,
            exit_reason="CONTROLLED_SMOKE_CLOSE",
            closed_at=datetime.now(timezone.utc),
            realized_pnl=Decimal("0"),
        )
        repository.record_session_metrics(
            datetime.now(timezone.utc).date(), trade_closed=True
        )
        return {
            "status": "PASS",
            "mode": settings.trading_mode,
            "server": health.server,
            "instrument": "XAUUSDm",
            "quantity": str(quantity),
            "client_order_id": order.client_order_id,
            "sl": str(order.stop_loss_price),
            "tp": str(order.take_profit_price),
            "position_closed": True,
        }
    except Exception as exc:
        repository.set_trading_halt(
            True, reason=f"controlled demo smoke failed: {type(exc).__name__}"
        )
        raise
    finally:
        repository.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    try:
        result = run_smoke()
        LOGGER.info(
            "controlled demo smoke result=%s", json.dumps(result, sort_keys=True)
        )
    except ValidationError as exc:
        print(exc.errors())
        return 1
    except (BrokerConnectionError, OSError, ValueError) as exc:
        LOGGER.error("controlled demo smoke failed reason=%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
