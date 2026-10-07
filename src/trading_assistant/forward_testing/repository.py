"""Durable append-only Step 12 forward-ledger reads and writes over SQLite.

The repository only ever inserts. Re-recording the exact same deterministic
content is a verified no-op, so a restart, a repeated catch-up pass, or the same
candle close processed twice cannot create a duplicate cycle, observation,
paper plan, or outcome version.

A paper plan is unique per ``(exchange, symbol, timeframe, setup_id)``: if a
frozen paper plan already exists for a setup instance, the stored one is
returned unchanged and no second plan is created for the same setup. Existing
rows are never updated or deleted; outcome progress is appended as new versions
chained through ``supersedes_outcome_id``.

Nothing in this layer knows about orders, balances, positions, leverage, or
sizing: those concepts do not exist in the schema.
"""

from __future__ import annotations

from dataclasses import fields
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from trading_assistant.forward_testing.errors import (
    ForwardConflict,
    ForwardError,
    ForwardNotFound,
)
from trading_assistant.forward_testing.models import (
    ForwardCycle,
    ForwardHeartbeat,
    ForwardObservation,
    PaperOutcome,
    PaperPlan,
)
from trading_assistant.forward_testing.parameters import (
    CycleStatus,
    DataHealth,
    HeartbeatStatus,
    canonical_json,
    fingerprint,
)
from trading_assistant.forward_testing.tables import (
    ForwardCycleRow,
    ForwardObservationRow,
    ForwardPaperOutcomeRow,
    ForwardPaperPlanRow,
    ForwardRunnerHeartbeatRow,
)
from trading_assistant.historical_validation.parameters import FrictionAssumptions
from trading_assistant.journaling.types import OutcomeObservation
from trading_assistant.setup_qualification.models import SetupFamily, SetupState
from trading_assistant.trade_planning.models import PlanState

#: Cycle fields that belong to the deterministic identity of one close.
#: Deterministic columns compared when an identical identity is re-recorded.
#: Derived from the stored row shape so JSON-backed columns are compared in their
#: stored text form; only genuinely informational columns are skipped.
_CYCLE_IDENTITY_FIELDS = tuple(
    name
    for name in ForwardCycleRow.__table__.columns.keys()
    if name not in {"recorded_at", "market_data_json", "notes_json"}
)

_OBSERVATION_IDENTITY_FIELDS = tuple(
    name
    for name in ForwardObservationRow.__table__.columns.keys()
    if name != "recorded_at"
)

_PAPER_PLAN_IDENTITY_FIELDS = tuple(
    name
    for name in ForwardPaperPlanRow.__table__.columns.keys()
    if name != "recorded_at"
)

