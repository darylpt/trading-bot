"""Network-free deterministic backtest adapter with account metrics."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Sequence

from domain.models import MarketCandle, TechnicalSignal
from strategy.friction import (
    FrictionAssumptions,
    effective_spread,
    execution_price,
    holding_cost,
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
    initial_equity: Decimal = Decimal("10000")
    position_size: Decimal = Decimal("1")
    friction: FrictionAssumptions = FrictionAssumptions(
        spread=Decimal("0.0001"),
        slippage=Decimal("0.00002"),
        overnight_swap=Decimal("0"),
    )
    session_policy: SessionPolicy = SessionPolicy()

    def __post_init__(self) -> None:
        if not self.initial_equity.is_finite() or self.initial_equity <= 0:
            raise ValueError("initial equity must be positive and finite")
        if not self.position_size.is_finite() or self.position_size <= 0:
            raise ValueError("position size must be positive and finite")


@dataclass(frozen=True)
class BacktestEvent:
    signal: TechnicalSignal
    execution_price: Decimal
    spread: Decimal
    slippage: Decimal
    swap: Decimal


@dataclass(frozen=True)
class BacktestTrade:
    direction: str
    entry_timestamp: datetime
    exit_timestamp: datetime
    entry_price: Decimal
    exit_price: Decimal
    gross_pnl: Decimal
    friction_cost: Decimal
    net_pnl: Decimal


@dataclass(frozen=True)
class BacktestMetrics:
    """Account-level performance measurements for one deterministic run."""

    initial_equity: Decimal
    final_equity: Decimal
    equity_curve: tuple[Decimal, ...]
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    max_drawdown: Decimal
    win_rate: Decimal
    profit_factor: Decimal
    sharpe_ratio: Decimal
    spread_cost: Decimal
    slippage_cost: Decimal
    swap_cost: Decimal
    total_friction_cost: Decimal
    trades: tuple[BacktestTrade, ...]


@dataclass(frozen=True)
class BacktestResult:
    signals: tuple[TechnicalSignal, ...]
    events: tuple[BacktestEvent, ...]
    friction: FrictionAssumptions
    metrics: BacktestMetrics


def _directional_pnl(
    direction: str,
    entry_price: Decimal,
    exit_price: Decimal,
    quantity: Decimal,
) -> Decimal:
    delta = exit_price - entry_price
    return delta * quantity if direction == "LONG" else -delta * quantity


def _max_drawdown(equity_curve: tuple[Decimal, ...]) -> Decimal:
    peak = equity_curve[0]
    largest = Decimal("0")
    for equity in equity_curve:
        peak = max(peak, equity)
        largest = max(largest, peak - equity)
    return largest


def _sharpe_ratio(equity_curve: tuple[Decimal, ...]) -> Decimal:
    if len(equity_curve) < 3:
        return Decimal("0")
    returns = [
        float((current - previous) / previous)
        for previous, current in zip(equity_curve, equity_curve[1:])
        if previous > 0
    ]
    if len(returns) < 2:
        return Decimal("0")
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
    if variance <= 0:
        return Decimal("0")
    return Decimal(str(mean / math.sqrt(variance)))


def _account_metrics(
    events: tuple[BacktestEvent, ...],
    candles: Sequence[MarketCandle],
    config: BacktestConfig,
) -> BacktestMetrics:
    equity = config.initial_equity
    equity_curve = [equity]
    trades: list[BacktestTrade] = []
    open_event: BacktestEvent | None = None
    spread_cost = Decimal("0")
    slippage_cost = Decimal("0")
    swap_cost = Decimal("0")

    def close_trade(event: BacktestEvent) -> None:
        nonlocal equity, open_event, spread_cost, slippage_cost, swap_cost
        if open_event is None:
            return
        held_hours = max(
            0,
            int(
                (
                    event.signal.signal_timestamp - open_event.signal.signal_timestamp
                ).total_seconds()
                // 3600
            ),
        )
        gross = _directional_pnl(
            open_event.signal.direction,
            open_event.execution_price,
            event.execution_price,
            config.position_size,
        )
        entry_spread = open_event.spread * config.position_size
        exit_spread = event.spread * config.position_size
        entry_slippage = open_event.slippage * config.position_size
        exit_slippage = event.slippage * config.position_size
        swap = holding_cost(config.friction, held_hours)
        friction_cost = (
            entry_spread + exit_spread + entry_slippage + exit_slippage + swap
        )
        net = gross - friction_cost
        equity += net
        equity_curve.append(equity)
        spread_cost += entry_spread + exit_spread
        slippage_cost += entry_slippage + exit_slippage
        swap_cost += swap
        trades.append(
            BacktestTrade(
                direction=open_event.signal.direction,
                entry_timestamp=open_event.signal.signal_timestamp,
                exit_timestamp=event.signal.signal_timestamp,
                entry_price=open_event.execution_price,
                exit_price=event.execution_price,
                gross_pnl=gross,
                friction_cost=friction_cost,
                net_pnl=net,
            )
        )
        open_event = None

    for event in events:
        if (
            open_event is not None
            and event.signal.direction != open_event.signal.direction
        ):
            close_trade(event)
        if open_event is None:
            open_event = event

    unrealized = Decimal("0")
    if open_event is not None and candles:
        mark = candles[-1].close
        unrealized = (
            _directional_pnl(
                open_event.signal.direction,
                open_event.execution_price,
                mark,
                config.position_size,
            )
            - (open_event.spread + open_event.slippage) * config.position_size
        )

    gross_winners = sum(
        (trade.net_pnl for trade in trades if trade.net_pnl > 0), Decimal("0")
    )
    gross_losers = -sum(
        (trade.net_pnl for trade in trades if trade.net_pnl < 0), Decimal("0")
    )
    win_rate = (
        Decimal(sum(trade.net_pnl > 0 for trade in trades)) / Decimal(len(trades))
        if trades
        else Decimal("0")
    )
    profit_factor = gross_winners / gross_losers if gross_losers else Decimal("0")
    realized = equity - config.initial_equity
    total_friction = spread_cost + slippage_cost + swap_cost
    final_equity = equity + unrealized
    return BacktestMetrics(
        initial_equity=config.initial_equity,
        final_equity=final_equity,
        equity_curve=tuple(equity_curve),
        realized_pnl=realized,
        unrealized_pnl=unrealized,
        max_drawdown=_max_drawdown(tuple(equity_curve)),
        win_rate=win_rate,
        profit_factor=profit_factor,
        sharpe_ratio=_sharpe_ratio(tuple(equity_curve)),
        spread_cost=spread_cost,
        slippage_cost=slippage_cost,
        swap_cost=swap_cost,
        total_friction_cost=total_friction,
        trades=tuple(trades),
    )


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
    for index in range(1, len(indicators.points) - 1):
        frame = type(indicators)(
            indicators.points[: index + 1],
            indicators.rsi_period,
            indicators.fast_period,
            indicators.slow_period,
            indicators.atr_period,
        )
        signal_candle = candles[index]
        execution_candle = candles[index + 1]
        signal = generate_signal(frame, signal_candle.timestamp, config=SignalConfig())
        if signal is None or not can_enter(
            observed_at=signal_candle.timestamp,
            now=signal_candle.timestamp,
            policy=selected.session_policy,
        ):
            continue
        expanded = is_boundary_window(
            execution_candle.timestamp, selected.session_policy
        )
        price = execution_candle.open
        adjusted_price = execution_price(
            price,
            signal.direction,
            selected.friction,
            session_expanded=expanded,
        )
        signals.append(signal)
        events.append(
            BacktestEvent(
                signal=signal,
                execution_price=adjusted_price,
                spread=effective_spread(selected.friction, session_expanded=expanded),
                slippage=selected.friction.slippage,
                swap=selected.friction.overnight_swap,
            )
        )
    event_tuple = tuple(events)
    return BacktestResult(
        tuple(signals),
        event_tuple,
        selected.friction,
        _account_metrics(event_tuple, candles, selected),
    )
