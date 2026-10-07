"""Deterministic, read-only trade planning over one Step 5 QUALIFIED setup.

``plan_trade`` consumes an existing ``QualificationSnapshot``, the exact
``QualificationFrame`` at that same ``as_of``, and a setup ID. It never replays,
re-qualifies, re-detects, mutates its inputs, or writes anything. A plan is a
reproducible immutable snapshot at its planning ``as_of``: to obtain a
*current* plan, callers must obtain a current Step 5 snapshot and plan against
it; an old plan is retained history, never a live signal.

Trading policy (deterministic and mandatory):

* Entry is the actual decision-time price (the latest closed close at ``as_of``).
  An earlier, frozen, or otherwise better price is never substituted, so a
  setup whose move already happened cannot be chased through an old entry.
* The stop is the structural invalidation plus the configured buffer. It is
  never tightened to manufacture an acceptable reward-to-risk.
* Targets are genuine structural levels only, and at least one must reach the
  mandatory ``min_r_multiple`` floor (default 1R). A structurally targetless
  setup is refused; no synthetic R target can rescue it.
* ``preferred_r_multiple`` is a classification recorded on the plan; it never
  blocks a plan that clears the mandatory floor.

State vocabulary is fixed and documented. ``reasons`` carries machine-readable
codes. NO_PLAN means a required fact is missing/UNKNOWN, the source setup is
not currently usable, or no genuine structural target reaches the mandatory
reward-to-risk floor, so nothing may be guessed through the gap. INVALID means
inputs were present but a derived number violates a hard invariant. State
priority is INVALID > NO_PLAN so contradictions are never downgraded into a
mere gap, and offending numbers are reported but never silently corrected.
"""

from datetime import datetime, timedelta
from decimal import Decimal

from trading_assistant.market_data.timeframes import require_utc_datetime
from trading_assistant.market_structure.candles import interval_for_timeframe
from trading_assistant.market_structure.numeric import divide, quantize_derived
from trading_assistant.pattern_liquidity.events import (
    Breakout,
    Direction,
    FailedBreakout,
    Reference,
    Retest,
    Sweep,
)
from trading_assistant.pattern_liquidity.snapshot import PatternLiquiditySnapshot
from trading_assistant.setup_qualification.families import (
    direction_for,
    family_for,
    reference_for,
)
from trading_assistant.setup_qualification.models import (
    QualificationFrame,
    QualificationSnapshot,
    RuleOutcome,
    SetupFamily,
    SetupResult,
    SetupState,
)
from trading_assistant.trade_planning.levels import (
    find_seed,
    is_available_at,
    is_long,
    plan_close_level,
    reference_observed_at,
    select_confirmation,
    stop_buffer,
    structural_target_candidates,
    usable_level,
)
from trading_assistant.trade_planning.models import (
    UNKNOWN_LEVEL,
    PlannedLevel,
    PlannedTarget,
    PlanningRuleResult,
    PlanState,
    TradePlanResult,
)
from trading_assistant.trade_planning.parameters import (
    PLANNING_RULES_VERSION,
    PlanningParameters,
    decimal_text,
    fingerprint,
)

#: Codes that mark a contradiction between present inputs (INVALID), rather
#: than an absent input (NO_PLAN).
INVALID_CODES = frozenset(
    {
        "instrument_mismatch",
        "frame_snapshot_asof_mismatch",
        "reference_id_mismatch",
        "family_mismatch",
        "direction_mismatch",
        "setup_identity_mismatch",
        "future_evidence_used",
        "frozen_range_evidence_missing",
        "active_range_mismatch",
        "range_followthrough_failed",
        "entry_level_not_usable",
        "invalid_reference_band",
        "entry_not_on_trade_side",
        "range_entry_outside_frozen_bounds",
        "stop_non_positive",
        "stop_not_beyond_entry",
        "non_positive_risk",
    }
)

#: Reason code recorded when no genuine structural target reaches the mandatory
#: reward-to-risk floor. This is a refusal (NO_PLAN), never an invalid plan, and
#: it is public because downstream layers recognise exactly this refusal (the
#: decision-time entry has moved away from the opportunity) rather than matching
#: free text.
MINIMUM_R_MULTIPLE_NOT_MET = "minimum_r_multiple_not_met"

