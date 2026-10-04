from __future__ import annotations

import json
from collections import Counter, deque
from pathlib import Path

import pytest

from sentiment.evaluation import (
    SentimentEvaluationCase,
    SentimentLabel,
    classify_sentiment_score,
    evaluate_sentiment_cases,
    load_sentiment_evaluation_cases,
)
from sentiment.llm_client import LLMSentimentClient


class SequenceClient(LLMSentimentClient):
    def __init__(self, responses: list[str]) -> None:
        super().__init__(timeout_seconds=1.0)
        self.responses = deque(responses)
        self.contexts: list[str] = []

    def _request_json(self, context: str) -> str:
        self.contexts.append(context)
        return self.responses.popleft()


class TimeoutClient(LLMSentimentClient):
    def __init__(self) -> None:
        super().__init__(timeout_seconds=1.0)
        self.calls = 0

    def _request_json(self, context: str) -> str:
        self.calls += 1
        raise TimeoutError("test timeout")


class IntermittentTimeoutClient(LLMSentimentClient):
    def __init__(self, outcomes: list[str | None]) -> None:
        super().__init__(timeout_seconds=1.0)
        self.outcomes = deque(outcomes)
        self.calls = 0

    def _request_json(self, context: str) -> str:
        self.calls += 1
        response = self.outcomes.popleft()
        if response is None:
            raise TimeoutError("test timeout")
        return response


def _response(score: float | None, confidence: float) -> str:
    return json.dumps(
        {
            "sentiment_score": score,
            "confidence_score": confidence,
            "reasoning": "benchmark result",
            "risk_modifier": 1.0,
        }
    )


def _case(case_id: str, label: SentimentLabel) -> SentimentEvaluationCase:
    return SentimentEvaluationCase(
        case_id=case_id,
        source_record="fixture",
        headline=f"Example {case_id}",
        expected_label=label,
    )


def test_classifier_matches_strict_production_thresholds() -> None:
    assert classify_sentiment_score(0.51, threshold=0.5) == "positive"
    assert classify_sentiment_score(0.5, threshold=0.5) == "neutral"
    assert classify_sentiment_score(-0.5, threshold=0.5) == "neutral"
    assert classify_sentiment_score(-0.51, threshold=0.5) == "negative"
    assert classify_sentiment_score(None, threshold=0.5) == "unavailable"


def test_evaluator_reports_label_agreement_and_keeps_unavailable_distinct() -> None:
    cases = (
        _case("positive", "positive"),
        _case("negative", "negative"),
        _case("neutral", "neutral"),
    )
    client = SequenceClient(
        [
            _response(0.8, 0.9),
            _response(0.0, 0.2),
            _response(None, 0.0),
        ]
    )

    report = evaluate_sentiment_cases(client, cases, threshold=0.5)

    result = report.as_dict()
    assert result["total"] == 3
    assert result["correct"] == 1
    assert result["accuracy"] == pytest.approx(1 / 3)
    assert result["coverage"] == pytest.approx(2 / 3)
    assert result["valid_response_rate"] == 1.0
    assert result["valid_abstentions"] == 1
    assert result["macro_f1"] == pytest.approx(1 / 3)
    assert result["screening_passed"] is False
    assert result["confusion_matrix"]["negative"]["neutral"] == 1
    assert result["confusion_matrix"]["neutral"]["unavailable"] == 1
    assert result["per_class_metrics"]["negative"]["f1"] == 0.0
    assert result["items"][2]["confidence_score"] == 0.0
    assert result["items"][2]["response_status"] == "valid"
    contexts = [json.loads(context) for context in client.contexts]
    assert [context["technical_signal"]["direction"] for context in contexts] == [
        "BUY",
        "BUY",
        "BUY",
    ]
    assert all("expected_label" not in context for context in contexts)


def test_evaluator_passes_pair_context_without_the_expected_label() -> None:
    case = SentimentEvaluationCase(
        case_id="pair-context",
        source_record="fixture",
        headline="Euro strengthens against the dollar",
        expected_label="positive",
        source="FX Street",
        instrument="EURUSDm",
        currency="EUR/USD",
    )
    client = SequenceClient([_response(0.8, 0.9)])

    evaluate_sentiment_cases(client, (case,), threshold=0.5)

    context = json.loads(client.contexts[0])
    assert context["technical_signal"]["instrument"] == "EURUSDm"
    assert context["news"][0]["source"] == "FX Street"
    assert context["news"][0]["currency"] == "EUR/USD"
    assert "expected_label" not in context


