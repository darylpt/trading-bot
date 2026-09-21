from datetime import datetime, timedelta, timezone

import pytest

from domain.models import NewsEvent
from sentiment.models import SentimentAnalysisResult
from sentiment.news_gate import is_in_news_blackout
from sentiment.parser import parse_llm_sentiment_response


@pytest.fixture
def high_impact_event() -> NewsEvent:
    event_time = datetime(2026, 9, 10, 13, 30, tzinfo=timezone.utc)
    return NewsEvent(
        event_id="cpi-1",
        source="calendar",
        headline="US CPI release",
        published_at=event_time,
        currency="USD",
        impact="HIGH",
        retrieved_at=event_time - timedelta(minutes=1),
    )


def test_high_impact_event_blocks_before_and_after_release(
    high_impact_event: NewsEvent,
) -> None:
    event_time = high_impact_event.published_at
    assert is_in_news_blackout(event_time - timedelta(minutes=30), [high_impact_event])
    assert is_in_news_blackout(event_time + timedelta(minutes=30), [high_impact_event])
    assert not is_in_news_blackout(
        event_time - timedelta(minutes=31), [high_impact_event]
    )
    assert not is_in_news_blackout(
        event_time + timedelta(minutes=31), [high_impact_event]
    )


def test_non_high_impact_event_does_not_block(
    high_impact_event: NewsEvent,
) -> None:
    low_impact_event = high_impact_event.model_copy(update={"impact": "LOW"})
    assert not is_in_news_blackout(high_impact_event.published_at, [low_impact_event])


def test_valid_json_response_is_parsed() -> None:
    result = parse_llm_sentiment_response(
        '{"sentiment_score":0.82,"confidence_score":0.9,'
        '"reasoning":"supportive macro context","risk_modifier":0.5}'
    )
    assert isinstance(result, SentimentAnalysisResult)
    assert result.sentiment_score == 0.82
    assert result.decision == "ADJUST_RISK"
    assert result.confidence_score == 0.9
    assert result.risk_modifier == 0.5


def test_malformed_and_invalid_json_fail_closed() -> None:
    for raw_response in (
        "not json",
        '{"sentiment_score":0.8,"confidence_score":0.8}',
        '{"sentiment_score":1.2,"confidence_score":0.8,"reasoning":"bad"}',
        '{"sentiment_score":0.8,"confidence_score":0.8,'
        '"reasoning":"ok","unexpected":"field"}',
    ):
        result = parse_llm_sentiment_response(raw_response)
        assert result.decision == "REJECT"
        assert result.sentiment_score is None
        assert result.confidence_score == 0.0
        assert result.risk_modifier == 1.0


def test_timeout_during_json_parsing_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sentiment.parser as parser_module

    def raise_timeout(raw_response: str) -> object:
        raise TimeoutError(raw_response)

    monkeypatch.setattr(parser_module.json, "loads", raise_timeout)
    result = parser_module.parse_llm_sentiment_response("{}")
    assert result.decision == "REJECT"
    assert result.sentiment_score is None
    assert result.confidence_score == 0.0
