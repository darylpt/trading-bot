# Phase 0 Specification — Secure and Freeze the Baseline

## Status

**Implemented locally.** Local safety checks pass. The verified broker symbol is `XAUUSDm`; the rotated credentials authenticate the bridge and read-only demo readiness passes. Protected demo order/reconciliation evidence remains blocked because order submission returns sanitized `UNKNOWN`/HTTP `503` without phase-specific MT5 diagnostics; operator acceptance review and forward-test evidence remain required for verification.

The repository now uses one explicit Exness MT5 Option B host-bridge contract. `SIMULATED` remains local-only; `BROKER_DEMO` never falls back to local data; `LIVE` remains rejected.

This creates three risks:

1. A simulated run can be mistaken for broker-connected paper trading.
2. Documentation can direct implementation toward conflicting architectures.
3. A future session can make changes without a single authoritative contract.

## Decision

The first broker-demo architecture is **Exness MT5**, using an explicitly documented demo adapter behind the standard `BaseBroker` abstraction.

The first release will support:

- `SIMULATED`: deterministic local market data and local `SimulatedBroker` paper fills.
- `BROKER_DEMO`: Exness MT5 demo market data, account reads, demo orders, and reconciliation through `ExnessMT5Broker`.
- `LIVE`: rejected and unavailable by default.

OANDA is discarded for this project because of regional/account friction and lack of support for the required local payment rails. Do not add, restore, or document OANDA implementation work.

The selected deployment mechanism is **Option B: native Windows host bridge**. Docker Desktop app/test containers connect to an operator-run HTTPS MT5 bridge at `EXNESS_BRIDGE_HOST` (default `host.docker.internal`) and `EXNESS_BRIDGE_PORT` (default `18812`). The bridge hosts the Exness MT5 terminal and exposes the typed JSON-RPC contract; no Wine sidecar is introduced. This is an architecture decision, not evidence that Exness connectivity has already been validated.

### Security baseline

- Use least-privilege environment injection: the test service receives no broker/LLM credentials; the dashboard receives only its required database/UI settings; PostgreSQL receives only its own credentials; the application receives only runtime credentials it needs.
- Bind the dashboard to loopback by default or protect it with authenticated, authorized operator access. Runtime-control actions require explicit authorization and audit logging.
- Define an exact provider-to-endpoint allowlist, require HTTPS for broker endpoints, reject look-alike hosts, userinfo, arbitrary redirects, and unsafe ports, and attach credentials only after endpoint validation.
- Ensure endpoint validation parses scheme, hostname, port, and path rather than using substring markers.
- Keep all local, test, and container execution explicitly paper/demo-only.
- Reject contradictory, missing, ambiguous, or unsupported mode/provider configuration.
- Prohibit automatic fallback from `BROKER_DEMO` to `SIMULATED`.
- Keep local simulation reproducible with pinned dependencies, deterministic fixtures, and recorded commands.

### Runtime baseline

- Define explicit `SIMULATED`, `BROKER_DEMO`, and unavailable `LIVE` modes.
- Make provider, mode, endpoint, account readiness, data source, and halt reason observable without secrets.
- Prohibit automatic fallback from `BROKER_DEMO` to `SIMULATED`.
- Keep local simulation reproducible with pinned dependencies, deterministic fixtures, and recorded commands.

### Documentation baseline

- Make this specification the Phase 0 contract.
- Make `docs/PAPER-TRADING-READINESS.md` the broker-demo readiness contract.
- Make `docs/SPEC-DRIVEN-DEVELOPMENT.md` the implementation workflow.
- Reconcile `CONTEXT.md`, `ARCHITECTURE.md`, `ROADMAP.md`, `docs/PLAN.md`, `docs/TECHNICAL-SPEC.md`, and `docs/PRODUCT-SPEC.md` with the selected architecture and current implementation.
- Remove or clearly label stale claims, planned files, duplicate phase maps, and unsupported provider behavior.

### Baseline freeze

Record a baseline manifest containing:

- Source revision or immutable change identifier.
- Python version.
- Dependency lock/input and image identifier.
- Configuration mode and provider, with secrets omitted.
- Fixture/data file checksums.
- Database schema/version.
- Commands executed and their results.
- Known limitations and intentionally unimplemented paths.

## Non-goals

