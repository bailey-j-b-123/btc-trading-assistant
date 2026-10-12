"""Reference-provenance gate: plans must not rest on levels the record cannot account for.

What these tests pin:

* the verdict is derived from the evidence the decision itself consumed — the
  swings a level was built from, its zone/range formation record, and the oldest
  catalog event recorded against the same reference — never from a fixed
  warm-up constant (changing the parameters moves the gate);
* ``TRUNCATED`` withholds the Step 6 plan for exactly the affected setups and
  records the deterministic refusal reason; it never creates a paper plan, never
  reclassifies the candidate as a missed opportunity, and leaves every other
  candidate at the same close planned as before;
* an unverifiable verdict (no frame, no stored history, a seed event outside the
  loaded catalog) is reported as ``UNAVAILABLE`` and withholds planning rather
  than being read as complete;
* the limits are explicit: a short record can under-report opportunities without
  any sign of it, so the inventory flag and the lookback advisory are surfaced
  as reported-but-not-blocking statements rather than hidden;
* the labelled forward fixture — whose levels are fully accounted for by its own
  stored history — is unaffected, so the gate adds no new blocking anywhere the
  record is adequate.

Everything runs offline against temporary migrated SQLite databases and
synthetic candles. Nothing here is real market data or a claim about live
BRAIN decisions.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from forward_fixtures import (
    EPOCH,
    EXCHANGE,
    QUALIFYING_BOUNDARY,
    SYMBOL,
    TIMEFRAME,
    labelled_series,
    make_harness,
)

from trading_assistant.forward_testing.parameters import (
    MISSED_OPPORTUNITY_REASON,
    PAPER_TRADE_ACTIVE_REASON,
    REFERENCE_PROVENANCE_REASON,
)
from trading_assistant.pattern_liquidity.events import (
    Breakout,
    FailedBreakout,
    Reference,
    Retest,
    Sweep,
)
from trading_assistant.setup_qualification.provenance import (
    REFERENCE_PROVENANCE_RULES_VERSION,
    ProvenanceStatus,
    assess_reference_provenance,
)
from trading_assistant.setup_qualification.models import (
    QualificationSnapshot,
    SetupResult,
    SetupState,
)

#: Boundary one hour per candle after the last synthetic candle built below.
DECISION = EPOCH + timedelta(hours=40)


# ---------------------------------------------------------------------------
# Synthetic evidence builders (real Step 4 dataclasses, hand-built windows)
# ---------------------------------------------------------------------------


def _open(hour: int) -> datetime:
    return EPOCH + timedelta(hours=hour)


def _swing(hour: int, price: str = "100") -> SimpleNamespace:
    return SimpleNamespace(
        timestamp=_open(hour),
        price=Decimal(price),
        confirmed_at=_open(hour + 1),
    )


def _reference(
    *,
    reference_id: str,
    kind: str,
    swing_hours: tuple[int, ...],
    zone_first_hour: int | None = None,
    zone_member_hours: tuple[int, ...] = (),
) -> Reference:
    return Reference(
        id=reference_id,
        type=kind,  # type: ignore[arg-type]
        band_low=Decimal("99"),
        band_high=Decimal("101"),
        known_at=_open(swing_hours[-1]),
        swings=tuple(_swing(hour) for hour in swing_hours),
        zone=None
        if zone_first_hour is None
        else SimpleNamespace(
            first_observed_timestamp=_open(zone_first_hour),
            source_swing_timestamps=tuple(_open(hour) for hour in zone_member_hours),
        ),
    )


def _breakout(*, event_id: str, hour: int, reference: Reference) -> Breakout:
    return Breakout(
        id=event_id,
        direction="bullish",
        reference=reference,
        previous_candle=_candle(hour - 1),
        candle=_candle(hour),
        known_at=_open(hour),
        confirmation_candles=(),
        breakout_close=Decimal("102"),
        penetration=Decimal("1"),
        penetration_pct=Decimal("1"),
        penetration_atr=None,
        volatility=SimpleNamespace(),
        volume=SimpleNamespace(),
        parameters=SimpleNamespace(),
    )


def _sweep(*, event_id: str, hour: int, reference: Reference) -> Sweep:
    return Sweep(
        id=event_id,
        direction="above",
        reference=reference,
        previous_candle=_candle(hour - 1),
        candle=_candle(hour),
        known_at=_open(hour),
        extreme=Decimal("103"),
        penetration=Decimal("1"),
        penetration_pct=Decimal("1"),
        penetration_atr=None,
        reclaim_close=Decimal("101"),
        volatility=SimpleNamespace(),
        volume=SimpleNamespace(),
        parameters=SimpleNamespace(),
    )


def _candle(hour: int) -> SimpleNamespace:
    return SimpleNamespace(timestamp=_open(hour))


def _setup(
    *,
    setup_id: str,
    seed_event_id: str,
    reference_id: str,
    state: SetupState = SetupState.QUALIFIED,
) -> SetupResult:
    return SetupResult(
        id=setup_id,
        family="breakout_retest_continuation",  # type: ignore[arg-type]
        direction="bullish",  # type: ignore[arg-type]
        state=state,
        seed_event_id=seed_event_id,
        reference_id=reference_id,
        created_at=_open(30),
        as_of=DECISION,
        source_timeframes=(TIMEFRAME,),
        rules=(),
    )


def _frame(
    *,
    events: tuple[object, ...],
    swings: tuple[SimpleNamespace, ...],
    left_window: int = 2,
    lookback_swings: int = 40,
    equal_lookback_swings: int = 40,
    window_start: datetime | None = None,
) -> SimpleNamespace:
    """A Step 3/4 frame exposing exactly the attributes the gate reads."""

    structure = SimpleNamespace(
        confirmed_swings=swings,
        window_start_timestamp=EPOCH if window_start is None else window_start,
        parameters=SimpleNamespace(
            swings=SimpleNamespace(left_window=left_window, right_window=2),
            levels=SimpleNamespace(lookback_swings=lookback_swings),
        ),
    )
    patterns = SimpleNamespace(
        structure=structure,
        parameters=SimpleNamespace(equal_lookback_swings=equal_lookback_swings),
        events=lambda: events,
    )
    return SimpleNamespace(patterns=patterns)


def _snapshot(setups: tuple[SetupResult, ...]) -> QualificationSnapshot:
    return QualificationSnapshot(
        exchange=EXCHANGE,
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        as_of=DECISION,
        state=SetupState.QUALIFIED,
        setups=setups,
        reasons=(),
        config_fingerprint="synthetic",
        rules_version="synthetic",
        source_timeframes=(TIMEFRAME,),
        status="evaluated",
    )


def _assess(
    *,
    frame: SimpleNamespace | None,
    setups: tuple[SetupResult, ...],
    first_stored_hour: int | None = 0,
) -> object:
    return assess_reference_provenance(
        exchange=EXCHANGE,
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        decision_time=DECISION,
        frame=frame,
        snapshot=_snapshot(setups) if frame is not None else None,
        first_stored_open=None if first_stored_hour is None else _open(first_stored_hour),
    )


# ---------------------------------------------------------------------------
# The verdict itself
# ---------------------------------------------------------------------------


def test_a_level_first_evidenced_at_the_start_of_history_is_truncated() -> None:
    """A swing with no stored lead-in was already forming when the record began."""

    # Stored history starts at hour 5; the level's only candle is hour 5, so its
    # two-candle left window is not stored: provenance cannot be established.
    reference = _reference(
        reference_id="ref-edge", kind="swing_high", swing_hours=(5,)
    )
    events = (_breakout(event_id="evt-1", hour=30, reference=reference),)
    setups = (
        _setup(setup_id="setup-edge", seed_event_id="evt-1", reference_id="ref-edge"),
    )
    frame = _frame(events=events, swings=(_swing(5), _swing(20), _swing(30)))

    assessment = _assess(frame=frame, setups=setups, first_stored_hour=5)

    assert assessment.status is ProvenanceStatus.TRUNCATED
    assert assessment.blocked_setup_ids == frozenset({"setup-edge"})
    (entry,) = assessment.setups
    assert entry.status is ProvenanceStatus.TRUNCATED
    assert entry.blocked is True
    assert entry.oldest_evidence_open == _open(5)
    assert entry.earliest_accountable_open == _open(7)  # left_window = 2 closes
    assert "already forming" in entry.reasons[0]


def test_a_level_with_its_full_left_window_stored_is_accountable() -> None:
    """The boundary case: the lead-in is stored exactly, so nothing is withheld."""

    reference = _reference(
        reference_id="ref-ok", kind="swing_low", swing_hours=(7,)
    )
    events = (_sweep(event_id="evt-2", hour=30, reference=reference),)
    setups = (_setup(setup_id="setup-ok", seed_event_id="evt-2", reference_id="ref-ok"),)
    frame = _frame(events=events, swings=(_swing(7), _swing(20), _swing(30)))

    assessment = _assess(frame=frame, setups=setups, first_stored_hour=5)

    assert assessment.status is ProvenanceStatus.COMPLETE
    assert assessment.blocked_setup_ids == frozenset()
    (entry,) = assessment.setups
    assert entry.status is ProvenanceStatus.COMPLETE
    assert entry.reasons == ()


def test_an_older_touch_of_the_same_level_is_part_of_its_provenance() -> None:
    """The level's evidence record accumulates, so the oldest touch decides.

    The seed event is recent (hour 30) and the swing sits well inside stored
    history, yet the same level was already being tested at hour 5 — the first
    stored candle. Provenance reaches past the start of the record.
    """

    reference = _reference(reference_id="ref-old", kind="swing_high", swing_hours=(9,))
    events = (
        _breakout(event_id="evt-first", hour=5, reference=reference),
        _breakout(event_id="evt-later", hour=30, reference=reference),
    )
    setups = (_setup(setup_id="setup-x", seed_event_id="evt-later", reference_id="ref-old"),)
    frame = _frame(events=events, swings=(_swing(9), _swing(20), _swing(30)))

    assessment = _assess(frame=frame, setups=setups, first_stored_hour=5)

    assert assessment.status is ProvenanceStatus.TRUNCATED
    (entry,) = assessment.setups
    assert entry.oldest_evidence_open == _open(5)


def test_a_zone_level_is_accountable_once_it_is_observed_inside_the_record() -> None:
    """A clustered zone needs no lead-in of its own: its observation is the record.

    Its first observation is one candle after the stored start, which the gate
    accepts; a zone already observed *by* the first stored candle would not be.
    """

    reference = _reference(
        reference_id="ref-zone",
        kind="zone",
        swing_hours=(7, 12),
        zone_first_hour=6,
        zone_member_hours=(7, 12),
    )
    events = (_breakout(event_id="evt-zone", hour=30, reference=reference),)
    setups = (_setup(setup_id="setup-zone", seed_event_id="evt-zone", reference_id="ref-zone"),)
    frame = _frame(events=events, swings=(_swing(7), _swing(12), _swing(30)))

    assessment = _assess(frame=frame, setups=setups, first_stored_hour=5)

    assert assessment.status is ProvenanceStatus.COMPLETE
    (entry,) = assessment.setups
    # Evidence at the very first stored candle would already prove a level older
    # than the record, so accountability starts one candle later.
    assert entry.earliest_accountable_open == _open(6)


def test_a_zone_already_observed_by_the_first_stored_candle_is_truncated() -> None:
    reference = _reference(
        reference_id="ref-zone-edge",
        kind="zone",
        swing_hours=(5, 12),
        zone_first_hour=5,
        zone_member_hours=(5, 12),
    )
    events = (_breakout(event_id="evt-zone-edge", hour=30, reference=reference),)
    setups = (
        _setup(setup_id="setup-zone-edge", seed_event_id="evt-zone-edge", reference_id="ref-zone-edge"),
    )
    frame = _frame(events=events, swings=(_swing(5), _swing(12), _swing(30)))

    assessment = _assess(frame=frame, setups=setups, first_stored_hour=5)

    assert assessment.status is ProvenanceStatus.TRUNCATED
    assert assessment.blocked_setup_ids == frozenset({"setup-zone-edge"})


def test_the_gate_follows_the_parameters_instead_of_a_hardcoded_depth() -> None:
    """A deeper configured left window makes an otherwise sound level unverifiable."""

    reference = _reference(reference_id="ref-param", kind="swing_high", swing_hours=(7,))
    events = (_breakout(event_id="evt-param", hour=30, reference=reference),)
    setups = (_setup(setup_id="setup-p", seed_event_id="evt-param", reference_id="ref-param"),)
    swings = (_swing(7), _swing(20), _swing(30))
    tight = _frame(events=events, swings=swings, left_window=2)
    wide = _frame(events=events, swings=swings, left_window=5)

    assert _assess(frame=tight, setups=setups, first_stored_hour=5).status is (
        ProvenanceStatus.COMPLETE
    )
    truncated = _assess(frame=wide, setups=setups, first_stored_hour=5)
    assert truncated.status is ProvenanceStatus.TRUNCATED
    assert truncated.blocked_setup_ids == frozenset({"setup-p"})


def test_only_the_affected_setups_are_blocked() -> None:
    edge = _reference(reference_id="ref-edge2", kind="swing_high", swing_hours=(5,))
    sound = _reference(reference_id="ref-sound", kind="swing_high", swing_hours=(20,))
    events = (
        _breakout(event_id="evt-edge2", hour=30, reference=edge),
        _breakout(event_id="evt-sound", hour=31, reference=sound),
    )
    # Stored history starts at hour 4: the level first evidenced at hour 5 has no
    # left window stored, while the one at hour 20 does.
    setups = (
        _setup(setup_id="setup-blocked", seed_event_id="evt-edge2", reference_id="ref-edge2"),
        _setup(
            setup_id="setup-allowed",
            seed_event_id="evt-sound",
            reference_id="ref-sound",
            state=SetupState.WATCH,
        ),
    )
    frame = _frame(events=events, swings=(_swing(5), _swing(20), _swing(30)))

    assessment = _assess(frame=frame, setups=setups, first_stored_hour=4)

    assert assessment.status is ProvenanceStatus.TRUNCATED
    assert assessment.blocked_setup_ids == frozenset({"setup-blocked"})
    assert assessment.blocks(setups[0]) is True
    assert assessment.blocks(setups[1]) is False
    assert "1 of 2 live setup(s)" in assessment.summary


def test_setups_that_are_not_live_are_not_assessed() -> None:
    reference = _reference(reference_id="ref-term", kind="swing_high", swing_hours=(5,))
    events = (_breakout(event_id="evt-term", hour=30, reference=reference),)
    setups = (
        _setup(
            setup_id="setup-terminal",
            seed_event_id="evt-term",
            reference_id="ref-term",
            state=SetupState.NO_SETUP,
        ),
    )
    frame = _frame(events=events, swings=(_swing(5), _swing(30)))

    assessment = _assess(frame=frame, setups=setups, first_stored_hour=0)

    assert assessment.setups == ()
    assert assessment.status is ProvenanceStatus.COMPLETE


def test_an_unresolvable_seed_event_withholds_rather_than_assumes() -> None:
    reference = _reference(reference_id="ref-missing", kind="swing_high", swing_hours=(20,))
    events = (_breakout(event_id="evt-present", hour=30, reference=reference),)
    setups = (
        _setup(setup_id="setup-orphan", seed_event_id="evt-absent", reference_id="ref-missing"),
    )
    frame = _frame(events=events, swings=(_swing(20), _swing(30)))

    assessment = _assess(frame=frame, setups=setups, first_stored_hour=0)

    (entry,) = assessment.setups
    assert entry.status is ProvenanceStatus.UNAVAILABLE
    assert "cannot be verified" in entry.reasons[0]
    # Unverifiable is not verified: the plan is withheld and the boundary is not
    # reported as complete.
    assert assessment.blocked_setup_ids == frozenset({"setup-orphan"})
    assert assessment.status is ProvenanceStatus.TRUNCATED


@pytest.mark.parametrize(
    ("frame", "first_stored_hour"),
    [(None, 0), (_frame(events=(), swings=()), None)],
    ids=["no frame", "no stored candle"],
)
def test_absence_of_evidence_is_reported_as_unavailable(
    frame: object | None, first_stored_hour: int | None
) -> None:
    assessment = _assess(frame=frame, setups=(), first_stored_hour=first_stored_hour)

    assert assessment.status is ProvenanceStatus.UNAVAILABLE
    assert assessment.complete is False
    assert assessment.blocked_setup_ids == frozenset()
    assert assessment.setups == ()


def test_a_thin_level_inventory_is_reported_but_does_not_block() -> None:
    """Under-observation is surfaced, not silently treated as a settled no-trade.

    With fewer confirmed swings than the clustering lookback, and the first swing
    sitting at the very start of the record, the run cannot know which older
    levels it never saw. That limit is stated; it does not withhold a plan whose
    own level is accounted for.
    """

    reference = _reference(reference_id="ref-thin", kind="swing_high", swing_hours=(20,))
    events = (_breakout(event_id="evt-thin", hour=30, reference=reference),)
    setups = (_setup(setup_id="setup-thin", seed_event_id="evt-thin", reference_id="ref-thin"),)
    frame = _frame(
        events=events,
        swings=(_swing(5), _swing(20), _swing(30)),
        lookback_swings=40,
    )

    assessment = _assess(frame=frame, setups=setups, first_stored_hour=5)

    assert assessment.status is ProvenanceStatus.COMPLETE
    assert assessment.swing_count == 3
    assert assessment.level_lookback_swings == 40
    assert assessment.inventory_at_history_start is True
    assert len(assessment.advisory_reasons) == 2
    assert any("bounded by the start of stored history" in r for r in assessment.advisory_reasons)
    assert any("not a settled no-trade verdict" in r for r in assessment.advisory_reasons)


def test_the_serialised_verdict_is_exact_and_self_describing() -> None:
    reference = _reference(reference_id="ref-json", kind="swing_high", swing_hours=(5,))
    events = (_breakout(event_id="evt-json", hour=30, reference=reference),)
    setups = (_setup(setup_id="setup-json", seed_event_id="evt-json", reference_id="ref-json"),)
    frame = _frame(events=events, swings=(_swing(5), _swing(30)))

    payload = _assess(frame=frame, setups=setups, first_stored_hour=5).to_json_dict()

    assert payload["rules_version"] == REFERENCE_PROVENANCE_RULES_VERSION
    assert payload["decision_time"] == DECISION.isoformat().replace("+00:00", "Z")
    assert payload["status"] == "TRUNCATED"
    assert payload["blocked_setup_ids"] == ["setup-json"]
    assert payload["first_stored_open"].endswith("Z")
    assert payload["swing_left_window"] == 2
    assert payload["setups"][0]["reference_type"] == "swing_high"
    assert payload["setups"][0]["blocked"] is True


# ---------------------------------------------------------------------------
# The forward runner: withholding, reasoning, and no invented paper trade
# ---------------------------------------------------------------------------


def _block_all_setups(module: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Force every live setup at the assessed close to be provenance-truncated.

    The seam is the assessment function itself, so the production decision path
    (planning, paper-trade creation, reason recording, cycle notes) runs
    unchanged against a verdict it cannot distinguish from a real one.
    """

    import importlib

    target = importlib.import_module(module)
    original = target.assess_reference_provenance

    def _assessed(*args, **kwargs):  # type: ignore[no-untyped-def]
        assessment = original(*args, **kwargs)
        blocked = tuple(
            replace(entry, status=ProvenanceStatus.TRUNCATED, reasons=("forced by test",))
            for entry in assessment.setups
        )
        return replace(assessment, setups=blocked, status=ProvenanceStatus.TRUNCATED)

    monkeypatch.setattr(target, "assess_reference_provenance", _assessed)


