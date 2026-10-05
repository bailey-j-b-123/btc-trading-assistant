"""Immutable Step 6 planning contracts.

A ``TradePlanResult`` is a transparent, auditable snapshot of one deterministic
planning pass over one Step 5 ``SetupResult`` at one ``as_of``. It is never an
order, request, position, or recommendation. Levels are either exact derived
Decimals with full source traceability, or ``None`` plus an explicit machine-
readable reason when the required evidence is missing — a number is never
invented, corrected, or clamped.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from trading_assistant.market_structure.snapshot import to_jsonable
from trading_assistant.pattern_liquidity.events import Direction
from trading_assistant.setup_qualification.models import (
    RuleOutcome,
    SetupFamily,
)


class PlanState(StrEnum):
    """Exact planning outcomes. ``QUALIFIED`` never implies ``PLANNABLE``.

    ``PLANNABLE``
        Every required planning input was available at ``as_of``, all planning
        rules passed, and the proposed levels satisfy the documented hard
        invariants. This is a deterministic proposal only; it is not advice,
        profitability, or an execution instruction.
    ``NO_PLAN``
        No actionable plan can be produced because the source setup is not
        currently usable (not QUALIFIED, terminal, or stale at the requested
        ``as_of``) or because a required input is missing/UNKNOWN. Nothing is
        guessed through the gap.
    ``INVALID``
        All inputs were present, but a derived plan violates a hard planning
        invariant (wrong-side stop, zero/negative risk, wrong-side entry,
        contradictory frozen evidence, or an explicitly enabled minimum-R rule
        that no surviving target meets). Invalid plans are reported with their
        offending numbers; they are never silently corrected.
    """

    PLANNABLE = "PLANNABLE"
    NO_PLAN = "NO_PLAN"
    INVALID = "INVALID"


@dataclass(frozen=True, slots=True)
class PlannedLevel:
    """One planning level plus the exact upstream fact and derivation behind it.

    ``source_value`` is the value observed at the source before any planning
    transformation; ``transformation`` is the exact documented formula applied
    to reach ``value`` (``None`` means the source value was copied verbatim).
    Timestamps are never invented: absent upstream facts stay ``None`` and are
    additionally reported in the plan's ``missing_inputs`` when required.
    """

    value: Decimal | None
    source_id: str | None
    source_type: str
    timeframe: str | None
    observed_at: datetime | None
    confirmed_at: datetime | None
    source_value: Decimal | None
    transformation: str | None


#: Placeholder for a level that could not be derived at all.
UNKNOWN_LEVEL = PlannedLevel(
    value=None,
    source_id=None,
    source_type="unknown",
    timeframe=None,
    observed_at=None,
    confirmed_at=None,
    source_value=None,
    transformation=None,
)


@dataclass(frozen=True, slots=True)
class PlannedTarget:
    """One proposed target with exact unit-neutral reward and R multiple.

    ``reward_per_unit`` and ``risk_per_unit`` are absolute Decimal price
    distances per unit of the instrument; ``r_multiple`` is
    ``quantize_derived(reward / risk)``. ``is_structural`` distinguishes a
    level taken from pre-existing market-structure evidence from an
    explicitly labelled R-derived fallback target.
    """

    level: PlannedLevel
    reward_per_unit: Decimal | None
    r_multiple: Decimal | None
    is_structural: bool


@dataclass(frozen=True, slots=True)
class PlanningRuleResult:
    """One planning rule's outcome; shared vocabulary with Step 5 rule results."""

    rule_id: str
    outcome: RuleOutcome
    reason: str


@dataclass(frozen=True, slots=True)
class TradePlanResult:
    """Complete planning verdict for one setup at one ``as_of``.

    Identity fields (``setup_id``, family, direction, timestamps) are always
    reported when known. Numerical fields are populated only from evidence
    available at ``as_of``; ``None`` plus a machine-readable ``reasons`` entry
    and/or ``missing_inputs`` is the documented representation of "unknown".
    ``excluded_targets`` preserves every rejected target with its exact
    rejection reason so rejections are auditable rather than silent.
    """

    id: str
    state: PlanState
    exchange: str
    symbol: str
    timeframe: str
    source_timeframes: tuple[str, ...]
    setup_id: str | None
    family: SetupFamily | None
    direction: Direction | None
    setup_created_at: datetime | None
    setup_as_of: datetime | None
    as_of: datetime | None
    state_detail: str | None
    entry: PlannedLevel
    invalidation: PlannedLevel
    stop: PlannedLevel
    risk_per_unit: Decimal | None
    targets: tuple[PlannedTarget, ...]
    rules: tuple[PlanningRuleResult, ...]
    reasons: tuple[str, ...]
    missing_inputs: tuple[str, ...]
    excluded_targets: tuple[str, ...]
    setup_config_fingerprint: str | None
    config_fingerprint: str
    planning_rules_version: str

    @property
    def actionable(self) -> bool:
        """True only for a complete PLANNABLE result; never a recommendation."""
        return self.state is PlanState.PLANNABLE

    @property
    def passed_rules(self) -> tuple[str, ...]:
        return self._rules_with(RuleOutcome.PASS)

    @property
    def failed_rules(self) -> tuple[str, ...]:
        return self._rules_with(RuleOutcome.FAIL)

    @property
    def pending_rules(self) -> tuple[str, ...]:
        return self._rules_with(RuleOutcome.PENDING)

    def _rules_with(self, outcome: RuleOutcome) -> tuple[str, ...]:
        return tuple(r.rule_id for r in self.rules if r.outcome == outcome)

    def to_json_dict(self) -> dict[str, Any]:
        return to_jsonable(self)
