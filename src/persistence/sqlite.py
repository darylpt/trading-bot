"""SQLite repository with idempotent operational writes."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path


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
    latency_ms: int | None = None
    slippage: Decimal | None = None


@dataclass(frozen=True)
class SessionMetricsRecord:
    """Daily aggregate metrics for forward-test reporting."""

    session_date: date
    broker_latency_total_ms: int
    broker_latency_samples: int
    news_blackout_hits: int
    updated_at: datetime


class SQLiteRepository:
    """Persist sanitized trades on a durable SQLite path."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.initialize_schema()

    def initialize_schema(self) -> None:
        """Create every operational table, including session metrics."""
        schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
        self.connection.executescript(schema)
        self.connection.commit()

    def save_trade(self, record: TradeRecord) -> None:
        self.connection.execute(
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

    def save_execution_log(self, record: ExecutionLogRecord) -> None:
        """Persist one sanitized execution event."""
        self.connection.execute(
            """INSERT INTO execution_logs
            (client_order_id, event_type, provider, error_class, message,
             latency_ms, slippage, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.client_order_id,
                record.event_type,
                record.provider,
                record.error_class,
                record.message,
                record.latency_ms,
                None if record.slippage is None else str(record.slippage),
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
    ) -> None:
        """Atomically accumulate daily latency and blackout metrics."""
        if broker_latency_ms is not None and broker_latency_ms < 0:
            raise ValueError("broker latency must be non-negative")
        self.connection.execute(
            """INSERT INTO session_metrics
               (session_date, broker_latency_total_ms, broker_latency_samples,
                news_blackout_hits, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(session_date) DO UPDATE SET
                 broker_latency_total_ms =
                   broker_latency_total_ms + excluded.broker_latency_total_ms,
                 broker_latency_samples =
                   broker_latency_samples + excluded.broker_latency_samples,
                 news_blackout_hits =
                   news_blackout_hits + excluded.news_blackout_hits,
                 updated_at = excluded.updated_at""",
            (
                session_date.isoformat(),
                broker_latency_ms or 0,
                1 if broker_latency_ms is not None else 0,
                1 if news_blackout_hit else 0,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self.connection.commit()

    def get_session_metrics(self, session_date: date) -> SessionMetricsRecord | None:
        """Return the aggregate metrics for one UTC trading session."""
        row = self.connection.execute(
            """SELECT session_date, broker_latency_total_ms,
                      broker_latency_samples, news_blackout_hits, updated_at
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
            updated_at=datetime.fromisoformat(str(row[4])),
        )

    def get_latest_metrics(self, limit: int = 5) -> list[SessionMetricsRecord]:
        """Return the most recently updated daily metric aggregates."""
        if limit <= 0:
            raise ValueError("metrics limit must be positive")
        rows = self.connection.execute(
            """SELECT session_date, broker_latency_total_ms,
                      broker_latency_samples, news_blackout_hits, updated_at
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
                updated_at=datetime.fromisoformat(str(row[4])),
            )
            for row in rows
        ]

    def close(self) -> None:
        self.connection.close()
