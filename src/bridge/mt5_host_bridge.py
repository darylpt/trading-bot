"""Native Windows MT5 HTTPS JSON-RPC bridge for the Exness trading client.

Exposes:
    GET  /health                                 – terminal/auth/server-clock state
    POST /api/accounts/{id}                       – account snapshot
    GET  /api/accounts/{id}/quotes/{symbol}        – real-time bid/ask
    GET  /api/accounts/{id}/instruments/{symbol}   – instrument metadata
    GET  /api/accounts/{id}/instruments/{symbol}/session – session state
    GET  /api/accounts/{id}/candles?instrument=&timeframe=&limit= – OHLCV bars
    POST /api/accounts/{id}/orders                 – submit demo order
    GET  /api/accounts/{id}/orders/{client_id}     – reconcile order
    DELETE /api/accounts/{id}/orders/{client_id}   – cancel order
    POST /api/accounts/{id}/positions/{pos_id}/close – close position
    POST /rpc                                      – FastAPI JSON-RPC 2.0 endpoint

JSON-RPC methods:
    account.snapshot, quote.get, instrument.metadata,
    instrument.session, candles.get, order.submit,
    order.status, account.info, position.close,
    account_info, symbol_info_tick, order_send,
    positions_get, order_close

Safety rules (project-wide, authoritative):
    - DEMO/PAPER only; any live-mode request is rejected.
    - Mandatory SL/TP on every order; directional exit validation.
    - Non-finite / non-positive numeric values rejected.
    - Unknown methods, malformed JSON, missing fields → explicit JSON errors.
    - MT5 execution failures and timeouts → UNKNOWN plus halt-new-entries signal.
    - No secrets logged; error messages sanitized.
    - MT5 operations serialized (not thread-safe).
    - Uvicorn serves HTTPS with an explicit or auto-generated certificate.
"""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import math
import os
import shutil
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable, Literal, TypeVar
from urllib.parse import unquote, urlparse

import uvicorn

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse


# Optional dependency – only available on Windows with MetaTrader5 installed.
# Import guarded so the module loads/reviews on any platform; fails at runtime
# startup if the terminal is not reachable.
_mt5: Any = None  # set by _mt5_init()

LOGGER = logging.getLogger("mt5_bridge")

_ENVIRONMENT: Literal["DEMO"] = "DEMO"
_EXPECTED_ACCOUNT = "463948680"
_EXPECTED_SERVER = "Exness-MT5Trial17"
_LISTEN_HOST = "0.0.0.0"
_DEFAULT_PORT = 18812

_T = TypeVar("_T")


@dataclass(frozen=True)
class _MT5CallFailure:
    """Sanitized failure details for one serialized MT5 call."""

    phase: str
    exception_type: str | None
    last_error_code: int | str | None
    last_error_comment: str

    def as_dict(self) -> dict[str, object]:
        return {
            "phase": self.phase,
            "exception_type": self.exception_type,
            "last_error_code": self.last_error_code,
            "last_error_comment": self.last_error_comment,
        }

    def __str__(self) -> str:
        return (
            f"phase={self.phase} exception={self.exception_type or 'none'} "
            f"last_error_code={self.last_error_code!s} "
            f"last_error_comment={self.last_error_comment}"
        )


class MT5OperationError(RuntimeError):
    """An MT5 provider failure that makes the transaction state unknown."""

    def __init__(
        self,
        message: str,
        *,
        phase: str = "unknown",
        diagnostics: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.phase = phase
        self.diagnostics = diagnostics or {"phase": phase}


# MT5 operation serialization – bindings are not thread-safe.
_MT5_LOCK = threading.Lock()


# ── helpers ──────────────────────────────────────────────────────────────────


def _finite_pos(name: str, raw: object) -> Decimal:
    """Validate a required positive finite Decimal from a raw value."""
    try:
        d = Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f"{name} must be a valid number")
    if not d.is_finite() or d <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return d


def _finite_nonneg(name: str, raw: object) -> Decimal:
    """Validate a required non-negative finite Decimal from a raw value."""
    try:
        d = Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f"{name} must be a valid number")
    if not d.is_finite() or d < 0:
        raise ValueError(f"{name} must be finite and non-negative")
    return d


def _opt_finite_pos(raw: object) -> Decimal | None:
    """Optional positive finite Decimal."""
    if raw is None:
        return None
    try:
        d = Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return d if d.is_finite() and d > 0 else None


def _ts_to_iso(value: float) -> str:
    """Convert a POSIX timestamp (seconds) to ISO-8601 UTC string."""
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        return ""
    return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _supported_symbol(symbol: str) -> str:
    """Validate and normalize symbols to the MT5 broker spelling."""
    value = symbol.strip()
    if not value:
        raise ValueError("symbol must not be empty")
    normalized = value.upper().replace("_", "")
    if normalized in {"XAUUSD.M", "XAUUSDM"}:
        return "XAUUSDm"
    if normalized in {"EURUSD", "EURUSDM"}:
        return "EURUSDm"
    return value.upper()


def _preferred_filling_mode(symbol_info: object) -> int:
    """Choose a market filling mode supported by the MT5 symbol."""
    mode = int(getattr(symbol_info, "filling_mode", 0))
    ioc_flag = int(getattr(_mt5, "SYMBOL_FILLING_IOC", 2))
    fok_flag = int(getattr(_mt5, "SYMBOL_FILLING_FOK", 1))
    if mode & ioc_flag:
        return int(getattr(_mt5, "ORDER_FILLING_IOC", 1))
    if mode & fok_flag:
        return int(getattr(_mt5, "ORDER_FILLING_FOK", 0))
    return int(getattr(_mt5, "ORDER_FILLING_RETURN", 2))


# ── MT5 interaction (all calls go through the lock) ──────────────────────────


def _mt5_init(
    *,
    login: int,
    password: str,
    server: str,
) -> None:
    global _mt5
    try:
        _mt5 = importlib.import_module("MetaTrader5")
    except ImportError as exc:
        raise RuntimeError("MetaTrader5 Python package is not installed") from exc

    # Shutdown any prior session first (defensive).
    try:
        _mt5.shutdown()
    except Exception:  # noqa: BLE001 – best-effort cleanup
        pass

    if not _mt5.initialize(login=login, password=password, server=server):
        info = _mt5.last_error()
        raise RuntimeError(f"MT5 initialize failed: terminal_error={info!r}")

    ai = _mt5.account_info()
    if ai is None:
        _mt5.shutdown()
        raise RuntimeError("MT5 account_info returned None after initialization")

    actual_login = str(getattr(ai, "login", ""))
    actual_server = str(getattr(ai, "server", ""))
    if actual_login != str(login):
        _mt5.shutdown()
        raise RuntimeError("MT5 login mismatch")
    if actual_server != server:
        _mt5.shutdown()
        raise RuntimeError("MT5 server mismatch")

    LOGGER.info("MT5 initialized: server=%s", actual_server)


def _mt5_ready() -> tuple[bool, str]:
    """Check the existing MT5 session without resetting credentials."""
    if _mt5 is None:
        return False, "MetaTrader5 module not loaded"
    try:
        terminal = _mt5.terminal_info()
        account = _mt5.account_info()
    except Exception as exc:  # noqa: BLE001
        return False, type(exc).__name__
    if terminal is None:
        return False, "MT5 terminal info unavailable"
    if not bool(getattr(terminal, "connected", False)):
        return False, "MT5 terminal is disconnected"
    if account is None:
        return False, "MT5 account info unavailable"
    return True, ""


def _mt5_ensure_init() -> None:
    """Re-initialize the MT5 connection only when the existing session is unavailable."""
    if _mt5 is None:
        raise RuntimeError("MetaTrader5 module not loaded")
    ready, reason = _mt5_ready()
    if ready:
        return
    login = int(os.environ["EXNESS_LOGIN"])
    if not _mt5.initialize(
        login=login,
        password=os.environ["EXNESS_PASSWORD"],
        server=os.environ["EXNESS_SERVER"],
    ):
        raise RuntimeError(f"MT5 terminal unavailable: {reason}")


def _validate_mt5_identity() -> None:
    """Reject operations unless the expected connected demo account is active."""
    if _mt5 is None:
        raise RuntimeError("MetaTrader5 module not loaded")
    account = _mt5.account_info()
    terminal = _mt5.terminal_info()
    if account is None or terminal is None:
        raise RuntimeError("MT5 account or terminal state unavailable")
    if not bool(getattr(terminal, "connected", False)):
        raise RuntimeError("MT5 terminal is disconnected")
    if str(getattr(account, "login", "")) != _EXPECTED_ACCOUNT:
        raise RuntimeError("MT5 account identity mismatch")
    if str(getattr(account, "server", "")) != _EXPECTED_SERVER:
        raise RuntimeError("MT5 server identity mismatch")


def _sanitize_provider_text(value: object) -> str:
    """Keep provider diagnostics bounded and free of credential-like text."""
    text = " ".join(str(value).split())
    lowered = text.lower()
    if any(
        marker in lowered
        for marker in ("authorization", "bearer ", "password", "api_key", "token=")
    ):
        return "[redacted]"
    return text[:160] if text else ""


def _last_mt5_error_parts() -> tuple[int | str | None, str]:
    """Return only the MT5 numeric error and sanitized provider comment."""
    if _mt5 is None:
        return None, "MT5 module not loaded"
    try:
        err = _mt5.last_error()
        if isinstance(err, tuple) and len(err) >= 2:
            code = err[0] if isinstance(err[0], (int, str)) else None
            return code, _sanitize_provider_text(err[1])
        return None, _sanitize_provider_text(err or "unknown MT5 error")
    except Exception:  # noqa: BLE001
        return None, "MT5 error unavailable"


