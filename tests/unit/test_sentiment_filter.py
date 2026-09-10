import pytest

from pydantic import ValidationError

from domain.models import LLMSentimentResponse


def test_valid_sentiment_score_is_strictly_numeric() -> None:
    assert LLMSentimentResponse(sentiment_score=0.76).sentiment_score == 0.76


def test_invalid_sentiment_score_fails_closed_at_schema_boundary() -> None:
    with pytest.raises(ValidationError):
        LLMSentimentResponse.model_validate({"sentiment_score": 1.01})
    with pytest.raises(ValidationError):
        LLMSentimentResponse.model_validate({"sentiment_score": 0.8, "unexpected": "x"})
