"""SQLite repository with idempotent operational writes."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class TradeRecord:
    client_order_id: str
    instrument: str
    direction: str
    quantity: Decimal
    entry_price: Decimal
    stop_loss_price: Decimal
    take_profit_price: Decimal
    account_equity: Decimal
    risk_fraction: Decimal
    status: str
    environment: str
    rejection_reason: str | None = None


@dataclass(frozen=True)
class ExecutionLogRecord:
    """Sanitized execution or pipeline event for operational audit."""

    client_order_id: str | None
    event_type: str
    provider: str
    error_class: str | None
    message: str
    instrument: str = "UNKNOWN"
    strategy_name: str = "ema_crossover"
    decision_rationale: str | None = None
    reference_price: Decimal | None = None
    fast_average: Decimal | None = None
    slow_average: Decimal | None = None
    distance_to_crossover: Decimal | None = None
    latency_ms: int | None = None
    slippage: Decimal | None = None


@dataclass(frozen=True)
class LLMDecisionRecord:
    """Sanitized, validated sentiment-gate decision for operator audit."""

    evaluated_at: datetime
    instrument: str
    strategy_name: str
    technical_action: Literal["BUY", "SELL"]
    provider: str
    model: str
    decision: Literal["CONFIRM", "REJECT", "ADJUST_RISK"]
    gate_reason: str
    sentiment_score: Decimal | None
    confidence_score: Decimal
    risk_modifier: Decimal
    reasoning: str
    news_event_count: int


@dataclass(frozen=True)
class PositionRecord:
    """Persisted paper position lifecycle state."""

    position_id: str
    client_order_id: str
    instrument: str
    direction: Literal["LONG", "SHORT"]
    quantity: Decimal
    entry_price: Decimal
    stop_loss_price: Decimal
    take_profit_price: Decimal
    status: Literal["OPEN", "CLOSED"]
    opened_at: datetime
    closed_at: datetime | None = None
    exit_price: Decimal | None = None
    exit_reason: str | None = None
    realized_pnl: Decimal = Decimal("0")


@dataclass(frozen=True)
class SessionMetricsRecord:
    """Daily aggregate metrics for forward-test reporting."""

    session_date: date
    broker_latency_total_ms: int
    broker_latency_samples: int
    news_blackout_hits: int
    updated_at: datetime
    realized_pnl: Decimal = Decimal("0")
    closed_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0


@dataclass(frozen=True)
class CircuitBreakerRecord:
    """Latest persisted market-data safety state."""

    status: Literal["CLEAR", "HALTED"]
    reason: str
    candle_timestamp: datetime | None
    spread: Decimal | None
    updated_at: datetime


def _direction(value: object) -> Literal["LONG", "SHORT"]:
    direction = str(value)
    if direction == "LONG":
        return "LONG"
    if direction == "SHORT":
        return "SHORT"
    raise ValueError("stored position direction is invalid")


def _position_status(value: object) -> Literal["OPEN", "CLOSED"]:
    status = str(value)
    if status == "OPEN":
        return "OPEN"
    if status == "CLOSED":
        return "CLOSED"
    raise ValueError("stored position status is invalid")


def _position_from_row(row: tuple[object, ...]) -> PositionRecord:
    return PositionRecord(
        position_id=str(row[0]),
        client_order_id=str(row[1]),
        instrument=str(row[2]),
        direction=_direction(row[3]),
        quantity=Decimal(str(row[4])),
        entry_price=Decimal(str(row[5])),
        stop_loss_price=Decimal(str(row[6])),
        take_profit_price=Decimal(str(row[7])),
        status=_position_status(row[8]),
        opened_at=datetime.fromisoformat(str(row[9])),
        closed_at=None if row[10] is None else datetime.fromisoformat(str(row[10])),
        exit_price=None if row[11] is None else Decimal(str(row[11])),
        exit_reason=None if row[12] is None else str(row[12]),
        realized_pnl=Decimal(str(row[13])),
    )


class SQLiteRepository:
    """Persist sanitized trades on a durable SQLite path."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.initialize_schema()

    def initialize_schema(self) -> None:
        legacy_sql = self.connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'trade_logs'"
        ).fetchone()
        legacy_status_sql = "" if legacy_sql is None else str(legacy_sql[0])
        requires_trade_status_migration = any(
            marker not in legacy_status_sql
            for marker in ("PARTIALLY_FILLED", "ORDER_NOT_FOUND")
        )
        if legacy_sql is not None and requires_trade_status_migration:
            self.connection.execute(
                "ALTER TABLE trade_logs RENAME TO trade_logs_legacy"
            )
            self.connection.commit()
        schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
        self.connection.executescript(schema)
        if legacy_sql is not None and requires_trade_status_migration:
            self.connection.execute(
                """INSERT INTO trade_logs
                   SELECT client_order_id, instrument, direction, quantity,
                          entry_price, stop_loss_price, take_profit_price,
                          account_equity, risk_fraction, status, environment,
                          rejection_reason, opened_at
                   FROM trade_logs_legacy"""
            )
            self.connection.execute("DROP TABLE trade_logs_legacy")
        existing_columns = {
            str(row[1])
            for row in self.connection.execute("PRAGMA table_info(session_metrics)")
        }
        execution_columns = {
            str(row[1])
            for row in self.connection.execute("PRAGMA table_info(execution_logs)")
        }
        for name, definition in (
            ("instrument", "TEXT NOT NULL DEFAULT 'UNKNOWN'"),
            ("strategy_name", "TEXT NOT NULL DEFAULT 'ema_crossover'"),
            ("decision_rationale", "TEXT"),
            ("reference_price", "NUMERIC"),
            ("fast_average", "NUMERIC"),
            ("slow_average", "NUMERIC"),
            ("distance_to_crossover", "NUMERIC"),
        ):
            if name not in execution_columns:
                self.connection.execute(
                    f"ALTER TABLE execution_logs ADD COLUMN {name} {definition}"
                )
        for name, definition in (
            ("realized_pnl", "NUMERIC NOT NULL DEFAULT 0"),
            ("closed_trades", "INTEGER NOT NULL DEFAULT 0"),
            ("winning_trades", "INTEGER NOT NULL DEFAULT 0"),
            ("losing_trades", "INTEGER NOT NULL DEFAULT 0"),
        ):
            if name not in existing_columns:
                self.connection.execute(
                    f"ALTER TABLE session_metrics ADD COLUMN {name} {definition}"
                )
        self.connection.commit()

    def save_trade(self, record: TradeRecord) -> bool:
        cursor = self.connection.execute(
            """INSERT OR IGNORE INTO trade_logs
            (client_order_id, instrument, direction, quantity, entry_price,
             stop_loss_price, take_profit_price, account_equity, risk_fraction,
             status, environment, rejection_reason, opened_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.client_order_id,
                record.instrument,
                record.direction,
                str(record.quantity),
                str(record.entry_price),
                str(record.stop_loss_price),
                str(record.take_profit_price),
                str(record.account_equity),
                str(record.risk_fraction),
                record.status,
                record.environment,
                record.rejection_reason,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def update_trade_status(
        self,
        client_order_id: str,
        *,
        status: str,
        rejection_reason: str | None = None,
    ) -> None:
        """Update durable broker state without replacing the original intent."""
        if status not in {
            "PENDING_SUBMISSION",
            "PENDING",
            "FILLED",
            "PARTIALLY_FILLED",
            "ACCEPTED",
            "REJECTED",
            "CANCELLED",
            "EXPIRED",
            "ORDER_NOT_FOUND",
            "UNKNOWN",
        }:
            raise ValueError("invalid trade status")
        cursor = self.connection.execute(
            """UPDATE trade_logs SET status = ?, rejection_reason = ?
               WHERE client_order_id = ?""",
            (status, rejection_reason, client_order_id),
        )
        if cursor.rowcount != 1:
            raise ValueError("trade intent is missing")
        self.connection.commit()

    def get_unresolved_trades(self) -> list[TradeRecord]:
        """Return intents that require broker reconciliation."""
        rows = self.connection.execute(
            """SELECT client_order_id, instrument, direction, quantity,
                      entry_price, stop_loss_price, take_profit_price,
                      account_equity, risk_fraction, status, environment,
                      rejection_reason
               FROM trade_logs WHERE status IN ('PENDING_SUBMISSION', 'PENDING', 'UNKNOWN')
               ORDER BY opened_at, client_order_id"""
        ).fetchall()
        return [
            TradeRecord(
                client_order_id=str(row[0]),
                instrument=str(row[1]),
                direction=str(row[2]),
                quantity=Decimal(str(row[3])),
                entry_price=Decimal(str(row[4])),
                stop_loss_price=Decimal(str(row[5])),
                take_profit_price=Decimal(str(row[6])),
                account_equity=Decimal(str(row[7])),
                risk_fraction=Decimal(str(row[8])),
                status=str(row[9]),
                environment=str(row[10]),
                rejection_reason=None if row[11] is None else str(row[11]),
            )
            for row in rows
        ]

    def get_reconcilable_trades(self) -> list[TradeRecord]:
        """Return durable intents that may identify broker open positions."""
        rows = self.connection.execute(
            """SELECT client_order_id, instrument, direction, quantity,
                      entry_price, stop_loss_price, take_profit_price,
                      account_equity, risk_fraction, status, environment,
                      rejection_reason
               FROM trade_logs
               WHERE status IN ('PENDING_SUBMISSION', 'PENDING', 'FILLED', 'ACCEPTED', 'UNKNOWN')
               ORDER BY opened_at, client_order_id"""
        ).fetchall()
        return [
            TradeRecord(
                client_order_id=str(row[0]),
                instrument=str(row[1]),
                direction=str(row[2]),
                quantity=Decimal(str(row[3])),
                entry_price=Decimal(str(row[4])),
                stop_loss_price=Decimal(str(row[5])),
                take_profit_price=Decimal(str(row[6])),
                account_equity=Decimal(str(row[7])),
                risk_fraction=Decimal(str(row[8])),
                status=str(row[9]),
                environment=str(row[10]),
                rejection_reason=None if row[11] is None else str(row[11]),
            )
            for row in rows
        ]

    def trade_count(self) -> int:
        row = self.connection.execute("SELECT COUNT(*) FROM trade_logs").fetchone()
        return int(row[0]) if row is not None else 0

    def get_trade(self, client_order_id: str) -> TradeRecord | None:
        """Retrieve a typed trade record by its idempotency key."""
        row = self.connection.execute(
            """SELECT client_order_id, instrument, direction, quantity, entry_price,
                      stop_loss_price, take_profit_price, account_equity,
                      risk_fraction, status, environment, rejection_reason
               FROM trade_logs WHERE client_order_id = ?""",
            (client_order_id,),
        ).fetchone()
        if row is None:
            return None
        return TradeRecord(
            client_order_id=str(row[0]),
            instrument=str(row[1]),
            direction=str(row[2]),
            quantity=Decimal(str(row[3])),
            entry_price=Decimal(str(row[4])),
            stop_loss_price=Decimal(str(row[5])),
            take_profit_price=Decimal(str(row[6])),
            account_equity=Decimal(str(row[7])),
            risk_fraction=Decimal(str(row[8])),
            status=str(row[9]),
            environment=str(row[10]),
            rejection_reason=None if row[11] is None else str(row[11]),
        )

    def save_position(self, record: PositionRecord) -> None:
        """Persist an open paper position idempotently."""
        self.connection.execute(
            """INSERT OR IGNORE INTO positions
               (position_id, client_order_id, instrument, direction, quantity,
                entry_price, stop_loss_price, take_profit_price, status,
                opened_at, closed_at, exit_price, exit_reason, realized_pnl)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.position_id,
                record.client_order_id,
                record.instrument,
                record.direction,
                str(record.quantity),
                str(record.entry_price),
                str(record.stop_loss_price),
                str(record.take_profit_price),
                record.status,
                record.opened_at.isoformat(),
                None if record.closed_at is None else record.closed_at.isoformat(),
                None if record.exit_price is None else str(record.exit_price),
                record.exit_reason,
                str(record.realized_pnl),
            ),
        )
        self.connection.commit()

    def get_position(self, position_id: str) -> PositionRecord | None:
        row = self.connection.execute(
            """SELECT position_id, client_order_id, instrument, direction, quantity,
                      entry_price, stop_loss_price, take_profit_price, status,
                      opened_at, closed_at, exit_price, exit_reason, realized_pnl
               FROM positions WHERE position_id = ?""",
            (position_id,),
        ).fetchone()
        return None if row is None else _position_from_row(tuple(row))

    def get_open_positions(self, instrument: str | None = None) -> list[PositionRecord]:
        query = """SELECT position_id, client_order_id, instrument, direction, quantity,
                          entry_price, stop_loss_price, take_profit_price, status,
                          opened_at, closed_at, exit_price, exit_reason, realized_pnl
                   FROM positions WHERE status = 'OPEN'"""
        parameters: tuple[str, ...] = ()
        if instrument is not None:
            query += " AND instrument = ?"
            parameters = (instrument,)
        query += " ORDER BY opened_at ASC, position_id ASC"
        rows = self.connection.execute(query, parameters).fetchall()
        return [_position_from_row(tuple(row)) for row in rows]

    def close_position(
        self,
        position_id: str,
        *,
        exit_price: Decimal,
        exit_reason: str,
        closed_at: datetime,
        realized_pnl: Decimal,
    ) -> PositionRecord:
        cursor = self.connection.execute(
            """UPDATE positions
               SET status = 'CLOSED', closed_at = ?, exit_price = ?,
                   exit_reason = ?, realized_pnl = ?
               WHERE position_id = ? AND status = 'OPEN'""",
            (
                closed_at.isoformat(),
                str(exit_price),
                exit_reason,
                str(realized_pnl),
                position_id,
            ),
        )
        if cursor.rowcount != 1:
            raise ValueError("position is missing or already closed")
        self.connection.commit()
        position = self.get_position(position_id)
        if position is None:
            raise RuntimeError("closed position was not persisted")
        return position

    def save_execution_log(self, record: ExecutionLogRecord) -> None:
        """Persist one sanitized execution event."""
        self.connection.execute(
            """INSERT INTO execution_logs
            (client_order_id, instrument, strategy_name, event_type, provider, error_class,
             message, decision_rationale, reference_price, fast_average, slow_average,
             distance_to_crossover, latency_ms, slippage, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.client_order_id,
                record.instrument,
                record.strategy_name,
                record.event_type,
                record.provider,
                record.error_class,
                record.message,
                record.decision_rationale,
                None if record.reference_price is None else str(record.reference_price),
                None if record.fast_average is None else str(record.fast_average),
                None if record.slow_average is None else str(record.slow_average),
                (
                    None
                    if record.distance_to_crossover is None
                    else str(record.distance_to_crossover)
                ),
                record.latency_ms,
                None if record.slippage is None else str(record.slippage),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self.connection.commit()

    def save_llm_decision(self, record: LLMDecisionRecord) -> None:
        """Persist one validated sentiment-gate evaluation without raw payloads."""
        if not record.instrument or not record.strategy_name:
            raise ValueError("LLM decision scope is required")
        if not record.provider or not record.model:
            raise ValueError("LLM provider and model are required")
        if not record.gate_reason:
            raise ValueError("LLM gate reason is required")
        if record.sentiment_score is not None and not (
            Decimal("-1") <= record.sentiment_score <= Decimal("1")
        ):
            raise ValueError("LLM sentiment score is outside [-1, 1]")
        if not Decimal("0") <= record.confidence_score <= Decimal("1"):
            raise ValueError("LLM confidence score is outside [0, 1]")
        if not Decimal("0") < record.risk_modifier <= Decimal("1"):
            raise ValueError("LLM risk modifier is outside (0, 1]")
        if record.news_event_count < 0:
            raise ValueError("LLM news event count must be non-negative")
        reasoning = record.reasoning.strip()
        if not reasoning:
            raise ValueError("LLM reasoning is required")
        self.connection.execute(
            """INSERT INTO llm_decisions
               (evaluated_at, instrument, strategy_name, technical_action,
                provider, model, decision, gate_reason, sentiment_score,
                confidence_score, risk_modifier, reasoning, news_event_count,
                created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.evaluated_at.isoformat(),
                record.instrument,
                record.strategy_name,
                record.technical_action,
                record.provider,
                record.model,
                record.decision,
                record.gate_reason,
                (
                    None
                    if record.sentiment_score is None
                    else str(record.sentiment_score)
                ),
                str(record.confidence_score),
                str(record.risk_modifier),
                reasoning[:2000],
                record.news_event_count,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self.connection.commit()

    def execution_log_count(self) -> int:
        """Return the number of persisted operational events."""
        row = self.connection.execute("SELECT COUNT(*) FROM execution_logs").fetchone()
        return int(row[0]) if row is not None else 0

    def record_session_metrics(
        self,
        session_date: date,
        *,
        broker_latency_ms: int | None = None,
        news_blackout_hit: bool = False,
        realized_pnl: Decimal = Decimal("0"),
        trade_closed: bool = False,
        winning_trade: bool = False,
        losing_trade: bool = False,
    ) -> None:
        """Atomically accumulate latency, P&L, and win/loss metrics."""
        if broker_latency_ms is not None and broker_latency_ms < 0:
            raise ValueError("broker latency must be non-negative")
        if not realized_pnl.is_finite():
            raise ValueError("realized P&L must be finite")
        if winning_trade and losing_trade:
            raise ValueError("a trade cannot be both winning and losing")
        self.connection.execute(
            """INSERT INTO session_metrics
               (session_date, broker_latency_total_ms, broker_latency_samples,
                news_blackout_hits, realized_pnl, closed_trades,
                winning_trades, losing_trades, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(session_date) DO UPDATE SET
                 broker_latency_total_ms =
                   broker_latency_total_ms + excluded.broker_latency_total_ms,
                 broker_latency_samples =
                   broker_latency_samples + excluded.broker_latency_samples,
                 news_blackout_hits =
                   news_blackout_hits + excluded.news_blackout_hits,
                 realized_pnl = realized_pnl + excluded.realized_pnl,
                 closed_trades = closed_trades + excluded.closed_trades,
                 winning_trades = winning_trades + excluded.winning_trades,
                 losing_trades = losing_trades + excluded.losing_trades,
                 updated_at = excluded.updated_at""",
            (
                session_date.isoformat(),
                broker_latency_ms or 0,
                1 if broker_latency_ms is not None else 0,
                1 if news_blackout_hit else 0,
                str(realized_pnl),
                1 if trade_closed else 0,
                1 if winning_trade else 0,
                1 if losing_trade else 0,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self.connection.commit()

    def get_session_metrics(self, session_date: date) -> SessionMetricsRecord | None:
        """Return the aggregate metrics for one UTC trading session."""
        row = self.connection.execute(
            """SELECT session_date, broker_latency_total_ms,
                      broker_latency_samples, news_blackout_hits,
                      realized_pnl, closed_trades, winning_trades,
                      losing_trades, updated_at
               FROM session_metrics WHERE session_date = ?""",
            (session_date.isoformat(),),
        ).fetchone()
        if row is None:
            return None
        return SessionMetricsRecord(
            session_date=date.fromisoformat(str(row[0])),
            broker_latency_total_ms=int(row[1]),
            broker_latency_samples=int(row[2]),
            news_blackout_hits=int(row[3]),
            realized_pnl=Decimal(str(row[4])),
            closed_trades=int(row[5]),
            winning_trades=int(row[6]),
            losing_trades=int(row[7]),
            updated_at=datetime.fromisoformat(str(row[8])),
        )

    def get_latest_metrics(self, limit: int = 5) -> list[SessionMetricsRecord]:
        """Return the most recently updated daily metric aggregates."""
        if limit <= 0:
            raise ValueError("metrics limit must be positive")
        rows = self.connection.execute(
            """SELECT session_date, broker_latency_total_ms,
                      broker_latency_samples, news_blackout_hits,
                      realized_pnl, closed_trades, winning_trades,
                      losing_trades, updated_at
               FROM session_metrics
               ORDER BY updated_at DESC, session_date DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [
            SessionMetricsRecord(
                session_date=date.fromisoformat(str(row[0])),
                broker_latency_total_ms=int(row[1]),
                broker_latency_samples=int(row[2]),
                news_blackout_hits=int(row[3]),
                realized_pnl=Decimal(str(row[4])),
                closed_trades=int(row[5]),
                winning_trades=int(row[6]),
                losing_trades=int(row[7]),
                updated_at=datetime.fromisoformat(str(row[8])),
            )
            for row in rows
        ]

    def save_circuit_breaker(
        self,
        *,
        status: Literal["CLEAR", "HALTED"],
        reason: str,
        candle_timestamp: datetime | None,
        spread: Decimal | None,
        updated_at: datetime,
    ) -> None:
        self.connection.execute(
            """INSERT INTO circuit_breaker_status
               (singleton_id, status, reason, candle_timestamp, spread, updated_at)
               VALUES (1, ?, ?, ?, ?, ?)
               ON CONFLICT(singleton_id) DO UPDATE SET
                 status = excluded.status,
                 reason = excluded.reason,
                 candle_timestamp = excluded.candle_timestamp,
                 spread = excluded.spread,
                 updated_at = excluded.updated_at""",
            (
                status,
                reason,
                None if candle_timestamp is None else candle_timestamp.isoformat(),
                None if spread is None else str(spread),
                updated_at.isoformat(),
            ),
        )
        self.connection.commit()

    def clear_circuit_breaker(self) -> None:
        """Discard health state from a previous daemon process."""
        self.connection.execute("DELETE FROM circuit_breaker_status")
        self.connection.commit()

    def get_circuit_breaker(self) -> CircuitBreakerRecord | None:
        row = self.connection.execute(
            """SELECT status, reason, candle_timestamp, spread, updated_at
               FROM circuit_breaker_status WHERE singleton_id = 1"""
        ).fetchone()
        if row is None:
            return None
        status: Literal["CLEAR", "HALTED"] = (
            "CLEAR" if str(row[0]) == "CLEAR" else "HALTED"
        )
        return CircuitBreakerRecord(
            status=status,
            reason=str(row[1]),
            candle_timestamp=(
                None if row[2] is None else datetime.fromisoformat(str(row[2]))
            ),
            spread=None if row[3] is None else Decimal(str(row[3])),
            updated_at=datetime.fromisoformat(str(row[4])),
        )

    def set_trading_halt(self, halted: bool, *, reason: str) -> None:
        """Persist the global entry halt without affecting position monitoring."""
        self.connection.execute(
            """INSERT INTO runtime_controls
               (singleton_id, trading_halted, reason, updated_at)
               VALUES (1, ?, ?, ?)
               ON CONFLICT(singleton_id) DO UPDATE SET
                 trading_halted = excluded.trading_halted,
                 reason = excluded.reason,
                 updated_at = excluded.updated_at""",
            (
                int(halted),
                reason,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self.connection.commit()

    def get_trading_halt(self) -> tuple[bool, str]:
        """Return the persisted halt state and operator reason."""
        row = self.connection.execute(
            """SELECT trading_halted, reason FROM runtime_controls
               WHERE singleton_id = 1"""
        ).fetchone()
        return (False, "not configured") if row is None else (bool(row[0]), str(row[1]))

    def close(self) -> None:
        self.connection.close()
