# Implementation Plan: Containerized Hybrid Forex Trading Bot

## 1. Execution contract

- Execute high-level tasks in phase order and subtasks in numeric order.
- Each subtask has one observable deliverable, one target implementation file, one target test file, and one primary verification command.
- Before the first Docker subtask, build the image with `docker compose build`. The per-subtask primary command remains the exact test command listed below.
- Use the module boundaries in `docs/TECHNICAL-SPEC.md`: `src/strategy`, `src/sentiment`, `src/risk`, and `src/execution`.
- Keep tests deterministic. Use fake news, LLM, broker, clock, and database providers; never use live credentials or live endpoints in tests.
- Every broker-facing subtask must preserve `PAPER_TRADING=true`, `LIVE_TRADING=false`, mandatory hard stop-loss/take-profit, final risk revalidation, and the single execution gate.
- Replace initial `NotImplementedError` stubs with behavior assertions as the related deliverable is implemented. Do not delete a test to make the suite green.
- Docker commands assume safe PostgreSQL runtime variables are available through `.env` or the shell. No command uses live credentials.
- A subtask is complete only when its target implementation file, target test file, acceptance behavior, and both verification commands are complete.

## 2. Phase 1: Environment & Base Models

### Task 1: Establish the container test baseline

The image, Compose services, build context, dependencies, runtime flags, volumes, and container health checks must be independently verifiable.

#### Task 1.1: Build the Python 3.11-slim runtime image

- **Deliverable:** The Dockerfile installs the required compiler, PostgreSQL, and native TA-Lib build dependencies and runs as the non-root `app` user.
- **Target Implementation File:** `Dockerfile`
- **Target Test File:** `tests/unit/test_container_runtime.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/unit/test_container_runtime.py`
- **Fallback Local Command:** `pytest tests/unit/test_container_runtime.py`

#### Task 1.2: Configure the application Compose service

- **Deliverable:** The `app` service mounts source/data/log paths, uses paper mode, reads runtime configuration, and uses the expected restart policy.
- **Target Implementation File:** `docker-compose.yml`
- **Target Test File:** `tests/unit/test_container_runtime.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/unit/test_container_runtime.py`
- **Fallback Local Command:** `pytest tests/unit/test_container_runtime.py`

#### Task 1.3: Exclude secrets and build artifacts from the image context

- **Deliverable:** `.env`, credentials, caches, databases, VCS metadata, and test artifacts are excluded while `.env.example` remains available.
- **Target Implementation File:** `.dockerignore`
- **Target Test File:** `tests/unit/test_container_runtime.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/unit/test_container_runtime.py`
- **Fallback Local Command:** `pytest tests/unit/test_container_runtime.py`

#### Task 1.4: Pin the container runtime dependencies

- **Deliverable:** Runtime and verification dependencies are pinned to Python 3.11-compatible versions, including Pydantic, pytest, pandas, and the selected TA-compatible indicator package.
- **Target Implementation File:** `requirements.txt`
- **Target Test File:** `tests/unit/test_container_runtime.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/unit/test_container_runtime.py`
- **Fallback Local Command:** `pytest tests/unit/test_container_runtime.py`

### Task 2: Implement typed runtime settings

#### Task 2.1: Load runtime environment settings

- **Deliverable:** Pydantic settings load database, broker, paper-mode, logging, data-directory, and provider configuration from runtime environment variables.
- **Target Implementation File:** `src/config/settings.py`
- **Target Test File:** `tests/unit/test_settings.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/unit/test_settings.py`
- **Fallback Local Command:** `pytest tests/unit/test_settings.py`

#### Task 2.2: Enforce startup safety settings

- **Deliverable:** Settings reject contradictory paper/live flags, non-demo endpoints, missing drawdown limits, and invalid numeric limits.
- **Target Implementation File:** `src/config/settings.py`
- **Target Test File:** `tests/unit/test_settings.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/unit/test_settings.py`
- **Fallback Local Command:** `pytest tests/unit/test_settings.py`

#### Task 2.3: Publish safe configuration placeholders

- **Deliverable:** `.env.example` documents required variable names and safe non-secret paper/demo placeholders without credentials.
- **Target Implementation File:** `.env.example`
- **Target Test File:** `tests/unit/test_settings.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/unit/test_settings.py`
- **Fallback Local Command:** `pytest tests/unit/test_settings.py`

