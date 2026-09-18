# Product Specification: Hybrid Algorithmic & AI Forex Trading System

> **Status and authority:** This product specification is retained as historical context. Current delivery status and broker-demo acceptance are governed by `ROADMAP.md`, `docs/PHASE-0-SECURE-BASELINE-SPEC.md`, `docs/PAPER-TRADING-READINESS.md`, and `TODO.md`. The primary broker target is Exness MT5 behind `BaseBroker`; local testing uses `SimulatedBroker`. OANDA was evaluated and discarded; do not add OANDA implementation work. Local simulation is not broker connectivity.

## 1. Product definition

Build a risk-managed, educational, semi-automated Forex trading system that combines:

1. A deterministic technical engine for OHLC data, indicators, strategy signals, and backtesting.
2. An LLM qualitative analyst for financial-news parsing and macroeconomic sentiment scoring.
3. A risk circuit breaker that sizes positions dynamically and rejects unsafe orders.
4. A broker execution boundary that initially targets paper/demo accounts only.
5. Containerized background execution with durable trade, sentiment, news, and execution records.

The system is an R&D laboratory for quantitative finance and market mechanics. It is not investment advice, does not promise profitability, and must never place live-money trades during the initial system phases.

## 2. Product architecture and delivery phases

```text
Financial news feeds -> LLM Sentiment Filter --┐
                                               ├-> Risk Management -> Execution
Market OHLC feeds -> Technical Engine --------┘
```

| Phase | Outcome | In scope |
| --- | --- | --- |
| Phase 0 | Secure and freeze the baseline | Explicit runtime modes, credential hygiene, documentation reconciliation, reproducible local simulation, and recorded evidence. |
| Phase 1 | Backtesting foundation | Historical 15-minute EUR/USD data and deterministic RSI + moving-average-crossover strategy. |
| Phase 2 | Custom risk engine | Dynamic lot sizing and ATR-based trailing stop-loss behavior. |
| Phase 3 | LLM sentiment integration | Financial-news ingestion and strict JSON sentiment classification. |
| Phase 4 | Broker-connected demo pipeline | Exness MT5 demo connectivity, broker-aware risk, protected demo orders, reconciliation, and operational evidence. |
The LLM is an analyst and filter. It never submits, modifies, or cancels an order. The technical engine emits signals; risk management produces approved order intents; only execution submits provider orders.

## 3. Functional requirements

### 3.1 Technical Engine

#### TE-1: Data acquisition and normalization

- Load historical 15-minute EUR/USD candles from CSV for Phase 1.
- Normalize OHLC records into typed models and preserve chronological ordering.
- Support Exness MT5 demo market data in the first broker-demo phase. OANDA is discarded; no OANDA implementation is in scope.
- Track data freshness and block new entries after a WebSocket drop, market-data gap, or stale price condition.

#### TE-2: Indicators and strategy

- Calculate RSI, moving averages, and ATR using `pandas`, `numpy`, and `pandas-ta` or an equivalent Backtrader/Freqtrade implementation.
- Phase 1 implements the RSI + moving-average-crossover strategy.
- ATR-based trailing stops are a Phase 2 capability.
- Support both `LONG` and `SHORT` signals.
- Emit typed `TechnicalSignal` objects, never provider-specific broker payloads.
- A technical signal must identify instrument, direction, signal timestamp, reference price, and indicator inputs.

#### TE-3: Deterministic backtesting

- Identical candles, configuration, and friction assumptions must produce the same signal sequence.
- Backtests must model dynamic bid-ask spread, slippage, and overnight swap rates.
- Backtests must account for weekend roll-overs and spread expansion around market open/close.
- Technical calculations must not call a broker or LLM.

#### TE-4: Strategy trigger behavior

- A bullish RSI/MA setup emits a `LONG` signal when the configured candle evaluation completes.
- A bearish RSI/MA setup emits a `SHORT` signal when the configured candle evaluation completes.
- Signal generation is necessary but not sufficient for execution; sentiment, news, drawdown, risk, exit, freshness, paper-mode, and broker gates still apply.

### 3.2 LLM Sentiment Filter

#### SF-1: News ingestion

- Consume financial-news headlines from a configured source such as a News API or Forex Factory.
- Preserve source, headline, publication time, and available currency/instrument context.
- Preserve provider-supplied impact classification when present.
- Deduplicate events and track freshness.
- Run analysis on 15-minute/1-hour contexts or scheduled hourly polls; never issue per-tick LLM calls.

