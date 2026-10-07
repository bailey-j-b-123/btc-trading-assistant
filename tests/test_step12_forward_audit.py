"""Component #9 audit: forward runner/ledger (Step 12) regression tests.

* One boundary's cycle, observations, and paper plans were committed in
  separate transactions while the runner skips already-complete cycles, so
  a crash or error between the writes left a permanently recorded boundary
  whose candidate evidence was silently missing. The boundary write is now
  one atomic transaction.
* ``created`` was inferred by comparing ``recorded_at``, misreporting a
  re-recorded row as created whenever two passes shared one clock instant
  (frozen clocks, duplicate catch-up). It now comes from the write itself.
"""

from __future__ import annotations

from decimal import Decimal as D
from types import SimpleNamespace

import pytest
from forward_fixtures import QUALIFYING_BOUNDARY, bar
from market_structure_fixtures import EPOCH, INTERVAL
from test_forward_reporting import (
    harness_with_a_resolved_target,
    historical_report,
)
from test_forward_service import harness_with_a_paper_plan

import trading_assistant.forward_testing.repository as repository
from trading_assistant.forward_testing import build_forward_comparison
from trading_assistant.forward_testing.parameters import VersionSeparation
from trading_assistant.forward_testing.reporting import (
    _HIGHER_VOLATILITY,
    _volatility_labels,
)
from trading_assistant.forward_testing.tables import ForwardObservationRow
from trading_assistant.historical_validation.models import MetricStatus
from trading_assistant.trade_planning import PlanningParameters


def test_boundary_write_is_atomic_when_an_observation_insert_fails(monkeypatch) -> None:
    harness = harness_with_a_paper_plan()
    before_cycles = len(harness.cycles())
    before_observations = len(harness.observations())
    assert before_observations > 0
    harness.store((bar(21, 126, low=123),))
    harness.advance_to(QUALIFYING_BOUNDARY + INTERVAL)

    real_insert = repository.sqlite_insert

    def guarded(table, *args, **kwargs):
        if table is ForwardObservationRow:
            raise RuntimeError("injected observation-write failure")
        return real_insert(table, *args, **kwargs)

    monkeypatch.setattr(repository, "sqlite_insert", guarded)
    with pytest.raises(RuntimeError, match="injected observation-write failure"):
        harness.run(refresh_market_data=False)
    # No partial boundary may survive: neither the cycle row nor any evidence.
    assert len(harness.cycles()) == before_cycles
    assert len(harness.observations()) == before_observations


def test_duplicate_inserts_report_not_created_at_the_same_instant() -> None:
    harness = harness_with_a_paper_plan()
    cycle = harness.cycles()[0]
    stored_cycle, cycle_created = harness.service.ledger.insert_cycle(cycle)
    assert stored_cycle.cycle_id == cycle.cycle_id
    assert cycle_created is False
    observation = harness.observations()[0]
    stored_observation, observation_created = harness.service.ledger.insert_observation(
        observation
    )
    assert stored_observation.observation_id == observation.observation_id
    assert observation_created is False
    plan = harness.plans()[0]
    stored_plan, plan_created = harness.service.ledger.insert_paper_plan(plan)
    assert stored_plan.paper_plan_id == plan.paper_plan_id
    assert plan_created is False


def test_separated_report_warns_that_breakdowns_span_versions() -> None:
    harness = harness_with_a_paper_plan()
    # A stricter floor than the v2 default (1) is a genuinely different
    # planning version; the old fixture used 1, which is now the default.
    harness.service.planning_parameters = PlanningParameters(min_r_multiple=D("2"))
    harness.step((bar(21, 126, low=123),), refresh_market_data=False)
    report = harness.report()
    assert report.version_separation is VersionSeparation.SEPARATED
    assert report.breakdowns
    assert any(
        warning.startswith("breakdowns_span_multiple_version_fingerprints:")
        for warning in report.warnings
    )


def test_comparison_judges_recomputed_historical_rates_against_the_step11_floor() -> None:
    harness = harness_with_a_resolved_target()
    historical = historical_report(harness)
    generated_at = QUALIFYING_BOUNDARY + 2 * INTERVAL

    legacy = build_forward_comparison(
        forward=harness.report(),
        historical=historical,
        generated_at=generated_at,
    )
    assert legacy.historical.metrics.ambiguous_rate.denominator >= 1
    assert (
        legacy.historical.metrics.ambiguous_rate.status
        is MetricStatus.SUFFICIENT_DATA
    )

    floored = build_forward_comparison(
        forward=harness.report(),
        historical=historical,
        generated_at=generated_at,
        historical_minimum_sample_size=20,
    )
    assert (
        floored.historical.metrics.ambiguous_rate.status
        is MetricStatus.INSUFFICIENT_DATA
    )


def test_same_close_observations_share_one_volatility_history() -> None:
    priors = tuple(
        SimpleNamespace(
            observation_id=f"prior-{index}",
            as_of=EPOCH + index * INTERVAL,
            atr_percent_of_price=D(value),
        )
        for index, value in enumerate(("1", "1", "1", "3", "3"))
    )
    siblings = (
        SimpleNamespace(
            observation_id="sibling-extreme",
            as_of=EPOCH + 5 * INTERVAL,
            atr_percent_of_price=D("100"),
        ),
        SimpleNamespace(
            observation_id="sibling-mid",
            as_of=EPOCH + 5 * INTERVAL,
            atr_percent_of_price=D("1.5"),
        ),
    )
    labels = _volatility_labels(priors + siblings)
    # Both siblings are judged against the pre-close median (1): the extreme
    # sibling's own close must not move the median under its sibling.
    assert labels["sibling-extreme"] == _HIGHER_VOLATILITY
    assert labels["sibling-mid"] == _HIGHER_VOLATILITY
