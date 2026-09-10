"""Strict JSON sentiment parsing with a rejecting fallback."""

from __future__ import annotations

import json
from json import JSONDecodeError
from typing import overload
from pydantic import ValidationError

from sentiment.models import SentimentAnalysisResult


_FALLBACK_REASON = "LLM sentiment response was invalid or unavailable"


def _rejected_result() -> SentimentAnalysisResult:
    return SentimentAnalysisResult(
        decision="REJECT",
        confidence_score=0.0,
        reasoning=_FALLBACK_REASON,
        risk_modifier=1.0,
    )


@overload
def parse_llm_sentiment_response(
    raw_response: str,
) -> SentimentAnalysisResult: ...


@overload
def parse_llm_sentiment_response(
    raw_response: TimeoutError,
) -> SentimentAnalysisResult: ...


def parse_llm_sentiment_response(
    raw_response: str | TimeoutError,
) -> SentimentAnalysisResult:
    """Parse one strict JSON result; every expected failure rejects safely."""
    if isinstance(raw_response, TimeoutError):
        return _rejected_result()
    try:
        payload = json.loads(raw_response)
        return SentimentAnalysisResult.model_validate(payload)
    except (JSONDecodeError, TypeError, ValueError, ValidationError, TimeoutError):
        return _rejected_result()
