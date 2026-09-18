"""Streamlit dashboard for paper-trading operational metrics and controls."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import streamlit as st

from trading_bot.runtime_config import (
    RuntimeConfig,
    SUPPORTED_INSTRUMENTS,
    SUPPORTED_STRATEGIES,
)

DATABASE_PATH = Path(
    os.getenv("DASHBOARD_DATABASE_PATH", "/app/data/session_metrics.db")
)
PHILIPPINE_TIMEZONE = ZoneInfo("Asia/Manila")


def _connect() -> sqlite3.Connection | None:
    """Open the trading database read-only; never create or mutate it."""
    if not DATABASE_PATH.exists():
        return None
    return sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)


def _control_connection() -> sqlite3.Connection | None:
    """Open the operational database for explicit operator controls only."""
    if not DATABASE_PATH.exists():
        return None
    return sqlite3.connect(DATABASE_PATH)


def _set_trading_halt(halted: bool, reason: str) -> None:
    connection = _control_connection()
    if connection is None:
        return
    try:
        connection.execute(
            """INSERT INTO runtime_controls
               (singleton_id, trading_halted, reason, updated_at)
               VALUES (1, ?, ?, datetime('now'))
               ON CONFLICT(singleton_id) DO UPDATE SET
                 trading_halted = excluded.trading_halted,
                 reason = excluded.reason,
                 updated_at = excluded.updated_at""",
            (int(halted), reason),
        )
        connection.commit()
    finally:
        connection.close()


def _query(sql: str, parameters: tuple[object, ...] = ()) -> list[dict[str, Any]]:
    connection = _connect()
    if connection is None:
        return []
    try:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(sql, parameters).fetchall()]
    finally:
        connection.close()


def _decimal(value: object) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _philippine_time(value: object) -> str:
    """Format a stored UTC timestamp in Philippine Standard Time."""
    if value is None:
        return ""
    try:
        timestamp = datetime.fromisoformat(str(value))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=ZoneInfo("UTC"))
        return timestamp.astimezone(PHILIPPINE_TIMEZONE).strftime(
            "%Y-%m-%d %H:%M:%S PST"
        )
    except ValueError:
        return str(value)


def _session_summary() -> dict[str, object]:
    rows = _query(
        """SELECT realized_pnl, closed_trades, winning_trades, losing_trades
           FROM session_metrics ORDER BY session_date DESC LIMIT 1"""
    )
    if not rows:
        return {"realized_pnl": Decimal("0"), "closed_trades": 0, "winning_trades": 0}
    row = rows[0]
    return {
        "realized_pnl": _decimal(row["realized_pnl"]),
        "closed_trades": int(row["closed_trades"]),
        "winning_trades": int(row["winning_trades"]),
    }


def _drawdown_status(summary: dict[str, object]) -> str:
    baseline = os.getenv("ACCOUNT_EQUITY")
    if not baseline:
        return "UNAVAILABLE (ACCOUNT_EQUITY not configured)"
    baseline_equity = _decimal(baseline)
    if baseline_equity <= 0:
        return "UNAVAILABLE (invalid ACCOUNT_EQUITY)"
    drawdown = max(-_decimal(summary["realized_pnl"]) / baseline_equity, Decimal("0"))
    limit = min(_decimal(os.getenv("DAILY_DRAWDOWN_LIMIT", "0.01")), Decimal("0.01"))
    return (
        f"HALTED ({drawdown:.2%})" if drawdown >= limit else f"CLEAR ({drawdown:.2%})"
    )


def _render_instrument_control() -> tuple[str, str]:
    """Render and persist the daemon's active instrument and strategy."""
    runtime_config = RuntimeConfig(DATABASE_PATH)
    active_instrument = runtime_config.get_active_instrument()
    active_strategy = runtime_config.get_active_strategy()
    selected_instrument = st.sidebar.selectbox(
        "Active instrument",
        SUPPORTED_INSTRUMENTS,
        index=SUPPORTED_INSTRUMENTS.index(active_instrument),
    )
    selected_strategy = st.sidebar.selectbox(
        "Active strategy",
        SUPPORTED_STRATEGIES,
        index=SUPPORTED_STRATEGIES.index(active_strategy),
    )
    instrument_changed = selected_instrument != active_instrument
    strategy_changed = selected_strategy != active_strategy
    if instrument_changed:
        runtime_config.set_active_instrument(selected_instrument)
    if strategy_changed:
        runtime_config.set_active_strategy(selected_strategy)
    if instrument_changed or strategy_changed:
        st.rerun()
    st.subheader("Instrument control panel")
    st.metric("Active Target Instrument", selected_instrument)
    st.metric("Active Strategy", selected_strategy)
    return selected_instrument, selected_strategy


