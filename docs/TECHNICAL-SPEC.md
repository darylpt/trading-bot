# Technical Specification: Containerized Hybrid Forex Trading Bot

## 1. Technical objectives

Implement the system described in `CONTEXT.md` and constrained by `AGENTS.md` as a typed Python 3.11+ application:

- Deterministic technical strategy and backtesting.
- Provider-neutral, strict-JSON LLM sentiment analysis.
- A risk circuit breaker enforcing per-trade and daily-loss limits.
- A single broker execution gate for OANDA v20 or MetaTrader 5 paper/demo accounts.
- Docker and Docker Compose isolation for Python and native C dependencies, including TA-Lib.
- Durable SQLite or PostgreSQL trade, sentiment, news, and execution history.

Live-money execution is prohibited by default.

## 2. Runtime and container topology

### 2.1 Images and services

The canonical image uses `python:3.11-slim`. All Python packages and native/system libraries must be installed from repository-controlled configuration and the `Dockerfile`; host compilers, headers, TA-Lib, broker SDKs, or Python environments are not deployment prerequisites.

`docker-compose.yml` defines these logical services:

| Service | Responsibility | Contract |
| --- | --- | --- |
| `app` | Long-running strategy engine and background workers | Runs paper/demo mode only; restart policy is `unless-stopped`; reads runtime `.env`; mounts source for development. |
| `test` | One-shot test runner | Uses the same image and dependencies as `app`; runs `pytest`; uses fake providers and no broker credentials. |
| `postgres` | Optional durable relational store | Uses a named volume for PostgreSQL data; private Compose network; health check required before dependent writes. |

The app development mounts are:

```yaml
volumes:
  - ./src:/app/src
  - ./tests:/app/tests
```

Application persistence mounts are named volumes:

```yaml
volumes:
  - bot_data:/app/data
  - bot_logs:/app/logs
```

PostgreSQL persists to:

```yaml
volumes:
  - postgres_data:/var/lib/postgresql/data
```

Container replacement must not delete trade logs, sentiment history, news events, slippage, latency, or rejection diagnostics.

### 2.2 Dockerfile contract

The `Dockerfile` must:

1. Start from `python:3.11-slim`.
2. Install `build-essential`, `gcc`, headers, `pkg-config`, PostgreSQL client/build dependencies, and the native TA-Lib C library required by indicator packages.
3. Copy and install `requirements.txt`; if `pyproject.toml` exists, install the package from it after source copy.
4. Set `PYTHONUNBUFFERED=1` and `PYTHONDONTWRITEBYTECODE=1`.
5. Configure `/app/src` as the import path or install the package into the image.
6. Create and use a non-root `app` user.
7. Never copy `.env`, credentials, local databases, caches, or VCS metadata into an image layer.
8. Leave the runtime command to Compose so the same image can run `app` or `test`.

### 2.3 Compose runtime contract

- `app` receives `.env` through a read-only runtime mount at `/app/.env` or an equivalent Compose `env_file`; the file is never copied during build.
- `PAPER_TRADING=true`, `LIVE_TRADING=false`, and an OANDA/MT5 demo endpoint are required.
- `test` runs `docker compose run --rm test pytest ...` and does not need broker credentials.
- PostgreSQL credentials are supplied through runtime environment configuration and never hardcoded in Compose or Dockerfile instructions.
- PostgreSQL remains private to the Compose network unless an explicit local-development port mapping is configured.
- Named volumes are not removed by routine `docker compose down` verification.

Recommended commands:

```bash
docker compose build
docker compose run --rm test pytest -q
docker compose up --build -d app
docker compose logs --no-color app
docker compose down
```

## 3. Containerized module layout

Production code is organized under `src/` with boundaries that match the runtime responsibilities:
The required top-level module paths are `src/strategy`, `src/sentiment`, `src/execution`, and `src/risk`; `src/domain`, `src/config`, `src/persistence`, and `src/observability` support those boundaries.

