"""Safe container entrypoint; full broker orchestration is added later."""

from __future__ import annotations

import argparse
import logging
import math
import os
import time
from collections.abc import Sequence
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, TypeAdapter

from config.settings import Settings
from domain.models import MarketCandle
from persistence.sqlite import (
    ExecutionLogRecord,
    SQLiteRepository,
    SessionMetricsRecord,
)
from strategy.market_data import MarketDataError, load_csv_candles
from trading_bot.engine import PipelineConfig
from trading_bot.execution import PaperExecutionEngine
from trading_bot.market_data_seed import ensure_default_market_data
from trading_bot.risk import RiskDecision, evaluate_signal
from trading_bot.strategy import Signal, moving_average_signal

LOGGER = logging.getLogger(__name__)


def _boolean_env(name: str, default: str) -> bool:
    value = os.getenv(name, default).lower()
    if value == "true":
        return True
    if value == "false":
        return False
    raise ValueError(f"{name} must be true or false")


def _broker_environment(value: str) -> Literal["paper", "demo"]:
    if value == "paper":
        return "paper"
    if value == "demo":
        return "demo"
    raise ValueError("BROKER_ENV must be paper or demo")


def _optional_decimal_env(name: str) -> Decimal | None:
    raw_value = os.getenv(name)
    if not raw_value:
        return None
    try:
        return Decimal(raw_value)
    except InvalidOperation as exc:
        raise ValueError(f"{name} must be a valid decimal") from exc


def _fetch_market_data(
    data_dir: Path, *, minimum_history: int
) -> tuple[MarketCandle, ...]:
    configured_path = os.environ.get("MARKET_DATA_PATH")
    try:
        market_data_path = (
            Path(configured_path)
            if configured_path
            else ensure_default_market_data(data_dir)
        )
        return load_csv_candles(market_data_path, minimum_history=minimum_history)
    except (OSError, MarketDataError) as exc:
        LOGGER.warning(
            "market data fetch rejected path=%s reason=%s",
            market_data_path,
            exc,
        )
        return ()


def _evaluate_strategy(
    candles: Sequence[MarketCandle],
    *,
    timestamp: datetime,
    config: PipelineConfig,
) -> Signal:
    return moving_average_signal(
        tuple(candles),
        fast_period=config.fast_period,
        slow_period=config.slow_period,
        current_time=timestamp,
    )


def _check_order(signal: Signal | None, risk_decision: RiskDecision | None) -> bool:
    if signal is None:
        LOGGER.info("tick order check result=NO_ORDER")
        return False
    if risk_decision is None or not risk_decision.approved:
        LOGGER.info(
            "tick risk check result=REJECTED reason=%s",
            risk_decision.reason if risk_decision is not None else "NO_RISK_DECISION",
        )
        return False
    LOGGER.info(
        "tick order check result=%s instrument=%s position_size=%s",
        signal.action,
        signal.instrument,
        risk_decision.position_size,
    )
    return True


