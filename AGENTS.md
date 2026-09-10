# Agent Instructions & Project Workspace Rules

This repository is a production-oriented hybrid algorithmic and AI Forex trading system. These rules apply to every human and AI change. Preserve the safety invariants below even when adding new strategies, brokers, data sources, or LLM providers.

## 1. Stack and environment

- **Python:** 3.11 or newer. Use the repository virtual environment for every command.
- **Trading engine:** Backtrader or Freqtrade for backtesting, event loops, and dry-run paper trading.
- **Market data:** `pandas`, `numpy`, and `pandas-ta` for OHLC data and indicators such as RSI, moving averages, and ATR.
- **LLM layer:** OpenAI (`gpt-4o-mini`) or Ollama for news analysis. LLM output is structured data, never strategy instructions or free-form text.
- **Broker adapters:** OANDA v20 REST API or MetaTrader 5, initially against demo/paper accounts only.
- **Configuration:** `.env` loaded through `pydantic-settings`. Secrets belong in environment variables, not source code.
- **Validation and quality:** Type hints throughout; `mypy` for static typing; Pydantic models for all external and LLM payloads; `pytest` for tests; Ruff for linting and formatting when configured.
- **Container runtime:** Docker and Docker Compose for isolated builds, deterministic native dependencies, and continuous background execution.
- **Base image:** `python:3.11-slim` unless a reviewed change documents a replacement.

Never introduce a second package manager or an untracked dependency installation path. Prefer `python -m <tool>` so the command uses the active interpreter.

### Environment setup

Windows PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

macOS/Linux:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If the repository later adopts `pyproject.toml`, keep its declared dependency and tool configuration authoritative and update these commands rather than creating a parallel setup mechanism. Do not commit `.venv`, `.env`, credentials, local databases, or generated artifacts.

All runtime, test, Python, and native/system dependencies must be codified in `Dockerfile` and locked through the repository dependency configuration. Do not assume host-installed compilers, TA-Lib, shared libraries, headers, or broker SDKs exist. The image must install and verify C/system dependencies required by packages such as TA-Lib. Host Python environments are optional developer conveniences, never the source of deployment truth.

### Standard commands

Run commands from the repository root with the virtual environment activated:

```bash
# Compile/import-level smoke check
python -m compileall -q src tests

# Test suite
python -m pytest

# Static typing
python -m mypy src

# Lint
python -m ruff check .

# Formatting check
python -m ruff format --check .
```

### Docker and Compose commands

Run these from the repository root after creating or updating `Dockerfile`, `docker-compose.yml`, and `.dockerignore`:

```bash
# Build the deterministic application/test image
docker compose build

# Run the full test suite inside the image
docker compose run --rm test pytest -q

# Run one test module inside the image
docker compose run --rm test pytest tests/unit/test_risk_management.py -q

# Start the paper-trading/background services
docker compose up --build -d

# Inspect service output
docker compose logs -f app

# Stop services without deleting named volumes
docker compose down
```

The Compose `test` service is the authoritative dependency-isolated test path. The `bot`/`app` service must run only in paper/demo mode. Use named or bind-mounted volumes for SQLite/PostgreSQL data and logs; never rely on a container filesystem for durable records.

Use the project-specific equivalents only when the repository adds an explicit configuration or script. A change is not complete until the relevant tests, `mypy`, and lint/format checks pass, or the exact environmental blocker is reported. For trading behavior, also run the affected backtest or paper-trading smoke path; a passing unit suite alone does not prove execution safety.

## 2. Required project structure

Keep production code under `src/`; keep tests separate. Do not add arbitrary top-level utility modules.

