"""Observational diagnostics preserve detector/qualification/planning decisions."""

from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal as D
from types import SimpleNamespace

import pytest
from test_setup_qualification import breakout, held, frame, at
from test_trade_planning import displaced_series
from test_multi_timeframe import _snapshot, candle
from trading_assistant.multi_timeframe.boundaries import resolve_decision_boundary
from trading_assistant.multi_timeframe.confirmation import evaluate_confirmation
from trading_assistant.multi_timeframe.execution import evaluate_execution
from trading_assistant.multi_timeframe.alignment import evaluate_alignment
from trading_assistant.multi_timeframe.decision import gate_decision
from trading_assistant.multi_timeframe.setup import (
    build_setup_snapshot,
    select_active_setup,
)
from trading_assistant.market_structure.parameters import MarketStructureParameters
from trading_assistant.setup_qualification.engine import enumerate_qualifications
from trading_assistant.setup_qualification.parameters import QualificationParameters
from trading_assistant.strategy_lab.candidate_diagnostics import (
    CandidateDiagnostics,
    retest_geometry,
)
from trading_assistant.strategy_lab.models import Execution
from trading_assistant.strategy_lab.simulation import initial_state
from trading_assistant.strategy_lab.store import Store


def fixture():
    seed = breakout()
    other = replace(
        seed,
        id=seed.id + "distinct",
        reference=replace(seed.reference, id=seed.reference.id + "distinct"),
    )
    frames = (
        frame(6, (seed, other), candles=displaced_series(6)),
        frame(7, (seed, other, held(seed)), candles=displaced_series(7)),
    )
    q = enumerate_qualifications(frames, as_of=at(7))[-1]
    snapshot = _snapshot(decision_time=at(7))
    sources = {
        tf: tuple(
            candle(tf, at(6) + i * step, "112")
            for i in range(int(timedelta(hours=1) / step))
        )
        for tf, step in (
            ("4h", timedelta(hours=4)),
            ("1h", timedelta(hours=1)),
            ("15m", timedelta(minutes=15)),
            ("5m", timedelta(minutes=5)),
        )
    }
    sources["1h"] = tuple(candle("1h", at(i), "112") for i in range(7))
    sources["4h"] = (candle("4h", at(0), "112"),)
    boundary = resolve_decision_boundary(at(7), snapshot.hierarchy, sources)
    setup = build_setup_snapshot(
        q,
        selected=select_active_setup(q),
        ended=None,
        frame=frames[-1],
        boundary=boundary.boundary_for("1h"),
    )
    confirmation = evaluate_confirmation(
        setup, sources["15m"], boundary=boundary.boundary_for("15m")
    )
    execution = evaluate_execution(
        setup, confirmation, sources["5m"], boundary=boundary.boundary_for("5m")
    )
    alignment = evaluate_alignment(snapshot.context, setup)
    verdict = gate_decision(
        context=snapshot.context,
        setup=setup,
        confirmation=confirmation,
        execution=execution,
        alignment=alignment,
    )
    snapshot = replace(
        snapshot,
        boundary=boundary,
        setup=setup,
        confirmation=confirmation,
        execution=execution,
        alignment=alignment,
        decision=verdict[0],
    )
    service = SimpleNamespace(
        hierarchy=snapshot.hierarchy,
        qualification_parameters=QualificationParameters(),
        structure_parameters=MarketStructureParameters(),
    )
    portfolios = {p: initial_state(Execution()) for p in ("A1", "A2", "A3")}
    return dict(
        snapshot=snapshot,
        frames=frames,
        sources=sources,
        service=service,
        before=portfolios,
        after=portfolios,
        produced=[],
    )


@pytest.mark.parametrize(
    "distance,kind",
    [("0", "physical"), (".01", "tolerance_only"), ("10", "no_current_contact")],
)
def test_contact_is_measured_not_inferred_from_held_state(distance, kind):
    event = held(breakout())
    ref = event.breakout.reference
    low = ref.band_high + D(distance)
    event = replace(
        event,
        candle=replace(event.candle, low=low, high=low + 1),
        distance_from_band=D(distance),
    )
    evidence = retest_geometry(event)
    assert evidence["contact"] == kind and D(evidence["nearest_distance"]) == D(
        distance
    )
    assert (
        evidence["breakout_id"] == event.breakout.id
        and evidence["reference_id"] == ref.id
    )
    assert (
        evidence["state"] == "held"
    )  # remains unchanged, not relabeled a new observed event


def test_all_candidates_are_captured_before_selection_with_exact_links(tmp_path):
    kwargs = fixture()
    original = kwargs["snapshot"].to_json_dict()
    store = Store(tmp_path / "lab")
    observer = CandidateDiagnostics(store, "test", {"start": at(7)})
    facts, observations, coverage = observer.capture(**kwargs)
    assert coverage["candidates"] == 2 and len(observations) == 2
    assert sum(o["selected"] for o in observations) == 1
    assert sum(o["diagnostic_only"] for o in observations) == 1
    bases = [v[1] for v in facts.values() if v[0] == "candidate_basis"]
    assert sorted(len(b["retests"]) for b in bases) == [
        0,
        1,
    ]  # nearby reference is not joined
    assert all(datetime.fromisoformat(b["setup"]["created_at"]) == at(6) for b in bases)
    assert kwargs["snapshot"].to_json_dict() == original
    with pytest.raises(RuntimeError, match="transaction"):
        observer.commit((facts, observations, coverage), at(7).isoformat())
    store.close()


