# Paper-Trading Readiness Specification

## Purpose

This document is the canonical checklist for proving that the system can connect to Exness MT5 demo and execute paper trading safely. It supplements `ARCHITECTURE.md`, `ROADMAP.md`, `CONTEXT.md`, and `TODO.md`.

Implementation follows the workflow in `docs/SPEC-DRIVEN-DEVELOPMENT.md`: define the contract and evidence first, implement one vertical slice, verify failure and success paths, then update readiness status. Broker implementations remain behind `BaseBroker`; local testing uses `SimulatedBroker`.

"Flawless" means fail-closed, observable, restart-safe, and reconciled. It does not mean that a provider, network, market, or model cannot fail.

## Current status

Recorded implementation includes a local simulated quote/paper-fill path and an Exness MT5 demo path. The controlled one-order `XAUUSDm` lifecycle is historical Phase 4/5 evidence, not multi-day readiness. Phase 7's active forward-test target is `EURUSDm`; it requires fresh instrument-specific readiness and lifecycle evidence. Historical broker connectivity or successful startup is not evidence of current runtime readiness.

Phase 7 remains in progress and unverified. On 2026-09-25, the operator-approved schedule changed to any weekday UTC time only while the broker reports a fresh open session; weekends, broker-closed sessions, and stale/unavailable session state are rejected. A supervised launcher and follow-up preflight passed on that date, with `NO_APPROVED_SIGNAL` and no trade evidenced. The latest dated recorded preflight, 2026-09-26, was **NO-GO** due to weekend schedule, bridge health, durable `bridge_health:BROKER_CONNECTION_ERROR` halt, circuit revalidation, and broker position/history reconciliation. No live/demo verification is being performed here; these dated records do not describe current runtime state.

Historical XAUUSDm host probe (retained for audit):
- At the time of the recorded probe, `terminal64.exe` was running and the bridge authenticated with then-current credentials; this is not a current runtime claim.
- The recorded read-only readiness check passed after accounting for sequential bridge-call latency: account equity `10000.0`, `XAUUSDm` session open, spread `0.260`, `32` closed candles, writable database, and clock drift below one second.
- Broker backfill preserved known session/maintenance gaps without synthesizing candles; unclassified gaps remained fail-closed.
- A non-submitting protected-order preflight returned `ACCEPTED` with `protection_confirmed=true`; the bridge did not call `order_send`.
- After restarting the bridge with direct native MT5 dispatch for `order_check`/`order_send` and shortening the MT5 comment to the provider's accepted length, one controlled `0.01` LONG `XAUUSDm` demo order returned broker retcode `10009`, confirmed attached SL/TP, reconciled to a broker position, closed safely, and left no matching open position. Durable local state recorded `FILLED` and `CLOSED`; the sanitized smoke result is retained in the session evidence.
- The prior failed controlled attempt left a local runtime halt; it was reset after operator review for Phase 1 forward-test preparation. Halt state remains fail-closed and must not be cleared as a smoke-test side effect.
- The bridge exposed sanitized `order_check`/`order_send` phase diagnostics, a non-submitting protected-order preflight, and timestamped authoritative `ORDER_NOT_FOUND` versus transport `UNKNOWN`. Multi-day forward-test evidence and operator acceptance remain unverified.
- A later historical post-exit probe recorded Windows NTP near-synchronized, while XAUUSDm quote data was approximately `20,382s` old. Startup rejected with `StaleMarketDataError`, persisted a halt, and submitted no order. The Phase 2 clock incident was subsequently followed by synchronized clock evidence recorded in `TODO.md`; clock drift is not asserted as a current issue.

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

