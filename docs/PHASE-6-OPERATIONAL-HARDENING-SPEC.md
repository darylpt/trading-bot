# Phase 6 Specification — Operational Hardening
## Status

**Implemented locally.** Readiness, alerts, durable controls, and Compose isolation exist; failure-injection and runtime evidence remain required for verification.

## Problem

Process health alone cannot show whether trading is safe. Operators need readiness, sanitized audit history, alerts, and durable failure evidence.

## Scope

- Define explicit process-health and trading-readiness states.
- Persist sanitized decision, quote, risk, order, fill, reconciliation, halt, recovery, and configuration-version records.
- Alert on disconnects, stale data, clock drift, unprotected positions, unknown orders, mismatches, drawdown halts, database failures, and fallback activation.
- Resolve SQLite/PostgreSQL ownership and remove unused hard startup dependencies.
- Add failure-injection coverage across provider, data, risk, execution, persistence, and restart boundaries.

## Non-goals

No live enablement, risk increase, strategy complexity, or replacement of the single execution gate.

## Invariants

- Readiness is fail-closed and distinguishes every blocking dependency.
- Logs and persistence contain no credentials, authorization headers, signed URLs, or unredacted provider payloads.
- Alerts do not mutate trading state except through explicit authorized control paths.
- Durable records permit decision and order reconstruction.

## Dependencies and interfaces

Requires verified broker lifecycle and reconciliation. Provides operational state, audit records, alerts, and failure evidence to the forward-test phase.

## Acceptance evidence

Automated redaction, persistence, readiness, alert, and failure-injection checks pass. Compose startup and database ownership are demonstrated with safe runtime credentials.

## Exit criteria

Operational readiness is observable, durable, secret-safe, and verified under injected failures.
## Unresolved decisions

- Final alert transport and operator ownership.
- SQLite/PostgreSQL ownership boundary after runtime evidence is collected.
## Executable slice contract — P6-S1 operational readiness

**Precondition:** Phases 1–5 are verified, including reconciled order/position state.

### Ownership and interface

- **Readiness state:** `src/observability/readiness.py`, `src/config/health.py`, and `src/trading_bot/engine.py`.
- **Sanitized audit and alerts:** `src/observability/logging.py`, `src/persistence/sqlite.py`, and `src/persistence/schema.sql`.
- **Runtime ownership:** `Dockerfile`, `docker-compose.yml`, `.dockerignore`, and the Compose health/readiness commands.
- **Tests:** `tests/integration/test_persistence_observability.py`, `tests/unit/test_container_runtime.py`, readiness/logging tests, and failure-injection coverage.
- Inputs are dependency health signals and lifecycle events. Outputs are separate process-health and trading-readiness states, durable sanitized records, alerts, and explicit halt/recovery decisions.

### State transitions

`PROCESS_STARTING → PROCESS_HEALTHY`; readiness independently transitions `NOT_READY → READY` only when mode, authentication, market data, account, instrument, database, clock, risk, and reconciliation checks pass. Any dependency failure transitions `READY → HALTED`; recovery requires a fresh successful check and reconciliation, not a process restart alone. `HALTED → READY` is never automatic when state is unknown or unprotected.

### Failure and security policy

Persist enough structured data to reconstruct decision, quote, sentiment, risk, order, fill, reconciliation, halt, recovery, and configuration revision without raw payloads or secrets. Alert delivery is non-trading side effect only; only explicit authorized control paths may change state. Define one authoritative SQLite/PostgreSQL owner before enabling both. Test and dashboard services receive no broker/LLM credentials.

### Acceptance scenarios

1. Process health can be healthy while trading readiness is `NOT_READY` or `HALTED` for each missing dependency.
2. Every configured alert condition emits a sanitized, attributable alert and does not submit/cancel/modify an order.
3. Persisted records survive restart and reconstruct one decision-to-reconciliation chain.
4. Secret-like values, authorization headers, signed URLs, and raw provider payloads are rejected or redacted in logs and persistence.
5. Compose starts with least-privilege environment injection, durable volumes, and a database owner that is demonstrably writable.
6. Injected provider, data, risk, execution, persistence, and restart failures leave the system halted and observable.

### Verification

- `python -m pytest tests/integration/test_persistence_observability.py tests/unit/test_container_runtime.py`
- Add readiness-state, alert, redaction, persistence-restart, and failure-injection tests before verification.
- `python -m mypy src`
- `python -m ruff check .`
- `python -m ruff format --check .`
- `docker compose build` and `docker compose run --rm test pytest -q`
- Paper-only Compose smoke run records process health, trading readiness, one sanitized lifecycle chain, and an injected halt/recovery.

### Exit gate

Mark this slice verified only when readiness, audit, alert, persistence, Compose isolation, and failure-injection evidence pass. Phase 7 cannot begin on process-health evidence alone.
