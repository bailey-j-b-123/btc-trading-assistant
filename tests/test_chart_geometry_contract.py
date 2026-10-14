"""Contract tests for chart-geometry-v1.

Every candle in this module is an offline synthetic fixture. These tests cover
causal closed-history evaluation, gap refusal, frozen geometry, terminal
lifecycle transitions, exact scoped identity, analysis-only projection, and
package isolation. They do not fabricate trading decisions or outcomes.
"""

from __future__ import annotations

import ast
import copy
import datetime as dt
import decimal
import json
import re
import subprocess
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any

from test_chart_geometry_fixtures import (
    ASCENDING_PIVOTS,
    ASCENDING_TAIL,
    CUP_PIVOTS,
    CUP_TAIL,
    EPOCH,
    EXCHANGE,
    INTERVAL,
    SYMBOL,
    SYMMETRICAL_APEX_TAIL,
    SYMMETRICAL_PIVOTS,
    TIMEFRAME,
    close_time,
    detect,
    synthetic_series,
    with_gap,
)
from trading_assistant.chart_geometry import (
    CHART_GEOMETRY_FAMILY_COUNT,
    CHART_GEOMETRY_FINGERPRINT,
    CHART_GEOMETRY_VERSION,
    ChartGeometryDiagnostics,
    ChartGeometryFamily,
    ChartGeometryParameters,
    ChartGeometrySnapshot,
    detect_chart_geometry,
)
from trading_assistant.chart_geometry.identity import formation_identity
from trading_assistant.market_data.types import Candle
from trading_assistant.market_structure.swings import SwingKind

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = REPO_ROOT / "src" / "trading_assistant" / "chart_geometry"
DEPENDENCY_PREFIXES = (
    "sqlalchemy",
    "alembic",
    "ccxt",
    "fastapi",
    "uvicorn",
    "httpx",
    "requests",
    "urllib",
    "socket",
    "sqlite3",
    "trading_assistant.database",
    "trading_assistant.web",
    "trading_assistant.pattern_liquidity",
    "trading_assistant.decision",
)
PROJECTED_CLAIM_WORDS = re.compile(
    r"confidence|probabilit|likelihood|forecast|target|profit|win.?rate|signal|score",
    re.IGNORECASE,
)
HEX_64 = re.compile(r"^[0-9a-f]{64}$")


def canonical(snapshot: ChartGeometrySnapshot) -> dict[str, Any]:
    """JSON projection with only the intentionally boundary-dependent count removed."""

    payload = snapshot.to_json_dict()
    payload["diagnostics"].pop("excluded_future_candle_count")
    return payload


def assert_no_binary_float(test: unittest.TestCase, value: Any, path: str = "$") -> None:
    """Reject binary floats anywhere in a projected record."""

    if isinstance(value, float):
        test.fail(f"binary float at {path}: {value!r}")
    if isinstance(value, dict):
        for key, child in value.items():
            assert_no_binary_float(test, child, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            assert_no_binary_float(test, child, f"{path}[{index}]")


def projection_keys(value: Any):
    """Yield all mapping keys in a nested JSON projection."""

    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key)
            yield from projection_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from projection_keys(child)


