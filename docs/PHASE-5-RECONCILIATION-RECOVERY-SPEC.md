# Phase 5 Specification — Reconciliation and Recovery
## Status

**Implemented locally.** Durable reconciliation and halt behavior exist; restart and broker-state evidence remain required for verification.

## Problem

Process failures, timeouts, partial fills, and external broker changes can leave local and broker state inconsistent or expose unprotected positions.

## Scope

- Reconcile open orders and positions after submission timeout, provider reconnect, process failure, and restart.
- Persist client and broker identifiers, requested/actual values, protective exits, and state transitions.
- Recover safely from partial, unknown, cancelled, expired, and externally changed positions.
- Define supported netting/hedging and one-position-per-instrument behavior.

## Non-goals

No new strategy, live mode, blind retries, or risk bypass.

## Invariants

- Unknown state blocks position increases.
- No position is considered safe without confirmed protection.
- Reconciliation mismatch halts new entries and is observable.
- Non-idempotent submissions are never blindly retried.

## Dependencies and interfaces

Requires verified demo execution and persistence contracts. Supplies authoritative reconciled order/position state and recovery decisions to operational readiness.

## Acceptance evidence

Failure injection covers timeout, restart, duplicate submission, partial fill, unknown order, external position change, missing protection, and database failure. Recovery evidence proves safe halt, reconciliation, and restart continuity.

## Exit criteria

Broker/local state converges deterministically or remains safely halted with a recorded reason.
## Unresolved decisions

- Authoritative reconciliation cadence and broker history window.
- Final netting/hedging policy and treatment of externally opened positions.
## Executable slice contract — P5-S1 reconciliation and recovery

**Precondition:** Phase 4 demo execution and the required persistence contract are verified.

### Ownership and interface

- **Reconciliation service:** `src/execution/reconciliation.py` (new boundary module only if existing executor interfaces cannot own it).
- **Execution state:** `src/execution/executor.py`, `src/execution/protocols.py`, and `src/execution/payloads.py`.
- **Persistence:** `src/persistence/sqlite.py` and `src/persistence/schema.sql`.
- **Runtime recovery:** `src/trading_bot/engine.py` and `src/observability/readiness.py`.
- **Tests:** `tests/integration/test_execution_gate.py`, `tests/integration/test_paper_execution.py`, `tests/integration/test_persistence_observability.py`, and failure-injection coverage.
- Input is local lifecycle state plus authoritative broker orders/positions. Output is a reconciled typed state, a recovery action, and a durable sanitized transition record.

### State transitions

`LOCAL_UNKNOWN → RECONCILING`; matching broker/local state transitions to `RECONCILED`; missing broker confirmation, timeout, restart, or provider reconnect remains `UNKNOWN` and blocks entries; partial fills transition to `PARTIALLY_FILLED`; external positions transition to `EXTERNAL_REVIEW`; missing protection transitions to `UNPROTECTED → HALTED`; safe convergence transitions to `RECOVERED`; irreconcilable mismatch remains `HALTED`.

### Failure and recovery policy

Reconcile before any retry or position increase. Use client idempotency keys and broker IDs to collapse duplicate observations. Never blindly resubmit unknown orders. Require explicit policy for externally opened positions and netting/hedging before enabling that path. A database failure preserves the halt and prevents claiming reconciliation. Record mismatch, halt, recovery, and closure reasons without raw provider payloads.

### Observed reconciliation blocker and corrective contract

The current bridge returns a non-success response when no matching deal or open position is found. The adapter treats that response as broker unavailability, so the durable intent remains `UNKNOWN` even when the account snapshot contains no matching position.

The reconciliation contract must distinguish `ORDER_NOT_FOUND` from transport failure while remaining fail-closed: query authoritative deal history and open positions, return a typed not-found result with evidence timestamps, keep the trading halt until the result is operator-reviewed, and never convert not-found into `FILLED`. Transport or incomplete-history failures remain `UNKNOWN`.

### Acceptance scenarios

1. Matching local and broker snapshots converge to `RECONCILED` idempotently across repeated runs.
2. Submission timeout, process restart, reconnect, duplicate observation, partial fill, unknown order, external position, missing protection, and database failure produce the specified safe state.
3. Any mismatch blocks new entries until a fresh broker snapshot resolves it or an operator-authorized safe closure completes.
4. Restart restores pending lifecycle state and does not duplicate orders or fills.
5. A controlled demo restart/reconcile/close scenario proves durable identifiers, position convergence, and safe halt behavior.

### Verification

- `python -m pytest tests/integration/test_execution_gate.py tests/integration/test_paper_execution.py tests/integration/test_persistence_observability.py`
- Add failure-injection tests for timeout, restart, duplicate, partial, unknown, external, unprotected, and persistence-failure cases.
- `python -m mypy src`
- `python -m ruff check .`
- `python -m ruff format --check .`
- Opt-in demo recovery smoke test records pre/post broker and local state, reconciliation decision, and sanitized evidence.

### Exit gate

Mark this slice verified only when broker/local state converges deterministically or remains explicitly halted, and restart evidence proves no duplicate submission or unprotected exposure.
