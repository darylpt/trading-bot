"""Validated sentiment analysis results."""

from __future__ import annotations

import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SentimentAnalysisResult(BaseModel):
    """Provider-neutral, strategy-safe sentiment output."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: Literal["CONFIRM", "REJECT", "ADJUST_RISK"]
    confidence_score: Annotated[float, Field(ge=0.0, le=1.0)]
    reasoning: str = Field(min_length=1)
    risk_modifier: Annotated[float, Field(gt=0.0, le=1.0)] = 1.0

    @field_validator("confidence_score", "risk_modifier")
    @classmethod
    def require_finite_values(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("sentiment numeric values must be finite")
        return value