### Task 3: Define Pydantic domain models

#### Task 3.1: Define market and technical signal contracts

- **Deliverable:** `MarketCandle` and `TechnicalSignal` validate timeframe, prices, chronology inputs, direction, timestamp, and indicator metadata.
- **Target Implementation File:** `src/domain/models.py`
- **Target Test File:** `tests/unit/test_domain_models.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/unit/test_domain_models.py`
- **Fallback Local Command:** `pytest tests/unit/test_domain_models.py`

#### Task 3.2: Define news and sentiment contracts

- **Deliverable:** `NewsEvent`, `LLMNewsItem`, `LLMSentimentRequest`, `LLMSentimentResponse`, and `SentimentResult` reject malformed, unknown, and out-of-range data.
- **Target Implementation File:** `src/domain/models.py`
- **Target Test File:** `tests/unit/test_domain_models.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/unit/test_domain_models.py`
- **Fallback Local Command:** `pytest tests/unit/test_domain_models.py`

#### Task 3.3: Define account, drawdown, order, and execution contracts

- **Deliverable:** Account, drawdown, order intent, broker payload, exit, and execution result models enforce risk, direction, environment, and mandatory-exit invariants.
- **Target Implementation File:** `src/domain/models.py`
- **Target Test File:** `tests/unit/test_domain_models.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/unit/test_domain_models.py`
- **Fallback Local Command:** `pytest tests/unit/test_domain_models.py`

## 3. Phase 2: Technical Strategy Engine

### Task 4: Normalize historical market data

#### Task 4.1: Load CSV candles into typed records

- **Deliverable:** The CSV adapter reads 15-minute EUR/USD rows into typed chronological candle records.
- **Target Implementation File:** `src/strategy/market_data.py`
- **Target Test File:** `tests/unit/test_market_data.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/unit/test_market_data.py`
- **Fallback Local Command:** `pytest tests/unit/test_market_data.py`

#### Task 4.2: Reject malformed and insufficient market data

- **Deliverable:** The adapter rejects invalid OHLC relationships, non-finite/non-positive prices, disorderly timestamps, gaps, and insufficient indicator history.
- **Target Implementation File:** `src/strategy/market_data.py`
- **Target Test File:** `tests/unit/test_market_data.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/unit/test_market_data.py`
- **Fallback Local Command:** `pytest tests/unit/test_market_data.py`

### Task 5: Implement RSI, moving-average, and ATR calculations

#### Task 5.1: Calculate RSI and moving averages

- **Deliverable:** Pure deterministic RSI, fast moving-average, and slow moving-average calculations return no fabricated warm-up values.
- **Target Implementation File:** `src/strategy/indicators.py`
- **Target Test File:** `tests/unit/test_indicators.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/unit/test_indicators.py`
- **Fallback Local Command:** `pytest tests/unit/test_indicators.py`

#### Task 5.2: Calculate ATR and indicator warm-up state

- **Deliverable:** ATR calculations and lookback readiness are deterministic and explicitly identify insufficient history.
- **Target Implementation File:** `src/strategy/indicators.py`
- **Target Test File:** `tests/unit/test_indicators.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/unit/test_indicators.py`
- **Fallback Local Command:** `pytest tests/unit/test_indicators.py`

### Task 6: Generate typed LONG and SHORT signals

#### Task 6.1: Emit bullish LONG technical signals

- **Deliverable:** A completed bullish RSI/MA setup emits one typed `LONG` technical signal with instrument, timestamp, price, and indicator inputs.
- **Target Implementation File:** `src/strategy/signals.py`
- **Target Test File:** `tests/unit/test_signals.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/unit/test_signals.py`
- **Fallback Local Command:** `pytest tests/unit/test_signals.py`

#### Task 6.2: Emit bearish SHORT technical signals

- **Deliverable:** A completed bearish RSI/MA setup emits one typed `SHORT` technical signal with no provider-specific fields.
- **Target Implementation File:** `src/strategy/signals.py`
- **Target Test File:** `tests/unit/test_signals.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/unit/test_signals.py`
- **Fallback Local Command:** `pytest tests/unit/test_signals.py`

#### Task 6.3: Suppress invalid and incomplete signals

