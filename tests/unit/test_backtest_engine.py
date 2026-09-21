from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from backtest.engine import BacktestBar, BacktestConfig, load_bars, run_backtest
from domain.models import MarketCandle
from trading_bot.strategy import Signal


def _bar(index: int, *, open_price: str = "100") -> BacktestBar:
    timestamp = datetime(2026, 1, 5, tzinfo=timezone.utc) + timedelta(minutes=index)
    price = Decimal(open_price)
    return BacktestBar(
        timestamp=timestamp,
        open=price,
        high=price + Decimal("1"),
        low=price - Decimal("1"),
        close=price,
        tick_volume=Decimal("10"),
        spread=Decimal("0"),
    )


def test_load_bars_requires_all_execution_columns(tmp_path: Path) -> None:
    path = tmp_path / "bars.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["timestamp", "open", "high", "low", "close", "tick_volume", "spread"]
        )
        writer.writerow(["2026-01-05T00:00:00Z", "100", "101", "99", "100", "10", "2"])
    bars = load_bars(path)
    assert bars[0].tick_volume == Decimal("10")
    assert bars[0].spread == Decimal("2")


def test_replay_uses_only_completed_history_and_enters_next_bar() -> None:
    bars = tuple(_bar(index) for index in range(6))
    seen_lengths: list[int] = []
    seen_timestamps: list[datetime] = []

    def generator(history: tuple[MarketCandle, ...]) -> Signal:
        seen_lengths.append(len(history))
        seen_timestamps.append(history[-1].timestamp)
        return Signal(
            action="BUY" if len(history) == 3 else "HOLD",
            timestamp=history[-1].timestamp,
            instrument="XAUUSDm",
            reference_price=history[-1].close,
        )

    result = run_backtest(
        bars,
        BacktestConfig(
            instrument="XAUUSDm",
            fast_period=1,
            slow_period=2,
            stop_loss_pips=100,
            take_profit_pips=200,
            enable_filters=False,
            use_atr_exits=False,
        ),
        signal_generator=generator,
    )

    assert seen_lengths[0] == 3
    assert seen_timestamps[0] == bars[2].timestamp
    assert result.trades[0].entry_timestamp == bars[3].timestamp


def test_stop_loss_wins_when_stop_and_target_hit_same_bar() -> None:
    bars = (
        _bar(0),
        _bar(1),
        _bar(2),
        BacktestBar(
            timestamp=datetime(2026, 1, 5, 0, 3, tzinfo=timezone.utc),
            open=Decimal("100"),
            high=Decimal("103"),
            low=Decimal("98"),
            close=Decimal("100"),
            tick_volume=Decimal("10"),
            spread=Decimal("0"),
        ),
        _bar(4),
    )

    def generator(history: tuple[MarketCandle, ...]) -> Signal:
        return Signal(
            action="BUY" if len(history) == 3 else "HOLD",
            timestamp=history[-1].timestamp,
            instrument="XAUUSDm",
            reference_price=history[-1].close,
        )

    result = run_backtest(
        bars,
        BacktestConfig(
            instrument="XAUUSDm",
            fast_period=1,
            slow_period=2,
            pip_size=Decimal("0.01"),
            spread_pips=Decimal("0"),
            slippage_pips=Decimal("0"),
            commission_per_lot=Decimal("0"),
            stop_loss_pips=100,
            take_profit_pips=200,
            enable_filters=False,
            use_atr_exits=False,
        ),
        signal_generator=generator,
    )

    assert result.trades[0].exit_reason == "STOP_LOSS"
    assert result.trades[0].net_pnl < 0
    assert result.metrics.total_trades == 1


def _trend_bars(*, spread: str = "10") -> tuple[BacktestBar, ...]:
    bars: list[BacktestBar] = []
    for index in range(30):
        timestamp = datetime(2026, 1, 5, 12, tzinfo=timezone.utc) + timedelta(
            minutes=index
        )
        price = Decimal("100") + Decimal(index) / Decimal("10")
        bars.append(
            BacktestBar(
                timestamp=timestamp,
                open=price,
                high=price + Decimal("1"),
                low=price - Decimal("1"),
                close=price + Decimal("0.1"),
                tick_volume=Decimal("10"),
                spread=Decimal(spread),
            )
        )
    return tuple(bars)


def _filter_config(**overrides: object) -> BacktestConfig:
    values: dict[str, object] = {
        "fast_period": 1,
        "slow_period": 2,
        "enable_filters": True,
        "trend_timeframe": "15m",
        "trend_period": 1,
        "adx_timeframe": "5m",
        "adx_period": 1,
        "minimum_adx": Decimal("1"),
        "atr_period": 2,
        "atr_average_period": 1,
        "use_atr_exits": False,
        "stop_loss_pips": 100,
        "take_profit_pips": 200,
        "max_spread_points": Decimal("50"),
    }
    values.update(overrides)
    return BacktestConfig(**values)