def _safe_call(
    fn: Callable[..., _T], label: str, *args: object, **kwargs: object
) -> tuple[_T | None, _MT5CallFailure | None]:
    """Execute an MT5 function and retain sanitized phase diagnostics."""
    with _MT5_LOCK:
        start = time.monotonic()
        try:
            _mt5_ensure_init()
            _validate_mt5_identity()
            if label == "order_check":
                result = _mt5.order_check(args[0])
            elif label == "order_send":
                result = _mt5.order_send(args[0])
            else:
                result = fn(*args, **kwargs)
            elapsed_ms = round((time.monotonic() - start) * 1000)
            LOGGER.debug("MT5 phase=%s completed in %dms", label, elapsed_ms)
            return result, None
        except Exception as exc:  # noqa: BLE001
            code, comment = _last_mt5_error_parts()
            failure = _MT5CallFailure(
                phase=label,
                exception_type=type(exc).__name__,
                last_error_code=code,
                last_error_comment=comment,
            )
            LOGGER.warning(
                "MT5 phase=%s failed exception=%s last_error_code=%s "
                "last_error_comment=%s",
                label,
                failure.exception_type,
                failure.last_error_code,
                failure.last_error_comment,
            )
            return None, failure


def _last_mt5_error() -> str:
    """Return a sanitized description of the last MT5 error."""
    code, comment = _last_mt5_error_parts()
    return f"retcode={code!s} comment={comment}"


def _ensure_symbol_selected(symbol: str) -> None:
    """Make the requested instrument visible before MT5 symbol operations."""
    result, err = _safe_call(_mt5.symbol_select, "symbol_select", symbol, True)
    if err or result is not True:
        raise RuntimeError(f"instrument selection failed: {err or _last_mt5_error()}")


def _tick_time_msec(tick: object) -> float:
    """Extract millisecond timestamp from tick, falling back to seconds."""
    raw_msc = getattr(tick, "time_msc", None)
    if isinstance(raw_msc, (int, float)) and math.isfinite(raw_msc) and raw_msc > 0:
        return raw_msc / 1000.0
    return float(getattr(tick, "time", 0))


# ── error / result responses ────────────────────────────────────────────────


def _err_json(handler: BaseHTTPRequestHandler, code: int, msg: str) -> None:
    """Send a JSON error response and log at WARNING or ERROR level."""
    body = json.dumps({"error": msg}).encode()
    if code >= 500:
        LOGGER.error("→ %d %s", code, msg)
    else:
        LOGGER.warning("→ %d %s", code, msg)
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Connection", "close")
    handler.end_headers()
    handler.wfile.write(body)


def _ok_json(handler: BaseHTTPRequestHandler, obj: object) -> None:
    """Send a 200 JSON response."""
    body = json.dumps(obj).encode()
    LOGGER.debug("→ 200 (%d bytes)", len(body))
    handler.send_response(200)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Connection", "close")
    handler.end_headers()
    handler.wfile.write(body)


def _rpc_reply(handler: BaseHTTPRequestHandler, req_id: object, result: object) -> None:
    """Send a JSON-RPC 2.0 success response."""
    _ok_json(handler, {"jsonrpc": "2.0", "id": req_id, "result": result})


