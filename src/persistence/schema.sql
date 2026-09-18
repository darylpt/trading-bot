CREATE TABLE IF NOT EXISTS trade_logs (
    client_order_id TEXT PRIMARY KEY,
    instrument TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('LONG', 'SHORT')),
    quantity NUMERIC NOT NULL CHECK (quantity > 0),
    entry_price NUMERIC NOT NULL,
    stop_loss_price NUMERIC NOT NULL,
    take_profit_price NUMERIC NOT NULL,
    account_equity NUMERIC NOT NULL,
    risk_fraction NUMERIC NOT NULL CHECK (risk_fraction <= 0.01),
    status TEXT NOT NULL CHECK (status IN ('PENDING_SUBMISSION', 'PENDING', 'FILLED', 'PARTIALLY_FILLED', 'ACCEPTED', 'REJECTED', 'CANCELLED', 'EXPIRED', 'UNKNOWN')),
    environment TEXT NOT NULL CHECK (environment IN ('PAPER', 'DEMO')),
    rejection_reason TEXT,
    opened_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS execution_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_order_id TEXT,
    instrument TEXT NOT NULL DEFAULT 'UNKNOWN',
    strategy_name TEXT NOT NULL DEFAULT 'ema_crossover',
    event_type TEXT NOT NULL,
    provider TEXT NOT NULL,
    error_class TEXT,
    message TEXT NOT NULL,
    decision_rationale TEXT,
    reference_price NUMERIC,
    fast_average NUMERIC,
    slow_average NUMERIC,
    distance_to_crossover NUMERIC,
    latency_ms INTEGER,
    slippage NUMERIC,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS session_metrics (
    session_date TEXT PRIMARY KEY,
    broker_latency_total_ms INTEGER NOT NULL CHECK (broker_latency_total_ms >= 0),
    broker_latency_samples INTEGER NOT NULL CHECK (broker_latency_samples >= 0),
    news_blackout_hits INTEGER NOT NULL CHECK (news_blackout_hits >= 0),
    realized_pnl NUMERIC NOT NULL DEFAULT 0,
    closed_trades INTEGER NOT NULL DEFAULT 0 CHECK (closed_trades >= 0),
    winning_trades INTEGER NOT NULL DEFAULT 0 CHECK (winning_trades >= 0),
    losing_trades INTEGER NOT NULL DEFAULT 0 CHECK (losing_trades >= 0),
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS positions (
    position_id TEXT PRIMARY KEY,
    client_order_id TEXT NOT NULL UNIQUE,
    instrument TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('LONG', 'SHORT')),
    quantity NUMERIC NOT NULL CHECK (quantity > 0),
    entry_price NUMERIC NOT NULL CHECK (entry_price > 0),
    stop_loss_price NUMERIC NOT NULL CHECK (stop_loss_price > 0),
    take_profit_price NUMERIC NOT NULL CHECK (take_profit_price > 0),
    status TEXT NOT NULL CHECK (status IN ('OPEN', 'CLOSED')),
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    exit_price NUMERIC,
    exit_reason TEXT,
    realized_pnl NUMERIC NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS circuit_breaker_status (
    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
    status TEXT NOT NULL CHECK (status IN ('CLEAR', 'HALTED')),
    reason TEXT NOT NULL,
    candle_timestamp TEXT,
    spread NUMERIC,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runtime_controls (
    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
    trading_halted INTEGER NOT NULL CHECK (trading_halted IN (0, 1)),
    reason TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
