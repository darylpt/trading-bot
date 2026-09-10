"""SQLite repository with idempotent operational writes."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
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


class SQLiteRepository:
    """Persist sanitized trades on a durable SQLite path."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
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

    def close(self) -> None:
        self.connection.close()
