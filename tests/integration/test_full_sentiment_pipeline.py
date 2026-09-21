from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from domain.models import (
    AccountSnapshot,
    BrokerOrderPayload,
    ExecutionResult,
    NewsEvent,
)
from execution.executor import ExecutionGate
from persistence.sqlite import SQLiteRepository
from sentiment.llm_client import LLMSentimentClient
from sentiment.models import SentimentAnalysisResult
from strategy.market_data import load_csv_candles
from trading_bot.engine import PipelineConfig, TradingEngine


class MockSentimentClient(LLMSentimentClient):
    def __init__(self, result: SentimentAnalysisResult) -> None:
        super().__init__(timeout_seconds=1.0)
        self.result = result
        self.calls = 0

    def _request_json(self, context: str) -> str:
        self.calls += 1
        return json.dumps(self.result.model_dump())


class PaperGateway:
    def __init__(self) -> None:
        self.payloads: list[BrokerOrderPayload] = []

    def submit_order(self, payload: BrokerOrderPayload) -> ExecutionResult:
        self.payloads.append(payload)
        return ExecutionResult(
            client_order_id=payload.client_order_id,
            status="ACCEPTED",
            environment=payload.environment,
        )

    def reconcile_order(self, client_order_id: str) -> ExecutionResult | None:
        return None


@dataclass(frozen=True)
class PipelineFixture:
    candles_path: Path
    account: AccountSnapshot
    current_time: datetime


def _fixture(tmp_path: Path) -> PipelineFixture:
    current_time = datetime(2026, 1, 5, 13, 15, tzinfo=timezone.utc)
    candles_path = tmp_path / "eur_usd_15m.csv"
    candles_path.write_text(
        "timestamp,open,high,low,close,volume\n"
        "2026-01-05T12:00:00Z,1.0995,1.1010,1.0990,1.1000,100\n"
        "2026-01-05T12:15:00Z,1.0995,1.1010,1.0990,1.1000,110\n"
        "2026-01-05T12:30:00Z,1.0995,1.1010,1.0990,1.1000,120\n"
        "2026-01-05T12:45:00Z,1.0995,1.1010,1.0990,1.1000,130\n"
        "2026-01-05T13:00:00Z,1.0955,1.0960,1.0940,1.0950,140\n"
        "2026-01-05T13:15:00Z,1.1055,1.1070,1.1040,1.1060,150\n",
        encoding="utf-8",
    )
    return PipelineFixture(
        candles_path=candles_path,
        account=AccountSnapshot(
            account_id="paper-account",
            equity=Decimal("10000"),
            balance=Decimal("10000"),
            captured_at=current_time,
            environment="PAPER",
        ),
        current_time=current_time,
    )


def _news_event(published_at: datetime) -> NewsEvent:
    return NewsEvent(
        event_id="cpi-1",
        source="calendar",
        headline="CPI release",
        published_at=published_at,
        currency="USD",
        impact="HIGH",
        retrieved_at=published_at,
    )


def _engine(
    tmp_path: Path,
    llm: MockSentimentClient,
    gateway: PaperGateway,
) -> tuple[TradingEngine, SQLiteRepository]:
    repository = SQLiteRepository(tmp_path / "session_metrics.db")
    engine = TradingEngine(
        sentiment_client=llm,
        execution_gate=ExecutionGate(gateway, environment="PAPER"),
        repository=repository,
        config=PipelineConfig(
            rsi_period=2,
            fast_period=2,
            slow_period=3,
            atr_period=2,
        ),
    )
    return engine, repository