class ForwardLedgerRepository:
    """SQLite-backed immutable forward ledger."""

    def __init__(self, engine: Engine) -> None:
        if engine.dialect.name != "sqlite":
            raise ValueError(
                "ForwardLedgerRepository currently requires a SQLite SQLAlchemy engine"
            )
        self._sessions = sessionmaker(
            bind=engine, class_=Session, expire_on_commit=False
        )

    # ------------------------------------------------------------------
    # Writes (append-only)
    # ------------------------------------------------------------------

    def insert_cycle(self, cycle: ForwardCycle) -> tuple[ForwardCycle, bool]:
        """Insert one cycle, or return the identical already-stored one.

        Returns ``(cycle, created)``: ``created`` is ``False`` when this exact
        deterministic close was already recorded (a restart, a repeated pass, or
        a duplicate catch-up request), which is what keeps forward decisions
        unique per candle close.
        """

        with self._sessions.begin() as session:
            return self._insert_cycle(session, cycle)

    def insert_cycle_bundle(
        self,
        cycle: ForwardCycle,
        observations: tuple[ForwardObservation, ...] | list[ForwardObservation],
        plans: tuple[PaperPlan, ...] | list[PaperPlan],
    ) -> tuple[
        ForwardCycle,
        bool,
        tuple[tuple[ForwardObservation, bool], ...],
        tuple[tuple[PaperPlan, bool], ...],
    ]:
        """Insert one boundary's cycle, observations, and plans atomically.

        The runner skips already-complete cycles, so a cycle row committed
        without its candidate evidence could never be repaired: the boundary
        would stay permanently recorded while its observations and paper
        plan were silently missing. One transaction makes the boundary
        all-or-nothing; a failure anywhere rolls every row of the boundary
        back, and the next pass re-records identical rows.
        """

        with self._sessions.begin() as session:
            stored_cycle, cycle_created = self._insert_cycle(session, cycle)
            stored_observations = tuple(
                self._insert_observation(session, observation)
                for observation in observations
            )
            stored_plans = tuple(
                self._insert_paper_plan(session, plan) for plan in plans
            )
            return stored_cycle, cycle_created, stored_observations, stored_plans

    def _insert_cycle(
        self, session: Session, cycle: ForwardCycle
    ) -> tuple[ForwardCycle, bool]:
        values = _cycle_values(cycle)
        result = session.execute(
            sqlite_insert(ForwardCycleRow)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["cycle_id"])
        )
        # ``rowcount`` is the write itself reporting whether the row landed:
        # comparing ``recorded_at`` instead would misreport a re-recorded row
        # as created whenever two passes share one clock instant.
        created = result.rowcount == 1
        row = session.get(ForwardCycleRow, cycle.cycle_id)
        if row is None:
            raise ForwardError(f"forward cycle {cycle.cycle_id} was not stored")
        _verify_unchanged(
            row,
            values,
            fields=_CYCLE_IDENTITY_FIELDS,
            what=f"forward cycle {cycle.cycle_id}",
        )
        return _cycle_from_row(row), created

    def insert_observation(
        self, observation: ForwardObservation
    ) -> tuple[ForwardObservation, bool]:
        """Insert one candidate observation, or return the identical stored one."""

        with self._sessions.begin() as session:
            return self._insert_observation(session, observation)

    def _insert_observation(
        self, session: Session, observation: ForwardObservation
    ) -> tuple[ForwardObservation, bool]:
        values = _observation_values(observation)
        result = session.execute(
            sqlite_insert(ForwardObservationRow)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["observation_id"])
        )
        created = result.rowcount == 1
        row = session.get(ForwardObservationRow, observation.observation_id)
        if row is None:
            raise ForwardError(
                f"forward observation {observation.observation_id} was not stored"
            )
        _verify_unchanged(
            row,
            values,
            fields=_OBSERVATION_IDENTITY_FIELDS,
            what=f"forward observation {observation.observation_id}",
        )
        return _observation_from_row(row), created

    def insert_paper_plan(self, plan: PaperPlan) -> tuple[PaperPlan, bool]:
        """Insert the frozen paper plan for a setup, or return the stored one.

        Returns ``(plan, created)``. The ``(exchange, symbol, timeframe,
        setup_id)`` uniqueness means one setup instance can never produce two
        paper plans, so a setup that stays plannable across many candles is
        counted once and cannot inflate entry/target rates.
        """

        with self._sessions.begin() as session:
            return self._insert_paper_plan(session, plan)

    def _insert_paper_plan(
        self, session: Session, plan: PaperPlan
    ) -> tuple[PaperPlan, bool]:
        values = _paper_plan_values(plan)
        insert_result = session.execute(
            sqlite_insert(ForwardPaperPlanRow)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["paper_plan_id"])
        )
        plan_inserted = insert_result.rowcount == 1
        existing_for_setup = session.scalars(
            select(ForwardPaperPlanRow).where(
                ForwardPaperPlanRow.exchange == plan.exchange,
                ForwardPaperPlanRow.symbol == plan.symbol,
                ForwardPaperPlanRow.timeframe == plan.timeframe,
                ForwardPaperPlanRow.setup_id == plan.setup_id,
            )
        ).first()
        if existing_for_setup is None:
            raise ForwardError(
                f"paper plan for setup {plan.setup_id} was not stored"
            )
        if existing_for_setup.paper_plan_id != plan.paper_plan_id:
            # A second, later plan for the same setup instance is never
            # created: the first PLANNABLE projection is the frozen one.
            return _paper_plan_from_row(existing_for_setup), False
        row = session.get(ForwardPaperPlanRow, plan.paper_plan_id)
        if row is None:
            raise ForwardError(f"paper plan {plan.paper_plan_id} was not stored")
        _verify_unchanged(
            row,
            values,
            fields=_PAPER_PLAN_IDENTITY_FIELDS,
            what=f"paper plan {plan.paper_plan_id}",
        )
        return _paper_plan_from_row(row), plan_inserted

    def append_outcome(self, outcome: PaperOutcome) -> tuple[PaperOutcome, bool]:
        """Append one outcome version, or return the identical stored one."""

        values = _outcome_values(outcome)
        with self._sessions.begin() as session:
            plan_row = session.get(ForwardPaperPlanRow, outcome.paper_plan_id)
            if plan_row is None:
                raise ForwardNotFound(
                    f"paper plan {outcome.paper_plan_id} is not stored"
                )
            existing = session.scalars(
                select(ForwardPaperOutcomeRow).where(
                    ForwardPaperOutcomeRow.paper_plan_id == outcome.paper_plan_id,
                    ForwardPaperOutcomeRow.payload_json == outcome.payload_json,
                )
            ).first()
            if existing is not None:
                # ``outcome_id``, ``sequence`` and ``supersedes_outcome_id`` are
                # repository-assigned, so the incoming payload carries placeholders
                # for them. ``recorded_at`` is the write-time instant the runner
                # observed the close; the deterministic identity of the outcome
                # itself is the plan, the payload, and the rules/config.
                identity_fields = tuple(
                    field.name for field in fields(PaperOutcome)
                    if field.name
                    not in {
                        "observation",
                        "outcome_id",
                        "sequence",
                        "supersedes_outcome_id",
                        "recorded_at",
                    }
                )
                _verify_unchanged(
                    existing,
                    values,
                    fields=identity_fields,
                    what=f"paper outcome {existing.outcome_id}",
                )
                return _outcome_from_row(existing), False
            next_sequence = (
                session.scalar(
                    select(func.max(ForwardPaperOutcomeRow.sequence)).where(
                        ForwardPaperOutcomeRow.paper_plan_id == outcome.paper_plan_id
                    )
                )
                or 0
            ) + 1
            previous = session.scalars(
                select(ForwardPaperOutcomeRow)
                .where(ForwardPaperOutcomeRow.paper_plan_id == outcome.paper_plan_id)
                .order_by(ForwardPaperOutcomeRow.sequence.desc())
                .limit(1)
            ).first()
            outcome_id = fingerprint(
                "forward-paper-outcome",
                outcome.paper_plan_id,
                outcome.payload_json,
                next_sequence,
            )
            session.execute(
                sqlite_insert(ForwardPaperOutcomeRow)
                .values(
                    **{
                        **values,
                        "outcome_id": outcome_id,
                        "sequence": next_sequence,
                        "supersedes_outcome_id": (
                            None if previous is None else previous.outcome_id
                        ),
                    }
                )
                .on_conflict_do_nothing(index_elements=["outcome_id"])
            )
            row = session.get(ForwardPaperOutcomeRow, outcome_id)
            if row is None:
                raise ForwardError(
                    f"paper outcome for plan {outcome.paper_plan_id} was not stored"
                )
            return _outcome_from_row(row), True

    def append_heartbeat(self, heartbeat: ForwardHeartbeat) -> ForwardHeartbeat:
        """Append one runner heartbeat row."""

        values = _heartbeat_values(heartbeat)
        with self._sessions.begin() as session:
            session.execute(
                sqlite_insert(ForwardRunnerHeartbeatRow)
                .values(**values)
                .on_conflict_do_nothing(index_elements=["heartbeat_id"])
            )
            row = session.get(ForwardRunnerHeartbeatRow, heartbeat.heartbeat_id)
            if row is None:
                raise ForwardError(
                    f"runner heartbeat {heartbeat.heartbeat_id} was not stored"
                )
            return _heartbeat_from_row(row)

    # ------------------------------------------------------------------
    # Reads (SELECT only)
    # ------------------------------------------------------------------

    def cycles(
        self,
        *,
        exchange: str,
        symbol: str,
        timeframe: str,
        limit: int | None = None,
        newest_first: bool = False,
    ) -> tuple[ForwardCycle, ...]:
        statement = select(ForwardCycleRow).where(
            ForwardCycleRow.exchange == exchange,
            ForwardCycleRow.symbol == symbol,
            ForwardCycleRow.timeframe == timeframe,
        )
        statement = statement.order_by(
            ForwardCycleRow.as_of.desc()
            if newest_first
            else ForwardCycleRow.as_of.asc()
        )
        if limit is not None:
            statement = statement.limit(limit)
        with self._sessions() as session:
            rows = session.scalars(statement).all()
            return tuple(_cycle_from_row(row) for row in rows)

    def latest_cycle(
        self, *, exchange: str, symbol: str, timeframe: str
    ) -> ForwardCycle | None:
        values = self.cycles(
            exchange=exchange, symbol=symbol, timeframe=timeframe, limit=1,
            newest_first=True,
        )
        return values[0] if values else None

    def observations(
        self,
        *,
        exchange: str,
        symbol: str,
        timeframe: str,
        limit: int | None = None,
        newest_first: bool = False,
        paper_only: bool = False,
    ) -> tuple[ForwardObservation, ...]:
        statement = select(ForwardObservationRow).where(
            ForwardObservationRow.exchange == exchange,
            ForwardObservationRow.symbol == symbol,
            ForwardObservationRow.timeframe == timeframe,
        )
        if paper_only:
            statement = statement.where(ForwardObservationRow.paper_plan_id.is_not(None))
        statement = statement.order_by(
            ForwardObservationRow.as_of.desc()
            if newest_first
            else ForwardObservationRow.as_of.asc(),
            ForwardObservationRow.observation_id.asc(),
        )
        if limit is not None:
            statement = statement.limit(limit)
        with self._sessions() as session:
            rows = session.scalars(statement).all()
            return tuple(_observation_from_row(row) for row in rows)

    def paper_plans(
        self,
        *,
        exchange: str,
        symbol: str,
        timeframe: str,
        limit: int | None = None,
        newest_first: bool = False,
    ) -> tuple[PaperPlan, ...]:
        statement = (
            select(ForwardPaperPlanRow)
            .where(
                ForwardPaperPlanRow.exchange == exchange,
                ForwardPaperPlanRow.symbol == symbol,
                ForwardPaperPlanRow.timeframe == timeframe,
            )
            .order_by(
                ForwardPaperPlanRow.plan_as_of.desc()
                if newest_first
                else ForwardPaperPlanRow.plan_as_of.asc()
            )
        )
        if limit is not None:
            statement = statement.limit(limit)
        with self._sessions() as session:
            rows = session.scalars(statement).all()
            return tuple(_paper_plan_from_row(row) for row in rows)

    def get_paper_plan(self, paper_plan_id: str) -> PaperPlan:
        with self._sessions() as session:
            row = session.get(ForwardPaperPlanRow, paper_plan_id)
            if row is None:
                raise ForwardNotFound(f"paper plan {paper_plan_id} is not stored")
            return _paper_plan_from_row(row)

    def setup_was_plannable(
        self,
        *,
        exchange: str,
        symbol: str,
        timeframe: str,
        setup_id: str,
        before: datetime,
    ) -> bool:
        """Whether this setup instance recorded a floor-passing plan earlier.

        A stored observation with ``PLANNABLE`` proves the mandatory
        reward-to-risk floor was met at that close (the floor is one of the
        always-evaluated planning rules), so it is the frozen evidence the
        MISSED reason needs: the opportunity was genuinely enterable before the
        market moved, and it is compared strictly before the current boundary.
        """

        statement = (
            select(ForwardObservationRow.observation_id)
            .where(
                ForwardObservationRow.exchange == exchange,
                ForwardObservationRow.symbol == symbol,
                ForwardObservationRow.timeframe == timeframe,
                ForwardObservationRow.setup_id == setup_id,
                ForwardObservationRow.as_of < before,
                ForwardObservationRow.plan_state == PlanState.PLANNABLE.value,
            )
            .limit(1)
        )
        with self._sessions() as session:
            return session.scalars(statement).first() is not None

    def paper_plan_for_setup(
        self, *, exchange: str, symbol: str, timeframe: str, setup_id: str
    ) -> PaperPlan | None:
        with self._sessions() as session:
            row = session.scalars(
                select(ForwardPaperPlanRow).where(
                    ForwardPaperPlanRow.exchange == exchange,
                    ForwardPaperPlanRow.symbol == symbol,
                    ForwardPaperPlanRow.timeframe == timeframe,
                    ForwardPaperPlanRow.setup_id == setup_id,
                )
            ).first()
            return None if row is None else _paper_plan_from_row(row)

    def outcome_versions(self, paper_plan_id: str) -> tuple[PaperOutcome, ...]:
        """Every stored outcome version for one paper plan, oldest first."""

        statement = (
            select(ForwardPaperOutcomeRow)
            .where(ForwardPaperOutcomeRow.paper_plan_id == paper_plan_id)
            .order_by(ForwardPaperOutcomeRow.sequence.asc())
        )
        with self._sessions() as session:
            rows = session.scalars(statement).all()
            return tuple(_outcome_from_row(row) for row in rows)

    def latest_outcomes(
        self, *, exchange: str, symbol: str, timeframe: str
    ) -> tuple[PaperOutcome, ...]:
        """The newest outcome version per paper plan for one instrument."""

        statement = (
            select(ForwardPaperOutcomeRow)
            .join(
                ForwardPaperPlanRow,
                ForwardPaperPlanRow.paper_plan_id
                == ForwardPaperOutcomeRow.paper_plan_id,
            )
            .where(
                ForwardPaperPlanRow.exchange == exchange,
                ForwardPaperPlanRow.symbol == symbol,
                ForwardPaperPlanRow.timeframe == timeframe,
            )
            .order_by(
                ForwardPaperOutcomeRow.paper_plan_id.asc(),
                ForwardPaperOutcomeRow.sequence.asc(),
            )
        )
        with self._sessions() as session:
            rows = session.scalars(statement).all()
        latest: dict[str, PaperOutcome] = {}
        for row in rows:
            latest[row.paper_plan_id] = _outcome_from_row(row)
        return tuple(latest[key] for key in sorted(latest))

    def get_outcome(self, outcome_id: str) -> PaperOutcome:
        """Recover one historical outcome version exactly as it was stored."""

        with self._sessions() as session:
            row = session.get(ForwardPaperOutcomeRow, outcome_id)
            if row is None:
                raise ForwardNotFound(f"paper outcome {outcome_id} is not stored")
            return _outcome_from_row(row)

    def latest_heartbeat(
        self, *, exchange: str, symbol: str, timeframe: str
    ) -> ForwardHeartbeat | None:
        """The most recently written heartbeat for one instrument.

        One runner pass writes its STARTED/PROCESSED/STOPPED rows at a single
        clock instant, so ``recorded_at`` alone cannot order them and the
        content fingerprint is arbitrary; ties are therefore broken by insertion
        order (SQLite ``rowid``), which is what "latest" actually means here —
        otherwise a finished pass could report its own STARTED row as the
        newest state.
        """

        statement = (
            select(ForwardRunnerHeartbeatRow)
            .where(
                ForwardRunnerHeartbeatRow.exchange == exchange,
                ForwardRunnerHeartbeatRow.symbol == symbol,
                ForwardRunnerHeartbeatRow.timeframe == timeframe,
            )
            .order_by(
                ForwardRunnerHeartbeatRow.recorded_at.desc(),
                text("rowid DESC"),
            )
            .limit(1)
        )
        with self._sessions() as session:
            row = session.scalars(statement).first()
            return None if row is None else _heartbeat_from_row(row)

    def counts(
        self, *, exchange: str, symbol: str, timeframe: str
    ) -> dict[str, int]:
        """Exact stored row counts for one instrument (never a fabricated state)."""

        with self._sessions() as session:
            cycles = session.scalar(
                select(func.count())
                .select_from(ForwardCycleRow)
                .where(
                    ForwardCycleRow.exchange == exchange,
                    ForwardCycleRow.symbol == symbol,
                    ForwardCycleRow.timeframe == timeframe,
                )
            )
            observations = session.scalar(
                select(func.count())
                .select_from(ForwardObservationRow)
                .where(
                    ForwardObservationRow.exchange == exchange,
                    ForwardObservationRow.symbol == symbol,
                    ForwardObservationRow.timeframe == timeframe,
                )
            )
            plans = session.scalar(
                select(func.count())
                .select_from(ForwardPaperPlanRow)
                .where(
                    ForwardPaperPlanRow.exchange == exchange,
                    ForwardPaperPlanRow.symbol == symbol,
                    ForwardPaperPlanRow.timeframe == timeframe,
                )
            )
            outcomes = session.scalar(
                select(func.count())
                .select_from(ForwardPaperOutcomeRow)
                .join(
                    ForwardPaperPlanRow,
                    ForwardPaperPlanRow.paper_plan_id
                    == ForwardPaperOutcomeRow.paper_plan_id,
                )
                .where(
                    ForwardPaperPlanRow.exchange == exchange,
                    ForwardPaperPlanRow.symbol == symbol,
                    ForwardPaperPlanRow.timeframe == timeframe,
                )
            )
        return {
            "cycles": int(cycles or 0),
            "observations": int(observations or 0),
            "paper_plans": int(plans or 0),
            "outcome_versions": int(outcomes or 0),
        }