class TestClosedHistoryOnly(unittest.TestCase):
    """Future candles cannot influence a snapshot at an earlier boundary."""

    def test_future_candles_are_excluded_and_not_used_as_anchors(self):
        candles = synthetic_series(ASCENDING_PIVOTS, tail=ASCENDING_TAIL)
        boundary = close_time(18)
        full_input = detect(candles, as_of=boundary)
        prefix_input = detect(candles[:19], as_of=boundary)

        self.assertEqual(canonical(full_input), canonical(prefix_input))
        self.assertEqual(full_input.diagnostics.closed_candle_count, 19)
        self.assertEqual(full_input.diagnostics.excluded_future_candle_count, len(candles) - 19)
        self.assertEqual([event.state for event in full_input.events], ["formed"])
        for event in full_input.events:
            self.assertLessEqual(event.anchor_candle.timestamp + INTERVAL, boundary)
            self.assertLessEqual(event.known_at, boundary)

    def test_extreme_future_candle_mutation_does_not_change_the_closed_result(self):
        candles = synthetic_series(ASCENDING_PIVOTS, tail=ASCENDING_TAIL)
        boundary = close_time(len(candles) - 1)
        extreme_closes = (
            decimal.Decimal("99999999"),
            decimal.Decimal("0.0001"),
            decimal.Decimal("7"),
        )
        future: list[Candle] = []
        for index, close in enumerate(extreme_closes, start=len(candles)):
            open_ = decimal.Decimal("50")
            high = max(open_, close) + decimal.Decimal("1")
            low = max(
                min(open_, close) - decimal.Decimal("0.00001"),
                decimal.Decimal("0.00001"),
            )
            future.append(
                Candle(
                    exchange=EXCHANGE,
                    symbol=SYMBOL,
                    timeframe=TIMEFRAME,
                    timestamp=EPOCH + INTERVAL * index,
                    open=open_,
                    high=high,
                    low=low,
                    close=close,
                    volume=decimal.Decimal("0"),
                )
            )

        original = detect(candles, as_of=boundary)
        mutated = detect(tuple(candles) + tuple(future), as_of=boundary)

        self.assertEqual(canonical(original), canonical(mutated))
        self.assertEqual(mutated.diagnostics.excluded_future_candle_count, len(future))

    def test_prefix_equivalence_holds_at_every_closed_boundary(self):
        candles = synthetic_series(CUP_PIVOTS, tail=CUP_TAIL)

        for boundary_index in range(len(candles)):
            boundary = close_time(boundary_index)
            prefix = detect(candles[: boundary_index + 1], as_of=boundary)
            full = detect(candles, as_of=boundary)
            with self.subTest(boundary_index=boundary_index):
                self.assertEqual(canonical(full), canonical(prefix))
                self.assertEqual(
                    full.diagnostics.excluded_future_candle_count,
                    len(candles) - boundary_index - 1,
                )

    def test_unconfirmed_last_pivot_is_invisible(self):
        candles = synthetic_series(ASCENDING_PIVOTS, tail=ASCENDING_TAIL)
        snapshot = detect(candles, as_of=close_time(17))

        self.assertEqual(snapshot.formations, ())
        self.assertEqual(snapshot.events, ())
        self.assertEqual(snapshot.diagnostics.confirmed_swing_count, 3)
        self.assertEqual(snapshot.diagnostics.candidate_window_count, 0)

    def test_naive_as_of_is_rejected(self):
        candles = synthetic_series(ASCENDING_PIVOTS, tail=ASCENDING_TAIL)
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            detect(candles, as_of=dt.datetime(2024, 1, 1))


class TestInsufficientAndGappedHistory(unittest.TestCase):
    """History shortfalls and holes are reported, never filled or bridged."""

    def test_empty_and_short_history_report_the_required_minimum(self):
        empty = detect_chart_geometry(
            (), exchange=EXCHANGE, symbol=SYMBOL, timeframe=TIMEFRAME, as_of=EPOCH
        )
        series = synthetic_series(ASCENDING_PIVOTS, tail=ASCENDING_TAIL)
        short = detect(series[:7], as_of=close_time(6))
        exact = detect(series[:8], as_of=close_time(7))

        self.assertEqual(empty.diagnostics.closed_candle_count, 0)
        self.assertTrue(empty.diagnostics.insufficient_history)
        self.assertEqual(short.diagnostics.required_candle_count, 8)
        self.assertTrue(short.diagnostics.insufficient_history)
        self.assertEqual(exact.diagnostics.closed_candle_count, 8)
        self.assertFalse(exact.diagnostics.insufficient_history)
        self.assertEqual(exact.formations, ())

    def test_gap_inside_formation_is_rejected_instead_of_bridged(self):
        tail = (
            ("108", "109.1", "107.95", "109.0"),
            ("109.0", "109.3", "108.0", "109.2"),
        )
        candles = synthetic_series(ASCENDING_PIVOTS, leg=8, tail=tail)
        snapshot = detect(with_gap(candles, drop_index=20), as_of=close_time(34))

        self.assertEqual(
            snapshot.formations_for(ChartGeometryFamily.ASCENDING_TRIANGLE), ()
        )
        self.assertIn(
            "candle_gap_inside_formation",
            {rejection.reason for rejection in snapshot.diagnostics.rejections},
        )

    def test_gap_after_formation_truncates_lifecycle_without_a_transition(self):
        candles = synthetic_series(ASCENDING_PIVOTS, tail=ASCENDING_TAIL)
        snapshot = detect(with_gap(candles, drop_index=19))
        formation = snapshot.formations_for(ChartGeometryFamily.ASCENDING_TRIANGLE)[0]

        self.assertEqual([event.state for event in formation.events], ["formed"])
        self.assertTrue(formation.lifecycle_truncated)
        self.assertEqual(
            formation.lifecycle_truncation_reason,
            "candle_gap_between_formation_and_event_anchor",
        )
        self.assertEqual(snapshot.diagnostics.gap_truncated_count, 1)

    def test_missing_history_before_first_component_does_not_shift_or_bridge_pivots(self):
        candles = synthetic_series(ASCENDING_PIVOTS, tail=ASCENDING_TAIL)
        snapshot = detect(candles[1:])
        formations = snapshot.formations_for(ChartGeometryFamily.ASCENDING_TRIANGLE)

        self.assertEqual(len(formations), 1)
        self.assertEqual(
            [component.timestamp for component in formations[0].components],
            [candles[index].timestamp for index in (4, 8, 12, 16)],
        )


