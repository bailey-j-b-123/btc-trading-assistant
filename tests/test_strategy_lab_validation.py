"""Synthetic-only validation controls; never evidence of market profitability."""

from decimal import Decimal as D
import json
import pytest
from trading_assistant.strategy_lab.validation import (
    ValidationRules,
    bootstrap_lower,
    gate,
)
from trading_assistant.strategy_lab.campaign import ResearchPlan, run_campaign
from trading_assistant.strategy_lab.store import Store
from trading_assistant.strategy_lab.data import acquire
from test_public_archive import archive


def test_actual_detector_pipeline_validates_promotes_once_and_protects_holdout(
    tmp_path,
):
    """Artificial markets test software control flow, NEVER market performance."""
    store = Store(tmp_path / "lab")
    months = [f"2024-{i:02}" for i in range(1, 6)]

    def geometry(rows):
        for i, row in enumerate(rows):
            # A repeated synthetic rise/fall with a flat ending: actual moving
            # average detector, fills and exits all execute without injected signals.
            phase = i % 100
            excursion = max(0, phase - 20) if phase < 60 else 100 - phase
            price = D("100") + D(excursion) * D("0.3")
            if i >= len(rows) - 100:
                price = D("100")
            row[1:6] = [
                str(price),
                str(price + D("0.1")),
                str(price - D("0.1")),
                str(price),
                "100",
            ]

    for month in months:
        raw, checksum = archive(month, "1h", mutate=geometry)
        acquire(
            store,
            [month],
            download=lambda url: (
                checksum.encode() if url.endswith(".CHECKSUM") else raw
            ),
        )
    plan = ResearchPlan(
        development=("2024-01-01T00:00:00+00:00", "2024-02-01T00:00:00+00:00"),
        folds=tuple(
            (f"2024-{i:02}-01T00:00:00+00:00", f"2024-{i + 1:02}-01T00:00:00+00:00")
            for i in (2, 3, 4)
        ),
        holdout=("2024-05-01T00:00:00+00:00", "2024-06-01T00:00:00+00:00"),
        search_size=1,
        rules=ValidationRules(
            minimum_development_trades=5,
            minimum_fold_trades=5,
            minimum_holdout_trades=5,
            bootstrap_samples=1000,
        ),
    )
    answer = None
    for _ in range(12):
        answer = run_campaign(store, plan, max_jobs=1, prepare=False)
        if answer["finished"]:
            break
        store.close()
        store = Store(tmp_path / "lab")
    assert answer["finished"] and answer["promoted"], answer
    assert answer["completed_jobs"] == 10
    assert run_campaign(store, plan, prepare=False)["jobs_this_run"] == 0
    assert store.db.execute("SELECT count(*) FROM promotions").fetchone()[0] == 1
    from dataclasses import replace

    attempted_reuse = run_campaign(
        store, replace(plan, search_size=2), max_jobs=256, prepare=False
    )
    assert "holdout_unavailable" in attempted_reuse["reason"]
    assert store.db.execute("SELECT count(*) FROM promotions").fetchone()[0] == 1
    assert (
        sum(
            json.loads(r[0])["stage"] == "holdout"
            for r in store.db.execute("SELECT definition FROM experiments")
        )
        == 2
    )
    store.close()


def result(values):
    vals = list(map(D, values))
    n = len(vals)
    return {
        "closed_trades": n,
        "unresolved_gap_positions": 0,
        "net_return": str(sum(vals) / 100),
        "open_positions": 0,
        "pending_entries": 0,
        "expectancy_r": str(sum(vals) / n),
        "maximum_close_mark_drawdown": "0.05",
        "ambiguous_adverse_trades": 0,
        "net_r_values": list(map(str, vals)),
    }


def test_validation_rejects_small_gross_only_overfit_uncertain_and_incomplete_results():
    rules = ValidationRules()
    for measured, reason in (
        (result(["1"] * 8), "insufficient_development_sample"),
        (result(["-0.01"] * 100), "nonpositive_after_cost_expectancy"),
        (result(["1", "-0.99"] * 60), "block_bootstrap_lower_not_positive"),
        (
            {**result(["1"] * 120), "open_positions": 1},
            "unfinished_position_or_pending_entry",
        ),
    ):
        v = gate(
            measured,
            search_size=16,
            rules=rules,
            stage="development",
            seed="frozen-fixture",
        )
        assert not v["passed"] and reason in v["reasons"]


def test_bootstrap_is_deterministic_full_search_adjusted_and_accepts_a_software_positive_control():
    rules = ValidationRules()
    data = ["0.5", "0.6", "0.4", "0.8"] * 30
    a = bootstrap_lower(data, search_size=16, rules=rules, seed="test-only")
    assert a == bootstrap_lower(data, search_size=16, rules=rules, seed="test-only")
    assert a <= bootstrap_lower(data, search_size=1, rules=rules, seed="test-only")
    assert gate(
        result(data), search_size=16, rules=rules, stage="development", seed="test-only"
    )["passed"]
    with pytest.raises(ValueError):
        bootstrap_lower(data, search_size=0, rules=rules, seed="test-only")


def test_campaign_chronology_and_frozen_plan_guards():
    with pytest.raises(ValueError):
        ResearchPlan(folds=())
    with pytest.raises(ValueError):
        ResearchPlan(holdout=("2022-01-01T00:00:00+00:00", "2023-01-01T00:00:00+00:00"))
    with pytest.raises(ValueError):
        ResearchPlan(search_size=129)
    assert ResearchPlan() == ResearchPlan()


def test_actual_campaign_resumes_rejects_no_signal_candidate_and_never_touches_holdout(
    tmp_path,
):
    store = Store(tmp_path / "lab")
    raw, checksum = archive("2024-01", "1h")

    def download(url):
        return checksum.encode() if url.endswith(".CHECKSUM") else raw

    acquire(store, ["2024-01"], download=download)
    plan = ResearchPlan(
        development=("2024-01-01T00:00:00+00:00", "2024-02-01T00:00:00+00:00"),
        folds=(
            ("2024-02-01T00:00:00+00:00", "2024-03-01T00:00:00+00:00"),
            ("2024-03-01T00:00:00+00:00", "2024-04-01T00:00:00+00:00"),
            ("2024-04-01T00:00:00+00:00", "2024-05-01T00:00:00+00:00"),
        ),
        search_size=1,
    )
    partial = run_campaign(store, plan, max_jobs=1, prepare=False)
    assert not partial["finished"] and partial["completed_jobs"] == 1
    completed = run_campaign(store, plan, max_jobs=1, prepare=False)
    assert (
        completed["finished"]
        and completed["completed_jobs"] == 2
        and completed["promoted"] is None
    )
    repeat = run_campaign(store, plan, max_jobs=1, prepare=False)
    assert repeat["jobs_this_run"] == 0
    assert store.db.execute("SELECT count(*) FROM experiments").fetchone()[0] == 2
    assert store.db.execute("SELECT count(*) FROM holdout_custody").fetchone()[0] == 0
    assert store.db.execute("SELECT count(*) FROM promotions").fetchone()[0] == 0
    state = json.loads(store.db.execute("SELECT state FROM campaigns").fetchone()[0])
    assert all(
        "insufficient_development_sample" in j["gate"]["reasons"]
        for j in state["jobs"].values()
    )
    store.close()