```text
src/
├── config/
│   ├── __init__.py
│   └── settings.py                 # pydantic-settings and safety flags
├── domain/
│   ├── __init__.py
│   └── models.py                   # Pydantic cross-module contracts
├── strategy/
│   ├── __init__.py
│   ├── market_data.py               # CSV/OANDA/MT5 candle normalization
│   ├── indicators.py                # RSI, MA, ATR calculations
│   ├── signals.py                   # LONG/SHORT technical signals
│   └── backtest.py                  # Backtrader/Freqtrade adapter
├── sentiment/
│   ├── __init__.py
│   ├── news.py                      # News API/Forex Factory normalization
│   ├── prompts.py                   # Forced JSON prompt construction
│   ├── providers.py                 # OpenAI/Ollama provider adapters
│   └── parser.py                    # Strict Pydantic response validation
├── risk/
│   ├── __init__.py
│   ├── sizing.py                    # Dynamic position size
│   ├── limits.py                    # 1% and broker constraints
│   ├── drawdown.py                  # Daily drawdown state machine
│   └── gate.py                      # Final approval/rejection gate
├── execution/
│   ├── __init__.py
│   ├── protocols.py                 # Broker-neutral interfaces
│   ├── payloads.py                  # OANDA/MT5 canonical payloads
│   ├── oanda.py                     # OANDA v20 adapter
│   ├── mt5.py                       # MetaTrader 5 adapter
│   └── executor.py                  # Single rejecting submission wrapper
├── persistence/
│   ├── __init__.py
│   ├── sqlite.py                    # SQLite repositories
│   └── postgres.py                  # Optional PostgreSQL repositories
├── observability/
│   ├── __init__.py
│   └── logging.py                   # Sanitized execution diagnostics
└── app.py                           # Container entrypoint/orchestration
```

Dependency direction:

```text
strategy -> domain
sentiment -> domain
risk -> domain
execution -> domain
persistence -> domain
app -> strategy + sentiment + risk + execution + persistence
```

Rules:

- `strategy` never calls LLM or broker code.
- `sentiment` never submits, modifies, or cancels orders.
- `risk` never calls provider-specific APIs.
- `execution` is the only broker submission path.
- `persistence` never stores secrets.
- Cross-module data is typed; unvalidated dictionaries do not cross boundaries.

## 4. Pydantic models and schemas

All ingress payloads and cross-module contracts use Pydantic models or typed immutable equivalents. Unknown fields are rejected unless a provider adapter explicitly documents an exception.

### 4.1 Market candle

```python
class MarketCandle(BaseModel):
    instrument: str
    timeframe: Literal["15m", "1h"]
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal | None = None
```

Validation:

- Prices are finite and positive.
- `high >= max(open, close)`.
- `low <= min(open, close)`.
- The data adapter preserves chronological order.

### 4.2 Technical signal

```python
class TechnicalSignal(BaseModel):
    instrument: str
    direction: Literal["LONG", "SHORT"]
    signal_timestamp: datetime
    reference_price: Decimal
    rsi: Decimal
    moving_average_fast: Decimal
    moving_average_slow: Decimal
    source: Literal["technical_engine"]
```

This is a strategy intent, not an order. It contains no broker credentials or provider payload fields.

### 4.3 News event

```python
class NewsEvent(BaseModel):
    event_id: str
    source: str
    headline: str
    published_at: datetime
    instrument: str | None = None
    currency: str | None = None
    impact: Literal["LOW", "MEDIUM", "HIGH"] | None = None
    retrieved_at: datetime
```

The provider's impact classification is preserved when available. The system does not invent a universal impact score. A blackout interval is explicit configuration.

### 4.4 LLM input payload

```python
class LLMNewsItem(BaseModel):
    event_id: str
    headline: str
    published_at: datetime
    instrument: str | None = None
    currency: str | None = None

class LLMSentimentRequest(BaseModel):
    request_id: str
    model: Literal["gpt-4o-mini", "ollama"]
    analyzed_at: datetime
    news: list[LLMNewsItem]
    response_format: Literal["json_object"]
```

The provider request must set:

```python
response_format={"type": "json_object"}
```

