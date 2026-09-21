# Phase 5 Specification — Reconciliation and Recovery
## Status

**Verified.** The controlled demo lifecycle reconciled and closed safely; deterministic failure-injection coverage passes; and a controlled daemon restart after a transient provider halt restored fresh readiness with durable halt state cleared only after operator review, zero open positions, and no duplicate submission.

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

Acceptance evidence is recorded:

- Targeted reconciliation and failure-injection suite: `27 passed`.
- Controlled demo lifecycle: protected fill, broker/local reconciliation, safe close, durable `FILLED`/`CLOSED` state, and no residual broker position.
- Controlled daemon restart: startup readiness passed after restart, durable trading halt remained cleared only after fresh readiness and operator review, and `0` broker/local open positions remained.

## Exit criteria

Broker/local state converges deterministically or remains safely halted with a recorded reason.
## Decisions and remaining gate

- **Resolved:** reconcile at startup and before every retry or position increase; the active daemon performs readiness/reconciliation checks before entries.
- **Resolved:** use one-position-per-instrument netting semantics for this demo path; externally opened or mismatched positions remain `EXTERNAL_REVIEW`/`HALTED` until operator-authorized closure.
- **Verified:** the restart/recovery run converged with durable state, no duplicate submission, and no unprotected exposure.
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

Historical attempts returned no matching deal or open position, but the prior bridge contract surfaced that absence as reconciliation unavailability. The corrected bridge queries deal history, open positions, and pending orders, returning a timestamped typed `ORDER_NOT_FOUND` result only when all authoritative snapshots succeed. Any transport or incomplete-history failure remains `UNKNOWN`.

The successful controlled smoke reconciled a filled `XAUUSDm` position, closed it safely, observed no matching open position afterward, and persisted a `CLOSED` position record. The adapter and durable execution state still preserve `ORDER_NOT_FOUND` distinctly from `UNKNOWN`; operator review is required for the earlier persisted halt, and `ORDER_NOT_FOUND` is never converted to `FILLED`.

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
