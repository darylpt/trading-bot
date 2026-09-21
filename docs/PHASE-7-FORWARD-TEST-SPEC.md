# Phase 7 Specification — Controlled Demo Forward Test
## Status

**Running; unverified.** Windows time synchronization is now healthy, but the bridge has no fresh XAUUSDm market tick after the supervised daemon/bridge exit. A post-resync readiness attempt correctly failed closed on stale quote data; no completion claim is made until the market feed resumes and the multi-day evidence, recovery evidence, and final review are recorded.

## Current runtime evidence

- Windows time synchronization remains healthy: `Leap Indicator: 0`, `Source: pool.ntp.org,0x9`, and latest stripchart offset approximately `+0.36s`, within the `5s` tolerance.
- The restarted bridge passes health/authentication, but the latest XAUUSDm quote is approximately `47,300s` old and the latest candle is approximately `48,977s` old. The broker session endpoint may report open, but the feed has no fresh tick; readiness rejects it with `StaleMarketDataError`.
- Durable runtime halt is `True` with reason `StaleMarketDataError`; no local/broker position is open and no order was submitted during recovery.
- The supervised daemon exit code `1073807364` (`0x40010004`) and subsequent stale-feed recovery attempt are recorded as an operational incident. Application tolerance remains `5s`.
- The earlier clock-drift incident is resolved by host NTP synchronization; it was not addressed by widening application tolerance.

## Current operator disposition

- **NO-GO to resume at the current probe:** host time is corrected, but the broker quote/candle stream is stale. Do not clear the durable halt until fresh quote/candle readiness passes.
- This is an interim demo-only safety disposition. The final decision still requires fresh broker data, five approved weekdays, incident dispositions, all-position reconciliation, and operator review; it makes no live-trading claim.

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
## Decisions

- **Approved:** five weekdays, Monday–Friday, 13:00–16:00 UTC; readiness at 12:45 UTC; close and reconcile by 16:15 UTC.
- **Approved:** sanitized structured logs are the mandatory alert destination for this run; the HTTPS webhook boundary remains optional and no URL is stored in source control.
- **Approved:** daily drawdown `0.01`, maximum XAUUSDm spread `0.35`, maximum slippage `2.0` pips, maximum clock drift `5` seconds, and maximum market-data age `300` seconds.

The runtime is active under these decisions. The phase remains unverified until the multi-day evidence package, restart/recovery review, incident dispositions, and final demo-only go/no-go decision are complete.

### Approved forward-test operating defaults

These defaults govern the active paper-only run. They do not enable live trading or change the risk limit.

| Control | Approved value | Halt or review behavior |
| --- | --- | --- |
| Runtime | `BROKER_DEMO`, `PAPER_TRADING=true`, `LIVE_TRADING=false` | Reject any contradictory configuration |
| Instrument | `XAUUSDm` | Reject an unauthorized symbol |
| Entry window | Weekdays, 13:00–16:00 UTC | No new entries outside the window |
| Readiness/recovery | 12:45 UTC preflight; reconcile by 16:15 UTC | Do not start or finish with unknown state |
| Daily drawdown | `1%` of session-start equity | Halt new entries for the trading day |
| Maximum spread | `0.35` price units | Halt new entries until a fresh quote is within tolerance |
| Maximum slippage | `2.0` pips | Reject/review the affected execution |
| Clock/data freshness | `5s` drift; `300s` data age | Halt new entries and require fresh readiness |
| Alert events | disconnect, stale data, clock drift, auth failure, unknown order, reconciliation mismatch, unprotected position, drawdown halt, database failure, fallback, repeated rejection | Emit a sanitized structured-log alert and block entries until fresh readiness/reconciliation; optional webhook delivery is non-authoritative |

The schedule is deliberately narrower than the full 24/5 market. `13:00–16:00 UTC` is the approved conservative London/New York overlap window for this run; reapproval is required if observed liquidity or broker session behavior changes.
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