- **Deliverable:** No signal is emitted for incomplete indicator history, malformed input, or non-trigger conditions, and no broker/LLM dependency is called.
- **Target Implementation File:** `src/strategy/signals.py`
- **Target Test File:** `tests/unit/test_signals.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/unit/test_signals.py`
- **Fallback Local Command:** `pytest tests/unit/test_signals.py`

### Task 7: Add the Backtrader/Freqtrade backtest adapter

#### Task 7.1: Adapt strategy events to the selected backtest framework

- **Deliverable:** Historical candles and strategy configuration run through one selected Backtrader/Freqtrade adapter.
- **Target Implementation File:** `src/strategy/backtest.py`
- **Target Test File:** `tests/integration/test_backtest.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/integration/test_backtest.py`
- **Fallback Local Command:** `pytest tests/integration/test_backtest.py`

#### Task 7.2: Guarantee deterministic backtest results

- **Deliverable:** Identical candles, configuration, and friction inputs produce identical signal sequences and result inputs without network calls.
- **Target Implementation File:** `src/strategy/backtest.py`
- **Target Test File:** `tests/integration/test_backtest.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/integration/test_backtest.py`
- **Fallback Local Command:** `pytest tests/integration/test_backtest.py`

### Task 8: Model Forex friction and sessions

#### Task 8.1: Apply spread, slippage, and swap assumptions

- **Deliverable:** Backtest results include dynamic spread, slippage, overnight swap, and market-open/close spread expansion.
- **Target Implementation File:** `src/strategy/friction.py`
- **Target Test File:** `tests/unit/test_strategy_friction.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/unit/test_strategy_friction.py`
- **Fallback Local Command:** `pytest tests/unit/test_strategy_friction.py`

#### Task 8.2: Enforce Forex sessions and stale-data blocking

- **Deliverable:** Weekend roll-over, 24/5 session boundaries, market gaps, and stale prices explicitly block new entries where required.
- **Target Implementation File:** `src/strategy/sessions.py`
- **Target Test File:** `tests/unit/test_strategy_friction.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/unit/test_strategy_friction.py`
- **Fallback Local Command:** `pytest tests/unit/test_strategy_friction.py`

## 4. Phase 3: LLM Sentiment Parser

### Task 9: Normalize financial-news events

#### Task 9.1: Normalize provider news payloads

- **Deliverable:** News API and Forex Factory payloads become typed events preserving source, headline, publication time, currency, instrument, and impact.
- **Target Implementation File:** `src/sentiment/news.py`
- **Target Test File:** `tests/unit/test_news_ingestion.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/unit/test_news_ingestion.py`
- **Fallback Local Command:** `pytest tests/unit/test_news_ingestion.py`

#### Task 9.2: Deduplicate and track news freshness

- **Deliverable:** Repeated events are deduplicated and stale or missing timestamps are represented explicitly without invented impact scores.
- **Target Implementation File:** `src/sentiment/news.py`
- **Target Test File:** `tests/unit/test_news_ingestion.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/unit/test_news_ingestion.py`
- **Fallback Local Command:** `pytest tests/unit/test_news_ingestion.py`

### Task 10: Define the forced-JSON sentiment request

#### Task 10.1: Validate the provider-neutral request

- **Deliverable:** Sentiment requests validate model, request ID, analysis time, news items, and `response_format="json_object"`.
- **Target Implementation File:** `src/sentiment/prompts.py`
- **Target Test File:** `tests/unit/test_sentiment_prompt.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/unit/test_sentiment_prompt.py`
- **Fallback Local Command:** `pytest tests/unit/test_sentiment_prompt.py`

#### Task 10.2: Build the safety-constrained prompt

- **Deliverable:** Prompt construction requires one numeric score in `[-1.0, +1.0]`, excludes order instructions, and is scheduled for 15m/1h or hourly contexts.
- **Target Implementation File:** `src/sentiment/prompts.py`
- **Target Test File:** `tests/unit/test_sentiment_prompt.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/unit/test_sentiment_prompt.py`
- **Fallback Local Command:** `pytest tests/unit/test_sentiment_prompt.py`

### Task 11: Parse and validate LLM JSON responses

#### Task 11.1: Parse strict sentiment JSON

