# Project TODO

This is the persistent project task list. It is the source of truth for the next session's work queue. Keep task status evidence-based; do not mark a task complete because code or documentation exists.

## Status convention

Use these statuses instead of treating a checkbox as proof of completion:

- `PROPOSED` — identified but not yet started.
- `IN_PROGRESS` — actively being worked.
- `BLOCKED` — cannot proceed until a named dependency or decision is resolved.
- `IMPLEMENTED` — code or documentation exists, but acceptance evidence is incomplete.
- `VERIFIED` — acceptance evidence is recorded and the contract passes.
- `DEFERRED` — intentionally postponed with a reason.
- `REJECTED` — intentionally not pursuing, with a reason.

`VERIFIED` is the only status that satisfies an SDD acceptance gate. `IMPLEMENTED` must never be described as complete. Every `VERIFIED` task should link to its evidence or command output.

## Current phase — Phase 0: Secure and Freeze the Baseline

- **IMPLEMENTED** — Baseline safety contracts and runtime mode matrix are in place; local evidence is recorded below.
- **IMPLEMENTED** — Credential safety is enforced by environment-only loading and sanitized logging; external credential rotation remains operator-owned.
- **IMPLEMENTED** — Exness MT5 demo architecture, `BaseBroker`, `SimulatedBroker`/simulated quote source, and explicit unavailable LIVE mode are implemented.
- **IMPLEMENTED** — Demo endpoint allowlist, no-fallback behavior, Compose least-privilege injection, and dashboard loopback binding are implemented.
- **IMPLEMENTED** — Local simulated baseline runs through the production entrypoint without broker credentials.
- **IMPLEMENTED** — Legacy environment schema keys are explicitly modeled with safe compatibility rules; the verified Exness symbol is `XAUUSDm`.
- **EVIDENCE** — `docs/PHASE-0-BASELINE-MANIFEST.md` records Python, dependency/fixture/schema hashes, local `SIMULATED` smoke, secret-artifact checks, image inspection, and command outputs.
- **BLOCKED** — External credential rotation and operator acceptance sign-off remain outside repository control.
- **IMPLEMENTED** — Native Windows MT5 FastAPI/uvicorn HTTPS bridge exists at `src/bridge/mt5_host_bridge.py`; requested RPC aliases, mandatory SL/TP validation, symbol-supported filling-mode selection, retcode mapping, and UNKNOWN/halt errors are covered by focused tests and static checks.
- **EVIDENCE** — After the readiness latency fix, the bridge passed a complete read-only check for `XAUUSDm`: account equity `10000.0`, session open, spread `0.260`, `32` closed candles, writable database, and clock drift below one second.
- **IMPLEMENTED** — Broker backfill now preserves known session/maintenance gaps without synthesizing candles; arbitrary gaps remain a fail-closed condition.
- **BLOCKED** — Four controlled `0.01` LONG demo submissions with attached exits did not produce a protected fill. The first two returned `UNKNOWN` and reconciled to no matching broker order/open position; the third returned `UNKNOWN` with unavailable reconciliation; after credential rotation, the fourth also returned `UNKNOWN`, with no open position and reconciliation still unavailable. Durable local state remains halted; no further blind retry is authorized.
- **BLOCKED** — Protected demo closure and multi-day forward-test evidence remain; operator acceptance sign-off is still an external gate.
- **RESOLVED** — The credential-bearing validation-output issue was fixed with `hide_input_in_errors=True`; the affected credentials were rotated before the latest retry.
- **NEXT CORRECTIVE SLICE** — Instrument `order_check`/`order_send` with sanitized MT5 phase diagnostics; return deterministic precheck failures as `REJECTED`; add typed `ORDER_NOT_FOUND` reconciliation distinct from transport failure; add non-submitting order-preflight tests before any further demo submission.

## Specification backlog after Phase 0

The executable contracts are implemented locally; they remain unverified until their external/demo acceptance gates pass.

- **IMPLEMENTED** — `docs/PHASE-1-BROKER-READINESS-SPEC.md` — readiness checks cover authentication, account, session, quote, metadata, history, database, freshness, and clock drift.
- **IMPLEMENTED** — `docs/PHASE-2-MARKET-DATA-SPEC.md` — broker candle polling, validation, backfill, quote freshness, and no simulated fallback in demo mode.
- **IMPLEMENTED** — `docs/PHASE-3-BROKER-AWARE-RISK-SPEC.md` — tick-value sizing, executable quote checks, spread, stop/freeze, margin, and equity revalidation.
- **IMPLEMENTED** — `docs/PHASE-4-DEMO-EXECUTION-SPEC.md` — demo execution engine uses the single gate, idempotency, protective-exit confirmation, and post-fill reconciliation.
- **IMPLEMENTED** — `docs/PHASE-5-RECONCILIATION-RECOVERY-SPEC.md` — durable order reconciliation, account-position reconstruction, and persistent halt behavior.
- **IMPLEMENTED** — `docs/PHASE-6-OPERATIONAL-HARDENING-SPEC.md` — separate readiness state, sanitized alerts, durable controls, and Compose isolation.
- **BLOCKED** — `docs/PHASE-7-FORWARD-TEST-SPEC.md` — controlled multi-day demo evidence cannot run without approved external account, instrument, schedule, alert route, and thresholds.

## Evidence rule

No phase is `VERIFIED` until its external/demo acceptance evidence is archived; no phase enables live trading.

## Rules

- All trading remains paper/demo-only.
- `SimulatedBroker` must never contact the broker.
- Broker-demo failures halt new entries and never silently fall back to simulation.
- All orders remain behind the standard `BaseBroker` and execution gate.
- Every non-trivial task requires a written contract and acceptance evidence.
- Tests, specifications, implementation, and runtime behavior must remain synchronized.
