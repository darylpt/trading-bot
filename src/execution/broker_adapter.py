"""Fail-closed OANDA/MT5 demo broker communication adapters."""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Literal, Protocol, runtime_checkable
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field

from domain.models import BrokerOrderPayload, Direction, ExecutionResult

Provider = Literal["oanda", "mt5"]
Environment = Literal["PAPER", "DEMO"]
ExecutionStatus = Literal["ACCEPTED", "REJECTED", "UNKNOWN"]


class BrokerConnectionError(ConnectionError):
    """Raised when broker availability or response validity is unsafe."""


class StaleMarketDataError(BrokerConnectionError):
    """Raised when a quote is older than the configured freshness window."""


class OpenPosition(BaseModel):
    """Typed open-position state returned by a broker account query."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument: str = Field(min_length=1)
    direction: Direction
    quantity: Decimal = Field(gt=0)
    entry_price: Decimal = Field(gt=0)


class BrokerAccountState(BaseModel):
    """Account balance, equity, margin, and current open positions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    balance: Decimal = Field(gt=0)
    equity: Decimal = Field(gt=0)
    margin: Decimal = Field(ge=0)
    open_positions: tuple[OpenPosition, ...]
    captured_at: datetime
    environment: Environment


class MarketQuote(BaseModel):
    """A broker bid/ask quote with its provider timestamp."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument: str = Field(min_length=1)
    bid: Decimal = Field(gt=0)
    ask: Decimal = Field(gt=0)
    observed_at: datetime

    @property
    def spread(self) -> Decimal:
        """Return the current ask-minus-bid spread."""
        if self.ask <= self.bid:
            raise ValueError("broker quote ask must exceed bid")
        return self.ask - self.bid


@runtime_checkable
class BrokerTransport(Protocol):
    """Minimal injectable synchronous HTTP transport boundary."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> object: ...


