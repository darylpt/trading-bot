# Trading Bot Roadmap

This roadmap is the delivery contract for the paper/demo trading system. Work advances in phase order and preserves fail-closed risk and execution boundaries.

## Phase 1 — Runtime Foundation (Complete)

- Containerize the Python 3.11 runtime with Docker Compose.
- Persist operational records in SQLite, with optional PostgreSQL infrastructure.
- Provide the cross-shell persistence CLI for schema initialization and metrics inspection.
- Seed valid OHLCV market data when the configured default file is absent.
- Support single-tick execution with `python -m trading_bot --once`.
- Support daemon execution with a configurable `TICK_INTERVAL_SECONDS` schedule.
- Persist `session_metrics.db` records on every tick.

## Phase 2 — Strategy Signals (Complete)

- Implement the typed moving-average crossover strategy in `src/trading_bot/strategy.py`.
- Consume validated OHLCV candles from `market_data.csv`.
- Emit `BUY`, `SELL`, or `HOLD` signals.
- Cover crossover, warmup, HOLD, and CSV-ingestion behavior with pytest.

## Phase 3 — Risk Management & Guardrails (Complete)

- Add `src/trading_bot/risk.py` as the pre-trade risk boundary.
- Enforce position sizing with a maximum configured risk fraction of 1–2%; the existing one-percent execution invariant remains the hard ceiling unless explicitly reviewed.
- Enforce `DAILY_DRAWDOWN_LIMIT` against current account equity and the session baseline.
- Reject HOLD signals, invalid prices, non-positive stop distances, missing exits, and orders that exceed account or risk limits.
- Wire risk approval into `run_tick()` before any order can be considered executable.
- Record risk rejection reasons in operational logs and metrics.

## Phase 4 — Paper Execution & Position Tracking (Complete)

- Implement `src/trading_bot/execution.py` for paper-order lifecycle tracking.
- Track open positions, entry and exit prices, quantity, direction, and execution status.
- Attach and monitor mandatory Stop Loss and Take Profit levels.
- Reconcile fills and position state without bypassing the execution gate.
- Update `session_metrics.db` with position, realized P&L, and win/loss outcomes.

## Phase 5 — Exness MT5 Bridge (Complete)

- Integrate a demo-safe Exness MT5 adapter using `BROKER_TOKEN`, `BROKER_ACCOUNT`, `BROKER_SERVER`, and `BROKER_ENDPOINT`.
- Fall back to the local simulated feed for `BROKER_ENV=demo` and unavailable MT5 bindings.
- Poll bid/ask quotes and aggregate midpoint OHLCV bars into `market_data.csv`.
- Wire the gateway into scheduled `run_tick()` execution using `TICK_INTERVAL_SECONDS`.
- Preserve credential isolation, stale-data rejection, idempotency, and fail-closed behavior.

## Resume Verification

Before starting the next phase, inspect persisted paper metrics with:

```text
docker compose exec app python -m persistence --database "/app/data/session_metrics.db" --query
```

## Delivery Rules

- No live-money path is enabled by default.
- Strategies produce typed signals; risk produces approved order intents; execution is the only broker submission boundary.
- Provider failures, stale data, missing configuration, schema failures, and unknown order state reject or close safely.
- Every phase requires deterministic tests, type checks, lint, formatting, and the relevant paper/demo smoke path.