# ----------------------------------------------------------------------
# Row/value mapping helpers
# ----------------------------------------------------------------------


def _verify_unchanged(
    row: Any,
    values: dict[str, Any],
    *,
    fields: tuple[str, ...],
    what: str,
) -> None:
    """Refuse to accept a stored row whose deterministic content differs.

    Non-deterministic, informational columns (for example the market-data fetch
    summary of the pass that first recorded a cycle) are skipped on purpose:
    the row describes the first recording and is never rewritten.
    """

    for name in fields:
        stored = getattr(row, name)
        incoming = values.get(name)
        if stored != incoming:
            raise ForwardConflict(
                f"stored {what} differs from the identical identity being "
                f"recorded (field {name!r}); forward history is append-only"
            )


def _cycle_values(cycle: ForwardCycle) -> dict[str, Any]:
    return {
        "cycle_id": cycle.cycle_id,
        "rules_version": cycle.rules_version,
        "exchange": cycle.exchange,
        "symbol": cycle.symbol,
        "timeframe": cycle.timeframe,
        "as_of": cycle.as_of,
        "candle_open_time": cycle.candle_open_time,
        "recorded_at": cycle.recorded_at,
        "status": cycle.status.value,
        "data_health": cycle.data_health.value,
        "data_health_detail": cycle.data_health_detail,
        "staleness_intervals": cycle.staleness_intervals,
        "missing_candle_count": cycle.missing_candle_count,
        "latest_stored_candle": cycle.latest_stored_candle,
        "snapshot_state": None if cycle.snapshot_state is None else cycle.snapshot_state.value,
        "snapshot_id": cycle.snapshot_id,
        "snapshot_json": cycle.snapshot_json,
        "observation_count": cycle.observation_count,
        "paper_plan_count": cycle.paper_plan_count,
        "setup_state_counts_json": canonical_json(cycle.setup_state_counts),
        "plan_state_counts_json": canonical_json(cycle.plan_state_counts),
        "structure_fingerprint": cycle.structure_fingerprint,
        "pattern_fingerprint": cycle.pattern_fingerprint,
        "source_candle_count": cycle.source_candle_count,
        "strategy_versions_json": canonical_json(cycle.strategy_versions),
        "version_fingerprint": cycle.version_fingerprint,
        "explanation_context_fingerprint": cycle.explanation_context_fingerprint,
        "explanation_manifest_fingerprint": cycle.explanation_manifest_fingerprint,
        "explanation_id": cycle.explanation_id,
        "explanation_headline": cycle.explanation_headline,
        "explanation_renderer_version": cycle.explanation_renderer_version,
        "market_data_json": cycle.market_data_json,
        "notes_json": canonical_json(cycle.notes),
    }


