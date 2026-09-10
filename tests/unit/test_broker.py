from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from execution.broker_adapter import MarketQuote, StaleMarketDataError
from trading_bot.broker.market_data import (
    BarAggregator,
    LiveMarketDataGateway,
    SimulatedQuoteSource,
)
from trading_bot.broker.mt5 import BrokerConnectionConfig, MT5QuoteSource


def quote(timestamp: str, bid: str, ask: str) -> MarketQuote:
    return MarketQuote(
        instrument="EUR_USD",
        bid=Decimal(bid),
        ask=Decimal(ask),
        observed_at=datetime.fromisoformat(timestamp.replace("Z", "+00:00")),
    )


def write_feed(path: Path) -> None:
    path.write_text(
        "timestamp,open,high,low,close,volume\n"
        "2026-01-05T00:00:00Z,1.1000,1.1010,1.0990,1.1000,100\n",
        encoding="utf-8",
    )


def test_demo_environment_uses_local_simulated_quote_feed(tmp_path: Path) -> None:
    data_path = tmp_path / "market_data.csv"
    write_feed(data_path)
    fallback = SimulatedQuoteSource(
        data_path,
        now=lambda: datetime(2026, 1, 5, 12, 1, tzinfo=timezone.utc),
    )
    source = MT5QuoteSource(
        BrokerConnectionConfig(
            token=None,
            account=None,
            server=None,
            endpoint=None,
            environment="demo",
            fallback_data_path=data_path,
        ),
        fallback=fallback,
    )

    result = source.poll_quote()

    assert source.mode == "SIMULATED"
    assert source.fallback_reason == "BROKER_ENV=demo uses the local paper feed"
    assert result.bid == Decimal("1.0999")
    assert result.ask == Decimal("1.1001")


def test_missing_mt5_bindings_fall_back_to_local_feed(tmp_path: Path) -> None:
    data_path = tmp_path / "market_data.csv"
    write_feed(data_path)
    fallback = SimulatedQuoteSource(data_path)
    source = MT5QuoteSource(
        BrokerConnectionConfig(
            token="demo-token",
            account="123456",
            server="Exness-MT5Trial",
            endpoint=None,
            environment="paper",
            fallback_data_path=data_path,
        ),
        fallback=fallback,
    )

    result = source.poll_quote()

    assert source.mode == "SIMULATED"
    assert result.instrument == "EUR_USD"
    assert source.fallback_reason is not None


def test_bar_aggregator_updates_ohlcv_and_rejects_backwards_quotes() -> None:
    aggregator = BarAggregator()
    first = aggregator.add_quote(quote("2026-01-05T12:01:00Z", "1.0999", "1.1001"))
    second = aggregator.add_quote(quote("2026-01-05T12:05:00Z", "1.1019", "1.1021"))

    assert first.completed is None
    assert second.current.open == Decimal("1.1000")
    assert second.current.high == Decimal("1.1020")
    assert second.current.low == Decimal("1.1000")
    assert second.current.close == Decimal("1.1020")
    assert second.current.volume == Decimal("2")

    with pytest.raises(StaleMarketDataError):
        aggregator.add_quote(quote("2026-01-05T11:59:00Z", "1.0999", "1.1001"))


def test_gateway_persists_current_live_bar_for_strategy_consumption(
    tmp_path: Path,
) -> None:
    data_path = tmp_path / "market_data.csv"
    source_quotes = [
        quote("2026-01-05T12:01:00Z", "1.0999", "1.1001"),
        quote("2026-01-05T12:16:00Z", "1.1009", "1.1011"),
    ]

    class Source:
        def __init__(self) -> None:
            self.index = 0

        def poll_quote(self, instrument: str) -> MarketQuote:
            result = source_quotes[self.index]
            self.index += 1
            return result

    gateway = LiveMarketDataGateway(Source(), data_path=data_path)
    first = gateway.poll()
    second = gateway.poll()

    assert len(first) == 1
    assert len(second) == 2
    assert second[-1].close == Decimal("1.1010")
    assert data_path.read_text(encoding="utf-8").count("2026-01-05") == 2
