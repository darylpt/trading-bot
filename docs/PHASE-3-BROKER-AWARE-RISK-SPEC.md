# Phase 3 Specification — Broker-Aware Risk
## Status

**Implemented locally.** Broker-aware sizing and pre-submit revalidation code exist; broker contract evidence remains required for verification.

## Problem

Local risk calculations do not establish executable quantity, margin, precision, stop-distance, or contract constraints for an Exness instrument.

## Scope

- Re-read account equity and available margin immediately before entry approval.
- Use current executable bid/ask and validated instrument metadata.
- Calculate quantity, stop-loss, and take-profit with contract size, tick/pip value, precision, steps, and broker limits.
- Enforce the one-percent account-risk ceiling, drawdown, exposure, margin, spread, and duplicate-position limits.

## Non-goals

No broker order submission, live trading, strategy changes, or risk-limit increases.

## Invariants

- No order intent lacks a hard stop-loss and take-profit.
- Invalid direction relationships, non-finite values, stale inputs, non-positive distances, and excess risk reject.
- Environment-provided equity is test-only, never broker-demo truth.
- Risk is rechecked immediately before submission.

## Dependencies and interfaces

Requires verified Phase 1 account/instrument readiness and Phase 2 quote continuity. Produces a typed approved order intent or a recorded rejection for the single execution gate.

## Acceptance evidence

Deterministic tests cover long/short sizing, precision and min/max quantity, stop/freeze levels, spread/slippage/margin effects, drawdown, duplicate exposure, stale inputs, and one-percent boundary behavior.

## Exit criteria

Broker-aware risk approval is verified and rejects unsafe or unverifiable order intents without bypass paths.
## Unresolved decisions

- Exact Exness instrument metadata mapping and conversion rules.
- Whether the account permits netting or hedging, subject to broker evidence.
## Executable slice contract — P3-S1 broker-aware risk approval

**Precondition:** Phases 1 and 2 are verified and provide fresh account, instrument, quote, and continuity data.

### Ownership and interface

- **Risk calculation:** `src/risk/sizing.py`, `src/risk/limits.py`, and `src/risk/drawdown.py`.
- **Typed boundary:** `src/domain/models.py` and `src/execution/payloads.py`.
- **Final gate integration:** `src/execution/executor.py` and `src/execution/broker_adapter.py`.
- **Tests:** `tests/unit/test_trading_risk_guardrails.py`, `tests/unit/test_risk.py`, `tests/integration/test_execution_gate.py`, and new broker-metadata risk cases.
- Input is a technical signal plus fresh broker snapshot, executable quote, instrument metadata, open exposure, and configured limits. Output is exactly one typed `APPROVED_ORDER_INTENT` or a sanitized `REJECTED` decision.

### State transitions

`SIGNAL_RECEIVED → RISK_INPUT_VALIDATING → APPROVED_ORDER_INTENT`; invalid/stale/missing input transitions to `REJECTED`; approved intent transitions to `REVALIDATION_REQUIRED` immediately before submission; changed account, quote, metadata, spread, margin, or exposure transitions to `REJECTED`. Risk approval never submits an order.

### Calculation and failure policy

Use current account equity and the executable side price. Calculate quantity from stop distance and instrument contract/tick metadata, round only to the broker quantity step, and enforce min/max quantity, precision, stop/freeze distance, margin, spread, drawdown, exposure, and duplicate-position constraints. The hard ceiling is 1% of account equity; non-finite values, invalid LONG/SHORT exit relationships, non-positive distances, missing exits, stale timestamps, and unavailable metadata reject. Environment equity is test-only.

### Acceptance scenarios

1. Valid LONG and SHORT inputs produce direction-correct exits and quantity within broker constraints.
2. Exact one-percent risk is accepted only at the configured boundary; any amount above it rejects.
3. Missing/stale account or quote, invalid metadata, excessive spread, insufficient margin, drawdown, duplicate exposure, invalid stop/freeze distance, and rounding below minimum all reject with a reason.
4. A final re-read detects changed equity/quote/exposure and invalidates the earlier intent.
5. No rejected or unapproved intent reaches a broker spy or alternate execution path.

### Verification

- `python -m pytest tests/unit/test_trading_risk_guardrails.py tests/unit/test_risk.py tests/integration/test_execution_gate.py`
- Add focused LONG/SHORT broker metadata, one-percent-boundary, revalidation, margin, and stale-input tests before verification.
- `python -m mypy src`
- `python -m ruff check .`
- `python -m ruff format --check .`
- Deterministic risk smoke scenario proves one approved intent and one rejection; no broker submission occurs.

### Exit gate

Mark this slice verified only when all unsafe-state tests reject, the approved intent carries hard stop-loss and take-profit, and final revalidation is demonstrated. Phase 4 may consume only the approved intent type.
