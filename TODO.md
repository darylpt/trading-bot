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

## Current phase — Phase 7: Controlled Demo Forward Test

- **VERIFIED** — Phase 0 secure baseline; evidence is recorded in `docs/PHASE-0-BASELINE-MANIFEST.md`.
- **VERIFIED** — Phase 1 broker-demo read-only readiness; fresh bridge evidence is recorded below.
- **VERIFIED** — Phase 2 market-data continuity and broker backfill; deterministic continuity tests and controlled broker quote/candle polling evidence are recorded. A later provider freshness incident remains safely halted under Phase 7.
- **VERIFIED** — Phase 3 broker-aware risk; deterministic guardrail tests, live Exness metadata, one-percent risk smoke, and protected demo lifecycle evidence are recorded.
- **VERIFIED** — Phase 4 protected demo lifecycle; the controlled XAUUSDm fill/reconcile/close evidence is recorded.
- **VERIFIED** — Phase 5 reconciliation/recovery; deterministic failure injection, controlled daemon restart/recovery, and zero residual positions are recorded.
- **VERIFIED** — Phase 6 operational hardening; sanitized structured alerts, durable controls, forward-test window enforcement, and provider-failure recovery are recorded.
- **IMPLEMENTED** — Hybrid runtime cutover: production ticks now load typed fresh news, call the configured strict-JSON sentiment provider on a bounded cadence, apply directional thresholds and risk modifiers, and block entries on unavailable sentiment before the existing risk/execution gates. Evidence: `tests/integration/test_hybrid_runtime.py`, full host suite `155 passed, 1 skipped`, Ruff, format, and mypy.
- **IN_PROGRESS** — Phase 7 paper-only runtime remains configured for XAUUSDm, the 13:00–16:00 UTC entry window, and structured alerts; the daemon is halted because the post-resync broker feed is stale.
- **BLOCKED** — Phase 7 cannot be marked verified until the multi-day evidence package, restart/reconciliation review, and final demo-only go/no-go decision are recorded.

- **EVIDENCE** — Windows time synchronization now passes: `Leap Indicator: 0`, `Source: pool.ntp.org,0x9`, and stripchart offset approximately `+0.56s`.
- **EVIDENCE** — Restarted bridge health passed, but XAUUSDm quote age was approximately `47,300s` and latest candle age approximately `48,977s`; startup persisted `StaleMarketDataError`, retained zero open positions, and submitted no order.
- **EVIDENCE** — Latest offline checks: host `pytest tests/unit tests/integration` `139 passed, 1 skipped`; Docker Compose test `138 passed, 1 skipped`; Ruff lint passed; Ruff format check passed; mypy passed; NTP stripchart offset approximately `+0.36s`.
- **EVIDENCE** — Local setup probe: Ollama `0.34.2` is installed; `llama3.2:3b` is GPU-loaded and the typed adapter returned strict sentiment JSON in `0.91s`. The paper/demo startup probe failed closed because the configured MT5 bridge at `127.0.0.1:18812` refused the connection; no order was submitted.
- **EVIDENCE** — Current supervised bridge start succeeded: MT5 initialized against `Exness-MT5Trial17`, HTTPS bridge ready on port `18812`, and broker health reported connected/authorized. The immediate paper readiness probe then failed closed on stale `XAUUSDm` quote data; current UTC date is Saturday.
- **IMPLEMENTED** — Forex Factory calendar normalization now accepts the approved XML/JSON formats, filters USD/All events for `XAUUSDm`, rejects unknown timestamps/impact values, and atomically refreshes `data/news_events.json` through `tools.refresh_news_events`. Contract tests pass; the public endpoint currently returns HTTP `429`, so continuous refresh is not verified.
- **BLOCKED** — The five-day paper run remains stopped until the broker session reopens with fresh quote/candle data and the news provider permits a successful refresh.
- **IMPLEMENTED** — Dashboard now exposes effective paper readiness, MT5 bridge health, broker halt state, Ollama model/API state, typed news snapshot freshness, database event age, and provider diagnostics. Browser smoke on the rebuilt Docker image showed `Bridge READY`, `Ollama READY`, `News feed READY`, and `Broker gate HALTED` with `StaleMarketDataError`; host verification passed `157 tests, 1 skipped`, mypy, Ruff, format, and compile checks.
- **IMPLEMENTED** — LLM sentiment audit is now durable in `llm_decisions`, recording provider/model, technical signal, derived decision, score, confidence, risk modifier, gate reason, bounded reasoning, and news-event count without raw prompts or payloads. The dashboard renders the latest gate result and recent history. Hybrid runtime tests passed `2`, persistence/observability focus passed `11`, full suite passed `157 tests, 1 skipped`, and browser smoke displayed a populated Ollama `REJECT` decision with score `0.2`, confidence `0.4`, risk modifier `0.5`, and `SENTIMENT_BELOW_BUY_THRESHOLD`.
- **IMPLEMENTED** — Added the EODHD historical calendar contract, allowlisted HTTPS client, USD/All filtering for `XAUUSDm`, strict timestamp/impact validation, content-addressed immutable archives, and `tools.import_historical_calendar`. Fixture tests pass; live import is intentionally blocked until a licensed EODHD API key is supplied through `HISTORICAL_CALENDAR_API_KEY` (the no-key CLI smoke rejected closed).

