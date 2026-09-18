# Phase 7 Specification — Controlled Demo Forward Test
## Status

**Staged but blocked.** The production `BROKER_DEMO` entrypoint, readiness gate, durable SQLite metrics, and interval control are available; no multi-day run starts until the lifecycle gate, approved window, alert route, and quantitative thresholds are provided.

## Problem

Unit tests and a short demo smoke test cannot prove multi-day continuity, restart behavior, reconciliation, or operational stability.

## Scope

- Run a controlled, demo-only, multi-day Exness MT5 forward test.
- Measure uptime, data continuity, spread, slippage, latency, rejected orders, protective-exit confirmation, P&L reconciliation, restart recovery, and halts.
- Review every halt and unexplained mismatch before changing strategy complexity or risk.
- Produce a redacted evidence package and explicit go/no-go decision.

## Non-goals

No live credentials, live endpoint, increased risk, unattended production claim, or strategy optimization during the test.

## Invariants

- `PAPER_TRADING=true` and `LIVE_TRADING=false` remain enforced.
- Any stale, unknown, unprotected, unreconciled, or secret-exposing condition halts safely.
- Metrics are sanitized, timestamped, reproducible, and attributable to a configuration revision.
- Forward-test evidence cannot be substituted by local simulation.

## Dependencies and interfaces

Requires verified Phases 1–6, an approved demo account, controlled test window, durable metrics, and operator review procedures.

## Acceptance evidence

A recorded multi-day run includes configuration, runtime, connectivity, data, execution, risk, reconciliation, recovery, alert, and secret-hygiene evidence. All halts and mismatches have dispositions.

## Exit criteria

The project has a reviewed demo-only operational result. No live-trading readiness is implied.
## Unresolved decisions

- Test duration, trading schedule, and approved demo instrument after prior-phase evidence.
- Quantitative go/no-go thresholds for continuity, latency, slippage, and reconciliation.
## Executable slice contract — P7-S1 controlled forward test

**Precondition:** Phases 1–6 are verified; the demo account, authorized instrument, operator schedule, durable metrics, alert route, and recovery procedure are approved.

### Ownership and interface

- **Runtime under test:** the production `BROKER_DEMO` path through `src/trading_bot/__main__.py`, `src/trading_bot/engine.py`, `src/execution/`, `src/observability/`, and `src/persistence/`.
- **Readiness gate:** `src/observability/readiness.py`; no forward-test run may bypass it.
- **Evidence consumer:** `tests/integration/test_forward_test_readiness.py` plus a redacted operator evidence package outside source control.
- Inputs are an immutable configuration revision, controlled demo account/instrument/window, and verified runtime. Outputs are timestamped sanitized metrics, incident/halt dispositions, restart/reconciliation records, and an explicit demo-only go/no-go decision.

### State transitions

`APPROVED_WINDOW → STARTING → RUNNING`; stale data, provider disconnect, clock drift, rejected/unknown order, missing protection, reconciliation mismatch, database failure, or secret exposure transitions to `HALTED`; `HALTED → RECOVERY_CHECKING → RUNNING` requires fresh readiness and reconciliation evidence; `RUNNING → COMPLETED` occurs only at the scheduled end with all positions safely closed.

### Failure and security policy

Enforce `PAPER_TRADING=true`, `LIVE_TRADING=false`, demo provider, allowlisted endpoint, and operator authorization for the entire run. Any unknown, unreconciled, unprotected, stale, or secret-exposing state halts new entries and follows the recovery contract. Do not optimize strategy, alter risk, use live credentials/endpoints, or substitute local simulation for demo evidence.

### Acceptance scenarios

1. The run records configuration revision, mode/provider, uptime, connectivity, quote/candle continuity, spread, latency, slippage, order outcomes, protection confirmations, P&L reconciliation, restarts, alerts, and halts.
2. An injected disconnect, stale quote, restart, unknown order, reconciliation mismatch, database failure, or redaction violation halts safely and records a disposition.
3. Recovery resumes only after fresh readiness and reconciliation; no duplicate order or position increase occurs.
4. The scheduled run closes all positions safely and produces a complete redacted evidence package.
5. Operator review records go/no-go against thresholds decided from prior-phase evidence; the result makes no live-trading claim.

### Verification

- `python -m pytest tests/integration/test_forward_test_readiness.py`
- Run the repository compile, test, type, lint, format, and Compose checks from `AGENTS.md` before the controlled window.
- Execute the approved multi-day demo-only run; archive sanitized runtime, database, alert, restart, reconciliation, and secret-scan evidence.
- Review every halt, mismatch, rejected order, and unexplained metric before recording the decision.

### Exit gate

Mark this slice verified only after the multi-day demo evidence is complete, all incidents have dispositions, all positions are closed/reconciled, and an operator review records the demo-only go/no-go decision. This gate never enables live trading.
