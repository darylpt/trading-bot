# Trading Bot Architecture

## Runtime Boundaries

The application is a paper/demo-only, fail-closed trading system:

```text
market data -> strategy signal -> risk approval -> execution gate -> broker adapter
       \-> news/sentiment filter ----------------^                  |
                                             SQLite session metrics
```

- `src/strategy/` validates candles and calculates deterministic indicators.
- `src/trading_bot/strategy.py` emits typed `BUY`/`SELL`/`HOLD` crossover signals.
- `src/sentiment/` validates strict JSON sentiment and news blackout state. Sentiment never submits orders.
- `src/trading_bot/risk.py` is the Phase 3 pre-trade risk boundary.
- `src/risk/` contains reusable sizing, ATR exits, and order-risk calculations.
- `src/execution/` contains broker-neutral payloads, adapters, reconciliation, and the rejecting execution gate.
- `src/persistence/` owns SQLite schema creation, operational writes, session metrics, and the cross-shell CLI.
- `src/observability/` owns sanitized lifecycle logging and forward-test readiness checks.
- `src/trading_bot/__main__.py` orchestrates startup, one tick, and daemon scheduling; it does not bypass strategy, risk, or execution boundaries.

## Environment Variables

All values below are safe paper/demo defaults. Secrets are injected at runtime only.

| Variable | Default | Contract |
| --- | --- | --- |
| `PAPER_TRADING` | `true` | Must remain true for local/container execution. |
| `LIVE_TRADING` | `false` | Must remain false by default. |
| `BROKER_ENV` | `demo` | Allowed values: `paper`, `demo`. |
| `BROKER_PROVIDER` | `mt5` in Compose, `oanda` in settings | Selects the provider adapter. |
| `BROKER_ENDPOINT` | unset | Optional provider URL; demo/practice endpoints only. |
| `BROKER_TOKEN` | local demo placeholder | Runtime credential; never commit a real token. |
| `DAILY_DRAWDOWN_LIMIT` | `0.05` | Daily loss fraction used by Phase 3 risk guardrails. |
| `TICK_INTERVAL_SECONDS` | `60` | Positive daemon interval; `--interval` may override it. |
| `DATA_DIR` | `data` locally, `/app/data` in Compose | Holds `market_data.csv` and `session_metrics.db`. |
| `LOG_DIR` | `logs` locally, `/app/logs` in Compose | Operational logs. |
| `MARKET_DATA_PATH` | unset | Optional OHLCV CSV override; otherwise `DATA_DIR/market_data.csv`. |
| `ACCOUNT_EQUITY` | unset | Current paper account equity; missing values reject BUY/SELL signals. |
| `SESSION_START_EQUITY` | unset | Session baseline equity used for drawdown calculation. |
| `RISK_STOP_DISTANCE` | unset | Positive stop distance used for position sizing; missing values reject BUY/SELL signals. |
| `SENTIMENT_PROVIDER` | `openai` | Strict JSON sentiment provider. |
| `OLLAMA_BASE_URL` | unset | Optional local Ollama URL. |
| `DATABASE_URL` | unset locally | Optional PostgreSQL service URL; SQLite remains the initial persistence path. |

Missing values must resolve to safe demo defaults or fail closed. Empty values are treated as missing. Real credentials must never be replaced with a value that could authorize live trading.

## Repository Structure

```text
/
├── src/
│   ├── config/                  # Pydantic settings and health checks
│   ├── domain/                  # Strict cross-module models
│   ├── execution/               # Broker adapters and execution gate
│   ├── observability/           # Sanitized lifecycle logs and readiness
│   ├── persistence/             # SQLite repository, schema, CLI
│   ├── risk/                    # Sizing, exits, drawdown calculations
│   ├── sentiment/               # News and strict JSON LLM contracts
│   ├── strategy/                # Candle loading and deterministic indicators
│   └── trading_bot/             # Entrypoint, orchestration, signals, seeding
├── tests/unit/                  # Pure validation and calculation tests
├── tests/integration/           # Pipeline, persistence, and adapter contracts
├── Dockerfile                  # Python 3.11-slim deterministic image
├── docker-compose.yml           # app, test, and PostgreSQL services
├── ROADMAP.md                  # Phased delivery contract
└── ARCHITECTURE.md             # This boundary and contract document
```

## Database Schemas

SQLite is initialized idempotently from `src/persistence/schema.sql`.

### `trade_logs`

Stores validated order intents and execution status: client order ID, instrument, direction, quantity, entry, Stop Loss, Take Profit, account equity, risk fraction, environment, rejection reason, and open timestamp.

### `execution_logs`

Stores sanitized operational events: client order ID, event type, provider, error class, message, latency, slippage, and creation timestamp. Secrets and authorization headers are never persisted.

### `session_metrics`

Stores one aggregate row per UTC session date:

- `session_date` primary key
- `broker_latency_total_ms`
- `broker_latency_samples`
- `news_blackout_hits`
- `updated_at`

`SQLiteRepository` creates all tables on connection and exposes `get_latest_metrics()` for CLI inspection.

## Testing and Quality Standards

Run commands with the repository Python 3.11 virtual environment:

```text
python -m pytest
python -m mypy src
python -m ruff check .
python -m ruff format --check .
python -m compileall -q src tests
```

- Tests assert observable behavior, rejection paths, limits, transitions, and persistence invariants.
- External providers use deterministic fakes in tests; no live credentials or live endpoints are used.
- Every new exported contract requires caller coverage and strict type checking.
- Risk and execution changes require an affected paper/demo smoke test in addition to unit tests.
- Docker verification uses the Compose `test` service and safe runtime environment values.