def _cycle_from_row(row: ForwardCycleRow) -> ForwardCycle:
    import json

    return ForwardCycle(
        cycle_id=row.cycle_id,
        rules_version=row.rules_version,
        exchange=row.exchange,
        symbol=row.symbol,
        timeframe=row.timeframe,
        as_of=row.as_of,
        candle_open_time=row.candle_open_time,
        recorded_at=row.recorded_at,
        status=CycleStatus(row.status),
        data_health=DataHealth(row.data_health),
        data_health_detail=row.data_health_detail,
        staleness_intervals=row.staleness_intervals,
        missing_candle_count=row.missing_candle_count,
        latest_stored_candle=row.latest_stored_candle,
        snapshot_state=(
            None if row.snapshot_state is None else SetupState(row.snapshot_state)
        ),
        snapshot_id=row.snapshot_id,
        snapshot_json=row.snapshot_json,
        observation_count=row.observation_count,
        paper_plan_count=row.paper_plan_count,
        setup_state_counts=tuple(
            (str(item[0]), int(item[1]))
            for item in json.loads(row.setup_state_counts_json)
        ),
        plan_state_counts=tuple(
            (str(item[0]), int(item[1]))
            for item in json.loads(row.plan_state_counts_json)
        ),
        structure_fingerprint=row.structure_fingerprint,
        pattern_fingerprint=row.pattern_fingerprint,
        source_candle_count=row.source_candle_count,
        strategy_versions=tuple(
            (str(item[0]), str(item[1]))
            for item in json.loads(row.strategy_versions_json)
        ),
        version_fingerprint=row.version_fingerprint,
        explanation_context_fingerprint=row.explanation_context_fingerprint,
        explanation_manifest_fingerprint=row.explanation_manifest_fingerprint,
        explanation_id=row.explanation_id,
        explanation_headline=row.explanation_headline,
        explanation_renderer_version=row.explanation_renderer_version,
        market_data_json=row.market_data_json,
        notes=tuple(str(item) for item in json.loads(row.notes_json)),
    )


