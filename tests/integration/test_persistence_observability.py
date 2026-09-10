from datetime import date
from decimal import Decimal
from pathlib import Path

from observability.logging import sanitize_fields
from persistence.sqlite import SQLiteRepository, TradeRecord


def test_sqlite_trade_write_is_idempotent(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "trades.sqlite")
    record = TradeRecord(
        client_order_id="persist-1",
        instrument="EUR_USD",
        direction="LONG",
        quantity=Decimal("20"),
        entry_price=Decimal("1.1"),
        stop_loss_price=Decimal("1.095"),
        take_profit_price=Decimal("1.11"),
        account_equity=Decimal("10000"),
        risk_fraction=Decimal("0.01"),
        status="ACCEPTED",
        environment="PAPER",
    )
    repository.save_trade(record)
    repository.save_trade(record)
    assert repository.trade_count() == 1
    repository.close()


def test_session_metrics_accumulate_latency_and_blackouts(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "metrics.sqlite")
    session_date = date(2026, 9, 10)

    repository.record_session_metrics(
        session_date, broker_latency_ms=120, news_blackout_hit=True
    )
    repository.record_session_metrics(session_date, broker_latency_ms=80)

    metrics = repository.get_session_metrics(session_date)
    repository.close()

    assert metrics is not None
    assert metrics.broker_latency_total_ms == 200
    assert metrics.broker_latency_samples == 2
    assert metrics.news_blackout_hits == 1


def test_observability_redacts_sensitive_keys_and_values() -> None:
    safe = sanitize_fields(
        {"rejection_reason": "timeout", "api_key": "secret", "message": "token=secret"},
        secrets=("secret",),
    )
    assert safe["rejection_reason"] == "timeout"
    assert safe["api_key"] == "[REDACTED]"
    assert safe["message"] == "[REDACTED]"
