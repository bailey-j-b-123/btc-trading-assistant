"""Step 12 — live forward paper testing / real-time validation.

This package turns the existing assistant into a safe live forward-testing
system: it continuously ingests **public** BTC market data, runs the *existing*
deterministic Steps 3–6 pipeline as each base-timeframe candle closes, records
what the assistant genuinely knew at that moment, creates **paper** observations
from PLANNABLE Step 6 proposals, tracks their subsequent outcomes with the
existing Step 7 observation semantics, and compares the live forward results
with Step 11 historical validation.

Hard boundaries, by construction:

* no real money, no orders, no exchange authentication, no private endpoints;
* no balances, positions, leverage, margin, liquidation, or position sizing;
* no autonomous trading and no strategy/parameter optimisation;
* Bailey remains the decision maker; the system observes, calculates, records
  and explains.

Paper trading and historical performance do not establish future profitability.
"""

from trading_assistant.historical_validation import FrictionAssumptions
from trading_assistant.forward_testing.errors import (
    ForwardConflict,
    ForwardDataUnavailable,
    ForwardError,
    ForwardNotConfigured,
    ForwardNotFound,
)
from trading_assistant.forward_testing.models import (
    ComparisonRow,
    ComparisonSide,
    ForwardBreakdown,
    ForwardCohortMetrics,
    ForwardComparison,
    ForwardCycle,
    ForwardHeartbeat,
    ForwardLedger,
    ForwardObservation,
    ForwardReport,
    ForwardVersionCohort,
    PaperOutcome,
    PaperPlan,
)
from trading_assistant.forward_testing.parameters import (
    FORWARD_LEDGER_RULES_VERSION,
    FORWARD_PARAMETERS_VERSION,
    FORWARD_RUNNER_RULES_VERSION,
    CycleStatus,
    DataHealth,
    ForwardParameters,
    HeartbeatStatus,
    RunnerSettings,
    VersionSeparation,
    canonical_json,
    fingerprint,
)
from trading_assistant.forward_testing.reporting import (
    FORWARD_REPORT_LIMITATIONS,
    build_forward_comparison,
    build_forward_metrics_payload,
    build_forward_report,
)
from trading_assistant.forward_testing.repository import ForwardLedgerRepository
from trading_assistant.forward_testing.runner import (
    ForwardRunner,
    format_status,
    run_forever,
    run_single_pass,
)
from trading_assistant.forward_testing.service import (
    FORWARD_LIMITATIONS,
    ForwardRunResult,
    ForwardTestService,
)

__all__ = [
    "FORWARD_LEDGER_RULES_VERSION",
    "FORWARD_LIMITATIONS",
    "FORWARD_PARAMETERS_VERSION",
    "FORWARD_REPORT_LIMITATIONS",
    "FORWARD_RUNNER_RULES_VERSION",
    "ComparisonRow",
    "ComparisonSide",
    "CycleStatus",
    "DataHealth",
    "ForwardBreakdown",
    "ForwardCohortMetrics",
    "ForwardComparison",
    "ForwardConflict",
    "ForwardCycle",
    "ForwardDataUnavailable",
    "ForwardError",
    "ForwardHeartbeat",
    "ForwardLedger",
    "ForwardLedgerRepository",
    "ForwardNotConfigured",
    "ForwardNotFound",
    "ForwardObservation",
    "ForwardParameters",
    "ForwardReport",
    "ForwardRunResult",
    "ForwardRunner",
    "ForwardTestService",
    "ForwardVersionCohort",
    "FrictionAssumptions",
    "HeartbeatStatus",
    "PaperOutcome",
    "PaperPlan",
    "RunnerSettings",
    "VersionSeparation",
    "build_forward_comparison",
    "build_forward_metrics_payload",
    "build_forward_report",
    "canonical_json",
    "fingerprint",
    "format_status",
    "run_forever",
    "run_single_pass",
]