#: Exact planning rules in evaluation order. ``minimum_r_multiple`` is a base
#: rule: the mandatory reward-to-risk floor is always evaluated.
BASE_RULES = (
    "source_inputs_consistent",
    "setup_usable",
    "seed_evidence_available",
    "frozen_identity_consistent",
    "availability_at_as_of",
    "family_confirmation_available",
    "family_state_reverified",
    "entry_level_available",
    "reference_band_available",
    "entry_on_trade_side",
    "stop_buffer_inputs_available",
    "stop_beyond_entry",
    "positive_risk",
    "target_levels_valid",
    "minimum_r_multiple",
)


class _PlanContext:
    """Mutable per-call accumulator; the final TradePlanResult stays frozen."""

    def __init__(self, parameters: PlanningParameters) -> None:
        self.parameters = parameters
        self.rules: list[PlanningRuleResult] = []
        self.reasons: list[str] = []
        self.missing_inputs: list[str] = []
        self.excluded_targets: list[str] = []
        self.recorded: set[str] = set()
        self.entry = UNKNOWN_LEVEL
        self.invalidation = UNKNOWN_LEVEL
        self.stop = UNKNOWN_LEVEL
        self.risk_per_unit: Decimal | None = None
        self.preferred_r_multiple_met: bool | None = None
        self.targets: tuple[PlannedTarget, ...] = ()
        self.setup: SetupResult | None = None
        self.requested_setup_id: str | None = None
        self.gap_seen = False

    def record(self, rule_id: str, outcome: RuleOutcome, reason: str) -> None:
        self.recorded.add(rule_id)
        self.rules.append(PlanningRuleResult(rule_id, outcome, reason))

    def fail(self, code: str, rule_id: str, detail: str) -> None:
        self.reasons.append(code)
        self.record(rule_id, RuleOutcome.FAIL, detail)

    def gap(
        self, code: str, rule_id: str, detail: str, *, input_name: str | None = None
    ) -> None:
        if input_name is not None:
            self.missing_inputs.append(input_name)
        self.fail(code, rule_id, detail)
        self.gap_seen = True

    def finalize(
        self,
        *,
        snapshot: QualificationSnapshot,
        instrument: tuple[str, str, str],
        as_of: datetime,
    ) -> TradePlanResult:
        expected = list(BASE_RULES)
        pending_reason = (
            f"not evaluated; blocking outcome: {self.reasons[0]}"
            if self.reasons
            else "not evaluated"
        )
        for rule_id in expected:
            if rule_id not in self.recorded:
                self.rules.append(
                    PlanningRuleResult(rule_id, RuleOutcome.PENDING, pending_reason)
                )
        state = (
            PlanState.PLANNABLE
            if not self.reasons
            else PlanState.INVALID
            if any(code in INVALID_CODES for code in self.reasons)
            else PlanState.NO_PLAN
        )
        setup = self.setup
        fields = {
            "state": state,
            "exchange": instrument[0],
            "symbol": instrument[1],
            "timeframe": instrument[2],
            "source_timeframes": snapshot.source_timeframes,
            "setup_id": setup.id if setup is not None else self.requested_setup_id,
            "family": setup.family if setup is not None else None,
            "direction": setup.direction if setup is not None else None,
            "setup_created_at": setup.created_at if setup is not None else None,
            "setup_as_of": setup.as_of if setup is not None else None,
            "as_of": as_of,
            "state_detail": self.reasons[0] if self.reasons else None,
            "entry": self.entry,
            "invalidation": self.invalidation,
            "stop": self.stop,
            "risk_per_unit": self.risk_per_unit,
            "preferred_r_multiple_met": self.preferred_r_multiple_met,
            "targets": self.targets,
            "rules": tuple(self.rules),
            "reasons": tuple(self.reasons),
            "missing_inputs": tuple(self.missing_inputs),
            "excluded_targets": tuple(self.excluded_targets),
            "setup_config_fingerprint": snapshot.config_fingerprint,
            "config_fingerprint": self.parameters.fingerprint(),
            "planning_rules_version": PLANNING_RULES_VERSION,
        }
        fields["id"] = fingerprint("plan-identity", fields)
        return TradePlanResult(**fields)