def test_forward_planning_proceeds_when_the_record_accounts_for_its_levels() -> None:
    """Regression guard for the fixture BRAIN actually plans on today."""

    harness = make_harness(series=labelled_series())
    try:
        harness.advance_to(QUALIFYING_BOUNDARY)
        result = harness.run(refresh_market_data=False)
        assert result.paper_plans_created == 1
        plans = harness.plans()
        assert len(plans) == 1
        qualified = [o for o in harness.observations() if o.setup_state is SetupState.QUALIFIED]
        assert qualified and all(o.paper_plan_id is not None or o.no_trade_reason == PAPER_TRADE_ACTIVE_REASON for o in qualified)
        (cycle,) = harness.cycles()
        assert not any("provenance" in note.lower() for note in cycle.notes)
        versions = dict(cycle.strategy_versions)
        assert versions["reference_provenance_rules"] == REFERENCE_PROVENANCE_RULES_VERSION
    finally:
        harness.engine.dispose()


def test_forward_withholds_planning_and_records_the_reason(monkeypatch) -> None:
    _block_all_setups("trading_assistant.forward_testing.service", monkeypatch)
    harness = make_harness(series=labelled_series())
    try:
        harness.advance_to(QUALIFYING_BOUNDARY)
        result = harness.run(refresh_market_data=False)

        assert result.paper_plans_created == 0
        assert list(harness.plans()) == []
        qualified = [o for o in harness.observations() if o.setup_state is SetupState.QUALIFIED]
        assert qualified, "fixture must still produce a qualified candidate to withhold"
        for observation in qualified:
            assert observation.plan_state is None
            assert observation.plan_id is None
            assert observation.paper_plan_id is None
            assert observation.no_trade_reason == REFERENCE_PROVENANCE_REASON
            # Never reclassified as a market verdict or as a missed opportunity.
            assert observation.no_trade_reason != MISSED_OPPORTUNITY_REASON
            assert observation.no_trade_reason != PAPER_TRADE_ACTIVE_REASON

        (cycle,) = harness.cycles()
        # The close still reaches a conclusion and the candidate is still
        # recorded: only the paper trade is withheld.
        assert cycle.status.value == "COMPLETE"
        assert cycle.observation_count == 6
        assert any(
            "planning withheld because their reference level is first evidenced at "
            "the start of stored history" in note
            for note in cycle.notes
        ), cycle.notes
        assert not any("incomplete" in note for note in cycle.notes)
    finally:
        harness.engine.dispose()


