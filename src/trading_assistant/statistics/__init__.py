"""Step 8: deterministic read-only statistics over immutable Step 7 evidence."""

from trading_assistant.statistics.analysis import (
    DEFAULT_GROUP_BY,
    NO_DECISION,
    NO_OUTCOME_BY_CUTOFF,
    NO_PLAN_ATTACHED,
    PLAN_NOT_YET_VISIBLE,
    VERSION_GROUP_BY,
    StatisticsAnalyzer,
    StatisticsFilters,
)
from trading_assistant.statistics.config import (
    DEFAULT_DECIMAL_PLACES,
    DEFAULT_DECIMAL_PRECISION,
    DEFAULT_MINIMUM_SAMPLE_SIZE,
    DEFAULT_QUANTILES,
    STATISTICS_RULES_VERSION,
    StatisticsConfig,
)
from trading_assistant.statistics.dataset import (
    JournalDataset,
    JournalDatasetReader,
    StatisticsDatasetError,
)
from trading_assistant.statistics.models import (
    DecimalValueCount,
    DimensionCounts,
    DistributionSummary,
    GroupStatistics,
    MetricStatus,
    QuantileValue,
    RateSummary,
    ReportDataQuality,
    StatisticsReport,
    TargetSummary,
    ValueCount,
    VersionProfile,
)
from trading_assistant.statistics.service import JournalStatisticsService

__all__ = [
    "DEFAULT_DECIMAL_PLACES",
    "DEFAULT_DECIMAL_PRECISION",
    "DEFAULT_GROUP_BY",
    "DEFAULT_MINIMUM_SAMPLE_SIZE",
    "DEFAULT_QUANTILES",
    "NO_DECISION",
    "NO_OUTCOME_BY_CUTOFF",
    "NO_PLAN_ATTACHED",
    "PLAN_NOT_YET_VISIBLE",
    "STATISTICS_RULES_VERSION",
    "VERSION_GROUP_BY",
    "DecimalValueCount",
    "DimensionCounts",
    "DistributionSummary",
    "GroupStatistics",
    "JournalDataset",
    "JournalDatasetReader",
    "JournalStatisticsService",
    "MetricStatus",
    "QuantileValue",
    "RateSummary",
    "ReportDataQuality",
    "StatisticsAnalyzer",
    "StatisticsConfig",
    "StatisticsDatasetError",
    "StatisticsFilters",
    "StatisticsReport",
    "TargetSummary",
    "ValueCount",
    "VersionProfile",
]
