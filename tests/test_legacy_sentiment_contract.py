"""Compatibility coverage for strict sentiment validation."""

import pytest
from pydantic import ValidationError

from domain.models import LLMSentimentResponse


def test_malformed_or_out_of_range_sentiment_is_rejected() -> None:
    with pytest.raises(ValidationError):
        LLMSentimentResponse.model_validate_json("not valid json")
    with pytest.raises(ValidationError):
        LLMSentimentResponse(sentiment_score=1.01)
