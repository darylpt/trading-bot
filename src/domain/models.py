"""Validated contracts shared across the trading pipeline."""

from __future__ import annotations

import math
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Direction = Literal["LONG", "SHORT"]
Impact = Literal["LOW", "MEDIUM", "HIGH"]


def _finite_decimal(value: Decimal) -> Decimal:
    if not value.is_finite():
        raise ValueError("value must be finite")
    return value


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MarketCandle(StrictModel):
    instrument: str = Field(min_length=1)
    timeframe: Literal["15m", "1h"]
    timestamp: datetime
    open: Decimal = Field(gt=0)
    high: Decimal = Field(gt=0)
    low: Decimal = Field(gt=0)
    close: Decimal = Field(gt=0)
    volume: Decimal | None = Field(default=None, ge=0)

    @field_validator("open", "high", "low", "close", "volume")
    @classmethod
    def finite_prices(cls, value: Decimal | None) -> Decimal | None:
        return None if value is None else _finite_decimal(value)

    @model_validator(mode="after")
    def validate_ohlc(self) -> MarketCandle:
        if self.high < max(self.open, self.close):
            raise ValueError("high must be at least open and close")
        if self.low > min(self.open, self.close):
            raise ValueError("low must be at most open and close")
        return self


class TechnicalSignal(StrictModel):
    instrument: str = Field(min_length=1)
    direction: Direction
    signal_timestamp: datetime
    reference_price: Decimal = Field(gt=0)
    rsi: Decimal = Field(ge=0, le=100)
    moving_average_fast: Decimal = Field(gt=0)
    moving_average_slow: Decimal = Field(gt=0)
    source: Literal["technical_engine"] = "technical_engine"

    @field_validator(
        "reference_price", "rsi", "moving_average_fast", "moving_average_slow"
    )
    @classmethod
    def finite_values(cls, value: Decimal) -> Decimal:
        return _finite_decimal(value)


class NewsEvent(StrictModel):
    event_id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    headline: str = Field(min_length=1)
    published_at: datetime
    instrument: str | None = None
    currency: str | None = None
    impact: Impact | None = None
    retrieved_at: datetime


class LLMNewsItem(StrictModel):
    event_id: str = Field(min_length=1)
    headline: str = Field(min_length=1)
    published_at: datetime
    instrument: str | None = None
    currency: str | None = None


class LLMSentimentRequest(StrictModel):
    request_id: str = Field(min_length=1)
    model: Literal["gpt-4o-mini", "ollama"]
    analyzed_at: datetime
    news: list[LLMNewsItem]
    response_format: Literal["json_object"] = "json_object"


class LLMSentimentResponse(StrictModel):
    sentiment_score: Annotated[float, Field(ge=-1.0, le=1.0)]

    @field_validator("sentiment_score")
    @classmethod
    def finite_score(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("sentiment score must be finite")
        return value


class SentimentResult(StrictModel):
    request_id: str = Field(min_length=1)
    sentiment_score: float | None = Field(default=None, ge=-1.0, le=1.0)
    analyzed_at: datetime
    source: Literal["openai", "ollama"]
    news_event_ids: list[str]
    validation_status: Literal["VALID", "REJECTED"]
    error_type: str | None = None

    @field_validator("sentiment_score")
    @classmethod
    def finite_result_score(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("sentiment score must be finite")
        return value


class AccountSnapshot(StrictModel):
    account_id: str = Field(min_length=1)
    equity: Decimal = Field(gt=0)
    balance: Decimal = Field(gt=0)
    captured_at: datetime
    environment: Literal["PAPER", "DEMO"]

    @field_validator("equity", "balance")
    @classmethod
    def finite_account_values(cls, value: Decimal) -> Decimal:
        return _finite_decimal(value)


class DailyDrawdownState(StrictModel):
    trading_day: date
    baseline_equity: Decimal = Field(gt=0)
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    drawdown_limit: Decimal = Field(gt=0, le=Decimal("1"))
    halt_active: bool = False
    halted_at: datetime | None = None
    halt_reason: str | None = None

    @field_validator(
        "baseline_equity", "realized_pnl", "unrealized_pnl", "drawdown_limit"
    )
    @classmethod
    def finite_drawdown_values(cls, value: Decimal) -> Decimal:
        return _finite_decimal(value)


class ExitPayload(StrictModel):
    price: Decimal = Field(gt=0)

    @field_validator("price")
    @classmethod
    def finite_exit(cls, value: Decimal) -> Decimal:
        return _finite_decimal(value)


class OrderIntent(StrictModel):
    client_order_id: str = Field(min_length=1)
    instrument: str = Field(min_length=1)
    direction: Direction
    quantity: Decimal = Field(gt=0)
    entry_price: Decimal = Field(gt=0)
    stop_loss_price: Decimal = Field(gt=0)
    take_profit_price: Decimal = Field(gt=0)
    account_equity: Decimal = Field(gt=0)
    risk_fraction: Decimal = Field(gt=0, le=Decimal("0.01"))
    signal_timestamp: datetime
    sentiment_score: Decimal | None = Field(
        default=None, ge=Decimal("-1"), le=Decimal("1")
    )

    @field_validator(
        "quantity",
        "entry_price",
        "stop_loss_price",
        "take_profit_price",
        "account_equity",
        "risk_fraction",
        "sentiment_score",
    )
    @classmethod
    def finite_order_values(cls, value: Decimal | None) -> Decimal | None:
        return None if value is None else _finite_decimal(value)

    @model_validator(mode="after")
    def validate_directional_exits(self) -> OrderIntent:
        if self.direction == "LONG" and not (
            self.stop_loss_price < self.entry_price < self.take_profit_price
        ):
            raise ValueError("LONG exits must bracket entry directionally")
        if self.direction == "SHORT" and not (
            self.take_profit_price < self.entry_price < self.stop_loss_price
        ):
            raise ValueError("SHORT exits must bracket entry directionally")
        return self


class BrokerOrderPayload(StrictModel):
    client_order_id: str = Field(min_length=1)
    instrument: str = Field(min_length=1)
    direction: Direction
    quantity: Decimal = Field(gt=0)
    entry_price: Decimal = Field(gt=0)
    stop_loss: ExitPayload
    take_profit: ExitPayload
    account_equity: Decimal = Field(gt=0)
    risk_fraction: Decimal = Field(gt=0, le=Decimal("0.01"))
    environment: Literal["PAPER", "DEMO"]


class ExecutionResult(StrictModel):
    client_order_id: str = Field(min_length=1)
    provider_order_id: str | None = None
    status: Literal[
        "ACCEPTED",
        "FILLED",
        "PARTIALLY_FILLED",
        "REJECTED",
        "CANCELLED",
        "EXPIRED",
        "UNKNOWN",
    ]
    filled_quantity: Decimal | None = Field(default=None, gt=0)
    fill_price: Decimal | None = Field(default=None, gt=0)
    slippage: Decimal | None = None
    latency_ms: int | None = Field(default=None, ge=0)
    protection_confirmed: bool | None = None
    rejection_reason: str | None = None
    environment: Literal["PAPER", "DEMO"]