def test_forward_reason_is_stored_exactly_and_never_overwrites_a_plan(monkeypatch) -> None:
    """A withheld candidate leaves the paper ledger empty, not partially filled."""

    _block_all_setups("trading_assistant.forward_testing.service", monkeypatch)
    harness = make_harness(series=labelled_series())
    try:
        harness.advance_to(QUALIFYING_BOUNDARY)
        harness.run(refresh_market_data=False)
        assert harness.counts()["paper_plans"] == 0
        assert not list(harness.latest_outcomes())
        reasons = {o.no_trade_reason for o in harness.observations()}
        assert REFERENCE_PROVENANCE_REASON in reasons
    finally:
        harness.engine.dispose()


# ---------------------------------------------------------------------------
# The dashboard: the same verdict, the same refusal, visible to the operator
# ---------------------------------------------------------------------------


def _dashboard_client(tmp_path, name: str, *, block: bool, monkeypatch: pytest.MonkeyPatch):
    from web_fixtures import insert_candles, make_client, make_settings, migrated_engine, qualified_clock
    from web_fixtures import qualifying_candles

    if block:
        _block_all_setups("trading_assistant.web.dashboard_service", monkeypatch)
    engine, url = migrated_engine(tmp_path, name)
    insert_candles(engine, qualifying_candles())
    return engine, make_client(engine, make_settings(url), clock=qualified_clock())


