# Paper-Trading Readiness Specification

## Purpose

This document is the canonical checklist for proving that the system can connect to Exness MT5 demo and execute paper trading safely. It supplements `ARCHITECTURE.md`, `ROADMAP.md`, `CONTEXT.md`, and `TODO.md`.

Implementation follows the workflow in `docs/SPEC-DRIVEN-DEVELOPMENT.md`: define the contract and evidence first, implement one vertical slice, verify failure and success paths, then update readiness status. Broker implementations remain behind `BaseBroker`; local testing uses `SimulatedBroker`.

"Flawless" means fail-closed, observable, restart-safe, and reconciled. It does not mean that a provider, network, market, or model cannot fail.

## Current status

The repository currently provides a local simulated quote and paper-fill path. It does not yet prove broker-connected paper trading.

Current host probe:

- `terminal64.exe` is running and the updated bridge authenticates with the rotated `.env` credentials.
- The complete read-only readiness check passed after accounting for sequential bridge-call latency: account equity `10000.0`, `XAUUSDm` session open, spread `0.260`, `32` closed candles, writable database, and clock drift below one second.
- Broker backfill now preserves known session/maintenance gaps without synthesizing candles; unclassified gaps remain fail-closed.
- Four controlled `0.01` LONG demo submissions with attached exits did not produce a protected fill. The first two reconciled to no matching broker order/open position; the third and fourth returned `UNKNOWN`, with reconciliation unavailable. The durable local database remains halted and no further blind retry is authorized.
- The bridge logs the submit request and returns HTTP `503`, but does not yet expose whether `order_check` or `order_send` failed. Protected-exit confirmation, broker-position reconciliation, safe closure, and multi-day forward-test evidence remain unverified.

## Safety prerequisites

- Rotate any credential that has appeared in `.env`, logs, screenshots, commits, or chat.
- Keep `.env` local-only, ignored, and out of Docker image layers and artifacts.
- Use demo/practice accounts only.
- Keep `PAPER_TRADING=true` and `LIVE_TRADING=false`.
- Reject startup for unknown, ambiguous, or incompatible provider/environment combinations.
- Never silently downgrade broker-demo mode to simulated mode.
- Never submit an order without a confirmed hard stop-loss and take-profit.

## Required runtime modes

The runtime must make its source and execution semantics explicit:

| Mode | Market data | Order behavior | Failure behavior |
| --- | --- | --- | --- |
| `SIMULATED` | Deterministic local feed | Local paper fills | Continue only as a simulation |
| `BROKER_DEMO` | Real demo/practice broker | Demo account order lifecycle | Halt new entries; reconcile state |
| `LIVE` | Not available by default | Not available by default | Refuse startup |

Startup and health output must expose the selected mode, provider, data source, account-readiness state, and halt reason without exposing secrets.

Use the selected **Option B native Windows host bridge** behind `BaseBroker`: Docker Desktop containers connect to the operator-run HTTPS JSON-RPC bridge through `EXNESS_BRIDGE_HOST` and `EXNESS_BRIDGE_PORT`. Native MT5 bindings, a Wine terminal sidecar, and the Windows-host bridge are different products and must not be treated as interchangeable. `ExnessMT5Broker` is the explicit demo adapter; `SimulatedBroker` is the local implementation.

An Exness MT5 broker-demo readiness check must prove:


1. TLS/DNS connection to an allowlisted demo endpoint.
2. Authentication with the demo account.
3. Current account equity, balance, margin, and available margin.
4. Instrument availability and trading-session status.
5. Contract size, tick/pip size, tick/pip value, precision, quantity step, and min/max quantity.
6. Broker stop-distance, freeze-level, margin, and direction constraints.
7. Current bid/ask quote with provider timestamp.
8. Historical candle backfill sufficient for strategy warmup.
9. Database writability.
10. Clock drift within the configured limit.

The check must fail closed if any response is missing, malformed, stale, unauthorized, or inconsistent.

## Market-data requirements

