from __future__ import annotations

from types import SimpleNamespace

from bridge import mt5_host_bridge as bridge


class FakeMT5:
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    TRADE_ACTION_DEAL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_IOC = 1
    TRADE_RETCODE_DONE = 10009
    TIMEFRAME_M1 = 1
    TIMEFRAME_M5 = 5
    TIMEFRAME_M15 = 15
    TIMEFRAME_H1 = 60
    TIMEFRAME_H4 = 240
    TIMEFRAME_D1 = 1440

    def __init__(
        self,
        *,
        fail_order_send: bool = False,
        order_check_retcode: int = 10009,
    ) -> None:
        self.fail_order_send = fail_order_send
        self.order_check_retcode = order_check_retcode
        self.order_send_calls = 0
        self.order_check_calls = 0
        self.symbol_select_calls: list[tuple[str, bool]] = []

    def initialize(self, **kwargs: object) -> bool:
        return True

    def account_info(self) -> object:
        return SimpleNamespace(login=463948680, server="Exness-MT5Trial17")

    def terminal_info(self) -> object:
        return SimpleNamespace(connected=True)

    def symbol_select(self, symbol: str, enable: bool) -> bool:
        self.symbol_select_calls.append((symbol, enable))
        return enable

    def symbol_info(self, symbol: str) -> object:
        return SimpleNamespace(
            volume_min=0.01,
            volume_max=100.0,
            volume_step=0.01,
        )

    def symbol_info_tick(self, symbol: str) -> object:
        return SimpleNamespace(bid=2000.0, ask=2000.2, time=1_750_000_000)

    def copy_rates_from_pos(
        self, symbol: str, timeframe: int, start_pos: int, count: int
    ) -> list[dict[str, float]]:
        return [
            {
                "time": 1_750_000_000.0,
                "open": 2000.0,
                "high": 2001.0,
                "low": 1999.0,
                "close": 2000.5,
                "tick_volume": 100.0,
            }
        ]

    def order_check(self, request: dict[str, object]) -> object:
        self.order_check_calls += 1
        return SimpleNamespace(retcode=self.order_check_retcode, comment="check result")

    def order_send(self, request: dict[str, object]) -> object:
        self.order_send_calls += 1
        if self.fail_order_send:
            raise TimeoutError("provider timeout")
        return SimpleNamespace(
            retcode=self.TRADE_RETCODE_DONE,
            order=123,
            deal=456,
            volume=0.01,
            price=2000.0,
            comment="ok",
        )

    def history_deals_get(self, start: object, end: object) -> list[object]:
        return []

    def positions_get(self) -> list[object]:
        return []

    def orders_get(self) -> list[object]:
        return []

    def last_error(self) -> tuple[int, str]:
        return (0, "ok")


def test_health_requires_connected_expected_demo_identity(monkeypatch) -> None:
    monkeypatch.setattr(bridge, "_mt5", FakeMT5())

    status_code, payload = bridge._fastapi_health()

    assert status_code == 200
    assert payload["status"] == "ok"
    assert payload["connected"] is True
    assert payload["terminalConnected"] is True
    assert payload["authorized"] is True
    assert payload["accountId"] == "463948680"
    assert isinstance(payload["serverTime"], str)
    assert payload["server"] == "Exness-MT5Trial17"


def test_quote_selects_verified_symbol_before_read(monkeypatch) -> None:
    fake = FakeMT5()
    monkeypatch.setattr(bridge, "_mt5", fake)

    payload = bridge._rpc_quote_get({"symbol": "XAUUSDm"})

    assert payload["instrument"] == "XAUUSDm"
    assert fake.symbol_select_calls == [("XAUUSDm", True)]


def test_order_send_timeout_returns_unknown_and_halt_signal(monkeypatch) -> None:
    fake = FakeMT5(fail_order_send=True)
    monkeypatch.setattr(bridge, "_mt5", fake)

    response = bridge._dispatch_rpc(
        {
            "jsonrpc": "2.0",
            "id": "timeout-1",
            "method": "order_send",
            "params": {
                "symbol": "XAUUSDm",
                "volume": "0.01",
                "type": "ORDER_TYPE_BUY",
                "price": "2000",
                "sl": "1990",
                "tp": "2010",
                "comment": "timeout-1",
                "environment": "DEMO",
            },
        }
    )

    assert response["error"]["data"]["status"] == "UNKNOWN"
    assert response["error"]["data"]["halt_new_entries"] is True
    assert response["error"]["data"]["phase"] == "order_send"
    diagnostics = response["error"]["data"]["diagnostics"]
    assert diagnostics["failure"]["exception_type"] == "TimeoutError"
    assert "timeout-1" not in str(diagnostics)
    assert fake.order_send_calls == 1