- **Deliverable:** Valid JSON is validated through the strict Pydantic sentiment response schema with unknown fields rejected.
- **Target Implementation File:** `src/sentiment/parser.py`
- **Target Test File:** `tests/unit/test_sentiment_parser.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/unit/test_sentiment_parser.py`
- **Fallback Local Command:** `pytest tests/unit/test_sentiment_parser.py`

#### Task 11.2: Fail closed on invalid provider output

- **Deliverable:** Malformed JSON, prose, missing/non-numeric/non-finite/out-of-range scores, and provider failures produce an explicit rejected result with no guessed fallback.
- **Target Implementation File:** `src/sentiment/parser.py`
- **Target Test File:** `tests/unit/test_sentiment_parser.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/unit/test_sentiment_parser.py`
- **Fallback Local Command:** `pytest tests/unit/test_sentiment_parser.py`

### Task 12: Implement OpenAI and Ollama provider adapters

#### Task 12.1: Implement the OpenAI sentiment adapter

- **Deliverable:** The `gpt-4o-mini` adapter sends forced JSON requests and maps fake-client responses into the provider-neutral result.
- **Target Implementation File:** `src/sentiment/providers.py`
- **Target Test File:** `tests/integration/test_sentiment_providers.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/integration/test_sentiment_providers.py`
- **Fallback Local Command:** `pytest tests/integration/test_sentiment_providers.py`

#### Task 12.2: Implement the Ollama sentiment adapter

- **Deliverable:** The Ollama adapter implements the same protocol, timeout behavior, strict parsing, and no-execution boundary as the OpenAI adapter.
- **Target Implementation File:** `src/sentiment/providers.py`
- **Target Test File:** `tests/integration/test_sentiment_providers.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/integration/test_sentiment_providers.py`
- **Fallback Local Command:** `pytest tests/integration/test_sentiment_providers.py`

### Task 13: Enforce sentiment confirmation and news blackouts

#### Task 13.1: Enforce the positive sentiment threshold

- **Deliverable:** A technical BUY is eligible for sentiment confirmation only when validated sentiment is strictly greater than `+0.50`; neutral, bearish, stale, or failed sentiment rejects or holds it.
- **Target Implementation File:** `src/sentiment/gate.py`
- **Target Test File:** `tests/unit/test_sentiment_gate.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/unit/test_sentiment_gate.py`
- **Fallback Local Command:** `pytest tests/unit/test_sentiment_gate.py`

#### Task 13.2: Enforce configured news blackout windows

- **Deliverable:** Relevant high-impact events inside explicit blackout intervals reject new entries, including entries with positive sentiment; missing impact follows explicit policy.
- **Target Implementation File:** `src/sentiment/blackout.py`
- **Target Test File:** `tests/unit/test_sentiment_gate.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/unit/test_sentiment_gate.py`
- **Fallback Local Command:** `pytest tests/unit/test_sentiment_gate.py`

## 5. Phase 4: Risk Enforcer

### Task 14: Implement dynamic position sizing

#### Task 14.1: Calculate the one-percent dynamic size

- **Deliverable:** Position size uses `account_equity * 0.01 / abs(entry_price - stop_loss_price)` for every entry.
- **Target Implementation File:** `src/risk/sizing.py`
- **Target Test File:** `tests/unit/test_position_sizing.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/unit/test_position_sizing.py`
- **Fallback Local Command:** `pytest tests/unit/test_position_sizing.py`

#### Task 14.2: Reject invalid stop distances and apply broker constraints

- **Deliverable:** Zero/negative/non-finite stop distances are rejected and contract-size, pip-value, minimum, and maximum constraints are applied explicitly.
- **Target Implementation File:** `src/risk/sizing.py`
- **Target Test File:** `tests/unit/test_position_sizing.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/unit/test_position_sizing.py`
- **Fallback Local Command:** `pytest tests/unit/test_position_sizing.py`

### Task 15: Enforce per-trade limits and directional exits

#### Task 15.1: Validate risk fraction and quantity limits

- **Deliverable:** Order intents reject risk above 1%, non-positive quantity, invalid account equity, and broker constraint violations.
- **Target Implementation File:** `src/risk/limits.py`
- **Target Test File:** `tests/unit/test_risk_limits.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/unit/test_risk_limits.py`
- **Fallback Local Command:** `pytest tests/unit/test_risk_limits.py`