def _rpc_err(
    handler: BaseHTTPRequestHandler,
    req_id: object,
    code: int,
    msg: str,
) -> None:
    """Send a JSON-RPC 2.0 error response."""
    body = json.dumps(
        {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": msg}}
    ).encode()
    LOGGER.warning("RPC error id=%s code=%d msg=%s", req_id, code, msg)
    handler.send_response(200)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Connection", "close")
    handler.end_headers()
    handler.wfile.write(body)


# ── HTTP request handler ────────────────────────────────────────────────────


class _BridgeHandler(BaseHTTPRequestHandler):
    """Dispatch incoming HTTPS requests to REST or JSON-RPC handlers."""

    # Suppress default stderr logging from BaseHTTPRequestHandler.
    def log_message(self, fmt: str, *args: object) -> None:
        LOGGER.debug(fmt, *args)

    def _read_body(self) -> bytes | None:
        """Read the request body; return None on error."""
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length <= 0:
            return b""
        if content_length > 1_048_576:  # 1 MiB limit
            _err_json(self, 413, "request body too large")
            return None
        return self.rfile.read(content_length)

    # ── routing ──────────────────────────────────────────────────────────

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path.rstrip("/")) or "/"

        LOGGER.debug("GET %s", path)

        if path == "/health":
            return self._handle_health()

        parts = path.split("/")
        if len(parts) >= 4 and parts[1] == "api" and parts[2] == "accounts":
            account_id = parts[3]
            if account_id != _EXPECTED_ACCOUNT:
                return _err_json(self, 403, "account identity mismatch")
            if len(parts) == 4:
                return self._handle_rest_account(account_id)
            rest = "/".join(parts[4:])
            if rest.startswith("quotes/"):
                return self._handle_rest_quote(account_id, rest[len("quotes/") :])
            if rest.startswith("instruments/"):
                inner = rest[len("instruments/") :]
                inner_parts = inner.split("/", 1)
                symbol = inner_parts[0]
                if len(inner_parts) == 2 and inner_parts[1] == "session":
                    return self._handle_rest_session(account_id, symbol)
                return self._handle_rest_instrument(account_id, symbol)
            if rest.startswith("candles"):
                return self._handle_rest_candles(account_id, parsed.query)
            if rest.startswith("orders/"):
                return self._handle_rest_order_status(
                    account_id, rest[len("orders/") :]
                )

        _err_json(self, 404, "not found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path.rstrip("/")) or "/"
        body = self._read_body()
        if body is None:
            return

        LOGGER.debug("POST %s", path)
        if path == "/rpc":
            return self._handle_rpc(body)

        parts = path.split("/")
        if len(parts) >= 5 and parts[1] == "api" and parts[2] == "accounts":
            account_id = parts[3]
            if account_id != _EXPECTED_ACCOUNT:
                return _err_json(self, 403, "account identity mismatch")
            rest = "/".join(parts[4:])
            if rest == "orders/preflight":
                return self._handle_rest_preflight_order(account_id, body)
            if rest == "orders":
                return self._handle_rest_submit_order(account_id, body)
            if rest.startswith("positions/"):
                inner = rest[len("positions/") :]
                inner_parts = inner.split("/", 1)
                if len(inner_parts) == 2 and inner_parts[1] == "close":
                    return self._handle_rest_close_position(
                        account_id, inner_parts[0], body
                    )
        _err_json(self, 404, "not found")

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path.rstrip("/")) or "/"

        LOGGER.debug("DELETE %s", path)
        parts = path.split("/")
        if len(parts) >= 5 and parts[1] == "api" and parts[2] == "accounts":
            account_id = parts[3]
            if account_id != _EXPECTED_ACCOUNT:
                return _err_json(self, 403, "account identity mismatch")
            rest = "/".join(parts[4:])
            if rest.startswith("orders/"):
                return self._handle_rest_cancel_order(
                    account_id, rest[len("orders/") :]
                )

        _err_json(self, 404, "not found")

    # ── health ────────────────────────────────────────────────────────────

    def _handle_health(self) -> None:
        """GET /health – 200 only when terminal is connected, authorized,
        and the account identity matches the expected defaults."""
        with _MT5_LOCK:
            connected, err = _mt5_ready()

        if not connected:
            _err_json(self, 503, f"MT5 terminal not connected: {err}")
            return

        with _MT5_LOCK:
            try:
                _mt5_ensure_init()
                ai = _mt5.account_info()
                ti = _mt5.terminal_info()
                server_time_raw = _mt5.copy_rates_from_pos(
                    "EURUSD", _mt5.TIMEFRAME_M1, 0, 1
                )
            except Exception as exc:  # noqa: BLE001
                _err_json(self, 503, f"MT5 state error: {type(exc).__name__}")
                return

        if ai is None or ti is None:
            _err_json(self, 503, "MT5 terminal info unavailable")
            return

        logged_in = bool(getattr(ai, "login", 0))
        connected_flag = bool(getattr(ti, "connected", False))
        actual_server = str(getattr(ai, "server", ""))
        actual_login = str(getattr(ai, "login", ""))
        server_time_iso = _now_iso()
        if server_time_raw is not None and len(server_time_raw) > 0:
            st = float(server_time_raw[0]["time"])
            if math.isfinite(st) and st > 0:
                server_time_iso = datetime.fromtimestamp(
                    st, tz=timezone.utc
                ).isoformat()

        authorized = logged_in and connected_flag
        terminal_ok = connected_flag and authorized
        if actual_server != _EXPECTED_SERVER:
            _err_json(self, 503, "MT5 server identity mismatch")
            return
        if actual_login != _EXPECTED_ACCOUNT:
            _err_json(self, 503, "MT5 account identity mismatch")
            return

        health = {
            "status": "ok",
            "connected": terminal_ok,
            "terminalConnected": terminal_ok,
            "authorized": authorized,
            "accountId": actual_login,
            "server": actual_server,
            "serverTime": server_time_iso,
        }
        LOGGER.info("health OK: server=%s", actual_server)
        _ok_json(self, health)

    # ── REST helpers (matching BaseBroker path expectations) ──────────────

    def _handle_rest_account(self, account_id: str) -> None:
        """GET /api/accounts/{id}
        Response matches ``get_account_snapshot`` in BaseBroker.

        Positions use the long/short nested format that ``_parse_position``
        expects (the direct-format branch has a known upstream bug where
        ``units`` is referenced before assignment).
        """
        result, err = _safe_call(_mt5.account_info, "account_info")
        if err or result is None:
            return _err_json(
                self, 503, f"account unavailable: {err or _last_mt5_error()}"
            )
        ai = result

        positions: list[dict[str, object]] = []
        result_p, err_p = _safe_call(_mt5.positions_get, "positions_get")
        if result_p is not None:
            for pos in result_p:
                pos_type = int(getattr(pos, "type", 0))
                vol = float(getattr(pos, "volume", 0))
                entry = float(getattr(pos, "price_open", 0))
                ticket = str(getattr(pos, "ticket", ""))
                side_key = "long" if pos_type == 0 else "short"
                positions.append(
                    {
                        "instrument": str(getattr(pos, "symbol", "")),
                        side_key: {
                            "units": str(vol if pos_type == 0 else -vol),
                            "averagePrice": str(entry),
                            "entry_price": str(entry),
                            "positionId": ticket,
                            "ticket": ticket,
                        },
                        "positionId": ticket,
                    }
                )

        account = {
            "balance": str(float(getattr(ai, "balance", 0))),
            "equity": str(float(getattr(ai, "equity", 0))),
            "NAV": str(float(getattr(ai, "equity", 0))),
            "margin": str(float(getattr(ai, "margin", 0))),
            "marginUsed": str(float(getattr(ai, "margin", 0))),
            "positions": positions,
            "open_positions": positions,
            "timestamp": _now_iso(),
            "time": _now_iso(),
        }
        _ok_json(self, {"account": account, "timestamp": account["timestamp"]})

    def _handle_rest_quote(self, account_id: str, symbol: str) -> None:
        """GET /api/accounts/{id}/quotes/{symbol}
        Response matches ``_parse_quote`` in BaseBroker.

        Returns either direct or OANDA-style prices[] format.
        """
        try:
            symbol = _supported_symbol(symbol)
        except ValueError as exc:
            return _err_json(self, 400, str(exc))

        result, err = _safe_call(_mt5.symbol_info_tick, "symbol_info_tick", symbol)
        if err or result is None:
            return _err_json(
                self, 503, f"quote unavailable: {err or _last_mt5_error()}"
            )

        tick = result
        bid = float(getattr(tick, "bid", 0))
        ask = float(getattr(tick, "ask", 0))
        if not (math.isfinite(bid) and math.isfinite(ask) and ask > bid):
            return _err_json(self, 503, "invalid quote data from MT5")

        observed_at = _ts_to_iso(_tick_time_msec(tick))
        if not observed_at:
            observed_at = _now_iso()

        # Return OANDA-style format that BaseBroker._parse_quote also handles.
        _ok_json(
            self,
            {
                "instrument": symbol,
                "bid": str(bid),
                "ask": str(ask),
                "timestamp": observed_at,
                "prices": [
                    {
                        "instrument": symbol,
                        "bids": [{"price": str(bid)}],
                        "asks": [{"price": str(ask)}],
                        "timestamp": observed_at,
                    }
                ],
            },
        )

    def _handle_rest_instrument(self, account_id: str, symbol: str) -> None:
        """GET /api/accounts/{id}/instruments/{symbol}
        Response matches ``get_instrument_metadata`` in BaseBroker.
        """
        try:
            symbol = _supported_symbol(symbol)
        except ValueError as exc:
            return _err_json(self, 400, str(exc))

        result, err = _safe_call(_mt5.symbol_info, "symbol_info", symbol)
        if err or result is None:
            return _err_json(
                self,
                503,
                f"instrument metadata unavailable: {err or _last_mt5_error()}",
            )

        si = result
        digits = int(getattr(si, "digits", 0))
        tick_size_raw = getattr(si, "trade_tick_size", None) or getattr(
            si, "point", 0.0001
        )
        tick_val_raw = getattr(si, "trade_tick_value", None) or getattr(
            si, "trade_contract_size", 100.0
        )
        contract_raw = getattr(si, "trade_contract_size", 100.0)

        info = {
            "contractSize": str(float(str(contract_raw))),
            "tickSize": str(float(str(tick_size_raw))),
            "tickValue": str(float(str(tick_val_raw))),
            "quantityStep": str(float(getattr(si, "volume_step", 0.01))),
            "minimumQuantity": str(float(getattr(si, "volume_min", 0.01))),
            "maximumQuantity": str(float(getattr(si, "volume_max", 100.0))),
            "stopLevel": str(float(getattr(si, "trade_stops_level", 0))),
            "freezeLevel": str(float(getattr(si, "freeze_level", 0))),
            "precision": digits,
            "timestamp": _now_iso(),
        }
        _ok_json(self, {"instrument": info, "timestamp": info["timestamp"]})

    def _handle_rest_session(self, account_id: str, symbol: str) -> None:
        """GET /api/accounts/{id}/instruments/{symbol}/session
        Response matches ``get_trading_session`` in BaseBroker.
        """
        try:
            symbol = _supported_symbol(symbol)
        except ValueError as exc:
            return _err_json(self, 400, str(exc))

        result, err = _safe_call(_mt5.symbol_info, "symbol_info", symbol)
        if err or result is None:
            return _err_json(
                self, 503, f"session unavailable: {err or _last_mt5_error()}"
            )

        si = result
        is_visible = bool(getattr(si, "visible", False))
        is_trade_mode = int(getattr(si, "trade_mode", 0))
        # trade_mode: 0=disabled, 1=longonly, 2=shortonly, 3=closeonly, 4=full
        is_open = is_visible and is_trade_mode != 0
        session = {
            "instrument": symbol,
            "isOpen": is_open,
            "timestamp": _now_iso(),
        }
        _ok_json(self, {"session": session, "timestamp": session["timestamp"]})

    def _handle_rest_candles(self, account_id: str, query: str) -> None:
        """GET /api/accounts/{id}/candles?instrument=X&timeframe=Y&limit=Z
        Response matches ``get_historical_candles`` in BaseBroker.
        """
        params: dict[str, str] = {}
        for part in query.split("&"):
            if "=" in part:
                key, query_value = part.split("=", 1)
                params[key] = query_value

        raw_instrument = params.get("instrument", "")
        timeframe_str = params.get("timeframe", "15m")
        try:
            limit = max(1, min(int(params.get("limit", "100")), 5000))
        except ValueError:
            return _err_json(self, 400, "limit must be an integer")

        try:
            symbol = _supported_symbol(raw_instrument)
        except ValueError as exc:
            return _err_json(self, 400, str(exc))

        tf_map = {
            "1m": _mt5.TIMEFRAME_M1,
            "5m": _mt5.TIMEFRAME_M5,
            "15m": _mt5.TIMEFRAME_M15,
            "1h": _mt5.TIMEFRAME_H1,
            "4h": _mt5.TIMEFRAME_H4,
            "1d": _mt5.TIMEFRAME_D1,
        }
        if timeframe_str in tf_map:
            tf = tf_map[timeframe_str]
        elif timeframe_str.endswith("m"):
            try:
                tf = int(timeframe_str[:-1])
            except ValueError:
                return _err_json(self, 400, f"unsupported timeframe: {timeframe_str}")
        else:
            return _err_json(self, 400, f"unsupported timeframe: {timeframe_str}")

        result, err = _safe_call(
            _mt5.copy_rates_from_pos, "copy_rates_from_pos", symbol, tf, 0, limit
        )
        if err:
            return _err_json(self, 503, f"candles unavailable: {err}")

        rates = result
        if rates is None or len(rates) == 0:
            return _err_json(self, 503, "MT5 returned no candle data")

        bars: list[dict[str, object]] = []
        for rate in rates:
            t = float(rate["time"])
            o = float(rate["open"])
            h = float(rate["high"])
            lo = float(rate["low"])
            c = float(rate["close"])
            candle_volume = float(str(rate.get("tick_volume", 0)))
            if not (
                math.isfinite(t)
                and math.isfinite(o)
                and math.isfinite(h)
                and math.isfinite(lo)
                and math.isfinite(c)
            ):
                continue
            bars.append(
                {
                    "instrument": symbol,
                    "timeframe": timeframe_str,
                    "timestamp": _ts_to_iso(t),
                    "open": str(o),
                    "high": str(h),
                    "low": str(lo),
                    "close": str(c),
                    "volume": str(candle_volume),
                }
            )

        if not bars:
            return _err_json(self, 503, "no valid candle data parsed")

        _ok_json(self, {"candles": bars, "bars": bars})

    def _handle_rest_submit_order(self, account_id: str, body: bytes) -> None:
        """POST /api/accounts/{id}/orders
        Response matches ``submit_order`` in BaseBroker.

        Expected request body:
            {
              "action": "DEAL",
              "symbol": "XAUUSDm",
              "volume": "0.01",
              "type": "ORDER_TYPE_BUY" | "ORDER_TYPE_SELL",
              "price": "...",
              "sl": "...",
              "tp": "...",
              "comment": "client-order-id",
              "environment": "DEMO"
            }
        """
        try:
            req = json.loads(body)
        except (json.JSONDecodeError, ValueError) as exc:
            return _err_json(self, 400, f"invalid JSON: {exc}")
        if not isinstance(req, dict):
            return _err_json(self, 400, "request must be a JSON object")
        try:
            result = _process_order(req)
        except MT5OperationError as exc:
            return _err_json(
                self,
                503,
                f"MT5 {exc.phase} failed; transaction state is unknown",
            )
        except ValueError as exc:
            return _err_json(self, 400, str(exc))

        status_code = 200 if result.get("status") in ("FILLED", "ACCEPTED") else 200
        body_bytes = json.dumps(result).encode()
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body_bytes)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body_bytes)

    def _handle_rest_preflight_order(self, account_id: str, body: bytes) -> None:
        """POST /api/accounts/{id}/orders/preflight without order submission."""
        try:
            req = json.loads(body)
        except (json.JSONDecodeError, ValueError) as exc:
            return _err_json(self, 400, f"invalid JSON: {exc}")
        if not isinstance(req, dict):
            return _err_json(self, 400, "request must be a JSON object")
        try:
            result = _process_order(req, submit=False)
        except ValueError as exc:
            return _err_json(self, 400, str(exc))
        _ok_json(self, result)

    def _handle_rest_cancel_order(self, account_id: str, client_id: str) -> None:
        """DELETE /api/accounts/{id}/orders/{client_order_id}
        Response matches ``cancel_order`` in BaseBroker.
        """
        if not client_id:
            return _err_json(self, 400, "client_order_id is required")

        # MT5 does not have a direct cancel-by-comment; look up the pending order.
        result, err = _safe_call(_mt5.orders_get, "orders_get")
        if err:
            return _err_json(self, 503, f"cancel lookup failed: {err}")

        orders: list[object] = (
            [
                candidate
                for candidate in result
                if str(getattr(candidate, "comment", "")) == client_id
            ]
            if result
            else []
        )
        if orders:
            order = orders[0]
            request = {
                "action": _mt5.TRADE_ACTION_REMOVE,
                "order": int(getattr(order, "ticket", 0)),
            }
            check_result, check_err = _safe_call(
                _mt5.order_check, "order_check", request
            )
            if check_err:
                return _err_json(self, 503, f"cancel check failed: {check_err}")
            send_result, send_err = _safe_call(_mt5.order_send, "order_send", request)
            if send_err:
                return _err_json(self, 503, f"cancel failed: {send_err}")
            if send_result and send_result.retcode != _mt5.TRADE_RETCODE_DONE:
                return _err_json(
                    self,
                    503,
                    f"MT5 cancel rejected: retcode={send_result.retcode} "
                    f"comment={getattr(send_result, 'comment', '')}",
                )

        # Always return 200 with the expected shape; BaseBroker just checks
        # for a valid orderId and ACCEPTED status.
        _ok_json(
            self,
            {
                "order": {"id": client_id},
                "orderId": client_id,
                "status": "ACCEPTED",
            },
        )

    def _handle_rest_close_position(
        self, account_id: str, pos_id: str, body: bytes
    ) -> None:
        """POST /api/accounts/{id}/positions/{pos_id}/close
        Response matches ``close_position`` in BaseBroker.
        """
        try:
            req = json.loads(body)
        except (json.JSONDecodeError, ValueError) as exc:
            return _err_json(self, 400, f"invalid JSON: {exc}")
        if not isinstance(req, dict):
            return _err_json(self, 400, "request must be a JSON object")

        try:
            result = _process_close(req)
        except ValueError as exc:
            return _err_json(self, 400, str(exc))

        if result.get("error"):
            return _err_json(self, 503, str(result["error"]))

        _ok_json(self, result)

    def _handle_rest_order_status(self, account_id: str, client_id: str) -> None:
        """GET /api/accounts/{id}/orders/{client_order_id}."""
        if not client_id:
            return _err_json(self, 400, "client_order_id is required")
        try:
            result = _get_order_status(client_id)
        except MT5OperationError as exc:
            return _err_json(
                self,
                503,
                f"MT5 {exc.phase} failed; reconciliation is unknown",
            )
        _ok_json(self, result)

    # ── JSON-RPC 2.0 ─────────────────────────────────────────────────────

    def _handle_rpc(self, body: bytes) -> None:
        """POST /rpc – JSON-RPC 2.0 single or batch."""
        try:
            raw = json.loads(body)
        except (json.JSONDecodeError, ValueError) as exc:
            return _err_json(self, 400, f"invalid JSON: {exc}")

        # Batch: array of request objects.
        if isinstance(raw, list):
            if not raw:
                return _err_json(self, 400, "empty RPC batch")
            replies: list[object] = []
            for item in raw:
                reply = _dispatch_rpc(item)
                if reply is not None:
                    replies.append(reply)
            body_bytes = json.dumps(replies).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body_bytes)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body_bytes)
            return

        if not isinstance(raw, dict):
            return _err_json(self, 400, "RPC request must be an object or array")

        reply = _dispatch_rpc(raw)
        if reply is None:
            # Notification (no id) – 204 No Content.
            self.send_response(204)
            self.send_header("Connection", "close")
            self.end_headers()
            return

        body_bytes = json.dumps(reply).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body_bytes)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body_bytes)