def _order_params(client_id: str = "test-order") -> dict[str, object]:
    return {
        "symbol": "XAUUSDm",
        "volume": "0.01",
        "type": "ORDER_TYPE_BUY",
        "price": "2000",
        "sl": "1990",
        "tp": "2010",
        "comment": client_id,
        "environment": "DEMO",
    }


def test_order_check_rejection_is_deterministic_and_does_not_submit(
    monkeypatch,
) -> None:
    fake = FakeMT5(order_check_retcode=10016)
    monkeypatch.setattr(bridge, "_mt5", fake)

    response = bridge._dispatch_rpc(
        {
            "jsonrpc": "2.0",
            "id": "check-reject-1",
            "method": "order_send",
            "params": _order_params("check-reject-1"),
        }
    )

    assert response is not None
    result = response["result"]
    assert result["status"] == "REJECTED"
    assert result["phase"] == "order_check"
    assert result["retcode"] == 10016
    assert fake.order_send_calls == 0


def test_order_preflight_runs_check_without_submission(monkeypatch) -> None:
    fake = FakeMT5()
    monkeypatch.setattr(bridge, "_mt5", fake)

    response = bridge._dispatch_rpc(
        {
            "jsonrpc": "2.0",
            "id": "preflight-1",
            "method": "order.preflight",
            "params": _order_params("preflight-1"),
        }
    )

    assert response is not None
    assert response["result"]["status"] == "ACCEPTED"
    assert response["result"]["preflight"] is True
    assert fake.order_check_calls == 1
    assert fake.order_send_calls == 0


def test_authoritative_reconciliation_returns_order_not_found(monkeypatch) -> None:
    monkeypatch.setattr(bridge, "_mt5", FakeMT5())

    result = bridge._rpc_order_status({"client_order_id": "missing-1"})

    assert result["status"] == "ORDER_NOT_FOUND"
    assert result["reconciliation"]["checked_at"]
    assert result["reconciliation"]["history_deals_checked"] is True
    assert result["reconciliation"]["positions_checked"] is True
    assert result["reconciliation"]["orders_checked"] is True


def test_reconciliation_transport_failure_is_unknown(monkeypatch) -> None:
    fake = FakeMT5()
    fake.history_deals_get = lambda start, end: None
    monkeypatch.setattr(bridge, "_mt5", fake)

    response = bridge._dispatch_rpc(
        {
            "jsonrpc": "2.0",
            "id": "reconcile-failure-1",
            "method": "order.status",
            "params": {"client_order_id": "missing-2"},
        }
    )

    assert response is not None
    assert response["error"]["data"]["status"] == "UNKNOWN"
    assert response["error"]["data"]["phase"] == "history_deals_get"


def test_order_send_requires_both_protective_exits(monkeypatch) -> None:
    fake = FakeMT5()
    monkeypatch.setattr(bridge, "_mt5", fake)

    response = bridge._dispatch_rpc(
        {
            "jsonrpc": "2.0",
            "id": "missing-exit-1",
            "method": "order_send",
            "params": {
                "symbol": "XAUUSDm",
                "volume": "0.01",
                "type": "ORDER_TYPE_BUY",
                "price": "2000",
                "sl": "1990",
                "comment": "missing-exit-1",
                "environment": "DEMO",
            },
        }
    )

    assert response is not None
    assert response["error"]["code"] == -32602
    assert fake.order_send_calls == 0


def test_supported_symbol_uses_verified_mt5_suffix() -> None:
    assert bridge._supported_symbol("XAUUSDm") == "XAUUSDm"
    assert bridge._supported_symbol("XAUUSD.m") == "XAUUSDm"
    assert bridge._supported_symbol("EUR_USD") == "EURUSDm"
    assert bridge._supported_symbol("EURUSD") == "EURUSDm"


def test_preferred_filling_mode_uses_symbol_supported_flags(monkeypatch) -> None:
    fake = FakeMT5()
    fake.SYMBOL_FILLING_FOK = 1
    fake.SYMBOL_FILLING_IOC = 2
    fake.ORDER_FILLING_FOK = 0
    fake.ORDER_FILLING_RETURN = 2
    monkeypatch.setattr(bridge, "_mt5", fake)

    assert (
        bridge._preferred_filling_mode(SimpleNamespace(filling_mode=1))
        == fake.ORDER_FILLING_FOK
    )
    assert (
        bridge._preferred_filling_mode(SimpleNamespace(filling_mode=2))
        == fake.ORDER_FILLING_IOC
    )


def test_candle_read_uses_closed_rates_and_structured_volume(monkeypatch) -> None:
    fake = FakeMT5()
    monkeypatch.setattr(bridge, "_mt5", fake)

    result = bridge._rpc_candles_get(
        {"symbol": "XAUUSDm", "timeframe": "15m", "limit": 1}
    )

    assert result["candles"][0]["instrument"] == "XAUUSDm"
    assert result["candles"][0]["volume"] == "100.0"