def plan_trade(
    *,
    snapshot: QualificationSnapshot,
    frame: QualificationFrame,
    setup_id: str,
    parameters: PlanningParameters | None = None,
) -> TradePlanResult:
    """Produce the deterministic plan (or explicit refusal) for one setup.

    Raises ``TypeError``/``ValueError`` only for argument misuse; every domain
    gap or contradiction is reported inside the returned result instead.
    """
    if not isinstance(snapshot, QualificationSnapshot):
        raise TypeError("snapshot must be a Step 5 QualificationSnapshot")
    if not isinstance(frame, QualificationFrame):
        raise TypeError("frame must be a Step 5 QualificationFrame")
    if not isinstance(parameters, PlanningParameters | None):
        raise TypeError("parameters must be PlanningParameters or None")
    if not isinstance(setup_id, str) or not setup_id.strip():
        raise ValueError("setup_id must be a non-empty string")
    p = parameters or PlanningParameters()

    patterns = frame.patterns
    instrument = (patterns.exchange, patterns.symbol, patterns.timeframe)
    interval = interval_for_timeframe(patterns.timeframe)
    frame_as_of = require_utc_datetime(patterns.as_of, field_name="frame.as_of")
    as_of = require_utc_datetime(snapshot.as_of, field_name="snapshot.as_of")

    context = _PlanContext(p)
    context.requested_setup_id = setup_id

    # Rule 1: snapshot, frame, and instrument must describe the same instant.
    if (snapshot.exchange, snapshot.symbol, snapshot.timeframe) != instrument:
        context.fail(
            "instrument_mismatch",
            "source_inputs_consistent",
            f"snapshot {snapshot.exchange}/{snapshot.symbol}/{snapshot.timeframe} "
            f"does not match frame {instrument[0]}/{instrument[1]}/{instrument[2]}",
        )
    elif frame_as_of != as_of:
        context.fail(
            "frame_snapshot_asof_mismatch",
            "source_inputs_consistent",
            f"frame as_of {frame_as_of} differs from snapshot as_of {as_of}; "
            "planning requires the exact frame evaluated at the snapshot instant",
        )
    else:
        context.record(
            "source_inputs_consistent",
            RuleOutcome.PASS,
            f"snapshot and frame agree on instrument and as_of {as_of}",
        )

    # Rules 2-15: derive only from the current, consistent frame.
    if not context.reasons:
        _check_setup_usable(context, snapshot=snapshot, setup_id=setup_id, as_of=as_of)
        if not context.reasons:
            _plan_from_evidence(
                context, patterns=patterns, interval=interval, as_of=as_of
            )
    return context.finalize(snapshot=snapshot, instrument=instrument, as_of=as_of)


def _check_setup_usable(
    context: _PlanContext,
    *,
    snapshot: QualificationSnapshot,
    setup_id: str,
    as_of: datetime,
) -> None:
    setup = next((s for s in snapshot.setups if s.id == setup_id), None)
    context.setup = setup
    if setup is None:
        context.gap(
            "setup_not_in_snapshot",
            "setup_usable",
            f"setup {setup_id} is not present in the snapshot at {as_of}",
            input_name="step5_setup",
        )
    elif setup.terminal_reason is not None:
        context.gap(
            "terminal_source_setup",
            "setup_usable",
            f"source setup is terminally invalidated: {setup.terminal_reason} "
            f"at {setup.ended_at}; expired, invalidated, and NO_SETUP states "
            "never produce actionable plans",
        )
    elif setup.state is not SetupState.QUALIFIED:
        context.gap(
            "setup_not_qualified",
            "setup_usable",
            f"source setup state is {setup.state}; only QUALIFIED setups plan, "
            "and QUALIFIED never implies PLANNABLE by itself",
        )
    elif setup.as_of != as_of:
        context.gap(
            "stale_qualification",
            "setup_usable",
            f"setup as_of {setup.as_of} is not the snapshot as_of {as_of}; an "
            "old QUALIFIED snapshot is never reused as though current",
        )
    else:
        context.record(
            "setup_usable",
            RuleOutcome.PASS,
            f"setup {setup.id} is QUALIFIED at {setup.as_of}; seed "
            f"{setup.seed_event_id} created {setup.created_at}",
        )