def test_valid_abstention_is_distinct_from_invalid_json() -> None:
    cases = (
        _case("valid-abstention", "neutral"),
        _case("invalid-json", "positive"),
        _case("valid-negative", "negative"),
    )
    client = SequenceClient(
        [
            _response(None, 0.0),
            "{invalid-json",
            _response(-0.8, 0.9),
        ]
    )

    result = evaluate_sentiment_cases(client, cases, threshold=0.5).as_dict()

    assert [item["response_status"] for item in result["items"]] == [
        "valid",
        "invalid_json",
        "valid",
    ]
    assert [item["predicted_label"] for item in result["items"]] == [
        "unavailable",
        "unavailable",
        "negative",
    ]


def test_evaluator_stops_after_timeout_budget_and_marks_unrun_cases() -> None:
    cases = (
        _case("positive-1", "positive"),
        _case("negative-1", "negative"),
        _case("neutral-1", "neutral"),
        _case("positive-2", "positive"),
        _case("negative-2", "negative"),
    )
    client = IntermittentTimeoutClient([None, _response(-0.8, 0.9), None, None])

    result = evaluate_sentiment_cases(
        client,
        cases,
        threshold=0.5,
        max_timeouts=3,
    ).as_dict()

    assert client.calls == 4
    assert result["total"] == 5
    assert result["evaluated_cases"] == 4
    assert result["complete"] is False
    assert result["termination_reason"] == "timeout_limit_reached"
    assert result["response_status_counts"] == {
        "timeout": 3,
        "valid": 1,
        "not_run": 1,
    }
    assert [item["response_status"] for item in result["items"]] == [
        "timeout",
        "valid",
        "timeout",
        "timeout",
        "not_run",
    ]
    assert result["screening_passed"] is False


def test_bundled_set_has_three_financially_labeled_classes() -> None:
    path = (
        Path(__file__).parents[2]
        / "src"
        / "sentiment"
        / "financial_phrasebank_eval.jsonl"
    )

    cases = load_sentiment_evaluation_cases(path)

    assert len(cases) == 9
    assert {case.expected_label for case in cases} == {
        "positive",
        "negative",
        "neutral",
    }


def test_bundled_eurusd_set_is_balanced_and_source_stratified() -> None:
    path = Path(__file__).parents[2] / "src" / "sentiment" / "eurusd_news_eval.jsonl"

    cases = load_sentiment_evaluation_cases(path)

    assert len(cases) == 90
    assert Counter(case.expected_label for case in cases) == {
        "positive": 30,
        "negative": 30,
        "neutral": 30,
    }
    assert Counter((case.expected_label, case.source) for case in cases) == {
        ("positive", "Forex Live"): 3,
        ("positive", "FX Street"): 27,
        ("negative", "Forex Live"): 2,
        ("negative", "FX Street"): 28,
        ("neutral", "Forex Live"): 2,
        ("neutral", "FX Street"): 28,
    }
    assert all(
        case.instrument == "EURUSDm" and case.currency == "EUR/USD" for case in cases
    )


