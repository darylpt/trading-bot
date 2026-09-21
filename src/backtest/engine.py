"""Offline, candle-by-candle XAUUSDm backtesting with conservative filters."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from decimal import Decimal, InvalidOperation
from typing import Callable, Literal, Sequence
from domain.models import MarketCandle
from trading_bot.strategy import Signal, SignalAction, moving_average_signal

Direction = Literal["LONG", "SHORT"]
ExitReason = Literal[
    "STOP_LOSS",
    "TAKE_PROFIT",
    "BREAKEVEN_STOP",
    "OPPOSITE_SIGNAL",
    "END_OF_DATA",
]
SignalGenerator = Callable[[tuple[MarketCandle, ...]], Signal]
Timeframe = Literal["1m", "5m", "15m", "1h"]


@dataclass(frozen=True)
class BacktestBar:
    """One completed historical candle used by the replay loop."""

    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    tick_volume: Decimal
    spread: Decimal

    def to_candle(self, instrument: str, timeframe: Timeframe) -> MarketCandle:
        return MarketCandle(
            instrument=instrument,
            timeframe=timeframe,
            timestamp=self.timestamp,
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.tick_volume,
        )


@dataclass(frozen=True)
class BacktestConfig:
    instrument: str = "XAUUSDm"
    timeframe: Literal["1m", "5m"] = "1m"
    initial_equity: Decimal = Decimal("10000")
    quantity: Decimal = Decimal("0.01")
    contract_size: Decimal = Decimal("100")
    pip_size: Decimal = Decimal("0.01")
    spread_pips: Decimal = Decimal("0.35")
    slippage_pips: Decimal = Decimal("2.0")
    commission_per_lot: Decimal = Decimal("7.00")
    commission_round_turn_per_lot: Decimal | None = None
    stop_loss_pips: Decimal = Decimal("100")
    take_profit_pips: Decimal = Decimal("200")
    fast_period: int = 5
    slow_period: int = 20
    confirmation_bars: int = 1
    exit_cooldown_bars: int = 0

    # Filter policy. The defaults implement the requested filtered strategy.
    enable_filters: bool = True
    trend_timeframe: Literal["15m", "1h"] = "1h"
    trend_period: int = 200
    session_start_hour: int = 12
    session_end_hour: int = 16
    max_spread_points: Decimal = Decimal("50")
    adx_timeframe: Literal["5m", "15m"] = "15m"
    adx_period: int = 14
    minimum_adx: Decimal = Decimal("20")
    atr_period: int = 14
    atr_average_period: int = 20

    # Exit policy. ATR exits replace fixed exits when enabled.
    use_atr_exits: bool = True
    atr_stop_multiple: Decimal = Decimal("1.5")
    atr_target_multiple: Decimal = Decimal("2.0")
    enable_trailing_lock: bool = True
    trailing_lock_multiple: Decimal = Decimal("1.0")

    def __post_init__(self) -> None:
        positive = (
            self.initial_equity,
            self.quantity,
            self.contract_size,
            self.pip_size,
            self.stop_loss_pips,
            self.take_profit_pips,
            self.minimum_adx,
            self.atr_stop_multiple,
            self.atr_target_multiple,
            self.trailing_lock_multiple,
        )
        if any(
            not Decimal(str(value)).is_finite() or Decimal(str(value)) <= 0
            for value in positive
        ):
            raise ValueError(
                "equity, sizing, pip, and indicator values must be positive"
            )
        non_negative: tuple[Decimal, ...] = (
            self.spread_pips,
            self.slippage_pips,
            self.commission_per_lot,
            self.max_spread_points,
        )
        if self.commission_round_turn_per_lot is not None:
            non_negative += (self.commission_round_turn_per_lot,)
        if any(
            not Decimal(str(value)).is_finite() or Decimal(str(value)) < 0
            for value in non_negative
        ):
            raise ValueError(
                "friction and spread limits must be finite and non-negative"
            )
        if self.confirmation_bars <= 0 or self.exit_cooldown_bars < 0:
            raise ValueError("confirmation must be positive and cooldown non-negative")
        if self.trend_period <= 0 or self.adx_period <= 0 or self.atr_period <= 0:
            raise ValueError("indicator periods must be positive")
        if self.atr_average_period <= 0:
            raise ValueError("ATR average period must be positive")
        if (
            not 0 <= self.session_start_hour <= 23
            or not 1 <= self.session_end_hour <= 24
        ):
            raise ValueError("session hours must be within UTC day bounds")
        if self.session_start_hour >= self.session_end_hour:
            raise ValueError("session must have a positive UTC duration")

    @property
    def spread_price(self) -> Decimal:
        return self.spread_pips * self.pip_size

    @property
    def slippage_price(self) -> Decimal:
        return self.slippage_pips * self.pip_size

    @property
    def stop_distance(self) -> Decimal:
        return self.stop_loss_pips * self.pip_size

    @property
    def target_distance(self) -> Decimal:
        return self.take_profit_pips * self.pip_size

    @property
    def commission_cost(self) -> Decimal:
        rate = (
            self.commission_round_turn_per_lot
            if self.commission_round_turn_per_lot is not None
            else self.commission_per_lot * Decimal("2")
        )
        return rate * self.quantity


@dataclass(frozen=True)
class BacktestTrade:
    direction: Direction
    entry_timestamp: datetime
    exit_timestamp: datetime
    entry_price: Decimal
    exit_price: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    exit_reason: ExitReason
    gross_pnl: Decimal
    spread_cost: Decimal
    slippage_cost: Decimal
    commission: Decimal
    net_pnl: Decimal

    @property
    def duration(self) -> timedelta:
        return self.exit_timestamp - self.entry_timestamp


@dataclass(frozen=True)
class BacktestMetrics:
    initial_equity: Decimal
    final_equity: Decimal
    net_profit: Decimal
    total_return_pct: Decimal
    win_rate_pct: Decimal
    profit_factor: Decimal
    max_drawdown_pct: Decimal
    total_trades: int
    average_trade_duration_minutes: Decimal
    gross_profit: Decimal
    gross_loss: Decimal
    spread_cost: Decimal
    slippage_cost: Decimal
    commission: Decimal


@dataclass(frozen=True)
class BacktestResult:
    bars: int
    trades: tuple[BacktestTrade, ...]
    equity_curve: tuple[Decimal, ...]
    metrics: BacktestMetrics
    filter_rejections: tuple[tuple[str, int], ...] = ()


@dataclass
class _OpenPosition:
    direction: Direction
    entry_timestamp: datetime
    entry_price: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    atr_at_entry: Decimal | None
    trailing_armed: bool = False


@dataclass(frozen=True)
class _AggregatedBar:
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal


@dataclass(frozen=True)
class _IndicatorSnapshot:
    trend_ema: Decimal | None
    adx: Decimal | None
    m1_atr: Decimal | None
    m1_atr_average: Decimal | None


def _decimal(value: object, field: str, *, non_negative: bool = False) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"invalid {field}") from exc
    if not parsed.is_finite() or (parsed < 0 if non_negative else parsed <= 0):
        qualifier = "non-negative" if non_negative else "positive"
        raise ValueError(f"{field} must be finite and {qualifier}")
    return parsed


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid candle timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("candle timestamp must include timezone")
    return parsed.astimezone(timezone.utc)


def load_bars(
    path: str | Path, *, timeframe: Literal["1m", "5m"] = "1m"
) -> tuple[BacktestBar, ...]:
    """Load and validate completed MT5 OHLCV bars without synthesizing gaps."""
    rows: list[BacktestBar] = []
    with Path(path).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "tick_volume",
            "spread",
        }
        if not required.issubset(reader.fieldnames or set()):
            missing = sorted(required - set(reader.fieldnames or set()))
            raise ValueError(f"CSV is missing required columns: {', '.join(missing)}")
        for row in reader:
            bar = BacktestBar(
                timestamp=_timestamp(row["timestamp"]),
                open=_decimal(row["open"], "open"),
                high=_decimal(row["high"], "high"),
                low=_decimal(row["low"], "low"),
                close=_decimal(row["close"], "close"),
                tick_volume=_decimal(
                    row["tick_volume"], "tick_volume", non_negative=True
                ),
                spread=_decimal(row["spread"], "spread", non_negative=True),
            )
            if bar.high < max(bar.open, bar.close) or bar.low > min(
                bar.open, bar.close
            ):
                raise ValueError(
                    f"invalid OHLC relationship at {bar.timestamp.isoformat()}"
                )
            rows.append(bar)
    if not rows:
        raise ValueError("CSV contains no candles")
    expected = timedelta(minutes=1 if timeframe == "1m" else 5)
    for previous, current in zip(rows, rows[1:]):
        if current.timestamp <= previous.timestamp:
            raise ValueError("candles must be strictly chronological")
        if current.timestamp - previous.timestamp < expected:
            raise ValueError("candles contain a duplicate or sub-timeframe interval")
    return tuple(rows)


def _signal_relation(signal: Signal) -> int:
    if signal.fast_average is None or signal.slow_average is None:
        return 0
    if signal.fast_average > signal.slow_average:
        return 1
    if signal.fast_average < signal.slow_average:
        return -1
    return 0


def _signal_generator(config: BacktestConfig) -> SignalGenerator:
    def generate(candles: tuple[MarketCandle, ...]) -> Signal:
        current = moving_average_signal(
            candles,
            fast_period=config.fast_period,
            slow_period=config.slow_period,
            current_time=candles[-1].timestamp,
            instrument=config.instrument,
        )
        if config.confirmation_bars == 1:
            return current
        if len(candles) < config.slow_period + config.confirmation_bars:
            return Signal(
                action="HOLD",
                timestamp=current.timestamp,
                instrument=current.instrument,
                reference_price=current.reference_price,
                fast_average=current.fast_average,
                slow_average=current.slow_average,
                rationale="Insufficient bars for crossover confirmation",
            )
        relations = [_signal_relation(current)]
        for offset in range(1, config.confirmation_bars):
            previous = moving_average_signal(
                candles[:-offset],
                fast_period=config.fast_period,
                slow_period=config.slow_period,
                current_time=candles[-offset - 1].timestamp,
                instrument=config.instrument,
            )
            relations.append(_signal_relation(previous))
        prior = moving_average_signal(
            candles[: -config.confirmation_bars],
            fast_period=config.fast_period,
            slow_period=config.slow_period,
            current_time=candles[-config.confirmation_bars - 1].timestamp,
            instrument=config.instrument,
        )
        prior_relation = _signal_relation(prior)
        action: SignalAction = "HOLD"
        if all(relation == 1 for relation in relations) and prior_relation != 1:
            action = "BUY"
        elif all(relation == -1 for relation in relations) and prior_relation != -1:
            action = "SELL"
        return Signal(
            action=action,
            timestamp=current.timestamp,
            instrument=current.instrument,
            reference_price=current.reference_price,
            fast_average=current.fast_average,
            slow_average=current.slow_average,
            rationale=f"{current.rationale}; {config.confirmation_bars}-bar confirmation",
        )

    return generate


def _bucket_start(timestamp: datetime, minutes: int) -> datetime:
    minute = (timestamp.minute // minutes) * minutes
    return timestamp.replace(minute=minute, second=0, microsecond=0)


def _aggregate_bars(
    bars: Sequence[BacktestBar], minutes: int, *, source_minutes: int
) -> tuple[tuple[_AggregatedBar, ...], tuple[int, ...]]:
    if minutes % source_minutes != 0:
        raise ValueError("higher timeframe must be divisible by source timeframe")
    expected = timedelta(minutes=source_minutes)
    bars_per_group = minutes // source_minutes
    groups: dict[datetime, list[tuple[int, BacktestBar]]] = {}
    for index, bar in enumerate(bars):
        groups.setdefault(_bucket_start(bar.timestamp, minutes), []).append(
            (index, bar)
        )

    aggregated: list[_AggregatedBar] = []
    end_indices: list[int] = []
    for bucket, entries in sorted(groups.items()):
        entries.sort(key=lambda item: item[1].timestamp)
        if len(entries) != bars_per_group:
            continue
        if any(
            current.timestamp - previous.timestamp != expected
            for (_, previous), (_, current) in zip(entries, entries[1:])
        ):
            continue
        if entries[0][1].timestamp != bucket:
            continue
        first = entries[0][1]
        last = entries[-1][1]
        aggregated.append(
            _AggregatedBar(
                timestamp=bucket,
                open=first.open,
                high=max(entry.high for _, entry in entries),
                low=min(entry.low for _, entry in entries),
                close=last.close,
            )
        )
        end_indices.append(entries[-1][0])

    latest_by_bar: list[int] = []
    group_index = -1
    for index in range(len(bars)):
        while (
            group_index + 1 < len(end_indices) and end_indices[group_index + 1] <= index
        ):
            group_index += 1
        latest_by_bar.append(group_index)
    return tuple(aggregated), tuple(latest_by_bar)


def _ema(values: Sequence[Decimal], period: int) -> tuple[Decimal | None, ...]:
    result: list[Decimal | None] = [None] * len(values)
    if len(values) < period:
        return tuple(result)
    current = sum(values[:period], Decimal("0")) / Decimal(period)
    result[period - 1] = current
    multiplier = Decimal("2") / Decimal(period + 1)
    for index in range(period, len(values)):
        current = (values[index] - current) * multiplier + current
        result[index] = current
    return tuple(result)


def _true_ranges(bars: Sequence[_AggregatedBar]) -> list[Decimal]:
    ranges: list[Decimal] = []
    for index, bar in enumerate(bars):
        if index == 0:
            ranges.append(bar.high - bar.low)
            continue
        previous_close = bars[index - 1].close
        ranges.append(
            max(
                bar.high - bar.low,
                abs(bar.high - previous_close),
                abs(bar.low - previous_close),
            )
        )
    return ranges


def _atr(values: Sequence[Decimal], period: int) -> tuple[Decimal | None, ...]:
    result: list[Decimal | None] = [None] * len(values)
    if len(values) < period:
        return tuple(result)
    current = sum(values[:period], Decimal("0")) / Decimal(period)
    result[period - 1] = current
    for index in range(period, len(values)):
        current = (current * Decimal(period - 1) + values[index]) / Decimal(period)
        result[index] = current
    return tuple(result)


def _rolling_average(
    values: Sequence[Decimal | None], period: int
) -> tuple[Decimal | None, ...]:
    result: list[Decimal | None] = [None] * len(values)
    for index in range(period - 1, len(values)):
        window = values[index - period + 1 : index + 1]
        if all(value is not None for value in window):
            result[index] = sum(
                (value for value in window if value is not None), Decimal("0")
            ) / Decimal(period)
    return tuple(result)


def _adx(bars: Sequence[_AggregatedBar], period: int) -> tuple[Decimal | None, ...]:
    result: list[Decimal | None] = [None] * len(bars)
    if len(bars) < period * 2:
        return tuple(result)
    true_ranges = _true_ranges(bars)
    plus_dm = [Decimal("0")]
    minus_dm = [Decimal("0")]
    for previous, current in zip(bars, bars[1:]):
        upward = current.high - previous.high
        downward = previous.low - current.low
        plus_dm.append(upward if upward > downward and upward > 0 else Decimal("0"))
        minus_dm.append(
            downward if downward > upward and downward > 0 else Decimal("0")
        )

    smoothed_tr = sum(true_ranges[1 : period + 1], Decimal("0"))
    smoothed_plus = sum(plus_dm[1 : period + 1], Decimal("0"))
    smoothed_minus = sum(minus_dm[1 : period + 1], Decimal("0"))
    dx_values: list[Decimal] = []
    for index in range(period, len(bars)):
        if index > period:
            smoothed_tr = (
                smoothed_tr - smoothed_tr / Decimal(period) + true_ranges[index]
            )
            smoothed_plus = (
                smoothed_plus - smoothed_plus / Decimal(period) + plus_dm[index]
            )
            smoothed_minus = (
                smoothed_minus - smoothed_minus / Decimal(period) + minus_dm[index]
            )
        plus_di = (
            smoothed_plus / smoothed_tr * Decimal("100")
            if smoothed_tr > 0
            else Decimal("0")
        )
        minus_di = (
            smoothed_minus / smoothed_tr * Decimal("100")
            if smoothed_tr > 0
            else Decimal("0")
        )
        denominator = plus_di + minus_di
        dx = (
            abs(plus_di - minus_di) / denominator * Decimal("100")
            if denominator > 0
            else Decimal("0")
        )
        dx_values.append(dx)
        if len(dx_values) >= period:
            if len(dx_values) == period:
                result[index] = sum(dx_values, Decimal("0")) / Decimal(period)
            else:
                previous_adx = result[index - 1]
                if previous_adx is not None:
                    result[index] = (previous_adx * Decimal(period - 1) + dx) / Decimal(
                        period
                    )
    return tuple(result)


def _indicator_snapshots(
    bars: Sequence[BacktestBar], config: BacktestConfig
) -> tuple[_IndicatorSnapshot, ...]:
    m1_ranges = _true_ranges(
        tuple(
            _AggregatedBar(bar.timestamp, bar.open, bar.high, bar.low, bar.close)
            for bar in bars
        )
    )
    m1_atr = _atr(m1_ranges, config.atr_period)
    m1_atr_average = _rolling_average(m1_atr, config.atr_average_period)
    source_minutes = 1 if config.timeframe == "1m" else 5
    trend_minutes = 15 if config.trend_timeframe == "15m" else 60
    trend_bars, trend_mapping = _aggregate_bars(
        bars, trend_minutes, source_minutes=source_minutes
    )
    trend_ema = _ema(tuple(bar.close for bar in trend_bars), config.trend_period)

    adx_minutes = 5 if config.adx_timeframe == "5m" else 15
    adx_bars, adx_mapping = _aggregate_bars(
        bars, adx_minutes, source_minutes=source_minutes
    )
    adx_values = _adx(adx_bars, config.adx_period)

    snapshots: list[_IndicatorSnapshot] = []
    for index, bar in enumerate(bars):
        trend_index = trend_mapping[index]
        adx_index = adx_mapping[index]
        snapshots.append(
            _IndicatorSnapshot(
                trend_ema=(trend_ema[trend_index] if trend_index >= 0 else None),
                adx=adx_values[adx_index] if adx_index >= 0 else None,
                m1_atr=m1_atr[index],
                m1_atr_average=m1_atr_average[index],
            )
        )
    return tuple(snapshots)


def _entry_price(
    raw_price: Decimal, direction: Direction, config: BacktestConfig
) -> Decimal:
    adjustment = config.spread_price / Decimal("2") + config.slippage_price
    return raw_price + adjustment if direction == "LONG" else raw_price - adjustment


def _exit_price(
    raw_price: Decimal, direction: Direction, config: BacktestConfig
) -> Decimal:
    adjustment = config.spread_price / Decimal("2") + config.slippage_price
    return raw_price - adjustment if direction == "LONG" else raw_price + adjustment


def _exit_trigger(
    position: _OpenPosition, bar: BacktestBar
) -> tuple[Decimal, ExitReason] | None:
    stop_reason: ExitReason = (
        "BREAKEVEN_STOP" if position.trailing_armed else "STOP_LOSS"
    )
    if position.direction == "LONG":
        if bar.open <= position.stop_loss:
            return bar.open, stop_reason
        if bar.open >= position.take_profit:
            return bar.open, "TAKE_PROFIT"
        if bar.low <= position.stop_loss and bar.high >= position.take_profit:
            return position.stop_loss, stop_reason
        if bar.low <= position.stop_loss:
            return position.stop_loss, stop_reason
        if bar.high >= position.take_profit:
            return position.take_profit, "TAKE_PROFIT"
    else:
        if bar.open >= position.stop_loss:
            return bar.open, stop_reason
        if bar.open <= position.take_profit:
            return bar.open, "TAKE_PROFIT"
        if bar.high >= position.stop_loss and bar.low <= position.take_profit:
            return position.stop_loss, stop_reason
        if bar.high >= position.stop_loss:
            return position.stop_loss, stop_reason
        if bar.low <= position.take_profit:
            return position.take_profit, "TAKE_PROFIT"
    return None


def _arm_trailing_lock(
    position: _OpenPosition, bar: BacktestBar, config: BacktestConfig
) -> None:
    if (
        not config.enable_trailing_lock
        or position.trailing_armed
        or position.atr_at_entry is None
    ):
        return
    threshold = position.atr_at_entry * config.trailing_lock_multiple
    if position.direction == "LONG" and bar.high >= position.entry_price + threshold:
        position.stop_loss = max(position.stop_loss, position.entry_price)
        position.trailing_armed = True
    elif position.direction == "SHORT" and bar.low <= position.entry_price - threshold:
        position.stop_loss = min(position.stop_loss, position.entry_price)
        position.trailing_armed = True


def _close_position(
    position: _OpenPosition,
    bar: BacktestBar,
    raw_exit_price: Decimal,
    reason: ExitReason,
    config: BacktestConfig,
) -> BacktestTrade:
    exit_price = _exit_price(raw_exit_price, position.direction, config)
    direction_multiplier = (
        Decimal("1") if position.direction == "LONG" else Decimal("-1")
    )
    gross = (
        (exit_price - position.entry_price)
        * direction_multiplier
        * config.quantity
        * config.contract_size
    )
    spread_cost = config.spread_price * config.quantity * config.contract_size
    slippage_cost = (
        config.slippage_price * Decimal("2") * config.quantity * config.contract_size
    )
    commission = config.commission_cost
    return BacktestTrade(
        direction=position.direction,
        entry_timestamp=position.entry_timestamp,
        exit_timestamp=bar.timestamp,
        entry_price=position.entry_price,
        exit_price=exit_price,
        stop_loss=position.stop_loss,
        take_profit=position.take_profit,
        exit_reason=reason,
        gross_pnl=gross,
        spread_cost=spread_cost,
        slippage_cost=slippage_cost,
        commission=commission,
        net_pnl=gross - spread_cost - slippage_cost - commission,
    )


def _mark_to_market(
    position: _OpenPosition,
    bar: BacktestBar,
    equity: Decimal,
    config: BacktestConfig,
) -> Decimal:
    marked_exit = _exit_price(bar.close, position.direction, config)
    direction_multiplier = (
        Decimal("1") if position.direction == "LONG" else Decimal("-1")
    )
    gross = (
        (marked_exit - position.entry_price)
        * direction_multiplier
        * config.quantity
        * config.contract_size
    )
    costs = (
        config.spread_price * config.quantity * config.contract_size
        + config.slippage_price * Decimal("2") * config.quantity * config.contract_size
        + config.commission_cost
    )
    return equity + gross - costs


def _metrics(
    trades: tuple[BacktestTrade, ...],
    equity_curve: tuple[Decimal, ...],
    config: BacktestConfig,
) -> BacktestMetrics:
    gross_profit = sum(
        (trade.net_pnl for trade in trades if trade.net_pnl > 0), Decimal("0")
    )
    gross_loss = -sum(
        (trade.net_pnl for trade in trades if trade.net_pnl < 0), Decimal("0")
    )
    net_profit = sum((trade.net_pnl for trade in trades), Decimal("0"))
    wins = sum(trade.net_pnl > 0 for trade in trades)
    total_trades = len(trades)
    peak = equity_curve[0]
    max_drawdown_pct = Decimal("0")
    for equity in equity_curve:
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown_pct = max(
                max_drawdown_pct, (peak - equity) / peak * Decimal("100")
            )
    average_duration = (
        sum((trade.duration.total_seconds() for trade in trades), 0.0)
        / total_trades
        / 60
        if trades
        else 0.0
    )
    profit_factor = (
        gross_profit / gross_loss
        if gross_loss > 0
        else Decimal("Infinity")
        if gross_profit > 0
        else Decimal("0")
    )
    return BacktestMetrics(
        initial_equity=config.initial_equity,
        final_equity=config.initial_equity + net_profit,
        net_profit=net_profit,
        total_return_pct=net_profit / config.initial_equity * Decimal("100"),
        win_rate_pct=(Decimal(wins) / Decimal(total_trades) * Decimal("100"))
        if trades
        else Decimal("0"),
        profit_factor=profit_factor,
        max_drawdown_pct=max_drawdown_pct,
        total_trades=total_trades,
        average_trade_duration_minutes=Decimal(str(average_duration)),
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        spread_cost=sum((trade.spread_cost for trade in trades), Decimal("0")),
        slippage_cost=sum((trade.slippage_cost for trade in trades), Decimal("0")),
        commission=sum((trade.commission for trade in trades), Decimal("0")),
    )


def _record_rejection(rejections: dict[str, int], reason: str) -> None:
    rejections[reason] = rejections.get(reason, 0) + 1


def _passes_filters(
    signal_bar: BacktestBar,
    entry_bar: BacktestBar,
    direction: Direction,
    snapshot: _IndicatorSnapshot,
    config: BacktestConfig,
    rejections: dict[str, int],
) -> bool:
    if not config.enable_filters:
        return True
    if (
        not config.session_start_hour
        <= entry_bar.timestamp.hour
        < config.session_end_hour
    ):
        _record_rejection(rejections, "SESSION")
        return False
    if entry_bar.spread > config.max_spread_points:
        _record_rejection(rejections, "SPREAD")
        return False
    if snapshot.trend_ema is None:
        _record_rejection(rejections, "HTF_WARMUP")
        return False
    if direction == "LONG" and signal_bar.close <= snapshot.trend_ema:
        _record_rejection(rejections, "HTF_TREND")
        return False
    if direction == "SHORT" and signal_bar.close >= snapshot.trend_ema:
        _record_rejection(rejections, "HTF_TREND")
        return False
    if snapshot.adx is None or snapshot.adx <= config.minimum_adx:
        _record_rejection(rejections, "ADX")
        return False
    if snapshot.m1_atr is None or snapshot.m1_atr_average is None:
        _record_rejection(rejections, "ATR_WARMUP")
        return False
    if snapshot.m1_atr < snapshot.m1_atr_average:
        _record_rejection(rejections, "ATR_COMPRESSION")
        return False
    return True


def run_backtest(
    bars: Sequence[BacktestBar],
    config: BacktestConfig | None = None,
    *,
    signal_generator: SignalGenerator | None = None,
) -> BacktestResult:
    """Replay completed bars; signals use only completed data and next-bar entries."""
    selected = config or BacktestConfig()
    if len(bars) < selected.slow_period + 2:
        raise ValueError("insufficient bars for strategy warmup and execution")
    candles = tuple(
        bar.to_candle(selected.instrument, selected.timeframe) for bar in bars
    )
    snapshots = _indicator_snapshots(bars, selected)
    generate = signal_generator or _signal_generator(selected)
    position: _OpenPosition | None = None
    trades: list[BacktestTrade] = []
    equity = selected.initial_equity
    equity_curve = [equity]
    rejections: dict[str, int] = {}
    cooldown_remaining = 0
    history_window = selected.slow_period + selected.confirmation_bars - 1

    for index in range(selected.slow_period, len(bars)):
        bar = bars[index]
        if position is not None:
            trigger = _exit_trigger(position, bar)
            if trigger is not None:
                trade = _close_position(position, bar, trigger[0], trigger[1], selected)
                trades.append(trade)
                equity += trade.net_pnl
                equity_curve.append(equity)
                position = None
                cooldown_remaining = selected.exit_cooldown_bars
                continue
            _arm_trailing_lock(position, bar, selected)
            equity_curve.append(_mark_to_market(position, bar, equity, selected))
        if index >= len(bars) - 1:
            break
        history = candles[max(0, index - history_window) : index + 1]
        signal = generate(history)
        if cooldown_remaining > 0:
            if signal.action in {"BUY", "SELL"}:
                _record_rejection(rejections, "COOLDOWN")
            cooldown_remaining -= 1
            continue
        if signal.action not in {"BUY", "SELL"}:
            continue
        direction: Direction = "LONG" if signal.action == "BUY" else "SHORT"
        next_bar = bars[index + 1]
        if not _passes_filters(
            bar, next_bar, direction, snapshots[index], selected, rejections
        ):
            continue
        atr = snapshots[index].m1_atr
        if selected.use_atr_exits and atr is None:
            _record_rejection(rejections, "ATR_EXIT_DATA")
            continue
        if position is not None:
            if position.direction == direction:
                continue
            trade = _close_position(
                position,
                next_bar,
                next_bar.open,
                "OPPOSITE_SIGNAL",
                selected,
            )
            trades.append(trade)
            equity += trade.net_pnl
            equity_curve.append(equity)
            position = None
            cooldown_remaining = selected.exit_cooldown_bars
            if cooldown_remaining > 0:
                continue
        entry = _entry_price(next_bar.open, direction, selected)
        if selected.use_atr_exits:
            assert atr is not None
            stop_distance = atr * selected.atr_stop_multiple
            target_distance = atr * selected.atr_target_multiple
        else:
            stop_distance = selected.stop_distance
            target_distance = selected.target_distance
        if direction == "LONG":
            stop_loss = entry - stop_distance
            take_profit = entry + target_distance
        else:
            stop_loss = entry + stop_distance
            take_profit = entry - target_distance
        position = _OpenPosition(
            direction=direction,
            entry_timestamp=next_bar.timestamp,
            entry_price=entry,
            stop_loss=stop_loss,
            take_profit=take_profit,
            atr_at_entry=atr,
        )

    if position is not None:
        last_bar = bars[-1]
        trade = _close_position(
            position, last_bar, last_bar.close, "END_OF_DATA", selected
        )
        trades.append(trade)
        equity += trade.net_pnl
        equity_curve.append(equity)

    trade_tuple = tuple(trades)
    return BacktestResult(
        bars=len(bars),
        trades=trade_tuple,
        equity_curve=tuple(equity_curve),
        metrics=_metrics(trade_tuple, tuple(equity_curve), selected),
        filter_rejections=tuple(sorted(rejections.items())),
    )


def format_report(
    result: BacktestResult, *, data_path: Path, instrument: str = "XAUUSDm"
) -> str:
    metrics = result.metrics
    profit_factor = (
        "inf"
        if not metrics.profit_factor.is_finite()
        else f"{metrics.profit_factor:.4f}"
    )
    lines = [
        f"{instrument} OFFLINE BACKTEST",
        "==========================",
        f"Data: {data_path}",
        f"Bars replayed: {result.bars}",
        "",
        f"Total Return: {metrics.total_return_pct:.4f}%",
        f"Net Profit: ${metrics.net_profit:.2f}",
        f"Final Equity: ${metrics.final_equity:.2f}",
        f"Win Rate: {metrics.win_rate_pct:.2f}%",
        f"Profit Factor: {profit_factor}",
        f"Maximum Drawdown: {metrics.max_drawdown_pct:.4f}%",
        f"Total Trades: {metrics.total_trades}",
        f"Average Trade Duration: {metrics.average_trade_duration_minutes:.2f} minutes",
        "",
        f"Spread Cost: ${metrics.spread_cost:.2f}",
        f"Slippage Cost: ${metrics.slippage_cost:.2f}",
        f"Commission: ${metrics.commission:.2f}",
    ]
    if result.filter_rejections:
        lines.extend(
            [
                "",
                "Filter Rejections:",
                *[f"  {reason}: {count}" for reason, count in result.filter_rejections],
            ]
        )
    return "\n".join(lines)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data", type=Path, default=Path("data/XAUUSDm_M1_historical.csv")
    )
    parser.add_argument("--instrument", default="XAUUSDm")
    parser.add_argument("--timeframe", choices=("1m", "5m"), default="1m")
    parser.add_argument("--session-start-hour", type=int, default=12)
    parser.add_argument("--session-end-hour", type=int, default=16)
    parser.add_argument("--max-spread-points", type=Decimal, default=Decimal("50"))
    parser.add_argument("--pip-size", type=Decimal, default=Decimal("0.01"))
    parser.add_argument("--contract-size", type=Decimal, default=Decimal("100"))
    parser.add_argument("--quantity", type=Decimal, default=Decimal("0.01"))
    parser.add_argument("--spread-pips", type=Decimal, default=Decimal("0.35"))
    parser.add_argument("--slippage-pips", type=Decimal, default=Decimal("2.0"))
    parser.add_argument("--commission-round-turn-per-lot", type=Decimal)
    parser.add_argument("--adx-threshold", type=Decimal, default=Decimal("20"))
    parser.add_argument("--confirmation-bars", type=int, default=1)
    parser.add_argument("--exit-cooldown-bars", type=int, default=0)
    parser.add_argument("--atr-stop-multiple", type=Decimal, default=Decimal("1.5"))
    parser.add_argument("--atr-target-multiple", type=Decimal, default=Decimal("2.0"))
    parser.add_argument("--disable-trailing-lock", action="store_true")
    parser.add_argument("--baseline", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    bars = load_bars(args.data, timeframe=args.timeframe)
    config = BacktestConfig(
        instrument=args.instrument,
        timeframe=args.timeframe,
        quantity=args.quantity,
        contract_size=args.contract_size,
        pip_size=args.pip_size,
        spread_pips=args.spread_pips,
        slippage_pips=args.slippage_pips,
        commission_round_turn_per_lot=args.commission_round_turn_per_lot,
        session_start_hour=args.session_start_hour,
        session_end_hour=args.session_end_hour,
        max_spread_points=args.max_spread_points,
        minimum_adx=args.adx_threshold,
        confirmation_bars=args.confirmation_bars,
        exit_cooldown_bars=args.exit_cooldown_bars,
        atr_stop_multiple=args.atr_stop_multiple,
        atr_target_multiple=args.atr_target_multiple,
        enable_filters=not args.baseline,
        use_atr_exits=not args.baseline,
        enable_trailing_lock=not args.baseline and not args.disable_trailing_lock,
    )
    result = run_backtest(bars, config)
    print(format_report(result, data_path=args.data, instrument=args.instrument))


if __name__ == "__main__":
    main()
