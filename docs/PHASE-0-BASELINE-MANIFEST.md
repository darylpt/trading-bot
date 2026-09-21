# Phase 0 Baseline Manifest

**Status:** Phase 0 verified against the recorded local, bridge, and operator-acceptance evidence. Phase 7 remains in progress and is governed by its separate exit gate.

This manifest records the reproducible local `SIMULATED` baseline. It contains no credentials, authorization headers, account passwords, or raw provider payloads. Timestamps and SQLite metadata are expected to vary between runs; the strategy, risk, execution, and schema outcomes are the reproducible contract.

## Source and inputs

| Item | Value |
| --- | --- |
| Source revision | `cd5ea4175e3060b99a3708b6f4cb54ba59ca6b7f` (base HEAD; working tree contains bridge implementation and evidence updates) |
| Python | `3.11.15` |
| Docker engine | `28.4.0` |
| Dependency input | `requirements.txt` SHA-256 `5f616b44efb44d3edc85aee693d172615536afed2e6a12f57fa36bb4bef76b7c` |
| Fixture input | `data/market_data.csv` SHA-256 `9a86ef38628db59f41cc177694474bdde847f5b5ee03570788a49390bfd2bdf7` |
| Schema input | `src/persistence/schema.sql` SHA-256 `c8dcf2b31d2cb38c21ccdb730c7dd9589608c149a1fc0f34765b9456f26443d3` |
| Database schema version | Source-managed `schema.sql`; no separate numeric migration version exists |
| Base image | `python:3.11-slim`, digest observed during build: `sha256:9534e5a8e315485d4061ed659af0fd78a284c015f9b73661b41d6bab25604534` |
| Local image | `trading-bot-test`, image ID `sha256:5983be05fb8c2c8df3384ac275bfd0d63aaa112de4725eb292ded1c0160daa3b` |

The source revision is recorded separately from the working-tree changes because the bridge implementation, dependency update, and this manifest are not committed at capture time.

## Safe runtime configuration

- `PAPER_TRADING=true`
- `LIVE_TRADING=false`
- `TRADING_MODE=SIMULATED`
- `BROKER_ENV=demo`
- `BROKER_PROVIDER=exness_mt5`
- `INSTRUMENT=XAUUSDm`
- `EXNESS_SYMBOL_SUFFIX=m`
- Deterministic fixture data under a temporary data directory
- Temporary SQLite database and logs
- No broker or LLM credentials supplied

## Evidence

| Check | Result |
| --- | --- |
| `.venv/Scripts/python.exe --version` | `Python 3.11.15` |
| `.venv/Scripts/python.exe -m compileall -q src tests` | Pass |
| `.venv/Scripts/python.exe -m pytest -q` | `131 passed, 1 skipped` |
| `.venv/Scripts/python.exe -m mypy src` | Pass; 52 source files |
| `.venv/Scripts/python.exe -m ruff check .` | Pass |
| `.venv/Scripts/python.exe -m ruff format --check .` | Pass; all checked files already formatted |
| `docker compose config --quiet` | Pass |
| `docker compose build test` | Pass; image `trading-bot-test` built |
| `docker compose run --rm test pytest -q` | `131 passed, 1 skipped` |
| Host corrective-slice verification | `138 passed, 1 skipped`; mypy passed; Ruff check and format checks passed. |
| `env PYTHONPATH=src .venv/Scripts/python.exe -m pytest tests/integration/test_forward_test_readiness.py tests/integration/test_execution_gate.py tests/integration/test_execution_boundary.py -q` | `9 passed` |
| Broker read-only readiness smoke | Pass after sequential bridge-call latency fix: account equity `10000.0`, session open, spread `0.260`, `32` candles, database writable, clock drift `0.001568` seconds. |
| Controlled demo lifecycle smoke | Pass; protected `0.01` LONG `XAUUSDm` order returned retcode `10009`, attached SL/TP were confirmed, the position reconciled and closed, and no residual broker position remained. |
| `docker run --rm --entrypoint sh trading-bot-test -c 'test ! -e /app/.env && test ! -e /app/mt5_bridge_key.pem && test ! -e /app/mt5_bridge_cert.pem'` | Pass; credential artifacts absent |
| `PYTHONPATH=src ... .venv/Scripts/python.exe -m trading_bot --once` | Pass; explicit runtime reported `mode=SIMULATED provider=exness_mt5` and exited without an order |
| `PYTHONPATH=src .venv/Scripts/python.exe -m persistence --database .tmp-baseline-data/session_metrics.db --query` | Pass; one persisted session row with zero closed trades and zero realized P&L |
| `git ls-files .env mt5_bridge_key.pem mt5_bridge_cert.pem` | No tracked credential artifacts returned |
| Container build-context policy | `.dockerignore` excludes `.env`, `*.pem`, `*.key`, and `*.crt`; image inspection confirmed the local certificate/key artifacts are absent |

The first host smoke invocation without `PYTHONPATH=src` failed because the source tree is not installed into the host virtual environment. The successful evidence uses the repository's explicit source path; the Dockerfile sets the equivalent `PYTHONPATH=/app/src`.
## Acceptance evidence status

| Gate | Current evidence | Status |
| --- | --- | --- |
| Configuration matrix | `tests/unit/test_settings.py` covers safe simulated mode, valid broker-demo mode, invalid account/endpoint, and unavailable live mode. | Recorded |
| Documentation cross-reference | `AGENTS.md`, `CONTEXT.md`, this manifest, the readiness contract, SDD workflow, and roadmap were reviewed against the selected Exness Option B architecture. | Recorded |
| Human/operator acceptance | Operator explicitly authorized bridge restart, fresh readiness verification, and controlled forward-test startup; no live-money path authorized. | Recorded |
| Native MT5 process | `terminal64.exe` observed and active during the fresh readiness check. | Pass |
| Bridge health | Fresh broker-demo readiness after restart: terminal connected and authorized; XAUUSDm account, session, quote, history, database, and clock checks passed. | Pass |
| Broker read operations | Fresh check returned account equity `9999.14`, spread `0.260`, `256` candles, and clock drift `0.001s`; no broker positions were open. | Pass |

## Current environment probe

- Native `MetaTrader5` binding directly sees the verified broker symbol `XAUUSDm`; `symbol_info_tick` and `copy_rates_from_pos` return data after symbol selection.
- The restarted bridge authenticates with the current `.env` demo credentials and is listening on the configured port.
- The previously recorded fresh read-only readiness passed with a sub-second account clock-drift measurement and no broker positions; later startup probes correctly halted on provider quote/candle freshness and did not submit an order.
- The runtime previously persisted `XAUUSDm` as the active broker-demo instrument and kept the circuit breaker clear when quote/data freshness passed; the current provider freshness incident is tracked in the Phase 2 and Phase 7 specifications.

## Known limitations and blockers

- Multi-day forward-test evidence, restart/recovery evidence, and final operator go/no-go review remain in progress.
- Optional external HTTPS webhook delivery is not configured; structured sanitized logs are the active alert route.
- This record proves the Phase 0 baseline and current broker-demo evidence; it does not enable live trading.

