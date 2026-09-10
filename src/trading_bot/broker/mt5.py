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

from execution.broker_adapter import (
    BrokerConnectionError,
    BrokerTransport,
    MT5DemoAdapter,
    MarketQuote,
    UrllibTransport,
)

BrokerEnvironment = Literal["paper", "demo"]
AdapterMode = Literal["SIMULATED", "REST", "NATIVE"]


@dataclass(frozen=True)
class BrokerConnectionConfig:
    """Non-live MT5 connection settings loaded from the environment."""

    token: str | None
    account: str | None
    server: str | None
    endpoint: str | None
    environment: BrokerEnvironment = "demo"
    instrument: str = "EUR_USD"
    fallback_data_path: Path = Path("data/market_data.csv")

    def __post_init__(self) -> None:
        if self.environment not in {"paper", "demo"}:
            raise ValueError("MT5 adapter only supports paper/demo environments")
        if not self.instrument:
            raise ValueError("MT5 instrument must not be empty")
        if self.endpoint:
            hostname = (urlparse(self.endpoint).hostname or "").lower()
            if not any(
                marker in hostname
                for marker in ("demo", "practice", "paper", "localhost", "127.0.0.1")
            ):
                raise ValueError("MT5 endpoint must be a demo, paper, or localhost URL")

    @classmethod
    def from_environment(
        cls,
        *,
        data_dir: Path,
        instrument: str = "EUR_USD",
    ) -> BrokerConnectionConfig:
        raw_environment = os.getenv("BROKER_ENV", "demo") or "demo"
        if raw_environment == "paper":
            environment: BrokerEnvironment = "paper"
        elif raw_environment == "demo":
            environment = "demo"
        else:
            raise ValueError("BROKER_ENV must be paper or demo")
        return cls(
            token=os.getenv("BROKER_TOKEN") or None,
            account=os.getenv("BROKER_ACCOUNT")
            or os.getenv("BROKER_ACCOUNT_ID")
            or None,
            server=os.getenv("BROKER_SERVER") or None,
            endpoint=os.getenv("BROKER_ENDPOINT") or None,
            environment=environment,
            instrument=instrument,
            fallback_data_path=data_dir / "market_data.csv",
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
        self.mode: AdapterMode = "SIMULATED"
        self.fallback_reason: str | None = None
        self._rest_adapter: MT5DemoAdapter | None = None
        self._native_source: _NativeMT5QuoteSource | None = None

        if config.environment == "demo":
            self.fallback_reason = "BROKER_ENV=demo uses the local paper feed"
            return
        if config.endpoint and config.account:
            self._rest_adapter = MT5DemoAdapter(
                transport or UrllibTransport(),
                base_url=config.endpoint,
                account_id=config.account,
                environment="PAPER",
                api_token=config.token,
                now=self._now,
            )
            self.mode = "REST"
            return
        if config.token and config.account and config.server:
            try:
                self._native_source = _NativeMT5QuoteSource(
                    token=config.token,
                    account=config.account,
                    server=config.server,
                    now=self._now,
                )
                self.mode = "NATIVE"
                return
            except (ImportError, OSError, ValueError) as exc:
                self.fallback_reason = f"MT5 bindings unavailable: {type(exc).__name__}"
        else:
            self.fallback_reason = "MT5 credentials or server are unavailable"

    def poll_quote(self, instrument: str | None = None) -> MarketQuote:
        symbol = instrument or self.config.instrument
        try:
            if self._rest_adapter is not None:
                return self._rest_adapter.get_market_quote(symbol)
            if self._native_source is not None:
                return self._native_source.poll_quote(symbol)
        except (BrokerConnectionError, ValueError, OSError):
            self.fallback_reason = "MT5 quote polling failed; using local paper feed"
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

    def poll_quote(self, instrument: str | None = None) -> MarketQuote:
        return self.source.poll_quote(instrument)

    def poll_market_data(self, instrument: str | None = None) -> MarketQuote:
        return self.poll_quote(instrument)

    get_market_quote = poll_market_data
