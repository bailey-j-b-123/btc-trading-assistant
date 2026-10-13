"""Audit the endurance comparator without changing a stored row or decision.

The engine generates every fixture record. Negative checks poison only copied
reporting inputs; mocked run_case returns those actual fixture projections so
the comparison layer can be checked separately from a long replay.
"""
from copy import deepcopy
from decimal import Decimal
import hashlib

import pytest

from forward_fixtures import INTERVAL, QUALIFYING_BOUNDARY, bar, labelled_series, make_harness
import forward_endurance_harness as harness_code


def _fixture(request, *, delayed=False):
    harness = make_harness(series=labelled_series(), ledger_start=QUALIFYING_BOUNDARY)
    request.addfinalizer(harness.engine.dispose)
    harness.advance_to(QUALIFYING_BOUNDARY)
    assert harness.run(refresh_market_data=False).paper_plans_created == 1
    if delayed:
        harness.advance_to(QUALIFYING_BOUNDARY + INTERVAL)
        harness.run(refresh_market_data=False)
        assert harness.latest_outcomes()[0].status.value == "INCOMPLETE_DATA"
    harness.step((bar(21, 126, low=123),), refresh_market_data=False)
    harness.step((bar(22, 140, high=141, low=124),), refresh_market_data=False)
    assert harness.latest_outcomes()[0].status.value == "TARGETS_REACHED"
    assert len(harness.outcome_versions(harness.plans()[0].paper_plan_id)) == (3 if delayed else 2)
    return harness


def _canonical_with_copied_rows(monkeypatch, harness, rows):
    with monkeypatch.context() as patch:
        patch.setattr(harness_code, "raw_rows", lambda _engine: rows)
        return harness_code.canonical_state(harness.engine, harness.service)


def _compare_fixture_projections(monkeypatch, baseline, stress):
    # This is comparison-only input, not a claimed execution of a soak scenario.
    reports = {
        "baseline": {"case": "baseline", "boundaries": 3, "canonical": baseline},
        "stress": {"case": "stress", "boundaries": 3, "canonical": stress},
    }
    with monkeypatch.context() as patch:
        patch.setattr(harness_code, "run_case", lambda name, _boundaries, **_kwargs: reports[name])
        return harness_code.run_endurance(3)


def test_comparison_detects_altered_earlier_outcome_even_when_latest_is_unchanged(monkeypatch, request):
    harness = _fixture(request)
    before = harness_code.canonical_state(harness.engine, harness.service)
    original = harness_code.raw_rows(harness.engine)
    poisoned = deepcopy(original)
    first = min(poisoned["forward_paper_outcomes"].values(), key=lambda row: row["sequence"])
    first["payload_json"] = '{"deliberately_poisoned_comparison_input":true}'
    after = _canonical_with_copied_rows(monkeypatch, harness, poisoned)
    assert harness_code.raw_rows(harness.engine) == original
    assert before["row_counts"] == after["row_counts"]
    assert before["hashes"]["latest_outcome_payloads"] == after["hashes"]["latest_outcome_payloads"]
    assert before["hashes"]["full_outcome_chains"] != after["hashes"]["full_outcome_chains"]


def test_comparison_retains_frozen_proposal_levels(monkeypatch, request):
    harness = _fixture(request)
    before = harness_code.canonical_state(harness.engine, harness.service)
    original = harness_code.raw_rows(harness.engine)
    poisoned = deepcopy(original)
    next(iter(poisoned["forward_paper_plans"].values()))["entry_level"] += Decimal(1)
    after = _canonical_with_copied_rows(monkeypatch, harness, poisoned)
    assert harness_code.raw_rows(harness.engine) == original
    assert before["hashes"]["forward_paper_plans"] != after["hashes"]["forward_paper_plans"]