def test_boundary_commit_rolls_back_deduplicates_facts_and_reopens(tmp_path):
    kwargs = fixture()
    root = tmp_path / "lab"
    store = Store(root)
    observer = CandidateDiagnostics(store, "test", {})
    staged = observer.capture(**kwargs)
    store.db.execute("BEGIN IMMEDIATE")
    observer.commit(staged, at(7).isoformat())
    store.db.execute("ROLLBACK")
    assert (
        store.db.execute("SELECT COUNT(*) FROM candidate_diagnostic_facts").fetchone()[
            0
        ]
        == 0
    )
    store.db.execute("BEGIN IMMEDIATE")
    observer.commit(staged, at(7).isoformat())
    store.db.execute("COMMIT")
    first = store.db.execute(
        "SELECT COUNT(*) FROM candidate_diagnostic_facts"
    ).fetchone()[0]
    store.close()
    store = Store(root)
    observer = CandidateDiagnostics(store, "test", {})
    later = at(7) + timedelta(minutes=5)
    store.db.execute("BEGIN IMMEDIATE")
    observer.commit(staged, later.isoformat())
    store.db.execute("COMMIT")
    assert (
        store.db.execute("SELECT COUNT(*) FROM candidate_diagnostic_facts").fetchone()[
            0
        ]
        == first
    )
    assert (
        store.db.execute(
            "SELECT COUNT(*) FROM candidate_diagnostic_observations"
        ).fetchone()[0]
        == 4
    )
    with pytest.raises(Exception):
        store.db.execute("BEGIN IMMEDIATE")
        observer.commit(staged, later.isoformat())
    store.db.execute("ROLLBACK")
    store.close()


def test_future_suffix_does_not_change_candidate_assessments_and_missing_data_is_explicit(
    tmp_path,
):
    kwargs = fixture()
    store = Store(tmp_path / "lab")
    observer = CandidateDiagnostics(store, "test", {})
    first = observer.capture(**kwargs)
    kwargs["sources"] = {
        tf: values + (candle(tf, at(20), "999"),)
        for tf, values in kwargs["sources"].items()
    }
    assert observer.capture(**kwargs) == first
    unavailable = observer.capture(**dict(kwargs, frames=None))
    assert unavailable[2]["available"] is False and unavailable[2]["candidates"] is None
    kwargs["sources"]["15m"] = kwargs["sources"]["15m"][1:]
    # Do not compare selected invariant to a snapshot from different candles.
    kwargs["snapshot"] = replace(
        kwargs["snapshot"], setup=replace(kwargs["snapshot"].setup, setup_id=None)
    )
    missing = observer.capture(**kwargs)
    assert all(o["confirmation"]["missing_candle_count"] == 1 for o in missing[1])
    store.close()


def test_readonly_export_preserves_rows_and_counts_unique_candidates(tmp_path):
    import importlib.util
    from pathlib import Path
    import hashlib
    import json

    script = Path(__file__).parents[1] / "scripts/export_candidate_diagnostics.py"
    spec = importlib.util.spec_from_file_location("candidate_export", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = tmp_path / "lab"
    store = Store(root)
    store.db.execute(
        "CREATE TABLE brain_runs(id TEXT PRIMARY KEY,state TEXT,result TEXT)"
    )
    state = {"portfolios": {"A1": initial_state(Execution())}}
    store.db.execute(
        "INSERT INTO brain_runs VALUES(?,?,NULL)", ("test", json.dumps(state))
    )
    observer = CandidateDiagnostics(store, "test", {"start": at(5)})
    staged = observer.capture(**fixture())
    store.db.execute("BEGIN IMMEDIATE")
    observer.commit(staged, at(7).isoformat())
    store.db.execute("COMMIT")
    store.db.execute("BEGIN IMMEDIATE")
    observer.commit(staged, (at(7) + timedelta(minutes=5)).isoformat())
    store.db.execute("COMMIT")
    store.close()
    database = root / "research.sqlite3"
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    report = module.export(root, observer.id, tmp_path / "export")
    assert report["total_distinct_candidates"] == 2
    assert report["detected_in_observed_period"] == 2
    assert report["distinct_selected_candidates"] == 1
    assert report["processed_boundaries"] == 2
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    with pytest.raises(ValueError, match="fresh"):
        module.export(root, observer.id, tmp_path / "export")


def test_large_target_catalog_keeps_exact_obstacle_prefix_without_quadratic_lists(
    monkeypatch,
):
    from trading_assistant.strategy_lab import candidate_diagnostics as diagnostic
    from trading_assistant.trade_planning.levels import TargetCandidate
    from trading_assistant.trade_planning.planner import plan_trade
    from test_trade_planning import qualified

    q, f, setup = qualified()
    plan = plan_trade(snapshot=q, frame=f, setup_id=setup.id)
    catalog = tuple(
        TargetCandidate(plan.entry.value + i, str(i), "fixture", at(1), at(2))
        for i in range(1, 121)
    )
    monkeypatch.setattr(
        diagnostic, "structural_target_catalog", lambda *a: (catalog, ())
    )
    result = diagnostic.targets(f, setup, plan)
    assert len(result["levels"]) == 120
    for index, level in enumerate(result["levels"]):
        assert level["intervening_prefix_length"] == index
        assert "intervening_source_ids" not in level
        assert [v["source_id"] for v in result["levels"][:index]] == [
            str(i) for i in range(1, index + 1)
        ]
