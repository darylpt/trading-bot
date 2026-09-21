"""Provider-neutral, fail-closed LLM sentiment adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from collections.abc import Callable, Sequence
from threading import Thread
from typing import Protocol

from ollama import Client
from openai import OpenAI
from openai.types.chat import ChatCompletionMessageParam

from sentiment.models import SentimentAnalysisResult
from sentiment.news_adapter import format_news_payload
from sentiment.parser import parse_llm_sentiment_response


SYSTEM_INSTRUCTION = (
    "You are a financial-news sentiment analyst. Return ONLY one raw JSON object "
    "with exactly these fields: sentiment_score (number from -1.0 to 1.0), "
    "confidence_score (number from 0.0 to 1.0), reasoning (string), and "
    "risk_modifier (number greater than 0.0 and at most 1.0). "
    "Do not use markdown. Do not provide order instructions, prices, position "
    "sizes, or broker actions."
)


class OpenAIMessage(Protocol):
    content: str | None


class OpenAIChoice(Protocol):
    message: OpenAIMessage


class OpenAIResponse(Protocol):
    choices: Sequence[OpenAIChoice]


class OpenAICompletions(Protocol):
    def create(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        response_format: dict[str, str],
        timeout: float,
    ) -> OpenAIResponse: ...


class OpenAIChat(Protocol):
    completions: OpenAICompletions


class OpenAIClient(Protocol):
    chat: OpenAIChat


class OllamaMessage(Protocol):
    content: str


class OllamaResponse(Protocol):
    message: OllamaMessage


class OllamaClient(Protocol):
    def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        format: str,
        options: dict[str, object],
    ) -> OllamaResponse: ...


def _call_with_timeout(operation: Callable[[], str], timeout_seconds: float) -> str:
    """Run a blocking provider call without allowing it to block the caller."""
    result: deque[tuple[bool, str | Exception]] = deque(maxlen=1)

    def run() -> None:
        try:
            result.append((True, operation()))
        except Exception as exc:
            result.append((False, exc))

    thread = Thread(target=run, daemon=True)
    thread.start()
    thread.join(timeout_seconds)
    if thread.is_alive():
        raise TimeoutError("LLM provider request exceeded configured timeout")
    if not result:
        raise RuntimeError("LLM provider returned no result")
    succeeded, value = result[0]
    if succeeded:
        if isinstance(value, str):
            return value
        raise RuntimeError("LLM provider returned a non-string response")
    if isinstance(value, Exception):
        raise value
    raise RuntimeError("LLM provider failed without an exception")


class LLMSentimentClient(ABC):
    """Common sentiment interface with a strict, rejecting result boundary."""

    def __init__(self, *, timeout_seconds: float = 5.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.timeout_seconds = timeout_seconds

    def analyze(
        self,
        headlines: list[dict[str, object]],
        technical_signal: dict[str, object],
    ) -> SentimentAnalysisResult:
        """Analyze normalized context and reject all provider failures."""
        context = format_news_payload(headlines, technical_signal)
        try:
            raw_response = _call_with_timeout(
                lambda: self._request_json(context), self.timeout_seconds
            )
        except TimeoutError as exc:
            return parse_llm_sentiment_response(exc)
        except Exception:
            # Provider SDKs expose different rate-limit and transport exceptions.
            # Any unexpected provider failure must remain a rejected decision.
            return parse_llm_sentiment_response("")
        return parse_llm_sentiment_response(raw_response)

    @abstractmethod
    def _request_json(self, context: str) -> str:
        """Return raw provider JSON or raise a provider/timeout exception."""
        raise NotImplementedError


class _OpenAIMessage(OpenAIMessage):
    def __init__(self, content: str | None) -> None:
        self.content = content


class _OpenAIChoice(OpenAIChoice):
    def __init__(self, message: _OpenAIMessage) -> None:
        self.message = message


class _OpenAIResponse(OpenAIResponse):
    def __init__(self, choices: list[_OpenAIChoice]) -> None:
        self.choices = choices


class _OpenAICompletionsAdapter(OpenAICompletions):
    def __init__(self, client: OpenAI) -> None:
        self._client = client

    def create(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        response_format: dict[str, str],
        timeout: float,
    ) -> OpenAIResponse:
        if model != "gpt-4o-mini" or len(messages) != 2:
            raise ValueError("unsupported OpenAI sentiment request")
        sdk_messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": messages[0]["content"]},
            {"role": "user", "content": messages[1]["content"]},
        ]
        response = self._client.chat.completions.create(
            model="gpt-4o-mini",
            messages=sdk_messages,
            response_format={"type": "json_object"},
            timeout=timeout,
        )
        return _OpenAIResponse(
            [
                _OpenAIChoice(_OpenAIMessage(choice.message.content))
                for choice in response.choices
            ]
        )


class _OpenAIChatAdapter(OpenAIChat):
    def __init__(self, client: OpenAI) -> None:
        self.completions = _OpenAICompletionsAdapter(client)


class _OpenAIClientAdapter(OpenAIClient):
    def __init__(self, client: OpenAI) -> None:
        self.chat = _OpenAIChatAdapter(client)


class _OllamaMessage(OllamaMessage):
    def __init__(self, content: str) -> None:
        self.content = content


class _OllamaResponse(OllamaResponse):
    def __init__(self, message: _OllamaMessage) -> None:
        self.message = message


class _OllamaClientAdapter(OllamaClient):
    def __init__(self, client: Client) -> None:
        self._client = client

    def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        format: str,
        options: dict[str, object],
    ) -> OllamaResponse:
        if format != "json":
            raise ValueError("Ollama sentiment output must be JSON")
        response = self._client.chat(
            model=model,
            messages=messages,
            format="json",
            options={"temperature": 0.0},
        )
        content = response.message.content
        if content is None:
            raise ValueError("Ollama sentiment response had no content")
        return _OllamaResponse(_OllamaMessage(content))


def create_openai_sentiment_adapter(
    api_key: str, *, timeout_seconds: float = 5.0
) -> OpenAISentimentAdapter:
    """Create the provider adapter behind a typed SDK boundary."""
    return OpenAISentimentAdapter(
        _OpenAIClientAdapter(OpenAI(api_key=api_key)),
        timeout_seconds=timeout_seconds,
    )


def create_ollama_sentiment_adapter(
    base_url: str,
    *,
    model: str = "llama3.1",
    timeout_seconds: float = 5.0,
) -> OllamaSentimentAdapter:
    """Create the provider adapter behind a typed SDK boundary."""
    return OllamaSentimentAdapter(
        _OllamaClientAdapter(Client(host=base_url)),
        model=model,
        timeout_seconds=timeout_seconds,
    )


class OpenAISentimentAdapter(LLMSentimentClient):
    """OpenAI adapter using forced JSON response mode."""

    def __init__(
        self,
        client: OpenAIClient,
        *,
        model: str = "gpt-4o-mini",
        timeout_seconds: float = 5.0,
    ) -> None:
        super().__init__(timeout_seconds=timeout_seconds)
        self.client = client
        self.model = model

    def _request_json(self, context: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": SYSTEM_INSTRUCTION},
                {"role": "user", "content": context},
            ],
            response_format={"type": "json_object"},
            timeout=self.timeout_seconds,
        )
        if not response.choices or response.choices[0].message.content is None:
            raise ValueError("provider returned no content")
        return response.choices[0].message.content


class OllamaSentimentAdapter(LLMSentimentClient):
    """Ollama adapter using JSON output mode and a bounded request timeout."""

    def __init__(
        self,
        client: OllamaClient,
        *,
        model: str = "llama3.1",
        timeout_seconds: float = 5.0,
    ) -> None:
        super().__init__(timeout_seconds=timeout_seconds)
        self.client = client
        self.model = model

    def _request_json(self, context: str) -> str:
        response = self.client.chat(
            model=self.model,
            messages=[
                {"role": "system", "content": SYSTEM_INSTRUCTION},
                {"role": "user", "content": context},
            ],
            format="json",
            options={"temperature": 0.0},
        )
        return response.message.content