def test_assigned_chain_identity_difference_without_extra_incomplete_audits_is_rejected(
    monkeypatch, request,
):
    harness = _fixture(request)
    original = harness_code.raw_rows(harness.engine)
    poisoned = deepcopy(original)
    outcomes = poisoned["forward_paper_outcomes"]
    assert len(outcomes) == 2
    assert all(row["status"] != "INCOMPLETE_DATA" for row in outcomes.values())
    renamed = {
        key: hashlib.sha256(f"copied-report-identity:{key}".encode()).hexdigest()
        for key in outcomes
    }
    replaced = {}
    for key, row in outcomes.items():
        row["outcome_id"] = renamed[key]
        if row["supersedes_outcome_id"] is not None:
            row["supersedes_outcome_id"] = renamed[row["supersedes_outcome_id"]]
        replaced[renamed[key]] = row
    poisoned["forward_paper_outcomes"] = replaced
    before = harness_code.canonical_state(harness.engine, harness.service)
    after = _canonical_with_copied_rows(monkeypatch, harness, poisoned)
    assert before["row_counts"] == after["row_counts"]
    assert before["hashes"]["latest_outcome_payloads"] == after["hashes"]["latest_outcome_payloads"]
    assert before["hashes"]["full_outcome_chains"] != after["hashes"]["full_outcome_chains"]
    report = _compare_fixture_projections(monkeypatch, before, after)
    assert report["checks_passed"] is False
    assert report["canonical_agreement"] is False
    assert report["canonical_comparison"]["full_outcome_chains"] is False
    assert "UNCLASSIFIED" in report["outcome_chain_classification"]
    assert harness_code.raw_rows(harness.engine) == original


def test_real_incomplete_audit_difference_is_reported_without_claiming_strict_chain_equality(
    monkeypatch, request,
):
    baseline = _fixture(request)
    stress = _fixture(request, delayed=True)
    baseline_rows = harness_code.raw_rows(baseline.engine)
    stress_rows = harness_code.raw_rows(stress.engine)
    report = _compare_fixture_projections(
        monkeypatch,
        harness_code.canonical_state(baseline.engine, baseline.service),
        harness_code.canonical_state(stress.engine, stress.service),
    )
    comparison = report["canonical_comparison"]
    assert comparison["full_outcome_chains"] is False
    assert report["checks_passed"] is True
    assert report["canonical_agreement"] is False
    assert all(matches for name, matches in comparison.items() if name != "full_outcome_chains")
    assert report["outcome_chain_classification"]
    assert stress_rows["forward_paper_outcomes"] != baseline_rows["forward_paper_outcomes"]
    assert harness_code.raw_rows(baseline.engine) == baseline_rows
    assert harness_code.raw_rows(stress.engine) == stress_rows
    assert report["cases"][1]["canonical"]["unresolved_audit_versions"]
    assert report["cases"][1]["canonical"]["row_counts"]["forward_paper_outcomes"] == 3


@pytest.mark.parametrize("field", ["payload_json", "observation_id"])
def test_incomplete_audit_allowance_cannot_hide_altered_non_incomplete_history(
    monkeypatch, request, field,
):
    baseline = _fixture(request)
    stress = _fixture(request, delayed=True)
    original = harness_code.raw_rows(stress.engine)
    poisoned = deepcopy(original)
    middle = next(row for row in poisoned["forward_paper_outcomes"].values() if row["status"] == "OPEN_AT_CUTOFF")
    if field == "payload_json":
        middle[field] = '{"deliberately_poisoned_comparison_input":true}'
    else:
        middle[field] = next(
            key for key in poisoned["forward_observations"] if key != middle["observation_id"]
        )
    before = harness_code.canonical_state(stress.engine, stress.service)
    after = _canonical_with_copied_rows(monkeypatch, stress, poisoned)
    assert before["hashes"]["latest_outcome_payloads"] == after["hashes"]["latest_outcome_payloads"]
    assert before["hashes"]["full_outcome_chains"] != after["hashes"]["full_outcome_chains"]
    report = _compare_fixture_projections(
        monkeypatch, harness_code.canonical_state(baseline.engine, baseline.service), after,
    )
    assert report["checks_passed"] is False
    assert report["canonical_agreement"] is False
    assert report["canonical_comparison"]["full_outcome_chains"] is False
    assert harness_code.raw_rows(stress.engine) == original
