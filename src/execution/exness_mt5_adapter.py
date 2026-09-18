"""Windows-hosted Exness MT5 bridge transport and startup health checks."""

from __future__ import annotations

import json
import math
import os
import ssl
from datetime import datetime, timezone
from typing import Mapping
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field

from trading_bot.config import DEMO_BRIDGE_HOSTS

from execution.broker_adapter import (
    BrokerConnectionError,
    BrokerResponse,
    UrllibTransport,
)


class BridgeHealth(BaseModel):
    """Sanitized health state reported by the host MT5 bridge."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    terminal_connected: bool
    authorized: bool
    account_id: str = Field(min_length=1)
    server: str = Field(min_length=1)
    server_time: datetime


class _BridgeResponse:
    """Minimal response object implementing the broker response protocol."""

    def __init__(self, status_code: int, body: bytes) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> object:
        return json.loads(self._body.decode("utf-8"))


class ExnessMT5BridgeTransport(UrllibTransport):
    """HTTP RPC transport to a Windows-hosted MT5 terminal bridge.

    The bridge is deliberately a narrow transport boundary. It must expose
    HTTPS JSON endpoints compatible with ``BaseBroker`` and a ``/health``
    endpoint returning terminal connectivity, authorization, account, server,
    and server-time fields.
    """

    _ALLOWED_HOSTS = DEMO_BRIDGE_HOSTS

    def __init__(self, base_url: str) -> None:
        parsed = urlparse(base_url)
        hostname = (parsed.hostname or "").lower().rstrip(".")
        if (
            parsed.scheme != "https"
            or parsed.username is not None
            or parsed.password is not None
            or hostname not in self._ALLOWED_HOSTS
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or parsed.port is None
            or not 1 <= parsed.port <= 65535
        ):
            raise ValueError("endpoint is not an allowlisted HTTPS bridge endpoint")
        self.base_url = base_url.rstrip("/")

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> object:
        """Call the local HTTPS bridge, trusting its operator-provided cert."""
        request = Request(url, data=body, headers=dict(headers), method=method)
        context = ssl.create_default_context()
        ca_file = os.getenv("EXNESS_BRIDGE_CA")
        if ca_file:
            context.load_verify_locations(cafile=ca_file)
        else:
            # The bridge's required default certificate is self-signed. The
            # endpoint is restricted to local allowlisted hosts above; operators
            # can set EXNESS_BRIDGE_CA to restore certificate verification.
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        try:
            with urlopen(request, timeout=timeout, context=context) as response:  # noqa: S310
                return _BridgeResponse(response.status, response.read())
        except HTTPError as exc:
            return _BridgeResponse(exc.code, exc.read())

    def check_health(self, *, timeout: float = 5.0) -> BridgeHealth:
        """Read and validate terminal/auth/server-clock health once."""
        try:
            raw = self.request(
                "GET",
                f"{self.base_url}/health",
                headers={"Accept": "application/json"},
                body=None,
                timeout=timeout,
            )
            status_code, payload = _decode_response(raw)
            if not 200 <= status_code < 300:
                raise BrokerConnectionError("MT5 bridge health check was rejected")
            return _parse_health(payload)
        except BrokerConnectionError:
            raise
        except (TimeoutError, ConnectionError, OSError, TypeError, ValueError) as exc:
            raise BrokerConnectionError("MT5 bridge health check failed") from exc

    def assert_ready(
        self,
        *,
        expected_server: str,
        expected_account_id: str,
        max_clock_drift_seconds: float,
        now: datetime | None = None,
        timeout: float = 5.0,
    ) -> BridgeHealth:
        """Fail closed unless terminal, auth, server, and clock are valid."""
        if not expected_server or not expected_account_id:
            raise ValueError("MT5 server and login are required")
        if max_clock_drift_seconds <= 0:
            raise ValueError("clock drift limit must be positive")
        health = self.check_health(timeout=timeout)
        if not health.terminal_connected:
            raise BrokerConnectionError("MT5 terminal is not connected")
        if not health.authorized:
            raise BrokerConnectionError("MT5 account authorization failed")
        if health.server != expected_server:
            raise BrokerConnectionError("MT5 server identity mismatch")
        if health.account_id != expected_account_id:
            raise BrokerConnectionError("MT5 account identity mismatch")
        current = _utc(now or datetime.now(timezone.utc))
        drift = abs((current - _utc(health.server_time)).total_seconds())
        if not math.isfinite(drift) or drift > max_clock_drift_seconds:
            raise BrokerConnectionError("MT5 server clock drift exceeds limit")
        return health


def _decode_response(response: object) -> tuple[int, Mapping[str, object]]:
    if isinstance(response, Mapping):
        return 200, response
    if not isinstance(response, BrokerResponse):
        raise BrokerConnectionError("MT5 bridge returned an invalid response")
    try:
        payload = response.json()
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise BrokerConnectionError("MT5 bridge returned invalid JSON") from exc
    if not isinstance(payload, Mapping):
        raise BrokerConnectionError("MT5 bridge health payload was not an object")
    return response.status_code, payload


def _parse_health(payload: Mapping[str, object]) -> BridgeHealth:
    try:
        connected = _required_bool(
            payload.get("terminalConnected", payload.get("terminal_connected")),
            "terminal connectivity",
        )
        authorized = _required_bool(
            payload.get("authorized", payload.get("accountAuthorized")),
            "account authorization",
        )
        account_id = _required_text(
            payload.get("accountId", payload.get("account_id")), "account id"
        )
        server = _required_text(payload.get("server"), "server")
        server_time = _timestamp(payload.get("serverTime", payload.get("server_time")))
        return BridgeHealth(
            terminal_connected=connected,
            authorized=authorized,
            account_id=account_id,
            server=server,
            server_time=server_time,
        )
    except (TypeError, ValueError) as exc:
        raise BrokerConnectionError("MT5 bridge health payload was invalid") from exc


def _required_bool(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be boolean")
    return value


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} is missing")
    return value


def _timestamp(value: object) -> datetime:
    if isinstance(value, datetime):
        return _utc(value)
    if isinstance(value, (int, float)) and math.isfinite(value):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str):
        return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    raise ValueError("server time is missing or invalid")


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
