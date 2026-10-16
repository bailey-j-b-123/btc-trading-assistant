"""Synthetic recorded contracts only; no detections, trades or profit evidence."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json

import pytest
from trading_assistant.strategy_lab.models import canonical
from trading_assistant.strategy_lab.opportunity_diagnostics import (
    summarize_observations,
    summarize_run,
)
from trading_assistant.strategy_lab.store import Store

START = datetime(2024, 1, 1, tzinfo=UTC)


def observation(i=0, **changes):
    at = (START + timedelta(minutes=5 * i)).isoformat()
    snapshot = {
        "exchange": "binance",
        "symbol": "BTC/USDT",
        "decision_time": at,
        "decision": "awaiting_confirmation",
        "alignment": "counter_trend",
        "reasons": ["counter_trend_blocked_below_plannable"],
        "setup": {
            "setup_id": "synthetic-setup",
            "timeframe": "1h",
            "state": "QUALIFIED",
            "created_at": (START - timedelta(hours=1)).isoformat(),
            "reference_id": "zone-a",
            "config_fingerprint": "config-a",
            "pending_rules": ["volume"],
            "opposing_rules": [],
        },
        "confirmation": {"state": "waiting"},
        "execution": {"state": "waiting"},
    }
    snapshot.update(changes)
    return at, {
        "snapshot": snapshot,
        "plan": None,
        "policies": {"A2": {"decision": "plannable"}},
    }


def rows(items):
    return [(at, canonical(payload)) for at, payload in items]


def test_deduplicate_observations_keep_actual_baseline_and_first_observed_stages():
    a = observation()
    b = observation(1, alignment="aligned", confirmation={"state": "confirming"})
    c = observation(
        2,
        decision="plannable",
        alignment="aligned",
        confirmation={"state": "confirming"},
        execution={"state": "triggered"},
    )
    saved = deepcopy([a, b, c])
    result = summarize_observations(rows([a, b, b, c]))
    assert [a, b, c] == saved
    assert (
        result["counts"]["boundaries"] == 3
        and result["counts"]["identical_duplicates_ignored"] == 1
    )
    assert (
        result["unique_selected_setups"] == 1 and result["unique_recorded_plans"] == 0
    )
    item = result["opportunities"][0]
    assert item["observations"] == 3 and item["created_before_cohort"]
    assert item["first_stage_observed"]["plannable"] == c[0]
    assert item["first_stage_observed"]["confirming"] == b[0]
    assert item["first_stage_observed"]["actionable_plan"] is None
    assert item["gate_details"]["pending_qualification_rule"] == {"volume": 3}
    assert result["baseline_decisions"] == {
        "awaiting_confirmation": 2,
        "plannable": 1,
    }  # never A2
    assert (
        result["coverage"]["retest_contact"] is None
        and not result["profitability_claim"]
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("timeframe", "15m"),
        ("config_fingerprint", "config-b"),
        ("created_at", "2023-12-31T22:00:00+00:00"),
    ],
)
def test_matching_price_or_setup_string_does_not_merge_different_scopes(field, value):
    a, b = observation(), observation(1)
    b[1]["snapshot"]["setup"][field] = value
    assert summarize_observations(rows([a, b]))["unique_selected_setups"] == 2


def test_conflicting_boundary_future_evidence_and_changed_reference_are_refused():
    a = observation()
    b = deepcopy(a)
    b[1]["snapshot"]["decision"] = "plannable"
    with pytest.raises(ValueError, match="conflicting duplicate"):
        summarize_observations(rows([a, b]))
    b = observation(1)
    b[1]["snapshot"]["setup"]["reference_id"] = "nearby-but-different"
    with pytest.raises(ValueError, match="changed reference"):
        summarize_observations(rows([a, b]))
    b = observation()
    b[1]["snapshot"]["execution"]["trigger_at"] = (
        START + timedelta(minutes=5)
    ).isoformat()
    with pytest.raises(ValueError, match="future timestamp"):
        summarize_observations(rows([b]))
    with pytest.raises(ValueError, match="chronological"):
        summarize_observations(rows([observation(1), a]))


def test_missing_selected_setup_and_plan_do_not_invent_success_or_available_cash():
    result = summarize_observations(rows([observation(setup={})]))
    assert (
        result["unique_selected_setups"] == 0 and result["unique_recorded_plans"] == 0
    )
    assert result["counts"]["boundaries_without_selected_setup"] == 1
    assert all(x == 0 for x in result["unique_setups_reaching_stage"].values())
    assert result["coverage"]["counterfactual_fills"] is None


def test_other_policy_plan_is_retained_but_not_counted_as_baseline_actionable():
    at, payload = observation(decision="watch")
    payload["plan"] = {
        "id": "synthetic-plan",
        "setup_id": "synthetic-setup",
        "state": "PLANNABLE",
        "as_of": at,
    }
    result = summarize_observations(rows([(at, payload)]))
    assert result["unique_recorded_plans"] == 1
    assert result["unique_baseline_observed_plans"] == 0
    assert result["unique_setups_reaching_stage"]["actionable_plan"] == 0
    assert result["plans"] == [payload["plan"]]


def test_readonly_completed_cohort_guard_and_source_rows_unchanged(tmp_path):
    store = Store(tmp_path / "lab")
    store.db.executescript(
        "CREATE TABLE brain_runs(id TEXT PRIMARY KEY,definition TEXT,result TEXT);CREATE TABLE brain_observations(run TEXT,boundary TEXT,payload TEXT);"
    )
    definition = {
        "start": START.isoformat(),
        "end": (START + timedelta(hours=1)).isoformat(),
    }
    store.db.execute(
        "INSERT INTO brain_runs VALUES(?,?,?)", ("test", canonical(definition), "{}")
    )
    at, payload = observation()
    store.db.execute(
        "INSERT INTO brain_observations VALUES(?,?,?)", ("test", at, canonical(payload))
    )
    before = store.db.execute("SELECT * FROM brain_observations").fetchall()
    try:
        answer = summarize_run(store.root, "test")
        assert answer["source_run"] == "test" and answer["unique_selected_setups"] == 1
        assert store.db.execute("SELECT * FROM brain_observations").fetchall() == before
        store.db.execute(
            "INSERT INTO brain_runs VALUES(?,?,NULL)",
            ("partial", canonical(definition)),
        )
        with pytest.raises(ValueError, match="completed"):
            summarize_run(store.root, "partial")
        protected = {
            "start": "2025-01-01T00:00:00+00:00",
            "end": "2025-02-01T00:00:00+00:00",
        }
        store.db.execute(
            "INSERT INTO brain_runs VALUES(?,?,?)",
            ("protected", json.dumps(protected), "{}"),
        )
        with pytest.raises(ValueError, match="sealed 2025"):
            summarize_run(
                store.root, "protected"
            )  # metadata only; no actual protected records
    finally:
        store.close()