# ── RPC dispatch ─────────────────────────────────────────────────────────────


def _dispatch_rpc(req: object) -> object | None:
    """Dispatch one JSON-RPC 2.0 request; return None for notifications."""
    if not isinstance(req, dict):
        return {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32600, "message": "invalid request"},
        }
    if req.get("jsonrpc") != "2.0":
        return {
            "jsonrpc": "2.0",
            "id": req.get("id"),
            "error": {"code": -32600, "message": "jsonrpc must be 2.0"},
        }

    req_id = req.get("id")
    method = req.get("method")
    params = req.get("params")
    if not isinstance(method, str) or not method:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32600, "message": "missing method"},
        }
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32602, "message": "params must be an object"},
        }

    dispatch = {
        "account.snapshot": _rpc_account_snapshot,
        "quote.get": _rpc_quote_get,
        "instrument.metadata": _rpc_instrument_metadata,
        "instrument.session": _rpc_instrument_session,
        "candles.get": _rpc_candles_get,
        "order.submit": _rpc_order_submit,
        "order.preflight": _rpc_order_preflight,
        "order.status": _rpc_order_status,
        "account.info": _rpc_account_info,
        "position.close": _rpc_position_close,
        "account_info": _rpc_account_info_unknown_on_failure,
        "symbol_info_tick": _rpc_symbol_info_tick,
        "order_send": _rpc_order_send,
        "positions_get": _rpc_positions_get,
        "order_close": _rpc_order_close,
    }
    handler = dispatch.get(method)
    if handler is None:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"unknown method: {method}"},
        }

    try:
        result = handler(params)
        return {"jsonrpc": "2.0", "id": req_id, "result": result}
    except MT5OperationError as exc:
        LOGGER.error(
            "RPC %s failed phase=%s; transaction state is unknown",
            method,
            exc.phase,
        )
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {
                "code": -32001,
                "message": "MT5 operation failed; transaction state is unknown",
                "data": {
                    "status": "UNKNOWN",
                    "halt_new_entries": True,
                    "phase": exc.phase,
                    "diagnostics": exc.diagnostics,
                },
            },
        }
    except ValueError as exc:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32602, "message": str(exc)},
        }
    except Exception as exc:  # noqa: BLE001
        LOGGER.error("RPC %s failed: %s", method, type(exc).__name__)
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {
                "code": -32000,
                "message": "internal error; transaction state is unknown",
                "data": {"status": "UNKNOWN", "halt_new_entries": True},
            },
        }


# ── RPC method implementations ──────────────────────────────────────────────


def _rpc_account_snapshot(params: dict[str, object]) -> dict[str, object]:
    """account.snapshot – returns balance, equity, margin, positions.

    Positions use the long/short nested format compatible with
    ``BaseBroker._parse_position``.
    """
    result, err = _safe_call(_mt5.account_info, "account_info")
    if err or result is None:
        raise ValueError(f"account info unavailable: {err or _last_mt5_error()}")
    ai = result

    positions: list[dict[str, object]] = []
    result_p, err_p = _safe_call(_mt5.positions_get, "positions_get")
    if result_p is not None:
        for pos in result_p:
            pos_type = int(getattr(pos, "type", 0))
            vol = float(getattr(pos, "volume", 0))
            entry = float(getattr(pos, "price_open", 0))
            ticket = str(getattr(pos, "ticket", ""))
            side_key = "long" if pos_type == 0 else "short"
            positions.append(
                {
                    "instrument": str(getattr(pos, "symbol", "")),
                    side_key: {
                        "units": str(vol if pos_type == 0 else -vol),
                        "averagePrice": str(entry),
                        "entry_price": str(entry),
                        "positionId": ticket,
                        "ticket": ticket,
                    },
                    "positionId": ticket,
                }
            )

    return {
        "balance": str(float(getattr(ai, "balance", 0))),
        "equity": str(float(getattr(ai, "equity", 0))),
        "NAV": str(float(getattr(ai, "equity", 0))),
        "margin": str(float(getattr(ai, "margin", 0))),
        "marginUsed": str(float(getattr(ai, "margin", 0))),
        "positions": positions,
        "open_positions": positions,
        "timestamp": _now_iso(),
        "time": _now_iso(),
        "environment": "DEMO",
    }


def _rpc_quote_get(params: dict[str, object]) -> dict[str, object]:
    """quote.get – returns bid/ask for a symbol.

    Params: {"symbol": "XAUUSDm"}
    """
    symbol_raw = params.get("symbol")
    if not symbol_raw or not isinstance(symbol_raw, str):
        raise ValueError("symbol is required")
    symbol = _supported_symbol(symbol_raw)
    _ensure_symbol_selected(symbol)

    result, err = _safe_call(_mt5.symbol_info_tick, "symbol_info_tick", symbol)
    if err or result is None:
        raise ValueError(f"quote unavailable: {err or _last_mt5_error()}")
    tick = result
    bid = float(getattr(tick, "bid", 0))
    ask = float(getattr(tick, "ask", 0))
    if not (math.isfinite(bid) and math.isfinite(ask) and ask > bid):
        raise ValueError("invalid quote data from MT5")

    return {
        "instrument": symbol,
        "bid": str(bid),
        "ask": str(ask),
        "timestamp": _ts_to_iso(_tick_time_msec(tick)),
    }


def _rpc_instrument_metadata(params: dict[str, object]) -> dict[str, object]:
    """instrument.metadata – returns instrument trading constraints.

    Params: {"symbol": "XAUUSDm"}
    """
    symbol_raw = params.get("symbol")
    if not symbol_raw or not isinstance(symbol_raw, str):
        raise ValueError("symbol is required")
    symbol = _supported_symbol(symbol_raw)
    _ensure_symbol_selected(symbol)

    result, err = _safe_call(_mt5.symbol_info, "symbol_info", symbol)
    if err or result is None:
        raise ValueError(f"instrument unavailable: {err or _last_mt5_error()}")
    si = result

    digits = int(getattr(si, "digits", 0))
    tick_size = float(
        str(getattr(si, "trade_tick_size", None) or getattr(si, "point", 0.0001))
    )
    tick_val = float(
        str(
            getattr(si, "trade_tick_value", None)
            or getattr(si, "trade_contract_size", 100.0)
        )
    )
    contract = float(str(getattr(si, "trade_contract_size", 100.0)))

    return {
        "instrument": symbol,
        "contractSize": str(contract),
        "tickSize": str(tick_size),
        "tickValue": str(tick_val),
        "quantityStep": str(float(getattr(si, "volume_step", 0.01))),
        "minimumQuantity": str(float(getattr(si, "volume_min", 0.01))),
        "maximumQuantity": str(float(getattr(si, "volume_max", 100.0))),
        "stopLevel": str(float(getattr(si, "trade_stops_level", 0))),
        "freezeLevel": str(float(getattr(si, "freeze_level", 0))),
        "precision": digits,
        "timestamp": _now_iso(),
    }