def _plan_from_evidence(
    context: _PlanContext,
    *,
    patterns: PatternLiquiditySnapshot,
    interval: timedelta,
    as_of: datetime,
) -> None:
    setup = context.setup
    assert setup is not None  # established by the setup_usable PASS above

    # Rule 3: the seed event and its frozen reference must exist in this frame.
    seed = find_seed(patterns, setup.seed_event_id)
    if seed is None:
        context.gap(
            "seed_event_missing",
            "seed_evidence_available",
            f"seed event {setup.seed_event_id} is absent from the frame catalog "
            "or is not a Breakout/FailedBreakout/Sweep",
            input_name="step4_seed_event",
        )
        return
    reference = reference_for(seed)
    if reference.id != setup.reference_id:
        context.fail(
            "reference_id_mismatch",
            "seed_evidence_available",
            f"seed reference {reference.id} does not match the setup's frozen "
            f"reference {setup.reference_id}",
        )
        return
    context.record(
        "seed_evidence_available",
        RuleOutcome.PASS,
        f"seed {seed.id} ({type(seed).__name__}) with frozen reference "
        f"{reference.id} present at {as_of}",
    )

    # Rule 4: family and direction must match the upstream seed routing.
    if family_for(seed).family != setup.family:
        context.fail(
            "family_mismatch",
            "frozen_identity_consistent",
            f"seed routes to family {family_for(seed).family}; setup records "
            f"{setup.family}",
        )
        return
    if direction_for(seed) != setup.direction:
        context.fail(
            "direction_mismatch",
            "frozen_identity_consistent",
            f"seed direction is {direction_for(seed)}; setup records {setup.direction}",
        )
        return
    if setup.created_at != seed.known_at:
        context.fail(
            "setup_identity_mismatch",
            "frozen_identity_consistent",
            f"setup created_at {setup.created_at} differs from the seed "
            f"known_at {seed.known_at}",
        )
        return
    context.record(
        "frozen_identity_consistent",
        RuleOutcome.PASS,
        f"family {setup.family.value} and direction {setup.direction} match the "
        "frozen seed and its recorded creation time",
    )

    # Rule 5: nothing used may post-date the planning instant (anti-lookahead).
    confirmation = select_confirmation(patterns, seed, setup.family, setup.direction)
    violations = _future_violations(seed, setup, confirmation, as_of, interval)
    if violations:
        context.fail(
            "future_evidence_used", "availability_at_as_of", "; ".join(violations)
        )
        return
    context.record(
        "availability_at_as_of",
        RuleOutcome.PASS,
        "seed, frozen reference, confirmation, and setup timestamps are all at "
        f"or before {as_of}",
    )

    # Rule 6: the frozen family confirmation must be locatable in the catalog.
    if not _check_confirmation(context, setup, confirmation):
        return

    # Rule 7: the family state facts Step 5 relied on must still reconcile.
    if not _reverify_family_state(context, patterns, setup, seed, reference):
        return

    # Rule 8: resolve the entry level from the configured entry rule.
    entry_value = _resolve_entry(context, patterns, interval, as_of=as_of)
    if entry_value is None:
        return

    # Rules 9-10: logical invalidation from the frozen band; entry side check.
    invalidation_value = _resolve_invalidation_and_side(
        context, patterns, setup, reference, entry_value
    )
    if invalidation_value is None:
        return

    # Rules 11-13: buffered stop and strictly positive, correctly sided risk.
    _resolve_stop(context, patterns, setup, reference, entry_value, invalidation_value)
    if context.gap_seen:
        return  # dependent rules stay PENDING; one gap, one cause

    # Rules 14-15: targets, then the optional minimum-R policy.
    _select_targets(context, patterns, setup, entry_value, as_of)
    if context.gap_seen:
        return
    _apply_minimum_r(context)


