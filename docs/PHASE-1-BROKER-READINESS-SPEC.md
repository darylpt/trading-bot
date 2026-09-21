# Phase 1 Specification — Exness MT5 Broker Readiness
## Status

**Verified.** Fresh Exness MT5 demo read-only readiness passed for `XAUUSDm`: terminal connected and authorized, equity `9999.14`, spread `0.260`, `256` candles, writable database, and clock drift `0.001s`. Order lifecycle and reconciliation remain governed by Phases 4–5.

## Problem

The system has a deterministic local simulation but no proven Exness MT5 demo connectivity. Broker-demo startup must distinguish authentication, account, instrument, market-session, database, and clock failures from local simulation.

## Scope

- Select the documented Exness MT5 demo transport behind `BaseBroker`.
- Implement read-only readiness checks in `ExnessMT5Broker`.
- Expose mode, provider, account-readiness state, data source, and halt reason without secrets.
- Preserve `SimulatedBroker` as the isolated `SIMULATED` implementation.

## Non-goals

No order submission, live trading, strategy changes, automatic fallback, or OANDA support.

## Invariants

- `BROKER_DEMO` requires explicit Exness configuration and never uses local data.
- `SIMULATED` never contacts a broker.
- `LIVE` is rejected.
- Missing, stale, malformed, unauthorized, or contradictory responses fail closed.
- All broker access remains behind `BaseBroker`.

## Dependencies and interfaces

Depends on verified Phase 0 configuration, endpoint, credential, and mode policy. Readiness must provide typed account, instrument, session, quote, database, and clock results to later market-data and risk slices.

## Acceptance evidence

A deterministic test matrix rejects incomplete configuration, invalid endpoints, stale or malformed responses, and unavailable providers. A controlled demo check proves allowlisted connection, authentication, account metadata, instrument metadata, quote freshness, database writability, and clock drift without logging secrets.

## Exit criteria

Read-only Exness demo readiness is verified with recorded evidence; no order path is enabled by this contract.
## Deployment decision

The selected Exness MT5 transport is the Option B native Windows host bridge. The bridge contract is HTTPS JSON-RPC with `/health`, account, instrument, session, quote, history, order, reconciliation, and close routes.

## Remaining unresolved decisions

The host bridge reports an authorized demo account and the broker symbol `XAUUSDm` is verified directly through the native binding. Read-only account, metadata, session, quote, candle, database, and clock checks pass; order lifecycle and reconciliation remain out of scope.

## Executable slice contract — P1-S1 read-only broker readiness

**Precondition:** the Phase 0 exit gate is verified. This slice enables broker reads only; it does not enable order submission.

### Ownership and interface

- **Settings and mode:** `src/config/settings.py` and `src/trading_bot/runtime_config.py`.
- **Broker adapter:** `src/execution/protocols.py`, `src/execution/broker_adapter.py`, and `src/trading_bot/broker/mt5.py`.
- **Readiness result:** `src/observability/readiness.py`.
- **Tests:** `tests/unit/test_settings.py`, `tests/unit/test_broker_adapter.py`, and `tests/integration/test_forward_test_readiness.py`.
- `ExnessMT5Broker` receives validated demo configuration and exposes typed connection, account, instrument, session, quote, database, and clock checks through `BaseBroker`; `SimulatedBroker` remains a separate implementation.

### Inputs and outputs

Inputs are explicit `BROKER_DEMO` settings, an allowlisted Exness transport, an injected clock/database probe, and provider responses. Output is a sanitized readiness report containing mode, provider, endpoint identity, account-readiness, data-source, checked timestamp, and halt reason; credentials and raw payloads never leave the adapter boundary.

### State transitions

`CONFIG_INVALID → REJECTED`; `CONFIG_VALID → CONNECTING → AUTHENTICATED → READINESS_CHECKING → READY`; any timeout, authentication failure, malformed/stale response, unavailable instrument/session, database failure, or clock drift transitions to `HALTED`. `SIMULATED → SIMULATED_READY` never invokes the Exness adapter. `LIVE → REJECTED`.

### Failure and security policy

Fail closed on every missing, unauthorized, contradictory, stale, non-finite, or mismatched provider field. Validate the endpoint before credential attachment and require demo environment. Provider failure halts new entries and cannot read local fixtures as fallback. Readiness logging is allowlist-based and redacts account identifiers, tokens, authorization headers, and provider payloads.

### Acceptance scenarios

1. Invalid mode/provider, missing credential, unsafe endpoint, or incomplete account settings rejects startup.
2. A valid fake provider returns all required account, instrument, session, quote, database, and clock fields and yields `READY`.
3. Each provider failure yields `HALTED` with a stable sanitized reason and zero simulated-feed calls.
4. `SIMULATED` remains runnable with a broker spy proving no Exness calls.
5. A controlled demo-only read check proves TLS/DNS, authentication, account equity/margin, instrument constraints, quote freshness, history availability, database writability, and clock drift.

### Verification

- `python -m pytest tests/unit/test_settings.py tests/unit/test_broker_adapter.py tests/integration/test_forward_test_readiness.py`
- `python -m mypy src`
- `python -m ruff check .`
- `python -m ruff format --check .`
- Opt-in demo read-only smoke test with `PAPER_TRADING=true` and `LIVE_TRADING=false`; record redacted readiness output and no-order evidence.

### Exit gate

Mark this slice verified only when deterministic failure/success tests and the controlled read-only demo evidence pass. No Phase 2 implementation starts before that record exists.