The prompt must require one numeric sentiment score in `[-1.0, +1.0]` and prohibit order instructions. The LLM operates on 15-minute/1-hour contexts or scheduled hourly polls, not every tick.

### 4.5 LLM output payload

Canonical JSON:

```json
{
  "sentiment_score": 0.72
}
```

Pydantic contract:

```python
class LLMSentimentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sentiment_score: Annotated[float, Field(ge=-1.0, le=1.0, allow_inf_nan=False)]
```

The adapter adds trusted metadata after validation:

```python
class SentimentResult(BaseModel):
    request_id: str
    sentiment_score: float
    analyzed_at: datetime
    source: Literal["openai", "ollama"]
    news_event_ids: list[str]
    validation_status: Literal["VALID", "REJECTED"]
    error_type: str | None = None
```

Malformed JSON, unknown fields, missing fields, non-finite values, provider timeout, and out-of-range scores produce `REJECTED`, never a guessed score.

### 4.6 Account and drawdown state

```python
class AccountSnapshot(BaseModel):
    account_id: str
    equity: Decimal
    balance: Decimal
    captured_at: datetime
    environment: Literal["PAPER", "DEMO"]

class DailyDrawdownState(BaseModel):
    trading_day: date
    baseline_equity: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    drawdown_limit: Decimal
    halt_active: bool
    halted_at: datetime | None = None
    halt_reason: str | None = None
```

Rules:

- `drawdown_limit` is required configuration; no unlimited or silent default exists.
- Drawdown is evaluated from realized plus unrealized P&L against the trading-day baseline.
- Reaching or exceeding the limit blocks new entries.
- Reset requires the configured trading-day boundary and a fresh account snapshot.

### 4.7 Order intent and broker payload

```python
class OrderIntent(BaseModel):
    client_order_id: str
    instrument: str
    direction: Literal["LONG", "SHORT"]
    quantity: Decimal
    entry_price: Decimal
    stop_loss_price: Decimal
    take_profit_price: Decimal
    account_equity: Decimal
    risk_fraction: Decimal
    signal_timestamp: datetime
    sentiment_score: Decimal | None = None
```

Required validation:

- `risk_fraction <= Decimal("0.01")`.
- `quantity > 0`.
- Entry, stop-loss, and take-profit are finite and non-zero distances.
- `LONG`: stop-loss below entry; take-profit above entry.
- `SHORT`: stop-loss above entry; take-profit below entry.
- An active drawdown halt cannot produce an `OrderIntent`.

Provider-neutral payload:

```python
class ExitPayload(BaseModel):
    price: Decimal

class BrokerOrderPayload(BaseModel):
    client_order_id: str
    instrument: str
    direction: Literal["LONG", "SHORT"]
    quantity: Decimal
    entry_price: Decimal
    stop_loss: ExitPayload
    take_profit: ExitPayload
    account_equity: Decimal
    risk_fraction: Decimal
    environment: Literal["PAPER", "DEMO"]
```

The OANDA and MT5 adapters map this payload to provider-specific fields. They must not remove either exit.

### 4.8 Execution result

```python
class ExecutionResult(BaseModel):
    client_order_id: str
    provider_order_id: str | None = None
    status: Literal["ACCEPTED", "REJECTED", "UNKNOWN"]
    filled_quantity: Decimal | None = None
    fill_price: Decimal | None = None
    slippage: Decimal | None = None
    latency_ms: int | None = None
    rejection_reason: str | None = None
    environment: Literal["PAPER", "DEMO"]
```

`UNKNOWN` is not success. The executor reconciles the client order ID before any non-idempotent retry.

## 5. Interfaces and contracts

### 5.1 Technical engine

```python
def calculate_indicators(
    candles: Sequence[MarketCandle],
) -> IndicatorFrame:
    ...

def generate_signal(
    indicators: IndicatorFrame,
    timestamp: datetime,
) -> TechnicalSignal | None:
    ...
```

Both functions are deterministic and network-free. They represent insufficient lookback data explicitly.

### 5.2 Sentiment provider