def _observation_values(observation: ForwardObservation) -> dict[str, Any]:
    return {
        "observation_id": observation.observation_id,
        "cycle_id": observation.cycle_id,
        "observation_rules_version": observation.observation_rules_version,
        "exchange": observation.exchange,
        "symbol": observation.symbol,
        "timeframe": observation.timeframe,
        "source_timeframes_json": canonical_json(observation.source_timeframes),
        "as_of": observation.as_of,
        "candle_open_time": observation.candle_open_time,
        "recorded_at": observation.recorded_at,
        "snapshot_state": observation.snapshot_state.value,
        "snapshot_id": observation.snapshot_id,
        "setup_id": observation.setup_id,
        "setup_family": observation.setup_family.value,
        "setup_direction": observation.setup_direction,
        "setup_state": observation.setup_state.value,
        "setup_created_at": observation.setup_created_at,
        "setup_seed_event_id": observation.setup_seed_event_id,
        "setup_reference_id": observation.setup_reference_id,
        "setup_terminal_reason": observation.setup_terminal_reason,
        "setup_ended_at": observation.setup_ended_at,
        "passed_rules_json": canonical_json(observation.passed_rules),
        "failed_rules_json": canonical_json(observation.failed_rules),
        "pending_rules_json": canonical_json(observation.pending_rules),
        "veto_rules_json": canonical_json(observation.veto_rules),
        "setup_json": observation.setup_json,
        "plan_id": observation.plan_id,
        "plan_state": (
            None if observation.plan_state is None else observation.plan_state.value
        ),
        "plan_json": observation.plan_json,
        "plan_entry": observation.plan_entry,
        "plan_invalidation": observation.plan_invalidation,
        "plan_stop": observation.plan_stop,
        "plan_risk_per_unit": observation.plan_risk_per_unit,
        "plan_targets_json": canonical_json(
            [format(value, "f") for value in observation.plan_targets]
        ),
        "plan_target_r_json": canonical_json(
            [
                None if value is None else format(value, "f")
                for value in observation.plan_target_r_multiples
            ]
        ),
        "plan_config_fingerprint": observation.plan_config_fingerprint,
        "planning_rules_version": observation.planning_rules_version,
        "paper_plan_id": observation.paper_plan_id,
        "no_trade_reason": observation.no_trade_reason,
        "data_health": observation.data_health.value,
        "missing_candle_count": observation.missing_candle_count,
        "market_trend": observation.market_trend,
        "atr_percent_of_price": observation.atr_percent_of_price,
        "calendar_period": observation.calendar_period,
        "structure_fingerprint": observation.structure_fingerprint,
        "pattern_fingerprint": observation.pattern_fingerprint,
        "version_fingerprint": observation.version_fingerprint,
    }


