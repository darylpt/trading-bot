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
    status TEXT NOT NULL CHECK (status IN ('ACCEPTED', 'REJECTED', 'UNKNOWN')),
    environment TEXT NOT NULL CHECK (environment IN ('PAPER', 'DEMO')),
    rejection_reason TEXT,
    opened_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS execution_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_order_id TEXT,
    event_type TEXT NOT NULL,
    provider TEXT NOT NULL,
    error_class TEXT,
    message TEXT NOT NULL,
    latency_ms INTEGER,
    slippage NUMERIC,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS session_metrics (
    session_date TEXT PRIMARY KEY,
    broker_latency_total_ms INTEGER NOT NULL CHECK (broker_latency_total_ms >= 0),
    broker_latency_samples INTEGER NOT NULL CHECK (broker_latency_samples >= 0),
    news_blackout_hits INTEGER NOT NULL CHECK (news_blackout_hits >= 0),
    updated_at TEXT NOT NULL
);
