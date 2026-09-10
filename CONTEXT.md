Here is a production-ready **`CONTEXT.md`** file summarizing the discussion, technical decisions, architectural paradigm, and roadmap for the Hybrid Forex Trading Bot project. You can commit this file directly to the root of your project repository.

---

# `CONTEXT.md`

```markdown
# Project Context: Hybrid Algorithmic & AI Forex Trading System

This document serves as the persistent context memory for developers, collaborators, and AI coding agents (Cursor, Claude Code, Copilot) working in this workspace.

---

## 1. Executive Summary & Goals

* **Project Scope:** A hybrid algorithmic Forex trading system that pairs a deterministic quantitative execution engine (technical indicators, dynamic risk management) with an LLM qualitative analyst (financial news parsing, macroeconomic sentiment scoring).
* **Primary Objective:** Build a risk-managed, educational, semi-automated trading system that acts as an "R&D laboratory" for an engineer learning quantitative finance and market mechanics.
* **Core Philosophy — "Build the Brain, Reuse the Skeleton":**
  * **REUSE:** Open-source infrastructure for broker APIs, order routing, WebSocket data streams, and backtesting metrics.
  * **BUILD:** Custom technical indicator strategy classes, dynamic position-sizing engines, and LLM news sentiment pipelines.

---

## 2. Technical Stack & Tooling

| Component | Technology / Library | Purpose |
| :--- | :--- | :--- |
| **Primary Language** | Python 3.11+ | Data science, strategy scripting, and API integrations. |
| **Execution Framework** | Backtrader / Freqtrade | Time-series backtesting, event loops, dry-run paper trading. |
| **Market Data Processing** | `pandas`, `numpy`, `pandas-ta` | Technical indicator calculations (RSI, Moving Averages, ATR). |
| **Qualitative / AI Layer** | OpenAI API (`gpt-4o-mini`) / Ollama | News headline analysis and structured JSON sentiment scoring. |
| **Broker Integration** | OANDA v20 REST API / MetaTrader 5 | Paper trading execution, WebSocket live price streams. |
| **Project Configuration** | `.env` + `pydantic-settings` | Secure API key storage and environment management. |
| **Container Runtime** | Docker / Docker Compose | Isolated, reproducible development, testing, and continuous background execution. |
| **Base Image** | Python 3.11-slim | Minimal, deterministic runtime image for the strategy engine and workers. |

---

## 3. System Architecture & Component Interaction

```

[ Financial News Feeds ]                 [ Market Price Feeds (OHLC) ]
(News API / Forex Factory)                    (OANDA / MT5 WebSocket)
│                                            │
▼                                            ▼
┌──────────────────────────┐                ┌──────────────────────────┐
│   LLM Sentiment Module   │                │   Quantitative Engine    │
│  (Structured JSON Output)│                │  (Backtrader / Freqtrade)│
└───────────┬──────────────┘                └────────────┬─────────────┘
│                                            │
│ Score: -1.0 to +1.0                        │ Technical Signals
└─────────────────────┬──────────────────────┘ (RSI, ATR, MA)
│
▼
┌──────────────────────────┐
│     Risk Management      │
│  (1% Risk/Trade Rule,    │
│   Dynamic Lot Sizing)    │
└─────────────┬────────────┘
│
▼
┌──────────────────────────┐
│   Broker Order Execution │
│   (Paper Trading Account)│
└──────────────────────────┘

```

---

## 4. Key Architectural Decisions & Trading Rules

### A. Non-Negotiable Risk Management (The Circuit Breaker)
1. **The 1% Rule:** No trade can risk more than 1.0% of total account balance.
2. **Dynamic Position Sizing:** Lot size is calculated dynamically on every entry signal:
   $$\text{Position Size} = \frac{\text{Account Equity} \times 0.01}{|\text{Entry Price} - \text{Stop Loss Price}|}$$
3. **Hard Stop-Losses:** Every order placed via the broker API **must** contain an attached hard stop-loss. Orders without stop-losses are rejected by the execution wrapper.

### B. LLM Sentiment Integration Strategy
1. **Deterministic Contracts:** LLMs must never output free-form text to the strategy. They must use forced JSON mode (`response_format={"type": "json_object"}`).
2. **Sentiment Filtering:** The LLM does not execute trades directly. It outputs a score between `-1.0` (Extreme Bearish) and `+1.0` (Extreme Bullish). Technical BUY signals are executed only if LLM Sentiment > `+0.50`.
3. **Latency Mitigation:** LLMs operate on higher timeframes (15m, 1h) or scheduled hourly polls to avoid latency bottlenecks and high API polling costs.

### C. Market Context Differences (PSE vs. Global Forex)
* **24/5 Execution:** Forex operates continuously; code must handle weekend roll-overs and spread expansions during market open/close.
* **Two-Way Trading:** Support for both `LONG` and `SHORT` positions.
* **Friction Awareness:** All backtests must incorporate dynamic bid-ask spreads, slippage models, and overnight swap rates.
### D. Containerized Runtime & Persistence
* **Containerized Execution:** The strategy engine and continuous background workers run inside Docker containers so Python and native dependencies are isolated and reproducible.
* **Service Orchestration:** Docker Compose manages the application/worker services, paper-trading runtime, and supporting database service.
* **Persistent Logs:** SQLite data/log directories and PostgreSQL data/log volumes must be mounted as persistent volumes so container replacement does not discard execution metrics, slippage, latency errors, sentiment scores, or news records.
* **Runtime Configuration:** Environment files and credentials are supplied at container runtime; secrets must never be baked into image layers.

---

## 5. Phased Implementation Roadmap

* [x] **Phase 0: Environment & Architecture Setup**
  * Establish workspace configuration (`AGENTS.md` / `.cursorrules`).
  * Set up virtual environment and core dependencies (`backtrader`, `pandas`, `openai`).
* [ ] **Phase 1: Backtesting Foundation**
  * Load historical 15m EUR/USD candle data from CSV.
  * Build technical indicator strategy (RSI + Moving Average Crossover).
* [ ] **Phase 2: Custom Risk Engine**
  * Implement dynamic lot-sizing calculator.
  * Implement Average True Range (ATR) based trailing stop-losses.
* [ ] **Phase 3: LLM Sentiment Integration**
  * Build news fetcher module (Financial news headlines API).
  * Build OpenAI / Ollama structured JSON prompt classifier.
* [ ] **Phase 4: Paper Trading Pipeline**
  * Connect strategy engine to OANDA / MT5 Demo API.
  * Log execution metrics, slippage, and latency errors to SQLite database.

---

## 6. Developer Guidelines & Non-Negotiables

1. **Never Commit API Keys:** All broker and OpenAI keys must reside strictly in `.env`.
2. **Paper Trading First:** Never execute live money trades. The initial system must run in a simulated paper-trading environment.
3. **Validate Infrastructure Code:** Ensure exception handling around API connection timeouts, WebSocket drops, and non-deterministic LLM JSON payloads.

```