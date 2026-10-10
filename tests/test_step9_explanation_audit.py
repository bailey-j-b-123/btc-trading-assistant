"""Component #7 audit: AI explanation (Step 9) regression tests.

* The context builder refused future-dated decisions, journals, statistics,
  and hierarchies, but a latest outcome observed past the explanation cutoff
  was silently embedded — future market facts grounding a current
  explanation. It is now refused like every other future input.
* A snapshot with ``as_of=None`` left every cutoff guard unanchored (dated
  optionals crashed with ``TypeError`` instead of the contract error).
* Provider-response validation skipped non-string prose silently, letting it
  bypass every numeric-token and forbidden-language scan while still being
  rendered; malformed claim collections crashed validation instead of
  failing as violations.
* The local renderer cited two values without recording them (the setup id
  in the no-passed-rules line, statistics group keys) and hardcoded the
  default 4H/1H/15M/5M ladder in one line — false for any custom hierarchy.
"""

from __future__ import annotations

from dataclasses import replace

from test_ai_explanation import (
    SERVICE,
    dated_record,
    grounded_response,
    plannable_scenario,
    section_text,
    stat_observation,
    statistics_report_with,
)
from test_multi_timeframe import _snapshot as _mtf_snapshot
from test_setup_qualification import at

from trading_assistant.ai_explanation import (
    build_fact_manifest,
    validate_provider_response,
)
from trading_assistant.ai_explanation.errors import ContextBuildError
from trading_assistant.multi_timeframe.hierarchy import (
    TimeframeHierarchy,
    TimeframeRole,
    TimeframeStep,
)
from trading_assistant.setup_qualification import RuleOutcome


def _journaled():
    snapshot, planning_frame, setup, plan = plannable_scenario()
    record = replace(
        dated_record(0),
        exchange=snapshot.exchange,
        symbol=snapshot.symbol,
        setup_id=setup.id,
        plan_id=plan.id,
    )
    return snapshot, planning_frame, setup, plan, record


def test_future_outcome_cannot_ground_an_explanation() -> None:
    snapshot, planning_frame, setup, plan, record = _journaled()
    outcome = stat_observation(record, "stopped")
    assert outcome.observed_through <= snapshot.as_of
    SERVICE.build_context(
        snapshot=snapshot,
        setup_id=setup.id,
        frame=planning_frame,
        plan=plan,
        journal_record=record,
        latest_outcome=outcome,
    )
    future_outcome = replace(outcome, observed_through=at(8))
    try:
        SERVICE.build_context(
            snapshot=snapshot,
            setup_id=setup.id,
            frame=planning_frame,
            plan=plan,
            journal_record=record,
            latest_outcome=future_outcome,
        )
    except ContextBuildError as exc:
        assert "after the explanation as_of" in str(exc)
        assert "future data" in str(exc)
    else:
        raise AssertionError("future outcome was accepted")


def test_snapshot_without_as_of_is_refused() -> None:
    snapshot, _frame, _setup, _plan = plannable_scenario()
    timeless = replace(snapshot, as_of=None)
    try:
        SERVICE.build_context(snapshot=timeless)
    except ContextBuildError as exc:
        assert "as_of" in str(exc)
    else:
        raise AssertionError("as_of=None snapshot was accepted")


def test_non_string_provider_prose_is_a_violation_not_silently_skipped() -> None:
    snapshot, planning_frame, setup, plan = plannable_scenario()
    context = SERVICE.build_context(
        snapshot=snapshot, setup_id=setup.id, frame=planning_frame, plan=plan
    )
    manifest = build_fact_manifest(context)
    bad = grounded_response(plan_explanation=12345, risk_notes=("fine", 999))
    violations = validate_provider_response(bad, context, manifest)
    assert any("invalid_prose_type" in v and "plan_explanation" in v for v in violations)
    assert any("invalid_prose_type" in v and "risk_notes[1]" in v for v in violations)
    # Well-typed prose still validates cleanly.
    good = grounded_response(plan_explanation="Recorded levels only.", risk_notes=["ok"])
    assert validate_provider_response(good, context, manifest) == ()


def test_malformed_claim_collections_are_violations_not_crashes() -> None:
    snapshot, planning_frame, setup, plan = plannable_scenario()
    context = SERVICE.build_context(
        snapshot=snapshot, setup_id=setup.id, frame=planning_frame, plan=plan
    )
    manifest = build_fact_manifest(context)
    bad = grounded_response(evidence_for=None, risk_notes="not-a-tuple")
    violations = validate_provider_response(bad, context, manifest)
    assert any("invalid_claims_type" in v and "evidence_for" in v for v in violations)
    assert any("invalid_prose_type" in v and "risk_notes" in v for v in violations)


def test_renderer_records_every_cited_value() -> None:
    snapshot, planning_frame, setup, plan = plannable_scenario()
    report, _records = statistics_report_with(["stopped"])
    context = SERVICE.build_context(
        snapshot=snapshot,
        setup_id=setup.id,
        frame=planning_frame,
        plan=plan,
        statistics_report=report,
    )
    result = SERVICE.explain(context)
    assert "ctx.statistics.report.groups[0].key[0][0]" in result.referenced_fact_ids
    assert "ctx.statistics.report.groups[0].key[0][1]" in result.referenced_fact_ids
    # A setup with no passed rules still cites its own id.
    pending_setup = replace(
        setup,
        rules=tuple(
            replace(rule, outcome=RuleOutcome.PENDING) for rule in setup.rules
        ),
    )
    pending_snapshot = replace(snapshot, setups=(pending_setup,))
    pending_context = SERVICE.build_context(
        snapshot=pending_snapshot, setup_id=setup.id, frame=planning_frame
    )
    pending_result = SERVICE.explain(pending_context)
    assert "no passed rules recorded" in section_text(pending_result, 3)
    assert "ctx.qualification.setups[0].id" in pending_result.referenced_fact_ids


def test_hierarchy_ladder_uses_recorded_timeframes() -> None:
    snapshot, planning_frame, _setup, _plan = plannable_scenario()
    base = replace(_mtf_snapshot(), exchange=snapshot.exchange)
    custom = replace(
        base,
        hierarchy=TimeframeHierarchy(
            steps=(
                TimeframeStep(role=TimeframeRole.CONTEXT, timeframe="1d"),
                TimeframeStep(role=TimeframeRole.SETUP, timeframe="4h"),
                TimeframeStep(role=TimeframeRole.CONFIRMATION, timeframe="1h"),
                TimeframeStep(role=TimeframeRole.EXECUTION, timeframe="30m"),
            )
        ),
        context=replace(base.context, timeframe="1d"),
        setup=replace(base.setup, timeframe="4h"),
        confirmation=replace(base.confirmation, timeframe="1h"),
        execution=replace(base.execution, timeframe="30m"),
    )
    context = SERVICE.build_context(
        snapshot=snapshot, frame=planning_frame, hierarchy=custom
    )
    line = section_text(SERVICE.explain(context), 6)
    assert "1d context, 4h setup, 1h confirmation, 30m execution" in line
    assert "4H context" not in line
