# Phase 2 Specification — Broker Market Data Continuity
## Status

**Implemented locally.** Broker quote/candle validation and backfill code exist; broker historical session/maintenance gaps are preserved explicitly, while live freshness and continuity evidence remains required for verification.

## Problem

Execution decisions require trustworthy broker bid/ask quotes and closed-candle history. A seeded CSV or midpoint-only feed cannot prove freshness, continuity, or executable pricing.

## Scope

- Backfill and persist Exness candles with provider timestamps.
- Preserve bid/ask quotes and validate finite prices, `ask > bid`, spread, freshness, symbol, timeframe, and clock drift.
- Track aggregation state and last processed provider timestamp across restart.
- Detect gaps, duplicates, out-of-order data, forming candles, market closures, and provider maintenance.

## Non-goals

No broker order submission, broker-aware sizing, live mode, or strategy redesign.

## Invariants

- Signals use closed candles unless an explicit intrabar contract exists.
- Unclassified stale, crossed, wide-spread, mismatched, or ambiguous data blocks new entries; known broker session/maintenance gaps remain observable and do not become synthetic candles.
- `BROKER_DEMO` never substitutes local fixtures.
- Restart resumes from validated persisted state.

## Dependencies and interfaces

Requires verified Phase 1 readiness and supplies typed quotes, candles, freshness, continuity, and halt state to strategy and risk boundaries.

## Acceptance evidence

Tests cover stale/crossed/wide-spread quotes, timestamp drift, gaps, duplicates, out-of-order candles, symbol/timeframe mismatch, weekend/rollover boundaries, and restart recovery. A controlled demo check proves backfill and live quote continuity.

## Exit criteria

The broker market-data path is restart-safe, observable, and verified against the listed failure and continuity scenarios.
## Unresolved decisions

- Exact broker candle and quote transport once Phase 1 selects the deployment boundary.
- Retention and schema details for persisted aggregation state.
## Executable slice contract — P2-S1 broker market-data continuity

**Precondition:** the Phase 1 read-only readiness slice is verified.

### Ownership and interface

- **Provider ingestion:** `src/trading_bot/broker/market_data.py`.
- **Normalization and validation:** `src/strategy/market_data.py`, `src/strategy/sessions.py`, and `src/domain/models.py`.
- **Persistence:** `src/persistence/schema.sql` and `src/persistence/sqlite.py`.
- **Pipeline integration:** `src/trading_bot/engine.py` and `src/trading_bot/__main__.py`.
- **Tests:** `tests/unit/test_broker.py`, `tests/unit/test_market_data_seed.py`, and new/updated market-data integration coverage.
- The adapter emits typed bid/ask quotes, closed candles, provider timestamps, continuity status, and an entry halt reason; it does not emit orders.

### Inputs and outputs

Inputs are an authenticated Exness market-data provider, symbol/timeframe, persisted aggregation cursor, local clock, spread/freshness/session limits, and broker history. Outputs are validated quote/candle records, updated cursor state, and `DATA_READY` or a specific `DATA_HALTED` reason.

### State transitions

`NO_CURSOR → BACKFILLING → DATA_READY`; `DATA_READY → STREAMING`; `STREAMING → DATA_HALTED` for stale/crossed/wide-spread/gapped/mismatched data, clock drift, provider maintenance, or persistence failure; `DATA_HALTED → BACKFILLING` only after a fresh broker backfill validates continuity. Forming candles remain `FORMING` and cannot trigger entries; closed candles transition to `CLOSED → PROCESSED` exactly once.

### Failure policy

Reject non-finite/non-positive prices, `ask <= bid`, stale timestamps, symbol/timeframe mismatches, duplicates, out-of-order candles, unexplained gaps, and invalid aggregation state. Weekend, rollover, and maintenance boundaries use an explicit session policy rather than treating expected closure as a data gap. In `BROKER_DEMO`, fixture reads are forbidden.

### Acceptance scenarios

1. Valid backfill produces chronologically ordered closed candles and preserves provider timestamps, bid, ask, and spread.
2. Crossed, stale, wide-spread, gapped, duplicate, out-of-order, mismatched, or clock-drifted data yields `DATA_HALTED` and blocks new entries.
3. Forming candles never reach signal generation; a restart resumes from the persisted cursor without duplicate processing.
4. Weekend/rollover and provider maintenance are classified deterministically and recover through backfill.
5. A controlled demo check proves backfill, live quote continuity, persistence, and safe halt/recovery without local fallback.

### Verification

- `python -m pytest tests/unit/test_broker.py tests/unit/test_market_data_seed.py tests/integration/test_technical_pipeline_dry_run.py`
- Add focused market-data continuity tests for every scenario above before implementation is marked verified.
- `python -m mypy src`
- `python -m ruff check .`
- `python -m ruff format --check .`
- Opt-in demo market-data smoke test records provider timestamps, cursor recovery, quote continuity, and halt reasons.

### Exit gate

Mark this slice verified only when deterministic continuity/failure tests and the controlled demo backfill/stream evidence pass. The next slice may consume only `DATA_READY` data.