def _check_confirmation(
    context: _PlanContext,
    setup: SetupResult,
    confirmation: Breakout | Retest | None,
) -> bool:
    if setup.family is SetupFamily.BREAKOUT_RETEST:
        if confirmation is None:
            context.gap(
                "confirmation_event_missing",
                "family_confirmation_available",
                "no held Step 4 retest of the seed breakout after the seed "
                "exists in this frame",
                input_name="step4_retest(held)",
            )
            return False
        context.record(
            "family_confirmation_available",
            RuleOutcome.PASS,
            f"held retest {confirmation.id} confirmed {confirmation.known_at}",
        )
        return True
    if setup.family is SetupFamily.LIQUIDITY_REVERSAL:
        if confirmation is None:
            context.gap(
                "confirmation_event_missing",
                "family_confirmation_available",
                "no later directional Step 4 breakout at a different reference "
                "exists in this frame",
                input_name="step4_confirmation_breakout",
            )
            return False
        context.record(
            "family_confirmation_available",
            RuleOutcome.PASS,
            f"reversal confirmation breakout {confirmation.id} confirmed "
            f"{confirmation.known_at}",
        )
        return True
    context.record(
        "family_confirmation_available",
        RuleOutcome.PASS,
        "range reversal carries its confirmation inside the seed event and "
        "current context; re-verified by family_state_reverified",
    )
    return True


def _reverify_family_state(
    context: _PlanContext,
    patterns: PatternLiquiditySnapshot,
    setup: SetupResult,
    seed: Breakout | FailedBreakout | Sweep,
    reference: Reference,
) -> bool:
    if setup.family is not SetupFamily.RANGE_REVERSAL:
        context.record(
            "family_state_reverified",
            RuleOutcome.PASS,
            "confirmation selection exactly mirrors the Step 5 rule predicates; "
            "no additional family context state is required",
        )
        return True
    frozen = reference.range
    if frozen is None:
        context.fail(
            "frozen_range_evidence_missing",
            "family_state_reverified",
            "a range-family seed reference must carry its frozen range bounds",
        )
        return False
    active = patterns.structure.active_range
    if (
        active is None
        or active.range_low != frozen.range_low
        or active.range_high != frozen.range_high
    ):
        context.fail(
            "active_range_mismatch",
            "family_state_reverified",
            f"current active range "
            f"{None if active is None else (active.range_low, active.range_high)} "
            f"does not equal the frozen seed bounds "
            f"({decimal_text(frozen.range_low)}, {decimal_text(frozen.range_high)})",
        )
        return False
    current_close = patterns.structure.volatility.latest_close
    if not isinstance(current_close, Decimal):
        context.gap(
            "current_close_unavailable",
            "family_state_reverified",
            "the latest close is unknown, so range follow-through cannot be "
            "re-verified from source facts",
            input_name="structure.volatility.latest_close",
        )
        return False
    long = is_long(setup.direction)
    inside = frozen.range_low <= current_close <= frozen.range_high
    inward = (
        current_close > seed.candle.close if long else current_close < seed.candle.close
    )
    if not (inside and inward):
        context.fail(
            "range_followthrough_failed",
            "family_state_reverified",
            f"close {decimal_text(current_close)} must lie inside "
            f"[{decimal_text(frozen.range_low)}, {decimal_text(frozen.range_high)}] "
            f"and move strictly farther inward than the seed close "
            f"{decimal_text(seed.candle.close)}",
        )
        return False
    context.record(
        "family_state_reverified",
        RuleOutcome.PASS,
        f"frozen range bounds ({decimal_text(frozen.range_low)}, "
        f"{decimal_text(frozen.range_high)}) are still the active range and the "
        "latest closed close has moved further inward than the seed close",
    )
    return True


