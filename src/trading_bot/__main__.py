"""Safe container entrypoint for the paper/demo trading pipeline."""

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


from config.settings import Settings
from domain.models import MarketCandle
from persistence.sqlite import (
    ExecutionLogRecord,
    SQLiteRepository,
    SessionMetricsRecord,
)
from execution.broker_adapter import BrokerConnectionError
from observability.readiness import check_broker_demo_readiness
from execution.executor import ExecutionGate
from strategy.market_data import MarketDataError, load_csv_candles
from trading_bot.broker import (
    BrokerConnectionConfig,
    LiveMarketDataGateway,
    MT5BrokerAdapter,
    MT5QuoteSource,
    SimulatedQuoteSource,
)
from trading_bot.execution import BrokerDemoExecutionEngine, PaperExecutionEngine
from trading_bot.market_data_seed import ensure_default_market_data
from trading_bot.risk import RiskDecision, evaluate_signal
from trading_bot.engine import PipelineConfig
from trading_bot.runtime_config import RuntimeConfig
from trading_bot.strategy import Signal, evaluate_strategy

LOGGER = logging.getLogger(__name__)


def _boolean_env(name: str, default: str) -> bool:
    value = os.getenv(name, default).lower()
    if value == "true":
        return True
    if value == "false":
        return False
    raise ValueError(f"{name} must be true or false")


def _dynamic_motion(value: str) -> Literal["random_walk", "sine_wave"]:
    if value == "random_walk":
        return "random_walk"
    if value == "sine_wave":
        return "sine_wave"
    raise ValueError("DYNAMIC_MARKET_MOTION must be random_walk or sine_wave")


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
    data_dir: Path,
    *,
    instrument: str,
    minimum_history: int,
) -> tuple[MarketCandle, ...]:
    configured_path = os.environ.get("MARKET_DATA_PATH")
    try:
        market_data_path = (
            Path(configured_path)
            if configured_path
            else ensure_default_market_data(data_dir, instrument=instrument)
        )
        return load_csv_candles(
            market_data_path, instrument=instrument, minimum_history=minimum_history
        )
    except (OSError, MarketDataError) as exc:
        LOGGER.warning(
            "market data fetch rejected path=%s reason=%s",
            market_data_path,
            exc,
        )
        return ()


def _create_market_data_gateway(
    settings: Settings,
) -> LiveMarketDataGateway:
    if settings.provider != "exness_mt5":
        raise ValueError("unsupported broker provider")
    config = BrokerConnectionConfig.from_environment(
        data_dir=settings.data_dir,
        instrument=settings.instrument,
        token=settings.broker_token,
        account=settings.broker_account or settings.broker_account_id,
        server=settings.broker_server,
        endpoint=(
            str(settings.broker_endpoint)
            if settings.broker_endpoint is not None
            else None
        ),
        bridge_host=settings.exness_bridge_host,
        bridge_port=settings.exness_bridge_port,
        environment=settings.broker_environment,
        runtime_mode=(
            "BROKER_DEMO" if settings.trading_mode == "BROKER_DEMO" else "SIMULATED"
        ),
        future_tolerance_seconds=float(settings.max_clock_drift_seconds),
    )
    raw_motion = _dynamic_motion(os.getenv("DYNAMIC_MARKET_MOTION", "random_walk"))
    if settings.trading_mode == "SIMULATED":
        ensure_default_market_data(
            settings.data_dir,
            instrument=settings.instrument,
            refresh=False,
        )
    fallback = SimulatedQuoteSource(
        config.fallback_data_path,
        dynamic=settings.trading_mode == "SIMULATED"
        and _boolean_env("DYNAMIC_MARKET_DATA", "false"),
        motion=raw_motion,
    )
    source = MT5QuoteSource(config, fallback=fallback)
    adapter = MT5BrokerAdapter(source)
    if adapter.fallback_reason is not None:
        LOGGER.info(
            "broker quote mode=%s reason=%s", adapter.mode, adapter.fallback_reason
        )
    return LiveMarketDataGateway(
        adapter,
        data_path=config.fallback_data_path,
        instrument=config.instrument,
        load_existing=settings.trading_mode == "SIMULATED",
        allow_market_gaps=settings.trading_mode == "BROKER_DEMO",
    )