class TestChronologyAndFrozenGeometry(unittest.TestCase):
    """Formation knowledge and later transitions are strictly causal."""

    def test_a_preformation_breakout_cannot_confirm_the_formation(self):
        tail = (
            ("108", "121", "107.95", "120"),  # index 17; closed before formation is knowable
            ASCENDING_TAIL[1],
        )
        candles = synthetic_series(ASCENDING_PIVOTS, tail=tail)
        snapshot = detect(candles, as_of=close_time(18))
        formation = snapshot.formations_for(ChartGeometryFamily.ASCENDING_TRIANGLE)[0]

        self.assertEqual([event.state for event in formation.events], ["formed"])
        self.assertEqual(formation.formed_at, close_time(18))
        self.assertIsNone(formation.observed_direction)

    def test_confirmation_uses_a_later_closed_candle_and_observed_direction(self):
        candles = synthetic_series(ASCENDING_PIVOTS, tail=ASCENDING_TAIL)
        formation = detect(candles).formations_for(ChartGeometryFamily.ASCENDING_TRIANGLE)[0]

        self.assertEqual([event.state for event in formation.events], ["formed", "confirmed"])
        formed, confirmed = formation.events
        self.assertEqual(formed.known_at, close_time(18))
        self.assertEqual(confirmed.anchor_candle, candles[20])
        self.assertEqual(confirmed.known_at, close_time(20))
        self.assertGreater(confirmed.known_at, formation.formed_at)
        self.assertEqual(confirmed.reason, "close_beyond_upper_boundary")
        self.assertEqual(confirmed.observed_direction, "above")
        self.assertEqual(formation.observed_direction, "above")

    def test_components_keep_original_pivot_and_actual_confirmation_times(self):
        candles = synthetic_series(ASCENDING_PIVOTS, tail=ASCENDING_TAIL)
        formation = detect(candles).formations_for(ChartGeometryFamily.ASCENDING_TRIANGLE)[0]
        expected = (
            (4, SwingKind.HIGH),
            (8, SwingKind.LOW),
            (12, SwingKind.HIGH),
            (16, SwingKind.LOW),
        )

        for component, (pivot_index, kind) in zip(formation.components, expected, strict=True):
            with self.subTest(pivot_index=pivot_index):
                self.assertEqual(component.timestamp, candles[pivot_index].timestamp)
                self.assertIs(component.pivot_kind, kind)
                self.assertEqual(component.confirmed_by_timestamp, candles[pivot_index + 2].timestamp)
                self.assertEqual(component.confirmed_at, close_time(pivot_index + 2))
                self.assertEqual(component.swing.timestamp, component.timestamp)
                self.assertEqual(component.swing.confirmed_at, component.confirmed_at)
                self.assertEqual(component.swing.confirmed_by_timestamp, component.confirmed_by_timestamp)

    def test_geometry_and_digest_are_frozen_after_later_candles_arrive(self):
        candles = synthetic_series(ASCENDING_PIVOTS, tail=ASCENDING_TAIL)
        first_seen = detect(candles, as_of=close_time(18)).formations_for(
            ChartGeometryFamily.ASCENDING_TRIANGLE
        )[0]
        later = detect(candles).formations_for(ChartGeometryFamily.ASCENDING_TRIANGLE)[0]

        self.assertEqual(first_seen.formation_id, later.formation_id)
        self.assertEqual(first_seen.components, later.components)
        self.assertEqual(first_seen.geometry, later.geometry)
        self.assertEqual(first_seen.formed_at, later.formed_at)
        self.assertEqual(
            first_seen.events[0].frozen_geometry_digest,
            later.events[0].frozen_geometry_digest,
        )
        self.assertEqual(
            {event.frozen_geometry_digest for event in later.events},
            {later.events[0].frozen_geometry_digest},
        )

    def test_invalidation_at_apex_is_terminal_and_never_becomes_confirmation(self):
        candles = synthetic_series(SYMMETRICAL_PIVOTS, tail=SYMMETRICAL_APEX_TAIL)
        snapshot = detect(candles)
        formation = snapshot.formations_for(ChartGeometryFamily.SYMMETRICAL_TRIANGLE)[0]

        self.assertEqual([event.state for event in formation.events], ["formed", "invalidated"])
        self.assertEqual(formation.state, "invalidated")
        self.assertEqual(formation.terminal_reason, "boundaries_converged_or_crossed")
        self.assertEqual(formation.events[-1].reason, "boundaries_converged_or_crossed")
        self.assertEqual(snapshot.diagnostics.confirmed_count, 0)
        self.assertEqual(snapshot.diagnostics.invalidated_count, 1)
        self.assertEqual(len({event.event_id for event in formation.events}), 2)


