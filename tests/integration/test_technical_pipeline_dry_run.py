"""End-to-end deterministic technical strategy dry run."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from domain.models import BrokerOrderPayload, ExecutionResult, OrderIntent
from execution.executor import ExecutionGate
from persistence.sqlite import SQLiteRepository, TradeRecord
from risk.limits import calculate_atr_exits, validate_order_risk
from risk.sizing import calculate_position_size
from strategy.indicators import calculate_indicators
from strategy.market_data import load_csv_candles
from strategy.signals import generate_signal


class PaperGateway:
    """Deterministic fake gateway that records the submitted paper payload."""

    def __init__(self) -> None:
        self.payloads: list[BrokerOrderPayload] = []

    def submit_order(self, payload: BrokerOrderPayload) -> ExecutionResult:
        self.payloads.append(payload)
        return ExecutionResult(
            client_order_id=payload.client_order_id,
            provider_order_id="paper-fill-1",
            status="ACCEPTED",
            filled_quantity=payload.quantity,
            fill_price=payload.entry_price,
            environment=payload.environment,
        )

    def reconcile_order(self, client_order_id: str) -> ExecutionResult | None:
        return None


def test_technical_pipeline_paper_demo_dry_run(tmp_path: Path) -> None:
    csv_path = tmp_path / "eur_usd_15m.csv"
    csv_path.write_text(
        "timestamp,open,high,low,close,volume\n"
        "2026-01-05T12:00:00Z,1.0995,1.1010,1.0990,1.1000,100\n"
        "2026-01-05T12:15:00Z,1.0995,1.1010,1.0990,1.1000,110\n"
        "2026-01-05T12:30:00Z,1.0995,1.1010,1.0990,1.1000,120\n"
        "2026-01-05T12:45:00Z,1.0995,1.1010,1.0990,1.1000,130\n"
        "2026-01-05T13:00:00Z,1.0955,1.0960,1.0940,1.0950,140\n"
        "2026-01-05T13:15:00Z,1.1055,1.1070,1.1040,1.1060,150\n",
        encoding="utf-8",
    )

    candles = load_csv_candles(csv_path, minimum_history=6)
    indicators = calculate_indicators(
        candles, rsi_period=2, fast_period=2, slow_period=3, atr_period=2
    )
    signal = generate_signal(indicators)

    assert signal is not None
    assert signal.direction == "LONG"
    assert signal.rsi <= Decimal("70")
    latest = indicators.latest
    assert latest is not None and latest.atr is not None

    stop_loss, take_profit = calculate_atr_exits(
        reference_price=signal.reference_price,
        atr=latest.atr,
        direction=signal.direction,
    )
    quantity = calculate_position_size(
        account_equity=Decimal("10000"),
        entry_price=signal.reference_price,
        stop_loss_price=stop_loss,
    )
    order = OrderIntent(
        client_order_id="technical-dry-run-1",
        instrument=signal.instrument,
        direction=signal.direction,
        quantity=quantity,
        entry_price=signal.reference_price,
        stop_loss_price=stop_loss,
        take_profit_price=take_profit,
        account_equity=Decimal("10000"),
        risk_fraction=Decimal("0.01"),
        signal_timestamp=signal.signal_timestamp,
        sentiment_score=None,
    )
    validate_order_risk(order)

    gateway = PaperGateway()
    execution = ExecutionGate(gateway, environment="PAPER").submit(order)

    assert execution.status == "ACCEPTED"
    assert len(gateway.payloads) == 1
    payload = gateway.payloads[0]
    assert payload.stop_loss.price == stop_loss
    assert payload.take_profit.price == take_profit
    assert payload.environment == "PAPER"

    repository = SQLiteRepository(tmp_path / "trades.sqlite")
    repository.save_trade(
        TradeRecord(
            client_order_id=execution.client_order_id,
            instrument=payload.instrument,
            direction=payload.direction,
            quantity=payload.quantity,
            entry_price=payload.entry_price,
            stop_loss_price=payload.stop_loss.price,
            take_profit_price=payload.take_profit.price,
            account_equity=payload.account_equity,
            risk_fraction=payload.risk_fraction,
            status=execution.status,
            environment=execution.environment,
            rejection_reason=execution.rejection_reason,
        )
    )
    persisted = repository.get_trade("technical-dry-run-1")
    repository.close()

    assert persisted is not None
    assert persisted.status == "ACCEPTED"
    assert persisted.direction == "LONG"
    assert persisted.quantity == quantity
    assert persisted.stop_loss_price == stop_loss
    assert persisted.take_profit_price == take_profit