- Preserve bid and ask; do not use midpoint-only data for execution decisions.
- Validate finite positive prices and `ask > bid`.
- Reject stale quotes and excessive spreads.
- Validate provider timestamps and local clock drift.
- Backfill candles on startup and resume after restart.
- Persist aggregation state and the last processed provider timestamp.
- Detect gaps, duplicates, out-of-order candles, timeframe mismatches, and symbol mismatches.
- Generate signals from closed candles unless a documented intrabar contract exists.
- Distinguish a forming candle from a completed candle.
- Do not discard valid history merely because a live timestamp is far ahead; rebuild from broker history instead.
- Handle weekends, rollovers, market reopen, daylight-saving transitions, and provider maintenance windows.

## Strategy and sentiment requirements

- Use one authoritative runtime pipeline.
- Define warmup, duplicate-signal, open-position, opposite-signal, and restart behavior.
- If sentiment is required, wire the strict news and sentiment gate into the production tick path.
- Reject missing, stale, neutral, malformed, or unavailable sentiment.
- Apply high-impact news blackout before order approval.
- Bound provider latency, cost, and request frequency.
- The LLM must never submit, cancel, modify, or override an order or risk limit.

## Risk requirements

For every entry, immediately before submission:

- Re-read live account equity and available margin.
- Read current bid/ask and calculate the executable side price.
- Calculate stop-loss and take-profit from current market data.
- Calculate quantity using broker instrument metadata.
- Include spread, slippage, commissions, swap, and conversion effects where applicable.
- Enforce the one-percent maximum account-risk ceiling.
- Enforce daily drawdown, exposure, margin, quantity, precision, and duplicate-position limits.
- Reject non-finite values, invalid direction relationships, non-positive distances, and stale inputs.

Environment-provided equity and stop distance are acceptable for deterministic tests, not as the source of truth for broker-demo execution.

## Order and position lifecycle

The single execution gate must handle:

- Client idempotency key.
- Broker order ID.
- Accepted, filled, partially filled, rejected, cancelled, expired, and unknown states.
- Requested versus actual quantity and price.
- Confirmed attached stop-loss and take-profit.
- Position creation, modification, closure, and external changes.
- Reconciliation after timeout, process failure, and restart.
- No blind retry of non-idempotent submissions.
- No position increase while order or account state is unknown.
- Immediate safe handling of any position without confirmed protection.

The system must define whether it permits one position per instrument, netting, or hedging.

## Persistence and observability

Persist enough sanitized information to reconstruct each decision:

- Decision, candle, quote, and provider timestamps.
- Instrument, bid, ask, spread, and market-data source.
- Indicators, signal rationale, news IDs, and sentiment decision.
- Account equity, margin, stop, target, quantity, and sizing inputs.
- Client and broker order IDs.
- Submission/fill/reconciliation latency.
- Requested versus actual fill price and slippage.
- Rejection, halt, provider error, and recovery reason.
- Runtime/configuration version.

Never persist credentials, authorization headers, signed URLs, or unredacted provider payloads.

Health must distinguish process health from trading readiness. Readiness must report broker authentication, market-data freshness, account freshness, instrument validity, database writability, clock status, and active halt state.

Alert on broker disconnects, stale data, clock drift, authentication failures, reconciliation mismatches, unknown orders, unprotected positions, drawdown halts, database failures, fallback activation, and repeated rejections.

## Verification gates

### Automated checks

Run the repository's Python 3.11 compile, test, type, lint, format, and Compose test checks. Tests must cover malformed responses, provider failures, stale data, spread limits, clock drift, gaps, partial fills, unknown order state, duplicate submission, restart recovery, reconciliation mismatch, database failure, and secret redaction.

### Demo smoke test

A separately controlled demo-only smoke test must prove:

1. Safe configuration loads.
2. Demo broker authentication succeeds.
3. Account and instrument metadata are valid.
4. Quote and historical candles are valid.
5. Strategy and risk approve a deterministic small order.
6. Broker confirms both protective exits.
7. The position is reconciled.
8. The position is closed safely.
9. Database records are complete.
10. Logs contain no secrets.

### Forward test

Run a multi-day demo-only forward test. Review connectivity uptime, data gaps, spread, slippage, latency, rejected orders, protective-exit confirmation, P&L reconciliation, restart recovery, and all halts before changing strategy complexity or risk.

The system is not broker-connected paper-trading ready until every applicable gate passes with recorded evidence.
