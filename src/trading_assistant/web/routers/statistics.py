"""Step 8 statistics endpoints: one report or rolling window comparisons."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Query, Request

from trading_assistant.statistics.analysis import (
    GROUPABLE_DIMENSIONS,
    StatisticsFilters,
)
from trading_assistant.web.dashboard_service import DashboardError
from trading_assistant.web.schemas import parse_utc_iso

router = APIRouter(prefix="/api/statistics", tags=["statistics"])

MAX_ROLLING_PERIODS = 24


def _parse_filters(raw_filters: dict[str, list[str]]) -> StatisticsFilters | None:
    if not raw_filters:
        return None
    criteria: dict[str, tuple[str, ...]] = {}
    for dimension, values in raw_filters.items():
        if dimension not in GROUPABLE_DIMENSIONS:
            raise DashboardError(
                "unsupported_filter_dimension",
                f"filter dimension {dimension!r} is not supported; available: "
                + ", ".join(sorted(GROUPABLE_DIMENSIONS)),
            )
        criteria[dimension] = tuple(values)
    return StatisticsFilters.from_mapping(criteria)


def _parse_group_by(group_by: str | None) -> tuple[str, ...] | None:
    if group_by is None or not group_by.strip():
        return None
    dimensions = tuple(part.strip() for part in group_by.split(",") if part.strip())
    for dimension in dimensions:
        if dimension not in GROUPABLE_DIMENSIONS:
            raise DashboardError(
                "unsupported_group_dimension",
                f"group_by dimension {dimension!r} is not supported; available: "
                + ", ".join(sorted(GROUPABLE_DIMENSIONS)),
            )
    return dimensions


@router.get("")
def get_statistics(
    request: Request,
    as_of: str | None = Query(default=None, min_length=20, max_length=40),
    window_start: str | None = Query(default=None, min_length=20, max_length=40),
    group_by: str | None = Query(default=None, max_length=256),
    allow_mixed_versions: bool = Query(default=False),
) -> dict[str, object]:
    state = request.app.state.services
    parsed_as_of = (
        parse_utc_iso(as_of, field_name="as_of") if as_of is not None else state.now()
    )
    parsed_window_start = (
        parse_utc_iso(window_start, field_name="window_start")
        if window_start is not None
        else None
    )
    raw_filters: dict[str, list[str]] = {}
    for key, values in request.query_params.multi_items():
        if key.startswith("filter:"):
            raw_filters.setdefault(key[len("filter:") :], []).append(values)
    report = state.statistics.analyze(
        as_of=parsed_as_of,
        window_start=parsed_window_start,
        filters=_parse_filters(raw_filters),
        group_by=_parse_group_by(group_by),
        allow_mixed_versions=allow_mixed_versions,
    )
    return report.to_json_dict()


@router.get("/rolling")
def get_rolling_statistics(
    request: Request,
    lookback_days: int = Query(default=30, ge=1, le=366),
    periods: int = Query(default=6, ge=1, le=MAX_ROLLING_PERIODS),
    as_of: str | None = Query(default=None, min_length=20, max_length=40),
    group_by: str | None = Query(default=None, max_length=256),
    allow_mixed_versions: bool = Query(default=False),
) -> dict[str, object]:
    state = request.app.state.services
    parsed_as_of = (
        parse_utc_iso(as_of, field_name="as_of") if as_of is not None else state.now()
    )
    lookback = timedelta(days=lookback_days)
    cutoffs = [parsed_as_of - lookback * index for index in range(periods)][::-1]
    reports = state.statistics.rolling_reports(
        cutoffs=cutoffs,
        lookback=lookback,
        group_by=_parse_group_by(group_by),
        allow_mixed_versions=allow_mixed_versions,
    )
    return {
        "lookback_days": lookback_days,
        "periods": periods,
        "reports": [report.to_json_dict() for report in reports],
    }