def test_dashboard_reports_the_provenance_verdict_next_to_the_plan(tmp_path, monkeypatch) -> None:
    engine, client = _dashboard_client(tmp_path, "prov-ok.sqlite3", block=False, monkeypatch=monkeypatch)
    try:
        payload = client.get("/api/dashboard").json()
        verdict = payload["reference_provenance"]
        assert verdict["rules_version"] == REFERENCE_PROVENANCE_RULES_VERSION
        assert verdict["status"] == "COMPLETE"
        assert verdict["blocked_setup_ids"] == []
        assert verdict["first_stored_open"] == "2024-01-01T00:00:00Z"
        assert payload["plan"] is not None
        assert payload["planning"]["state"] is not None
        assert not any("provenance" in reason for reason in payload["planning"]["reasons"])
    finally:
        engine.dispose()


def test_dashboard_withholds_a_plan_whose_level_provenance_is_unverified(
    tmp_path, monkeypatch
) -> None:
    engine, client = _dashboard_client(tmp_path, "prov-block.sqlite3", block=True, monkeypatch=monkeypatch)
    try:
        payload = client.get("/api/dashboard").json()
        assert payload["reference_provenance"]["status"] == "TRUNCATED"
        assert payload["reference_provenance"]["blocked_setup_ids"]
        assert payload["plan"] is None
        assert payload["planning"]["state"] is None
        (reason,) = payload["planning"]["reasons"]
        assert reason.startswith("planning withheld:")
        assert "forced by test" in reason
        assert payload["journal"]["can_decide"] is False
        assert payload["journal"]["disabled_reason"].startswith("planning withheld:")
    finally:
        engine.dispose()


def test_decision_on_a_withheld_proposal_is_refused(tmp_path, monkeypatch) -> None:
    engine, client = _dashboard_client(tmp_path, "prov-decide.sqlite3", block=True, monkeypatch=monkeypatch)
    try:
        dashboard = client.get("/api/dashboard").json()
        response = client.post(
            "/api/dashboard/decisions",
            json={
                "decision": "ACCEPTED",
                "as_of": dashboard["meta"]["as_of"],
                "setup_id": dashboard["qualification"]["selected_setup_id"],
            },
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "proposal_not_decidable"
        assert "planning withheld" in response.json()["error"]["message"]
    finally:
        engine.dispose()
