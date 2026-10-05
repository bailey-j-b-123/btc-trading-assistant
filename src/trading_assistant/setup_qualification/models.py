"""Immutable Step 5 contracts. References point to, never amend, source facts."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from trading_assistant.market_structure.higher_timeframe import HigherTimeframeContext
from trading_assistant.market_structure.snapshot import to_jsonable
from trading_assistant.pattern_liquidity.events import Direction
from trading_assistant.pattern_liquidity.snapshot import PatternLiquiditySnapshot


class SetupState(StrEnum):
    NO_SETUP = "NO_SETUP"
    WATCH = "WATCH"
    QUALIFIED = "QUALIFIED"


class SetupFamily(StrEnum):
    BREAKOUT_RETEST = "breakout_retest_continuation"
    LIQUIDITY_REVERSAL = "failed_breakout_sweep_reversal"
    RANGE_REVERSAL = "range_rejection_reversal"


class EvidenceStatus(StrEnum):
    SUPPORTIVE = "supportive"
    OPPOSING = "opposing"
    NEUTRAL = "neutral"
    UNKNOWN = "unknown"


class RuleOutcome(StrEnum):
    PASS = "passed"
    FAIL = "failed"
    PENDING = "pending"


@dataclass(frozen=True, slots=True)
class QualificationFrame:
    """One Step 4 snapshot plus same-as-of Step 3 higher-timeframe outputs."""

    patterns: PatternLiquiditySnapshot
    higher_timeframes: tuple[HigherTimeframeContext, ...] = ()


@dataclass(frozen=True, slots=True)
class QualificationEvidence:
    source_reference: str | None
    timeframe: str
    observed_at: datetime | None
    confirmed_at: datetime | None
    category: str
    status: EvidenceStatus
    reason: str


@dataclass(frozen=True, slots=True)
class RuleResult:
    rule_id: str
    required: bool
    outcome: RuleOutcome
    reason: str
    evidence: tuple[QualificationEvidence, ...]
    veto: bool = False


@dataclass(frozen=True, slots=True)
class SetupResult:
    id: str
    family: SetupFamily
    direction: Direction
    state: SetupState
    seed_event_id: str
    reference_id: str
    created_at: datetime
    as_of: datetime
    source_timeframes: tuple[str, ...]
    rules: tuple[RuleResult, ...]
    terminal_reason: str | None = None
    ended_at: datetime | None = None

    @property
    def evidence(self) -> tuple[QualificationEvidence, ...]:
        return tuple(e for rule in self.rules for e in rule.evidence)

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


@dataclass(frozen=True, slots=True)
class QualificationSnapshot:
    exchange: str
    symbol: str
    timeframe: str
    as_of: datetime
    state: SetupState
    setups: tuple[SetupResult, ...]
    reasons: tuple[str, ...]
    config_fingerprint: str
    rules_version: str
    source_timeframes: tuple[str, ...]
    status: Literal["evaluated", "incomplete"]

    def to_json_dict(self) -> dict[str, Any]:
        return to_jsonable(self)
