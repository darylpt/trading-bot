# Spec-Driven Development Workflow

## Purpose

This repository uses Spec-Driven Development (SDD) for safety-sensitive trading changes. Specifications define behavior and acceptance evidence before implementation begins. Code is complete only when it satisfies the specification and its verification gates.

The canonical domain readiness specification is `docs/PAPER-TRADING-READINESS.md`. This document defines how that specification is used.

## Specification hierarchy

Resolve conflicts in this order:

1. `AGENTS.md` — repository safety invariants and engineering rules.
2. `CONTEXT.md` — project decisions and non-negotiable architecture.
3. `docs/PAPER-TRADING-READINESS.md` — broker-demo readiness contract.
4. `ARCHITECTURE.md` — runtime boundaries and data ownership.
5. `ROADMAP.md` — phase ordering and delivery status.
6. Module-level specifications and tests.

A lower-level document MUST NOT weaken a higher-level safety rule.

## Specification depth strategy

Create high-level contracts for all remaining phases before implementation so dependencies, safety boundaries, and evidence expectations are visible. Detailed executable slice contracts may be created now, but must be revalidated immediately before implementation if prerequisite evidence or provider decisions change.

Use two levels:

1. **Strategic phase contract:** purpose, problem, scope, non-goals, invariants, dependencies, interfaces, acceptance evidence, exit criteria, and unresolved decisions.
2. **Executable slice contract:** exact modules, state transitions, failure policy, test scenarios, smoke scenario, and verification commands. Each phase specification now contains one; its status remains proposed until implementation evidence passes.

Phase 0 must be verified before implementing Phase 1. A later phase may be documented as `Proposed` or `TBD`, but it must not be marked implemented or verified without evidence.

Planned strategic and executable phase contracts:

- `docs/PHASE-0-SECURE-BASELINE-SPEC.md` — proposed and currently active; executable slice P0-S1 created.
- `docs/PHASE-1-BROKER-READINESS-SPEC.md` — proposed; executable slice P1-S1 created and pending Phase 0 verification.
- `docs/PHASE-2-MARKET-DATA-SPEC.md` — proposed; executable slice P2-S1 created and pending Phase 1 verification.
- `docs/PHASE-3-BROKER-AWARE-RISK-SPEC.md` — proposed; executable slice P3-S1 created and pending Phases 1–2 verification.
- `docs/PHASE-4-DEMO-EXECUTION-SPEC.md` — proposed; executable slice P4-S1 created and pending Phases 1–3 verification.
- `docs/PHASE-5-RECONCILIATION-RECOVERY-SPEC.md` — proposed; executable slice P5-S1 created and pending Phase 4 verification.
- `docs/PHASE-6-OPERATIONAL-HARDENING-SPEC.md` — proposed; executable slice P6-S1 created and pending Phases 1–5 verification.
- `docs/PHASE-7-FORWARD-TEST-SPEC.md` — proposed; executable slice P7-S1 created and pending Phases 1–6 verification.

## Required SDD cycle

Every non-trivial change follows this cycle:

1. **State the problem** — identify the current unsafe, missing, or incorrect behavior.
2. **Write the contract** — define inputs, outputs, state transitions, failure behavior, and security constraints.
3. **Define acceptance evidence** — specify the smoke scenario, test, diagnostic, or operational observation that proves the contract.
4. **Trace ownership** — identify the authoritative module, all callers, persistence records, configuration, and documentation affected.
5. **Implement the smallest vertical slice** — complete the path end-to-end before adding abstractions or adjacent features.
6. **Verify failure first** — prove unsafe, stale, malformed, unavailable, and ambiguous states reject or halt correctly.
7. **Verify the happy path** — prove the intended observable behavior using deterministic fixtures or a controlled demo environment.
8. **Run required quality checks** — use the repository's Python, test, type, lint, format, and container checks.
9. **Update the specification status** — mark the roadmap and readiness evidence accurately; never claim completion based on code existence alone.
10. **Review the change** — check secrets, live endpoints, bypasses, unsafe fallbacks, persistence, restart behavior, and all affected callers.

## Contract format

Each implementation slice should record:

- **Problem:** what is wrong and why it matters.
- **Scope:** exact modules, interfaces, configuration, and persistence affected.
- **Invariants:** conditions that must always hold.
- **State transitions:** allowed states and transitions, including unknown/error states.
- **Failure policy:** reject, halt, reconcile, close safely, or retry a safe read.
- **Security policy:** credential handling, endpoint restrictions, redaction, and mode constraints.
- **Acceptance scenarios:** observable examples for success and failure.
- **Evidence:** command, smoke test, or recorded demo result.
- **Status:** proposed, implemented, verified, or blocked.

## Trading implementation slices

Implement readiness in this order:

1. Credential hygiene and explicit runtime modes.
2. One selected broker architecture and read-only readiness.
3. Broker historical data, quote validation, and restart-safe candle state.
4. Broker-aware account, instrument, and risk calculations.
5. Rejection-only order planning through the execution gate.
6. Small demo order submission with confirmed protective exits.
7. Idempotency, partial/unknown order handling, and reconciliation.
8. Restart recovery and operational readiness/alerts.
9. Failure-injection verification.
10. Multi-day controlled demo forward test.

Each slice must preserve the local simulation path and must not silently substitute it for a broker-demo path.

## Test and evidence policy

Tests defend observable contracts, not implementation details. Add or retain a test only when a plausible regression would fail it.

Required coverage includes:

- Safe and invalid configuration.
- Provider response validation.
- Stale, discontinuous, crossed, and wide-spread market data.
- Clock drift and market-session boundaries.
- Idempotency, partial fills, unknown states, and reconciliation.
- Restart recovery and persistence failures.
- Secret and authorization-header redaction.

Deterministic tests prove logic. An opt-in demo smoke test proves provider integration. A multi-day forward test proves operational behavior. None substitutes for the others.

## Definition of done

A change is done only when:

- The specification and implementation agree.
- All affected callers and persistence contracts are updated.
- Unsafe and unavailable states fail closed.
- The narrowest relevant verification passes.
- Required repository quality checks pass.
- Demo behavior is proven separately when broker connectivity is involved.
- Documentation and roadmap status reflect actual evidence.
- No live-money path, secret, fallback bypass, or unverified claim was introduced.

## Session-start rule

At the start of every new session, read `AGENTS.md`, `CONTEXT.md`, `TODO.md`, `docs/PHASE-0-SECURE-BASELINE-SPEC.md`, `docs/PAPER-TRADING-READINESS.md`, and this document before changing or assessing broker connectivity, market data, execution, risk, sentiment, persistence, observability, Docker runtime, or readiness.
