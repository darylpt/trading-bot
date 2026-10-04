"""Strict JSON sentiment parsing with a rejecting fallback."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from json import JSONDecodeError
from typing import Literal, overload

from pydantic import ValidationError

from sentiment.models import SentimentAnalysisResult

LOGGER = logging.getLogger(__name__)

_FALLBACK_REASON = "LLM sentiment response was invalid or unavailable"
_SAFE_SENTIMENT_FIELDS = frozenset(
    {"sentiment_score", "confidence_score", "reasoning", "risk_modifier"}
)


def _rejected_result() -> SentimentAnalysisResult:
    return SentimentAnalysisResult(
        sentiment_score=None,
        confidence_score=0.0,
        reasoning=_FALLBACK_REASON,
        risk_modifier=1.0,
    )


SentimentParseStatus = Literal[
    "valid",
    "invalid_json",
    "schema_validation",
    "invalid_response",
    "timeout",
    "provider_error",
]


@dataclass(frozen=True)
class SentimentParseResult:
    """Validated analysis plus a sanitized response status for diagnostics."""

    result: SentimentAnalysisResult
    status: SentimentParseStatus


def parse_llm_sentiment_response_with_status(
    raw_response: str | TimeoutError | None,
) -> SentimentParseResult:
    """Parse strict JSON while retaining only a bounded, non-sensitive status."""
    if isinstance(raw_response, TimeoutError):
        return SentimentParseResult(_rejected_result(), "timeout")
    if raw_response is None:
        return SentimentParseResult(_rejected_result(), "provider_error")
    try:
        payload = json.loads(raw_response)
        result = SentimentAnalysisResult.model_validate(payload)
        return SentimentParseResult(result, "valid")
    except JSONDecodeError:
        LOGGER.warning("sentiment response rejected reason=invalid_json")
        return SentimentParseResult(_rejected_result(), "invalid_json")
    except ValidationError as exc:
        errors = exc.errors(include_input=False)
        error_types = ",".join(sorted({str(error["type"]) for error in errors}))
        field_paths = ",".join(
            sorted(
                {
                    str(location[0])
                    if len(location) == 1 and location[0] in _SAFE_SENTIMENT_FIELDS
                    else "unknown"
                    for location in (error["loc"] for error in errors)
                }
            )
        )
        LOGGER.warning(
            "sentiment response rejected reason=schema_validation "
            "error_types=%s field_paths=%s",
            error_types,
            field_paths,
        )
        return SentimentParseResult(_rejected_result(), "schema_validation")
    except (TypeError, ValueError, TimeoutError) as exc:
        LOGGER.warning(
            "sentiment response rejected reason=invalid_response error_class=%s",
            type(exc).__name__,
        )
        return SentimentParseResult(_rejected_result(), "invalid_response")


@overload
def parse_llm_sentiment_response(raw_response: str) -> SentimentAnalysisResult: ...


@overload
def parse_llm_sentiment_response(
    raw_response: TimeoutError,
) -> SentimentAnalysisResult: ...


def parse_llm_sentiment_response(
    raw_response: str | TimeoutError,
) -> SentimentAnalysisResult:
    """Parse one strict JSON result; every expected failure rejects safely."""
    return parse_llm_sentiment_response_with_status(raw_response).result