def _observation_from_row(row: ForwardObservationRow) -> ForwardObservation:
    import json

    return ForwardObservation(
        observation_id=row.observation_id,
        cycle_id=row.cycle_id,
        observation_rules_version=row.observation_rules_version,
        exchange=row.exchange,
        symbol=row.symbol,
        timeframe=row.timeframe,
        source_timeframes=tuple(
            str(item) for item in json.loads(row.source_timeframes_json)
        ),
        as_of=row.as_of,
        candle_open_time=row.candle_open_time,
        recorded_at=row.recorded_at,
        snapshot_state=SetupState(row.snapshot_state),
        snapshot_id=row.snapshot_id,
        setup_id=row.setup_id,
        setup_family=SetupFamily(row.setup_family),
        setup_direction=row.setup_direction,
        setup_state=SetupState(row.setup_state),
        setup_created_at=row.setup_created_at,
        setup_seed_event_id=row.setup_seed_event_id,
        setup_reference_id=row.setup_reference_id,
        setup_terminal_reason=row.setup_terminal_reason,
        setup_ended_at=row.setup_ended_at,
        passed_rules=tuple(str(item) for item in json.loads(row.passed_rules_json)),
        failed_rules=tuple(str(item) for item in json.loads(row.failed_rules_json)),
        pending_rules=tuple(str(item) for item in json.loads(row.pending_rules_json)),
        veto_rules=tuple(str(item) for item in json.loads(row.veto_rules_json)),
        setup_json=row.setup_json,
        plan_id=row.plan_id,
        plan_state=None if row.plan_state is None else PlanState(row.plan_state),
        plan_json=row.plan_json,
        plan_entry=row.plan_entry,
        plan_invalidation=row.plan_invalidation,
        plan_stop=row.plan_stop,
        plan_risk_per_unit=row.plan_risk_per_unit,
        plan_targets=tuple(
            _decimal_text(item) for item in json.loads(row.plan_targets_json)
        ),
        plan_target_r_multiples=tuple(
            None if item is None else _decimal_text(item)
            for item in json.loads(row.plan_target_r_json)
        ),
        plan_config_fingerprint=row.plan_config_fingerprint,
        planning_rules_version=row.planning_rules_version,
        paper_plan_id=row.paper_plan_id,
        no_trade_reason=row.no_trade_reason,
        data_health=DataHealth(row.data_health),
        missing_candle_count=row.missing_candle_count,
        market_trend=row.market_trend,
        atr_percent_of_price=row.atr_percent_of_price,
        calendar_period=row.calendar_period,
        structure_fingerprint=row.structure_fingerprint,
        pattern_fingerprint=row.pattern_fingerprint,
        version_fingerprint=row.version_fingerprint,
    )