def _rpc_instrument_session(params: dict[str, object]) -> dict[str, object]:
    """instrument.session – returns whether the symbol is tradeable.

    Params: {"symbol": "XAUUSDm"}
    """
    symbol_raw = params.get("symbol")
    if not symbol_raw or not isinstance(symbol_raw, str):
        raise ValueError("symbol is required")
    symbol = _supported_symbol(symbol_raw)
    _ensure_symbol_selected(symbol)

    result, err = _safe_call(_mt5.symbol_info, "symbol_info", symbol)
    if err or result is None:
        raise ValueError(f"session unavailable: {err or _last_mt5_error()}")
    si = result

    is_visible = bool(getattr(si, "visible", False))
    trade_mode = int(getattr(si, "trade_mode", 0))
    return {
        "instrument": symbol,
        "isOpen": is_visible and trade_mode != 0,
        "timestamp": _now_iso(),
    }


def _rpc_candles_get(params: dict[str, object]) -> dict[str, object]:
    """candles.get – returns historical OHLCV bars.

    Params: {"symbol": "XAUUSDm", "timeframe": "15m", "limit": 100}
    """
    symbol_raw = params.get("symbol")
    if not symbol_raw or not isinstance(symbol_raw, str):
        raise ValueError("symbol is required")
    symbol = _supported_symbol(symbol_raw)
    _ensure_symbol_selected(symbol)

    tf_raw = params.get("timeframe", "15m")
    tf_str = str(tf_raw)
    tf_map = {
        "1m": _mt5.TIMEFRAME_M1,
        "5m": _mt5.TIMEFRAME_M5,
        "15m": _mt5.TIMEFRAME_M15,
        "1h": _mt5.TIMEFRAME_H1,
        "4h": _mt5.TIMEFRAME_H4,
        "1d": _mt5.TIMEFRAME_D1,
    }
    if tf_str in tf_map:
        tf = tf_map[tf_str]
    elif tf_str.endswith("m"):
        try:
            tf = int(tf_str[:-1])
        except ValueError:
            raise ValueError(f"unsupported timeframe: {tf_str}")
    else:
        raise ValueError(f"unsupported timeframe: {tf_str}")

    try:
        limit = max(1, min(int(str(params.get("limit", 100))), 5000))
    except (ValueError, TypeError):
        raise ValueError("limit must be a positive integer")

    result, err = _safe_call(
        _mt5.copy_rates_from_pos, "copy_rates_from_pos", symbol, tf, 1, limit
    )
    if err:
        raise ValueError(f"candles unavailable: {err}")
    rates = result
    if rates is None or len(rates) == 0:
        raise ValueError("MT5 returned no candle data")

    bars: list[dict[str, object]] = []
    for rate in rates:
        t = float(rate["time"])
        o = float(rate["open"])
        h = float(rate["high"])
        lo = float(rate["low"])
        c = float(rate["close"])
        v = float(rate["tick_volume"])
        if not (
            math.isfinite(t)
            and math.isfinite(o)
            and math.isfinite(h)
            and math.isfinite(lo)
            and math.isfinite(c)
        ):
            continue
        bars.append(
            {
                "instrument": symbol,
                "timeframe": tf_str,
                "timestamp": _ts_to_iso(t),
                "open": str(o),
                "high": str(h),
                "low": str(lo),
                "close": str(c),
                "volume": str(v),
            }
        )

    if not bars:
        raise ValueError("no valid candle data parsed")
    return {"candles": bars, "bars": bars}


def _rpc_order_submit(params: dict[str, object]) -> dict[str, object]:
    """order.submit – submit a demo order with mandatory SL/TP."""
    return _process_order(params)


def _rpc_order_preflight(params: dict[str, object]) -> dict[str, object]:
    """order.preflight – run MT5 order_check without submitting."""
    return _process_order(params, submit=False)


def _rpc_order_status(params: dict[str, object]) -> dict[str, object]:
    """order.status – reconcile an order by client_order_id."""
    client_id_raw = params.get("client_order_id")
    if not client_id_raw or not isinstance(client_id_raw, str):
        raise ValueError("client_order_id is required")
    return _get_order_status(client_id_raw)


def _rpc_account_info(params: dict[str, object]) -> dict[str, object]:
    """account.info – returns account identity and balance."""
    result, err = _safe_call(_mt5.account_info, "account_info")
    if err or result is None:
        raise ValueError(f"account info unavailable: {err or _last_mt5_error()}")
    ai = result
    return {
        "login": str(getattr(ai, "login", "")),
        "server": str(getattr(ai, "server", "")),
        "balance": str(float(getattr(ai, "balance", 0))),
        "equity": str(float(getattr(ai, "equity", 0))),
        "margin": str(float(getattr(ai, "margin", 0))),
        "margin_free": str(float(getattr(ai, "margin_free", 0))),
        "leverage": int(getattr(ai, "leverage", 0)),
        "currency": str(getattr(ai, "currency", "")),
        "trade_allowed": bool(getattr(ai, "trade_allowed", False)),
        "timestamp": _now_iso(),
    }


def _rpc_position_close(params: dict[str, object]) -> dict[str, object]:
    """position.close – close an open position.

    Params: {
        "symbol": "XAUUSDm",
        "volume": "0.01",
        "type": "ORDER_TYPE_SELL",
        "position": 123456789,
        "environment": "DEMO"
    }
    """
    result = _process_close(params)
    if result.get("error"):
        raise ValueError(result["error"])
    return result


def _rpc_account_info_unknown_on_failure(
    params: dict[str, object],
) -> dict[str, object]:
    """Return account details or an explicit unknown-state RPC error."""
    try:
        return _rpc_account_info(params)
    except ValueError as exc:
        raise MT5OperationError("account info operation failed") from exc


def _rpc_symbol_info_tick(params: dict[str, object]) -> dict[str, object]:
    """Return the current MT5 tick or an explicit unknown-state error."""
    try:
        return _rpc_quote_get(params)
    except ValueError as exc:
        raise MT5OperationError("symbol_info_tick operation failed") from exc


def _rpc_order_send(params: dict[str, object]) -> dict[str, object]:
    """Submit an order; provider ambiguity is raised by the order processor."""
    return _rpc_order_submit(params)


def _rpc_positions_get(params: dict[str, object]) -> dict[str, object]:
    """Return open positions or an explicit unknown-state RPC error."""
    try:
        snapshot = _rpc_account_snapshot(params)
    except ValueError as exc:
        raise MT5OperationError("positions_get operation failed") from exc
    return {"positions": snapshot["positions"]}


def _rpc_order_close(params: dict[str, object]) -> dict[str, object]:
    """Close a position while distinguishing validation from MT5 failure."""
    try:
        return _rpc_position_close(params)
    except ValueError as exc:
        message = str(exc).lower()
        if any(
            marker in message
            for marker in ("unavailable", "failed", "returned none", "rejected")
        ):
            raise MT5OperationError("order_close operation failed") from exc
        raise


# ── shared order logic ───────────────────────────────────────────────────────


def _order_facts(
    *,
    symbol: str,
    volume: float,
    price: float,
    sl: float,
    tp: float,
    filling_mode: int,
    symbol_info: object,
) -> dict[str, object]:
    """Return safe request and constraint facts for diagnostics."""
    return {
        "symbol": symbol,
        "volume": volume,
        "price": price,
        "sl": sl,
        "tp": tp,
        "filling_mode": filling_mode,
        "volume_min": float(getattr(symbol_info, "volume_min", 0.0)),
        "volume_max": float(getattr(symbol_info, "volume_max", 0.0)),
        "volume_step": float(getattr(symbol_info, "volume_step", 0.0)),
        "stops_level": int(getattr(symbol_info, "trade_stops_level", 0)),
        "freeze_level": int(getattr(symbol_info, "trade_freeze_level", 0)),
    }


def _provider_request_facts(request: object) -> dict[str, object]:
    """Extract safe fields from the provider's normalized request object."""
    return {
        name: getattr(request, name)
        for name in (
            "action",
            "symbol",
            "volume",
            "price",
            "sl",
            "tp",
            "deviation",
            "type",
            "type_filling",
            "type_time",
        )
        if hasattr(request, name)
    }


def _rejected_order_response(
    *,
    retcode: int | str,
    comment: str,
    phase: str,
    diagnostics: dict[str, object],
) -> dict[str, object]:
    """Return a typed deterministic rejection without an order identifier."""
    safe_comment = _sanitize_provider_text(comment)
    return {
        "orderCreateTransaction": {"id": "0"},
        "order": {"id": "0"},
        "orderId": "0",
        "status": "REJECTED",
        "phase": phase,
        "retcode": retcode,
        "comment": safe_comment,
        "errorMessage": f"{phase} rejected: retcode={retcode} comment={safe_comment}",
        "diagnostics": diagnostics,
        "protectionConfirmed": False,
        "protected": False,
    }


