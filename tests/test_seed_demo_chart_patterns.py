"""The Chart Patterns review series only supplies candles; every pattern on the
screen must come from BRAIN's unchanged detectors reading them.

These tests feed the series through the same path the dashboard uses (the last
CHART_EVIDENCE_WINDOW closed candles -> analyze_patterns -> build_chart_evidence)
and check what the detectors genuinely record. DEMONSTRATION DATA only.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

from trading_assistant.pattern_liquidity.analysis import analyze_patterns
from trading_assistant.web.chart_evidence import CHART_EVIDENCE_WINDOW, build_chart_evidence

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "seed_demo_chart_patterns.py"
HOUR = timedelta(hours=1)
LAST_CLOSED = datetime(2026, 10, 13, 22, tzinfo=UTC)


def _seed_module():
    spec = importlib.util.spec_from_file_location("seed_demo_chart_patterns", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _evidence(candles, as_of):
    window = tuple(c for c in candles if c.timestamp + HOUR <= as_of)[-CHART_EVIDENCE_WINDOW:]
    snapshot = analyze_patterns(window, exchange="binance", symbol="BTC/USDT", timeframe="1h", as_of=as_of)
    return build_chart_evidence(
        exchange="binance", symbol="BTC/USDT", timeframe="1h", as_of=as_of,
        snapshot=snapshot, candles=window, interval=HOUR,
    )


def test_series_is_deterministic_and_only_candles():
    seed = _seed_module()
    first = seed.build_candles(LAST_CLOSED)
    assert first == seed.build_candles(LAST_CLOSED)
    assert len(first) == 600
    assert first[-1].timestamp == LAST_CLOSED
    assert all(b.timestamp - a.timestamp == HOUR for a, b in zip(first, first[1:]))
    assert all(c.low <= min(c.open, c.close) and c.high >= max(c.open, c.close) for c in first)


def test_detectors_find_each_supported_type_in_the_review_series():
    candles = _seed_module().build_candles(LAST_CLOSED)
    payload = _evidence(candles, LAST_CLOSED + HOUR)
    assert payload["available"] is True
    current = {(p["type"], p["state"]) for p in payload["patterns"] if p["is_latest_state"]}
    assert ("head_and_shoulders", "confirmed") in current
    assert ("inverse_head_and_shoulders", "confirmed") in current
    assert {kind for kind, _ in current} >= {"double_top", "double_bottom"}
    # Only the four classical types the detector implements ever appear.
    assert {p["type"] for p in payload["patterns"]} <= {
        "double_top", "double_bottom", "head_and_shoulders", "inverse_head_and_shoulders",
    }
    assert payload["range"] is not None and payload["range"]["active"] is True
    for pattern in payload["patterns"]:
        assert not any("confidence" in key or "probability" in key for key in pattern)


def test_no_future_knowledge_before_the_head_and_shoulders_confirms():
    candles = _seed_module().build_candles(LAST_CLOSED)
    payload = _evidence(candles, LAST_CLOSED + HOUR)
    (confirmed,) = (
        p for p in payload["patterns"]
        if p["type"] == "head_and_shoulders" and p["state"] == "confirmed"
    )
    known_at = datetime.fromisoformat(confirmed["known_at"].replace("Z", "+00:00"))
    earlier = _evidence(candles, known_at - HOUR)
    assert not any(
        p["pattern_id"] == confirmed["pattern_id"] and p["state"] == "confirmed"
        for p in earlier["patterns"]
    )
    assert all(
        datetime.fromisoformat(p["known_at"].replace("Z", "+00:00")) <= known_at - HOUR
        for p in earlier["patterns"]
    )