class TestIdentityAndExactArithmetic(unittest.TestCase):
    """Ids are stable, scoped and built from exact component geometry."""

    def test_repeated_and_fresh_process_runs_produce_identical_ids(self):
        candles = synthetic_series(ASCENDING_PIVOTS, tail=ASCENDING_TAIL)
        first = detect(candles)
        repeated = detect(candles)
        formation = first.formations_for(ChartGeometryFamily.ASCENDING_TRIANGLE)[0]

        self.assertEqual(first, repeated)
        self.assertEqual(
            [event.event_id for event in first.events],
            [event.event_id for event in repeated.events],
        )
        self.assertRegex(formation.formation_id, HEX_64)
        for event in formation.events:
            self.assertRegex(event.event_id, HEX_64)
            self.assertRegex(event.frozen_geometry_digest, HEX_64)
            self.assertNotEqual(event.event_id, formation.formation_id)

        script = (
            "import json,sys;"
            f"sys.path[:0]=[{str(REPO_ROOT / 'src')!r},{str(REPO_ROOT / 'tests')!r}];"
            "from test_chart_geometry_fixtures import ASCENDING_PIVOTS,ASCENDING_TAIL,synthetic_series;"
            "from trading_assistant.chart_geometry import detect_chart_geometry;"
            "from datetime import timedelta;"
            "c=synthetic_series(ASCENDING_PIVOTS,tail=ASCENDING_TAIL);"
            "s=detect_chart_geometry(c,exchange='binance',symbol='BTC/USDT',timeframe='1h',"
            "as_of=c[-1].timestamp+timedelta(hours=1));"
            "print(json.dumps({'formations':[f.formation_id for f in s.formations],"
            "'events':[e.event_id for e in s.events],"
            "'digests':[e.frozen_geometry_digest for e in s.events]}))"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=REPO_ROOT,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        fresh = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(fresh["formations"], [item.formation_id for item in first.formations])
        self.assertEqual(fresh["events"], [item.event_id for item in first.events])
        self.assertEqual(
            fresh["digests"], [item.frozen_geometry_digest for item in first.events]
        )

    def test_identity_changes_with_scope_parameters_and_actual_component_fields(self):
        candles = synthetic_series(ASCENDING_PIVOTS, tail=ASCENDING_TAIL)
        base_snapshot = detect(candles)
        base = base_snapshot.formations_for(ChartGeometryFamily.ASCENDING_TRIANGLE)[0]

        for kwargs in (
            {"exchange": "kraken"},
            {"symbol": "ETH/USDT"},
            {"parameters": ChartGeometryParameters(breakout_tolerance_pct=decimal.Decimal("0.2"))},
        ):
            scoped_candles = tuple(
                replace(candle, **{key: value})
                for candle in candles
                for key, value in kwargs.items()
                if key in {"exchange", "symbol"}
            ) if any(key in kwargs for key in ("exchange", "symbol")) else candles
            call = {
                "exchange": kwargs.get("exchange", EXCHANGE),
                "symbol": kwargs.get("symbol", SYMBOL),
                "timeframe": TIMEFRAME,
                "as_of": close_time(len(candles) - 1),
                "parameters": kwargs.get("parameters"),
            }
            changed = detect_chart_geometry(scoped_candles, **call)
            changed_formation = changed.formations_for(ChartGeometryFamily.ASCENDING_TRIANGLE)[0]
            self.assertNotEqual(changed_formation.formation_id, base.formation_id)

        two_hour = synthetic_series(
            ASCENDING_PIVOTS,
            tail=ASCENDING_TAIL,
            interval=INTERVAL * 2,
            timeframe="2h",
        )
        changed_timeframe = detect_chart_geometry(
            two_hour,
            exchange=EXCHANGE,
            symbol=SYMBOL,
            timeframe="2h",
            as_of=close_time(len(two_hour) - 1, interval=INTERVAL * 2),
        )
        self.assertNotEqual(
            changed_timeframe.formations_for(ChartGeometryFamily.ASCENDING_TRIANGLE)[0].formation_id,
            base.formation_id,
        )

        altered_pivots = [100, 110, 104, "110.2", 108]
        changed_price = detect(synthetic_series(altered_pivots, tail=ASCENDING_TAIL))
        self.assertNotEqual(
            changed_price.formations_for(ChartGeometryFamily.ASCENDING_TRIANGLE)[0].formation_id,
            base.formation_id,
        )

        shifted = tuple(
            replace(candle, timestamp=candle.timestamp + dt.timedelta(hours=24))
            for candle in candles
        )
        changed_time = detect(shifted)
        self.assertNotEqual(
            changed_time.formations_for(ChartGeometryFamily.ASCENDING_TRIANGLE)[0].formation_id,
            base.formation_id,
        )

        def identity(components):
            return formation_identity(
                exchange=base.exchange,
                symbol=base.symbol,
                timeframe=base.timeframe,
                family=base.family,
                parameters_fingerprint=base.parameters_fingerprint,
                components=components,
            )

        for mutation in (
            replace(base.components[0], pivot_kind=SwingKind.LOW),
            replace(base.components[0], timestamp=base.components[0].timestamp + INTERVAL),
            replace(base.components[0], price=base.components[0].price + decimal.Decimal("0.01")),
        ):
            components = (mutation,) + base.components[1:]
            self.assertNotEqual(identity(components), base.formation_id)

    def test_boundary_lines_reproduce_their_actual_anchor_points(self):
        candles = synthetic_series(ASCENDING_PIVOTS, tail=ASCENDING_TAIL)
        formation = detect(candles).formations_for(ChartGeometryFamily.ASCENDING_TRIANGLE)[0]

        for line in (formation.geometry.upper, formation.geometry.lower):
            self.assertEqual(line.value_at(line.start_time), line.start_price)
            self.assertEqual(line.value_at(line.end_time), line.end_price)
        self.assertEqual(formation.geometry.upper.start_price, formation.components[0].price)
        self.assertEqual(formation.geometry.upper.end_price, formation.components[2].price)
        self.assertEqual(formation.geometry.lower.start_price, formation.components[1].price)
        self.assertEqual(formation.geometry.lower.end_price, formation.components[3].price)

    def test_projection_contains_no_binary_floats_or_invented_claim_fields(self):
        candles = synthetic_series(CUP_PIVOTS, tail=CUP_TAIL)
        snapshot = detect(candles)
        payload = snapshot.to_json_dict()
        assert_no_binary_float(self, payload)

        for key in projection_keys(payload):
            self.assertIsNone(PROJECTED_CLAIM_WORDS.search(key), key)
        serialized = json.dumps(payload, sort_keys=True).lower()
        for term in ("confidence", "probability", "likelihood", "target_price", "neckline", "zone_id"):
            self.assertNotIn(term, serialized)

        for family in ChartGeometryFamily:
            definition = family.definition_text().lower()
            for term in ("probabilit", "likely", "profit", "win rate", "target", "signal", "% chance"):
                self.assertNotIn(term, definition)

    def test_parameter_fingerprint_is_canonical_and_versioned(self):
        first = ChartGeometryParameters()
        equivalent = ChartGeometryParameters(
            min_boundary_gap_pct=decimal.Decimal("0.0500"),
            breakout_tolerance_pct=decimal.Decimal("0.1000"),
        )
        changed = ChartGeometryParameters(breakout_tolerance_pct=decimal.Decimal("0.2"))
        snapshot = detect(synthetic_series(ASCENDING_PIVOTS, tail=ASCENDING_TAIL))

        self.assertEqual(first.fingerprint(), equivalent.fingerprint())
        self.assertNotEqual(first.fingerprint(), changed.fingerprint())
        self.assertRegex(first.fingerprint(), HEX_64)
        self.assertEqual(CHART_GEOMETRY_FINGERPRINT, first.fingerprint())
        self.assertEqual(snapshot.rules_version, CHART_GEOMETRY_VERSION)
        self.assertEqual(snapshot.parameters_fingerprint, first.fingerprint())
        self.assertEqual(len(ChartGeometryFamily), CHART_GEOMETRY_FAMILY_COUNT)
        with self.assertRaises(ValueError):
            ChartGeometryParameters(flag_min_retrace_pct=60)
        with self.assertRaises(ValueError):
            ChartGeometryParameters(min_boundary_gap_pct=0)


class TestInputAndInstrumentSafety(unittest.TestCase):
    """Input records are not changed, and mislabelled inputs are rejected."""

    def test_detection_does_not_mutate_or_replace_input_candles(self):
        candles = synthetic_series(CUP_PIVOTS, tail=CUP_TAIL)
        before = copy.deepcopy(candles)
        snapshot = detect(candles)

        self.assertEqual(candles, before)
        candles_by_time = {candle.timestamp: candle for candle in candles}
        for event in snapshot.events:
            self.assertIs(event.anchor_candle, candles_by_time[event.anchor_candle.timestamp])
        for formation in snapshot.formations:
            for component in formation.components:
                self.assertEqual(component.swing.timestamp, component.timestamp)
                self.assertEqual(component.swing.price, component.price)

    def test_parameters_object_is_not_mutated(self):
        candles = synthetic_series(ASCENDING_PIVOTS, tail=ASCENDING_TAIL)
        parameters = ChartGeometryParameters()
        before = parameters.fingerprint()
        snapshot = detect(candles, parameters=parameters)

        self.assertIs(snapshot.parameters, parameters)
        self.assertEqual(parameters.fingerprint(), before)

    def test_wrong_market_scope_reports_the_offending_candle_index(self):
        candles = synthetic_series(ASCENDING_PIVOTS, tail=ASCENDING_TAIL)
        wrong_exchange = tuple(replace(candle, exchange="kraken") for candle in candles)

        with self.assertRaisesRegex(ValueError, "candle 0"):
            detect_chart_geometry(
                wrong_exchange,
                exchange=EXCHANGE,
                symbol=SYMBOL,
                timeframe=TIMEFRAME,
                as_of=close_time(len(candles) - 1),
            )

        mixed_symbol = list(candles)
        mixed_symbol[3] = replace(mixed_symbol[3], symbol="ETH/USDT")
        with self.assertRaisesRegex(ValueError, "share exchange, symbol, and timeframe"):
            detect(tuple(mixed_symbol))

    def test_invalid_timeframe_ordering_and_spacing_are_rejected(self):
        candles = synthetic_series(ASCENDING_PIVOTS, tail=ASCENDING_TAIL)

        with self.assertRaises(ValueError):
            detect_chart_geometry(
                candles,
                exchange=EXCHANGE,
                symbol=SYMBOL,
                timeframe="1M",
                as_of=close_time(len(candles) - 1),
            )
        with self.assertRaises(ValueError):
            detect(tuple(reversed(candles)))
        with self.assertRaises(ValueError):
            detect(tuple(candles) + (candles[-1],))

        irregular = list(candles)
        irregular[5] = replace(
            irregular[5], timestamp=irregular[5].timestamp + dt.timedelta(minutes=30)
        )
        with self.assertRaisesRegex(ValueError, "whole multiple"):
            detect(tuple(irregular))


class TestDiagnosticsAndPackageIsolation(unittest.TestCase):
    """Diagnostics describe this pass; the package remains analysis-only."""

    def test_diagnostics_and_version_cohort_match_the_detected_formation(self):
        candles = synthetic_series(ASCENDING_PIVOTS, tail=ASCENDING_TAIL)
        snapshot = detect(candles)
        diagnostics = snapshot.diagnostics
        formation = snapshot.formations_for(ChartGeometryFamily.ASCENDING_TRIANGLE)[0]

        self.assertIsInstance(diagnostics, ChartGeometryDiagnostics)
        self.assertEqual(diagnostics.closed_candle_count, len(candles))
        self.assertEqual(diagnostics.excluded_future_candle_count, 0)
        self.assertEqual(diagnostics.required_candle_count, 8)
        self.assertFalse(diagnostics.insufficient_history)
        self.assertEqual(diagnostics.confirmed_swing_count, 4)
        self.assertEqual(diagnostics.candidate_window_count, 1)
        self.assertEqual(diagnostics.accepted_candidate_count, 1)
        self.assertEqual(diagnostics.formation_count, 1)
        self.assertEqual(diagnostics.formed_only_count, 0)
        self.assertEqual(diagnostics.gap_truncated_count, 0)
        self.assertEqual(diagnostics.confirmed_count, 1)
        self.assertEqual(diagnostics.invalidated_count, 0)
        self.assertEqual(diagnostics.rejections, ())
        self.assertEqual(snapshot.rules_version, CHART_GEOMETRY_VERSION)
        self.assertEqual(formation.rules_version, CHART_GEOMETRY_VERSION)
        self.assertEqual(snapshot.parameters_fingerprint, CHART_GEOMETRY_FINGERPRINT)
        self.assertEqual(formation.parameters_fingerprint, snapshot.parameters_fingerprint)
        self.assertEqual(snapshot.to_json_dict()["rules_version"], CHART_GEOMETRY_VERSION)

    def test_package_has_no_forbidden_imports_or_calls(self):
        self.assertTrue(tuple(PACKAGE_DIR.glob("*.py")))
        for path in PACKAGE_DIR.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    imported = [node.module or ""]
                else:
                    imported = []
                for name in imported:
                    self.assertFalse(
                        any(name == prefix or name.startswith(prefix + ".") for prefix in DEPENDENCY_PREFIXES),
                        f"{path.name} imports forbidden module {name}",
                    )
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    self.assertNotEqual(node.func.id, "open", f"{path.name} performs file I/O")

    def test_decision_and_classical_detector_packages_do_not_import_chart_geometry(self):
        for package in (
            REPO_ROOT / "src" / "trading_assistant" / "decision",
            REPO_ROOT / "src" / "trading_assistant" / "pattern_liquidity",
        ):
            for path in package.glob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        imported = [alias.name for alias in node.names]
                    elif isinstance(node, ast.ImportFrom):
                        imported = [node.module or ""]
                    else:
                        imported = []
                    self.assertFalse(
                        any(name == "trading_assistant.chart_geometry" or name.startswith("trading_assistant.chart_geometry.") for name in imported),
                        f"{path.name} imports chart geometry",
                    )