def _process_order(req: dict[str, object], *, submit: bool = True) -> dict[str, object]:
    """Validate, preflight, and optionally submit one protected deal order."""
    symbol_raw = req.get("symbol")
    volume_raw = req.get("volume")
    order_type_raw = req.get("type")
    price_raw = req.get("price")
    sl_raw = req.get("sl")
    tp_raw = req.get("tp")
    client_id = req.get("comment")
    if not isinstance(client_id, str) or not client_id.strip():
        raise ValueError("comment client_order_id is required")
    client_id = client_id.strip()
    env_raw = str(req.get("environment", "DEMO")).upper()

    if env_raw not in ("PAPER", "DEMO"):
        raise ValueError("only PAPER/DEMO environments are allowed")
    if not symbol_raw or not isinstance(symbol_raw, str):
        raise ValueError("symbol is required")
    symbol = _supported_symbol(symbol_raw)
    _ensure_symbol_selected(symbol)

    if not order_type_raw or not isinstance(order_type_raw, str):
        raise ValueError("type is required (ORDER_TYPE_BUY or ORDER_TYPE_SELL)")
    otype = order_type_raw.strip().upper()
    if otype == "ORDER_TYPE_BUY":
        mt5_type = _mt5.ORDER_TYPE_BUY
    elif otype == "ORDER_TYPE_SELL":
        mt5_type = _mt5.ORDER_TYPE_SELL
    else:
        raise ValueError(f"unsupported order type: {order_type_raw}")

    volume = _finite_pos("volume", volume_raw)
    price = _finite_pos("price", price_raw)
    sl = _finite_pos("sl", sl_raw)
    tp = _finite_pos("tp", tp_raw)
    price_f = float(price)
    sl_f = float(sl)
    tp_f = float(tp)
    if mt5_type == _mt5.ORDER_TYPE_BUY:
        if not (sl_f < price_f < tp_f):
            raise ValueError("BUY exits must satisfy sl < price < tp")
    elif not (tp_f < price_f < sl_f):
        raise ValueError("SELL exits must satisfy tp < price < sl")

    result_si, err_si = _safe_call(_mt5.symbol_info, "symbol_info", symbol)
    if err_si or result_si is None:
        raise ValueError(f"symbol info unavailable: {err_si or _last_mt5_error()}")
    si = result_si
    vol_min = float(getattr(si, "volume_min", 0.01))
    vol_max = float(getattr(si, "volume_max", 100.0))
    vol_step = float(getattr(si, "volume_step", 0.01))
    volume_f = float(volume)
    if volume_f < vol_min:
        raise ValueError(f"volume {volume_f} below minimum {vol_min}")
    if volume_f > vol_max:
        raise ValueError(f"volume {volume_f} above maximum {vol_max}")
    if vol_step > 0:
        steps = round(volume_f / vol_step, 6)
        if abs(steps - round(steps)) > 1e-9:
            raise ValueError(f"volume {volume_f} not aligned to step {vol_step}")

    result_tick, err_tick = _safe_call(
        _mt5.symbol_info_tick, "symbol_info_tick", symbol
    )
    if err_tick or result_tick is None:
        raise ValueError(f"market data unavailable: {err_tick or _last_mt5_error()}")

    filling_mode = _preferred_filling_mode(si)
    facts = _order_facts(
        symbol=symbol,
        volume=volume_f,
        price=price_f,
        sl=sl_f,
        tp=tp_f,
        filling_mode=filling_mode,
        symbol_info=si,
    )
    request: dict[str, object] = {
        "action": _mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": volume_f,
        "type": mt5_type,
        "price": price_f,
        "sl": sl_f,
        "tp": tp_f,
        "deviation": 20,
        "magic": 234000,
        "comment": client_id,
        "type_time": _mt5.ORDER_TIME_GTC,
        "type_filling": filling_mode,
    }

    LOGGER.info(
        "order_submit phase=prepare symbol=%s type=%s vol=%s sl=%s tp=%s",
        symbol,
        otype,
        volume,
        sl,
        tp,
    )
    LOGGER.info("order_phase phase=order_check facts=%s", facts)

    result_check, err_check = _safe_call(_mt5.order_check, "order_check", request)
    if err_check:
        return _rejected_order_response(
            retcode=err_check.last_error_code or "CALL_FAILED",
            comment=err_check.last_error_comment,
            phase="order_check",
            diagnostics={"request": facts, "failure": err_check.as_dict()},
        )
    if result_check is None:
        code, comment = _last_mt5_error_parts()
        return _rejected_order_response(
            retcode=code or "NO_RESULT",
            comment=comment,
            phase="order_check",
            diagnostics={
                "request": facts,
                "failure": {
                    "phase": "order_check",
                    "exception_type": None,
                    "last_error_code": code,
                    "last_error_comment": comment,
                },
            },
        )
    check_retcode = int(getattr(result_check, "retcode", 0))
    check_comment = _sanitize_provider_text(getattr(result_check, "comment", ""))
    provider_facts = _provider_request_facts(getattr(result_check, "request", None))
    LOGGER.info(
        "order_phase phase=order_check_result retcode=%s comment=%s "
        "facts=%s provider_facts=%s",
        check_retcode,
        check_comment,
        facts,
        provider_facts,
    )
    if check_retcode not in (0, _mt5.TRADE_RETCODE_DONE):
        return _rejected_order_response(
            retcode=check_retcode,
            comment=check_comment,
            phase="order_check",
            diagnostics={"request": facts, "provider_request": provider_facts},
        )
    if not submit:
        return {
            "orderCreateTransaction": {"id": "0"},
            "order": {"id": "0"},
            "orderId": "0",
            "status": "ACCEPTED",
            "phase": "order_check",
            "retcode": check_retcode,
            "comment": check_comment,
            "preflight": True,
            "protectionConfirmed": True,
            "protected": True,
            "diagnostics": {
                "request": facts,
                "provider_request": provider_facts,
            },
        }
    LOGGER.info("order_phase phase=order_send facts=%s", facts)
    result_send, err_send = _safe_call(_mt5.order_send, "order_send", request)
    if err_send:
        raise MT5OperationError(
            "MT5 order_send failed; transaction state is unknown",
            phase="order_send",
            diagnostics={"request": facts, "failure": err_send.as_dict()},
        )
    if result_send is None:
        code, comment = _last_mt5_error_parts()
        raise MT5OperationError(
            "MT5 order_send returned no result; transaction state is unknown",
            phase="order_send",
            diagnostics={
                "request": facts,
                "failure": {
                    "phase": "order_send",
                    "exception_type": None,
                    "last_error_code": code,
                    "last_error_comment": comment,
                },
            },
        )

    deal = result_send
    retcode = int(getattr(deal, "retcode", 0))
    order_ticket = getattr(deal, "order", 0)
    deal_ticket = getattr(deal, "deal", 0)
    filled_volume = getattr(deal, "volume", 0.0)
    deal_price = getattr(deal, "price", 0.0)
    deal_comment = _sanitize_provider_text(getattr(deal, "comment", ""))
    LOGGER.info(
        "order_result phase=order_send retcode=%s order=%s deal=%s vol=%s "
        "price=%s comment=%s",
        retcode,
        order_ticket,
        deal_ticket,
        filled_volume,
        deal_price,
        deal_comment,
    )

    if retcode == _mt5.TRADE_RETCODE_DONE:
        protection_confirmed = sl_raw is not None and tp_raw is not None
        return {
            "orderCreateTransaction": {"id": str(deal_ticket)},
            "order": {"id": str(deal_ticket)},
            "orderId": str(deal_ticket),
            "status": "FILLED",
            "phase": "order_send",
            "retcode": retcode,
            "comment": deal_comment,
            "filledQuantity": str(float(filled_volume)),
            "filled_volume": str(float(filled_volume)),
            "fillPrice": str(float(deal_price)),
            "averagePrice": str(float(deal_price)),
            "stopLoss": str(sl_f),
            "sl": str(sl_f),
            "takeProfit": str(tp_f),
            "tp": str(tp_f),
            "protectionConfirmed": protection_confirmed,
            "protected": protection_confirmed,
        }
    return _rejected_order_response(
        retcode=retcode,
        comment=deal_comment,
        phase="order_send",
        diagnostics={"request": facts},
    )


def _get_order_status(client_id: str) -> dict[str, object]:
    """Reconcile an order using complete authoritative MT5 snapshots."""
    result_h, err_h = _safe_call(
        _mt5.history_deals_get,
        "history_deals_get",
        datetime(1970, 1, 1, tzinfo=timezone.utc),
        datetime.now(timezone.utc),
    )
    if err_h or result_h is None:
        failure = err_h or _MT5CallFailure(
            "history_deals_get", None, *_last_mt5_error_parts()
        )
        raise MT5OperationError(
            "MT5 order history is unavailable",
            phase="history_deals_get",
            diagnostics={"failure": failure.as_dict()},
        )
    LOGGER.info("order_phase phase=history_deals_get completed")
    for deal in result_h:
        if str(getattr(deal, "comment", "")) != client_id:
            continue
        sl = float(getattr(deal, "sl", 0))
        tp = float(getattr(deal, "tp", 0))
        return {
            "order": {"id": str(getattr(deal, "ticket", ""))},
            "orderId": str(getattr(deal, "ticket", "")),
            "status": "FILLED",
            "filledQuantity": str(float(getattr(deal, "volume", 0))),
            "filled_volume": str(float(getattr(deal, "volume", 0))),
            "fillPrice": str(float(getattr(deal, "price", 0))),
            "averagePrice": str(float(getattr(deal, "price", 0))),
            "stopLoss": str(sl),
            "sl": str(sl),
            "takeProfit": str(tp),
            "tp": str(tp),
            "protectionConfirmed": sl > 0 and tp > 0,
            "protected": sl > 0 and tp > 0,
        }

    result_p, err_p = _safe_call(_mt5.positions_get, "positions_get")
    if err_p or result_p is None:
        failure = err_p or _MT5CallFailure(
            "positions_get", None, *_last_mt5_error_parts()
        )
        raise MT5OperationError(
            "MT5 positions snapshot is unavailable",
            phase="positions_get",
            diagnostics={"failure": failure.as_dict()},
        )
    LOGGER.info("order_phase phase=positions_get completed")
    for pos in result_p:
        if str(getattr(pos, "comment", "")) != client_id:
            continue
        sl = float(getattr(pos, "sl", 0))
        tp = float(getattr(pos, "tp", 0))
        return {
            "order": {"id": str(getattr(pos, "ticket", ""))},
            "orderId": str(getattr(pos, "ticket", "")),
            "status": "FILLED",
            "filledQuantity": str(float(getattr(pos, "volume", 0))),
            "filled_volume": str(float(getattr(pos, "volume", 0))),
            "fillPrice": str(float(getattr(pos, "price_open", 0))),
            "averagePrice": str(float(getattr(pos, "price_open", 0))),
            "stopLoss": str(sl),
            "sl": str(sl),
            "takeProfit": str(tp),
            "tp": str(tp),
            "protectionConfirmed": sl > 0 and tp > 0,
            "protected": sl > 0 and tp > 0,
        }

    result_o, err_o = _safe_call(_mt5.orders_get, "orders_get")
    if err_o or result_o is None:
        failure = err_o or _MT5CallFailure("orders_get", None, *_last_mt5_error_parts())
        raise MT5OperationError(
            "MT5 pending-order snapshot is unavailable",
            phase="orders_get",
            diagnostics={"failure": failure.as_dict()},
        )
    LOGGER.info("order_phase phase=orders_get completed")
    for order in result_o:
        if str(getattr(order, "comment", "")) != client_id:
            continue
        return {
            "order": {"id": str(getattr(order, "ticket", ""))},
            "orderId": str(getattr(order, "ticket", "")),
            "status": "ACCEPTED",
            "protectionConfirmed": False,
            "protected": False,
        }

    return {
        "status": "ORDER_NOT_FOUND",
        "reconciliation": {
            "checked_at": _now_iso(),
            "history_deals_checked": True,
            "positions_checked": True,
            "orders_checked": True,
        },
        "errorMessage": "authoritative broker search found no matching order",
    }


