"""Run a paper-only, no-order diagnostic of the configured sentiment provider."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from config.settings import Settings
from sentiment.evaluation import (
    evaluate_sentiment_cases,
    load_sentiment_evaluation_cases,
)
from sentiment.llm_client import (
    LLMSentimentClient,
    create_ollama_sentiment_adapter,
    create_openai_sentiment_adapter,
)

_DEFAULT_CASES = Path(__file__).parents[1] / "sentiment" / "eurusd_news_eval.jsonl"
_DEFAULT_DATASET_NAME = (
    "Forex News Annotated Dataset for Sentiment Analysis, balanced EURUSD subset"
)
_DEFAULT_DATASET_SCOPE = (
    "90 manually labeled EURUSD headlines from Forex Live and FX Street, collected "
    "January-May 2023; pair-specific short-term impact labels, not profitability"
)

_MAX_PROVIDER_TIMEOUTS = 3


def _configured_sentiment_client(
    settings: Settings, *, model_override: str | None = None
) -> LLMSentimentClient:
    if settings.sentiment_provider == "ollama":
        if settings.ollama_base_url is None:
            raise ValueError("OLLAMA_BASE_URL is required for sentiment evaluation")
        return create_ollama_sentiment_adapter(
            str(settings.ollama_base_url),
            model=model_override or settings.ollama_model,
            timeout_seconds=30.0,
        )
    if model_override is not None:
        raise ValueError("--model is supported only with the Ollama provider")
    if settings.openai_api_key is None:
        raise ValueError("OPENAI_API_KEY is required for sentiment evaluation")
    return create_openai_sentiment_adapter(settings.openai_api_key)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate configured sentiment inference against a labeled benchmark. "
            "This command only calls the sentiment provider; it never connects to "
            "a broker or writes runtime state."
        )
    )
    parser.add_argument(
        "--cases",
        type=Path,
        default=_DEFAULT_CASES,
        help="UTF-8 JSONL cases (defaults to the bundled attributed subset)",
    )
    parser.add_argument(
        "--dataset-name",
        help="Dataset label for output when using a caller-supplied case file",
    )
    parser.add_argument(
        "--model",
        action="append",
        dest="models",
        help="Ollama model to evaluate; repeat to compare models",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Omit per-case output while retaining aggregate model metrics",
    )
    args = parser.parse_args(argv)

    try:
        settings = Settings(_env_file=None)
    except ValueError as exc:
        raise SystemExit(
            f"sentiment evaluation configuration rejected ({type(exc).__name__})"
        ) from None
    if not settings.paper_trading or settings.live_trading:
        raise SystemExit(
            "sentiment evaluation requires PAPER_TRADING=true and LIVE_TRADING=false"
        )
    if settings.trading_mode == "LIVE":
        raise SystemExit("sentiment evaluation refuses LIVE mode")
    if args.models and settings.sentiment_provider != "ollama":
        raise SystemExit("--model is supported only with the Ollama provider")
    models = args.models or [
        settings.ollama_model
        if settings.sentiment_provider == "ollama"
        else "gpt-4o-mini"
    ]
    if any(not model or model != model.strip() for model in models):
        raise SystemExit("model names must be non-empty and have no surrounding spaces")
    if len(set(models)) != len(models):
        raise SystemExit("model names must be unique")

    try:
        cases = load_sentiment_evaluation_cases(args.cases)
        model_results: list[dict[str, object]] = []
        unattempted_models: list[str] = []
        for model_index, model in enumerate(models):
            client = _configured_sentiment_client(
                settings,
                model_override=model
                if settings.sentiment_provider == "ollama"
                else None,
            )
            report = evaluate_sentiment_cases(
                client,
                cases,
                threshold=float(settings.sentiment_threshold),
                max_timeouts=_MAX_PROVIDER_TIMEOUTS,
            )
            model_results.append(
                {
                    "model": model,
                    "results": report.as_dict(include_items=not args.summary_only),
                }
            )
            if report.termination_reason is not None:
                unattempted_models = models[model_index + 1 :]
                break
    except (OSError, ValueError) as exc:
        raise SystemExit(
            f"sentiment evaluation failed ({type(exc).__name__})"
        ) from None

    is_default_dataset = args.cases.resolve() == _DEFAULT_CASES.resolve()
    output: dict[str, object] = {
        "provider": settings.sentiment_provider,
        "dataset": (
            args.dataset_name
            or (_DEFAULT_DATASET_NAME if is_default_dataset else args.cases.name)
        ),
        "dataset_scope": (
            _DEFAULT_DATASET_SCOPE
            if is_default_dataset
            else "Caller-supplied labeled cases; provenance is not asserted by this tool"
        ),
        "dataset_attribution": (
            "Fatouros et al. (2023), Zenodo DOI 10.5281/zenodo.7976208, CC BY 4.0; "
            "source headlines from Forex Live and FX Street"
            if is_default_dataset
            else None
        ),
        "runtime_boundary": (
            "sentiment-client-only; no broker, risk, execution, or database access"
        ),
    }
    if args.models:
        output["model_comparison"] = model_results
        if unattempted_models:
            output["unattempted_models"] = unattempted_models
            output["comparison_stop_reason"] = "timeout_limit_reached"
    else:
        output["model"] = model_results[0]["model"]
        output["results"] = model_results[0]["results"]
    json.dump(output, sys.stdout, indent=2, sort_keys=True, allow_nan=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
