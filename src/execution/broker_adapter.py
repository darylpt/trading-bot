"""Fail-closed Exness MT5 demo broker communication adapter."""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Literal, Protocol, runtime_checkable
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field

from trading_bot.config import DEMO_BRIDGE_HOSTS
from domain.models import BrokerOrderPayload, Direction, ExecutionResult, MarketCandle

Provider = Literal["exness_mt5"]
Environment = Literal["PAPER", "DEMO"]
ExecutionStatus = Literal[
    "ACCEPTED",
    "FILLED",
    "PARTIALLY_FILLED",
    "REJECTED",
    "CANCELLED",
    "EXPIRED",
    "UNKNOWN",
]


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
    position_id: str | None = None


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


class InstrumentMetadata(BaseModel):
    """Executable quantity, price, and protection constraints."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument: str = Field(min_length=1)
    contract_size: Decimal = Field(gt=0)
    tick_size: Decimal = Field(gt=0)
    tick_value: Decimal = Field(gt=0)
    quantity_step: Decimal = Field(gt=0)
    minimum_quantity: Decimal = Field(gt=0)
    maximum_quantity: Decimal | None = Field(default=None, gt=0)
    stop_level: Decimal = Field(ge=0)
    freeze_level: Decimal = Field(ge=0)
    precision: int = Field(ge=0, le=18)
    captured_at: datetime


class TradingSession(BaseModel):
    """Current instrument session state returned by the provider."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    instrument: str = Field(min_length=1)
    is_open: bool
    captured_at: datetime


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