def _process_close(req: dict[str, object]) -> dict[str, object]:
    """Process a position close request (used by both REST and RPC).

    Expected request fields:
        symbol, volume, type (ORDER_TYPE_BUY or ORDER_TYPE_SELL),
        position (optional ticket), environment

    Returns a dict matching what BaseBroker.close_position expects.
    """
    symbol_raw = req.get("symbol")
    volume_raw = req.get("volume")
    order_type_raw = req.get("type")
    position_id = req.get("position")
    env_raw = str(req.get("environment", "DEMO")).upper()

    if env_raw not in ("PAPER", "DEMO"):
        return {"error": "only PAPER/DEMO environments are allowed"}
    if not symbol_raw or not isinstance(symbol_raw, str):
        return {"error": "symbol is required"}
    symbol = _supported_symbol(symbol_raw)
    _ensure_symbol_selected(symbol)

    if not order_type_raw or not isinstance(order_type_raw, str):
        return {"error": "type is required"}
    otype = order_type_raw.strip().upper()
    if otype == "ORDER_TYPE_SELL":
        mt5_type = _mt5.ORDER_TYPE_SELL
    elif otype == "ORDER_TYPE_BUY":
        mt5_type = _mt5.ORDER_TYPE_BUY
    else:
        return {"error": f"unsupported close type: {order_type_raw}"}

    volume = _finite_pos("volume", volume_raw)

    # Validate lot size.
    result_si, err_si = _safe_call(_mt5.symbol_info, "symbol_info", symbol)
    if err_si or result_si is None:
        return {"error": f"symbol info unavailable: {err_si or _last_mt5_error()}"}
    si = result_si
    vol_min = float(getattr(si, "volume_min", 0.01))
    vol_max = float(getattr(si, "volume_max", 100.0))
    volume_f = float(volume)
    if volume_f < vol_min:
        return {"error": f"volume {volume_f} below minimum {vol_min}"}
    if volume_f > vol_max:
        return {"error": f"volume {volume_f} above maximum {vol_max}"}

    # Find the position to close.
    result_p, err_p = _safe_call(_mt5.positions_get, "positions_get", symbol=symbol)
    if err_p:
        return {"error": f"positions query failed: {err_p}"}

    positions = result_p
    if not positions or len(positions) == 0:
        return {"error": f"no open position for {symbol}"}

    if position_id is not None and position_id != "":
        target = None
        for pos in positions:
            if str(getattr(pos, "ticket", "")) == str(position_id):
                target = pos
                break
        if target is None:
            return {"error": f"position {position_id} not found for {symbol}"}
    else:
        target = positions[0]

    current_volume = float(getattr(target, "volume", 0))
    if abs(volume_f - current_volume) > 1e-9:
        return {
            "error": f"volume mismatch: requested={volume_f} current={current_volume}"
        }

    request: dict[str, object] = {
        "action": _mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": volume_f,
        "type": mt5_type,
        "position": int(getattr(target, "ticket", 0)),
        "deviation": 20,
        "magic": 234000,
        "comment": f"close-{getattr(target, 'ticket', '')}",
        "type_time": _mt5.ORDER_TIME_GTC,
        "type_filling": _preferred_filling_mode(si),
    }

    LOGGER.info(
        "position_close symbol=%s volume=%s type=%s position=%s",
        symbol,
        volume,
        otype,
        getattr(target, "ticket", ""),
    )

    result_send, err_send = _safe_call(_mt5.order_send, "order_send", request)
    if err_send:
        return {"error": f"close send failed: {err_send}"}

    deal = result_send
    if deal is None:
        return {"error": "order_send returned None"}

    retcode = int(getattr(deal, "retcode", 0))
    deal_ticket = getattr(deal, "deal", 0)
    deal_price = getattr(deal, "price", 0.0)
    deal_volume = getattr(deal, "volume", 0.0)

    if retcode == _mt5.TRADE_RETCODE_DONE:
        return {
            "order": {"id": str(deal_ticket)},
            "orderId": str(deal_ticket),
            "status": "FILLED",
            "retcode": retcode,
            "comment": str(getattr(deal, "comment", "")),
            "filledQuantity": str(float(deal_volume)),
            "filled_volume": str(float(deal_volume)),
            "fillPrice": str(float(deal_price)),
            "averagePrice": str(float(deal_price)),
            "protectionConfirmed": True,
            "protected": True,
        }

    comment = str(getattr(deal, "comment", ""))
    return {
        "status": "REJECTED",
        "retcode": retcode,
        "comment": comment,
        "errorMessage": f"MT5 close rejected: retcode={retcode} comment={comment}",
        "protectionConfirmed": False,
        "protected": False,
    }


def _fastapi_health() -> tuple[int, dict[str, object]]:
    """Return the fail-closed health response for ``GET /health``."""
    with _MT5_LOCK:
        if _mt5 is None:
            return 503, {
                "status": "error",
                "connected": False,
                "server": _EXPECTED_SERVER,
                "error": "MT5 module is unavailable",
            }
        try:
            _mt5_ensure_init()
            account = _mt5.account_info()
            terminal = _mt5.terminal_info()
        except Exception as exc:  # noqa: BLE001
            LOGGER.error("health check failed: %s", type(exc).__name__)
            return 503, {
                "status": "error",
                "connected": False,
                "server": _EXPECTED_SERVER,
                "error": "MT5 terminal state unavailable",
            }

    if account is None or terminal is None:
        return 503, {
            "status": "error",
            "connected": False,
            "server": _EXPECTED_SERVER,
            "error": "MT5 account or terminal state unavailable",
        }

    actual_login = str(getattr(account, "login", ""))
    actual_server = str(getattr(account, "server", ""))
    if actual_login != _EXPECTED_ACCOUNT:
        return 503, {
            "status": "error",
            "connected": False,
            "server": _EXPECTED_SERVER,
            "error": "MT5 account identity mismatch",
        }
    if actual_server != _EXPECTED_SERVER:
        return 503, {
            "status": "error",
            "connected": False,
            "server": actual_server or _EXPECTED_SERVER,
            "error": "MT5 server identity mismatch",
        }
    if not bool(getattr(terminal, "connected", False)):
        return 503, {
            "status": "error",
            "connected": False,
            "server": _EXPECTED_SERVER,
            "error": "MT5 terminal disconnected",
        }
    authorized = bool(getattr(account, "login", 0))
    terminal_ok = bool(getattr(terminal, "connected", False))
    return 200, {
        "status": "ok",
        "connected": True,
        "terminalConnected": terminal_ok,
        "authorized": authorized,
        "accountId": actual_login,
        "server": _EXPECTED_SERVER,
        "serverTime": _now_iso(),
    }


def _dispatch_rpc_payload(payload: object) -> object:
    """Dispatch a single or batch JSON-RPC payload."""
    if isinstance(payload, list):
        if not payload:
            return {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32600, "message": "empty RPC batch"},
            }
        replies = [_dispatch_rpc(item) for item in payload]
        return [reply for reply in replies if reply is not None]
    return _dispatch_rpc(payload)


def _fastapi_account_guard(account_id: str) -> JSONResponse | None:
    """Reject requests for any account other than the configured demo account."""
    if account_id != _EXPECTED_ACCOUNT:
        return JSONResponse(
            status_code=403,
            content={"error": "account identity mismatch"},
        )
    return None


def _fastapi_call(
    handler: Callable[[dict[str, object]], dict[str, object]],
    params: dict[str, object],
) -> JSONResponse:
    """Convert typed handler results into sanitized REST responses."""
    try:
        result = handler(params)
    except MT5OperationError as exc:
        return JSONResponse(
            status_code=503,
            content={
                "error": "MT5 operation failed",
                "status": "UNKNOWN",
                "halt_new_entries": True,
                "phase": exc.phase,
                "diagnostics": exc.diagnostics,
            },
        )
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    except Exception as exc:  # noqa: BLE001
        LOGGER.error("REST operation failed: %s", type(exc).__name__)
        return JSONResponse(
            status_code=503,
            content={
                "error": "MT5 operation failed",
                "status": "UNKNOWN",
                "halt_new_entries": True,
                "phase": "unknown",
            },
        )
    if result.get("error"):
        return JSONResponse(status_code=503, content=result)
    return JSONResponse(status_code=200, content=result)


async def _fastapi_request_object(request: Request) -> dict[str, object] | None:
    """Decode a JSON object without returning provider or parser internals."""
    try:
        payload: object = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


