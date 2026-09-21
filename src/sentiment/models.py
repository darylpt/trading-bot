"""Validated sentiment analysis results."""

from __future__ import annotations

import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SentimentAnalysisResult(BaseModel):
    """Provider-neutral, strategy-safe sentiment output."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sentiment_score: Annotated[float, Field(ge=-1.0, le=1.0)] | None = None
    confidence_score: Annotated[float, Field(ge=0.0, le=1.0)] = 0.0
    reasoning: str = Field(min_length=1)
    risk_modifier: Annotated[float, Field(gt=0.0, le=1.0)] = 1.0

    @field_validator("sentiment_score", "confidence_score", "risk_modifier")
    @classmethod
    def require_finite_values(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("sentiment numeric values must be finite")
        return value

    @property
    def decision(self) -> Literal["CONFIRM", "REJECT", "ADJUST_RISK"]:
        """Expose the derived decision without trusting model text labels."""
        if self.sentiment_score is None or self.sentiment_score <= 0.5:
            return "REJECT"
        if self.risk_modifier < 1.0:
            return "ADJUST_RISK"
        return "CONFIRM"