def _resolve_entry(
    context: _PlanContext,
    patterns: PatternLiquiditySnapshot,
    interval: timedelta,
    *,
    as_of: datetime,
) -> Decimal | None:
    """Entry is always the actual decision-time price.

    The anchor is the latest closed candle's close at the planning ``as_of``.
    No earlier, frozen, or otherwise better price is ever substituted, so a
    setup whose move already happened is measured at the price really available
    now instead of at a gone price that would flatter its reward-to-risk.
    """

    value, source_id, observed, confirmed = plan_close_level(
        patterns.structure.volatility, patterns.timeframe, interval
    )
    source_type = "step3_volatility_latest_close"
    # Anti-lookahead: the entry anchor itself must have been known at the
    # planning instant. A contradiction outranks a missing input, so this
    # check precedes the missing-close gap below.
    if observed is not None and observed + interval > as_of:
        context.fail(
            "future_evidence_used",
            "entry_level_available",
            f"plan-close candle opened {observed} closes {observed + interval} "
            f"> as_of {as_of}; the entry anchor was not yet known",
        )
        return None
    if not isinstance(value, Decimal):
        context.gap(
            "missing_plan_close",
            "entry_level_available",
            "the Step 3 latest closed close is unavailable at the frame; "
            "no substitute price is invented",
            input_name="structure.volatility.latest_close",
        )
        return None
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        context.fail(
            "entry_level_not_usable",
            "entry_level_available",
            f"proposed entry {decimal_text(value)} is not a finite positive price",
        )
        return None
    context.entry = PlannedLevel(
        value=value,
        source_id=source_id,
        source_type=source_type,
        timeframe=patterns.timeframe,
        observed_at=observed,
        confirmed_at=confirmed,
        source_value=value,
        transformation=None,
    )
    context.record(
        "entry_level_available",
        RuleOutcome.PASS,
        f"entry anchored on {source_type} {decimal_text(value)} via source {source_id}",
    )
    return value


def _resolve_invalidation_and_side(
    context: _PlanContext,
    patterns: PatternLiquiditySnapshot,
    setup: SetupResult,
    reference: Reference,
    entry_value: Decimal,
) -> Decimal | None:
    band_low, band_high = reference.band_low, reference.band_high
    if not (isinstance(band_low, Decimal) and isinstance(band_high, Decimal)):
        context.gap(
            "missing_reference_band",
            "reference_band_available",
            "the frozen reference band is missing, so invalidation and side "
            "checks cannot be derived",
            input_name="step4_reference_band",
        )
        return None
    if (
        not usable_level(band_low)
        or not usable_level(band_high)
        or band_low > band_high
    ):
        context.fail(
            "invalid_reference_band",
            "reference_band_available",
            f"frozen reference band ({decimal_text(band_low)}, "
            f"{decimal_text(band_high)}) is not an ordered pair of finite "
            "positive prices",
        )
        return None
    long = is_long(setup.direction)
    invalidation_value = band_low if long else band_high
    context.record(
        "reference_band_available",
        RuleOutcome.PASS,
        f"logical invalidation {decimal_text(invalidation_value)} equals the "
        f"{'lower' if long else 'upper'} side of the frozen reference band "
        f"[{decimal_text(band_low)}, {decimal_text(band_high)}]",
    )
    context.invalidation = PlannedLevel(
        value=invalidation_value,
        source_id=reference.id,
        source_type=(
            "step4_reference_band_low" if long else "step4_reference_band_high"
        ),
        timeframe=patterns.timeframe,
        observed_at=reference_observed_at(reference),
        confirmed_at=reference.known_at,
        source_value=invalidation_value,
        transformation=None,
    )
    if (long and entry_value < band_high) or (not long and entry_value > band_low):
        context.fail(
            "entry_not_on_trade_side",
            "entry_on_trade_side",
            f"{'long' if long else 'short'} entry {decimal_text(entry_value)} "
            f"is not on the trade side of the frozen band "
            f"[{decimal_text(band_low)}, {decimal_text(band_high)}]",
        )
        return None
    frozen = reference.range
    if (
        setup.family is SetupFamily.RANGE_REVERSAL
        and frozen is not None
        and (
            (long and entry_value > frozen.range_high)
            or (not long and entry_value < frozen.range_low)
        )
    ):
        context.fail(
            "range_entry_outside_frozen_bounds",
            "entry_on_trade_side",
            f"range entry {decimal_text(entry_value)} is outside the frozen "
            f"range [{decimal_text(frozen.range_low)}, "
            f"{decimal_text(frozen.range_high)}]",
        )
        return None
    context.record(
        "entry_on_trade_side",
        RuleOutcome.PASS,
        "entry lies on the trade side of the frozen reference band"
        + (
            " and inside the frozen range"
            if setup.family is SetupFamily.RANGE_REVERSAL
            else ""
        ),
    )
    return invalidation_value


