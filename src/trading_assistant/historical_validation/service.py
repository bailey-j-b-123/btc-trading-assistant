"""Deterministic, read-only chronological replay for Step 11.

The service composes existing Steps 2–7 rather than duplicating or tuning their
rules.  It freezes a bounded source-candle view, builds a Step 4 frame at every
base-candle close, replays Step 5 chronologically, invokes the unchanged Step 6
planner, and uses the pure Step 7 observation function for subsequent candles.
It has no database write path and never reads the Bailey journal.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from statistics import median

from sqlalchemy.engine import Engine

from trading_assistant.historical_validation.metrics import (
    CLEAN_OUTCOMES,
    COMPLETED_OUTCOMES,
    counts,
    counts_from_counter,
    directional_r,
    distribution,
    enum_text,
    friction_adjusted_r,
    r_exclusion,
    rate,
    r_values,
    terminal_endpoint,
)
from trading_assistant.historical_validation.models import (
    Breakdown,
    CohortMetrics,
    DatasetRange,
    RegimeLabel,
    ResolvedSplit,
    ValidationCohort,
    ValidationPhase,
    ValidationRecord,
    ValidationRecordKind,
    ValidationReport,
)
from trading_assistant.historical_validation.parameters import (
    HISTORICAL_VALIDATION_RULES_VERSION,
    FrictionAssumptions,
    ValidationConfig,
    fingerprint,
)
from trading_assistant.journaling import ProposedPlanLevels, observe_outcome
from trading_assistant.journaling.types import OutcomeObservation, OutcomeStatus
from trading_assistant.market_data.repository import CandleRepository
from trading_assistant.market_data.timeframes import latest_closed_candle_open_time
from trading_assistant.market_data.types import Candle
from trading_assistant.market_structure.candles import interval_for_timeframe
from trading_assistant.market_structure.higher_timeframe import (
    build_higher_timeframe_context,
)
from trading_assistant.market_structure.parameters import MarketStructureParameters
from trading_assistant.pattern_liquidity.parameters import PatternLiquidityParameters
from trading_assistant.pattern_liquidity.service import PatternLiquidityService
from trading_assistant.setup_qualification import (
    QualificationFrame,
    QualificationParameters,
    SetupState,
    enumerate_qualifications,
)
from trading_assistant.setup_qualification.parameters import RULES_VERSION
from trading_assistant.trade_planning import (
    PLANNING_RULES_VERSION,
    PlanningParameters,
    PlanState,
    plan_trade,
)

# These are intentionally descriptive labels only.  They are not consumed by
# Steps 3–6 and never gate or rank a setup.
_VOLATILITY_WARMUP_OBSERVATIONS = 5
# Shared with Step 12 forward reporting via ``historical_validation.metrics``.
_COMPLETED_OUTCOMES = COMPLETED_OUTCOMES
_CLEAN_OUTCOMES = CLEAN_OUTCOMES


class HistoricalValidationService:
    """Run isolated derived validation reports over stored Step 2 candles only."""

    def __init__(self, engine: Engine) -> None:
        self.candles = CandleRepository(engine)
        self.patterns = PatternLiquidityService(engine)

    def validate(
        self,
        *,
        exchange: str,
        symbol: str,
        timeframe: str,
        config: ValidationConfig | None = None,
        qualification_parameters: QualificationParameters | None = None,
        pattern_parameters: PatternLiquidityParameters | None = None,
        structure_parameters: MarketStructureParameters | None = None,
        planning_parameters: PlanningParameters | None = None,
    ) -> ValidationReport:
        """Create one fully derived, non-persisted historical validation report.

        Optional upstream parameter objects exist for audit/reproduction with a
        previously configured pipeline; callers must supply them before a run.
        This method never searches, changes, or selects them from results.
        """

        if not exchange or not symbol or not timeframe:
            raise ValueError("exchange, symbol, and timeframe must be non-empty")
        cfg = config or ValidationConfig()
        qp = qualification_parameters or QualificationParameters()
        pp = pattern_parameters or PatternLiquidityParameters()
        sp = structure_parameters or MarketStructureParameters()
        planning = planning_parameters or PlanningParameters()
        interval = interval_for_timeframe(timeframe)
        if cfg.split is not None:
            for name, boundary in (
                ("development_start", cfg.split.development_start),
                ("development_end", cfg.split.development_end),
                ("out_of_sample_start", cfg.split.out_of_sample_start),
                ("out_of_sample_end", cfg.split.out_of_sample_end),
            ):
                if (
                    boundary is not None
                    and latest_closed_candle_open_time(boundary, timeframe) + interval
                    != boundary
                ):
                    raise ValueError(
                        f"{name} must be a {timeframe} candle-close boundary"
                    )

        # Freeze a bounded source view before any replay work. An explicit split
        # is capped at its OOS end, so later database rows are neither used nor
        # fingerprinted. Every subsequent repository access carries an explicit
        # end bound; a latest database row cannot leak into a prior decision.
        requested_end = cfg.split.out_of_sample_end if cfg.split is not None else None
        base_result = self.candles.get_candles(
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            end_time=(
                latest_closed_candle_open_time(requested_end, timeframe)
                if requested_end is not None
                else None
            ),
        )
        base_candles = tuple(base_result.candles)
        end_as_of = (
            requested_end
            if requested_end is not None and base_candles
            else base_candles[-1].timestamp + interval
            if base_candles
            else None
        )
        source_candles = self._source_candles(
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            end_as_of=end_as_of,
            base_candles=base_candles,
            higher_timeframes=qp.higher_timeframes,
        )
        dataset_ranges = self._dataset_ranges(source_candles)
        dataset_fingerprint = fingerprint(
            "source-candles",
            exchange,
            symbol,
            timeframe,
            source_candles,
        )
        strategy_versions = self._strategy_versions(
            qp=qp, pp=pp, sp=sp, planning=planning, friction=cfg.friction
        )

        frames = self._frames(
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            end_as_of=end_as_of,
            base_candles=base_candles,
            qualification_parameters=qp,
            pattern_parameters=pp,
            structure_parameters=sp,
        )
        snapshots = (
            enumerate_qualifications(
                frames,
                as_of=frames[-1].patterns.as_of,
                parameters=qp,
            )
            if frames
            else ()
        )
        frame_by_as_of = {frame.patterns.as_of: frame for frame in frames}
        resolved_split = self._resolve_split(
            tuple(snapshot.as_of for snapshot in snapshots), cfg
        )
        regimes = self._regimes(frames)
        records = self._records(
            snapshots=snapshots,
            frame_by_as_of=frame_by_as_of,
            regimes=regimes,
            split=resolved_split,
            config=cfg,
            planning_parameters=planning,
            interval=interval,
        )

        development = self._cohort(
            phase=ValidationPhase.DEVELOPMENT,
            start=resolved_split.development_start,
            end=resolved_split.development_end,
            records=tuple(
                record
                for record in records
                if record.phase is ValidationPhase.DEVELOPMENT
            ),
            decision_boundaries=tuple(
                snapshot.as_of
                for snapshot in snapshots
                if _in_range(
                    snapshot.as_of,
                    resolved_split.development_start,
                    resolved_split.development_end,
                )
            ),
            config=cfg,
        )
        oos_records = tuple(
            record
            for record in records
            if record.phase is ValidationPhase.OUT_OF_SAMPLE
        )
        out_of_sample = (
            self._cohort(
                phase=ValidationPhase.OUT_OF_SAMPLE,
                start=resolved_split.out_of_sample_start,
                end=resolved_split.out_of_sample_end,
                records=oos_records,
                decision_boundaries=tuple(
                    snapshot.as_of
                    for snapshot in snapshots
                    if _in_range(
                        snapshot.as_of,
                        resolved_split.out_of_sample_start,
                        resolved_split.out_of_sample_end,
                    )
                ),
                config=cfg,
            )
            if resolved_split.out_of_sample_start is not None
            else None
        )
        warnings = self._report_warnings(development, out_of_sample, records, cfg)
        resolved_config_fingerprint = fingerprint(
            "resolved-validation-config",
            cfg,
            resolved_split,
            strategy_versions,
        )
        report_id = fingerprint(
            "validation-report",
            dataset_fingerprint,
            resolved_config_fingerprint,
            tuple(record.id for record in records),
            development,
            out_of_sample,
        )
        return ValidationReport(
            report_id=report_id,
            rules_version=HISTORICAL_VALIDATION_RULES_VERSION,
            dataset_fingerprint=dataset_fingerprint,
            config_fingerprint=cfg.fingerprint(),
            resolved_config_fingerprint=resolved_config_fingerprint,
            strategy_versions=strategy_versions,
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            dataset_ranges=dataset_ranges,
            split=resolved_split,
            records=records,
            development=development,
            out_of_sample=out_of_sample,
            warnings=warnings,
            limitations=(
                "HISTORICAL VALIDATION — NOT LIVE PERFORMANCE.",
                "Raw observations describe stored OHLC candles relative to proposed levels; they are not fills, realised profit, or an equity curve.",
                "Friction-adjusted values are a configurable hypothetical per-unit scenario only; there is no position sizing, leverage, account, balance, or execution model.",
                "Historical performance does not establish future profitability.",
            ),
        )

    # ------------------------------------------------------------------
    # Replay construction and anti-lookahead boundaries
    # ------------------------------------------------------------------

    def _source_candles(
        self,
        *,
        exchange: str,
        symbol: str,
        timeframe: str,
        end_as_of: datetime | None,
        base_candles: tuple[Candle, ...],
        higher_timeframes: tuple[str, ...],
    ) -> tuple[tuple[str, tuple[Candle, ...]], ...]:
        values: list[tuple[str, tuple[Candle, ...]]] = [(timeframe, base_candles)]
        if end_as_of is None:
            return tuple(values)
        for higher in higher_timeframes:
            end_open = latest_closed_candle_open_time(end_as_of, higher)
            result = self.candles.get_candles(
                exchange=exchange,
                symbol=symbol,
                timeframe=higher,
                end_time=end_open,
            )
            values.append((higher, tuple(result.candles)))
        return tuple(sorted(values, key=lambda item: item[0]))

    def _dataset_ranges(
        self, source: tuple[tuple[str, tuple[Candle, ...]], ...]
    ) -> tuple[DatasetRange, ...]:
        return tuple(
            DatasetRange(
                timeframe=timeframe,
                candle_count=len(candles),
                first_open=candles[0].timestamp if candles else None,
                last_close=(
                    candles[-1].timestamp + interval_for_timeframe(timeframe)
                    if candles
                    else None
                ),
            )
            for timeframe, candles in source
        )

    def _frames(
        self,
        *,
        exchange: str,
        symbol: str,
        timeframe: str,
        end_as_of: datetime | None,
        base_candles: tuple[Candle, ...],
        qualification_parameters: QualificationParameters,
        pattern_parameters: PatternLiquidityParameters,
        structure_parameters: MarketStructureParameters,
    ) -> tuple[QualificationFrame, ...]:
        if end_as_of is None or not base_candles:
            return ()
        interval = interval_for_timeframe(timeframe)
        at = base_candles[0].timestamp + interval
        frames: list[QualificationFrame] = []
        while at <= end_as_of:
            source = self.patterns.snapshot(
                exchange=exchange,
                symbol=symbol,
                timeframe=timeframe,
                as_of=at,
                parameters=pattern_parameters,
                structure_parameters=structure_parameters,
            )
            higher = []
            for higher_timeframe in qualification_parameters.higher_timeframes:
                expected = latest_closed_candle_open_time(at, higher_timeframe)
                result = self.candles.get_candles(
                    exchange=exchange,
                    symbol=symbol,
                    timeframe=higher_timeframe,
                    end_time=expected,
                )
                higher.append(
                    build_higher_timeframe_context(
                        higher_timeframe,
                        result.candles,
                        interval=interval_for_timeframe(higher_timeframe),
                        as_of=at,
                        expected_latest_closed_open_time=expected,
                        parameters=structure_parameters,
                        gaps=result.gaps,
                    )
                )
            frames.append(QualificationFrame(source, tuple(higher)))
            at += interval
        return tuple(frames)

    def _resolve_split(
        self,
        boundaries: tuple[datetime, ...],
        config: ValidationConfig,
    ) -> ResolvedSplit:
        if config.split is not None:
            return ResolvedSplit(
                config.split.development_start,
                config.split.development_end,
                config.split.out_of_sample_start,
                config.split.out_of_sample_end,
                "EXPLICIT",
            )
        if not boundaries:
            return ResolvedSplit(None, None, None, None, "AUTO_CHRONOLOGICAL_70_30")
        if len(boundaries) == 1:
            return ResolvedSplit(
                boundaries[0], boundaries[0], None, None, "AUTO_CHRONOLOGICAL_70_30"
            )
        # Floor keeps the OOS period non-empty. No outcome value participates in
        # this decision; only ordered source decision timestamps do.
        development_count = max(
            1,
            int(
                Decimal(len(boundaries)) * (Decimal(1) - config.out_of_sample_fraction)
            ),
        )
        development_count = min(development_count, len(boundaries) - 1)
        return ResolvedSplit(
            boundaries[0],
            boundaries[development_count - 1],
            boundaries[development_count],
            boundaries[-1],
            "AUTO_CHRONOLOGICAL_70_30",
        )

    def _regimes(
        self, frames: Sequence[QualificationFrame]
    ) -> dict[datetime, RegimeLabel]:
        result: dict[datetime, RegimeLabel] = {}
        available_atr_percent: list[Decimal] = []
        for frame in frames:
            structure = frame.patterns.structure
            volatility = structure.volatility
            atr_percent = volatility.atr_percent_of_price
            if volatility.available and atr_percent is not None:
                available_atr_percent.append(atr_percent)
                if len(available_atr_percent) >= _VOLATILITY_WARMUP_OBSERVATIONS:
                    reference = Decimal(str(median(available_atr_percent)))
                    volatility_label = (
                        "HIGHER_VOLATILITY"
                        if atr_percent > reference
                        else "LOWER_OR_EQUAL_VOLATILITY"
                    )
                else:
                    volatility_label = "INSUFFICIENT_VOLATILITY_HISTORY"
            else:
                volatility_label = "VOLATILITY_UNAVAILABLE"
            result[frame.patterns.as_of] = RegimeLabel(
                trend=structure.trend.direction.value.upper(),
                volatility=volatility_label,
                calendar_period=frame.patterns.as_of.strftime("%Y-%m"),
            )
        return result

    # ------------------------------------------------------------------
    # Derived candidate / plan / observation records
    # ------------------------------------------------------------------

    def _records(
        self,
        *,
        snapshots,
        frame_by_as_of: dict[datetime, QualificationFrame],
        regimes: dict[datetime, RegimeLabel],
        split: ResolvedSplit,
        config: ValidationConfig,
        planning_parameters: PlanningParameters,
        interval,
    ) -> tuple[ValidationRecord, ...]:
        records: list[ValidationRecord] = []
        for snapshot in snapshots:
            phase = _phase_for(snapshot.as_of, split)
            if phase is None:
                continue
            frame = frame_by_as_of[snapshot.as_of]
            regime = regimes[snapshot.as_of]
            if not snapshot.setups:
                records.append(
                    self._record(
                        phase=phase,
                        kind=ValidationRecordKind.SNAPSHOT,
                        snapshot=snapshot,
                        setup=None,
                        plan=None,
                        observation=None,
                        unavailable=None,
                        regime=regime,
                    )
                )
                continue
            for setup in snapshot.setups:
                plan = (
                    plan_trade(
                        snapshot=snapshot,
                        frame=frame,
                        setup_id=setup.id,
                        parameters=planning_parameters,
                    )
                    if setup.state is SetupState.QUALIFIED
                    else None
                )
                observation = None
                unavailable = None
                if plan is not None and plan.state is PlanState.PLANNABLE:
                    observation, unavailable = self._observe_plan(
                        plan=plan,
                        phase=phase,
                        split=split,
                        horizon=config.observation_horizon_candles,
                        interval=interval,
                    )
                records.append(
                    self._record(
                        phase=phase,
                        kind=ValidationRecordKind.SETUP,
                        snapshot=snapshot,
                        setup=setup,
                        plan=plan,
                        observation=observation,
                        unavailable=unavailable,
                        regime=regime,
                    )
                )
        return tuple(records)

    def _record(
        self,
        *,
        phase: ValidationPhase,
        kind: ValidationRecordKind,
        snapshot,
        setup,
        plan,
        observation,
        unavailable: str | None,
        regime: RegimeLabel,
    ) -> ValidationRecord:
        record_id = fingerprint(
            "validation-record",
            kind,
            snapshot.as_of,
            snapshot.state,
            None if setup is None else setup.id,
            None if plan is None else plan.id,
            None if observation is None else observation.id,
            unavailable,
        )
        return ValidationRecord(
            id=record_id,
            phase=phase,
            kind=kind,
            as_of=snapshot.as_of,
            snapshot_state=snapshot.state,
            setup=setup,
            plan=plan,
            observation=observation,
            observation_unavailable_reason=unavailable,
            regime=regime,
        )

    def _observe_plan(
        self,
        *,
        plan,
        phase: ValidationPhase,
        split: ResolvedSplit,
        horizon: int,
        interval,
    ) -> tuple[OutcomeObservation | None, str | None]:
        phase_end = (
            split.development_end
            if phase is ValidationPhase.DEVELOPMENT
            else split.out_of_sample_end
        )
        assert phase_end is not None
        # A plan at boundary T can only use a future candle opened at T.  The
        # last stored in-cohort candle opens one interval before the cohort end,
        # so development-plan outcomes can never consume OOS candles.
        final_in_phase_open = phase_end - interval
        observed_through = min(
            plan.as_of + interval * (horizon - 1), final_in_phase_open
        )
        if observed_through < plan.as_of:
            return None, "SPLIT_BOUNDARY_NO_WITHIN_COHORT_FUTURE_CANDLE"
        candles = self.candles.get_candles(
            exchange=plan.exchange,
            symbol=plan.symbol,
            timeframe=plan.timeframe,
            start_time=plan.as_of,
            end_time=observed_through,
        ).candles
        levels = ProposedPlanLevels.from_plan(plan)
        observation = observe_outcome(
            journal_id=f"historical-validation:{plan.id}",
            levels=levels,
            candles=candles,
            observed_through=observed_through,
        )
        return observation, None

    # ------------------------------------------------------------------
    # Metrics, diagnostics, and reproducible report material
    # ------------------------------------------------------------------

    def _cohort(
        self,
        *,
        phase: ValidationPhase,
        start: datetime | None,
        end: datetime | None,
        records: tuple[ValidationRecord, ...],
        decision_boundaries: tuple[datetime, ...],
        config: ValidationConfig,
    ) -> ValidationCohort:
        metrics = self._metrics(records, config)
        breakdowns: list[Breakdown] = []
        for dimension, selector in (
            (
                "setup_family",
                lambda r: _enum_text(r.setup.family) if r.setup else "NO_SETUP",
            ),
            (
                "direction",
                lambda r: _enum_text(r.setup.direction) if r.setup else "NO_SETUP",
            ),
            (
                "timeframe",
                lambda r: r.setup.source_timeframes[0] if r.setup else "NO_SETUP",
            ),
            ("market_trend", lambda r: r.regime.trend),
            ("volatility_regime", lambda r: r.regime.volatility),
            ("calendar_period", lambda r: r.regime.calendar_period),
        ):
            buckets: dict[str, list[ValidationRecord]] = defaultdict(list)
            for record in records:
                buckets[selector(record)].append(record)
            for value in sorted(buckets):
                breakdowns.append(
                    Breakdown(
                        dimension, value, self._metrics(tuple(buckets[value]), config)
                    )
                )
        warnings = list(self._cohort_warnings(metrics, phase, config))
        if not decision_boundaries:
            warnings.append("no_decision_boundaries_in_cohort")
        return ValidationCohort(
            phase=phase,
            start=start,
            end=end,
            decision_boundary_count=len(decision_boundaries),
            metrics=metrics,
            breakdowns=tuple(breakdowns),
            warnings=tuple(warnings),
        )

    def _metrics(
        self, records: Sequence[ValidationRecord], config: ValidationConfig
    ) -> CohortMetrics:
        setup_records = tuple(record for record in records if record.setup is not None)
        observations = tuple(
            record.observation for record in records if record.observation is not None
        )
        setup_counts = _counts(
            record.setup.state.value if record.setup else record.snapshot_state.value
            for record in records
        )
        plan_counts = _counts(
            record.plan.state.value if record.plan else "NOT_APPLICABLE"
            for record in records
        )
        status_counts = _counts(
            observation.status.value for observation in observations
        )
        completed = sum(o.status in _COMPLETED_OUTCOMES for o in observations)
        unresolved = sum(
            o.status in {OutcomeStatus.OPEN_AT_CUTOFF, OutcomeStatus.INCOMPLETE_DATA}
            for o in observations
        )
        ambiguous = sum(o.status is OutcomeStatus.AMBIGUOUS for o in observations)
        incomplete = sum(
            o.status is OutcomeStatus.INCOMPLETE_DATA for o in observations
        )

        known_entry = [
            o
            for o in observations
            if o.entry_reached
            or o.status
            in {OutcomeStatus.ENTRY_NOT_REACHED, OutcomeStatus.INVALIDATED_BEFORE_ENTRY}
        ]
        entry_rate = _rate(
            "entry_reached_rate",
            sum(o.entry_reached for o in known_entry),
            len(known_entry),
            "entry touches / observations with a known entry-touch state",
            config.minimum_sample_size,
        )
        clean_ordered = [
            o
            for o in observations
            if o.status in _CLEAN_OUTCOMES and o.entry_ordered and not o.stop_pre_entry
        ]
        stop_rate = _rate(
            "stop_rate_after_ordered_entry",
            sum(o.stop_reached for o in clean_ordered),
            len(clean_ordered),
            "proposed stop touches / clean observations with an ordered entry",
            config.minimum_sample_size,
        )
        target_count = max((len(o.target_levels) for o in observations), default=0)
        target_rates = tuple(
            _rate(
                f"target_{target_index + 1}_hit_rate",
                sum(
                    target_index in o.targets_reached
                    for o in clean_ordered
                    if target_index < len(o.target_levels)
                ),
                sum(target_index < len(o.target_levels) for o in clean_ordered),
                f"ordered target T{target_index + 1} reaches / clean ordered-entry observations proposing T{target_index + 1}",
                config.minimum_sample_size,
            )
            for target_index in range(target_count)
        )
        raw_values, raw_excluded, endpoints = self._raw_r_values(observations)
        friction_values = tuple(
            self._friction_adjusted_r(observation, endpoint, config.friction)
            for observation, endpoint in endpoints
        )
        return CohortMetrics(
            total_records=len(records),
            snapshot_records=sum(
                record.kind is ValidationRecordKind.SNAPSHOT for record in records
            ),
            setup_records=len(setup_records),
            distinct_setup_ids=len({record.setup.id for record in setup_records}),
            setup_state_counts=setup_counts,
            plan_state_counts=plan_counts,
            completed_count=completed,
            unresolved_count=unresolved,
            ambiguous_count=ambiguous,
            incomplete_count=incomplete,
            outcomes_observed_count=len(observations),
            outcomes_not_observed_count=sum(
                record.plan is not None
                and record.plan.state is PlanState.PLANNABLE
                and record.observation is None
                for record in records
            ),
            outcome_status_counts=status_counts,
            entry_reached_rate=entry_rate,
            stop_rate_after_ordered_entry=stop_rate,
            target_hit_rates=target_rates,
            raw_observational_r=_distribution(
                "raw_observational_hypothetical_R",
                raw_values,
                raw_excluded,
                len(observations),
                config.minimum_sample_size,
                "Unambiguous terminal proposed-level R only: STOPPED uses the proposed stop; TARGETS_REACHED uses the furthest reached proposed target. It is an OHLC observation, not realised profit.",
            ),
            friction_adjusted_hypothetical_r=_distribution(
                "friction_adjusted_hypothetical_R",
                friction_values,
                raw_excluded,
                len(observations),
                config.minimum_sample_size,
                "The same eligible proposed-level endpoints after explicit adverse entry/exit slippage and two-sided fees, normalized by original proposed risk. It is hypothetical only, not realised profit.",
            ),
        )

    def _raw_r_values(
        self, observations: Sequence[OutcomeObservation]
    ) -> tuple[
        tuple[Decimal, ...],
        Counter[str],
        tuple[tuple[OutcomeObservation, Decimal], ...],
    ]:
        """Raw observational R values, exclusions and endpoints (shared rules)."""

        return r_values(observations)

    def _friction_adjusted_r(
        self,
        observation: OutcomeObservation,
        endpoint: Decimal,
        assumptions: FrictionAssumptions,
    ) -> Decimal:
        """Hypothetical friction-adjusted R using the shared Step 11/12 rules."""

        return friction_adjusted_r(observation, endpoint, assumptions)

    def _cohort_warnings(
        self, metrics: CohortMetrics, phase: ValidationPhase, config: ValidationConfig
    ) -> tuple[str, ...]:
        warnings: list[str] = []
        if metrics.raw_observational_r.sample_size < config.minimum_sample_size:
            warnings.append(
                f"insufficient_raw_observational_R_sample:{metrics.raw_observational_r.sample_size}/{config.minimum_sample_size}"
            )
        if metrics.total_records < config.minimum_sample_size:
            warnings.append(
                f"insufficient_record_sample:{metrics.total_records}/{config.minimum_sample_size}"
            )
        if metrics.ambiguous_count:
            warnings.append(f"ambiguous_outcomes_separate:{metrics.ambiguous_count}")
        if metrics.incomplete_count:
            warnings.append(f"incomplete_outcomes_separate:{metrics.incomplete_count}")
        if metrics.outcomes_not_observed_count:
            warnings.append(
                f"plans_without_within_cohort_observation:{metrics.outcomes_not_observed_count}"
            )
        return tuple(warnings)

    def _report_warnings(
        self,
        development: ValidationCohort,
        out_of_sample: ValidationCohort | None,
        records: Sequence[ValidationRecord],
        config: ValidationConfig,
    ) -> tuple[str, ...]:
        warnings: list[str] = list(development.warnings)
        if out_of_sample is None:
            warnings.append("out_of_sample_period_unavailable")
        else:
            warnings.extend(out_of_sample.warnings)
            dev_average = development.metrics.raw_observational_r.average
            oos_average = out_of_sample.metrics.raw_observational_r.average
            if (
                dev_average is not None
                and oos_average is not None
                and oos_average < dev_average
            ):
                warnings.append(
                    "out_of_sample_raw_observational_R_deteriorates_vs_development"
                )
        if (
            config.friction.fee_bps
            or config.friction.entry_slippage_bps
            or config.friction.exit_slippage_bps
        ):
            raw = development.metrics.raw_observational_r.average
            adjusted = development.metrics.friction_adjusted_hypothetical_r.average
            if raw is not None and adjusted is not None and raw != adjusted:
                warnings.append("configured_friction_changes_hypothetical_R")
        warnings.extend(_concentration_warnings(records))
        return tuple(sorted(set(warnings)))

    def _strategy_versions(
        self,
        *,
        qp: QualificationParameters,
        pp: PatternLiquidityParameters,
        sp: MarketStructureParameters,
        planning: PlanningParameters,
        friction: FrictionAssumptions,
    ) -> tuple[tuple[str, str], ...]:
        return (
            ("historical_validation_rules", HISTORICAL_VALIDATION_RULES_VERSION),
            ("setup_qualification_rules", RULES_VERSION),
            ("trade_planning_rules", PLANNING_RULES_VERSION),
            ("qualification_parameters", fingerprint("qualification-parameters", qp)),
            ("pattern_liquidity_parameters", fingerprint("pattern-parameters", pp)),
            ("market_structure_parameters", fingerprint("structure-parameters", sp)),
            ("planning_parameters", planning.fingerprint()),
            ("friction_assumptions", friction.fingerprint()),
        )


# ----------------------------------------------------------------------
# Pure report helpers
# ----------------------------------------------------------------------


def _in_range(value: datetime, start: datetime | None, end: datetime | None) -> bool:
    return start is not None and end is not None and start <= value <= end


def _phase_for(value: datetime, split: ResolvedSplit) -> ValidationPhase | None:
    if _in_range(value, split.development_start, split.development_end):
        return ValidationPhase.DEVELOPMENT
    if _in_range(value, split.out_of_sample_start, split.out_of_sample_end):
        return ValidationPhase.OUT_OF_SAMPLE
    return None


# The pure rate/R helpers now live in ``historical_validation.metrics`` so
# Step 12 forward reporting uses the identical definitions instead of a copy.
# They remain importable here under their historical private names.
_enum_text = enum_text
_counts = counts
_rate = rate
_distribution = distribution
_counts_from_counter = counts_from_counter
_terminal_endpoint = terminal_endpoint
_directional_r = directional_r
_r_exclusion = r_exclusion


def _concentration_warnings(records: Sequence[ValidationRecord]) -> tuple[str, ...]:
    positives: list[ValidationRecord] = []
    for record in records:
        if record.observation is None:
            continue
        endpoint = _terminal_endpoint(record.observation)
        if endpoint is not None and _directional_r(record.observation, endpoint) > 0:
            positives.append(record)
    if not positives:
        return ()
    warnings: list[str] = []
    for label, key in (
        (
            "setup_family",
            lambda r: _enum_text(r.setup.family) if r.setup else "NO_SETUP",
        ),
        ("calendar_period", lambda r: r.regime.calendar_period),
    ):
        counts = Counter(key(record) for record in positives)
        value, count = min(counts.items(), key=lambda item: (-item[1], item[0]))
        if count * 100 >= len(positives) * 80:
            warnings.append(
                f"positive_raw_observational_R_concentrated_in_{label}:{value}:{count}/{len(positives)}"
            )
    return tuple(warnings)