def test_loader_rejects_missing_class_duplicate_id_and_unknown_fields(
    tmp_path: Path,
) -> None:
    path = tmp_path / "cases.jsonl"
    positive = {
        "case_id": "same",
        "source_record": "fixture:1",
        "headline": "A headline",
        "expected_label": "positive",
    }
    path.write_text(json.dumps(positive) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="cover all labels"):
        load_sentiment_evaluation_cases(path)

    path.write_text(
        "\n".join(
            json.dumps(
                {
                    **positive,
                    "case_id": case_id,
                    "expected_label": label,
                }
            )
            for case_id, label in (
                ("same", "positive"),
                ("same", "negative"),
                ("neutral", "neutral"),
            )
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="IDs must be unique"):
        load_sentiment_evaluation_cases(path)

    positive["unexpected"] = "rejected"
    path.write_text(
        "\n".join(
            json.dumps({**positive, "case_id": case_id, "expected_label": label})
            for case_id, label in (
                ("positive", "positive"),
                ("negative", "negative"),
                ("neutral", "neutral"),
            )
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="line 1"):
        load_sentiment_evaluation_cases(path)


def test_cli_refuses_non_paper_mode_before_provider_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from tools import evaluate_sentiment

    unsafe_settings = SimpleNamespace(
        paper_trading=False,
        live_trading=True,
        trading_mode="BROKER_DEMO",
    )
    monkeypatch.setattr(
        evaluate_sentiment,
        "Settings",
        lambda **kwargs: unsafe_settings,
    )

    with pytest.raises(SystemExit, match="requires PAPER_TRADING=true"):
        evaluate_sentiment.main([])


def test_cli_compares_models_with_compact_metrics_and_preserves_paper_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from types import SimpleNamespace

    from tools import evaluate_sentiment

    cases = (
        _case("positive", "positive"),
        _case("negative", "negative"),
        _case("neutral", "neutral"),
    )
    path = tmp_path / "cases.jsonl"
    path.write_text(
        "\n".join(case.model_dump_json() for case in cases),
        encoding="utf-8",
    )
    settings = SimpleNamespace(
        paper_trading=True,
        live_trading=False,
        trading_mode="BROKER_DEMO",
        sentiment_provider="ollama",
        ollama_model="llama3.2:3b",
        ollama_base_url="http://localhost:11434",
        sentiment_threshold=0.5,
    )
    monkeypatch.setattr(
        evaluate_sentiment,
        "Settings",
        lambda **kwargs: settings,
    )
    models: list[str | None] = []

    def make_client(
        _settings: object, *, model_override: str | None = None
    ) -> SequenceClient:
        models.append(model_override)
        return SequenceClient(
            [_response(0.8, 0.9), _response(-0.8, 0.9), _response(0.0, 0.9)]
        )

    monkeypatch.setattr(evaluate_sentiment, "_configured_sentiment_client", make_client)

    exit_code = evaluate_sentiment.main(
        [
            "--cases",
            str(path),
            "--model",
            "llama3.2:3b",
            "--model",
            "qwen3:8b",
            "--summary-only",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert models == ["llama3.2:3b", "qwen3:8b"]
    assert [entry["model"] for entry in output["model_comparison"]] == models
    assert all(
        entry["results"]["screening_passed"]
        and entry["results"]["coverage"] == 1.0
        and "items" not in entry["results"]
        for entry in output["model_comparison"]
    )


def test_cli_stops_remaining_models_after_timeout_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from types import SimpleNamespace

    from tools import evaluate_sentiment

    cases = (
        _case("positive", "positive"),
        _case("negative", "negative"),
        _case("neutral", "neutral"),
    )
    path = tmp_path / "cases.jsonl"
    path.write_text(
        "\n".join(case.model_dump_json() for case in cases),
        encoding="utf-8",
    )
    settings = SimpleNamespace(
        paper_trading=True,
        live_trading=False,
        trading_mode="BROKER_DEMO",
        sentiment_provider="ollama",
        ollama_model="llama3.2:3b",
        ollama_base_url="http://localhost:11434",
        sentiment_threshold=0.5,
    )
    monkeypatch.setattr(
        evaluate_sentiment,
        "Settings",
        lambda **kwargs: settings,
    )
    models: list[str | None] = []

    def make_client(
        _settings: object, *, model_override: str | None = None
    ) -> TimeoutClient:
        models.append(model_override)
        return TimeoutClient()

    monkeypatch.setattr(evaluate_sentiment, "_configured_sentiment_client", make_client)

    exit_code = evaluate_sentiment.main(
        [
            "--cases",
            str(path),
            "--model",
            "llama3.1:latest",
            "--model",
            "qwen3:8b",
            "--summary-only",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert models == ["llama3.1:latest"]
    assert output["comparison_stop_reason"] == "timeout_limit_reached"
    assert output["unattempted_models"] == ["qwen3:8b"]
    assert output["model_comparison"][0]["results"]["screening_passed"] is False
