# Phase 4 Specification — Exness Demo Execution
## Status

**Verified.** The single execution gate completed a protected `0.01` LONG `XAUUSDm` demo lifecycle with broker retcode `10009`, confirmed SL/TP, reconciliation, safe close, durable `FILLED`/`CLOSED` state, and no residual broker position. Later operational continuity remains governed by Phases 5–7.

## Problem

The local paper-fill lifecycle does not prove safe Exness demo submission, protective-exit attachment, or broker order-state handling.

## Scope

- Route demo orders exclusively through the existing execution gate and `ExnessMT5Broker`.
- Submit a small controlled demo order with a client idempotency key.
- Handle accepted, filled, partially filled, rejected, cancelled, expired, and unknown states.
- Confirm broker-side stop-loss and take-profit before treating a position as protected.
- Record requested versus actual quantity/price and sanitized lifecycle evidence.

## Non-goals

No live endpoint, blind retry, strategy expansion, or bypass/emergency order path.

## Invariants

- No order reaches a broker without validated exits and an approved risk intent.
- Unknown submission state does not increase exposure and requires reconciliation.
- Protective-exit confirmation is mandatory.
- Provider failure halts new entries and never falls back to simulation.

## Dependencies and interfaces

Requires verified Phases 1–3. Consumes approved order intents and emits typed broker order, fill, protection, and halt events to reconciliation and persistence.

## Acceptance evidence

Failure tests cover provider errors, duplicate submission, partial fills, unknown state, missing protection, and rejected orders. An opt-in demo smoke test proves safe configuration, small order lifecycle, protective exits, closure, reconciliation, and secret-free logs.

## Exit criteria

The complete controlled demo order lifecycle is verified through the single execution gate.
## Unresolved decisions

- Exact broker request/response payload mapping for the selected transport.
- Controlled demo instrument, quantity, and operator authorization procedure.
## Executable slice contract — P4-S1 controlled demo order

**Precondition:** Phases 1–3 are verified. This is the first slice permitted to submit a small Exness demo order.

### Ownership and interface

- **Order planning and gate:** `src/execution/payloads.py`, `src/execution/executor.py`, and `src/execution/broker_adapter.py`.
- **Provider adapter:** `src/trading_bot/broker/mt5.py`.
- **Persistence/observability:** `src/persistence/sqlite.py` and `src/observability/logging.py`.
- **Tests:** `tests/integration/test_execution_gate.py`, `tests/integration/test_paper_execution.py`, `tests/unit/test_execution.py`, and new provider-state cases.
- Input is a verified `APPROVED_ORDER_INTENT`; output is a typed order lifecycle event and, only after broker confirmation, a protected position. `SIMULATED` continues through `SimulatedBroker` without provider submission.

### State transitions

`APPROVED_ORDER_INTENT → SUBMISSION_PENDING → ACCEPTED → FILLED`; `ACCEPTED → PARTIALLY_FILLED → FILLED`; provider rejection/cancel/expiry transitions to terminal `REJECTED`, `CANCELLED`, or `EXPIRED`; timeout or ambiguous response transitions to `UNKNOWN` and blocks exposure increases; a fill without confirmed SL and TP transitions to `UNPROTECTED → HALTED`, never `PROTECTED`. Closure follows `PROTECTED → CLOSE_PENDING → CLOSED`.

### Failure and security policy

The gate revalidates mode, account, quote, risk, exits, and broker constraints immediately before submission. Require a client idempotency key and record broker order ID, requested/actual quantity and price, exit confirmation, latency, slippage, and sanitized failure reason. Never blindly retry a non-idempotent submission. Provider errors halt entries and never fall back to simulation.

### Observed provider blocker and corrective contract

Historical controlled Exness attempts reached the bridge order-submit path after readiness passed but returned sanitized `UNKNOWN` or deterministic `REJECTED` outcomes; no blind retry was allowed. After the native MT5 dispatch fix and provider-valid comment length, a later controlled order completed with broker retcode `10009`, confirmed protection, reconciliation, and safe closure. The earlier failure halt was explicitly reset after operator review.

The corrective implementation exposes sanitized `order_check` and `order_send` phase diagnostics, including MT5 retcode/comment, last-error code/comment, filling mode, symbol constraints, and safe request facts. Deterministic `order_check` failures return `REJECTED`; ambiguous `order_send` failures remain `UNKNOWN` and halt entries. The `order.preflight` RPC/REST path runs `order_check` without submission. Focused bridge, adapter, persistence, and execution tests cover these transitions. Evidence now includes an accepted protected preflight and one controlled `0.01` LONG demo order with broker retcode `10009`, confirmed exits, reconciliation, safe closure, and durable local records.

### Acceptance scenarios

1. A deterministic approved intent submits once with both exits and reaches `PROTECTED` only after broker confirmation.
2. Rejected, cancelled, expired, partial, timeout, duplicate, and unknown provider responses produce the defined state and no unsafe position increase.
3. Missing or unconfirmed stop-loss/take-profit prevents `PROTECTED` and activates a halt.
4. Requested versus actual quantity/price and slippage are persisted without secrets.
5. A controlled demo smoke test opens the smallest authorized demo position, confirms both protective exits, reconciles it, and closes it safely.

### Verification

- `python -m pytest tests/integration/test_execution_gate.py tests/integration/test_paper_execution.py tests/unit/test_execution.py`
- Add provider failure, idempotency, partial-fill, unknown-state, and protection-confirmation tests before verification.
- `python -m mypy src`
- `python -m ruff check .`
- `python -m ruff format --check .`
- Opt-in demo-only order lifecycle with explicit operator authorization, `PAPER_TRADING=true`, `LIVE_TRADING=false`, and secret-free logs.

### Exit gate

Mark this slice verified only when the failure matrix and controlled demo lifecycle prove single-gate submission, confirmed protection, safe closure, and complete sanitized records.
