"""Read-only database facade for deterministic Step 8 journal statistics."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta

from sqlalchemy.engine import Engine

from trading_assistant.statistics.analysis import StatisticsAnalyzer, StatisticsFilters
from trading_assistant.statistics.config import StatisticsConfig
from trading_assistant.statistics.dataset import JournalDataset, JournalDatasetReader
from trading_assistant.statistics.models import StatisticsReport


class JournalStatisticsService:
    """A SELECT-only journal reader plus the pure in-memory statistics analyzer.

    Constructing or using the service does not migrate a database, create
    tables, append journal versions, write aggregates, or query market candles.
    Reports are recomputed from a single immutable :class:`JournalDataset`.
    """

    def __init__(
        self, engine: Engine, *, config: StatisticsConfig | None = None
    ) -> None:
        self.reader = JournalDatasetReader(engine)
        self.analyzer = StatisticsAnalyzer(config)

    def read_dataset(self) -> JournalDataset:
        """Read all current Step 7 rows once; no journal rows are changed."""

        return self.reader.read()

    def analyze(
        self,
        *,
        as_of: datetime,
        window_start: datetime | None = None,
        filters: StatisticsFilters | Mapping[str, object] | None = None,
        group_by: Sequence[str] | None = None,
        allow_mixed_versions: bool = False,
    ) -> StatisticsReport:
        """Read one dataset and return a deterministic, non-persisted report."""

        dataset = self.read_dataset()
        return self.analyzer.analyze(
            dataset,
            as_of=as_of,
            window_start=window_start,
            filters=filters,
            group_by=group_by,
            allow_mixed_versions=allow_mixed_versions,
        )

    def rolling_reports(
        self,
        *,
        cutoffs: Sequence[datetime],
        lookback: timedelta,
        filters: StatisticsFilters | Mapping[str, object] | None = None,
        group_by: Sequence[str] | None = None,
        allow_mixed_versions: bool = False,
    ) -> tuple[StatisticsReport, ...]:
        """Analyze multiple windows from one materialized journal dataset."""

        dataset = self.read_dataset()
        return self.analyzer.rolling_reports(
            dataset,
            cutoffs=cutoffs,
            lookback=lookback,
            filters=filters,
            group_by=group_by,
            allow_mixed_versions=allow_mixed_versions,
        )
