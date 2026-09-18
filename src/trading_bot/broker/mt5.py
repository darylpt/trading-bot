"""Exness MT5 demo quote adapter with deterministic paper fallback."""

from __future__ import annotations

import importlib
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable, Literal, Protocol
from urllib.parse import urlparse

from domain.models import MarketCandle
from trading_bot.config import (
    DEFAULT_INSTRUMENT,
    DEMO_ACCOUNT_ID,
    DEMO_BRIDGE_HOSTS,
    validate_instrument,
)

from execution.broker_adapter import (
    BrokerConnectionError,
    BrokerTransport,
    ExnessMT5Broker,
    InstrumentMetadata,
    MarketQuote,
    TradingSession,
)
from execution.exness_mt5_adapter import (
    BridgeHealth,
    ExnessMT5BridgeTransport,
)

BrokerEnvironment = Literal["paper", "demo"]
AdapterMode = Literal["SIMULATED", "BROKER_DEMO", "REST", "NATIVE"]


@dataclass(frozen=True)
class BrokerConnectionConfig:
    """Non-live MT5 connection settings loaded from the environment."""

    token: str | None
    account: str | None
    server: str | None
    endpoint: str | None
    bridge_host: str | None = None
    bridge_port: int = 18812
    environment: BrokerEnvironment = "demo"
    runtime_mode: Literal["SIMULATED", "BROKER_DEMO"] = "SIMULATED"
    instrument: str = DEFAULT_INSTRUMENT
    fallback_data_path: Path = Path("data/market_data.csv")
    future_tolerance_seconds: float = 0.0

    def __post_init__(self) -> None:
        if self.environment not in {"paper", "demo"}:
            raise ValueError("MT5 adapter only supports paper/demo environments")
        if not self.instrument:
            raise ValueError("MT5 instrument must not be empty")
        validate_instrument(self.instrument)
        if self.future_tolerance_seconds < 0:
            raise ValueError("MT5 future tolerance must not be negative")
        if self.bridge_port < 1 or self.bridge_port > 65535:
            raise ValueError("MT5 bridge port is invalid")
        if self.runtime_mode == "BROKER_DEMO":
            if not self.account:
                raise ValueError("account is missing")
            if self.account != DEMO_ACCOUNT_ID:
                raise ValueError("account is not an allowlisted BROKER_DEMO account")
        if self.endpoint:
            parsed = urlparse(self.endpoint)
            hostname = (parsed.hostname or "").lower().rstrip(".")
            if (
                parsed.scheme != "https"
                or hostname not in {"demo.exness-mt5.local", *DEMO_BRIDGE_HOSTS}
                or not (
                    parsed.port in {None, 443}
                    or (hostname in DEMO_BRIDGE_HOSTS and parsed.port is not None)
                )
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("endpoint is not an allowlisted HTTPS demo endpoint")

    @classmethod
    def from_environment(
        cls,
        *,
        data_dir: Path,
        instrument: str = DEFAULT_INSTRUMENT,
        token: str | None = None,
        account: str | None = None,
        server: str | None = None,
        endpoint: str | None = None,
        bridge_host: str | None = None,
        bridge_port: int | None = None,
        environment: BrokerEnvironment | None = None,
        runtime_mode: Literal["SIMULATED", "BROKER_DEMO"] | None = None,
        future_tolerance_seconds: float = 0.0,
    ) -> BrokerConnectionConfig:
        raw_environment = os.getenv("BROKER_ENV", "demo") or "demo"
        selected_environment_value = environment or raw_environment
        if selected_environment_value == "paper":
            selected_environment: BrokerEnvironment = "paper"
        elif selected_environment_value == "demo":
            selected_environment = "demo"
        else:
            raise ValueError("BROKER_ENV must be paper or demo")
        raw_mode = os.getenv("TRADING_MODE", "SIMULATED") or "SIMULATED"
        selected_mode_value = runtime_mode or raw_mode
        if selected_mode_value == "SIMULATED":
            selected_mode: Literal["SIMULATED", "BROKER_DEMO"] = "SIMULATED"
        elif selected_mode_value == "BROKER_DEMO":
            selected_mode = "BROKER_DEMO"
        else:
            raise ValueError("TRADING_MODE must be SIMULATED or BROKER_DEMO")
        raw_bridge_port = os.getenv("EXNESS_BRIDGE_PORT", "18812")
        if bridge_port is None:
            try:
                selected_bridge_port = int(raw_bridge_port)
            except ValueError as exc:
                raise ValueError("EXNESS_BRIDGE_PORT must be an integer") from exc
        else:
            selected_bridge_port = bridge_port
        selected_bridge_host = (
            bridge_host
            if bridge_host is not None
            else os.getenv("EXNESS_BRIDGE_HOST") or None
        )
        selected_endpoint = (
            endpoint
            or os.getenv("BROKER_ENDPOINT")
            or (
                f"https://{selected_bridge_host}:{selected_bridge_port}"
                if selected_bridge_host
                else None
            )
        )
        selected_token = (
            token or os.getenv("EXNESS_PASSWORD") or os.getenv("BROKER_TOKEN")
        )
        selected_account = (
            account
            or os.getenv("EXNESS_LOGIN")
            or os.getenv("BROKER_ACCOUNT")
            or os.getenv("BROKER_ACCOUNT_ID")
        )
        selected_server = (
            server or os.getenv("EXNESS_SERVER") or os.getenv("BROKER_SERVER")
        )
        return cls(
            token=selected_token,
            account=selected_account,
            server=selected_server,
            endpoint=selected_endpoint,
            bridge_host=selected_bridge_host,
            bridge_port=selected_bridge_port,
            environment=selected_environment,
            runtime_mode=selected_mode,
            instrument=instrument,
            fallback_data_path=data_dir / "market_data.csv",
            future_tolerance_seconds=future_tolerance_seconds,
        )


class QuoteSource(Protocol):
    def poll_quote(self, instrument: str) -> MarketQuote: ...


class _NativeMT5QuoteSource:
    """Thin optional-binding boundary; MetaTrader5 is not a hard dependency."""

    def __init__(
        self,
        *,
        token: str,
        account: str,
        server: str,
        now: Callable[[], datetime],
    ) -> None:
        self._token = token
        self._account = account
        self._server = server
        self._now = now
        self._module = importlib.import_module("MetaTrader5")

    def poll_quote(self, instrument: str) -> MarketQuote:
        try:
            login = int(self._account)
        except ValueError as exc:
            raise BrokerConnectionError("MT5 account must be numeric") from exc
        initialize = getattr(self._module, "initialize", None)
        symbol_info_tick = getattr(self._module, "symbol_info_tick", None)
        shutdown = getattr(self._module, "shutdown", None)
        if not callable(initialize) or not callable(symbol_info_tick):
            raise BrokerConnectionError("MT5 bindings do not expose quote functions")
        try:
            initialized = initialize(
                login=login,
                password=self._token,
                server=self._server,
            )
            if not initialized:
                raise BrokerConnectionError("MT5 demo initialization failed")
            tick = symbol_info_tick(instrument)
            if tick is None:
                raise BrokerConnectionError("MT5 returned no quote")
            bid = Decimal(str(getattr(tick, "bid", 0)))
            ask = Decimal(str(getattr(tick, "ask", 0)))
            if not bid.is_finite() or not ask.is_finite() or ask <= bid:
                raise BrokerConnectionError("MT5 quote spread is invalid")
            raw_time_msc = getattr(tick, "time_msc", None)
            observed_at = (
                datetime.fromtimestamp(float(raw_time_msc) / 1000, tz=timezone.utc)
                if isinstance(raw_time_msc, (int, float))
                and math.isfinite(raw_time_msc)
                else self._now()
            )
            return MarketQuote(
                instrument=instrument,
                bid=bid,
                ask=ask,
                observed_at=observed_at,
            )
        finally:
            if callable(shutdown):
                shutdown()


class MT5QuoteSource:
    """Select native MT5, demo REST, or local simulated quote polling."""

    def __init__(
        self,
        config: BrokerConnectionConfig,
        *,
        fallback: QuoteSource,
        transport: BrokerTransport | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = config
        self._fallback = fallback
        self._now = now or (lambda: datetime.now(timezone.utc))
        self.mode: AdapterMode = (
            "BROKER_DEMO" if config.runtime_mode == "BROKER_DEMO" else "SIMULATED"
        )
        self.fallback_reason: str | None = None
        self._rest_adapter: ExnessMT5Broker | None = None
        self._bridge_transport: ExnessMT5BridgeTransport | None = None
        self._native_source: _NativeMT5QuoteSource | None = None

        if config.runtime_mode == "SIMULATED":
            if config.environment == "demo":
                self.fallback_reason = "BROKER_ENV=demo uses the local paper feed"
            elif config.endpoint and config.account:
                self.fallback_reason = "SIMULATED mode ignores broker configuration"
            else:
                self.fallback_reason = "SIMULATED mode uses the local paper feed"
            return
        if config.environment != "demo":
            self.fallback_reason = "BROKER_ENV must be demo for BROKER_DEMO"
            return
        if config.endpoint and config.account and config.token:
            bridge_transport: BrokerTransport
            if transport is not None:
                bridge_transport = transport
            else:
                self._bridge_transport = ExnessMT5BridgeTransport(config.endpoint)
                bridge_transport = self._bridge_transport
            self._rest_adapter = ExnessMT5Broker(
                bridge_transport,
                base_url=config.endpoint,
                account_id=config.account,
                environment="DEMO",
                api_token=config.token,
                future_tolerance_seconds=config.future_tolerance_seconds,
                now=self._now,
            )
            self.mode = "BROKER_DEMO"
            return
        missing_fields = tuple(
            name
            for name, value in (
                ("endpoint", config.endpoint),
                ("account", config.account),
                ("token", config.token),
            )
            if not value
        )
        self.fallback_reason = "BROKER_DEMO missing " + ", ".join(missing_fields)

    def health_check(
        self,
        *,
        expected_server: str,
        expected_account_id: str,
        max_clock_drift_seconds: float,
    ) -> BridgeHealth:
        if self._bridge_transport is None:
            raise BrokerConnectionError("MT5 bridge transport is unavailable")
        return self._bridge_transport.assert_ready(
            expected_server=expected_server,
            expected_account_id=expected_account_id,
            max_clock_drift_seconds=max_clock_drift_seconds,
            now=self._now(),
        )

    @property
    def broker(self) -> ExnessMT5Broker | None:
        """Return the REST broker when BROKER_DEMO is configured."""
        return self._rest_adapter

    def poll_quote(self, instrument: str | None = None) -> MarketQuote:
        symbol = instrument or self.config.instrument
        if self.config.runtime_mode == "BROKER_DEMO":
            if self._rest_adapter is None:
                raise BrokerConnectionError(
                    self.fallback_reason or "broker is unavailable"
                )
            try:
                return self._rest_adapter.get_market_quote(symbol)
            except (BrokerConnectionError, ValueError, OSError):
                self.fallback_reason = "MT5 quote polling failed"
                raise
        if self._rest_adapter is not None:
            return self._rest_adapter.get_market_quote(symbol)
        if self._native_source is not None:
            return self._native_source.poll_quote(symbol)
        return self._fallback.poll_quote(symbol)


class MT5BrokerAdapter:
    """Facade used by the runtime for demo-safe MT5 quote access."""

    def __init__(self, source: MT5QuoteSource) -> None:
        self.source = source

    @property
    def mode(self) -> AdapterMode:
        return self.source.mode

    @property
    def fallback_reason(self) -> str | None:
        return self.source.fallback_reason

    @property
    def broker(self) -> ExnessMT5Broker | None:
        """Return the configured broker gateway for readiness/reconciliation."""
        return self.source.broker

    def health_check(
        self,
        *,
        expected_server: str,
        expected_account_id: str,
        max_clock_drift_seconds: float,
    ) -> BridgeHealth:
        return self.source.health_check(
            expected_server=expected_server,
            expected_account_id=expected_account_id,
            max_clock_drift_seconds=max_clock_drift_seconds,
        )

    def get_instrument_metadata(self, instrument: str) -> InstrumentMetadata:
        if self.broker is None:
            raise BrokerConnectionError("broker-demo adapter is unavailable")
        return self.broker.get_instrument_metadata(instrument)

    def get_trading_session(self, instrument: str) -> TradingSession:
        if self.broker is None:
            raise BrokerConnectionError("broker-demo adapter is unavailable")
        return self.broker.get_trading_session(instrument)

    def get_historical_candles(
        self,
        instrument: str,
        *,
        timeframe: Literal["15m", "1h"] = "15m",
        limit: int = 256,
    ) -> tuple[MarketCandle, ...]:
        if self.broker is None:
            raise BrokerConnectionError("broker-demo adapter is unavailable")
        return self.broker.get_historical_candles(
            instrument, timeframe=timeframe, limit=limit
        )

    def poll_quote(self, instrument: str | None = None) -> MarketQuote:
        return self.source.poll_quote(instrument)

    def poll_market_data(self, instrument: str | None = None) -> MarketQuote:
        return self.poll_quote(instrument)

    get_market_quote = poll_market_data