class UrllibTransport:
    """Small standard-library REST transport for demo endpoints."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> object:
        request = Request(url, data=body, headers=dict(headers), method=method)
        try:
            with urlopen(request, timeout=timeout) as response:  # noqa: S310
                return _UrllibResponse(response.status, response.read())
        except HTTPError as exc:
            return _UrllibResponse(exc.code, exc.read())


class _UrllibResponse:
    def __init__(self, status_code: int, body: bytes) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> object:
        return json.loads(self._body.decode("utf-8"))


@runtime_checkable
class BrokerResponse(Protocol):
    status_code: int

    def json(self) -> object: ...


class BrokerAdapter:
    """Provider-neutral adapter restricted to paper/demo broker endpoints."""

    _TRANSIENT_STATUSES = frozenset({408, 429, 500, 502, 503, 504})

    def __init__(
        self,
        transport: BrokerTransport,
        *,
        provider: Provider,
        base_url: str,
        account_id: str,
        environment: Environment = "DEMO",
        api_token: str | None = None,
        timeout_seconds: float = 5.0,
        max_attempts: int = 3,
        backoff_seconds: float = 0.25,
        stale_after_seconds: float = 30.0,
        now: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        if environment not in {"PAPER", "DEMO"}:
            raise ValueError("broker adapter only supports paper/demo environments")
        if not any(
            marker in base_url.lower()
            for marker in ("practice", "demo", "paper", "localhost", "127.0.0.1")
        ):
            raise ValueError("broker endpoint must be a demo or practice endpoint")
        if not account_id:
            raise ValueError("account_id must not be empty")
        if timeout_seconds <= 0 or max_attempts <= 0 or backoff_seconds < 0:
            raise ValueError("broker retry settings are invalid")
        if stale_after_seconds <= 0:
            raise ValueError("stale_after_seconds must be positive")
        self.transport = transport
        self.provider = provider
        self.base_url = base_url.rstrip("/")
        self.account_id = account_id
        self.environment = environment
        self.api_token = api_token
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self.backoff_seconds = backoff_seconds
        self.stale_after_seconds = stale_after_seconds
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._sleep = sleep or time.sleep

    def get_account_snapshot(self) -> BrokerAccountState:
        """Poll balance, equity, margin, and open positions with safe retries."""
        payload = self._request("GET", self._account_path(), safe_read=True)
        try:
            account = _mapping(payload.get("account", payload))
            positions = account.get("positions", account.get("open_positions", []))
            if not isinstance(positions, Sequence) or isinstance(
                positions, (str, bytes)
            ):
                raise ValueError("positions must be a sequence")
            return BrokerAccountState(
                balance=_decimal(account.get("balance"), "balance"),
                equity=_decimal(account.get("equity", account.get("NAV")), "equity"),
                margin=_decimal(
                    account.get("margin", account.get("marginUsed", 0)), "margin"
                ),
                open_positions=tuple(
                    self._parse_position(_mapping(position)) for position in positions
                ),
                captured_at=self._timestamp(
                    account.get("timestamp", account.get("time", self._now()))
                ),
                environment=self.environment,
            )
        except (TypeError, ValueError, InvalidOperation) as exc:
            raise BrokerConnectionError("broker account response was invalid") from exc

    def poll_account(self) -> BrokerAccountState:
        """Compatibility alias for account polling."""
        return self.get_account_snapshot()

    def get_market_quote(self, instrument: str) -> MarketQuote:
        """Poll bid/ask data and reject quotes older than 30 seconds."""
        if not instrument:
            raise ValueError("instrument must not be empty")
        payload = self._request("GET", self._pricing_path(instrument), safe_read=True)
        try:
            quote = self._parse_quote(payload, instrument)
            age = (self._utc(self._now()) - quote.observed_at).total_seconds()
            if age < 0 or age > self.stale_after_seconds:
                raise StaleMarketDataError(
                    f"market quote for {instrument} is stale or from the future"
                )
            quote.spread
            return quote
        except StaleMarketDataError:
            raise
        except (TypeError, ValueError, InvalidOperation) as exc:
            raise BrokerConnectionError("broker quote response was invalid") from exc

    def poll_market_data(self, instrument: str) -> MarketQuote:
        """Compatibility alias for real-time market data polling."""
        return self.get_market_quote(instrument)

    def submit_order(self, payload: BrokerOrderPayload) -> ExecutionResult:
        """Submit one canonical order with mandatory broker-side SL and TP."""
        started = time.perf_counter()
        body = self._order_body(payload)
        response = self._request(
            "POST", self._orders_path(), body=body, safe_read=False
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        try:
            provider_order_id = _required_text(
                _first_value(
                    response,
                    "orderCreateTransaction",
                    "order",
                    "orderId",
                    "id",
                ),
                "provider order id",
            )
            status = _execution_status(response.get("status", "ACCEPTED"))
            return ExecutionResult(
                client_order_id=payload.client_order_id,
                provider_order_id=provider_order_id,
                status=status,
                latency_ms=latency_ms,
                environment=payload.environment,
                rejection_reason=_optional_text(response.get("errorMessage")),
            )
        except (TypeError, ValueError) as exc:
            raise BrokerConnectionError("broker order response was invalid") from exc

    def cancel_order(self, client_order_id: str) -> ExecutionResult:
        """Cancel one order through the demo endpoint without retrying."""
        if not client_order_id:
            raise ValueError("client_order_id must not be empty")
        response = self._request(
            "DELETE", self._order_path(client_order_id), safe_read=False
        )
        provider_order_id = _optional_text(
            _first_value(response, "order", "orderId", "id")
        )
        return ExecutionResult(
            client_order_id=client_order_id,
            provider_order_id=provider_order_id,
            status="ACCEPTED",
            environment=self.environment,
        )

    def reconcile_order(self, client_order_id: str) -> ExecutionResult | None:
        """Read an order state for reconciliation; never retries submission."""
        if not client_order_id:
            raise ValueError("client_order_id must not be empty")
        response = self._request(
            "GET", self._order_path(client_order_id), safe_read=True
        )
        status = _execution_status(response.get("status", "UNKNOWN"))
        return ExecutionResult(
            client_order_id=client_order_id,
            provider_order_id=_optional_text(
                _first_value(response, "order", "orderId", "id")
            ),
            status=status,
            environment=self.environment,
            rejection_reason=_optional_text(response.get("errorMessage")),
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: Mapping[str, object] | None = None,
        safe_read: bool,
    ) -> dict[str, object]:
        attempts = self.max_attempts if safe_read else 1
        request_body = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.api_token is not None:
            headers["Authorization"] = f"Bearer {self.api_token}"
        for attempt in range(attempts):
            try:
                raw_response = self.transport.request(
                    method,
                    f"{self.base_url}{path}",
                    headers=headers,
                    body=request_body,
                    timeout=self.timeout_seconds,
                )
                status_code, payload = self._decode_response(raw_response)
            except (TimeoutError, ConnectionError, OSError) as exc:
                if attempt + 1 < attempts:
                    self._backoff(attempt)
                    continue
                raise BrokerConnectionError("broker API is unreachable") from exc

            if 200 <= status_code < 300:
                return payload
            if status_code in self._TRANSIENT_STATUSES:
                if attempt + 1 < attempts:
                    self._backoff(attempt)
                    continue
                raise BrokerConnectionError(
                    f"broker API unavailable or rate-limited ({status_code})"
                )
            raise BrokerConnectionError(f"broker API rejected request ({status_code})")
        raise BrokerConnectionError("broker request exhausted retry policy")

    def _decode_response(self, response: object) -> tuple[int, dict[str, object]]:
        if isinstance(response, Mapping):
            return 200, dict(response)
        if not isinstance(response, BrokerResponse):
            raise BrokerConnectionError("broker transport returned an invalid response")
        try:
            payload = response.json()
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise BrokerConnectionError("broker response was not valid JSON") from exc
        if not isinstance(payload, Mapping):
            raise BrokerConnectionError("broker response body was not an object")
        return response.status_code, dict(payload)

    def _backoff(self, attempt: int) -> None:
        self._sleep(self.backoff_seconds * (2**attempt))

    def _account_path(self) -> str:
        if self.provider == "oanda":
            return f"/v3/accounts/{self.account_id}/summary"
        return f"/api/accounts/{self.account_id}"

    def _pricing_path(self, instrument: str) -> str:
        if self.provider == "oanda":
            return f"/v3/accounts/{self.account_id}/pricing?instruments={instrument}"
        return f"/api/accounts/{self.account_id}/quotes/{instrument}"

    def _orders_path(self) -> str:
        if self.provider == "oanda":
            return f"/v3/accounts/{self.account_id}/orders"
        return f"/api/accounts/{self.account_id}/orders"

    def _order_path(self, client_order_id: str) -> str:
        return f"{self._orders_path()}/{client_order_id}"

    def _order_body(self, payload: BrokerOrderPayload) -> dict[str, object]:
        if payload.environment not in {"PAPER", "DEMO"}:
            raise BrokerConnectionError("live order environments are forbidden")
        stop_loss = str(payload.stop_loss.price)
        take_profit = str(payload.take_profit.price)
        if self.provider == "oanda":
            units = str(
                payload.quantity if payload.direction == "LONG" else -payload.quantity
            )
            return {
                "order": {
                    "type": "MARKET",
                    "instrument": payload.instrument,
                    "units": units,
                    "clientExtensions": {"id": payload.client_order_id},
                    "stopLossOnFill": {"price": stop_loss},
                    "takeProfitOnFill": {"price": take_profit},
                }
            }
        return {
            "action": "DEAL",
            "symbol": payload.instrument,
            "volume": str(payload.quantity),
            "type": "ORDER_TYPE_BUY"
            if payload.direction == "LONG"
            else "ORDER_TYPE_SELL",
            "price": str(payload.entry_price),
            "sl": stop_loss,
            "tp": take_profit,
            "comment": payload.client_order_id,
            "environment": payload.environment,
        }

    def _parse_position(self, raw: Mapping[str, object]) -> OpenPosition:
        instrument = _required_text(
            raw.get("instrument", raw.get("symbol")), "position instrument"
        )
        if "long" in raw or "short" in raw:
            side_key = "long" if raw.get("long") else "short"
            side = _mapping(raw.get(side_key))
            units = _decimal(side.get("units"), "position units")
            return OpenPosition(
                instrument=instrument,
                direction="LONG" if side_key == "long" else "SHORT",
                quantity=abs(units),
                entry_price=_decimal(
                    side.get("averagePrice", side.get("entry_price")),
                    "position entry price",
                ),
            )
        units = _decimal(raw.get("units", raw.get("quantity")), "position units")
        return OpenPosition(
            instrument=instrument,
            direction="LONG" if units >= 0 else "SHORT",
            quantity=abs(units),
            entry_price=_decimal(
                raw.get("averagePrice", raw.get("entry_price")),
                "position entry price",
            ),
        )

    def _parse_quote(
        self, payload: Mapping[str, object], instrument: str
    ) -> MarketQuote:
        raw = payload
        prices = payload.get("prices")
        if isinstance(prices, Sequence) and not isinstance(prices, (str, bytes)):
            if not prices:
                raise ValueError("quote prices are empty")
            raw = _mapping(prices[0])
            bids = raw.get("bids")
            asks = raw.get("asks")
            if isinstance(bids, Sequence) and bids:
                raw = {**raw, "bid": _mapping(bids[0]).get("price")}
            if isinstance(asks, Sequence) and asks:
                raw = {**raw, "ask": _mapping(asks[0]).get("price")}
        return MarketQuote(
            instrument=_optional_text(raw.get("instrument")) or instrument,
            bid=_decimal(raw.get("bid"), "bid"),
            ask=_decimal(raw.get("ask"), "ask"),
            observed_at=self._timestamp(
                raw.get("timestamp", raw.get("time", raw.get("datetime")))
            ),
        )

    def _timestamp(self, value: object) -> datetime:
        if isinstance(value, datetime):
            return self._utc(value)
        if isinstance(value, (int, float)) and math.isfinite(value):
            return datetime.fromtimestamp(value, tz=timezone.utc)
        if isinstance(value, str):
            return self._utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
        raise ValueError("broker timestamp is missing or invalid")

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class OandaDemoAdapter(BrokerAdapter):
    """OANDA v20 practice adapter."""

    def __init__(
        self,
        transport: BrokerTransport,
        *,
        base_url: str,
        account_id: str,
        environment: Environment = "DEMO",
        api_token: str | None = None,
        timeout_seconds: float = 5.0,
        max_attempts: int = 3,
        backoff_seconds: float = 0.25,
        stale_after_seconds: float = 30.0,
        now: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        super().__init__(
            transport,
            provider="oanda",
            base_url=base_url,
            account_id=account_id,
            environment=environment,
            api_token=api_token,
            timeout_seconds=timeout_seconds,
            max_attempts=max_attempts,
            backoff_seconds=backoff_seconds,
            stale_after_seconds=stale_after_seconds,
            now=now,
            sleep=sleep,
        )


class MT5DemoAdapter(BrokerAdapter):
    """MetaTrader 5 demo adapter behind a REST bridge."""

    def __init__(
        self,
        transport: BrokerTransport,
        *,
        base_url: str,
        account_id: str,
        environment: Environment = "DEMO",
        api_token: str | None = None,
        timeout_seconds: float = 5.0,
        max_attempts: int = 3,
        backoff_seconds: float = 0.25,
        stale_after_seconds: float = 30.0,
        now: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        super().__init__(
            transport,
            provider="mt5",
            base_url=base_url,
            account_id=account_id,
            environment=environment,
            api_token=api_token,
            timeout_seconds=timeout_seconds,
            max_attempts=max_attempts,
            backoff_seconds=backoff_seconds,
            stale_after_seconds=stale_after_seconds,
            now=now,
            sleep=sleep,
        )


OandaBrokerAdapter = OandaDemoAdapter
MT5BrokerAdapter = MT5DemoAdapter


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("broker field must be an object")
    return value


def _decimal(value: object, field_name: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"{field_name} is invalid") from exc
    if not parsed.is_finite():
        raise ValueError(f"{field_name} must be finite")
    return parsed


def _required_text(value: object, field_name: str) -> str:
    text = _optional_text(value)
    if text is None:
        raise ValueError(f"{field_name} is missing")
    return text


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return _optional_text(value.get("id", value.get("orderId")))
    text = str(value)
    return text if text else None


def _execution_status(value: object) -> ExecutionStatus:
    status = str(value).upper()
    if status == "ACCEPTED":
        return "ACCEPTED"
    if status == "REJECTED":
        return "REJECTED"
    return "UNKNOWN"


def _first_value(payload: Mapping[str, object], *keys: str) -> object:
    for key in keys:
        if key in payload:
            return payload[key]
    return None