def _paper_plan_values(plan: PaperPlan) -> dict[str, Any]:
    return {
        "paper_plan_id": plan.paper_plan_id,
        "observation_id": plan.observation_id,
        "cycle_id": plan.cycle_id,
        "ledger_rules_version": plan.ledger_rules_version,
        "exchange": plan.exchange,
        "symbol": plan.symbol,
        "timeframe": plan.timeframe,
        "setup_id": plan.setup_id,
        "snapshot_id": plan.snapshot_id,
        "family": plan.family.value,
        "direction": plan.direction,
        "plan_id": plan.plan_id,
        "plan_as_of": plan.plan_as_of,
        "recorded_at": plan.recorded_at,
        "entry_level": plan.entry,
        "stop_level": plan.stop,
        "invalidation_level": plan.invalidation,
        "risk_per_unit": plan.risk_per_unit,
        "targets_json": canonical_json([format(value, "f") for value in plan.targets]),
        "target_r_json": canonical_json(
            [
                None if value is None else format(value, "f")
                for value in plan.target_r_multiples
            ]
        ),
        "plan_json": plan.plan_json,
        "plan_config_fingerprint": plan.plan_config_fingerprint,
        "planning_rules_version": plan.planning_rules_version,
        "strategy_versions_json": canonical_json(plan.strategy_versions),
        "version_fingerprint": plan.version_fingerprint,
        "friction_assumptions_json": canonical_json(plan.friction),
        "friction_fingerprint": plan.friction_fingerprint,
        "forward_parameters_fingerprint": plan.forward_parameters_fingerprint,
        "observation_horizon_candles": plan.observation_horizon_candles,
        "data_health": plan.data_health.value,
    }


def _paper_plan_from_row(row: ForwardPaperPlanRow) -> PaperPlan:
    import json

    return PaperPlan(
        paper_plan_id=row.paper_plan_id,
        observation_id=row.observation_id,
        cycle_id=row.cycle_id,
        ledger_rules_version=row.ledger_rules_version,
        exchange=row.exchange,
        symbol=row.symbol,
        timeframe=row.timeframe,
        setup_id=row.setup_id,
        snapshot_id=row.snapshot_id,
        family=SetupFamily(row.family),
        direction=row.direction,
        plan_id=row.plan_id,
        plan_as_of=row.plan_as_of,
        recorded_at=row.recorded_at,
        entry=row.entry_level,
        stop=row.stop_level,
        invalidation=row.invalidation_level,
        risk_per_unit=row.risk_per_unit,
        targets=tuple(
            _decimal_text(item) for item in json.loads(row.targets_json)
        ),
        target_r_multiples=tuple(
            None if item is None else _decimal_text(item)
            for item in json.loads(row.target_r_json)
        ),
        plan_json=row.plan_json,
        plan_config_fingerprint=row.plan_config_fingerprint,
        planning_rules_version=row.planning_rules_version,
        strategy_versions=tuple(
            (str(item[0]), str(item[1]))
            for item in json.loads(row.strategy_versions_json)
        ),
        version_fingerprint=row.version_fingerprint,
        friction=_friction_from_json(row.friction_assumptions_json),
        friction_fingerprint=row.friction_fingerprint,
        forward_parameters_fingerprint=row.forward_parameters_fingerprint,
        observation_horizon_candles=row.observation_horizon_candles,
        data_health=DataHealth(row.data_health),
    )