#### Task 15.2: Require direction-correct hard exits

- **Deliverable:** Every valid LONG/SHORT intent has a hard stop-loss and take-profit with strictly direction-correct, positive distances.
- **Target Implementation File:** `src/risk/limits.py`
- **Target Test File:** `tests/unit/test_risk_limits.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/unit/test_risk_limits.py`
- **Fallback Local Command:** `pytest tests/unit/test_risk_limits.py`

### Task 16: Implement daily drawdown state transitions

#### Task 16.1: Calculate daily drawdown state

- **Deliverable:** Realized and unrealized P&L are evaluated against a required trading-day baseline and configured drawdown limit.
- **Target Implementation File:** `src/risk/drawdown.py`
- **Target Test File:** `tests/unit/test_daily_drawdown.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/unit/test_daily_drawdown.py`
- **Fallback Local Command:** `pytest tests/unit/test_daily_drawdown.py`

#### Task 16.2: Halt entries and reset safely

- **Deliverable:** Reaching or exceeding the limit blocks new entries, preserves protective exits, records the halt, and resets only after the configured boundary and fresh account snapshot.
- **Target Implementation File:** `src/risk/drawdown.py`
- **Target Test File:** `tests/unit/test_daily_drawdown.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/unit/test_daily_drawdown.py`
- **Fallback Local Command:** `pytest tests/unit/test_daily_drawdown.py`

### Task 17: Compose the final risk approval gate

#### Task 17.1: Order the final approval checks

- **Deliverable:** One gate evaluates freshness, blackout, drawdown, sentiment, risk, exits, spread, and broker constraints before producing an order intent.
- **Target Implementation File:** `src/risk/gate.py`
- **Target Test File:** `tests/unit/test_risk_gate.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/unit/test_risk_gate.py`
- **Fallback Local Command:** `pytest tests/unit/test_risk_gate.py`

#### Task 17.2: Return fail-closed rejection decisions

- **Deliverable:** Every failed or unknown check returns a reasoned rejection, produces no order intent, and cannot be overridden by positive sentiment.
- **Target Implementation File:** `src/risk/gate.py`
- **Target Test File:** `tests/unit/test_risk_gate.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/unit/test_risk_gate.py`
- **Fallback Local Command:** `pytest tests/unit/test_risk_gate.py`

## 6. Phase 5: Broker Connector

### Task 18: Define broker-neutral execution contracts

#### Task 18.1: Define the broker executor protocol

- **Deliverable:** A typed broker protocol exposes one provider-neutral submit/reconcile boundary for OANDA and MT5 adapters.
- **Target Implementation File:** `src/execution/protocols.py`
- **Target Test File:** `tests/unit/test_execution_contracts.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/unit/test_execution_contracts.py`
- **Fallback Local Command:** `pytest tests/unit/test_execution_contracts.py`

#### Task 18.2: Validate canonical order and result payloads

- **Deliverable:** Canonical payloads preserve direction, quantity, entry, account equity, risk fraction, client order ID, mandatory exits, environment, and accepted/rejected/unknown status.
- **Target Implementation File:** `src/execution/payloads.py`
- **Target Test File:** `tests/unit/test_execution_contracts.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/unit/test_execution_contracts.py`
- **Fallback Local Command:** `pytest tests/unit/test_execution_contracts.py`

### Task 19: Implement the OANDA v20 demo adapter

#### Task 19.1: Map canonical payloads to OANDA requests

- **Deliverable:** The adapter maps a validated canonical payload to an OANDA v20 demo request with direction, quantity, client ID, and both hard exits.
- **Target Implementation File:** `src/execution/oanda.py`
- **Target Test File:** `tests/integration/test_oanda_adapter.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/integration/test_oanda_adapter.py`
- **Fallback Local Command:** `pytest tests/integration/test_oanda_adapter.py`

#### Task 19.2: Normalize OANDA responses and failures

- **Deliverable:** Accepted, rejected, timeout, and malformed OANDA responses become sanitized provider-neutral execution results without live endpoint access.
- **Target Implementation File:** `src/execution/oanda.py`
- **Target Test File:** `tests/integration/test_oanda_adapter.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/integration/test_oanda_adapter.py`
- **Fallback Local Command:** `pytest tests/integration/test_oanda_adapter.py`

