"""Central technical, sentiment, risk, execution, and persistence pipeline."""

from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from collections.abc import Sequence

from domain.models import (
    AccountSnapshot,
    ExecutionResult,
    MarketCandle,
    NewsEvent,
    OrderIntent,
    TechnicalSignal,
)
from execution.executor import ExecutionGate
from persistence.sqlite import ExecutionLogRecord, SQLiteRepository, TradeRecord
from risk.limits import calculate_atr_exits
from risk.sizing import calculate_position_size
from sentiment.llm_client import LLMSentimentClient
from sentiment.models import SentimentAnalysisResult
from sentiment.news_gate import is_in_news_blackout
from strategy.indicators import calculate_indicators
from strategy.market_data import MarketDataError
from strategy.signals import generate_signal


@dataclass(frozen=True)
class PipelineConfig:
    """Deterministic strategy and risk settings for one pipeline evaluation."""

    rsi_period: int = 14
    fast_period: int = 5
    slow_period: int = 20
    atr_period: int = 14
    news_buffer_minutes: int = 30
    risk_fraction: Decimal = Decimal("0.01")
    stop_multiplier: Decimal = Decimal("1")
    target_multiplier: Decimal = Decimal("2")
    contract_size: Decimal = Decimal("1")
    pip_value: Decimal = Decimal("1")
    minimum_size: Decimal = Decimal("0")
    maximum_size: Decimal | None = None
    quantity_step: Decimal = Decimal("0.01")


@dataclass(frozen=True)
class SentimentPipelineResult:
    """Observable result of one complete pipeline evaluation."""

    client_order_id: str
    technical_signal: TechnicalSignal | None
    sentiment: SentimentAnalysisResult | None
    order: OrderIntent | None
    execution: ExecutionResult


