# Phase 0 Baseline Manifest

**Status:** Implemented locally; broker-demo verification remains blocked.

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
| `env PYTHONPATH=src .venv/Scripts/python.exe -m pytest tests/integration/test_forward_test_readiness.py tests/integration/test_execution_gate.py tests/integration/test_execution_boundary.py -q` | `9 passed` |
| Broker read-only readiness smoke | Pass after sequential bridge-call latency fix: account equity `10000.0`, session open, spread `0.260`, `32` candles, database writable, clock drift `0.001568` seconds. |
| Controlled demo lifecycle smoke | Four authorized `0.01` LONG submissions with attached exits did not produce a protected fill; the latest returned HTTP `503`/`UNKNOWN`, no open position was found, and reconciliation remained unavailable. | Blocked |
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
| Human acceptance review | Required operator review of the Phase 0 scenarios and credential-rotation evidence. | Pending |
| Native MT5 process | `terminal64.exe` observed at `C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe`. | Pass |
| Bridge health | Updated bridge authenticated and reached its ready state with `Exness-MT5Trial17`; the complete read-only readiness check passed after accounting for bridge-call latency. | Pass |
| Broker read operations | Account, metadata, session, quote, and `32`-candle history reads pass; broker backfill preserves known session/maintenance gaps without synthesis. | Pass |

## Current environment probe

- Native `MetaTrader5` binding directly sees the verified broker symbol `XAUUSDm`; `symbol_info_tick` and `copy_rates_from_pos` return data after symbol selection.
- The updated bridge authenticates with the `.env` credentials and is listening on the configured port.
- The complete readiness probe passed with a sub-second account clock-drift measurement; order submission remains blocked by the protected-fill failure evidence.
- Current corrective action: expose sanitized `order_check`/`order_send` phase diagnostics and distinguish authoritative `ORDER_NOT_FOUND` reconciliation from transport failure before any further submission.

## Known limitations and blockers

- Protected demo order, position reconciliation, safe closure, and forward-test evidence remain unavailable; the controlled submission was halted safely in UNKNOWN state.
- Credential rotation and human acceptance sign-off remain operator-owned gates.
- Docker build and in-image credential inspection passed for the local `trading-bot-test` image.
- This record proves local simulation and a partial live bridge probe only. It does not yet prove broker-connected paper trading and does not enable live trading.