def _outcome_values(outcome: PaperOutcome) -> dict[str, Any]:
    observation = outcome.observation
    return {
        "outcome_id": outcome.outcome_id,
        "paper_plan_id": outcome.paper_plan_id,
        "observation_id": outcome.observation_id,
        "sequence": outcome.sequence,
        "supersedes_outcome_id": outcome.supersedes_outcome_id,
        "ledger_rules_version": outcome.ledger_rules_version,
        "observation_rules_version": outcome.observation_rules_version,
        "config_fingerprint": outcome.config_fingerprint,
        "recorded_at": outcome.recorded_at,
        "observed_through": observation.observed_through,
        "evaluated_through": observation.evaluated_through,
        "status": observation.status.value,
        "entry_reached": observation.entry_reached,
        "entry_ordered": observation.entry_ordered,
        "entry_timestamp": observation.entry_timestamp,
        "stop_reached": observation.stop_reached,
        "stop_pre_entry": observation.stop_pre_entry,
        "targets_reached_json": canonical_json(observation.targets_reached),
        "ambiguous": observation.ambiguous,
        "ambiguity_kind": observation.ambiguity_kind,
        "incomplete": observation.incomplete,
        "missing_candle_count": observation.missing_candle_count,
        "expected_candle_count": observation.expected_candle_count,
        "observed_candle_count": observation.observed_candle_count,
        "payload_json": outcome.payload_json,
    }


def _outcome_from_row(row: ForwardPaperOutcomeRow) -> PaperOutcome:
    payload = _json_object(row.payload_json, what="paper outcome payload")
    observation = OutcomeObservation.from_json_dict(payload)
    return PaperOutcome(
        outcome_id=row.outcome_id,
        paper_plan_id=row.paper_plan_id,
        observation_id=row.observation_id,
        sequence=row.sequence,
        supersedes_outcome_id=row.supersedes_outcome_id,
        ledger_rules_version=row.ledger_rules_version,
        observation_rules_version=row.observation_rules_version,
        config_fingerprint=row.config_fingerprint,
        recorded_at=row.recorded_at,
        payload_json=row.payload_json,
        observation=observation,
    )


def _heartbeat_values(heartbeat: ForwardHeartbeat) -> dict[str, Any]:
    return {
        "heartbeat_id": heartbeat.heartbeat_id,
        "runner_rules_version": heartbeat.runner_rules_version,
        "recorded_at": heartbeat.recorded_at,
        "status": heartbeat.status.value,
        "exchange": heartbeat.exchange,
        "symbol": heartbeat.symbol,
        "timeframe": heartbeat.timeframe,
        "detail": heartbeat.detail,
        "cycles_processed": heartbeat.cycles_processed,
        "observations_recorded": heartbeat.observations_recorded,
        "paper_plans_created": heartbeat.paper_plans_created,
        "outcomes_recorded": heartbeat.outcomes_recorded,
        "pending_boundaries": heartbeat.pending_boundaries,
        "latest_cycle_as_of": heartbeat.latest_cycle_as_of,
        "last_error": heartbeat.last_error,
        "error_type": heartbeat.error_type,
        "market_data_json": heartbeat.market_data_json,
    }


def _heartbeat_from_row(row: ForwardRunnerHeartbeatRow) -> ForwardHeartbeat:
    return ForwardHeartbeat(
        heartbeat_id=row.heartbeat_id,
        runner_rules_version=row.runner_rules_version,
        recorded_at=row.recorded_at,
        status=HeartbeatStatus(row.status),
        exchange=row.exchange,
        symbol=row.symbol,
        timeframe=row.timeframe,
        detail=row.detail,
        cycles_processed=row.cycles_processed,
        observations_recorded=row.observations_recorded,
        paper_plans_created=row.paper_plans_created,
        outcomes_recorded=row.outcomes_recorded,
        pending_boundaries=row.pending_boundaries,
        latest_cycle_as_of=row.latest_cycle_as_of,
        last_error=row.last_error,
        error_type=row.error_type,
        market_data_json=row.market_data_json,
    )


def _json_object(text: str, *, what: str) -> dict[str, Any]:
    import json

    try:
        payload = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise ForwardError(f"stored {what} is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ForwardError(f"stored {what} is not a JSON object")
    return payload


def _friction_from_json(text: str) -> FrictionAssumptions:
    from decimal import Decimal

    payload = _json_object(text, what="friction assumptions")
    try:
        return FrictionAssumptions(
            version=str(payload["version"]),
            fee_bps=Decimal(str(payload["fee_bps"])),
            entry_slippage_bps=Decimal(str(payload["entry_slippage_bps"])),
            exit_slippage_bps=Decimal(str(payload["exit_slippage_bps"])),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ForwardError(f"stored friction assumptions are malformed: {exc}") from exc


def _decimal_text(value: object):
    from decimal import Decimal, InvalidOperation

    if not isinstance(value, str):
        raise ForwardError("stored forward decimal must be exact decimal text")
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ForwardError("stored forward decimal is not a valid decimal") from exc
    if not parsed.is_finite():
        raise ForwardError("stored forward decimal must be finite")
    return parsed


__all__ = ["ForwardLedgerRepository"]