class TradingEngine:
    """Run the safe order path in a fixed technical-to-execution sequence."""

    def __init__(
        self,
        *,
        sentiment_client: LLMSentimentClient,
        execution_gate: ExecutionGate,
        repository: SQLiteRepository,
        config: PipelineConfig | None = None,
    ) -> None:
        self._sentiment_client = sentiment_client
        self._execution_gate = execution_gate
        self._repository = repository
        self._config = config or PipelineConfig()

    def run(
        self,
        *,
        candles: Sequence[MarketCandle],
        news_events: Sequence[NewsEvent],
        account: AccountSnapshot,
        client_order_id: str,
        current_time: datetime | None = None,
    ) -> SentimentPipelineResult:
        """Evaluate one signal and never dispatch before sentiment approval."""
        now = current_time or datetime.now(timezone.utc)
        try:
            indicators = calculate_indicators(
                candles,
                rsi_period=self._config.rsi_period,
                fast_period=self._config.fast_period,
                slow_period=self._config.slow_period,
                atr_period=self._config.atr_period,
            )
            technical_signal = generate_signal(indicators, now)
        except (MarketDataError, ValueError) as exc:
            return self._rejected(
                client_order_id,
                None,
                None,
                account,
                "TECHNICAL_REJECTED",
                str(exc),
            )

        if technical_signal is None:
            return self._rejected(
                client_order_id,
                None,
                None,
                account,
                "TECHNICAL_REJECTED",
                "no technical signal",
            )

        if is_in_news_blackout(
            now,
            news_events,
            buffer_minutes=self._config.news_buffer_minutes,
        ):
            return self._rejected(
                client_order_id,
                technical_signal,
                None,
                account,
                "NEWS_BLACKOUT",
                "high-impact news blackout",
            )

        sentiment = self._sentiment_client.analyze(
            [self._headline_payload(event) for event in news_events],
            self._technical_payload(technical_signal),
        )
        if sentiment.decision == "REJECT":
            return self._rejected(
                client_order_id,
                technical_signal,
                sentiment,
                account,
                "SENTIMENT_REJECTED",
                sentiment.reasoning,
            )

        latest = indicators.latest
        if latest is None or latest.atr is None:
            return self._rejected(
                client_order_id,
                technical_signal,
                sentiment,
                account,
                "RISK_REJECTED",
                "ATR is unavailable",
            )

        try:
            stop_loss, take_profit = calculate_atr_exits(
                reference_price=technical_signal.reference_price,
                atr=latest.atr,
                direction=technical_signal.direction,
                stop_multiplier=self._config.stop_multiplier,
                target_multiplier=self._config.target_multiplier,
            )
            risk_fraction = self._config.risk_fraction * Decimal(
                str(sentiment.risk_modifier)
            )
            quantity = calculate_position_size(
                account_equity=account.equity,
                entry_price=technical_signal.reference_price,
                stop_loss_price=stop_loss,
                risk_fraction=risk_fraction,
                contract_size=self._config.contract_size,
                pip_value=self._config.pip_value,
                minimum_size=self._config.minimum_size,
                maximum_size=self._config.maximum_size,
                quantity_step=self._config.quantity_step,
            )
            order = OrderIntent(
                client_order_id=client_order_id,
                instrument=technical_signal.instrument,
                direction=technical_signal.direction,
                quantity=quantity,
                entry_price=technical_signal.reference_price,
                stop_loss_price=stop_loss,
                take_profit_price=take_profit,
                account_equity=account.equity,
                risk_fraction=risk_fraction,
                signal_timestamp=technical_signal.signal_timestamp,
            )
        except ValueError as exc:
            return self._rejected(
                client_order_id,
                technical_signal,
                sentiment,
                account,
                "RISK_REJECTED",
                str(exc),
            )

        execution = self._execution_gate.submit(order)
        self._repository.save_trade(
            TradeRecord(
                client_order_id=execution.client_order_id,
                instrument=order.instrument,
                direction=order.direction,
                quantity=order.quantity,
                entry_price=order.entry_price,
                stop_loss_price=order.stop_loss_price,
                take_profit_price=order.take_profit_price,
                account_equity=order.account_equity,
                risk_fraction=order.risk_fraction,
                status=execution.status,
                environment=execution.environment,
                rejection_reason=execution.rejection_reason,
            )
        )
        self._repository.save_execution_log(
            ExecutionLogRecord(
                client_order_id=execution.client_order_id,
                event_type=f"ORDER_{execution.status}",
                provider="execution_gate",
                error_class=execution.rejection_reason,
                message="sentiment-approved order dispatched",
                latency_ms=execution.latency_ms,
                slippage=execution.slippage,
            )
        )
        return SentimentPipelineResult(
            client_order_id=client_order_id,
            technical_signal=technical_signal,
            sentiment=sentiment,
            order=order,
            execution=execution,
        )

    @staticmethod
    def _headline_payload(event: NewsEvent) -> dict[str, object]:
        return {
            "event_id": event.event_id,
            "source": event.source,
            "headline": event.headline,
            "published_at": event.published_at,
            "instrument": event.instrument,
            "currency": event.currency,
            "impact": event.impact,
        }

    @staticmethod
    def _technical_payload(signal: TechnicalSignal) -> dict[str, object]:
        return {
            "instrument": signal.instrument,
            "direction": signal.direction,
            "signal_timestamp": signal.signal_timestamp,
            "reference_price": signal.reference_price,
            "rsi": signal.rsi,
            "moving_average_fast": signal.moving_average_fast,
            "moving_average_slow": signal.moving_average_slow,
        }

    def _rejected(
        self,
        client_order_id: str,
        technical_signal: TechnicalSignal | None,
        sentiment: SentimentAnalysisResult | None,
        account: AccountSnapshot,
        event_type: str,
        reason: str,
    ) -> SentimentPipelineResult:
        execution = ExecutionResult(
            client_order_id=client_order_id,
            status="REJECTED",
            rejection_reason=reason,
            environment=account.environment,
        )
        self._repository.save_execution_log(
            ExecutionLogRecord(
                client_order_id=client_order_id,
                event_type=event_type,
                provider="sentiment_pipeline",
                error_class="PipelineRejection",
                message=reason,
            )
        )
        return SentimentPipelineResult(
            client_order_id=client_order_id,
            technical_signal=technical_signal,
            sentiment=sentiment,
            order=None,
            execution=execution,
        )