### Task 20: Implement the MetaTrader 5 demo adapter

#### Task 20.1: Map canonical payloads to MT5 requests

- **Deliverable:** The adapter maps canonical LONG/SHORT payloads to MT5 demo requests while preserving hard stop-loss and take-profit.
- **Target Implementation File:** `src/execution/mt5.py`
- **Target Test File:** `tests/integration/test_mt5_adapter.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/integration/test_mt5_adapter.py`
- **Fallback Local Command:** `pytest tests/integration/test_mt5_adapter.py`

#### Task 20.2: Normalize MT5 responses and failures

- **Deliverable:** Fake MT5 gateway responses, provider errors, and unknown states become sanitized provider-neutral execution results.
- **Target Implementation File:** `src/execution/mt5.py`
- **Target Test File:** `tests/integration/test_mt5_adapter.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/integration/test_mt5_adapter.py`
- **Fallback Local Command:** `pytest tests/integration/test_mt5_adapter.py`

### Task 21: Enforce the single rejecting execution wrapper

#### Task 21.1: Revalidate and submit approved orders once

- **Deliverable:** The execution wrapper is the only submission path, revalidates the order immediately before submission, and makes zero provider calls for invalid orders.
- **Target Implementation File:** `src/execution/executor.py`
- **Target Test File:** `tests/integration/test_execution_gate.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/integration/test_execution_gate.py`
- **Fallback Local Command:** `pytest tests/integration/test_execution_gate.py`

#### Task 21.2: Handle failures and unknown responses safely

- **Deliverable:** Timeouts and disconnects fail closed; unknown responses are reconciled by client order ID before any retry; valid paper orders retain both exits.
- **Target Implementation File:** `src/execution/executor.py`
- **Target Test File:** `tests/integration/test_execution_gate.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/integration/test_execution_gate.py`
- **Fallback Local Command:** `pytest tests/integration/test_execution_gate.py`

### Task 22: Enforce paper/demo runtime mode

#### Task 22.1: Reject unsafe startup configuration

- **Deliverable:** Application startup rejects missing/false paper flags, true live flags, and non-demo endpoints.
- **Target Implementation File:** `src/config/settings.py`
- **Target Test File:** `tests/integration/test_paper_mode.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/integration/test_paper_mode.py`
- **Fallback Local Command:** `pytest tests/integration/test_paper_mode.py`

#### Task 22.2: Reject unsafe broker submission configuration

- **Deliverable:** The execution wrapper rechecks paper/demo mode at submission and refuses unsafe provider routes independently of startup validation.
- **Target Implementation File:** `src/execution/executor.py`
- **Target Test File:** `tests/integration/test_paper_mode.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/integration/test_paper_mode.py`
- **Fallback Local Command:** `pytest tests/integration/test_paper_mode.py`

## 7. Phase 6: Persistence, Observability & Operations

### Task 23: Create SQLite trade and sentiment repositories

#### Task 23.1: Define the SQLite schema

- **Deliverable:** SQLite tables and constraints cover trade logs, sentiment history, news events, execution logs, correlation IDs, and idempotency keys.
- **Target Implementation File:** `src/persistence/schema.sql`
- **Target Test File:** `tests/integration/test_sqlite_persistence.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/integration/test_sqlite_persistence.py`
- **Fallback Local Command:** `pytest tests/integration/test_sqlite_persistence.py`

#### Task 23.2: Implement SQLite repositories

- **Deliverable:** Repositories persist and query sanitized records on a named-volume-compatible SQLite path with idempotent writes.
- **Target Implementation File:** `src/persistence/sqlite.py`
- **Target Test File:** `tests/integration/test_sqlite_persistence.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/integration/test_sqlite_persistence.py`
- **Fallback Local Command:** `pytest tests/integration/test_sqlite_persistence.py`

### Task 24: Add PostgreSQL repository parity

#### Task 24.1: Define PostgreSQL schema parity

- **Deliverable:** PostgreSQL tables match the logical SQLite trade, sentiment, news, and execution contracts.
- **Target Implementation File:** `src/persistence/postgres.py`
- **Target Test File:** `tests/integration/test_postgres_persistence.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/integration/test_postgres_persistence.py`
- **Fallback Local Command:** `pytest tests/integration/test_postgres_persistence.py`

