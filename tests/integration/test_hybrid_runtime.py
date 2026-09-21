from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from persistence.sqlite import SQLiteRepository
from sentiment.llm_client import LLMSentimentClient
from sentiment.models import SentimentAnalysisResult
from sentiment.news import JsonNewsFeed
from sentiment.runtime import SentimentGate
from trading_bot import __main__ as application
from trading_bot.market_data_seed import ensure_default_market_data
from trading_bot.runtime_config import RuntimeConfig
from trading_bot.strategy import Signal


class FakeSentimentClient(LLMSentimentClient):
    def __init__(self, score: float) -> None:
        super().__init__(timeout_seconds=1.0)
        self.score = score
        self.calls = 0

    def _request_json(self, context: str) -> str:
        self.calls += 1
        return json.dumps(
            SentimentAnalysisResult(
                sentiment_score=self.score,
                confidence_score=1.0,
                reasoning="deterministic test analysis",
            ).model_dump()
        )


def _signal(timestamp: datetime) -> Signal:
    return Signal(
        action="BUY",
        timestamp=timestamp,
        instrument="XAUUSDm",
        reference_price=Decimal("100"),
        rationale="deterministic technical entry",
    )


def _news_file(path: Path, timestamp: datetime) -> None:
    path.write_text(
        json.dumps(
            [
                {
                    "event_id": "macro-1",
                    "source": "test-calendar",
                    "headline": "Manufacturing data improves",
                    "published_at": timestamp.isoformat(),
                    "instrument": "XAUUSDm",
                    "currency": "USD",
                    "impact": "LOW",
                    "retrieved_at": timestamp.isoformat(),
                }
            ]
        ),
        encoding="utf-8",
    )


def test_runtime_entry_uses_sentiment_before_paper_execution(
    tmp_path: Path, monkeypatch
) -> None:
    now = datetime(2026, 9, 20, 13, 15, tzinfo=timezone.utc)
    data_dir = tmp_path / "data"
    ensure_default_market_data(data_dir, instrument="XAUUSDm")
    news_path = tmp_path / "news.json"
    _news_file(news_path, now)
    repository = SQLiteRepository(tmp_path / "session_metrics.db")
    RuntimeConfig(repository.path).set_active_instrument("XAUUSDm")
    client = FakeSentimentClient(0.8)
    monkeypatch.setattr(
        application, "_evaluate_strategy", lambda *args, **kwargs: _signal(now)
    )

    application.run_tick(
        repository,
        data_dir=data_dir,
        current_time=now,
        account_equity=Decimal("10000"),
        session_start_equity=Decimal("10000"),
        stop_distance=Decimal("1"),
        sentiment_gate=SentimentGate(client),
        sentiment_provider="ollama",
        sentiment_model="llama3.2:3b",
        news_feed=JsonNewsFeed(news_path),
    )

    assert client.calls == 1
    assert len(repository.get_open_positions()) == 1
    event_types = [
        row[0]
        for row in repository.connection.execute(
            "SELECT event_type FROM execution_logs ORDER BY id"
        ).fetchall()
    ]
    llm_row = repository.connection.execute(
        """SELECT technical_action, provider, model, decision, gate_reason,
                  sentiment_score, confidence_score, risk_modifier, reasoning
           FROM llm_decisions"""
    ).fetchone()
    assert llm_row is not None
    assert llm_row[:5] == (
        "BUY",
        "ollama",
        "llama3.2:3b",
        "CONFIRM",
        "SENTIMENT_APPROVED",
    )
    assert Decimal(str(llm_row[5])) == Decimal("0.8")
    assert Decimal(str(llm_row[6])) == Decimal("1.0")
    assert Decimal(str(llm_row[7])) == Decimal("1.0")
    assert llm_row[8] == "deterministic test analysis"
    assert "SENTIMENT_APPROVED" in event_types
    repository.close()


def test_runtime_entry_rejects_neutral_sentiment_without_position(
    tmp_path: Path, monkeypatch
) -> None:
    now = datetime(2026, 9, 20, 13, 15, tzinfo=timezone.utc)
    data_dir = tmp_path / "data"
    ensure_default_market_data(data_dir, instrument="XAUUSDm")
    news_path = tmp_path / "news.json"
    _news_file(news_path, now)
    repository = SQLiteRepository(tmp_path / "session_metrics.db")
    RuntimeConfig(repository.path).set_active_instrument("XAUUSDm")
    client = FakeSentimentClient(0.5)
    monkeypatch.setattr(
        application, "_evaluate_strategy", lambda *args, **kwargs: _signal(now)
    )

    application.run_tick(
        repository,
        data_dir=data_dir,
        current_time=now,
        account_equity=Decimal("10000"),
        session_start_equity=Decimal("10000"),
        stop_distance=Decimal("1"),
        sentiment_gate=SentimentGate(client),
        sentiment_provider="ollama",
        sentiment_model="llama3.2:3b",
        news_feed=JsonNewsFeed(news_path),
    )

    assert client.calls == 1
    assert repository.get_open_positions() == []
    row = repository.connection.execute(
        "SELECT event_type, message FROM execution_logs "
        "WHERE event_type = 'SENTIMENT_REJECTED'"
    ).fetchone()
    assert row == ("SENTIMENT_REJECTED", "SENTIMENT_BELOW_BUY_THRESHOLD")
    llm_row = repository.connection.execute(
        """SELECT technical_action, decision, gate_reason, sentiment_score,
                  confidence_score, risk_modifier
           FROM llm_decisions"""
    ).fetchone()
    assert llm_row is not None
    assert llm_row[:3] == ("BUY", "REJECT", "SENTIMENT_BELOW_BUY_THRESHOLD")
    assert Decimal(str(llm_row[3])) == Decimal("0.5")
    assert Decimal(str(llm_row[4])) == Decimal("1.0")
    assert Decimal(str(llm_row[5])) == Decimal("1.0")
    repository.close()
