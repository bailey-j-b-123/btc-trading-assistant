"""Step 11 historical replay and out-of-sample validation.

This package derives isolated, reproducible validation reports from stored
candles. It never writes Bailey's journal, creates orders, or changes Steps 3–6
strategy rules.
"""

from trading_assistant.historical_validation.models import (
    Breakdown,
    CohortMetrics,
    DatasetRange,
    MetricStatus,
    RateMetric,
    RDistribution,
    RegimeLabel,
    ResolvedSplit,
    ValidationCohort,
    ValidationPhase,
    ValidationRecord,
    ValidationRecordKind,
    ValidationReport,
    ValueCount,
)
from trading_assistant.historical_validation.parameters import (
    FRICTION_ASSUMPTIONS_VERSION,
    HISTORICAL_VALIDATION_RULES_VERSION,
    ChronologicalSplit,
    FrictionAssumptions,
    ValidationConfig,
    fingerprint,
)
from trading_assistant.historical_validation.service import HistoricalValidationService

__all__ = [
    "FRICTION_ASSUMPTIONS_VERSION",
    "HISTORICAL_VALIDATION_RULES_VERSION",
    "Breakdown",
    "ChronologicalSplit",
    "CohortMetrics",
    "DatasetRange",
    "FrictionAssumptions",
    "HistoricalValidationService",
    "MetricStatus",
    "RDistribution",
    "RateMetric",
    "RegimeLabel",
    "ResolvedSplit",
    "ValidationCohort",
    "ValidationConfig",
    "ValidationPhase",
    "ValidationRecord",
    "ValidationRecordKind",
    "ValidationReport",
    "ValueCount",
    "fingerprint",
]
