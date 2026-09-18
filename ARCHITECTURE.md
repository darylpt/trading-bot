# Trading Bot Architecture

## Runtime Boundaries

The application is a paper/demo-only, fail-closed trading system. Broker-connected demo trading and local simulation are separate runtime modes; broker-demo mode MUST NOT silently fall back to simulated data.

The canonical Phase 0 contract is `docs/PHASE-0-SECURE-BASELINE-SPEC.md`; readiness requirements are in `docs/PAPER-TRADING-READINESS.md`; SDD workflow is in `docs/SPEC-DRIVEN-DEVELOPMENT.md`; persistent tasks are in `TODO.md`.

```text
market data -> strategy signal -> risk approval -> execution gate -> broker adapter
       \-> news/sentiment filter ----------------^                  |
                                             SQLite session metrics
```

- `src/strategy/` validates candles and calculates deterministic indicators.
- `src/trading_bot/strategy.py` emits typed `BUY`/`SELL`/`HOLD` crossover signals.
- `src/sentiment/` validates strict JSON sentiment and news blackout state. Sentiment never submits orders.
- `src/trading_bot/risk.py` is the Phase 3 pre-trade risk boundary.
- `src/trading_bot/execution.py` is the local simulation boundary: it fills approved simulated orders, persists positions, evaluates protective and counter-signal exits, and records realized P&L.
- `src/trading_bot/broker/` owns `SimulatedBroker` for local paper testing and `ExnessMT5Broker` for the explicit demo/live boundary.
- `src/execution/` contains the standard `BaseBroker` abstraction, broker-neutral payloads, reconciliation, and the rejecting execution gate.
- `src/persistence/` owns SQLite schema creation, operational writes, session metrics, and the cross-shell CLI.
- `src/observability/` owns sanitized lifecycle logging and forward-test readiness checks.
- `src/trading_bot/__main__.py` orchestrates startup, one tick, and daemon scheduling; it does not bypass strategy, risk, or execution boundaries.

The normal runtime is not considered broker-connected paper trading until it proves demo authentication, account and instrument validation, broker order submission, protective-exit confirmation, idempotency, reconciliation, restart recovery, and the forward-test gates in `docs/PAPER-TRADING-READINESS.md`.

## Environment Variables

| Variable | Default | Contract |
| --- | --- | --- |
| `TRADING_MODE` | `SIMULATED` | Explicitly selects `SIMULATED` or `BROKER_DEMO`; `LIVE` is rejected. |
| `PAPER_TRADING` | `true` | Must remain true for local/container execution. |
| `LIVE_TRADING` | `false` | Must remain false by default. |
| `BROKER_PROVIDER` | `exness_mt5` | Exness MT5 is the primary broker-demo provider behind `BaseBroker`; `SimulatedBroker` is used locally. |
| `BROKER_ENDPOINT` | unset in simulation | Required and allowlisted for `BROKER_DEMO`; never accepts live endpoints. |
| `BROKER_TOKEN` | unset in simulation | Required only at runtime for `BROKER_DEMO`; never committed or logged. |
| `BROKER_ACCOUNT` | unset in simulation | Required for `BROKER_DEMO`. |
| `DAILY_DRAWDOWN_LIMIT` | explicit configuration | Positive validated limit; no unsafe production fallback. |
| `TICK_INTERVAL_SECONDS` | `60` | Positive daemon interval; `--interval` may override it. |
| `DATA_DIR` | `data` locally, `/app/data` in Compose | Holds simulation data and persisted records. |
| `LOG_DIR` | `logs` locally, `/app/logs` in Compose | Operational logs. |
| `MARKET_DATA_PATH` | unset | Simulation-only OHLCV CSV override. |
| `ACCOUNT_EQUITY` | unset | Simulation-test input only; broker-demo reads live account state. |
| `SESSION_START_EQUITY` | unset | Simulation-test input only; broker-demo derives session baseline safely. |
| `RISK_STOP_DISTANCE` | unset | Simulation-test input only; broker-demo calculates stop distance. |
| `MAX_DATA_AGE_SECONDS` | `300` | Positive freshness limit. |
| `MAX_CLOCK_DRIFT_SECONDS` | `5` | Positive clock-drift limit. |
| `MAX_SPREAD` | explicit configuration | Positive validated spread tolerance; zero must not silently disable safety. |
| `SENTIMENT_PROVIDER` | `openai` | Strict JSON sentiment provider when sentiment is enabled. |
| `DATABASE_URL` | unset locally | Optional until persistence backend ownership is finalized. |

Mode selection is explicit. Missing broker-demo settings reject startup; broker-demo provider failures halt new entries and reconcile existing state rather than using simulated data. See `docs/PHASE-0-SECURE-BASELINE-SPEC.md`.

Missing values must resolve to safe simulation defaults only in `SIMULATED`; required broker-demo values fail closed. Empty values are treated as missing. Real credentials must never be replaced with a value that could authorize live trading.

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
│       └── broker/              # SimulatedBroker and ExnessMT5Broker boundaries
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

### `positions`

Stores one paper position lifecycle: position and client order IDs, instrument, direction, quantity, entry, protective exits, open/closed status, timestamps, exit reason, exit price, and realized P&L. The repository only permits an open position to transition to `CLOSED` once.

### `execution_logs`

Stores sanitized operational events: client order ID, event type, provider, error class, message, latency, slippage, and creation timestamp. Secrets and authorization headers are never persisted.

### `session_metrics`

Stores one aggregate row per UTC session date:

- `session_date` primary key
- `broker_latency_total_ms`
- `broker_latency_samples`
- `news_blackout_hits`
- `realized_pnl`
- `closed_trades`
- `winning_trades`
- `losing_trades`
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
- External providers use deterministic fakes in unit tests. A separate opt-in demo smoke test is required to prove real broker connectivity; it MUST use demo credentials and an allowlisted demo endpoint.
- Every new exported contract requires caller coverage and strict type checking.
- Risk and execution changes require an affected paper/demo smoke test in addition to unit tests.
- Docker verification uses the Compose `test` service and safe runtime environment values.

The complete readiness definition, operational failure scenarios, security requirements, and multi-day forward-test gate are maintained in `docs/PAPER-TRADING-READINESS.md`.

## Resume Verification Command

Use this command at the start of a resumed session to inspect the persisted paper-trading state:

```text
docker compose exec app python -m persistence --database "/app/data/session_metrics.db" --query
```
