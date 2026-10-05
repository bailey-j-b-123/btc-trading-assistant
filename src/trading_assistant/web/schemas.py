"""Validated request/response contracts for the web API boundary.

These schemas only validate what callers send; every market, qualification,
plan, journal, statistics, and explanation payload is produced by the existing
Step 2–9 canonical projections (``to_json_dict``), never re-shaped here.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, Field, field_validator

_ISO_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$"
)


class DecisionValue(str, Enum):
    """The only decision values the UI may submit; PENDING is never a button."""

    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    SKIPPED = "SKIPPED"


def parse_utc_iso(value: str, *, field_name: str) -> datetime:
    if not isinstance(value, str) or not _ISO_PATTERN.match(value.strip()):
        raise ValueError(f"{field_name} must be an ISO-8601 UTC datetime string")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} must carry an explicit UTC offset")
    return parsed.astimezone()


class DashboardDecisionRequest(BaseModel):
    """Record Bailey's decision about the exact proposal shown at ``as_of``.

    ``as_of`` pins the deterministic Step 5 snapshot the decision applies to;
    the backend re-evaluates that exact instant, so the decision can never
    silently apply to a newer market state. There is deliberately no default
    for ``decision``.
    """

    decision: DecisionValue
    as_of: str = Field(min_length=20, max_length=40)
    symbol: str | None = Field(default=None, min_length=1, max_length=64)
    timeframe: str | None = Field(default=None, min_length=1, max_length=8)
    setup_id: str = Field(min_length=1, max_length=128)
    reason: str | None = Field(default=None, max_length=2000)

    @field_validator("as_of")
    @classmethod
    def validate_as_of(cls, value: str) -> str:
        parse_utc_iso(value, field_name="as_of")
        return value

    @field_validator("symbol", "timeframe", "setup_id")
    @classmethod
    def validate_non_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("value must not be blank")
        return value


class JournalDecisionRequest(BaseModel):
    """Append/correct a decision on one existing immutable journal record."""

    decision: DecisionValue
    reason: str | None = Field(default=None, max_length=2000)


class ObservationRequest(BaseModel):
    """Re-observe one journaled PLANNABLE plan at an explicit cutoff."""

    observed_through: Annotated[str, Field(min_length=20, max_length=40)] | None = None

    @field_validator("observed_through")
    @classmethod
    def validate_observed_through(cls, value: str | None) -> str | None:
        if value is not None:
            parse_utc_iso(value, field_name="observed_through")
        return value