The host bridge requires `Authorization: Bearer <BROKER_TOKEN>` for account,
health, order, reconciliation, close, cancel, and JSON-RPC operations.
`BROKER_TOKEN` is a separate random bridge credential and must not reuse
`EXNESS_PASSWORD`. Read-only market-data endpoints are unauthenticated but
cannot perform order actions. The broker client verifies the bridge
certificate supplied through `EXNESS_BRIDGE_CA`; TLS verification is mandatory.

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
- Reuse a sentiment result only for identical instrument, direction, reference price, rationale, and complete news payload, and only while younger than `SENTIMENT_INTERVAL_SECONDS`. The per-tick evaluation timestamp is not part of the qualitative request. Changed context during the interval rejects without returning a prior result; expiry requires a fresh analysis.
- Production Ollama inference has a bounded 30-second deadline. Timeout, malformed output, or missing sentiment remains rejected. `/api/tags` model availability is not proof of inference readiness; run a strict-schema local inference smoke before supervised startup.
- The LLM must never submit, cancel, modify, or override an order or risk limit.
- A `HOLD` is a strategy outcome, not a sentiment decision: do not call the sentiment provider or emit sentiment approval/rejection records for it, and do not evaluate it for entry risk.
- Provider failures and malformed responses remain fail-closed. Logs may record the failure stage, exception class, and allowlisted schema error types/field names, but must not include exception messages, raw model output, prompts, or provider payloads.
- `docker compose exec -T app python -m tools.evaluate_sentiment` runs the broker-isolated diagnostic. By default it evaluates 90 bundled EURUSD cases; repeat `--model <ollama-name>` to compare local models sequentially and add `--summary-only` to omit per-case metrics. The technical context uses `EURUSDm`, `EUR/USD`, and the same fixed `BUY` direction for every label. The CLI requires effective `PAPER_TRADING=true` and `LIVE_TRADING=false`; it calls only the sentiment client and never constructs a broker, evaluates risk, submits orders, or writes runtime persistence. After three provider timeouts per model, not necessarily consecutive, it marks remaining cases `not_run`, fails screening, and skips all later models. Run only when inference cannot interfere with the order-capable service and no unapproved external-provider cost will be incurred.
- Run one candidate per invocation rather than batching models; exceeding the timeout budget aborts later cases, and shared Ollama resources can delay runtime inference. Example syntax: `docker compose exec -T app python -m tools.evaluate_sentiment --model <ollama-name> --summary-only`. Check `ollama list` first and preserve the line above's non-interference requirement.
- Reports include valid-score coverage, strict response status, threshold-aligned confusion matrix, per-class precision/recall/F1, and macro-F1. Offline screening floors, chosen before comparison: coverage ≥80%, macro-F1 ≥0.70, and F1 ≥0.70 for every class. Abstentions and invalid responses are misses for class metrics. These floors are diagnostic only; they do not authorize a production gate change.
- The default case file is a deterministic 90-example subset of the manually annotated *Forex News Annotated Dataset for Sentiment Analysis* (Zenodo DOI [10.5281/zenodo.7976208](https://doi.org/10.5281/zenodo.7976208), CC BY 4.0; Fatouros et al., 2023). The source corpus has 758 EURUSD entries from Forex Live and FX Street, collected January–May 2023 and labeled for near-term impact on the associated pair. The subset has 30 positive, 30 negative, and 30 neutral labels; within each label/source stratum, cases are ordered by ascending SHA-256 of UTF-8 `EURUSD`, label, source name, and headline joined with NUL separators, then selected to approximate source proportions: positive 3/27, negative 2/28, neutral 2/28 (Forex Live/FX Street). It is historical and does not establish current performance or profitability.
- The nine-case stock/equity Financial PhraseBank subset remains available with `--cases src/sentiment/financial_phrasebank_eval.jsonl`; it is not evidence of EURUSD accuracy. Attribution: Malo et al., “Good debt or bad debt: Detecting semantic orientations in economic texts,” arXiv:1307.5336 (2013), https://huggingface.co/datasets/takala/financial_phrasebank. License: [CC BY-NC-SA 3.0](https://creativecommons.org/licenses/by-nc-sa/3.0/).
- The corrected 2026-10-02 Financial PhraseBank pilot (`llama3.2:3b`) produced 2/9 label matches, 2/9 score coverage, and seven valid abstentions. Withdraw the originally reported 3/9: expected labels had leaked through the varying technical-signal direction; the evaluator now fixes that context across labels.
- The EURUSD benchmark run on 2026-10-02 produced these no-order results: configured `llama3.2:3b` returned 90 valid responses, 45 valid abstentions, accuracy 29/90, score coverage 0.50, macro-F1 0.4321, and positive/negative/neutral F1 0.6383/0.4878/0.1702. `llama3.1:latest` timed out on all 90 cases with the original evaluator; its response coverage and class metrics were zero. Both fail all screening floors. The latter run took about 45 minutes and motivated a timeout limit.
- A later `qwen3:8b` attempt produced six observed timeout warnings but no report before the 1200-second command limit; it is incomplete and excluded from comparison metrics. This attempt shared the Ollama host with the active order-capable service, contrary to the non-interference requirement; no further model attempts were made. The app remained healthy and local open-position count was zero before and after, but execution-log count advanced from 13,883 to 13,959 during the run and `qwen3:8b` remained resident at 100% GPU afterward, so inference interference cannot be ruled out.

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

### Decision liveness and broker history

- Persist a unique `decision_id` across each tick's strategy, sentiment, risk, and execution events. `STRATEGY_NO_SIGNAL` and `EXECUTION_SKIPPED` are normal funnel outcomes, not execution rejections; genuine `*_REJECTED` events retain their error class.
- Persist `RUNTIME_HEARTBEAT` only after a completed tick. The dashboard presents process liveness separately from the latest broker-readiness snapshot and reports the failed readiness stage with a bounded reason code.
- The bridge exposes authenticated GET-only order/deal history for bounded UTC windows. Normalize and strictly validate rows, redact unsafe provider comments, persist snapshots idempotently, and reconcile recent local demo order identities before a supervised start. The history path must never call order submission, modification, cancellation, or close operations.
- Dashboard funnel counts are grouped by instrument and strategy for the last hour and day; HOLD/no-signal, stage blocks, risk approval/rejection, execution skips/rejections, fills, and closes remain distinct.


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
8. The position is closed through the execution gate after matching its exact broker identity, and a fresh account snapshot confirms removal.
9. Database records are complete.
10. Logs contain no secrets.

### Forward test

Run a multi-day demo-only forward test. Review connectivity uptime, data gaps, spread, slippage, latency, rejected orders, protective-exit confirmation, P&L reconciliation, restart recovery, and all halts before changing strategy complexity or risk.

The system is not broker-connected paper-trading ready until every applicable gate passes with recorded evidence.

## Executable Compose preflight and launch

Status: Implemented and exercised in recorded historical runs. The 2026-09-25 02:12 UTC supervised launcher passed the weekday broker-session preflight, recreated `app`, and Compose reported it healthy. A follow-up preflight passed with zero unresolved orders and matching broker/local positions. These results do not complete the multi-day forward-test gate or establish current runtime readiness; the latest dated recorded preflight is the 2026-09-26 NO-GO documented above.

### Problem

The operational dependencies span a Windows MT5 bridge, a Compose-managed application and news worker, the durable SQLite volume, and a host Ollama endpoint. Testing these separately allowed configuration and connectivity faults to recur at launch.

### Contract

- `python -m tools.paper_preflight` loads the effective app-container settings and performs read-only checks for paper/demo flags, active EURUSDm forward-test configuration, authenticated bridge health, broker account/instrument/session/quote/candle readiness, authenticated bounded order/deal history and recent local lifecycle reconciliation, configured maximum spread, fresh relevant news and high-impact blackout, exact local Ollama endpoint/model availability, SQLite state readability, zero unresolved orders, and exact broker/local open-position reconciliation.
- The report includes separate `ENTRY SCHEDULE`, persistent-halt, and circuit-breaker gates. Entries may be considered at any UTC time Monday–Friday only when the current EURUSDm broker session reports open with a fresh timestamp; weekends are blocked. The runtime rechecks the broker session every tick. An active operator halt, mismatch, unresolved order, stale/missing dependency, invalid mode, closed broker session, weekend, excessive spread, or news blackout makes launch `NO-GO`. A persisted circuit-breaker halt is accepted only after fresh broker readiness and spread checks; the preflight does not change its state. The command never clears an operator halt, reconciles by writing, submits an order, or starts Compose services.
- `tools/start_paper_runtime.ps1` rebuilds and runs that exact one-off Compose preflight first. It invokes `docker compose up -d app` only on exit code zero; every failed gate leaves the application service untouched. Runtime startup independently repeats its broker checks and risk/execution controls.
- Operator commands: `docker compose run --rm --build --no-deps app python -m tools.paper_preflight` (inspect only); `.\tools\start_paper_runtime.ps1` (preflight, then start only on GO).
- The dashboard does not depend on the trading app service; opening a read-only dashboard cannot start the bot.
- The read-only dashboard separates completed-tick liveness from broker readiness, displays sanitized readiness stage/reason diagnostics, and reports correlated strategy-to-execution counts for 1-hour and 24-hour windows. It reads persisted history and never receives the broker token.
- Diagnostics are sanitized: no credential, authorization header, raw provider payload, or account balance is printed.

### Acceptance evidence

1. Deterministic all-clear fixtures produce a complete GO report without changing SQLite state and include a weekday plus fresh, open broker-session evidence.
2. Safety-boundary tests cover paper/live flags, weekday versus weekend schedule, fresh/open versus closed broker session, stale session data, news freshness/blackout, sentiment model availability, SQLite state, halt, circuit breaker, spread, and reconciliation.
3. The 2026-09-24 16:53 UTC Compose one-off returned NO-GO for the closed window and active `BrokerConnectionError` halt; the SQLite hash and runtime controls remained unchanged, and `app` remained stopped.
4. After bridge recovery and reviewed halt recovery on 2026-09-25, the launcher passed every readiness gate but returned NO-GO for the closed entry window. The SQLite hash, halt, circuit state, unresolved-order count, and open-position count remained unchanged; `app` remained stopped.
5. Compose configuration confirms dashboard startup has no dependency on `app`. The original Asian-session GO/start path passed, and the updated weekday broker-session schedule also passed the supervised GO/start path on 2026-09-25; the multi-day evidence gate remains open.
6. At `2026-09-25T02:12:42Z`, a read-only preflight under the updated schedule passed all gates, reported zero unresolved orders, and confirmed broker/local position reconciliation. The runtime session-gate integration tests cover any weekday hour, closed broker sessions, and stale session timestamps.
7. Host tests passed `194 passed, 2 skipped`; the authoritative Compose suite passed `197 passed, 1 skipped`; `mypy src`, Ruff lint, compileall, and Compose configuration passed. An isolated Docker dashboard smoke rendered separate `Liveness`/`Broker readiness` snapshots and synthetic 1-hour/24-hour funnel rows. A prior serial broker readiness probe passed after an earlier preflight NO-GO, so that transient failure's cause remains unconfirmed. No broker order was submitted; the live authenticated history endpoint was not exercised, and no bridge/app service was restarted.
8. Latest 2026-09-28 evidence: the supervised launcher passed at `2026-09-28T11:37:58Z`; post-start and latest read-only preflights passed at `11:38:41Z` and `11:44:19Z`. The latest preflight at `12:29:13Z` also passed with fresh EURUSDm news, zero unresolved orders, zero broker orders/deals, and broker/local positions matched at zero. The 2026-09-28 recheck later returned a transient freshness `NO-GO`; follow-up read-only timestamps showed fresh broker account/metadata/session/quote data and a 15-minute candle age of about 995 seconds, after which preflight passed at `13:16:34Z`, again with 78 relevant news events, zero unresolved orders, zero broker orders/deals, and matched zero positions. The live dashboard showed `ema_crossover` `HOLD` (fast EMA `1.1369` below slow EMA `1.1373`), `FLAT`, and zero closed trades. No order or strategy change was made. A broker-confirmed lifecycle and multi-day acceptance remain outstanding.
9. After the user's 2026-09-28 selection of the existing `breakout_channel` strategy, the active runtime configuration was verified as `breakout_channel`; the healthy app generated `SHORT` lower-channel breakout signals. Read-only preflight at `14:59:14Z` passed all gates, with zero unresolved orders, zero broker orders/deals, and matched zero broker/local positions. The strict Ollama sentiment response failed schema validation (extra field(s), missing `reasoning`), so the strategy was rejected as `SENTIMENT_UNAVAILABLE`; subsequent changed-context decisions were rejected by the cooldown. No order or position increase occurred. No safety gate was relaxed; Phase 7 lifecycle and multi-day acceptance remain outstanding.
