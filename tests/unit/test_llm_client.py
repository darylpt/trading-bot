from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sentiment.llm_client import (
    OllamaSentimentAdapter,
    OpenAISentimentAdapter,
    SYSTEM_INSTRUCTION,
)
from sentiment.models import SentimentAnalysisResult
from sentiment.news_adapter import format_news_payload


HEADLINES = [
    {
        "title": "CPI shows easing inflation",
        "source": "calendar",
        "published_at": datetime(2026, 9, 10, 13, 30, tzinfo=timezone.utc),
        "impact": "HIGH",
    }
]
SIGNAL = {
    "instrument": "EUR_USD",
    "direction": "LONG",
    "reference_price": "1.1000",
}
VALID_JSON = (
    '{"sentiment_score":0.8,"confidence_score":0.9,'
    '"reasoning":"supportive headline","risk_modifier":1.0}'
)


@dataclass
class Message:
    content: str | None


@dataclass
class Choice:
    message: Message


@dataclass
class OpenAIResponse:
    choices: list[Choice]


class FakeCompletions:
    def __init__(
        self, response: OpenAIResponse | None = None, error: Exception | None = None
    ) -> None:
        self.response = response
        self.error = error
        self.kwargs: dict[str, object] = {}

    def create(self, **kwargs: object) -> OpenAIResponse:
        self.kwargs = kwargs
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


@dataclass
class FakeChat:
    completions: FakeCompletions


@dataclass
class FakeOpenAI:
    chat: FakeChat


@dataclass
class OllamaResponse:
    message: Message


class FakeOllama:
    def __init__(
        self, response: OllamaResponse | None = None, error: Exception | None = None
    ) -> None:
        self.response = response
        self.error = error
        self.kwargs: dict[str, object] = {}

    def chat(self, **kwargs: object) -> OllamaResponse:
        self.kwargs = kwargs
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


def test_news_payload_is_structured_and_bounded() -> None:
    payload = format_news_payload(HEADLINES, SIGNAL)
    assert '"CPI shows easing inflation"' in payload
    assert '"LONG"' in payload
    assert "calendar" in payload
    assert "object at" not in payload


def test_openai_adapter_parses_valid_json_and_forces_json_mode() -> None:
    completions = FakeCompletions(OpenAIResponse([Choice(Message(VALID_JSON))]))
    result = OpenAISentimentAdapter(
        FakeOpenAI(FakeChat(completions)), timeout_seconds=2.5
    ).analyze(HEADLINES, SIGNAL)
    assert isinstance(result, SentimentAnalysisResult)
    assert result.decision == "CONFIRM"
    assert completions.kwargs["response_format"] == {"type": "json_object"}
    assert completions.kwargs["timeout"] == 2.5
    messages = completions.kwargs["messages"]
    assert isinstance(messages, list)
    assert messages[0]["content"] == SYSTEM_INSTRUCTION


def test_rate_limit_error_fails_closed_for_both_adapters() -> None:
    openai_result = OpenAISentimentAdapter(
        FakeOpenAI(FakeChat(FakeCompletions(error=RuntimeError("rate limit"))))
    ).analyze(HEADLINES, SIGNAL)
    ollama_result = OllamaSentimentAdapter(
        FakeOllama(error=RuntimeError("rate limit"))
    ).analyze(HEADLINES, SIGNAL)
    assert openai_result.decision == "REJECT"
    assert ollama_result.decision == "REJECT"


def test_timeout_exception_is_forwarded_as_rejection() -> None:
    client = FakeOpenAI(FakeChat(FakeCompletions(error=TimeoutError("slow"))))
    result = OpenAISentimentAdapter(client).analyze(HEADLINES, SIGNAL)
    assert result.decision == "REJECT"
    assert result.confidence_score == 0.0


def test_ollama_adapter_uses_json_output_mode() -> None:
    client = FakeOllama(OllamaResponse(Message(VALID_JSON)))
    result = OllamaSentimentAdapter(client, timeout_seconds=3.0).analyze(
        HEADLINES, SIGNAL
    )
    assert result.decision == "CONFIRM"
    assert client.kwargs["format"] == "json"
    assert client.kwargs["options"] == {"temperature": 0.0}