## Specification backlog after Phase 0

Phase contracts retain their own evidence gates; current status is explicit below.

- **VERIFIED** — `docs/PHASE-1-BROKER-READINESS-SPEC.md` — fresh Exness MT5 read-only readiness passed for `XAUUSDm`.
- **VERIFIED** — `docs/PHASE-2-MARKET-DATA-SPEC.md` — broker candle polling, validation, backfill, quote freshness, no simulated fallback in demo mode, controlled live polling, and safe stale-data halt are recorded.
- **VERIFIED** — `docs/PHASE-3-BROKER-AWARE-RISK-SPEC.md` — broker metadata sizing, tick-value conversion, executable risk limits, revalidation, protection requirements, and no-bypass execution evidence are recorded.
- **VERIFIED** — `docs/PHASE-4-DEMO-EXECUTION-SPEC.md` — protected demo fill, exit confirmation, reconciliation, safe close, and durable `FILLED`/`CLOSED` state are recorded.
- **VERIFIED** — `docs/PHASE-5-RECONCILIATION-RECOVERY-SPEC.md` — durable order reconciliation, account-position reconstruction, persistent halt behavior, controlled restart/recovery, and no duplicate exposure are recorded.
- **VERIFIED** — `docs/PHASE-6-OPERATIONAL-HARDENING-SPEC.md` — readiness, sanitized structured alerts, optional HTTPS webhook delivery, durable controls, Compose isolation, window enforcement, and failure/recovery evidence are recorded.
- **EVIDENCE** — Framework and runtime verification: host `pytest` `144 passed, 1 skipped`; Docker Compose test `138 passed, 1 skipped`; compile, mypy, Ruff check, and Ruff format checks pass.
- **EVIDENCE** — Fresh broker-demo readiness and recovery: bridge authorized, equity `9999.14`, XAUUSDm spread `0.260`, `256` candles, clock drift `0.001s`, writable database, and `0` broker open positions; a transient provider halt was persisted, then cleared only after a fresh readiness pass and operator review.
- **BLOCKED** — The host clock issue is resolved; the remaining blocker is stale broker market data after the supervised process exit. Keep the daemon stopped and halt active until fresh quote/candle readiness passes.
- **IN_PROGRESS** — `docs/PHASE-7-FORWARD-TEST-SPEC.md` — multi-day evidence collection is active; completion still requires the full run, restart/reconciliation evidence, incident dispositions, and final review.

## Evidence rule

No phase is `VERIFIED` until its external/demo acceptance evidence is archived; no phase enables live trading.

## Rules

- All trading remains paper/demo-only.
- `SimulatedBroker` must never contact the broker.
- Broker-demo failures halt new entries and never silently fall back to simulation.
- All orders remain behind the standard `BaseBroker` and execution gate.
- Every non-trivial task requires a written contract and acceptance evidence.
- Tests, specifications, implementation, and runtime behavior must remain synchronized.
