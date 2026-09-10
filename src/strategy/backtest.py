"""Network-free deterministic backtest adapter."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from domain.models import MarketCandle, TechnicalSignal
from strategy.friction import (
    FrictionAssumptions,
    effective_spread,
    execution_price,
)
from strategy.indicators import calculate_indicators
from strategy.sessions import SessionPolicy, can_enter, is_boundary_window
from strategy.signals import SignalConfig, generate_signal


@dataclass(frozen=True)
class BacktestConfig:
    rsi_period: int = 14
    fast_period: int = 5
    slow_period: int = 20
    atr_period: int = 14
    friction: FrictionAssumptions = FrictionAssumptions(
        spread=Decimal("0.0001"),
        slippage=Decimal("0.00002"),
        overnight_swap=Decimal("0"),
    )
    session_policy: SessionPolicy = SessionPolicy()


@dataclass(frozen=True)
class BacktestEvent:
    signal: TechnicalSignal
    execution_price: Decimal
    spread: Decimal
    slippage: Decimal
    swap: Decimal


@dataclass(frozen=True)
class BacktestResult:
    signals: tuple[TechnicalSignal, ...]
    events: tuple[BacktestEvent, ...]
    friction: FrictionAssumptions


def run_backtest(
    candles: Sequence[MarketCandle], config: BacktestConfig | None = None
) -> BacktestResult:
    """Evaluate completed candles deterministically, without network dependencies."""
    selected = config or BacktestConfig()
    indicators = calculate_indicators(
        candles,
        rsi_period=selected.rsi_period,
        fast_period=selected.fast_period,
        slow_period=selected.slow_period,
        atr_period=selected.atr_period,
    )
    signals: list[TechnicalSignal] = []
    events: list[BacktestEvent] = []
    for index in range(1, len(indicators.points)):
        frame = type(indicators)(
            indicators.points[: index + 1],
            indicators.rsi_period,
            indicators.fast_period,
            indicators.slow_period,
            indicators.atr_period,
        )
        candle = candles[index]
        signal = generate_signal(frame, candle.timestamp, config=SignalConfig())
        if signal is None or not can_enter(
            observed_at=candle.timestamp,
            now=candle.timestamp,
            policy=selected.session_policy,
        ):
            continue
        expanded = is_boundary_window(candle.timestamp, selected.session_policy)
        price = execution_price(
            signal.reference_price,
            signal.direction,
            selected.friction,
            session_expanded=expanded,
        )
        signals.append(signal)
        events.append(
            BacktestEvent(
                signal=signal,
                execution_price=price,
                spread=effective_spread(selected.friction, session_expanded=expanded),
                slippage=selected.friction.slippage,
                swap=selected.friction.overnight_swap,
            )
        )
    return BacktestResult(tuple(signals), tuple(events), selected.friction)