def _render_strategy_state(instrument: str, strategy: str) -> None:
    """Display the latest registered strategy state for one instrument."""
    rows = _query(
        """SELECT event_type, decision_rationale, reference_price, fast_average,
                  slow_average, distance_to_crossover
           FROM execution_logs
           WHERE provider = 'strategy' AND instrument = ? AND strategy_name = ?
           ORDER BY created_at DESC LIMIT 1""",
        (instrument, strategy),
    )
    rules = {
        "ema_crossover": "BUY/SELL on fast EMA crossing above/below slow EMA; otherwise HOLD.",
        "rsi_mean_reversion": "BUY below RSI 30, SELL above RSI 70, otherwise HOLD.",
        "breakout_channel": "BUY above the upper 20-bar channel, SELL below the lower channel.",
    }
    st.subheader(f"Strategy & Live Indicators — {instrument}")
    st.caption(f"Strategy: {strategy}. Rules: {rules[strategy]}")
    if not rows:
        st.caption("No strategy decision has been recorded for this selection yet.")
        return
    row = rows[0]
    action = str(row["event_type"]).rsplit("_", 1)[-1]
    columns = st.columns(5)
    columns[0].metric("Status", action)
    columns[1].metric("Current Price", _format_indicator(row["reference_price"]))
    columns[2].metric("Fast EMA / Value", _format_indicator(row["fast_average"]))
    columns[3].metric("Slow EMA / Value", _format_indicator(row["slow_average"]))
    columns[4].metric(
        "Distance to Crossover", _format_indicator(row["distance_to_crossover"])
    )
    st.caption(f"Decision rationale: {row['decision_rationale'] or 'Unavailable'}")


def _format_indicator(value: object) -> str:
    return "Unavailable" if value is None else str(value)


def _render_circuit_breaker() -> None:
    """Display the persisted market-data entry circuit breaker state."""
    rows = _query(
        """SELECT status, reason, candle_timestamp, spread, updated_at
           FROM circuit_breaker_status WHERE singleton_id = 1"""
    )
    if not rows:
        st.warning("Circuit breaker: UNKNOWN (no runtime health record)")
        return
    row = rows[0]
    status = str(row["status"])
    label = "CLEAR" if status == "CLEAR" else "HALTED"
    if status == "CLEAR":
        st.success(f"Circuit breaker: {label} — {row['reason']}")
    else:
        st.error(f"Circuit breaker: {label} — {row['reason']}")
    st.caption(
        f"Last candle: {_philippine_time(row['candle_timestamp']) or 'Unavailable'}; "
        f"spread: {row['spread'] or 'Unavailable'}; "
        f"updated: {_philippine_time(row['updated_at'])}"
    )
    reason = str(row["reason"]).lower()
    columns = st.columns(4)
    columns[0].metric(
        "Feed freshness",
        "HALTED" if "stale" in reason or "unavailable" in reason else "CLEAR",
    )
    columns[1].metric("Clock drift", "HALTED" if "clock" in reason else "CLEAR")
    columns[2].metric("Spread tolerance", "HALTED" if "spread" in reason else "CLEAR")
    columns[3].metric("Risk guardrails", _drawdown_status(_session_summary()))


def _render_emergency_control() -> None:
    rows = _query(
        """SELECT trading_halted, reason, updated_at
           FROM runtime_controls WHERE singleton_id = 1"""
    )
    halted = bool(rows and rows[0]["trading_halted"])
    if halted:
        st.error(f"EMERGENCY HALT ACTIVE — {rows[0]['reason']}")
        if st.button("Resume new entries"):
            _set_trading_halt(False, "operator resumed entries")
            st.rerun()
    elif st.button("EMERGENCY HALT / KILL SWITCH", type="primary"):
        _set_trading_halt(True, "operator emergency halt")
        st.rerun()