def run_tick(
    repository: SQLiteRepository,
    *,
    data_dir: Path = Path("data"),
    config: PipelineConfig | None = None,
    current_time: datetime | None = None,
    account_equity: Decimal | None = None,
    session_start_equity: Decimal | None = None,
    stop_distance: Decimal | None = None,
    daily_drawdown_limit: Decimal = Decimal("0.05"),
    execution_engine: PaperExecutionEngine | None = None,
) -> SessionMetricsRecord:
    """Fetch data, evaluate strategy and risk, then persist tick metrics."""
    timestamp = current_time or datetime.now(timezone.utc)
    active_config = config or PipelineConfig()
    candles = _fetch_market_data(
        data_dir,
        minimum_history=max(
            active_config.fast_period,
            active_config.slow_period,
        )
        + 1,
    )
    try:
        signal = _evaluate_strategy(candles, timestamp=timestamp, config=active_config)
    except (MarketDataError, ValueError) as exc:
        LOGGER.warning("strategy evaluation rejected reason=%s", exc)
        signal = None

    risk_decision = (
        evaluate_signal(
            signal,
            account_equity=account_equity,
            session_start_equity=session_start_equity,
            stop_distance=stop_distance,
            daily_drawdown_limit=daily_drawdown_limit,
        )
        if signal is not None
        else None
    )
    _check_order(signal, risk_decision)
    if risk_decision is not None and not risk_decision.approved:
        repository.save_execution_log(
            ExecutionLogRecord(
                client_order_id=f"tick-{timestamp.isoformat()}",
                event_type="RISK_REJECTED",
                provider="risk",
                error_class="RiskGuardrail",
                message=risk_decision.reason,
            )
        )
    active_execution = execution_engine or PaperExecutionEngine(repository)
    execution_outcome = active_execution.process_tick(
        candles,
        signal=signal,
        risk_decision=risk_decision,
        account_equity=account_equity,
        current_time=timestamp,
    )
    if execution_outcome.rejection_reason is not None and (
        risk_decision is None or risk_decision.approved
    ):
        repository.save_execution_log(
            ExecutionLogRecord(
                client_order_id=f"tick-{timestamp.isoformat()}",
                event_type="EXECUTION_REJECTED",
                provider="paper",
                error_class="PaperExecution",
                message=execution_outcome.rejection_reason,
            )
        )

    repository.record_session_metrics(timestamp.date())
    metrics = repository.get_session_metrics(timestamp.date())
    if metrics is None:
        raise RuntimeError("session metrics row was not persisted")
    return metrics


def _positive_seconds(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("interval must be a number") from exc
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("interval must be greater than zero")
    return seconds


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--once",
        action="store_true",
        help="run one market-data and strategy tick, then exit",
    )
    parser.add_argument(
        "--interval",
        type=_positive_seconds,
        default=None,
        metavar="SECONDS",
        help="daemon tick interval; overrides TICK_INTERVAL_SECONDS",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(argv)
    raw_endpoint = os.environ.get("BROKER_ENDPOINT")
    broker_endpoint = (
        TypeAdapter(AnyHttpUrl).validate_python(raw_endpoint) if raw_endpoint else None
    )

    daily_drawdown_limit = Decimal(os.getenv("DAILY_DRAWDOWN_LIMIT", "0.05") or "0.05")
    settings = Settings(
        paper_trading=_boolean_env("PAPER_TRADING", "true"),
        live_trading=_boolean_env("LIVE_TRADING", "false"),
        broker_environment=_broker_environment(
            os.getenv("BROKER_ENV", "demo") or "demo"
        ),
        broker_endpoint=broker_endpoint,
        daily_drawdown_limit=daily_drawdown_limit,
        broker_token=os.getenv("BROKER_TOKEN", "local-demo-token")
        or "local-demo-token",
    )
    account_equity = _optional_decimal_env("ACCOUNT_EQUITY")
    session_start_equity = _optional_decimal_env("SESSION_START_EQUITY")
    stop_distance = _optional_decimal_env("RISK_STOP_DISTANCE")
    repository = SQLiteRepository(settings.data_dir / "session_metrics.db")
    logging.basicConfig(level=logging.INFO)
    LOGGER.info(
        "paper/demo runtime ready provider=%s data_dir=%s sqlite_path=%s",
        settings.provider,
        settings.data_dir,
        repository.path,
    )

    def execute_tick() -> SessionMetricsRecord:
        return run_tick(
            repository,
            data_dir=settings.data_dir,
            account_equity=account_equity,
            session_start_equity=session_start_equity,
            stop_distance=stop_distance,
            daily_drawdown_limit=daily_drawdown_limit,
        )

    try:
        if args.once:
            execute_tick()
            return
        interval = (
            args.interval
            if args.interval is not None
            else _positive_seconds(os.getenv("TICK_INTERVAL_SECONDS", "60") or "60")
        )
        while True:
            execute_tick()
            time.sleep(interval)
    finally:
        repository.close()


if __name__ == "__main__":
    main()
