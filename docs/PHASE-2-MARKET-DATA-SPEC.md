# Phase 2 Specification — Broker Market Data Continuity
## Status

**Verified.** Broker quote/candle validation and backfill code exist; broker historical session/maintenance gaps are preserved explicitly. The controlled demo run passed fresh quote/candle readiness and repeated bridge quote/candle polling before a later freshness incident. The current incident is correctly fail-closed: Windows NTP status is unsynchronized (`Local CMOS Clock`) and `w32tm` measured approximately `+5.52s` local offset, matching the apparent quote lead; no tolerance was relaxed and no entry occurred.

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

Acceptance evidence is recorded:

- Complete market-data acceptance tests: `33 passed`, including bridge routing, stale/future quote rejection, candle chronology/gap validation, aggregation, persistence, and no simulated fallback in broker-demo mode.
- Controlled demo evidence: authenticated Exness bridge, `256` closed candles, valid XAUUSDm quote/spread, repeated live quote/candle polling, persisted active instrument, and safe restart behavior.
- Failure evidence: the current stale/future provider data transitioned startup to a fail-closed halt without an order or position increase.

## Exit criteria

The broker market-data path is restart-safe, observable, and verified against the listed failure and continuity scenarios.
## Resolved decisions

- **Resolved:** Phase 1's Option B native Windows Exness bridge is the broker candle/quote transport.
- **Resolved:** the existing SQLite-backed CSV aggregation state and provider timestamps are the persistence contract; known broker session/maintenance gaps remain explicit rather than synthesized.
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
