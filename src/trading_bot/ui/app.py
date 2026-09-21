"""Streamlit dashboard for paper-trading operational metrics and controls."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timezone
import ssl
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any
from urllib.parse import quote as url_quote
from urllib.request import Request, urlopen
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


_DECIMAL_TOKEN = re.compile(r"(?<![A-Za-z_])[-+]?\d+\.\d+(?![A-Za-z_])")
_FOUR_DECIMAL_PLACES = Decimal("0.0001")


def _format_log_text(value: object) -> str:
    """Round decimal numbers in dashboard text without changing stored logs."""
    text = str(value)

    def replace(match: re.Match[str]) -> str:
        rounded = Decimal(match.group()).quantize(
            _FOUR_DECIMAL_PLACES, rounding=ROUND_HALF_UP
        )
        return f"{Decimal('0') if rounded == 0 else rounded:.4f}"

    return _DECIMAL_TOKEN.sub(replace, text)


def _format_market_value(value: object) -> str:
    if value is None:
        return "Unavailable"
    return _format_log_text(value)


def _utc_timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    try:
        timestamp = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc)


def _age_seconds(value: object) -> float | None:
    timestamp = _utc_timestamp(value)
    if timestamp is None:
        return None
    return max((datetime.now(timezone.utc) - timestamp).total_seconds(), 0.0)


def _fetch_json(
    url: str,
    *,
    timeout_seconds: float = 2.0,
    context: ssl.SSLContext | None = None,
) -> dict[str, object]:
    request = Request(url, headers={"Accept": "application/json"})
    with urlopen(request, timeout=timeout_seconds, context=context) as response:  # noqa: S310
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("provider response was not an object")
    return payload


def _ollama_status() -> dict[str, object]:
    base_url = os.getenv("OLLAMA_BASE_URL", "").rstrip("/")
    model = os.getenv("OLLAMA_MODEL", "Unavailable")
    if not base_url:
        return {
            "status": "NOT CONFIGURED",
            "model": model,
            "detail": "OLLAMA_BASE_URL missing",
        }
    try:
        tags = _fetch_json(f"{base_url}/api/tags")
        raw_models = tags.get("models", [])
        if not isinstance(raw_models, list):
            raise ValueError("model inventory was malformed")
        model_names = {
            str(item.get("name"))
            for item in raw_models
            if isinstance(item, dict) and item.get("name")
        }
        available = model in model_names
        ps = _fetch_json(f"{base_url}/api/ps")
        raw_loaded = ps.get("models", [])
        loaded_names = (
            {
                str(item.get("name"))
                for item in raw_loaded
                if isinstance(item, dict) and item.get("name")
            }
            if isinstance(raw_loaded, list)
            else set()
        )
        return {
            "status": "READY" if available else "MODEL MISSING",
            "model": model,
            "loaded": "YES" if model in loaded_names else "NO (loads on demand)",
            "detail": "Ollama API reachable",
        }
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {
            "status": "UNAVAILABLE",
            "model": model,
            "loaded": "UNKNOWN",
            "detail": type(exc).__name__,
        }


def _news_status() -> dict[str, object]:
    from sentiment.news import JsonNewsFeed, NewsFeedError

    configured_path = os.getenv("NEWS_EVENTS_PATH")
    if not configured_path:
        return {
            "status": "NOT CONFIGURED",
            "events": 0,
            "detail": "NEWS_EVENTS_PATH missing",
        }
    try:
        max_age = float(os.getenv("NEWS_MAX_AGE_SECONDS", "3600"))
        events = JsonNewsFeed(Path(configured_path), max_age_seconds=max_age).load(
            instrument=os.getenv("INSTRUMENT", "XAUUSDm"),
            current_time=datetime.now(timezone.utc),
        )
        newest = max((event.retrieved_at for event in events), default=None)
        status = "READY" if events else "EMPTY"
        return {
            "status": status,
            "events": len(events),
            "age": _age_seconds(newest),
            "source": os.getenv("NEWS_FEED_URL", "local snapshot"),
            "detail": (
                "typed snapshot validated"
                if events
                else "no fresh events in typed snapshot"
            ),
        }
    except (OSError, ValueError, NewsFeedError) as exc:
        return {
            "status": "STALE/INVALID",
            "events": 0,
            "source": os.getenv("NEWS_FEED_URL", "local snapshot"),
            "detail": str(exc),
        }


def _runtime_status() -> dict[str, object]:
    paper = os.getenv("PAPER_TRADING", "").lower() == "true"
    live = os.getenv("LIVE_TRADING", "").lower() == "true"
    mode = os.getenv("TRADING_MODE", "Unavailable")
    return {
        "status": "SAFE" if paper and not live else "UNSAFE",
        "mode": mode,
        "paper": paper,
        "live": live,
    }


def _bridge_status() -> dict[str, object]:
    host = os.getenv("EXNESS_BRIDGE_HOST", "localhost")
    port = os.getenv("EXNESS_BRIDGE_PORT", "18812")
    base_url = f"https://{host}:{port}"
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        payload = _fetch_json(f"{base_url}/health", context=context)
        connected = payload.get("terminalConnected", payload.get("terminal_connected"))
        authorized = payload.get("authorized", payload.get("accountAuthorized"))
        server = payload.get("server", "Unavailable")
        if connected is not True or authorized is not True:
            return {
                "status": "NOT READY",
                "server": str(server),
                "detail": "connection/authentication failed",
            }
        account_id = payload.get("accountId", payload.get("account_id"))
        instrument = os.getenv("INSTRUMENT", "XAUUSDm")
        if not isinstance(account_id, str) or not account_id:
            raise ValueError("health response omitted account identity")
        quote_payload = _fetch_json(
            f"{base_url}/api/accounts/{url_quote(account_id, safe='')}/quotes/"
            f"{url_quote(instrument, safe='')}",
            context=context,
        )
        quote_age = _age_seconds(
            quote_payload.get("timestamp", quote_payload.get("observed_at"))
        )
        max_age = _decimal(os.getenv("MAX_DATA_AGE_SECONDS", "300"))
        quote_ready = quote_age is not None and quote_age <= float(max_age)
        bid = quote_payload.get("bid")
        ask = quote_payload.get("ask")
        midpoint: Decimal | None = None
        quote_spread = quote_payload.get("spread")
        try:
            if bid is not None and ask is not None:
                midpoint = (Decimal(str(bid)) + Decimal(str(ask))) / Decimal("2")
                if quote_spread is None:
                    quote_spread = Decimal(str(ask)) - Decimal(str(bid))
        except (InvalidOperation, TypeError, ValueError):
            midpoint = None
            quote_spread = None
        return {
            "status": "READY" if quote_ready else "STALE QUOTE",
            "server": str(server),
            "quote_age": quote_age,
            "midpoint": midpoint,
            "quote_spread": quote_spread,
            "detail": (
                "connected, authorized, and quote fresh"
                if quote_ready
                else "connected and authorized; quote is stale or unavailable"
            ),
        }
    except (OSError, ValueError, json.JSONDecodeError):
        return {
            "status": "UNAVAILABLE",
            "server": "Unavailable",
            "detail": "health or quote probe failed",
        }


def _broker_status() -> dict[str, object]:
    breaker = _query(
        """SELECT status, reason, candle_timestamp, spread, updated_at
           FROM circuit_breaker_status WHERE singleton_id = 1"""
    )
    controls = _query(
        """SELECT trading_halted, reason, updated_at
           FROM runtime_controls WHERE singleton_id = 1"""
    )
    breaker_row = breaker[0] if breaker else {}
    control_row = controls[0] if controls else {}
    halted = (
        bool(control_row.get("trading_halted")) or breaker_row.get("status") == "HALTED"
    )
    reason = str(control_row.get("reason") or breaker_row.get("reason") or "unknown")
    return {
        "status": "HALTED" if halted else ("CLEAR" if breaker else "UNKNOWN"),
        "reason": reason,
        "candle_age": _age_seconds(breaker_row.get("candle_timestamp")),
        "spread": breaker_row.get("spread"),
        "updated_at": breaker_row.get("updated_at"),
    }


def _database_status() -> dict[str, object]:
    rows = _query("SELECT MAX(created_at) AS latest_event FROM execution_logs")
    latest = rows[0].get("latest_event") if rows else None
    return {"latest": latest, "age": _age_seconds(latest)}


def _age_label(value: object, divisor: float, suffix: str) -> str:
    if value is None:
        return "Unavailable"
    try:
        return f"{float(_decimal(value)) / divisor:.1f}{suffix}"
    except (TypeError, ValueError):
        return "Unavailable"


def _render_operational_readiness() -> dict[str, dict[str, object]]:
    runtime = _runtime_status()
    bridge = _bridge_status()
    broker = _broker_status()
    ollama = _ollama_status()
    news = _news_status()
    database = _database_status()
    blockers: list[str] = []
    if runtime["status"] != "SAFE":
        blockers.append("runtime safety flags")
    if bridge["status"] != "READY":
        blockers.append(f"bridge: {bridge['detail']}")
    if broker["status"] != "CLEAR":
        blockers.append(f"broker gate: {broker['reason']}")
    if ollama["status"] != "READY":
        blockers.append(f"Ollama: {ollama['detail']}")
    if news["status"] != "READY":
        blockers.append(f"news: {news['detail']}")
    if blockers:
        st.error("NO-GO — " + "; ".join(blockers))
    else:
        st.success("READY — all configured paper-trading gates are clear")

    st.subheader("Operational readiness")
    columns = st.columns(6)
    columns[0].metric("Runtime", str(runtime["status"]))
    columns[1].metric("Bridge", str(bridge["status"]))
    columns[2].metric(
        "Broker gate",
        "HALTED" if bridge["status"] != "READY" else str(broker["status"]),
    )
    columns[3].metric("Ollama", str(ollama["status"]))
    columns[4].metric("News feed", str(news["status"]))
    columns[5].metric("Last DB event", _age_label(database["age"], 3600, "h ago"))
    diagnostics = [
        {"Check": "Bridge server", "Value": str(bridge.get("server", "Unavailable"))},
        {"Check": "Bridge detail", "Value": str(bridge.get("detail", "Unavailable"))},
        {
            "Check": "Bridge quote age",
            "Value": _age_label(bridge.get("quote_age"), 3600, "h"),
        },
        {"Check": "Ollama model", "Value": str(ollama.get("model", "Unavailable"))},
        {"Check": "Ollama loaded", "Value": str(ollama.get("loaded", "Unavailable"))},
        {"Check": "Ollama detail", "Value": str(ollama.get("detail", "Unavailable"))},
        {"Check": "News events", "Value": str(news.get("events", 0))},
        {
            "Check": "News age",
            "Value": _age_label(news.get("age"), 60, "m"),
        },
        {"Check": "News source", "Value": str(news.get("source", "Unavailable"))},
        {"Check": "Broker reason", "Value": str(broker["reason"])},
        {
            "Check": "Latest candle age",
            "Value": _age_label(broker.get("candle_age"), 3600, "h"),
        },
        {"Check": "Broker spread", "Value": str(broker.get("spread", "Unavailable"))},
        {
            "Check": "Runtime halt updated",
            "Value": _philippine_time(broker.get("updated_at")),
        },
    ]
    with st.expander("Technical diagnostics", expanded=False):
        st.dataframe(diagnostics, use_container_width=True, hide_index=True)
    return {
        "runtime": runtime,
        "bridge": bridge,
        "broker": broker,
        "ollama": ollama,
        "news": news,
        "database": database,
    }


def _render_quick_overview(
    instrument: str,
    strategy: str,
    operational: dict[str, dict[str, object]],
) -> None:
    """Show the few values needed to understand the live paper session."""
    strategy_rows = _query(
        """SELECT event_type, created_at
           FROM execution_logs
           WHERE provider = 'strategy' AND instrument = ? AND strategy_name = ?
           ORDER BY created_at DESC LIMIT 1""",
        (instrument, strategy),
    )
    sentiment_rows = _query(
        """SELECT decision, sentiment_score, evaluated_at
           FROM llm_decisions
           WHERE instrument = ? AND strategy_name = ?
           ORDER BY evaluated_at DESC, id DESC LIMIT 1""",
        (instrument, strategy),
    )
    positions = _query(
        """SELECT direction, quantity FROM positions
           WHERE instrument = ? AND status = 'OPEN' ORDER BY opened_at""",
        (instrument,),
    )
    strategy_action = (
        str(strategy_rows[0]["event_type"]).rsplit("_", 1)[-1]
        if strategy_rows
        else "WAITING"
    )
    strategy_direction = {"BUY": "LONG", "SELL": "SHORT"}.get(
        strategy_action, strategy_action
    )
    sentiment = (
        "NOT REQUIRED"
        if strategy_action not in {"BUY", "SELL"}
        else str(sentiment_rows[0]["decision"]) if sentiment_rows else "WAITING"
    )
    position = (
        "FLAT"
        if not positions
        else ", ".join(f"{row['direction']} {row['quantity']}" for row in positions)
    )
    statuses = operational
    session_running = all(
        (
            statuses["runtime"]["status"] == "SAFE",
            statuses["bridge"]["status"] == "READY",
            statuses["broker"]["status"] == "CLEAR",
            statuses["ollama"]["status"] == "READY",
            statuses["news"]["status"] == "READY",
        )
    )
    summary = _session_summary()
    bridge = statuses["bridge"]
    st.subheader("Live overview")
    st.caption("Quick status for the current paper-trading session.")
    columns = st.columns(3)
    columns[0].metric("Trading state", "RUNNING" if session_running else "BLOCKED")
    columns[1].metric("Instrument", instrument)
    columns[2].metric("Price", _format_indicator(bridge.get("midpoint")))
    columns = st.columns(3)
    columns[0].metric("Direction", strategy_direction)
    columns[1].metric("LLM gate", sentiment)
    columns[2].metric("Position", position)
    columns = st.columns(3)
    columns[0].metric("Spread", _format_indicator(bridge.get("quote_spread")))
    columns[1].metric("Realized P&L", f"${summary['realized_pnl']}")
    columns[2].metric("Closed trades", str(summary["closed_trades"]))
    columns = st.columns(3)
    columns[0].metric(
        "Last signal",
        _age_label(
            _age_seconds(strategy_rows[0]["created_at"]) if strategy_rows else None,
            60,
            "m ago",
        ),
    )
    columns[1].metric("Strategy", strategy)
    columns[2].metric("Mode", str(statuses["runtime"]["mode"]))




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
        "ema_crossover": "LONG/SHORT on fast EMA crossing above/below slow EMA; otherwise HOLD.",
        "rsi_mean_reversion": "LONG below RSI 30, SHORT above RSI 70, otherwise HOLD.",
        "breakout_channel": "LONG above the upper 20-bar channel, SHORT below the lower channel.",
    }
    st.subheader(f"Strategy & Live Indicators — {instrument}")
    st.caption(f"Strategy: {strategy}. Rules: {rules[strategy]}")
    if not rows:
        st.info("WAITING — no strategy decision has been recorded yet.")
        return
    row = rows[0]
    action = str(row["event_type"]).rsplit("_", 1)[-1]
    direction = {"BUY": "LONG", "SELL": "SHORT"}.get(action, action)
    columns = st.columns(5)
    columns[0].metric("Direction", direction)
    columns[1].metric("Current Price", _format_market_value(row["reference_price"]))
    columns[2].metric("Fast EMA / Value", _format_market_value(row["fast_average"]))
    columns[3].metric("Slow EMA / Value", _format_market_value(row["slow_average"]))
    columns[4].metric(
        "Distance to Crossover", _format_market_value(row["distance_to_crossover"])
    )
    st.caption(
        f"Decision rationale: {_format_log_text(row['decision_rationale'] or 'Unavailable')}"
    )




def _render_llm_sentiment(instrument: str, strategy: str) -> None:
    """Display the latest validated LLM gate result and its audit history."""
    st.subheader("LLM Sentiment Gate")
    current_strategy_rows = _query(
        """SELECT event_type
           FROM execution_logs
           WHERE provider = 'strategy' AND instrument = ? AND strategy_name = ?
           ORDER BY created_at DESC LIMIT 1""",
        (instrument, strategy),
    )
    current_action = (
        str(current_strategy_rows[0]["event_type"]).rsplit("_", 1)[-1]
        if current_strategy_rows
        else "WAITING"
    )
    current_direction = {"BUY": "LONG", "SELL": "SHORT"}.get(
        current_action, current_action
    )
    requires_sentiment = current_action in {"BUY", "SELL"}
    latest = _query(
        """SELECT evaluated_at, technical_action, provider, model, decision,
                  gate_reason, sentiment_score, confidence_score, risk_modifier,
                  reasoning, news_event_count
           FROM llm_decisions
           WHERE instrument = ? AND strategy_name = ?
           ORDER BY evaluated_at DESC, id DESC LIMIT 1""",
        (instrument, strategy),
    )
    if not latest:
        if requires_sentiment:
            st.warning("Current LLM gate: WAITING — no validated result yet.")
        else:
            st.info(
                f"Current LLM gate: NOT REQUIRED — current direction is {current_direction}."
            )
        return
    row = latest[0]
    decision = str(row["decision"])
    if requires_sentiment:
        if decision == "CONFIRM":
            st.success(f"Current LLM gate: {decision}")
        elif decision == "ADJUST_RISK":
            st.warning(f"Current LLM gate: {decision}")
        else:
            st.warning(f"Current LLM gate: {decision}")
    else:
        st.info(
            f"Current LLM gate: NOT REQUIRED — current direction is {current_direction}."
        )
    columns = st.columns(5)
    columns[0].metric("Last sentiment score", _format_indicator(row["sentiment_score"]))
    columns[1].metric("Last confidence", _format_indicator(row["confidence_score"]))
    columns[2].metric("Last risk modifier", _format_indicator(row["risk_modifier"]))
    columns[3].metric("Last news events", str(row["news_event_count"]))
    columns[4].metric(
        "Analysis age", _age_label(_age_seconds(row["evaluated_at"]), 3600, "h")
    )
    technical_direction = {
        "BUY": "LONG",
        "SELL": "SHORT",
    }.get(str(row["technical_action"]), str(row["technical_action"]))
    st.caption(
        f"Last validated provider/model: {row['provider']} / {row['model']} · "
        f"Technical direction: {technical_direction} · "
        f"Analyzed: {_philippine_time(row['evaluated_at'])}"
    )
    st.caption(f"Last analysis gate result: {row['gate_reason']}")
    with st.expander("LLM reasoning and recent decisions", expanded=False):
        reasoning = _format_log_text(str(row["reasoning"])[:500])
        st.dataframe(
            [{"Validated model reasoning": reasoning}],
            use_container_width=True,
            hide_index=True,
        )

        history = _query(
            """SELECT evaluated_at, technical_action, sentiment_score,
                      confidence_score, risk_modifier, decision, gate_reason,
                      provider, model
               FROM llm_decisions
               WHERE instrument = ? AND strategy_name = ?
               ORDER BY evaluated_at DESC, id DESC LIMIT 10""",
            (instrument, strategy),
        )
        st.caption("Recent validated LLM decisions")
        st.dataframe(
            [
                {
                    "Analyzed At": _philippine_time(item["evaluated_at"]),
                    "Direction": {
                        "BUY": "LONG",
                        "SELL": "SHORT",
                    }.get(str(item["technical_action"]), str(item["technical_action"])),
                    "Score": item["sentiment_score"],
                    "Confidence": item["confidence_score"],
                    "Risk Modifier": item["risk_modifier"],
                    "Decision": item["decision"],
                    "Gate Result": item["gate_reason"],
                    "Provider / Model": f"{item['provider']} / {item['model']}",
                }
                for item in history
            ],
            use_container_width=True,
            hide_index=True,
        )


def _format_indicator(value: object) -> str:
    return "Unavailable" if value is None else str(value)


def _render_circuit_breaker() -> None:
    """Display the effective persisted market-data entry gate."""
    rows = _query(
        """SELECT status, reason, candle_timestamp, spread, updated_at
           FROM circuit_breaker_status WHERE singleton_id = 1"""
    )
    controls = _query(
        """SELECT trading_halted, reason, updated_at
           FROM runtime_controls WHERE singleton_id = 1"""
    )
    row = rows[0] if rows else {}
    control = controls[0] if controls else {}
    live_bridge = _bridge_status()
    control_halted = bool(control.get("trading_halted"))
    live_halted = live_bridge["status"] != "READY"
    status = (
        "HALTED"
        if control_halted or row.get("status") == "HALTED" or live_halted
        else "CLEAR"
    )
    reason = str(
        live_bridge["detail"]
        if live_halted
        else control.get("reason") or row.get("reason") or "no runtime health record"
    )
    if not rows and not controls and not live_halted:
        st.warning("Circuit breaker: UNKNOWN (no runtime health record)")
        return
    if status == "CLEAR":
        st.success(f"Circuit breaker: {status} — {reason}")
    else:
        st.error(f"Circuit breaker: {status} — {reason}")
    quote_age = live_bridge.get("quote_age")
    st.caption(
        f"Last candle: {_philippine_time(row.get('candle_timestamp')) or 'Unavailable'}; "
        f"spread: {row.get('spread') or 'Unavailable'}; "
        f"updated: {_philippine_time(control.get('updated_at') or row.get('updated_at'))}"
    )
    reason_lower = reason.lower()
    max_age = _decimal(os.getenv("MAX_DATA_AGE_SECONDS", "300"))
    feed_fresh = (
        quote_age is not None
        and float(_decimal(quote_age)) <= float(max_age)
        and not live_halted
        and not control_halted
    )
    columns = st.columns(4)
    columns[0].metric("Feed freshness", "CLEAR" if feed_fresh else "HALTED")
    columns[1].metric(
        "Clock drift",
        "HALTED" if "clock" in reason_lower else "CLEAR",
    )
    columns[2].metric(
        "Spread tolerance",
        "HALTED" if "spread" in reason_lower else "CLEAR",
    )
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
        "NO DATA"
        if not rows
        else (
            "∞"
            if not losses and wins
            else f"{len(wins) / len(losses):.2f}"
            if losses
            else "NO DATA"
        )
    )
    profit_factor = (
        "NO DATA"
        if not rows
        else (
            "∞"
            if gross_wins > 0 and gross_losses == 0
            else f"{gross_wins / gross_losses:.2f}"
            if gross_losses
            else "NO DATA"
        )
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
    st.caption(
        "NO DATA means no closed paper trades exist yet. "
        "MAE/MFE are not currently recorded."
    )
    columns = st.columns(6)
    columns[0].metric("Win/Loss Ratio", win_loss)
    columns[1].metric("Profit Factor", profit_factor)
    columns[2].metric(
        "Average Holding Time",
        "NO DATA" if average_holding is None else f"{average_holding:.2f}h",
    )
    columns[3].metric(
        "Expectancy / $ Risked",
        "NO DATA" if expectancy is None else f"${expectancy:.4f}",
    )
    columns[4].metric("MAE", "NOT RECORDED")
    columns[5].metric("MFE", "NOT RECORDED")


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
    if not rows:
        st.info("FLAT — no open positions.")
        return
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
    with st.expander("Audit logs", expanded=False):
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
                    "Decision Rationale": _format_log_text(
                        row["decision_rationale"] or row["message"]
                    ),
                    "Error Class": row["error_class"],
                    "Message": _format_log_text(row["message"]),
                    "Latency (ms)": row["latency_ms"],
                    "Slippage": _format_market_value(row["slippage"]),
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
    selected_instrument, selected_strategy = _render_instrument_control()
    operational = _render_operational_readiness()
    _render_quick_overview(selected_instrument, selected_strategy, operational)
    _render_emergency_control()
    _render_circuit_breaker()
    _render_strategy_state(selected_instrument, selected_strategy)
    _render_llm_sentiment(selected_instrument, selected_strategy)
    _render_summary()
    _render_trader_metrics()
    _render_positions()
    _render_audits()




if __name__ == "__main__":
    main()