#### Task 24.2: Verify PostgreSQL transactions and durable volumes

- **Deliverable:** PostgreSQL writes roll back safely, persist across container replacement, and never store secrets.
- **Target Implementation File:** `src/persistence/postgres.py`
- **Target Test File:** `tests/integration/test_postgres_persistence.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/integration/test_postgres_persistence.py`
- **Fallback Local Command:** `pytest tests/integration/test_postgres_persistence.py`

### Task 25: Add sanitized observability

#### Task 25.1: Implement credential-safe structured logging

- **Deliverable:** Structured logs record rejection and provider context while scrubbing credentials, authorization headers, account secrets, and raw secret-bearing payloads.
- **Target Implementation File:** `src/observability/logging.py`
- **Target Test File:** `tests/unit/test_observability.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/unit/test_observability.py`
- **Fallback Local Command:** `pytest tests/unit/test_observability.py`

#### Task 25.2: Record trading safety metrics and events

- **Deliverable:** Observability records latency, slippage, provider errors, blackout decisions, drawdown halts, correlation IDs, and rejection reasons.
- **Target Implementation File:** `src/observability/logging.py`
- **Target Test File:** `tests/unit/test_observability.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/unit/test_observability.py`
- **Fallback Local Command:** `pytest tests/unit/test_observability.py`

### Task 26: Wire the container application pipeline

#### Task 26.1: Compose the dependency-safe application pipeline

- **Deliverable:** `src/app.py` invokes market data, strategy, sentiment, risk, execution, and persistence in the prescribed order with typed boundaries.
- **Target Implementation File:** `src/app.py`
- **Target Test File:** `tests/integration/test_application_pipeline.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/integration/test_application_pipeline.py`
- **Fallback Local Command:** `pytest tests/integration/test_application_pipeline.py`

#### Task 26.2: Run deterministic background workers

- **Deliverable:** Background workers use injectable clocks and fake providers, preserve paper mode, and stop safely on rejection or infrastructure failure.
- **Target Implementation File:** `src/app.py`
- **Target Test File:** `tests/integration/test_application_pipeline.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/integration/test_application_pipeline.py`
- **Fallback Local Command:** `pytest tests/integration/test_application_pipeline.py`

### Task 27: Run the complete regression and container safety suite

#### Task 27.1: Exercise all acceptance gates end to end

- **Deliverable:** The integrated pipeline proves technical triggers, sentiment overrides, news blackouts, drawdown halts, risk limits, mandatory exits, paper mode, persistence, and failure handling.
- **Target Implementation File:** `src/app.py`
- **Target Test File:** `tests/integration/test_application_pipeline.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/integration/test_application_pipeline.py`
- **Fallback Local Command:** `pytest tests/integration/test_application_pipeline.py`

#### Task 27.2: Run the complete containerized regression suite

- **Deliverable:** The image builds and the complete `tests/` suite runs with no live credentials, live endpoints, or weakened safety checks.
- **Target Implementation File:** `Dockerfile`
- **Target Test File:** `tests/test_full_regression.py`
- **Primary Docker Command:** `docker compose run --rm app pytest tests/test_full_regression.py`
- **Fallback Local Command:** `pytest tests/test_full_regression.py`

## 8. Final release gates

Before release, run the full suite and container lifecycle checks from the repository root:

```bash
docker compose build
docker compose run --rm app pytest tests/test_full_regression.py
docker compose up --build -d app
docker compose logs --no-color app
docker compose down
```

Fallback local test command when Docker Desktop is unavailable:

```bash
pytest tests/test_full_regression.py
```

Release is blocked if any of the following is true:

- The image cannot build without host-installed TA-Lib or compilers.
- `PAPER_TRADING=true` and `LIVE_TRADING=false` are not enforced.
- Any order can bypass `src/execution/executor.py`.
- Any order lacks a hard stop-loss or take-profit.
- Per-trade risk can exceed 1%.
- An active daily drawdown halt can produce a new entry.
- Positive sentiment can override a news blackout, drawdown halt, or risk rejection.
- `.env` or credentials appear in image layers, logs, or persisted records.
- SQLite/PostgreSQL volumes do not preserve trade and sentiment history across container replacement.