```text
/
├── src/
│   └── trading_bot/
│       ├── config.py              # pydantic-settings configuration and safety flags
│       ├── domain/                # typed orders, positions, signals, market/news models
│       ├── data/                  # OHLC/news clients, normalization, cache boundaries
│       ├── indicators/             # deterministic pandas/numpy/pandas-ta calculations
│       ├── strategies/             # technical strategies; no broker or LLM side effects
│       ├── sentiment/              # LLM prompts, clients, strict response validation
│       ├── risk/                   # risk limits, sizing, stop/target calculations
│       ├── execution/              # broker adapters and the rejecting execution wrapper
│       └── services/                # orchestration and application use cases
├── tests/
│   ├── unit/                      # pure calculations and validation
│   ├── integration/               # broker/data/LLM adapter contracts
│   └── fixtures/                  # deterministic test data; no secrets
├── notebooks/                     # research only; never the execution path
├── data/                          # local historical data; keep large/secret files ignored
├── prisma/                        # not applicable unless explicitly introduced; do not copy web-app conventions
├── .env.example                   # variable names and safe placeholder values only
├── pyproject.toml                 # tool/dependency configuration when adopted
├── requirements.txt               # pinned/runtime dependencies if retained
├── Dockerfile              # Python 3.11-slim image and native/system dependencies
├── docker-compose.yml      # bot/app, test, and database services
├── .dockerignore           # excludes secrets, VCS data, caches, and local artifacts
└── CONTEXT.md                     # project decisions and roadmap
```

Keep adapters at boundaries. Strategies emit typed signals; risk converts approved signals into validated order intents; execution is the only layer allowed to submit broker orders. Do not let indicators, strategies, LLM clients, notebooks, or UI code call broker APIs directly.

## 3. Code style and typing

- Use 4-space indentation, UTF-8, and clear `snake_case` names; use `PascalCase` for classes and Pydantic models.
- Add complete type hints to public functions, methods, module-level constants, and returned collections. Use `X | None`, `list[T]`, and `dict[K, V]` with Python 3.11 syntax.
- `mypy` must run in strict mode when configuration is added. Do not use `Any`, unchecked casts, `# type: ignore`, or dynamic attribute access to bypass typing. If an external library lacks types, isolate and narrowly annotate the boundary.
- Use Pydantic models for environment settings, broker requests/responses, market/news records, LLM responses, and persisted external data. Validate at ingress; do not pass untyped dictionaries through business logic.
- Models must reject unknown fields unless a documented provider contract requires them. Constrain numeric ranges and enums: sentiment is `-1.0..1.0`, risk is `0..0.01`, and direction is `LONG` or `SHORT`.
- Prefer pure functions for indicators, sizing, signal combination, and risk calculations. Make time, prices, balances, and provider clients injectable for deterministic tests.
- Catch specific expected exceptions at infrastructure boundaries. Preserve context in logs without secrets. Never use a broad catch to turn an unsafe or unknown state into a trade.
- Use logging instead of `print` in production modules. Do not log API keys, authorization headers, account identifiers, or full provider payloads when they may contain secrets.
- Keep formatting/lint configuration centralized. Do not disable a rule inline without a narrow, documented reason.

## 4. Non-negotiable trading architecture

These are safety constraints, not suggestions. New code must make invalid states unrepresentable or reject them before execution.

### Risk and order invariants

1. **Maximum risk:** no trade may risk more than 1.0% of total account balance. Risk must be computed from account equity and the actual stop distance before order submission.
2. **Dynamic sizing:** calculate position size on every entry using the stop distance, equivalent to:
   `position_size = account_equity * 0.01 / abs(entry_price - stop_loss_price)`.
   Include instrument contract size, pip value, and broker minimum/maximum constraints in the typed implementation where applicable.
3. **Mandatory exits:** every order must include an attached hard stop-loss **and** take-profit before it reaches a broker adapter. Reject orders missing either exit, with invalid direction/price relationships, or with non-positive distance.
4. **Risk re-check:** revalidate balance, sizing, stop-loss, take-profit, spread, and broker constraints immediately before submission. Never rely only on an earlier strategy calculation.
5. **No bypasses:** the execution wrapper is the single gate for orders. No direct broker call, emergency path, retry path, notebook, or test helper may bypass its validations.
6. Support both `LONG` and `SHORT` positions with direction-correct exits. Account for dynamic bid-ask spread, slippage, and overnight swap rates in backtests and risk decisions.