#### SF-2: Forced JSON contract

- Every supported LLM call uses forced JSON mode:
  `response_format={"type": "json_object"}`.
- The strategy receives only a validated numeric score, never free-form model text.
- Sentiment is constrained to `-1.0..+1.0`.
- Malformed JSON, missing fields, unknown fields, non-finite values, non-numeric scores, and out-of-range scores are rejected.
- OpenAI `gpt-4o-mini` and Ollama are accessed through the same provider-neutral interface.

#### SF-3: Confirmation and rejection behavior

- A technical BUY may proceed only when validated sentiment is greater than `+0.50` and every other gate passes.
- Sentiment `<= +0.50`, missing sentiment, stale sentiment, provider failure, or schema failure rejects or holds the BUY.
- Positive sentiment confirms eligibility; it never overrides risk, exits, drawdown halts, news blackouts, stale data, or broker constraints.
- A high-impact news event inside its configured blackout interval rejects or holds new entries regardless of positive sentiment.
- The LLM never submits, modifies, or cancels an order.

### 3.3 Risk Management

#### RM-1: Per-trade risk ceiling

- No trade may risk more than 1.0% of total account balance.
- Calculate risk from account equity and actual entry-to-stop distance before submission.
- Apply instrument contract size, pip value, and broker min/max constraints where required.
- Re-check account balance, calculated size, exits, spread, and broker constraints immediately before submission.

#### RM-2: Dynamic position sizing

Calculate position size for every entry signal:

$$
\text{Position Size} = \frac{\text{Account Equity} \times 0.01}{|\text{Entry Price} - \text{Stop Loss Price}|}
$$

A zero or invalid stop distance is rejected. No static lot size may bypass the calculation.

#### RM-3: Mandatory exits

- Every broker order must include an attached hard stop-loss and take-profit before submission.
- Reject missing exits, direction-inconsistent exits, and non-positive distances.
- For `LONG`, stop-loss is below entry and take-profit is above entry.
- For `SHORT`, stop-loss is above entry and take-profit is below entry.
- ATR trailing behavior may adjust a stop only after the hard stop exists; it may not remove the hard stop invariant.

#### RM-4: Daily drawdown halt

- Track realized and unrealized P&L against the configured daily drawdown baseline.
- `DAILY_DRAWDOWN_LIMIT` is required configuration; no unsafe hardcoded default is allowed.
- When cumulative daily drawdown reaches or exceeds the configured limit, halt all new entries.
- A drawdown halt cannot be overridden by technical signals, positive sentiment, retries, or manual fallback defaults.
- Existing protective exits remain authoritative; the halt must not remove stop-losses or create a new position.
- Reset only at the explicitly configured trading-day boundary after a fresh account snapshot is available.
- The halt reason, baseline, observed drawdown, and reset state must be observable.

### 3.4 Execution Module

#### EX-1: Single execution boundary

- Only the execution wrapper may submit broker orders.
- Supported broker implementation is Exness MT5 behind `BaseBroker`; local paper testing uses `SimulatedBroker`.
- The initial target is the Exness MT5 demo account, including the verified micro-lot instrument `XAUUSDm`.
- Strategies, indicators, sentiment clients, notebooks, and data feeds must not call broker APIs directly.

#### EX-2: Provider payloads

- Map typed order intents to the `ExnessMT5Broker` payload while preserving direction, quantity, entry, hard stop-loss, take-profit, account equity, risk fraction, and client order ID.
- Reject the order if the provider payload cannot carry the mandatory exits or if the environment is not paper/demo.

#### EX-3: Session and market safety

- Support 24/5 Forex operation, weekend roll-overs, spread expansion, slippage, and overnight swap accounting.
- Reject or defer execution when market data is unavailable, stale, or inconsistent.
- Treat unknown broker responses as unknown, not as successful fills.
- Never blindly retry a non-idempotent order submission; reconcile with the client order ID first.

#### EX-4: Durable observability

- Record trade lifecycle, rejection reason, latency, slippage, provider error class, sentiment context, news context, and daily drawdown state.
- Use persistent SQLite volumes or PostgreSQL volumes in Compose.
- Never record credentials, authorization headers, or raw secret-bearing provider payloads.

## 4. Explicit Phase 1 NON-GOALS

Phase 1 is only the deterministic backtesting foundation. It does **not** include:

1. OANDA integration and implementation.
2. Live-money execution or any live-account endpoint.
3. Broker order placement, modification, cancellation, or reconciliation before the demo contract is verified.
4. OpenAI/Ollama calls, LLM sentiment scoring, or news polling.
5. Sentiment confirmation/rejection as an execution gate.
6. Provider-classified news blackout enforcement in the runtime pipeline.
7. Daily drawdown halt enforcement against a live or paper account; Phase 1 may define the pure state transition and tests only if needed.
8. ATR-based trailing-stop management; this belongs to Phase 2.
9. Broker-specific lot submission or provider payload mapping.
10. SQLite/PostgreSQL persistence for live execution metrics; persistence belongs to the paper-trading pipeline.
11. Per-tick LLM calls, unattended autonomous trading, investment advice, or profitability claims.
12. Any path that bypasses risk checks, paper-mode checks, strict JSON validation, or mandatory exits.

## 5. Acceptance criteria

Each scenario uses an injected clock, deterministic fixtures, and fake providers. “Submit” means reaching the broker adapter after every gate; in Phase 1, the equivalent is an accepted backtest event because no broker is connected.

### 5.1 Technical strategy triggers

#### AC-TE-01: Bullish setup emits LONG

- **Given** chronological 15-minute EUR/USD candles are loaded from CSV
- **And** the configured RSI and moving-average crossover indicate a valid bullish setup
- **When** the strategy evaluates the completed candle
- **Then** it emits one typed `LONG` technical signal
- **And** it does not call an LLM or broker

#### AC-TE-02: Bearish setup emits SHORT

- **Given** chronological 15-minute EUR/USD candles
- **And** the configured RSI and moving-average crossover indicate a valid bearish setup
- **When** the strategy evaluates the completed candle
- **Then** it emits one typed `SHORT` technical signal
- **And** the signal remains subject to risk and execution gates

#### AC-TE-03: No signal from incomplete data

- **Given** fewer candles than the configured indicator lookback or malformed OHLC data
- **When** the strategy evaluates the input
- **Then** it emits no trade signal
- **And** it does not fabricate indicator values

#### AC-TE-04: Backtest determinism and friction

- **Given** identical candles, configuration, spread, slippage, and swap assumptions
- **When** the backtest runs twice
- **Then** signal sequence and result inputs are identical
- **And** friction assumptions are included in the result

### 5.2 Sentiment confirmation and rejection overrides

#### AC-SF-01: Positive sentiment confirms BUY eligibility

- **Given** a valid technical BUY signal
- **And** the LLM request uses forced JSON mode
- **And** the validated sentiment score is greater than `+0.50`
- **And** no blackout or daily drawdown halt is active
- **And** risk and exit validation passes
- **When** the signal gate evaluates the candidate
- **Then** it may produce an approved order intent
- **And** the LLM provider is not called as an execution client

#### AC-SF-02: Neutral sentiment rejects BUY

- **Given** a valid technical BUY signal
- **And** validated sentiment is exactly `+0.50` or lower
- **When** the signal gate evaluates the candidate
- **Then** it rejects or holds the BUY
- **And** no broker submission occurs

#### AC-SF-03: Bearish sentiment rejects BUY

- **Given** a valid technical BUY signal
- **And** validated sentiment is below zero
- **When** the signal gate evaluates the candidate
- **Then** it rejects the BUY
- **And** positive technical indicators cannot override the sentiment rejection

#### AC-SF-04: Invalid sentiment fails closed

- **Given** a technical BUY signal
- **And** the provider times out, disconnects, returns malformed JSON, omits the score, or returns an out-of-range score
- **When** the signal gate evaluates the candidate
- **Then** it rejects or holds the BUY
- **And** it does not parse prose or guess a fallback score
- **And** it records the sanitized failure reason

#### AC-SF-05: Sentiment cannot override safety gates

- **Given** sentiment is greater than `+0.50`
- **And** risk exceeds 1%, an exit is missing/invalid, data is stale, or paper mode is unverified
- **When** final approval runs
- **Then** the candidate is rejected
- **And** no provider order call occurs

### 5.3 News blackout windows

#### AC-NEWS-01: High-impact event blocks new entry

- **Given** a configured news provider marks an event as high impact for the instrument or relevant currency
- **And** the current time is inside that event's explicitly configured blackout interval
- **When** any new entry signal is evaluated
- **Then** the entry is rejected or held
- **And** no broker submission occurs
- **And** the event ID and blackout reason are observable