def test_full_pipeline_confirms_sizes_dispatches_and_persists(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    llm = MockSentimentClient(
        SentimentAnalysisResult(
            sentiment_score=0.8,
            confidence_score=0.9,
            reasoning="supportive macro context",
            risk_modifier=0.5,
        )
    )
    gateway = PaperGateway()
    engine, repository = _engine(tmp_path, llm, gateway)

    result = engine.run(
        candles=load_csv_candles(fixture.candles_path, minimum_history=6),
        news_events=[_news_event(fixture.current_time - timedelta(hours=2))],
        account=fixture.account,
        client_order_id="sentiment-chain-1",
        current_time=fixture.current_time,
    )

    assert result.execution.status == "ACCEPTED"
    assert result.sentiment is not None
    assert result.sentiment.decision == "ADJUST_RISK"
    assert result.order is not None
    assert result.order.risk_fraction == Decimal("0.005")
    assert llm.calls == 1
    assert len(gateway.payloads) == 1
    assert repository.trade_count() == 1
    assert repository.execution_log_count() == 1
    latest_metrics = repository.get_latest_metrics(limit=1)
    assert len(latest_metrics) == 1
    assert latest_metrics[0].session_date == fixture.current_time.date()
    repository.close()


def test_news_blackout_blocks_before_llm_and_execution(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    llm = MockSentimentClient(
        SentimentAnalysisResult(
            sentiment_score=0.8,
            confidence_score=1.0,
            reasoning="positive",
        )
    )
    gateway = PaperGateway()
    engine, repository = _engine(tmp_path, llm, gateway)

    result = engine.run(
        candles=load_csv_candles(fixture.candles_path, minimum_history=6),
        news_events=[_news_event(fixture.current_time)],
        account=fixture.account,
        client_order_id="sentiment-chain-blackout",
        current_time=fixture.current_time,
    )

    assert result.execution.status == "REJECTED"
    assert result.execution.rejection_reason == "high-impact news blackout"
    assert llm.calls == 0
    assert gateway.payloads == []
    assert repository.trade_count() == 0
    assert repository.execution_log_count() == 1
    repository.close()


def test_rejected_sentiment_aborts_before_execution(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    llm = MockSentimentClient(
        SentimentAnalysisResult(
            sentiment_score=0.0,
            confidence_score=0.9,
            reasoning="insufficient confirmation",
        )
    )
    gateway = PaperGateway()
    engine, repository = _engine(tmp_path, llm, gateway)

    result = engine.run(
        candles=load_csv_candles(fixture.candles_path, minimum_history=6),
        news_events=[_news_event(fixture.current_time - timedelta(hours=2))],
        account=fixture.account,
        client_order_id="sentiment-chain-rejected",
        current_time=fixture.current_time,
    )

    assert result.execution.status == "REJECTED"
    assert result.execution.rejection_reason == "insufficient confirmation"
    assert llm.calls == 1
    assert gateway.payloads == []
    assert repository.trade_count() == 0
    assert repository.execution_log_count() == 1
    repository.close()


def test_restart_reconciles_durable_unknown_without_resubmission(
    tmp_path: Path,
) -> None:
    """Exercise the complete deterministic path across a process restart."""
    fixture = _fixture(tmp_path)
    candles = load_csv_candles(fixture.candles_path, minimum_history=6)
    llm = MockSentimentClient(
        SentimentAnalysisResult(
            sentiment_score=0.8,
            confidence_score=1.0,
            reasoning="deterministic paper approval",
        )
    )

    class TimedOutGateway(PaperGateway):
        def submit_order(self, payload: BrokerOrderPayload) -> ExecutionResult:
            self.payloads.append(payload)
            raise TimeoutError("simulated process interruption")

    first_gateway = TimedOutGateway()
    first_repository = SQLiteRepository(tmp_path / "session_metrics.db")
    first_engine = TradingEngine(
        sentiment_client=llm,
        execution_gate=ExecutionGate(
            first_gateway,
            environment="PAPER",
            repository=first_repository,
        ),
        repository=first_repository,
        config=PipelineConfig(
            rsi_period=2,
            fast_period=2,
            slow_period=3,
            atr_period=2,
        ),
    )
    first = first_engine.run(
        candles=candles,
        news_events=[_news_event(fixture.current_time - timedelta(hours=2))],
        account=fixture.account,
        client_order_id="restart-order-1",
        current_time=fixture.current_time,
    )
    assert first.execution.status == "UNKNOWN"
    assert first_repository.get_trade("restart-order-1").status == "UNKNOWN"
    first_repository.close()

    class ReconcileGateway(PaperGateway):
        def reconcile_order(self, client_order_id: str) -> ExecutionResult:
            return ExecutionResult(
                client_order_id=client_order_id,
                provider_order_id="broker-fill-1",
                status="ACCEPTED",
                filled_quantity=Decimal("1"),
                fill_price=Decimal("1.106"),
                environment="PAPER",
            )

    second_repository = SQLiteRepository(tmp_path / "session_metrics.db")
    second_gateway = ReconcileGateway()
    reconciled = ExecutionGate(
        second_gateway,
        environment="PAPER",
        repository=second_repository,
    ).reconcile_pending()

    assert [result.status for result in reconciled] == ["ACCEPTED"]
    assert second_gateway.payloads == []
    assert second_repository.get_trade("restart-order-1").status == "ACCEPTED"
    second_repository.close()
