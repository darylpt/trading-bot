# Trading Bot Roadmap

This roadmap is the delivery contract for the paper/demo trading system. Work advances in phase order and preserves fail-closed risk and execution boundaries.

## Phase 0 — Secure and Freeze the Baseline (Verified)

- Establish explicit `SIMULATED`, `BROKER_DEMO`, and unavailable `LIVE` runtime modes.
- Select Exness MT5 as the broker-demo architecture behind `BaseBroker`; retain `SimulatedBroker` for local paper testing.
- Rotate exposed credentials and prove secret exclusion/redaction.
- Reconcile all planning and specification documents with the canonical readiness and SDD documents.
- Freeze a reproducible local simulation baseline with configuration, dependency, fixture, schema, and command evidence.
- Complete the acceptance scenarios in `docs/PHASE-0-SECURE-BASELINE-SPEC.md`.

Phase 0 is verified against the recorded baseline manifest, fresh bridge/readiness evidence, protected demo lifecycle evidence, and operator acceptance record. It does not enable live trading.

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

## Phase 5 — Broker-Connected Demo Trading (Verified)

- Implement the Exness MT5 demo deployment and adapter contract behind `BaseBroker`.
- Separate `SIMULATED` and `BROKER_DEMO` modes; broker-demo failures MUST halt entries rather than use local fallback data.
- Authenticate the demo account and validate account state, instrument metadata, trading session, quote freshness, clock drift, and database readiness.
- Backfill and persist broker candles, aggregation state, bid/ask quotes, and provider timestamps.
- Route demo orders through the single execution gate.
- Confirm broker-side stop-loss and take-profit attachment before treating a position as protected.
- Add client idempotency, partial/unknown-order handling, broker-position reconciliation, and restart recovery.
- Prove the complete lifecycle with an opt-in demo smoke test.

Recorded evidence includes the protected `0.01` LONG XAUUSDm fill/reconcile/close lifecycle, fresh read-only readiness, deterministic failure injection, and a controlled daemon restart with zero residual positions and no duplicate submission. Multi-day operational evidence remains in Phase 7.

## Phase 6 — Operational Hardening (Verified)

- Upgrade process health into explicit trading-readiness states.
- Persist sanitized decision, quote, risk, order, fill, reconciliation, halt, recovery, and configuration-version records.
- Emit sanitized structured-log alerts and support optional HTTPS webhook delivery for broker disconnects, stale data, clock drift, unprotected positions, unknown orders, reconciliation mismatches, drawdown halts, database failures, and repeated rejections.
- Enforce the configured forward-test entry window without bypassing the execution gate.
- Resolve SQLite/PostgreSQL ownership and remove unused hard startup dependencies.
- Add failure-injection coverage for provider, data, risk, execution, persistence, and restart failures.

Phase 6 is verified: the structured-alert failure/recovery record is archived in the Phase 5/6 specifications and current runtime evidence; external webhook delivery remains optional.

## Phase 7 — Controlled Forward Test (In progress / unverified)

- Run a multi-day demo-only forward test with no live endpoint or live credentials.
- Measure uptime, data continuity, spread, slippage, latency, rejected orders, protective-exit confirmation, P&L reconciliation, and restart recovery.
- Review every halt and unexplained mismatch before changing strategy complexity or risk.

The controlled paper-only runtime remains configured for the approved XAUUSDm window and structured alert route. Windows time synchronization now passes, but the bridge has no fresh XAUUSDm tick after the supervised process exit; restart validation rejected approximately `47,300s`-old quote data. Phase 7 remains unverified and fail-closed pending fresh broker data and restart-incident review.

The canonical requirements and acceptance evidence for Phases 5–7 are in `docs/PAPER-TRADING-READINESS.md`.

All phases now have both a strategic contract and an executable slice contract in `docs/PHASE-*-SPEC.md`. Phases 0–6 are verified against recorded evidence; Phase 7 retains its multi-day operational acceptance gate and is currently blocked on provider freshness.

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