#### AC-NEWS-02: Blackout overrides positive sentiment

- **Given** a technical BUY with validated sentiment greater than `+0.50`
- **And** a relevant high-impact event is inside its blackout interval
- **When** the combined gate evaluates the candidate
- **Then** the blackout rejects the candidate
- **And** sentiment cannot override the blackout

#### AC-NEWS-03: Missing impact classification is not silently safe

- **Given** a news item has no provider impact classification
- **When** the system evaluates whether a blackout applies
- **Then** it follows an explicit configured policy
- **And** it does not invent a numeric impact score or silently treat the event as safe

The context does not define a universal impact taxonomy or blackout duration. The provider classification and interval must therefore be explicit configuration and observable in the decision record.

### 5.4 Daily drawdown halts

#### AC-RISK-01: Drawdown threshold halts entries

- **Given** a configured `DAILY_DRAWDOWN_LIMIT`
- **And** the account's realized plus unrealized daily drawdown reaches or exceeds that limit
- **When** a new technical signal reaches the risk gate
- **Then** the risk gate rejects the new entry
- **And** it records the baseline, observed drawdown, limit, and halt reason

#### AC-RISK-02: Drawdown halt overrides sentiment and strategy

- **Given** a valid technical setup and sentiment greater than `+0.50`
- **And** the daily drawdown halt is active
- **When** final approval runs
- **Then** no order intent is produced
- **And** no broker call occurs

#### AC-RISK-03: Drawdown halt has no unsafe default

- **Given** the daily drawdown limit is missing, malformed, or non-positive
- **When** runtime risk configuration loads
- **Then** startup or risk evaluation fails closed
- **And** the system does not assume an unlimited or silently chosen threshold

#### AC-RISK-04: Halt reset requires a fresh trading-day state

- **Given** a drawdown halt is active
- **And** the configured trading-day boundary has passed
- **And** a fresh account snapshot establishes the new baseline
- **When** the halt state resets
- **Then** new entries may be evaluated against the new baseline
- **And** the previous halt remains in durable history

### 5.5 Position sizing and exits

#### AC-RISK-05: One-percent ceiling

- **Given** account equity, entry price, stop-loss price, and proposed quantity
- **When** maximum loss is calculated
- **Then** risk is no greater than 1.0% of account balance
- **And** an over-limit quantity is rejected or reduced before execution

#### AC-RISK-06: Dynamic size formula

- **Given** account equity and a positive entry-to-stop distance
- **When** the risk engine sizes the order
- **Then** it uses `account_equity * 0.01 / abs(entry - stop_loss)`
- **And** applies contract-size, pip-value, and broker min/max rules where required

#### AC-RISK-07: Mandatory exits

- **Given** an order missing a hard stop-loss or take-profit
- **When** it reaches the execution wrapper
- **Then** the wrapper rejects it
- **And** no provider order call occurs

#### AC-RISK-08: Directional exits

- **Given** a `LONG` order with stop-loss at/above entry or take-profit at/below entry, or a `SHORT` order with the inverse invalid relationship
- **When** the wrapper validates it
- **Then** it rejects the order with a reason
- **And** it does not call the provider

### 5.6 Execution, containers, and failures

#### AC-EXEC-01: Paper/demo only

- **Given** the app container starts
- **When** configuration loads
- **Then** `PAPER_TRADING=true`, `LIVE_TRADING=false`, and a demo endpoint are required
- **And** live-money configuration is rejected

#### AC-EXEC-02: Infrastructure failure fails closed

- **Given** broker timeout, WebSocket drop, stale price, unknown response, or database-unavailable state
- **When** execution handles the failure
- **Then** no new position is created or increased
- **And** the sanitized failure is recorded

#### AC-EXEC-03: Durable volume behavior

- **Given** the app container is replaced
- **When** the container restarts against the same SQLite or PostgreSQL volume
- **Then** trade logs, sentiment history, news events, and execution diagnostics remain available
- **And** credentials are not stored in the volume

## 6. Success measures

- Phase 1 deterministically backtests 15-minute EUR/USD RSI + moving-average-crossover behavior.
- Every future order path enforces 1% risk, daily drawdown halts, mandatory hard stop-loss/take-profit, paper mode, and final revalidation.
- LLM input/output is strict JSON validated before it influences a BUY.
- High-impact news blackout decisions are explicit and observable.
- Container replacement does not lose trade or sentiment history.
- No live-money execution is possible by default.