class BaseBroker:
    """Standard broker abstraction restricted to paper/demo environments."""

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
        future_tolerance_seconds: float = 0.0,
        now: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        if environment not in {"PAPER", "DEMO"}:
            raise ValueError("broker adapter only supports paper/demo environments")
        parsed = urlparse(base_url)
        hostname = (parsed.hostname or "").lower().rstrip(".")
        if (
            parsed.scheme != "https"
            or parsed.username is not None
            or parsed.password is not None
            or not (
                parsed.port in {None, 443}
                or (hostname in DEMO_BRIDGE_HOSTS and parsed.port is not None)
            )
            or hostname not in {"demo.exness-mt5.local", *DEMO_BRIDGE_HOSTS}
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("endpoint is not an allowlisted HTTPS demo endpoint")
        if not account_id:
            raise ValueError("account is missing")
        if timeout_seconds <= 0 or max_attempts <= 0 or backoff_seconds < 0:
            raise ValueError("broker retry settings are invalid")
        if stale_after_seconds <= 0:
            raise ValueError("stale_after_seconds must be positive")
        if future_tolerance_seconds < 0:
            raise ValueError("future_tolerance_seconds must not be negative")
        self.transport = transport
        self.provider = provider
        self.base_url = base_url.rstrip("/")
        self.account_id = account_id
        self.environment = environment
        self.api_token = api_token
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self.backoff_seconds = backoff_seconds
        self.future_tolerance_seconds = future_tolerance_seconds
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

    def get_instrument_metadata(self, instrument: str) -> InstrumentMetadata:
        """Read broker quantity, price, and protection constraints."""
        if not instrument:
            raise ValueError("instrument must not be empty")
        payload = self._request(
            "GET", self._instrument_path(instrument), safe_read=True
        )
        try:
            instrument_field = payload.get("instrument")
            raw = _mapping(
                instrument_field if isinstance(instrument_field, Mapping) else payload
            )
            return InstrumentMetadata(
                instrument=_optional_text(raw.get("instrument", raw.get("symbol")))
                or instrument,
                contract_size=_decimal(
                    _first_value(raw, "contractSize", "contract_size"),
                    "contract size",
                ),
                tick_size=_decimal(
                    _first_value(raw, "tickSize", "tick_size"),
                    "tick size",
                ),
                tick_value=_decimal(
                    _first_value(raw, "tickValue", "tick_value"),
                    "tick value",
                ),
                quantity_step=_decimal(
                    _first_value(raw, "quantityStep", "volumeStep", "quantity_step"),
                    "quantity step",
                ),
                minimum_quantity=_decimal(
                    _first_value(raw, "minimumQuantity", "volumeMin", "min_quantity"),
                    "minimum quantity",
                ),
                maximum_quantity=(
                    None
                    if _first_value(raw, "maximumQuantity", "volumeMax", "max_quantity")
                    is None
                    else _decimal(
                        _first_value(
                            raw, "maximumQuantity", "volumeMax", "max_quantity"
                        ),
                        "maximum quantity",
                    )
                ),
                stop_level=_decimal(
                    _first_value(raw, "stopLevel", "stopsLevel", "stop_level"),
                    "stop level",
                ),
                freeze_level=_decimal(
                    _first_value(raw, "freezeLevel", "freeze_level"),
                    "freeze level",
                ),
                precision=_integer(
                    _first_value(raw, "precision", "digits"), "precision"
                ),
                captured_at=self._timestamp(
                    _first_value(raw, "timestamp", "time") or self._now()
                ),
            )
        except (TypeError, ValueError, InvalidOperation) as exc:
            raise BrokerConnectionError(
                "broker instrument metadata response was invalid"
            ) from exc

    def get_trading_session(self, instrument: str) -> TradingSession:
        """Read whether the instrument is currently tradeable."""
        payload = self._request("GET", self._session_path(instrument), safe_read=True)
        try:
            raw = _mapping(payload.get("session", payload))
            status = _first_value(raw, "isOpen", "open", "tradeable")
            if not isinstance(status, bool):
                raise ValueError("session status must be boolean")
            return TradingSession(
                instrument=_optional_text(raw.get("instrument", raw.get("symbol")))
                or instrument,
                is_open=status,
                captured_at=self._timestamp(
                    _first_value(raw, "timestamp", "time") or self._now()
                ),
            )
        except (TypeError, ValueError) as exc:
            raise BrokerConnectionError(
                "broker trading-session response was invalid"
            ) from exc

    def get_historical_candles(
        self,
        instrument: str,
        *,
        timeframe: Literal["15m", "1h"] = "15m",
        limit: int = 256,
    ) -> tuple[MarketCandle, ...]:
        """Backfill typed broker candles for strategy warm-up."""
        if not instrument or timeframe not in {"15m", "1h"} or limit <= 0:
            raise ValueError("historical candle request is invalid")
        payload = self._request(
            "GET",
            self._history_path(instrument, timeframe=timeframe, limit=limit),
            safe_read=True,
        )
        try:
            rows = payload.get("candles", payload.get("bars"))
            if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
                raise ValueError("historical candles must be a sequence")
            candles = tuple(
                MarketCandle(
                    instrument=_optional_text(row.get("instrument", row.get("symbol")))
                    or instrument,
                    timeframe=timeframe,
                    timestamp=self._timestamp(
                        _first_value(row, "timestamp", "time", "datetime")
                    ),
                    open=_decimal(_first_value(row, "open", "o"), "open"),
                    high=_decimal(_first_value(row, "high", "h"), "high"),
                    low=_decimal(_first_value(row, "low", "l"), "low"),
                    close=_decimal(_first_value(row, "close", "c"), "close"),
                    volume=(
                        None
                        if _first_value(row, "volume", "v") is None
                        else _decimal(_first_value(row, "volume", "v"), "volume")
                    ),
                )
                for value in rows
                for row in (_mapping(value),)
            )
            if not candles:
                raise ValueError("historical candles are empty")
            return candles
        except (TypeError, ValueError, InvalidOperation) as exc:
            raise BrokerConnectionError(
                "broker historical candle response was invalid"
            ) from exc

    def get_market_quote(self, instrument: str) -> MarketQuote:
        """Poll bid/ask data and reject quotes older than 30 seconds."""
        if not instrument:
            raise ValueError("instrument must not be empty")
        payload = self._request("GET", self._pricing_path(instrument), safe_read=True)
        try:
            quote = self._parse_quote(payload, instrument)
            age = (self._utc(self._now()) - quote.observed_at).total_seconds()
            if age < -self.future_tolerance_seconds or age > self.stale_after_seconds:
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
            "POST",
            self._orders_path(),
            body=body,
            safe_read=False,
            idempotency_key=payload.client_order_id,
        )
        latency_ms = round((time.perf_counter() - started) * 1000)
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
                filled_quantity=_optional_decimal(
                    _first_value(response, "filledQuantity", "filled_volume")
                ),
                fill_price=_optional_decimal(
                    _first_value(response, "fillPrice", "averagePrice")
                ),
                latency_ms=latency_ms,
                protection_confirmed=_protection_confirmation(response),
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

    def close_position(self, position: OpenPosition) -> ExecutionResult:
        """Close one identified broker position without retrying."""
        if not position.position_id:
            raise BrokerConnectionError("broker position id is unavailable")
        response = self._request(
            "POST",
            self._position_close_path(position.position_id),
            body={
                "symbol": position.instrument,
                "volume": str(position.quantity),
                "type": "ORDER_TYPE_SELL"
                if position.direction == "LONG"
                else "ORDER_TYPE_BUY",
                "environment": self.environment,
            },
            safe_read=False,
            idempotency_key=f"close-{position.position_id}",
        )
        try:
            return ExecutionResult(
                client_order_id=f"close-{position.position_id}",
                provider_order_id=_required_text(
                    _first_value(response, "order", "orderId", "id"),
                    "close order id",
                ),
                status=_execution_status(response.get("status", "UNKNOWN")),
                filled_quantity=_optional_decimal(
                    _first_value(response, "filledQuantity", "filled_volume")
                ),
                fill_price=_optional_decimal(
                    _first_value(response, "fillPrice", "averagePrice")
                ),
                protection_confirmed=True,
                environment=self.environment,
                rejection_reason=_optional_text(response.get("errorMessage")),
            )
        except (TypeError, ValueError) as exc:
            raise BrokerConnectionError("broker close response was invalid") from exc

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
            filled_quantity=_optional_decimal(
                _first_value(response, "filledQuantity", "filled_volume")
            ),
            fill_price=_optional_decimal(
                _first_value(response, "fillPrice", "averagePrice")
            ),
            protection_confirmed=_protection_confirmation(response),
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
        idempotency_key: str | None = None,
    ) -> dict[str, object]:
        attempts = self.max_attempts if safe_read else 1
        request_body = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
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
        return f"/api/accounts/{self.account_id}"

    def _pricing_path(self, instrument: str) -> str:
        return f"/api/accounts/{self.account_id}/quotes/{instrument}"

    def _instrument_path(self, instrument: str) -> str:
        return f"{self._account_path()}/instruments/{quote(instrument, safe='')}"

    def _session_path(self, instrument: str) -> str:
        return f"{self._instrument_path(instrument)}/session"

    def _history_path(self, instrument: str, *, timeframe: str, limit: int) -> str:
        query = urlencode(
            {"instrument": instrument, "timeframe": timeframe, "limit": limit}
        )
        return f"{self._account_path()}/candles?{query}"

    def _orders_path(self) -> str:
        return f"/api/accounts/{self.account_id}/orders"

    def _order_path(self, client_order_id: str) -> str:
        return f"{self._orders_path()}/{client_order_id}"

    def _position_close_path(self, position_id: str) -> str:
        return f"{self._account_path()}/positions/{quote(position_id, safe='')}/close"

    def _order_body(self, payload: BrokerOrderPayload) -> dict[str, object]:
        if payload.environment not in {"PAPER", "DEMO"}:
            raise BrokerConnectionError("live order environments are forbidden")
        return {
            "action": "DEAL",
            "symbol": payload.instrument,
            "volume": str(payload.quantity),
            "type": "ORDER_TYPE_BUY"
            if payload.direction == "LONG"
            else "ORDER_TYPE_SELL",
            "price": str(payload.entry_price),
            "sl": str(payload.stop_loss.price),
            "tp": str(payload.take_profit.price),
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
                position_id=_optional_text(
                    side.get("positionId", side.get("ticket", raw.get("positionId")))
                ),
            )
        return OpenPosition(
            instrument=instrument,
            direction="LONG" if units >= 0 else "SHORT",
            quantity=abs(units),
            entry_price=_decimal(
                raw.get("averagePrice", raw.get("entry_price")),
                "position entry price",
            ),
            position_id=_optional_text(
                raw.get("positionId", raw.get("ticket", raw.get("id")))
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


class ExnessMT5Broker(BaseBroker):
    """Exness MT5 demo adapter behind a REST bridge."""

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
        future_tolerance_seconds: float = 0.0,
        now: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        super().__init__(
            transport,
            provider="exness_mt5",
            base_url=base_url,
            account_id=account_id,
            environment=environment,
            api_token=api_token,
            timeout_seconds=timeout_seconds,
            max_attempts=max_attempts,
            backoff_seconds=backoff_seconds,
            stale_after_seconds=stale_after_seconds,
            future_tolerance_seconds=future_tolerance_seconds,
            now=now,
            sleep=sleep,
        )


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


def _integer(value: object, field_name: str) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} is invalid") from exc
    if parsed < 0:
        raise ValueError(f"{field_name} must be non-negative")
    return parsed


def _optional_decimal(value: object) -> Decimal | None:
    return None if value is None else _decimal(value, "optional decimal")


def _protection_confirmation(response: Mapping[str, object]) -> bool | None:
    explicit = response.get("protectionConfirmed", response.get("protected"))
    if isinstance(explicit, bool):
        return explicit
    stop_loss = _first_value(response, "stopLoss", "sl")
    take_profit = _first_value(response, "takeProfit", "tp")
    if stop_loss is not None and take_profit is not None:
        return True
    return None


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
    if status in {"FILLED", "EXECUTED", "COMPLETED"}:
        return "FILLED"
    if status in {"PARTIALLY_FILLED", "PARTIAL", "PARTIALLYFILLED"}:
        return "PARTIALLY_FILLED"
    if status == "ACCEPTED":
        return "ACCEPTED"
    if status in {"REJECTED", "DENIED"}:
        return "REJECTED"
    if status in {"CANCELLED", "CANCELED"}:
        return "CANCELLED"
    if status == "EXPIRED":
        return "EXPIRED"
    return "UNKNOWN"


def _first_value(payload: Mapping[str, object], *keys: str) -> object:
    for key in keys:
        if key in payload:
            return payload[key]
    return None