### LLM contract

- Force JSON responses for every LLM call: `response_format={"type": "json_object"}` where the provider supports it.
- Validate the raw response with a strict Pydantic model before use. Reject malformed JSON, unknown fields, missing fields, non-finite values, and scores outside `-1.0..1.0`. Never parse sentiment from prose or regex fallback text.
- The LLM is qualitative analysis only. It never submits, modifies, or cancels an order and never overrides risk controls.
- Technical BUY signals may be executed only when validated LLM sentiment is greater than `+0.50`. A missing, stale, neutral, bearish, malformed, or unavailable sentiment result must not authorize a trade. Preserve the same conservative policy for any future signal-combination rule.
- Run LLM analysis on the intended higher timeframes (15m/1h) or scheduled hourly polls; do not add per-tick calls that create latency or uncontrolled API cost.

### Errors and state transitions

- **Default policy is REJECT/CLOSED:** provider errors, timeouts, disconnects, stale data, schema failures, unknown order state, missing configuration, and any unhandled exception must produce no order and no position increase.
- Retries may repeat safe reads only. Never blindly retry a non-idempotent order submission; reconcile broker state first and require an explicit idempotency key where supported.
- WebSocket drops and market-data gaps must mark data stale and block new entries until freshness is re-established.
- Record rejection reason, strategy/signal identifiers, latency, slippage, and provider error class without recording secrets.
- Never silently substitute defaults for risk, exits, account balance, sentiment, or paper/live mode.

## 5. Safety and environment policy

- **Paper trading first and by default.** Every local run, test, backtest, and development process must set and verify a paper/demo mode, for example:
  `PAPER_TRADING=true`, `LIVE_TRADING=false`, and the provider's demo environment/base URL.
- Startup must fail closed unless the paper-trading flag is explicitly true and live trading is explicitly false. Production code must not silently infer mode from missing variables.
- Live trading is prohibited by default. Do not add a live-account path, live endpoint, or flag that enables live money without an explicit reviewed change to the safety policy.
- Load credentials only from environment variables through `pydantic-settings`. Never hardcode API keys, tokens, passwords, private keys, account secrets, or signed URLs in source, tests, notebooks, fixtures, documentation, logs, or commits.
- `.env` is local-only and must be ignored. Maintain `.env.example` with variable names and non-secret placeholders; never copy real values into it.
- Never print or persist secrets. Scrub authorization headers and credential-like fields from exceptions and structured logs.
- Use least-privilege demo credentials and separate broker accounts for development. Rotate any credential that may have been exposed and report the exposure rather than masking it in code.
- When running in Compose, mount `.env` or use `env_file` at runtime. Never `COPY .env`, pass credentials through Dockerfile `ARG`, or bake secrets into image layers, labels, logs, or generated artifacts.
- Keep `.env` excluded by `.dockerignore`; use `.env.example` for safe placeholders only.
- Do not disable stops, risk limits, schema validation, paper-mode checks, or reject-on-error behavior to make a test or integration pass. Tests must use fake providers and deterministic fixtures.

## 6. Change workflow

1. Read `CONTEXT.md` and the affected module/tests before editing. Preserve its architectural decisions.
2. Trace all callers when changing exported models, configuration fields, risk calculations, or broker contracts. Update every caller in the same change.
3. Add or update behavior-focused tests for boundaries, rejection paths, sizing, exit invariants, strict JSON validation, and paper-mode enforcement. Do not test implementation details.
4. Run the narrowest relevant test or paper/backtest smoke path, then run `python -m mypy src`, `python -m ruff check .`, and `python -m ruff format --check .`.
5. Review the diff for secrets, live endpoints, missing exits, unsafe fallbacks, and accidental changes to historical-data fixtures before committing.

When a conflict exists, this file's safety constraints and `CONTEXT.md` take precedence over convenience, provider defaults, or an agent's assumptions.
