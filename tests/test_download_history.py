"""Tests for scripts/download_history.py planning logic (offline only).

The download itself needs the network and an exchange, so these tests cover
only the pure planning helpers: timeframe flooring, day parsing, and
CLI-to-request resolution. Nothing here contacts the network.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "download_history.py"


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "download_history_script", SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script():
    return _load_script()


@pytest.fixture()
def settings():
    from trading_assistant.config import Settings

    return Settings()


def test_floor_to_timeframe_truncates_intraday(script) -> None:
    instant = datetime(2024, 5, 6, 12, 34, 56, tzinfo=UTC)
    assert script.floor_to_timeframe(instant, "1h") == datetime(
        2024, 5, 6, 12, 0, 0, tzinfo=UTC
    )
    assert script.floor_to_timeframe(instant, "15m") == datetime(
        2024, 5, 6, 12, 30, 0, tzinfo=UTC
    )


def test_floor_to_timeframe_leaves_aligned_instants_unchanged(script) -> None:
    instant = datetime(2024, 5, 6, 0, 0, 0, tzinfo=UTC)
    assert script.floor_to_timeframe(instant, "1d") == instant
    assert script.floor_to_timeframe(instant, "1h") == instant


def test_floor_to_timeframe_rejects_naive_datetimes(script) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        script.floor_to_timeframe(datetime(2024, 5, 6, 12, 0, 0), "1h")


def test_parse_day_accepts_calendar_days_as_utc_midnight(script) -> None:
    assert script.parse_day("2024-01-15", field_name="--start") == datetime(
        2024, 1, 15, 0, 0, 0, tzinfo=UTC
    )


def test_parse_day_rejects_anything_else(script) -> None:
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        script.parse_day("15/01/2024", field_name="--start")
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        script.parse_day("2024-13-01", field_name="--start")


def test_plan_defaults_to_configured_timeframe(script, settings) -> None:
    now = datetime(2024, 5, 6, 12, 34, 56, tzinfo=UTC)
    (request,) = script.build_download_plan(
        settings=settings,
        timeframe=None,
        all_timeframes=False,
        days=7,
        start=None,
        end=None,
        now=now,
    )
    assert request.timeframe == settings.default_timeframe
    assert request.start_time == datetime(2024, 4, 29, 12, 0, 0, tzinfo=UTC)
    assert request.end_time is None


def test_plan_all_timeframes_covers_supported_timeframes(script, settings) -> None:
    now = datetime(2024, 5, 6, 12, 0, 0, tzinfo=UTC)
    plan = script.build_download_plan(
        settings=settings,
        timeframe=None,
        all_timeframes=True,
        days=7,
        start=None,
        end=None,
        now=now,
    )
    assert [r.timeframe for r in plan] == list(settings.supported_timeframes)
    assert all(r.start_time.tzinfo is not None for r in plan)


def test_plan_honours_explicit_start_and_end_days(script, settings) -> None:
    now = datetime(2024, 5, 6, 12, 0, 0, tzinfo=UTC)
    (request,) = script.build_download_plan(
        settings=settings,
        timeframe="15m",
        all_timeframes=False,
        days=script.DEFAULT_DAYS,
        start="2024-01-01",
        end="2024-02-01",
        now=now,
    )
    assert request.timeframe == "15m"
    assert request.start_time == datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    assert request.end_time == datetime(2024, 2, 1, 0, 0, 0, tzinfo=UTC)


def test_plan_rejects_conflicting_or_invalid_options(script, settings) -> None:
    now = datetime(2024, 5, 6, 12, 0, 0, tzinfo=UTC)
    with pytest.raises(ValueError, match="mutually exclusive"):
        script.build_download_plan(
            settings=settings,
            timeframe="1h",
            all_timeframes=True,
            days=7,
            start=None,
            end=None,
            now=now,
        )
    with pytest.raises(ValueError, match="positive integer"):
        script.build_download_plan(
            settings=settings,
            timeframe=None,
            all_timeframes=False,
            days=0,
            start=None,
            end=None,
            now=now,
        )
    with pytest.raises(ValueError, match="mutually exclusive"):
        script.build_download_plan(
            settings=settings,
            timeframe=None,
            all_timeframes=False,
            days=7,
            start="2024-01-01",
            end=None,
            now=now,
        )
    with pytest.raises(ValueError, match="not in configured"):
        script.build_download_plan(
            settings=settings,
            timeframe="3h",
            all_timeframes=False,
            days=7,
            start=None,
            end=None,
            now=now,
        )
    with pytest.raises(ValueError, match="after end"):
        script.build_download_plan(
            settings=settings,
            timeframe="1h",
            all_timeframes=False,
            days=script.DEFAULT_DAYS,
            start="2024-02-01",
            end="2024-01-01",
            now=now,
        )


def test_plan_start_is_always_timeframe_aligned(script, settings) -> None:
    from trading_assistant.market_data.timeframes import (
        datetime_to_milliseconds,
        is_timeframe_aligned,
    )

    now = datetime(2024, 5, 6, 12, 34, 56, 789000, tzinfo=UTC)
    plan = script.build_download_plan(
        settings=settings,
        timeframe=None,
        all_timeframes=True,
        days=7,
        start=None,
        end=None,
        now=now,
    )
    for request in plan:
        start_ms = datetime_to_milliseconds(request.start_time)
        assert is_timeframe_aligned(start_ms, request.timeframe)
