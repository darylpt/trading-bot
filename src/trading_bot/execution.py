"""Paper fills, active positions, protective exits, and realized P&L."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal, Sequence

from domain.models import MarketCandle, OrderIntent
from persistence.sqlite import (
    ExecutionLogRecord,
    PositionRecord,
    SQLiteRepository,
    TradeRecord,
)
from trading_bot.risk import RiskDecision
from trading_bot.strategy import Signal

PositionDirection = Literal["LONG", "SHORT"]
ExitReason = Literal["STOP_LOSS", "TAKE_PROFIT", "COUNTER_SIGNAL"]


@dataclass(frozen=True)
class ExecutionOutcome:
    """Observable result of one paper execution tick."""

    opened_position: PositionRecord | None = None
    closed_positions: tuple[PositionRecord, ...] = ()
    rejection_reason: str | None = None


def _direction(signal: Signal) -> PositionDirection:
    if signal.action == "BUY":
        return "LONG"
    if signal.action == "SELL":
        return "SHORT"
    raise ValueError("only BUY and SELL signals can create positions")


def _take_profit_price(
    *,
    entry_price: Decimal,
    stop_loss_price: Decimal,
    direction: PositionDirection,
    multiple: Decimal,
) -> Decimal:
    if not multiple.is_finite() or multiple <= 0:
        raise ValueError("take-profit multiple must be finite and positive")
    distance = abs(entry_price - stop_loss_price)
    if distance <= 0:
        raise ValueError("entry and stop-loss prices must differ")
    if direction == "LONG":
        return entry_price + distance * multiple
    return entry_price - distance * multiple


def order_from_risk(
    signal: Signal,
    risk_decision: RiskDecision,
    *,
    account_equity: Decimal | None,
    timestamp: datetime,
    take_profit_multiple: Decimal = Decimal("2"),
) -> OrderIntent:
    """Build a validated paper order from one approved risk decision."""
    if not risk_decision.approved:
        raise ValueError("risk decision is not approved")
    if account_equity is None or not account_equity.is_finite() or account_equity <= 0:
        raise ValueError("account equity is unavailable")
    if signal.reference_price is None or not signal.reference_price.is_finite():
        raise ValueError("entry price is unavailable")
    if risk_decision.position_size is None or risk_decision.position_size <= 0:
        raise ValueError("approved position size is unavailable")
    if risk_decision.stop_loss_price is None:
        raise ValueError("approved stop-loss is unavailable")
    if not risk_decision.risk_amount.is_finite() or risk_decision.risk_amount <= 0:
        raise ValueError("approved risk amount is unavailable")

    direction = _direction(signal)
    entry_price = signal.reference_price
    stop_loss_price = risk_decision.stop_loss_price
    take_profit_price = _take_profit_price(
        entry_price=entry_price,
        stop_loss_price=stop_loss_price,
        direction=direction,
        multiple=take_profit_multiple,
    )
    risk_fraction = risk_decision.risk_amount / account_equity
    if not risk_fraction.is_finite() or not (
        Decimal("0") < risk_fraction <= Decimal("0.01")
    ):
        raise ValueError("approved risk fraction is outside the one-percent ceiling")

    return OrderIntent(
        client_order_id=f"paper-{signal.instrument}-{timestamp.isoformat()}-{signal.action}",
        instrument=signal.instrument,
        direction=direction,
        quantity=risk_decision.position_size,
        entry_price=entry_price,
        stop_loss_price=stop_loss_price,
        take_profit_price=take_profit_price,
        account_equity=account_equity,
        risk_fraction=risk_fraction,
        signal_timestamp=signal.timestamp,
    )


class PaperExecutionEngine:
    """Persist paper fills and advance open positions using OHLCV candles."""

    def __init__(
        self,
        repository: SQLiteRepository,
        *,
        take_profit_multiple: Decimal = Decimal("2"),
    ) -> None:
        self._repository = repository
        self._take_profit_multiple = take_profit_multiple

    def process_tick(
        self,
        candles: Sequence[MarketCandle],
        *,
        signal: Signal | None,
        risk_decision: RiskDecision | None,
        account_equity: Decimal | None,
        current_time: datetime | None = None,
    ) -> ExecutionOutcome:
        """Close triggered positions, then open at most one new paper position."""
        if not candles:
            return ExecutionOutcome(rejection_reason="NO_MARKET_DATA")
        candle = candles[-1]
        timestamp = current_time or candle.timestamp or datetime.now(timezone.utc)
        closed_positions = self._close_triggered_positions(candle, signal, timestamp)
        if closed_positions:
            return ExecutionOutcome(closed_positions=tuple(closed_positions))

        open_positions = self._repository.get_open_positions(candle.instrument)
        if open_positions:
            return ExecutionOutcome(rejection_reason="POSITION_ALREADY_OPEN")
        if signal is None or risk_decision is None:
            return ExecutionOutcome(rejection_reason="NO_APPROVED_SIGNAL")
        if not risk_decision.approved:
            return ExecutionOutcome(rejection_reason=risk_decision.reason)

        try:
            order = order_from_risk(
                signal,
                risk_decision,
                account_equity=account_equity,
                timestamp=timestamp,
                take_profit_multiple=self._take_profit_multiple,
            )
        except ValueError as exc:
            return ExecutionOutcome(rejection_reason=str(exc))

        position = PositionRecord(
            position_id=order.client_order_id,
            client_order_id=order.client_order_id,
            instrument=order.instrument,
            direction=order.direction,
            quantity=order.quantity,
            entry_price=order.entry_price,
            stop_loss_price=order.stop_loss_price,
            take_profit_price=order.take_profit_price,
            status="OPEN",
            opened_at=timestamp,
        )
        self._repository.save_trade(
            TradeRecord(
                client_order_id=order.client_order_id,
                instrument=order.instrument,
                direction=order.direction,
                quantity=order.quantity,
                entry_price=order.entry_price,
                stop_loss_price=order.stop_loss_price,
                take_profit_price=order.take_profit_price,
                account_equity=order.account_equity,
                risk_fraction=order.risk_fraction,
                status="ACCEPTED",
                environment="PAPER",
            )
        )
        self._repository.save_position(position)
        self._repository.save_execution_log(
            ExecutionLogRecord(
                client_order_id=order.client_order_id,
                event_type="PAPER_FILLED",
                provider="paper",
                error_class=None,
                message="paper position opened",
            )
        )
        return ExecutionOutcome(opened_position=position)

    def _close_triggered_positions(
        self,
        candle: MarketCandle,
        signal: Signal | None,
        timestamp: datetime,
    ) -> list[PositionRecord]:
        closed: list[PositionRecord] = []
        for position in self._repository.get_open_positions(candle.instrument):
            trigger = self._exit_trigger(position, candle, signal)
            if trigger is None:
                continue
            exit_reason, exit_price = trigger
            realized_pnl = self._realized_pnl(position, exit_price)
            closed_position = self._repository.close_position(
                position.position_id,
                exit_price=exit_price,
                exit_reason=exit_reason,
                closed_at=timestamp,
                realized_pnl=realized_pnl,
            )
            self._repository.record_session_metrics(
                timestamp.date(),
                realized_pnl=realized_pnl,
                trade_closed=True,
                winning_trade=realized_pnl > 0,
                losing_trade=realized_pnl < 0,
            )
            self._repository.save_execution_log(
                ExecutionLogRecord(
                    client_order_id=position.client_order_id,
                    event_type="PAPER_CLOSED",
                    provider="paper",
                    error_class=None,
                    message=f"paper position closed: {exit_reason}",
                    slippage=exit_price - position.entry_price,
                )
            )
            closed.append(closed_position)
        return closed

    @staticmethod
    def _exit_trigger(
        position: PositionRecord,
        candle: MarketCandle,
        signal: Signal | None,
    ) -> tuple[ExitReason, Decimal] | None:
        if position.direction == "LONG":
            if candle.low <= position.stop_loss_price:
                return "STOP_LOSS", position.stop_loss_price
            if candle.high >= position.take_profit_price:
                return "TAKE_PROFIT", position.take_profit_price
            if (
                signal is not None
                and signal.instrument == position.instrument
                and signal.action == "SELL"
            ):
                return "COUNTER_SIGNAL", candle.close
        else:
            if candle.high >= position.stop_loss_price:
                return "STOP_LOSS", position.stop_loss_price
            if candle.low <= position.take_profit_price:
                return "TAKE_PROFIT", position.take_profit_price
            if (
                signal is not None
                and signal.instrument == position.instrument
                and signal.action == "BUY"
            ):
                return "COUNTER_SIGNAL", candle.close
        return None

    @staticmethod
    def _realized_pnl(position: PositionRecord, exit_price: Decimal) -> Decimal:
        if position.direction == "LONG":
            return (exit_price - position.entry_price) * position.quantity
        return (position.entry_price - exit_price) * position.quantity
