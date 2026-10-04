"""No-order evaluation of validated LLM sentiment against labeled cases."""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from sentiment.llm_client import LLMSentimentClient
from sentiment.parser import SentimentParseStatus

SentimentLabel = Literal["positive", "negative", "neutral"]
_ALL_LABELS: tuple[SentimentLabel, ...] = ("positive", "negative", "neutral")
_PREDICTION_LABELS = (*_ALL_LABELS, "unavailable")

_MINIMUM_SCORE_COVERAGE = 0.80
_MINIMUM_MACRO_F1 = 0.70
_MINIMUM_PER_CLASS_F1 = 0.70


class SentimentEvaluationCase(BaseModel):
    """One attributed, human-labeled financial-news example."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(min_length=1)
    source_record: str = Field(min_length=1)
    headline: str = Field(min_length=1)
    expected_label: SentimentLabel
    source: str = Field(default="FinancialPhraseBank", min_length=1)
    instrument: str = Field(default="FINANCIAL_PHRASEBANK", min_length=1)
    currency: str | None = None


@dataclass(frozen=True)
class SentimentEvaluationItem:
    case_id: str
    expected_label: SentimentLabel
    predicted_label: str
    response_status: SentimentParseStatus | Literal["not_run"]
    sentiment_score: float | None
    confidence_score: float


@dataclass(frozen=True)
class SentimentEvaluationReport:
    items: tuple[SentimentEvaluationItem, ...]
    threshold: float
    termination_reason: Literal["timeout_limit_reached"] | None = None

    @property
    def correct(self) -> int:
        return sum(item.expected_label == item.predicted_label for item in self.items)

    @property
    def accuracy(self) -> float:
        return self.correct / len(self.items) if self.items else 0.0

    def as_dict(self, *, include_items: bool = True) -> dict[str, object]:
        class_counts = Counter(item.expected_label for item in self.items)
        class_correct = Counter(
            item.expected_label
            for item in self.items
            if item.expected_label == item.predicted_label
        )
        confusion = {
            expected: {
                predicted: sum(
                    item.expected_label == expected
                    and item.predicted_label == predicted
                    for item in self.items
                )
                for predicted in _PREDICTION_LABELS
            }
            for expected in _ALL_LABELS
        }
        per_class_metrics: dict[str, dict[str, int | float]] = {}
        class_f1_scores: list[float] = []
        for label in _ALL_LABELS:
            true_positive = confusion[label][label]
            predicted_count = sum(
                confusion[expected][label] for expected in _ALL_LABELS
            )
            recall = true_positive / class_counts[label] if class_counts[label] else 0.0
            precision = true_positive / predicted_count if predicted_count else 0.0
            f1 = (
                2 * precision * recall / (precision + recall)
                if precision + recall
                else 0.0
            )
            per_class_metrics[label] = {
                "correct": class_correct[label],
                "total": class_counts[label],
                "accuracy": recall,
                "precision": precision,
                "recall": recall,
                "f1": f1,
            }
            class_f1_scores.append(f1)

        total = len(self.items)
        evaluated_cases = sum(item.response_status != "not_run" for item in self.items)
        complete = evaluated_cases == total
        score_count = sum(
            item.response_status == "valid" and item.sentiment_score is not None
            for item in self.items
        )
        valid_response_count = sum(
            item.response_status == "valid" for item in self.items
        )
        coverage = score_count / total if total else 0.0
        macro_f1 = sum(class_f1_scores) / len(class_f1_scores)
        screening_passed = (
            complete
            and self.termination_reason is None
            and coverage >= _MINIMUM_SCORE_COVERAGE
            and macro_f1 >= _MINIMUM_MACRO_F1
            and all(
                metrics["f1"] >= _MINIMUM_PER_CLASS_F1
                for metrics in per_class_metrics.values()
            )
        )
        result: dict[str, object] = {
            "total": total,
            "evaluated_cases": evaluated_cases,
            "complete": complete,
            "termination_reason": self.termination_reason,
            "correct": self.correct,
            "accuracy": self.accuracy,
            "threshold": self.threshold,
            "coverage": coverage,
            "valid_response_rate": valid_response_count / total if total else 0.0,
            "valid_abstentions": sum(
                item.response_status == "valid" and item.sentiment_score is None
                for item in self.items
            ),
            "response_status_counts": dict(
                Counter(item.response_status for item in self.items)
            ),
            "per_class_accuracy": {
                label: {
                    "correct": class_correct[label],
                    "total": class_counts[label],
                    "accuracy": (
                        class_correct[label] / class_counts[label]
                        if class_counts[label]
                        else 0.0
                    ),
                }
                for label in _ALL_LABELS
            },
            "per_class_metrics": per_class_metrics,
            "macro_f1": macro_f1,
            "screening_criteria": {
                "minimum_score_coverage": _MINIMUM_SCORE_COVERAGE,
                "minimum_macro_f1": _MINIMUM_MACRO_F1,
                "minimum_per_class_f1": _MINIMUM_PER_CLASS_F1,
                "offline_only": True,
            },
            "screening_passed": screening_passed,
            "confusion_matrix": confusion,
        }
        if include_items:
            result["items"] = [asdict(item) for item in self.items]
        return result


def load_sentiment_evaluation_cases(path: Path) -> tuple[SentimentEvaluationCase, ...]:
    """Load strict JSONL cases and require examples for all three labels."""
    cases: list[SentimentEvaluationCase] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
            cases.append(SentimentEvaluationCase.model_validate(payload))
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(
                f"invalid sentiment evaluation case at line {line_number}"
            ) from exc
    if not cases:
        raise ValueError("sentiment evaluation dataset is empty")
    case_ids = [case.case_id for case in cases]
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("sentiment evaluation case IDs must be unique")
    missing = set(_ALL_LABELS) - {case.expected_label for case in cases}
    if missing:
        raise ValueError("sentiment evaluation dataset must cover all labels")
    return tuple(cases)


def classify_sentiment_score(score: float | None, *, threshold: float) -> str:
    """Map a parsed score to production-aligned polarity; abstentions stay distinct."""
    if not math.isfinite(threshold) or not 0.0 < threshold < 1.0:
        raise ValueError("sentiment threshold must be finite and between zero and one")
    if score is None:
        return "unavailable"
    if not math.isfinite(score) or not -1.0 <= score <= 1.0:
        raise ValueError("sentiment score must be finite and within [-1, 1]")
    if score > threshold:
        return "positive"
    if score < -threshold:
        return "negative"
    return "neutral"


def evaluate_sentiment_cases(
    client: LLMSentimentClient,
    cases: tuple[SentimentEvaluationCase, ...],
    *,
    threshold: float,
    max_timeouts: int | None = None,
) -> SentimentEvaluationReport:
    """Call only the sentiment client; no broker or persistence access."""
    if not cases:
        raise ValueError("sentiment evaluation requires at least one case")
    if max_timeouts is not None and max_timeouts < 1:
        raise ValueError("max_timeouts must be positive")
    items: list[SentimentEvaluationItem] = []
    timeout_count = 0
    termination_reason: Literal["timeout_limit_reached"] | None = None
    for index, case in enumerate(cases):
        # Keep the model context independent of the expected label.
        direction = "BUY"
        inference = client.analyze_with_status(
            [
                {
                    "headline": case.headline,
                    "source": case.source,
                    "published_at": None,
                    "currency": case.currency,
                    "instrument": case.instrument,
                    "impact": None,
                }
            ],
            {
                "instrument": case.instrument,
                "direction": direction,
                "reference_price": None,
                "signal_timestamp": None,
            },
        )
        result = inference.result
        items.append(
            SentimentEvaluationItem(
                case_id=case.case_id,
                expected_label=case.expected_label,
                predicted_label=classify_sentiment_score(
                    result.sentiment_score, threshold=threshold
                ),
                response_status=inference.status,
                sentiment_score=result.sentiment_score,
                confidence_score=result.confidence_score,
            )
        )
        if inference.status == "timeout":
            timeout_count += 1
        if max_timeouts is not None and timeout_count >= max_timeouts:
            termination_reason = "timeout_limit_reached"
            for not_run_case in cases[index + 1 :]:
                items.append(
                    SentimentEvaluationItem(
                        case_id=not_run_case.case_id,
                        expected_label=not_run_case.expected_label,
                        predicted_label="unavailable",
                        response_status="not_run",
                        sentiment_score=None,
                        confidence_score=0.0,
                    )
                )
            break
    return SentimentEvaluationReport(tuple(items), threshold, termination_reason)
