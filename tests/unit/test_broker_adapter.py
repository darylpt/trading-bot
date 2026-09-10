from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import cast

import pytest

from domain.models import BrokerOrderPayload, ExitPayload
from execution.broker_adapter import (
    BrokerAccountState,
    BrokerAdapter,
    BrokerConnectionError,
    MarketQuote,
    StaleMarketDataError,
)


class Response:
    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self.payload = payload

    def json(self) -> object:
        return self.payload


class FakeTransport:
    def __init__(self, *responses: object) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def request(self, method: str, url: str, **kwargs: object) -> object:
        self.calls.append({"method": method, "url": url, **kwargs})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def now() -> datetime:
    return datetime(2026, 9, 10, 13, 30, tzinfo=timezone.utc)


def adapter(
    transport: FakeTransport,
    *,
    sleep_calls: list[float] | None = None,
) -> BrokerAdapter:
    return BrokerAdapter(
        transport,
        provider="oanda",
        base_url="https://api-fxpractice.oanda.com",
        account_id="demo-account",
        environment="DEMO",
        api_token="demo-token",
        timeout_seconds=2.0,
        backoff_seconds=0.1,
        now=now,
        sleep=(sleep_calls if sleep_calls is not None else []).append,
    )


def order_payload() -> BrokerOrderPayload:
    return BrokerOrderPayload(
        client_order_id="order-1",
        instrument="EUR_USD",
        direction="LONG",
        quantity=Decimal("100"),
        entry_price=Decimal("1.1000"),
        stop_loss=ExitPayload(price=Decimal("1.0950")),
        take_profit=ExitPayload(price=Decimal("1.1100")),
        account_equity=Decimal("10000"),
        risk_fraction=Decimal("0.01"),
        environment="DEMO",
    )


def test_account_polling_returns_balance_equity_margin_and_positions() -> None:
    transport = FakeTransport(
        {
            "account": {
                "balance": "10000",
                "NAV": "10025",
                "marginUsed": "250",
                "timestamp": now().isoformat(),
                "positions": [
                    {
                        "instrument": "EUR_USD",
                        "long": {"units": "100", "averagePrice": "1.1"},
                    }
                ],
            }
        }
    )
    result = adapter(transport).get_account_snapshot()

    assert isinstance(result, BrokerAccountState)
    assert result.balance == Decimal("10000")
    assert result.equity == Decimal("10025")
    assert result.margin == Decimal("250")
    assert result.open_positions[0].direction == "LONG"
    assert result.open_positions[0].quantity == Decimal("100")


def test_market_polling_returns_live_spread() -> None:
    transport = FakeTransport(
        {
            "prices": [
                {
                    "instrument": "EUR_USD",
                    "bids": [{"price": "1.1000"}],
                    "asks": [{"price": "1.1002"}],
                    "time": now().isoformat(),
                }
            ]
        }
    )
    result = adapter(transport).get_market_quote("EUR_USD")

    assert isinstance(result, MarketQuote)
    assert result.bid == Decimal("1.1000")
    assert result.ask == Decimal("1.1002")
    assert result.spread == Decimal("0.0002")


def test_stale_market_data_is_rejected() -> None:
    transport = FakeTransport(
        {
            "bid": "1.1000",
            "ask": "1.1002",
            "timestamp": (now() - timedelta(seconds=31)).isoformat(),
        }
    )

    with pytest.raises(StaleMarketDataError):
        adapter(transport).get_market_quote("EUR_USD")


def test_order_placement_maps_broker_side_stop_and_target() -> None:
    transport = FakeTransport({"orderCreateTransaction": {"id": "broker-1"}})
    result = adapter(transport).submit_order(order_payload())

    assert result.status == "ACCEPTED"
    assert result.provider_order_id == "broker-1"
    request_body = cast(bytes, transport.calls[0]["body"]).decode("utf-8")
    assert '"stopLossOnFill": {"price": "1.0950"}' in request_body
    assert '"takeProfitOnFill": {"price": "1.1100"}' in request_body
    assert '"units": "100"' in request_body


def test_transient_timeout_retries_reads_with_exponential_backoff() -> None:
    sleep_calls: list[float] = []
    transport = FakeTransport(
        TimeoutError("first"),
        TimeoutError("second"),
        {"bid": "1.1000", "ask": "1.1002", "timestamp": now().isoformat()},
    )

    result = adapter(transport, sleep_calls=sleep_calls).get_market_quote("EUR_USD")

    assert result.spread == Decimal("0.0002")
    assert len(transport.calls) == 3
    assert sleep_calls == [0.1, 0.2]


def test_rate_limit_exhaustion_raises_typed_connection_error() -> None:
    sleep_calls: list[float] = []
    transport = FakeTransport(
        Response(429, {"error": "rate limited"}),
        Response(429, {"error": "rate limited"}),
        Response(429, {"error": "rate limited"}),
    )

    with pytest.raises(BrokerConnectionError):
        adapter(transport, sleep_calls=sleep_calls).get_account_snapshot()

    assert len(transport.calls) == 3
    assert sleep_calls == [0.1, 0.2]


def test_order_rate_limit_is_not_retried() -> None:
    transport = FakeTransport(Response(429, {"error": "rate limited"}))

    with pytest.raises(BrokerConnectionError):
        adapter(transport).submit_order(order_payload())

    assert len(transport.calls) == 1
