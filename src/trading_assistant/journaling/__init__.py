"""Step 7 — immutable decision & outcome journal.

The journal records what the existing system knew and proposed (exact Step 5
snapshot and Step 6 plan projections), what Bailey explicitly decided
(``PENDING``/``ACCEPTED``/``REJECTED``/``SKIPPED``), and what the market
subsequently did relative to the proposed levels, using deterministic
candle-based observation rules.

It is not a strategy, a backtester, an execution engine, or a statistics layer:
it places no orders, models no fills, positions, balances, leverage or monetary
P&L, and computes no aggregate performance metric. The journal records
historical system proposals, human decisions and deterministic market
observations; it does not prove profitability and does not represent exchange
execution unless a future execution system supplies genuine fill data.

Core principle preserved: software calculates → rules qualify → statistics
validate → AI explains → Bailey decides → everything gets recorded.
"""

from trading_assistant.journaling.errors import (
    JournalConflict,
    JournalError,
    JournalNotFound,
)
from trading_assistant.journaling.observation import (
    ENTRY_AND_EXIT_SAME_CANDLE,
    RESOLUTION_COVERAGE_INCOMPLETE,
    RESOLUTION_CONSISTENCY_CONFLICT,
    RESOLUTION_NO_CANDLES,
    RESOLUTION_PENDING_REASONS,
    RESOLUTION_SAME_MINUTE_AMBIGUOUS,
    RESOLUTION_TIMEFRAME,
    RESOLUTION_USED,
    STOP_AND_TARGET_SAME_CANDLE,
    observe_outcome,
)
from trading_assistant.journaling.parameters import (
    DECISION_RULES_VERSION,
    JOURNAL_RULES_VERSION,
    MAX_NOTE_LENGTH,
    OUTCOME_RESOLUTION_RULES_VERSION,
    OUTCOME_RULES_VERSION,
    OutcomeParameters,
    canonical_json,
    fingerprint,
    normalize_note,
)
from trading_assistant.journaling.repository import JournalRepository
from trading_assistant.journaling.service import JournalService, snapshot_identity
from trading_assistant.journaling.types import (
    DecisionRecord,
    DecisionState,
    JournalRecord,
    OutcomeEvent,
    OutcomeEventKind,
    OutcomeEventOrdering,
    OutcomeObservation,
    OutcomeStatus,
    OutcomeVersion,
    ProposedPlanLevels,
    RecordKind,
    record_identity_material,
)

__all__ = [
    "DECISION_RULES_VERSION",
    "ENTRY_AND_EXIT_SAME_CANDLE",
    "JOURNAL_RULES_VERSION",
    "MAX_NOTE_LENGTH",
    "OUTCOME_RESOLUTION_RULES_VERSION",
    "OUTCOME_RULES_VERSION",
    "RESOLUTION_COVERAGE_INCOMPLETE",
    "RESOLUTION_CONSISTENCY_CONFLICT",
    "RESOLUTION_NO_CANDLES",
    "RESOLUTION_PENDING_REASONS",
    "RESOLUTION_SAME_MINUTE_AMBIGUOUS",
    "RESOLUTION_TIMEFRAME",
    "RESOLUTION_USED",
    "STOP_AND_TARGET_SAME_CANDLE",
    "DecisionRecord",
    "DecisionState",
    "JournalConflict",
    "JournalError",
    "JournalNotFound",
    "JournalRecord",
    "JournalRepository",
    "JournalService",
    "OutcomeEvent",
    "OutcomeEventKind",
    "OutcomeEventOrdering",
    "OutcomeObservation",
    "OutcomeParameters",
    "OutcomeStatus",
    "OutcomeVersion",
    "ProposedPlanLevels",
    "RecordKind",
    "canonical_json",
    "fingerprint",
    "normalize_note",
    "observe_outcome",
    "record_identity_material",
    "snapshot_identity",
]
