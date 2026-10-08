"""Deterministic, read-only trade planning from Step 5 QUALIFIED setups.

PLANNABLE means "a complete deterministic proposal exists", never that a
trade is profitable, advisable, or should be executed. This package places
no orders, authenticates to no exchange, manages no positions, and does no
monetary sizing.
"""

from trading_assistant.trade_planning.levels import (
    TargetCandidate,
    structural_target_candidates,
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
    MINIMUM_R_MULTIPLE_FLOOR,
    PLANNING_RULES_VERSION,
    PlanningParameters,
    StopBufferMode,
    fingerprint,
)
from trading_assistant.trade_planning.planner import (
    BASE_RULES,
    INVALID_CODES,
    MINIMUM_R_MULTIPLE_NOT_MET,
    plan_trade,
)

__all__ = [
    "BASE_RULES",
    "INVALID_CODES",
    "MINIMUM_R_MULTIPLE_FLOOR",
    "MINIMUM_R_MULTIPLE_NOT_MET",
    "PLANNING_RULES_VERSION",
    "UNKNOWN_LEVEL",
    "PlanState",
    "PlannedLevel",
    "PlannedTarget",
    "PlanningParameters",
    "PlanningRuleResult",
    "StopBufferMode",
    "TargetCandidate",
    "TradePlanResult",
    "fingerprint",
    "plan_trade",
    "structural_target_candidates",
]