def _render_trader_metrics() -> None:
    """Display realized trading-quality metrics from closed paper positions."""
    rows = _query(
        """SELECT p.realized_pnl,
                  (julianday(p.closed_at) - julianday(p.opened_at)) * 86400.0
                    AS holding_seconds,
                  t.account_equity * t.risk_fraction AS risk_amount
           FROM positions AS p
           JOIN trade_logs AS t
             ON p.client_order_id = t.client_order_id
           WHERE p.status = 'CLOSED'"""
    )
    wins = [
        Decimal(str(row["realized_pnl"]))
        for row in rows
        if _decimal(row["realized_pnl"]) > 0
    ]
    losses = [
        abs(_decimal(row["realized_pnl"]))
        for row in rows
        if _decimal(row["realized_pnl"]) < 0
    ]
    gross_wins = sum(wins, Decimal("0"))
    gross_losses = sum(losses, Decimal("0"))
    win_loss = (
        "∞"
        if not losses and wins
        else (f"{len(wins) / len(losses):.2f}" if losses else "Unavailable")
    )
    profit_factor = (
        "∞"
        if gross_wins > 0 and gross_losses == 0
        else f"{gross_wins / gross_losses:.2f}"
        if gross_losses
        else "Unavailable"
    )
    average_holding = (
        sum(float(row["holding_seconds"]) for row in rows) / len(rows) / 3600
        if rows
        else None
    )
    total_risk = sum(_decimal(row["risk_amount"]) for row in rows)
    expectancy = (
        sum(_decimal(row["realized_pnl"]) for row in rows) / total_risk
        if total_risk > 0
        else None
    )
    st.subheader("Trader Metrics Panel")
    columns = st.columns(6)
    columns[0].metric("Win/Loss Ratio", win_loss)
    columns[1].metric("Profit Factor", profit_factor)
    columns[2].metric(
        "Average Holding Time",
        "Unavailable" if average_holding is None else f"{average_holding:.2f}h",
    )
    columns[3].metric(
        "Expectancy / $ Risked",
        "Unavailable" if expectancy is None else f"${expectancy:.4f}",
    )
    columns[4].metric("MAE", "Unavailable (not captured)")
    columns[5].metric("MFE", "Unavailable (not captured)")


def _render_summary() -> None:
    summary = _session_summary()
    closed = int(str(summary["closed_trades"]))
    winning = int(str(summary["winning_trades"]))
    win_rate = winning / closed if closed else 0.0
    st.subheader("Session summary")
    columns = st.columns(4)
    columns[0].metric("Realized P&L", f"${summary['realized_pnl']}")
    columns[1].metric("Closed trades", str(closed))
    columns[2].metric("Win rate", f"{win_rate:.1%}")
    columns[3].metric("Daily drawdown", _drawdown_status(summary))


def _render_positions() -> None:
    rows = _query(
        """SELECT instrument, direction, quantity, entry_price,
                  stop_loss_price, take_profit_price
           FROM positions WHERE status = 'OPEN' ORDER BY opened_at"""
    )
    st.subheader("Active positions")
    st.dataframe(
        [
            {
                "Instrument": row["instrument"],
                "Direction": row["direction"],
                "Size": row["quantity"],
                "Entry Price": row["entry_price"],
                "Stop Loss": row["stop_loss_price"],
                "Take Profit": row["take_profit_price"],
            }
            for row in rows
        ],
        use_container_width=True,
        hide_index=True,
    )


def _render_audits() -> None:
    st.subheader("System status logs (Philippine Time)")
    logs = _query(
        """SELECT created_at, event_type, provider, instrument, error_class, message,
                  decision_rationale, latency_ms, slippage
           FROM execution_logs ORDER BY created_at DESC LIMIT 100"""
    )
    st.dataframe(
        [
            {
                "Created At": _philippine_time(row["created_at"]),
                "Event Type": row["event_type"],
                "Provider": row["provider"],
                "Instrument": row["instrument"],
                "Decision Rationale": row["decision_rationale"] or row["message"],
                "Error Class": row["error_class"],
                "Message": row["message"],
                "Latency (ms)": row["latency_ms"],
                "Slippage": row["slippage"],
            }
            for row in logs
        ],
        use_container_width=True,
        hide_index=True,
    )

    st.subheader("Risk decision audit trail")
    audits = _query(
        """SELECT opened_at, client_order_id, instrument, direction, quantity,
                  account_equity, risk_fraction, status, rejection_reason
           FROM trade_logs ORDER BY opened_at DESC LIMIT 100"""
    )
    st.dataframe(
        [{**row, "opened_at": _philippine_time(row["opened_at"])} for row in audits],
        use_container_width=True,
        hide_index=True,
    )


def main() -> None:
    """Render the operational dashboard and runtime instrument control."""
    st.set_page_config(page_title="Trading Bot Dashboard", layout="wide")
    st.title("Trading Bot Dashboard")
    st.caption(f"SQLite source: {DATABASE_PATH}")
    if st.button("Refresh data"):
        # Dashboard queries intentionally have no long-lived cache.
        st.rerun()
    if not DATABASE_PATH.exists():
        st.warning(
            "SQLite database is not available yet. Start the paper-trading service."
        )
        return
    _render_emergency_control()
    _render_circuit_breaker()
    selected_instrument, selected_strategy = _render_instrument_control()
    _render_strategy_state(selected_instrument, selected_strategy)
    _render_summary()
    _render_trader_metrics()
    _render_positions()
    _render_audits()


if __name__ == "__main__":
    main()