def _resolve_stop(
    context: _PlanContext,
    patterns: PatternLiquiditySnapshot,
    setup: SetupResult,
    reference: Reference,
    entry_value: Decimal,
    invalidation_value: Decimal,
) -> None:
    p = context.parameters
    long = is_long(setup.direction)
    stop_value, formula, buffer_gap = stop_buffer(
        p, invalidation_value, patterns.structure.volatility, setup.direction
    )
    if buffer_gap is not None:
        context.gap(
            "missing_atr_for_stop_buffer",
            "stop_buffer_inputs_available",
            "the ATR buffer is configured but the Step 3 ATR is unavailable; "
            "no substitute buffer is invented",
            input_name=buffer_gap,
        )
        return
    context.record(
        "stop_buffer_inputs_available",
        RuleOutcome.PASS,
        f"stop buffer mode {p.stop_buffer_mode.value}: {formula}",
    )
    if (
        not isinstance(stop_value, Decimal)
        or not stop_value.is_finite()
        or stop_value <= 0
    ):
        context.fail(
            "stop_non_positive",
            "stop_beyond_entry",
            f"buffered stop {decimal_text(stop_value)} is not a finite positive price",
        )
        return
    if (long and stop_value >= entry_value) or (not long and stop_value <= entry_value):
        context.fail(
            "stop_not_beyond_entry",
            "stop_beyond_entry",
            f"{'long' if long else 'short'} stop {decimal_text(stop_value)} must "
            f"be strictly {'below' if long else 'above'} entry "
            f"{decimal_text(entry_value)}; it is rejected, never clamped",
        )
    else:
        context.record(
            "stop_beyond_entry",
            RuleOutcome.PASS,
            f"stop {decimal_text(stop_value)} is strictly "
            f"{'below' if long else 'above'} entry {decimal_text(entry_value)}",
        )
    context.stop = PlannedLevel(
        value=stop_value,
        source_id=f"{reference.id}:buffer",
        source_type=f"planner_stop_buffer_{p.stop_buffer_mode.value}",
        timeframe=patterns.timeframe,
        observed_at=context.invalidation.observed_at,
        confirmed_at=None,
        source_value=invalidation_value,
        transformation=formula,
    )
    risk = abs(entry_value - stop_value)
    if risk > 0:
        context.risk_per_unit = risk
        context.record(
            "positive_risk",
            RuleOutcome.PASS,
            f"risk_per_unit = |entry - stop| = {decimal_text(risk)}; unit-neutral "
            "price distance only, never position sizing",
        )
    else:
        context.fail(
            "non_positive_risk",
            "positive_risk",
            "risk_per_unit is zero; a plan with no entry-to-stop distance is "
            "rejected rather than adjusted",
        )


def _select_targets(
    context: _PlanContext,
    patterns: PatternLiquiditySnapshot,
    setup: SetupResult,
    entry_value: Decimal,
    as_of: datetime,
) -> None:
    p = context.parameters
    candidates, rejected = structural_target_candidates(
        patterns, entry_value, setup.direction, p, as_of
    )
    context.excluded_targets.extend(rejected)
    built: list[PlannedTarget] = []
    for candidate in candidates:
        reward, r_multiple = _reward_and_r(
            candidate.value, entry_value, setup.direction, context.risk_per_unit
        )
        built.append(
            PlannedTarget(
                level=PlannedLevel(
                    value=candidate.value,
                    source_id=candidate.source_id,
                    source_type=candidate.source_type,
                    timeframe=patterns.timeframe,
                    observed_at=candidate.observed_at,
                    confirmed_at=candidate.confirmed_at,
                    source_value=candidate.value,
                    transformation=None,
                ),
                reward_per_unit=reward,
                r_multiple=r_multiple,
                is_structural=True,
            )
        )
    if not built:
        context.gap(
            "no_valid_target_available",
            "target_levels_valid",
            "no genuine structural level is valid at this as_of; a plan is "
            "never rescued by an R-derived or synthetic target, so nothing is "
            "invented and the gap is reported",
        )
        return
    context.record(
        "target_levels_valid",
        RuleOutcome.PASS,
        f"{len(built)} genuine structural target(s) known at the planning as_of",
    )
    context.targets = tuple(built)