```python
class SentimentProvider(Protocol):
    def score_news(
        self,
        request: LLMSentimentRequest,
    ) -> SentimentResult:
        ...
```

OpenAI and Ollama implementations share this interface. They may fail, but they may never return unvalidated strategy text.

### 5.3 Risk gate

```python
def approve_entry(
    signal: TechnicalSignal,
    sentiment: SentimentResult | None,
    news_events: Sequence[NewsEvent],
    account: AccountSnapshot,
    drawdown: DailyDrawdownState,
    *,
    spread: Decimal,
    now: datetime,
) -> OrderIntent | Rejection:
    ...
```

Approval order:

1. Data freshness and market/session checks.
2. News blackout check.
3. Daily drawdown halt check.
4. BUY sentiment confirmation (`sentiment_score > +0.50`).
5. Account and 1% risk calculation.
6. Directional hard stop-loss/take-profit validation.
7. Broker min/max and spread constraints.
8. Final creation of `OrderIntent`.

Any failed or unknown condition returns `Rejection` and no order intent.

### 5.4 Broker executor

```python
class BrokerExecutor(Protocol):
    def get_account_snapshot(self) -> AccountSnapshot:
        ...

    def submit_order(self, payload: BrokerOrderPayload) -> ExecutionResult:
        ...
```

Only `src/execution/executor.py` may invoke this interface. It verifies paper/demo mode, re-checks risk and exits, maps the payload, submits once, and normalizes the response.

## 6. Database schema

SQLite is the initial Phase 4 persistence option. PostgreSQL is an optional Compose service with equivalent logical tables. All timestamps are UTC ISO-8601 values or database-native UTC timestamps.

### 6.1 `trade_logs`

| Column | Type | Constraints |
| --- | --- | --- |
| `id` | INTEGER/BIGSERIAL | Primary key |
| `client_order_id` | TEXT | Unique idempotency key |
| `provider_order_id` | TEXT | Nullable provider ID |
| `instrument` | TEXT | Required |
| `direction` | TEXT | `LONG` or `SHORT` |
| `quantity` | NUMERIC | Positive calculated size |
| `entry_price` | NUMERIC | Required |
| `stop_loss_price` | NUMERIC | Required hard stop |
| `take_profit_price` | NUMERIC | Required target |
| `account_equity` | NUMERIC | Sizing snapshot |
| `risk_fraction` | NUMERIC | Must be `<= 0.01` |
| `sentiment_score` | NUMERIC | Nullable; `-1..1` |
| `drawdown_state` | TEXT | JSON/sanitized state snapshot |
| `status` | TEXT | `ACCEPTED`, `REJECTED`, `UNKNOWN` |
| `fill_price` | NUMERIC | Nullable |
| `slippage` | NUMERIC | Nullable |
| `latency_ms` | INTEGER | Nullable |
| `rejection_reason` | TEXT | Nullable sanitized reason |
| `environment` | TEXT | `PAPER` or `DEMO` |
| `opened_at` | TIMESTAMP | Required |
| `closed_at` | TIMESTAMP | Nullable |

Indexes: unique `client_order_id`; indexes on `instrument`, `opened_at`, `status`, and `environment`.

### 6.2 `sentiment_history`

| Column | Type | Constraints |
| --- | --- | --- |
| `id` | INTEGER/BIGSERIAL | Primary key |
| `request_id` | TEXT | Unique provider request correlation ID |
| `source` | TEXT | `openai` or `ollama` |
| `model` | TEXT | Provider model identifier |
| `sentiment_score` | NUMERIC | `-1.0..1.0` when valid |
| `news_event_ids` | TEXT/JSONB | Referenced event IDs |
| `analyzed_at` | TIMESTAMP | Required UTC time |
| `validation_status` | TEXT | `VALID` or `REJECTED` |
| `error_type` | TEXT | Nullable sanitized category |

Never persist raw authorization-bearing LLM payloads.

### 6.3 `news_events`