- No live trading capability.
- No Exness MT5 broker order submission.
- No new trading strategy.
- No increase to risk limits.
- No OANDA implementation or compatibility path.
- No silent compatibility aliases for old provider/mode names.
- No claim of broker connectivity based on local simulation or fake-provider tests.

## Invariants

1. `LIVE` cannot start or submit orders.
2. `BROKER_DEMO` requires explicit Exness MT5 demo configuration and never uses local fallback data.
3. `SIMULATED` never contacts a broker and uses only `SimulatedBroker`.
4. All broker implementations conform to the standard `BaseBroker` abstraction.
5. No credential is committed, logged, persisted, or copied into an image layer.
6. Every order path remains behind the single execution gate.
7. No order can be considered safe without a hard stop-loss and take-profit.
8. Missing, stale, malformed, contradictory, or unknown state fails closed.
9. Documentation status reflects evidence, not code presence.
10. The local simulation baseline remains reproducible after documentation/configuration changes.
11. A future session can locate and read the governing specifications and TODO list from `AGENTS.md`.

## Required deliverables

- This Phase 0 specification.
- One canonical provider/mode configuration model.
- Reconciled documentation with no contradictory provider or phase claims.
- Secret-hygiene evidence and safe environment template.
- Explicit runtime-mode behavior and startup rejection rules.
- Reproducible local simulation baseline.
- Baseline manifest with command results.
- A Phase 0 status entry linked to its evidence.

## Acceptance scenarios

### A. Secret hygiene

Given a repository checkout and build context, `.env`, credentials, databases, caches, logs, and VCS metadata are excluded from image/context artifacts. Given application logs and persistence records, no credential-like value or authorization header is present.

### B. Mode separation

Given `SIMULATED`, the application uses only deterministic local data and `SimulatedBroker`. Given `BROKER_DEMO` without complete Exness MT5 settings, startup rejects. Given `BROKER_DEMO` with a provider outage, the application halts new entries and does not use simulated data. Given `LIVE`, startup rejects.
### C. Configuration consistency

Given any documented default or required variable, `Settings`, `.env.example`, Compose, architecture documentation, roadmap, and specifications agree on its meaning. No document describes the same mode as both broker-connected and simulated.
### D. Documentation consistency

Given a new session, the startup rules in `AGENTS.md` lead to this specification, the readiness specification, and the SDD workflow. Given the legacy planning documents, each is either reconciled, explicitly marked historical, or linked to the canonical contract.

### E. Reproducible baseline

Given the recorded environment and safe configuration, the local simulation produces the same observable strategy/risk/execution result and database schema outcome. The baseline record includes commands, outputs, checksums, and known limitations without secrets.

### F. Safety review

A review finds no live endpoint, live enablement path, silent broker-demo fallback, direct broker bypass, unprotected order path, credential exposure, or roadmap completion claim unsupported by evidence.

## Evidence required for completion

- Secret scan/report with values redacted.
- Docker build-context and image inspection result.
- Configuration matrix covering every mode/provider combination.
- Local simulation smoke result.
- Required Python compile, test, type, lint, format, and Compose results.
- Documentation cross-reference review.
- Baseline manifest committed without secrets.
- Human review of the Phase 0 acceptance scenarios.

## Exit criteria

Phase 0 is **verified** only when all required deliverables exist, all acceptance scenarios pass, the baseline manifest is recorded, documentation is internally consistent, and the roadmap links to the evidence. Until then, Phase 0 remains **proposed** or **implemented but unverified**.

## Next-session todo queue

Execute these in order:

1. Re-read `AGENTS.md`, `CONTEXT.md`, this specification, `docs/PAPER-TRADING-READINESS.md`, and `docs/SPEC-DRIVEN-DEVELOPMENT.md`.
2. Audit and rotate any exposed credentials; record redacted evidence.
3. Decide and document the exact Exness MT5 demo transport, endpoint/terminal allowlist, and credential-injection policy.
4. Define the `BaseBroker`, `SimulatedBroker`, and `ExnessMT5Broker` contract and mode/provider matrix.
5. Reconcile remaining OANDA references and stale provider, phase, and planned-file claims.
6. Specify least-privilege Compose environment injection for app, test, dashboard, and PostgreSQL.
7. Specify dashboard binding, authorization, and write-access policy.
8. Specify endpoint parsing, HTTPS, redirect, host, port, and authorization-header protections.
9. Freeze the reproducible local `SimulatedBroker` baseline and record its manifest.
10. Run the Phase 0 acceptance checks and record evidence.
11. Mark Phase 0 `implemented but unverified` or `verified`; never mark it complete without all evidence.
12. Only after Phase 0 verification may the Phase 1 executable slice be revalidated and implemented; its contract is already recorded in `docs/PHASE-1-BROKER-READINESS-SPEC.md`.