def test_filters_allow_trending_session_with_tight_spread() -> None:
    bars = _trend_bars()

    def generator(history: tuple[MarketCandle, ...]) -> Signal:
        return Signal(
            action="BUY" if history[-1].timestamp.minute == 17 else "HOLD",
            timestamp=history[-1].timestamp,
            instrument="XAUUSDm",
            reference_price=history[-1].close,
        )

    result = run_backtest(bars, _filter_config(), signal_generator=generator)

    assert result.metrics.total_trades == 1
    assert result.filter_rejections == ()


def test_spread_guard_rejects_entry_above_limit() -> None:
    bars = list(_trend_bars())
    bars[18] = BacktestBar(
        timestamp=bars[18].timestamp,
        open=bars[18].open,
        high=bars[18].high,
        low=bars[18].low,
        close=bars[18].close,
        tick_volume=bars[18].tick_volume,
        spread=Decimal("51"),
    )

    def generator(history: tuple[MarketCandle, ...]) -> Signal:
        return Signal(
            action="BUY" if history[-1].timestamp.minute == 17 else "HOLD",
            timestamp=history[-1].timestamp,
            instrument="XAUUSDm",
            reference_price=history[-1].close,
        )

    result = run_backtest(tuple(bars), _filter_config(), signal_generator=generator)

    assert result.metrics.total_trades == 0
    assert result.filter_rejections == (("SPREAD", 1),)


def test_atr_exit_and_trailing_lock_close_at_breakeven() -> None:
    bars = (
        _bar(0),
        _bar(1),
        _bar(2),
        BacktestBar(
            timestamp=datetime(2026, 1, 5, 0, 3, tzinfo=timezone.utc),
            open=Decimal("100"),
            high=Decimal("101.5"),
            low=Decimal("99.9"),
            close=Decimal("101"),
            tick_volume=Decimal("10"),
            spread=Decimal("0"),
        ),
        BacktestBar(
            timestamp=datetime(2026, 1, 5, 0, 4, tzinfo=timezone.utc),
            open=Decimal("101"),
            high=Decimal("102.5"),
            low=Decimal("100.5"),
            close=Decimal("102"),
            tick_volume=Decimal("10"),
            spread=Decimal("0"),
        ),
        BacktestBar(
            timestamp=datetime(2026, 1, 5, 0, 5, tzinfo=timezone.utc),
            open=Decimal("101"),
            high=Decimal("101.5"),
            low=Decimal("99"),
            close=Decimal("100"),
            tick_volume=Decimal("10"),
            spread=Decimal("0"),
        ),
    )

    def generator(history: tuple[MarketCandle, ...]) -> Signal:
        return Signal(
            action="BUY" if len(history) == 3 else "HOLD",
            timestamp=history[-1].timestamp,
            instrument="XAUUSDm",
            reference_price=history[-1].close,
        )

    result = run_backtest(
        bars,
        BacktestConfig(
            fast_period=1,
            slow_period=2,
            enable_filters=False,
            use_atr_exits=True,
            enable_trailing_lock=True,
            atr_period=2,
            atr_average_period=1,
            atr_stop_multiple=1,
            atr_target_multiple=2,
            trailing_lock_multiple=1,
            spread_pips=0,
            slippage_pips=0,
            commission_per_lot=0,
        ),
        signal_generator=generator,
    )

    assert result.trades[0].exit_reason == "BREAKEVEN_STOP"
    assert result.trades[0].stop_loss == result.trades[0].entry_price


def test_two_bar_confirmation_delays_crossover_entry() -> None:
    bars = tuple(
        _bar(index, open_price=str(price))
        for index, price in enumerate(("101", "101", "103", "104", "104", "104"))
    )
    result = run_backtest(
        bars,
        BacktestConfig(
            fast_period=1,
            slow_period=2,
            confirmation_bars=2,
            enable_filters=False,
            use_atr_exits=False,
        ),
    )

    assert result.trades[0].entry_timestamp == bars[4].timestamp


def test_exit_cooldown_blocks_reentry_for_configured_bars() -> None:
    bars = list(_bar(index) for index in range(9))
    bars[3] = BacktestBar(
        timestamp=bars[3].timestamp,
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("98"),
        close=Decimal("100"),
        tick_volume=Decimal("10"),
        spread=Decimal("0"),
    )

    def generator(history: tuple[MarketCandle, ...]) -> Signal:
        return Signal(
            action="BUY" if history[-1].timestamp == bars[2].timestamp else "SELL",
            timestamp=history[-1].timestamp,
            instrument="XAUUSDm",
            reference_price=history[-1].close,
        )

    result = run_backtest(
        bars,
        BacktestConfig(
            fast_period=1,
            slow_period=2,
            confirmation_bars=1,
            exit_cooldown_bars=3,
            enable_filters=False,
            use_atr_exits=False,
        ),
        signal_generator=generator,
    )

    assert result.trades[0].exit_reason == "STOP_LOSS"
    assert result.trades[1].entry_timestamp == bars[8].timestamp
    assert result.filter_rejections == (("COOLDOWN", 3),)