| Column | Type | Constraints |
| --- | --- | --- |
| `event_id` | TEXT | Primary key |
| `source` | TEXT | Provider name |
| `headline` | TEXT | Required |
| `published_at` | TIMESTAMP | Provider time |
| `retrieved_at` | TIMESTAMP | Ingestion time |
| `instrument` | TEXT | Nullable |
| `currency` | TEXT | Nullable |
| `impact` | TEXT | Provider classification when available |
| `blackout_start` | TIMESTAMP | Nullable configured interval |
| `blackout_end` | TIMESTAMP | Nullable configured interval |

### 6.4 `execution_logs`

| Column | Type | Constraints |
| --- | --- | --- |
| `id` | INTEGER/BIGSERIAL | Primary key |
| `client_order_id` | TEXT | Nullable correlation ID |
| `event_type` | TEXT | Validation, submission, fill, rejection, timeout, disconnect, halt, or error |
| `provider` | TEXT | OANDA, MT5, OpenAI, Ollama, or internal |
| `error_class` | TEXT | Nullable sanitized category |
| `message` | TEXT | No secrets or raw payloads |
| `latency_ms` | INTEGER | Nullable |
| `slippage` | NUMERIC | Nullable |
| `created_at` | TIMESTAMP | Required UTC time |

## 7. Error handling and fallback defaults

### 7.1 Fail-closed policy

Default policy is `REJECT/CLOSED`:

- Provider timeout, WebSocket disconnect, stale data, schema failure, unknown order state, or database failure produces no new order.
- Missing or contradictory paper/live configuration prevents startup or submission.
- Missing account equity, stop distance, take-profit, sentiment authorization, or drawdown limit prevents approval.
- A high-impact blackout or active daily drawdown halt prevents new entries.
- Unknown broker responses remain `UNKNOWN`; they are not treated as fills.

### 7.2 Safe fallback rules

- Retry safe read operations only, subject to provider limits.
- Never blindly retry non-idempotent order submission; reconcile `client_order_id` first.
- Do not fallback from invalid LLM JSON to prose parsing, a guessed score, or a neutral score that could authorize a trade.
- Do not silently default risk, exit prices, account balance, paper/live mode, blackout intervals, or daily drawdown thresholds.
- A missing optional provider impact classification follows an explicit configured policy; it is not automatically safe.
- A daily drawdown limit is required configuration. Missing or invalid configuration fails closed.

### 7.3 Sanitized observability

Record rejection reason, signal ID, event IDs, drawdown state, latency, slippage, and provider error class. Scrub API keys, authorization headers, account secrets, signed URLs, and raw secret-bearing payloads before logs or database writes.

## 8. Security strategy

- Store OpenAI, OANDA, MT5, PostgreSQL, and other credentials in `.env` or runtime secret injection only.
- Mount `.env` read-only into the `app` container at runtime or use an equivalent Compose `env_file`; never `COPY .env`.
- `.env` is excluded by `.dockerignore`; `.env.example` contains names and safe placeholders only.
- Never pass credentials through Dockerfile `ARG`, image labels, build logs, source files, tests, fixtures, notebooks, or committed configuration.
- Build images without broker/API secrets. Use BuildKit secret mounts only for private dependency installation and ensure no secret persists in a layer.
- Require `PAPER_TRADING=true`, `LIVE_TRADING=false`, and an OANDA/MT5 demo endpoint. Live endpoints are prohibited by default.
- Run the app as a non-root user and keep PostgreSQL private to the Compose network.
- Use least-privilege demo credentials and separate accounts for development.
- Persist operational records, never credentials, in SQLite/PostgreSQL volumes.
- Validate all external payloads at ingress with Pydantic and reject unknown fields unless explicitly mapped by an adapter.

## 9. Verification requirements

Before a containerized change is complete:

```bash
python -m pytest -q
python -m mypy src
python -m ruff check .
python -m ruff format --check .
docker compose build
docker compose run --rm test pytest -q
```

For strategy/risk/execution changes, also run the affected deterministic backtest or paper/demo smoke path. Do not report container build success when the Docker daemon or required runtime secrets are unavailable.
