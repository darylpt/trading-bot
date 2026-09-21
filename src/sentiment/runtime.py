"""Runtime sentiment gating with bounded analysis cadence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from collections.abc import Sequence

from domain.models import NewsEvent
from sentiment.llm_client import LLMSentimentClient
from sentiment.models import SentimentAnalysisResult
from trading_bot.strategy import Signal


@dataclass(frozen=True)
class SentimentGateResult:
    """One fail-closed sentiment decision for a technical signal."""

    approved: bool
    sentiment: SentimentAnalysisResult | None
    reason: str


class SentimentGate:
    """Analyze each signal context once and reject stale/unsafe AI decisions."""

    def __init__(
        self,
        client: LLMSentimentClient,
        *,
        minimum_interval_seconds: float = 900.0,
        threshold: float = 0.5,
    ) -> None:
        if minimum_interval_seconds <= 0:
            raise ValueError("sentiment interval must be positive")
        if not 0 < threshold < 1:
            raise ValueError("sentiment threshold must be between zero and one")
        self._client = client
        self._minimum_interval_seconds = minimum_interval_seconds
        self._threshold = threshold
        self._last_key: tuple[str, str, str, tuple[str, ...]] | None = None
        self._last_analyzed_at: datetime | None = None
        self._last_result: SentimentAnalysisResult | None = None

    def evaluate(
        self,
        signal: Signal,
        news_events: Sequence[NewsEvent],
        *,
        current_time: datetime,
    ) -> SentimentGateResult:
        """Return an approval only when validated sentiment confirms direction."""
        if signal.action not in {"BUY", "SELL"}:
            return SentimentGateResult(False, None, "NO_ENTRY_SIGNAL")

        now = self._utc(current_time)
        key = self._context_key(signal, news_events)
        if key == self._last_key and self._last_result is not None:
            return self._approval(signal, self._last_result)
        if (
            self._last_analyzed_at is not None
            and (now - self._last_analyzed_at).total_seconds()
            < self._minimum_interval_seconds
        ):
            return SentimentGateResult(False, self._last_result, "SENTIMENT_COOLDOWN")

        result = self._client.analyze(
            [self._news_payload(event) for event in news_events],
            self._signal_payload(signal),
        )
        self._last_key = key
        self._last_analyzed_at = now
        self._last_result = result
        return self._approval(signal, result)

    def _approval(
        self,
        signal: Signal,
        result: SentimentAnalysisResult,
    ) -> SentimentGateResult:
        score = result.sentiment_score
        if score is None:
            return SentimentGateResult(False, result, "SENTIMENT_UNAVAILABLE")
        if signal.action == "BUY" and score <= self._threshold:
            return SentimentGateResult(False, result, "SENTIMENT_BELOW_BUY_THRESHOLD")
        if signal.action == "SELL" and score >= -self._threshold:
            return SentimentGateResult(False, result, "SENTIMENT_ABOVE_SELL_THRESHOLD")
        return SentimentGateResult(True, result, "SENTIMENT_APPROVED")

    @staticmethod
    def _context_key(
        signal: Signal, news_events: Sequence[NewsEvent]
    ) -> tuple[str, str, str, tuple[str, ...]]:
        return (
            signal.instrument,
            signal.action,
            signal.timestamp.isoformat(),
            tuple(
                f"{event.event_id}:{event.published_at.isoformat()}"
                for event in news_events
            ),
        )

    @staticmethod
    def _signal_payload(signal: Signal) -> dict[str, object]:
        return {
            "instrument": signal.instrument,
            "direction": "LONG" if signal.action == "BUY" else "SHORT",
            "reference_price": signal.reference_price,
            "signal_timestamp": signal.timestamp,
            "rationale": signal.rationale,
        }

    @staticmethod
    def _news_payload(event: NewsEvent) -> dict[str, object]:
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
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