def _evaluate_strategy(
    candles: Sequence[MarketCandle],
    *,
    timestamp: datetime,
    config: PipelineConfig,
    instrument: str,
    strategy_name: str,
) -> Signal:
    return evaluate_strategy(
        strategy_name,
        tuple(candles),
        current_time=timestamp,
        instrument=instrument,
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


def _circuit_breaker_reason(
    candles: Sequence[MarketCandle],
    *,
    timestamp: datetime,
    gateway: LiveMarketDataGateway | None,
    max_data_age_seconds: float | None,
    max_clock_drift_seconds: float | None,
    max_spread: Decimal | None,
) -> tuple[str, Decimal | None]:
    """Return a fail-closed entry halt reason for the current market snapshot."""
    runtime_timestamp = (
        timestamp.replace(tzinfo=timezone.utc)
        if timestamp.tzinfo is None
        else timestamp.astimezone(timezone.utc)
    )
    quote = getattr(gateway, "last_quote", None)
    latest = (
        quote.observed_at
        if quote is not None
        else (candles[-1].timestamp if candles else None)
    )
    if latest is not None:
        latest = (
            latest.replace(tzinfo=timezone.utc)
            if latest.tzinfo is None
            else latest.astimezone(timezone.utc)
        )
    spread = quote.spread if quote is not None else None
    if not candles and (
        max_data_age_seconds is not None
        or max_clock_drift_seconds is not None
        or max_spread is not None
    ):
        return "market data unavailable", spread
    if not candles or latest is None:
        return "", spread
    age = (runtime_timestamp - latest).total_seconds()
    if max_clock_drift_seconds is not None and age < -max_clock_drift_seconds:
        return "market data clock is ahead of runtime clock", spread
    if max_data_age_seconds is not None and age > max_data_age_seconds:
        return f"market data stale ({age:.1f}s)", spread
    if max_spread is not None and max_spread > 0:
        if spread is None:
            return "market spread unavailable", spread
        if spread > max_spread:
            return f"market spread too wide ({spread})", spread
    return "", spread


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
    execution_engine: PaperExecutionEngine | BrokerDemoExecutionEngine | None = None,
    market_data_gateway: LiveMarketDataGateway | None = None,
    execution_gate: ExecutionGate | None = None,
    max_data_age_seconds: float | None = None,
    max_clock_drift_seconds: float | None = None,
    max_spread: Decimal | None = None,
) -> SessionMetricsRecord:
    """Fetch live or paper data, then evaluate strategy, risk, and execution."""
    timestamp = current_time or datetime.now(timezone.utc)
    active_config = config or PipelineConfig()
    runtime_config = RuntimeConfig(repository.path)
    active_instrument = runtime_config.get_active_instrument()
    active_strategy = runtime_config.get_active_strategy()
    if execution_gate is not None:
        execution_gate.reconcile()
    minimum_history = max(active_config.fast_period, active_config.slow_period) + 1
    if market_data_gateway is None:
        candles = _fetch_market_data(
            data_dir,
            instrument=active_instrument,
            minimum_history=minimum_history,
        )
    else:
        if isinstance(market_data_gateway, LiveMarketDataGateway):
            market_data_gateway.set_instrument(active_instrument)
        try:
            live_candles = market_data_gateway.poll()
        except (BrokerConnectionError, MarketDataError, OSError, ValueError) as exc:
            LOGGER.warning("live market data rejected reason=%s", type(exc).__name__)
            live_candles = ()
        candles = live_candles
    circuit_reason, spread = _circuit_breaker_reason(
        candles,
        timestamp=timestamp,
        gateway=market_data_gateway,
        max_data_age_seconds=max_data_age_seconds,
        max_clock_drift_seconds=max_clock_drift_seconds,
        max_spread=max_spread,
    )
    repository.save_circuit_breaker(
        status="HALTED" if circuit_reason else "CLEAR",
        reason=circuit_reason or "market data healthy",
        candle_timestamp=candles[-1].timestamp if candles else None,
        spread=spread,
        updated_at=timestamp,
    )
    trading_halted, halt_reason = repository.get_trading_halt()
    if circuit_reason or trading_halted:
        if trading_halted:
            LOGGER.warning("entry halt active reason=%s", halt_reason)
        signal = None
    else:
        try:
            signal = _evaluate_strategy(
                candles,
                timestamp=timestamp,
                config=active_config,
                instrument=active_instrument,
                strategy_name=active_strategy,
            )
        except (MarketDataError, ValueError) as exc:
            LOGGER.warning("strategy evaluation rejected reason=%s", exc)
            signal = None
    decision_rationale = (
        signal.rationale
        if signal is not None
        else (
            f"Emergency halt active: {halt_reason}"
            if trading_halted
            else "Strategy evaluation rejected before a signal was produced"
        )
    )
    repository.save_execution_log(
        ExecutionLogRecord(
            strategy_name=active_strategy,
            client_order_id=f"tick-{timestamp.isoformat()}",
            instrument=active_instrument,
            event_type=(
                f"STRATEGY_{signal.action}"
                if signal is not None
                else "STRATEGY_REJECTED"
            ),
            provider="strategy",
            error_class=None if signal is not None else "StrategyEvaluation",
            message=decision_rationale,
            decision_rationale=decision_rationale,
            reference_price=signal.reference_price if signal is not None else None,
            fast_average=signal.fast_average if signal is not None else None,
            slow_average=signal.slow_average if signal is not None else None,
            distance_to_crossover=(
                signal.distance_to_crossover if signal is not None else None
            ),
        )
    )

    risk_decision = (
        evaluate_signal(
            signal,
            instrument=active_instrument,
            account_equity=account_equity,
            session_start_equity=session_start_equity,
            stop_distance=stop_distance,
            daily_drawdown_limit=daily_drawdown_limit,
        )
        if signal is not None
        else None
    )
    if risk_decision is not None and not risk_decision.approved:
        repository.save_execution_log(
            ExecutionLogRecord(
                client_order_id=f"tick-{timestamp.isoformat()}",
                instrument=active_instrument,
                strategy_name=active_strategy,
                event_type="RISK_REJECTED",
                provider="risk",
                error_class="RiskGuardrail",
                message=risk_decision.reason,
                decision_rationale=(
                    f"{decision_rationale}; Risk rejected: {risk_decision.reason}"
                ),
                reference_price=signal.reference_price if signal is not None else None,
                fast_average=signal.fast_average if signal is not None else None,
                slow_average=signal.slow_average if signal is not None else None,
                distance_to_crossover=(
                    signal.distance_to_crossover if signal is not None else None
                ),
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
    execution_provider = (
        "broker_demo"
        if isinstance(active_execution, BrokerDemoExecutionEngine)
        else "paper"
    )
    if execution_outcome.rejection_reason is not None and (
        risk_decision is None or risk_decision.approved
    ):
        repository.save_execution_log(
            ExecutionLogRecord(
                client_order_id=f"tick-{timestamp.isoformat()}",
                instrument=active_instrument,
                strategy_name=active_strategy,
                event_type="EXECUTION_REJECTED",
                provider=execution_provider,
                error_class=(
                    "BrokerDemoExecution"
                    if execution_provider == "broker_demo"
                    else "PaperExecution"
                ),
                message=execution_outcome.rejection_reason,
                decision_rationale=(
                    f"{decision_rationale}; Execution rejected: "
                    f"{execution_outcome.rejection_reason}"
                ),
                reference_price=signal.reference_price if signal is not None else None,
                fast_average=signal.fast_average if signal is not None else None,
                slow_average=signal.slow_average if signal is not None else None,
                distance_to_crossover=(
                    signal.distance_to_crossover if signal is not None else None
                ),
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
    settings = Settings()
    repository = SQLiteRepository(settings.session_database_path)
    RuntimeConfig(repository.path)
    account_equity = _optional_decimal_env("ACCOUNT_EQUITY")
    session_start_equity = _optional_decimal_env("SESSION_START_EQUITY")
    stop_distance = _optional_decimal_env("RISK_STOP_DISTANCE")
    market_data_gateway = _create_market_data_gateway(settings)
    logging.basicConfig(level=logging.INFO)
    execution_engine: PaperExecutionEngine | BrokerDemoExecutionEngine | None = None
    execution_gate: ExecutionGate | None = None
    if settings.trading_mode == "BROKER_DEMO":
        if not isinstance(market_data_gateway.source, MT5BrokerAdapter):
            repository.close()
            raise BrokerConnectionError("broker-demo gateway is not configured")
        broker = market_data_gateway.source.broker
        if broker is None:
            repository.close()
            raise BrokerConnectionError("broker-demo adapter is unavailable")
        try:
            bridge_health = market_data_gateway.source.health_check(
                expected_server=settings.broker_server or "",
                expected_account_id=(
                    settings.broker_account or settings.broker_account_id or ""
                ),
                max_clock_drift_seconds=float(settings.max_clock_drift_seconds),
            )
            LOGGER.info(
                "MT5 bridge ready server=%s terminal_connected=%s authorized=%s",
                bridge_health.server,
                bridge_health.terminal_connected,
                bridge_health.authorized,
            )
            readiness = check_broker_demo_readiness(
                settings,
                broker,
                repository,
                instrument=settings.instrument,
            )
            market_data_gateway.backfill(
                broker.get_historical_candles(
                    settings.instrument,
                    timeframe="15m",
                    limit=256,
                )
            )
        except (BrokerConnectionError, MarketDataError, ValueError) as exc:
            repository.set_trading_halt(True, reason=type(exc).__name__)
            repository.close()
            raise BrokerConnectionError(
                "broker-demo startup validation failed"
            ) from exc
        if account_equity is None:
            account_equity = readiness.account_equity
        if session_start_equity is None:
            session_start_equity = readiness.account_equity
        execution_engine = BrokerDemoExecutionEngine(
            repository,
            broker,
            max_spread=settings.max_spread,
        )
        execution_gate = ExecutionGate(
            broker,
            environment="DEMO",
            repository=repository,
        )
    LOGGER.info(
        "paper/demo runtime ready mode=%s provider=%s data_dir=%s sqlite_path=%s",
        settings.trading_mode,
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
            daily_drawdown_limit=settings.daily_drawdown_limit,
            execution_engine=execution_engine,
            execution_gate=execution_gate,
            market_data_gateway=market_data_gateway,
            max_data_age_seconds=float(settings.max_data_age_seconds),
            max_clock_drift_seconds=float(settings.max_clock_drift_seconds),
            max_spread=settings.max_spread,
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