def _apply_minimum_r(context: _PlanContext) -> None:
    """Apply the mandatory reward-to-risk floor to the structural targets.

    The floor is policy: a plan whose structural targets all sit below it is
    refused, and the refusal is a NO_PLAN (there is no viable target), not a
    malformed plan. The preferred distance is only a classification: it is
    recorded and never blocks a plan that clears the mandatory floor.
    """

    threshold = context.parameters.min_r_multiple
    preferred = context.parameters.preferred_r_multiple
    kept: list[PlannedTarget] = []
    for target in context.targets:
        if target.r_multiple is None:
            context.excluded_targets.append(
                f"{decimal_text(target.level.value)}: unknown R cannot meet "
                f"minimum_r_multiple {decimal_text(threshold)}"
            )
        elif target.r_multiple < threshold:
            context.excluded_targets.append(
                f"{decimal_text(target.level.value)}: R "
                f"{decimal_text(target.r_multiple)} < minimum_r_multiple "
                f"{decimal_text(threshold)}"
            )
        else:
            kept.append(target)
    context.targets = tuple(kept)
    if not kept:
        context.fail(
            MINIMUM_R_MULTIPLE_NOT_MET,
            "minimum_r_multiple",
            f"no genuine structural target reaches the mandatory "
            f"minimum_r_multiple {decimal_text(threshold)}; per-target "
            "exclusions with exact R values are listed in excluded_targets",
        )
        return
    nearest = kept[0]
    context.preferred_r_multiple_met = (
        nearest.r_multiple is not None and nearest.r_multiple >= preferred
    )
    context.record(
        "minimum_r_multiple",
        RuleOutcome.PASS,
        f"{len(kept)} structural target(s) reach R >= "
        f"{decimal_text(threshold)}; nearest retained target R "
        f"{decimal_text(nearest.r_multiple)} is "
        + ("at or above" if context.preferred_r_multiple_met else "below")
        + f" the preferred {decimal_text(preferred)} (preferred only, never a gate)",
    )


def _reward_and_r(
    value: Decimal, entry: Decimal, direction: Direction, risk: Decimal | None
) -> tuple[Decimal, Decimal | None]:
    reward = value - entry if is_long(direction) else entry - value
    r_multiple = (
        quantize_derived(divide(reward, risk))
        if risk is not None and risk > 0
        else None
    )
    return reward, r_multiple


def _future_violations(
    seed: Breakout | FailedBreakout | Sweep,
    setup: SetupResult,
    confirmation: Breakout | Retest | None,
    as_of: datetime,
    interval: timedelta,
) -> list[str]:
    """Name every seed, confirmation, or setup fact after the planning instant."""
    problems: list[str] = []
    if not is_available_at(seed.known_at, as_of):
        problems.append(f"seed known_at {seed.known_at} > as_of {as_of}")
    candle = getattr(seed, "candle", None)
    if candle is not None and getattr(candle, "timestamp", None) is not None:
        close_time = candle.timestamp + interval
        if close_time > as_of:
            problems.append(f"seed candle closes {close_time} > as_of {as_of}")
    if confirmation is not None:
        if confirmation.known_at > as_of:
            problems.append(
                f"confirmation {confirmation.id} known_at {confirmation.known_at} "
                f"> as_of {as_of}"
            )
        conf_candle = getattr(confirmation, "candle", None)
        if (
            conf_candle is not None
            and getattr(conf_candle, "timestamp", None) is not None
        ):
            conf_close_time = conf_candle.timestamp + interval
            if conf_close_time > as_of:
                problems.append(
                    f"confirmation {confirmation.id} candle closes {conf_close_time} "
                    f"> as_of {as_of}"
                )
    reference = reference_for(seed)
    if not is_available_at(reference.known_at, as_of):
        problems.append(
            f"reference {reference.id} known_at {reference.known_at} > as_of {as_of}"
        )
    if not is_available_at(setup.created_at, as_of):
        problems.append(f"setup created_at {setup.created_at} > as_of {as_of}")
    if not is_available_at(setup.as_of, as_of):
        problems.append(f"setup as_of {setup.as_of} > as_of {as_of}")
    return problems