app = FastAPI(title="Exness MT5 Demo Bridge", docs_url=None, redoc_url=None)


@app.get("/api/accounts/{account_id}")
def rest_account(account_id: str) -> JSONResponse:
    guard = _fastapi_account_guard(account_id)
    if guard is not None:
        return guard
    try:
        account = _rpc_account_snapshot({})
    except ValueError as exc:
        return JSONResponse(status_code=503, content={"error": str(exc)})
    return JSONResponse(
        status_code=200,
        content={"account": account, "timestamp": account["timestamp"]},
    )


@app.get("/api/accounts/{account_id}/quotes/{symbol}")
def rest_quote(account_id: str, symbol: str) -> JSONResponse:
    guard = _fastapi_account_guard(account_id)
    if guard is not None:
        return guard
    return _fastapi_call(_rpc_symbol_info_tick, {"symbol": symbol})


@app.get("/api/accounts/{account_id}/instruments/{symbol}")
def rest_instrument(account_id: str, symbol: str) -> JSONResponse:
    guard = _fastapi_account_guard(account_id)
    if guard is not None:
        return guard
    return _fastapi_call(_rpc_instrument_metadata, {"symbol": symbol})


@app.get("/api/accounts/{account_id}/instruments/{symbol}/session")
def rest_session(account_id: str, symbol: str) -> JSONResponse:
    guard = _fastapi_account_guard(account_id)
    if guard is not None:
        return guard
    return _fastapi_call(_rpc_instrument_session, {"symbol": symbol})


@app.get("/api/accounts/{account_id}/candles")
def rest_candles(
    account_id: str,
    instrument: str = "",
    timeframe: str = "15m",
    limit: int = 100,
) -> JSONResponse:
    guard = _fastapi_account_guard(account_id)
    if guard is not None:
        return guard
    return _fastapi_call(
        _rpc_candles_get,
        {"symbol": instrument, "timeframe": timeframe, "limit": limit},
    )


@app.post("/api/accounts/{account_id}/orders")
async def rest_order(account_id: str, request: Request) -> JSONResponse:
    guard = _fastapi_account_guard(account_id)
    if guard is not None:
        return guard
    payload = await _fastapi_request_object(request)
    if payload is None:
        return JSONResponse(status_code=400, content={"error": "invalid JSON object"})
    return _fastapi_call(_rpc_order_send, payload)


@app.get("/api/accounts/{account_id}/orders/{client_order_id}")
def rest_order_status(account_id: str, client_order_id: str) -> JSONResponse:
    guard = _fastapi_account_guard(account_id)
    if guard is not None:
        return guard
    return _fastapi_call(
        _rpc_order_status,
        {"client_order_id": client_order_id},
    )


@app.post("/api/accounts/{account_id}/orders/preflight")
async def rest_order_preflight(account_id: str, request: Request) -> JSONResponse:
    guard = _fastapi_account_guard(account_id)
    if guard is not None:
        return guard
    payload = await _fastapi_request_object(request)
    if payload is None:
        return JSONResponse(status_code=400, content={"error": "invalid JSON object"})
    return _fastapi_call(_rpc_order_preflight, payload)


@app.post("/api/accounts/{account_id}/positions/{position_id}/close")
async def rest_close(
    account_id: str, position_id: str, request: Request
) -> JSONResponse:
    guard = _fastapi_account_guard(account_id)
    if guard is not None:
        return guard
    payload = await _fastapi_request_object(request)
    if payload is None:
        return JSONResponse(status_code=400, content={"error": "invalid JSON object"})
    payload.setdefault("position", position_id)
    return _fastapi_call(_rpc_order_close, payload)


@app.get("/health")
def health() -> JSONResponse:
    """Expose only healthy, authorized demo-terminal state as HTTP 200."""
    status_code, payload = _fastapi_health()
    return JSONResponse(status_code=status_code, content=payload)


@app.post("/rpc", response_model=None)
async def rpc(request: Request) -> JSONResponse | Response:
    """Handle JSON-RPC requests without hiding execution failures."""
    try:
        payload: object = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return JSONResponse(
            status_code=400,
            content={
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "invalid JSON"},
            },
        )

    response = _dispatch_rpc_payload(payload)
    if response is None:
        return Response(status_code=204)
    return JSONResponse(status_code=200, content=response)


# ── legacy stdlib server (kept for REST compatibility) ──────────────────────


class _ThreadedHTTPServer(HTTPServer):
    """HTTPServer that handles each request in a new thread."""

    daemon_threads = True

    def process_request(
        self,
        request: socket.socket | tuple[bytes, socket.socket],
        client_address: tuple[str, int],
    ) -> None:
        thread = threading.Thread(
            target=self.process_request_thread,
            args=(request, client_address),
            daemon=True,
        )
        thread.start()

    def process_request_thread(
        self,
        request: socket.socket | tuple[bytes, socket.socket],
        client_address: tuple[str, int],
    ) -> None:
        try:
            self.finish_request(request, client_address)
        except Exception:  # noqa: BLE001
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)


# ── self-signed cert generation ──────────────────────────────────────────────


def _generate_self_signed(cert_path: str, key_path: str) -> None:
    """Generate a local self-signed certificate with OpenSSL."""
    import subprocess

    openssl = shutil.which("openssl") or shutil.which("openssl.exe")
    if openssl is None:
        common_path = r"C:\Program Files\Git\usr\bin\openssl.exe"
        if os.path.isfile(common_path):
            openssl = common_path
    if openssl is None:
        raise RuntimeError(
            "OpenSSL is required to generate a certificate; "
            "provide --certfile and --keyfile instead"
        )

    LOGGER.info("Generating self-signed TLS certificate at %s", cert_path)
    try:
        subprocess.run(
            [
                openssl,
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-keyout",
                key_path,
                "-out",
                cert_path,
                "-days",
                "365",
                "-nodes",
                "-subj",
                "/CN=localhost/O=MT5Bridge/C=US",
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("OpenSSL failed to generate the certificate") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("OpenSSL timed out generating the certificate") from exc

    LOGGER.info("Self-signed TLS certificate generated")


# ── CLI entry point ──────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Exness MT5 native host bridge – HTTPS JSON-RPC server",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=_DEFAULT_PORT,
        help=f"listen port (default {_DEFAULT_PORT})",
    )
    parser.add_argument(
        "--certfile",
        type=str,
        default=None,
        help="TLS certificate PEM file path (or MT5_BRIDGE_CERT env var)",
    )
    parser.add_argument(
        "--keyfile",
        type=str,
        default=None,
        help="TLS private key PEM file path (or MT5_BRIDGE_KEY env var)",
    )
    parser.add_argument(
        "--generate-self-signed",
        action="store_true",
        help="Generate a deterministic self-signed cert/key at the given paths",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    # The operator command intentionally needs no certificate flags. Generate
    # a local certificate on first start; explicit partial configuration still
    # fails closed rather than silently replacing one operator-supplied file.
    certfile = args.certfile or os.environ.get("MT5_BRIDGE_CERT")
    keyfile = args.keyfile or os.environ.get("MT5_BRIDGE_KEY")
    if not certfile and not keyfile:
        certfile, keyfile = "mt5_bridge_cert.pem", "mt5_bridge_key.pem"
        if not (os.path.isfile(certfile) and os.path.isfile(keyfile)):
            try:
                _generate_self_signed(certfile, keyfile)
            except (OSError, RuntimeError) as exc:
                LOGGER.error("TLS certificate setup failed: %s", exc)
                return 1
    elif not certfile or not keyfile:
        LOGGER.error("Both TLS certificate and private key are required")
        return 1

    if not os.path.isfile(certfile):
        LOGGER.error("Certificate file not found: %s", certfile)
        return 1
    if not os.path.isfile(keyfile):
        LOGGER.error("Key file not found: %s", keyfile)
        return 1

    # ── MT5 credentials ──────────────────────────────────────────────────
    login_raw = os.environ.get("EXNESS_LOGIN")
    password = os.environ.get("EXNESS_PASSWORD")
    server = os.environ.get("EXNESS_SERVER")

    if not login_raw:
        LOGGER.error("EXNESS_LOGIN environment variable is required")
        return 1
    if not password:
        LOGGER.error("EXNESS_PASSWORD environment variable is required")
        return 1
    if not server:
        LOGGER.error("EXNESS_SERVER environment variable is required")
        return 1

    try:
        login = int(login_raw)
    except ValueError:
        LOGGER.error("EXNESS_LOGIN must be a valid integer")
        return 1
    if login <= 0:
        LOGGER.error("EXNESS_LOGIN must be positive")
        return 1

    # ── MT5 initialization ───────────────────────────────────────────────
    try:
        _mt5_init(login=login, password=password, server=server)
    except Exception as exc:  # noqa: BLE001
        LOGGER.error("MT5 initialization failed: %s", exc)
        _shutdown_mt5()
        return 1

    # Uvicorn owns the HTTPS listener and graceful signal handling.
    LOGGER.info(
        "MT5 bridge listening on https://%s:%d (TLS enabled, env=%s)",
        _LISTEN_HOST,
        args.port,
        _ENVIRONMENT,
    )
    LOGGER.info("MT5 bridge ready for demo account; SL/TP mandatory")
    try:
        uvicorn.run(
            app,
            host=_LISTEN_HOST,
            port=args.port,
            ssl_keyfile=keyfile,
            ssl_certfile=certfile,
            log_level="info",
        )
    except (KeyboardInterrupt, OSError, ssl.SSLError) as exc:
        LOGGER.error("MT5 bridge server stopped: %s", type(exc).__name__)
        return 1
    finally:
        LOGGER.info("Shutting down MT5 bridge...")
        _shutdown_mt5()

    return 0


def _shutdown_mt5() -> None:
    """Best-effort MT5 terminal shutdown."""
    if _mt5 is not None:
        try:
            _mt5.shutdown()
            LOGGER.info("MT5 terminal disconnected")
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    raise SystemExit(main())