## Next phase

Only after Phase 0 verification may implementation begin for Exness MT5 broker-demo read-only readiness. Phase 1 must use the selected Exness MT5 architecture and `BaseBroker` contract; it must not introduce OANDA code or reopen provider selection without a documented architecture decision and impact review.
## Executable slice contract — P0-S1 baseline freeze

**Status:** Implemented locally; external credential and broker-demo acceptance evidence remain blocked.

### Problem and scope

The repository must have one fail-closed configuration and documentation baseline before broker-demo work starts. This slice covers the canonical mode/provider model, credential hygiene, endpoint policy, Compose injection, documentation reconciliation, and a reproducible `SIMULATED` run.

### Ownership and interfaces

- **Configuration:** `src/config/settings.py`, `src/trading_bot/runtime_config.py`, `.env.example`.
- **Broker boundary:** `src/execution/protocols.py`, `src/execution/broker_adapter.py`, `src/trading_bot/broker/mt5.py`.
- **Runtime and readiness:** `src/trading_bot/__main__.py`, `src/observability/readiness.py`.
- **Persistence and evidence:** `src/persistence/schema.sql`, `src/persistence/sqlite.py`, and a redacted baseline manifest under `docs/`.
- **Runtime policy:** `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `TODO.md`, `ROADMAP.md`, and governing specifications.
- Inputs are environment variables and safe fixtures; outputs are typed settings, explicit startup rejection, sanitized readiness, and recorded baseline evidence.

### State transitions

`UNCONFIGURED → REJECTED` for missing/ambiguous/contradictory settings; `SIMULATED_CONFIGURED → SIMULATED_READY → SIMULATED_RUNNING`; `BROKER_DEMO_CONFIGURED → BROKER_DEMO_REJECTED` until the selected Exness transport is configured; `LIVE_REQUESTED → REJECTED`. A provider failure transitions `BROKER_DEMO_READY → HALTED`, never to simulation.

### Failure and security policy

Reject unknown modes/providers, unsafe endpoints, invalid paper/live flags, missing required credentials, invalid numeric limits, and missing mandatory exits. Parse and allowlist scheme, host, port, and path before attaching credentials. Tests receive no broker or LLM secrets. Logs, databases, images, and build context must contain no credentials, authorization headers, or signed URLs. No fallback or compatibility alias is permitted.

### Acceptance scenarios

1. `SIMULATED` runs only deterministic fixtures and `SimulatedBroker`; a broker spy records zero calls.
2. Incomplete `BROKER_DEMO` and every `LIVE` configuration fail startup with a non-secret reason.
3. A broker-demo outage produces `HALTED` and zero simulated-data reads.
4. `.env`, credentials, databases, caches, logs, and VCS metadata are absent from build context and image layers.
5. Repeating the safe simulation with the same revision, dependencies, fixtures, schema, and configuration produces the same observable result.

### Verification

- `python -m compileall -q src tests`
- `python -m pytest tests/unit/test_settings.py tests/unit/test_runtime_config.py tests/unit/test_broker_adapter.py tests/unit/test_container_runtime.py`
- `python -m pytest tests/integration/test_execution_boundary.py tests/integration/test_paper_execution.py`
- `python -m mypy src`
- `python -m ruff check .`
- `python -m ruff format --check .`
- `docker compose build` and `docker compose run --rm test pytest -q`
- A local `python -m trading_bot --once` smoke run with explicit paper mode, plus secret-scan, image/context inspection, documentation cross-reference review, and a redacted baseline manifest.

### Exit gate

Phase 0 remains implemented locally but unverified until every applicable acceptance scenario and required external/demo evidence has been recorded. The local command and artifact evidence is archived in `docs/PHASE-0-BASELINE-MANIFEST.md`. Only then may the Phase 1 slice be implemented or operationally relied upon.
