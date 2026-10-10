"""Immutable Step 7 domain types: journal records, decisions and observations.

Nothing here is an order, fill, position, balance, or profit claim. A
``JournalRecord`` preserves exactly what Steps 5-6 produced (canonical JSON
projections of the setup snapshot and, where one exists, the plan result). A
``DecisionRecord`` is one explicit human decision appended to that history. An
``OutcomeObservation`` is a deterministic statement about how the market moved
relative to a proposed plan — never about an executed trade.

All types are frozen dataclasses with slots, like Steps 3-6, so a stored
historical value cannot be edited in place: corrections are new rows.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from trading_assistant.journaling.errors import JournalError
from trading_assistant.market_data.types import CandleGap
from trading_assistant.market_structure.snapshot import to_jsonable
from trading_assistant.pattern_liquidity.events import Direction
from trading_assistant.setup_qualification.models import SetupFamily, SetupState
from trading_assistant.trade_planning.models import PlanState


class RecordKind(StrEnum):
    """What one immutable journal record describes.

    ``SNAPSHOT``
        A whole Step 5 snapshot (its aggregate state and every setup it
        contains) as an audit/replay observation, typically a NO_SETUP or WATCH
        moment. No plan can be attached to it.
    ``SETUP``
        One specific Step 5 ``SetupResult`` inside its snapshot, optionally with
        the Step 6 ``TradePlanResult`` produced from it (including explicit
        NO_PLAN/INVALID refusals).
    """

    SNAPSHOT = "snapshot"
    SETUP = "setup"


class DecisionState(StrEnum):
    """Explicit decision vocabulary; nothing else is a decision.

    ``PENDING``
        Recorded statement that the decision is still open. It must be written
        explicitly and is never implied by the absence of a decision.
    ``ACCEPTED``
        Bailey explicitly accepted the proposed plan for consideration. The
        journal records the decision only: it places no order, executes nothing,
        and asserts nothing about profitability.
    ``REJECTED``
        The proposal was considered and actively declined.
    ``SKIPPED``
        The proposal was let pass without an accept/reject judgement (for
        example the window lapsed). Also not execution and not evidence.
    """

    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    SKIPPED = "SKIPPED"


class OutcomeStatus(StrEnum):
    """Deterministic final state of one proposed-plan observation.

    ``ENTRY_NOT_REACHED``
        The window was fully observed and the proposed entry was never touched.
    ``INVALIDATED_BEFORE_ENTRY``
        The stop condition was met before the entry was ever touched, so the
        proposal's premise failed without an entry.
    ``STOPPED``
        The entry was touched first and the stop condition was met before any
        target.
    ``STOPPED_AFTER_TARGETS``
        One or more targets were reached after the entry and then the stop.
    ``TARGETS_REACHED``
        Every proposed target was reached after the entry, with no prior stop.
    ``OPEN_AT_CUTOFF``
        The entry was reached and neither the stop nor every target was reached
        by the fully observed cutoff; the trajectory is still open there.
    ``AMBIGUOUS``
        Two outcome-relevant levels were touched in the same candle and the
        OHLC data cannot establish which came first. The favourable result is
        never chosen: the trajectory stops at the ambiguity.
    ``INCOMPLETE_DATA``
        Required candles are missing and no terminal event was established before
        the gap. The outcome is UNKNOWN, never converted into a win or a loss.
    """

    ENTRY_NOT_REACHED = "ENTRY_NOT_REACHED"
    INVALIDATED_BEFORE_ENTRY = "INVALIDATED_BEFORE_ENTRY"
    STOPPED = "STOPPED"
    STOPPED_AFTER_TARGETS = "STOPPED_AFTER_TARGETS"
    TARGETS_REACHED = "TARGETS_REACHED"
    OPEN_AT_CUTOFF = "OPEN_AT_CUTOFF"
    AMBIGUOUS = "AMBIGUOUS"
    INCOMPLETE_DATA = "INCOMPLETE_DATA"


class OutcomeEventKind(StrEnum):
    """Which proposed level was touched by one recorded event."""

    ENTRY = "entry"
    STOP = "stop"
    TARGET = "target"


class OutcomeEventOrdering(StrEnum):
    """How an event sits in the deterministic trajectory.

    ``PRE_ENTRY``
        The level was touched before the entry; the touch is recorded for audit
        but does not count toward the post-entry trajectory.
    ``ORDERED``
        The touch is part of the deterministic post-entry (or entry) ordering.
    ``AMBIGUOUS``
        The touch shares a candle with another outcome-relevant touch whose
        ordering the OHLC data cannot establish.
    """

    PRE_ENTRY = "pre_entry"
    ORDERED = "ordered"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True, slots=True)
class OutcomeEvent:
    """One recorded level touch inside an observation window."""

    sequence: int
    kind: OutcomeEventKind
    target_index: int | None
    level: Decimal
    candle_timestamp: datetime
    candle_index: int
    ordering: OutcomeEventOrdering
    co_touched: bool

    @property
    def token(self) -> str:
        """Short deterministic label, e.g. ``entry``, ``stop``, ``target_1``."""

        if self.kind is OutcomeEventKind.TARGET:
            assert self.target_index is not None
            return f"target_{self.target_index + 1}"
        return self.kind.value

    def to_json_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


@dataclass(frozen=True, slots=True)
class JournalRecord:
    """One immutable record of what the system knew and proposed.

    Identity fields are deterministic fingerprints; the two JSON payloads are
    the exact canonical projections of the Step 5 snapshot and the Step 6 plan,
    so the historical record survives any future code or configuration change.
    """

    journal_id: str
    record_kind: RecordKind
    journal_rules_version: str
    exchange: str
    symbol: str
    timeframe: str
    source_timeframes: tuple[str, ...]
    setup_id: str | None
    setup_family: SetupFamily | None
    setup_direction: Direction | None
    setup_state: SetupState
    setup_created_at: datetime | None
    setup_as_of: datetime
    setup_seed_event_id: str | None
    setup_reference_id: str | None
    setup_config_fingerprint: str
    setup_rules_version: str
    setup_snapshot_id: str
    setup_snapshot_json: str
    plan_id: str | None
    plan_state: PlanState | None
    planning_as_of: datetime | None
    plan_json: str | None
    plan_config_fingerprint: str | None
    planning_rules_version: str | None

    @property
    def setup_snapshot_payload(self) -> dict[str, Any]:
        """The exact stored Step 5 snapshot projection as a fresh dictionary."""

        return _parse_payload(self.setup_snapshot_json, what="setup snapshot")

    @property
    def plan_payload(self) -> dict[str, Any] | None:
        """The exact stored Step 6 plan projection, or ``None`` when absent."""

        if self.plan_json is None:
            return None
        return _parse_payload(self.plan_json, what="plan")

    @property
    def has_plan(self) -> bool:
        return self.plan_id is not None

    def to_json_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    """One append-only decision row, self-describing for audit.

    The row repeats the traceability it needs (setup/plan ids, instrument,
    timeframes, as_ofs, fingerprints) directly from the immutable journal record
    it references, so an audit can read the decision without re-deriving
    anything. ``supersedes_decision_id`` links a correction to the row it
    supersedes; the earlier row is never modified.
    """

    decision_id: str
    journal_id: str
    sequence: int
    supersedes_decision_id: str | None
    decision: DecisionState
    decided_at: datetime
    reason: str | None
    decision_rules_version: str
    record_kind: RecordKind
    setup_id: str | None
    plan_id: str | None
    setup_family: SetupFamily | None
    direction: Direction | None
    setup_state: SetupState
    exchange: str
    symbol: str
    timeframe: str
    source_timeframes: tuple[str, ...]
    setup_as_of: datetime
    planning_as_of: datetime | None
    setup_config_fingerprint: str
    plan_config_fingerprint: str | None
    setup_rules_version: str
    planning_rules_version: str | None
    setup_snapshot_id: str

    def to_json_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


@dataclass(frozen=True, slots=True)
class ProposedPlanLevels:
    """The minimal immutable projection of one PLANNABLE plan.

    Outcome observation needs exactly these numbers and the instrument/window
    identity; nothing else from the plan can influence a deterministic outcome.
    The projection is built either from a live ``TradePlanResult`` or from the
    plan JSON stored on the journal record, so an observation is always computed
    from the journaled snapshot rather than from re-derived future state.
    """

    plan_id: str
    exchange: str
    symbol: str
    timeframe: str
    direction: Direction
    entry: Decimal
    stop: Decimal
    targets: tuple[Decimal, ...]
    risk_per_unit: Decimal
    as_of: datetime
    setup_id: str | None

    def __post_init__(self) -> None:
        for name in ("entry", "stop", "risk_per_unit"):
            value = getattr(self, name)
            _require_price(value, name=name)
        if self.risk_per_unit <= 0:
            raise ValueError("risk_per_unit must be positive")
        if not isinstance(self.targets, tuple) or not self.targets:
            raise ValueError("targets must be a non-empty immutable tuple")
        for index, target in enumerate(self.targets):
            _require_price(target, name=f"targets[{index}]")
        if len(set(self.targets)) != len(self.targets):
            raise ValueError("targets must not repeat the same level")
        if self.direction == "bullish":
            if not self.stop < self.entry:
                raise ValueError("a bullish plan requires stop < entry")
            if any(not target > self.entry for target in self.targets):
                raise ValueError("a bullish plan requires every target > entry")
        elif self.direction == "bearish":
            if not self.stop > self.entry:
                raise ValueError("a bearish plan requires stop > entry")
            if any(not target < self.entry for target in self.targets):
                raise ValueError("a bearish plan requires every target < entry")
        else:
            raise ValueError("direction must be 'bullish' or 'bearish'")

    @classmethod
    def from_plan(cls, plan: Any) -> ProposedPlanLevels:
        """Build the projection from a Step 6 ``TradePlanResult``.

        Refuses (``ValueError``) unless the plan is PLANNABLE and every required
        level is present: an observation cannot be invented for a refusal.
        """

        from trading_assistant.trade_planning.models import (
            PlanState as _PlanState,
        )
        from trading_assistant.trade_planning.models import (
            TradePlanResult,
        )

        if not isinstance(plan, TradePlanResult):
            raise TypeError("plan must be a Step 6 TradePlanResult")
        if plan.state is not _PlanState.PLANNABLE:
            raise ValueError(
                f"plan {plan.id} state is {plan.state}; only PLANNABLE plans "
                "carry proposed levels and can be observed"
            )
        values = {
            "entry": plan.entry.value,
            "stop": plan.stop.value,
            "risk_per_unit": plan.risk_per_unit,
        }
        missing = [name for name, value in values.items() if value is None]
        if missing:
            raise ValueError(
                f"PLANNABLE plan {plan.id} is missing required levels: "
                + ", ".join(sorted(missing))
            )
        targets = tuple(target.level.value for target in plan.targets)
        if any(value is None for value in targets):
            raise ValueError(f"PLANNABLE plan {plan.id} has a target without a value")
        if plan.as_of is None:
            raise ValueError(f"PLANNABLE plan {plan.id} has no as_of")
        if plan.direction is None:
            raise ValueError(f"PLANNABLE plan {plan.id} has no direction")
        return cls(
            plan_id=plan.id,
            exchange=plan.exchange,
            symbol=plan.symbol,
            timeframe=plan.timeframe,
            direction=plan.direction,
            entry=values["entry"],
            stop=values["stop"],
            targets=targets,
            risk_per_unit=values["risk_per_unit"],
            as_of=plan.as_of,
            setup_id=plan.setup_id,
        )

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> ProposedPlanLevels:
        """Decode the projection from the exact plan JSON stored on a record.

        Raises ``ValueError`` when the stored plan is not PLANNABLE or a value is
        unusable, and ``TypeError`` when a stored field has the wrong JSON type.
        """

        if not isinstance(payload, dict):
            raise TypeError("stored plan payload must be a JSON object")
        state_text = payload.get("state")
        if state_text != PlanState.PLANNABLE.value:
            raise ValueError(
                f"stored plan state is {state_text!r}; only PLANNABLE plans can "
                "be observed"
            )
        direction = payload.get("direction")
        if direction not in ("bullish", "bearish"):
            raise ValueError(
                f"stored plan direction must be bullish or bearish, not {direction!r}"
            )
        entry = _payload_decimal(payload, "entry", nested="value")
        stop = _payload_decimal(payload, "stop", nested="value")
        risk = _payload_decimal(payload, "risk_per_unit")
        raw_targets = payload.get("targets")
        if not isinstance(raw_targets, list) or not raw_targets:
            raise ValueError("stored PLANNABLE plan must contain at least one target")
        targets = []
        for index, target in enumerate(raw_targets):
            if not isinstance(target, dict) or not isinstance(
                target.get("level"), dict
            ):
                raise TypeError(f"stored plan target {index} is malformed")
            targets.append(_payload_decimal(target["level"], "value"))
        as_of = _payload_datetime(payload, "as_of")
        plan_id = payload.get("id")
        if not isinstance(plan_id, str) or not plan_id:
            raise ValueError("stored plan payload has no plan id")
        setup_id = payload.get("setup_id")
        if setup_id is not None and not isinstance(setup_id, str):
            raise ValueError("stored plan setup_id must be a string or null")
        return cls(
            plan_id=plan_id,
            exchange=_payload_text(payload, "exchange"),
            symbol=_payload_text(payload, "symbol"),
            timeframe=_payload_text(payload, "timeframe"),
            direction=direction,
            entry=entry,
            stop=stop,
            targets=tuple(targets),
            risk_per_unit=risk,
            as_of=as_of,
            setup_id=setup_id,
        )

    def to_json_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


@dataclass(frozen=True, slots=True)
class OutcomeVersion:
    """One stored observation version: content plus its append-only position.

    ``sequence`` is the 1-based append order for the journal record and
    ``supersedes_outcome_id`` the previously stored version, so the full
    observation history of one record is auditable and a historical T1 row stays
    identifiable after a T2 observation exists.
    """

    sequence: int
    supersedes_outcome_id: str | None
    observation: OutcomeObservation

    def to_json_dict(self) -> dict[str, Any]:
        return to_jsonable(self)


@dataclass(frozen=True, slots=True)
class OutcomeObservation:
    """Deterministic market observation of one journaled proposed plan.

    Every field is derived only from the journaled plan levels, the stored
    candles up to ``observed_through``, and the versioned observation
    configuration. ``mfe``/``mae`` are proposed-entry-relative maximum
    favourable/adverse *price moves* over the evaluated window, clamped at zero,
    with matching unit-neutral R multiples; the raw evaluated extremes are
    stored alongside so no information is lost.
    """

    id: str
    journal_id: str
    plan_id: str
    setup_id: str | None
    exchange: str
    symbol: str
    timeframe: str
    direction: Direction
    entry_level: Decimal
    stop_level: Decimal
    target_levels: tuple[Decimal, ...]
    risk_per_unit: Decimal
    window_start: datetime
    observed_through: datetime
    evaluated_through: datetime | None
    status: OutcomeStatus
    entry_reached: bool
    entry_ordered: bool
    entry_timestamp: datetime | None
    entry_candle_index: int | None
    stop_reached: bool
    stop_pre_entry: bool
    stop_timestamp: datetime | None
    targets_reached: tuple[int, ...]
    targets_pre_entry: tuple[int, ...]
    first_touch_order: tuple[tuple[str, ...], ...]
    events: tuple[OutcomeEvent, ...]
    ambiguous: bool
    ambiguity_kind: str | None
    ambiguity_timestamp: datetime | None
    incomplete: bool
    missing_candle_count: int
    missing_ranges: tuple[CandleGap, ...]
    expected_candle_count: int
    observed_candle_count: int
    evaluated_low: Decimal | None
    evaluated_low_timestamp: datetime | None
    evaluated_high: Decimal | None
    evaluated_high_timestamp: datetime | None
    post_entry_low: Decimal | None
    post_entry_high: Decimal | None
    mfe_price_move: Decimal | None
    mae_price_move: Decimal | None
    mfe_r: Decimal | None
    mae_r: Decimal | None
    config_fingerprint: str
    observation_rules_version: str
    #: Resolution timeframe declared by the observation rules version, or
    #: ``None`` for rules versions without a resolution policy (v1). Today the
    #: only approved resolution granularity is the 1-minute series used to
    #: order events inside an ambiguous higher-timeframe candle.
    resolution_timeframe: str | None = None
    #: True when an ambiguity arose and the resolution policy was consulted.
    resolution_attempted: bool = False
    #: True when genuine stored resolution candles actually determined an
    #: ordering (the trajectory continued or terminated on that evidence).
    resolution_used: bool = False
    #: Deterministic code describing the decisive resolution consultation:
    #: ``resolution_used``, or one of the unresolved reasons recorded by
    #: ``journaling.observation``. ``None`` when no ambiguity arose.
    resolution_reason: str | None = None
    #: Expected/present/missing resolution-candle counts over every candle the
    #: resolution policy consulted in this observation (0 when not attempted).
    resolution_expected_candles: int = 0
    resolution_present_candles: int = 0
    resolution_missing_candles: int = 0
    #: Inclusive missing ranges of resolution-candle open times (audit trail).
    resolution_missing_ranges: tuple[CandleGap, ...] = ()

    @classmethod
    def from_json_dict(cls, payload: dict[str, Any]) -> OutcomeObservation:
        """Strictly decode one stored observation payload back into this type.

        This is how a historical T1 observation stays recoverable after a later
        T2 observation: the stored canonical JSON is lossless and is decoded
        without touching current market data or code paths.
        """

        if not isinstance(payload, dict):
            raise JournalError("stored outcome payload must be a JSON object")
        try:
            events = tuple(
                OutcomeEvent(
                    sequence=_payload_int(event, "sequence"),
                    kind=OutcomeEventKind(_payload_text(event, "kind")),
                    target_index=_payload_optional_int(event, "target_index"),
                    level=_payload_decimal(event, "level"),
                    candle_timestamp=_payload_datetime(event, "candle_timestamp"),
                    candle_index=_payload_int(event, "candle_index"),
                    ordering=OutcomeEventOrdering(_payload_text(event, "ordering")),
                    co_touched=_payload_bool(event, "co_touched"),
                )
                for event in _payload_list(payload, "events")
            )
            gaps = tuple(
                CandleGap(
                    start=_payload_datetime(gap, "start"),
                    end=_payload_datetime(gap, "end"),
                    missing_count=_payload_int(gap, "missing_count"),
                )
                for gap in _payload_list(payload, "missing_ranges")
            )
            return cls(
                id=_payload_text(payload, "id"),
                journal_id=_payload_text(payload, "journal_id"),
                plan_id=_payload_text(payload, "plan_id"),
                setup_id=_payload_optional_text(payload, "setup_id"),
                exchange=_payload_text(payload, "exchange"),
                symbol=_payload_text(payload, "symbol"),
                timeframe=_payload_text(payload, "timeframe"),
                direction=_payload_direction(payload),
                entry_level=_payload_decimal(payload, "entry_level"),
                stop_level=_payload_decimal(payload, "stop_level"),
                target_levels=tuple(
                    _decimal_text(value, "target_levels[]")
                    for value in _payload_list(payload, "target_levels")
                ),
                risk_per_unit=_payload_decimal(payload, "risk_per_unit"),
                window_start=_payload_datetime(payload, "window_start"),
                observed_through=_payload_datetime(payload, "observed_through"),
                evaluated_through=_payload_optional_datetime(
                    payload, "evaluated_through"
                ),
                status=OutcomeStatus(_payload_text(payload, "status")),
                entry_reached=_payload_bool(payload, "entry_reached"),
                entry_ordered=_payload_bool(payload, "entry_ordered"),
                entry_timestamp=_payload_optional_datetime(payload, "entry_timestamp"),
                entry_candle_index=_payload_optional_int(payload, "entry_candle_index"),
                stop_reached=_payload_bool(payload, "stop_reached"),
                stop_pre_entry=_payload_bool(payload, "stop_pre_entry"),
                stop_timestamp=_payload_optional_datetime(payload, "stop_timestamp"),
                targets_reached=tuple(
                    _int_text(value, "targets_reached[]")
                    for value in _payload_list(payload, "targets_reached")
                ),
                targets_pre_entry=tuple(
                    _int_text(value, "targets_pre_entry[]")
                    for value in _payload_list(payload, "targets_pre_entry")
                ),
                first_touch_order=tuple(
                    tuple(str(token) for token in group)
                    for group in _payload_list(payload, "first_touch_order")
                ),
                events=events,
                ambiguous=_payload_bool(payload, "ambiguous"),
                ambiguity_kind=_payload_optional_text(payload, "ambiguity_kind"),
                ambiguity_timestamp=_payload_optional_datetime(
                    payload, "ambiguity_timestamp"
                ),
                incomplete=_payload_bool(payload, "incomplete"),
                missing_candle_count=_payload_int(payload, "missing_candle_count"),
                missing_ranges=gaps,
                expected_candle_count=_payload_int(payload, "expected_candle_count"),
                observed_candle_count=_payload_int(payload, "observed_candle_count"),
                evaluated_low=_payload_optional_decimal(payload, "evaluated_low"),
                evaluated_low_timestamp=_payload_optional_datetime(
                    payload, "evaluated_low_timestamp"
                ),
                evaluated_high=_payload_optional_decimal(payload, "evaluated_high"),
                evaluated_high_timestamp=_payload_optional_datetime(
                    payload, "evaluated_high_timestamp"
                ),
                post_entry_low=_payload_optional_decimal(payload, "post_entry_low"),
                post_entry_high=_payload_optional_decimal(payload, "post_entry_high"),
                mfe_price_move=_payload_optional_decimal(payload, "mfe_price_move"),
                mae_price_move=_payload_optional_decimal(payload, "mae_price_move"),
                mfe_r=_payload_optional_decimal(payload, "mfe_r"),
                mae_r=_payload_optional_decimal(payload, "mae_r"),
                resolution_timeframe=_backcompat_optional_text(
                    payload, "resolution_timeframe"
                ),
                resolution_attempted=_backcompat_optional_bool(
                    payload, "resolution_attempted", default=False
                ),
                resolution_used=_backcompat_optional_bool(
                    payload, "resolution_used", default=False
                ),
                resolution_reason=_backcompat_optional_text(
                    payload, "resolution_reason"
                ),
                resolution_expected_candles=_backcompat_optional_int(
                    payload, "resolution_expected_candles", default=0
                ),
                resolution_present_candles=_backcompat_optional_int(
                    payload, "resolution_present_candles", default=0
                ),
                resolution_missing_candles=_backcompat_optional_int(
                    payload, "resolution_missing_candles", default=0
                ),
                resolution_missing_ranges=_backcompat_gaps(payload),
                config_fingerprint=_payload_text(payload, "config_fingerprint"),
                observation_rules_version=_payload_text(
                    payload, "observation_rules_version"
                ),
            )
        except (TypeError, ValueError, KeyError) as exc:
            raise JournalError(f"stored outcome payload is malformed: {exc}") from exc

    def to_json_dict(self) -> dict[str, Any]:
        """Canonical, lossless projection; decoding it reproduces this object.

        journal-outcome-v1 observations keep exactly the fields they have
        always stored, so a historical payload is byte-identical to the row
        that was written and is never silently extended in place. The
        resolution fields exist only under journal-outcome-v2; decoding
        tolerates their absence on older payloads.
        """

        payload = to_jsonable(self)
        from trading_assistant.journaling.parameters import OUTCOME_RULES_VERSION

        if self.observation_rules_version == OUTCOME_RULES_VERSION:
            for key in (
                "resolution_timeframe",
                "resolution_attempted",
                "resolution_used",
                "resolution_reason",
                "resolution_expected_candles",
                "resolution_present_candles",
                "resolution_missing_candles",
                "resolution_missing_ranges",
            ):
                payload.pop(key, None)
        return payload


def record_identity_material(
    *,
    record_kind: RecordKind,
    setup_snapshot_id: str,
    setup_id: str | None,
    plan_id: str | None,
) -> dict[str, Any]:
    """Identity material for one journal record, documented in one place.

    A record is identified by the journal contract version, the exact Step 5
    snapshot content, the specific setup (or none for a whole-snapshot record),
    and the Step 6 plan id when a plan is attached. Re-journaling the same source
    content therefore reproduces the same id and cannot create a duplicate row.
    """

    return {
        "record_kind": record_kind,
        "setup_snapshot_id": setup_snapshot_id,
        "setup_id": setup_id,
        "plan_id": plan_id,
        "journal_rules_version": _journal_version(),
    }


def _journal_version() -> str:
    from trading_assistant.journaling.parameters import JOURNAL_RULES_VERSION

    return JOURNAL_RULES_VERSION


def _parse_payload(text: str, *, what: str) -> dict[str, Any]:
    import json

    try:
        payload = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise JournalError(f"stored {what} payload is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise JournalError(f"stored {what} payload is not a JSON object")
    return payload


def _require_price(value: object, *, name: str) -> Decimal:
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be a Decimal")
    if not value.is_finite():
        raise ValueError(f"{name} must be finite")
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _decimal_text(value: object, field_name: str) -> Decimal:
    if not isinstance(value, str):
        raise TypeError(f"stored {field_name} must be a decimal string")
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"stored {field_name} is not a valid decimal") from exc
    if not parsed.is_finite():
        raise ValueError(f"stored {field_name} must be finite")
    return parsed


def _int_text(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"stored {field_name} must be an integer")
    return value


def _parse_datetime(value: object, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise TypeError(f"stored {field_name} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"stored {field_name} is not a valid datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"stored {field_name} must be timezone-aware")
    return parsed.astimezone(UTC)


def _payload_field(payload: dict[str, Any], key: str) -> Any:
    if key not in payload:
        raise ValueError(f"stored payload is missing {key!r}")
    return payload[key]


def _payload_text(payload: dict[str, Any], key: str) -> str:
    value = _payload_field(payload, key)
    if not isinstance(value, str):
        raise TypeError(f"stored {key!r} must be a string")
    return value


def _payload_optional_text(payload: dict[str, Any], key: str) -> str | None:
    value = _payload_field(payload, key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"stored {key!r} must be a string or null")
    return value


def _payload_decimal(
    payload: dict[str, Any], key: str, *, nested: str | None = None
) -> Decimal:
    value = _payload_field(payload, key)
    if nested is not None:
        if not isinstance(value, dict):
            raise TypeError(f"stored {key!r} must be an object")
        value = _payload_field(value, nested)
    return _decimal_text(value, key)


def _payload_optional_decimal(payload: dict[str, Any], key: str) -> Decimal | None:
    value = _payload_field(payload, key)
    if value is None:
        return None
    return _decimal_text(value, key)


def _payload_datetime(payload: dict[str, Any], key: str) -> datetime:
    return _parse_datetime(_payload_field(payload, key), key)


def _payload_optional_datetime(payload: dict[str, Any], key: str) -> datetime | None:
    value = _payload_field(payload, key)
    if value is None:
        return None
    return _parse_datetime(value, key)


def _payload_int(payload: dict[str, Any], key: str) -> int:
    value = _payload_field(payload, key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"stored {key!r} must be an integer")
    return value


def _payload_optional_int(payload: dict[str, Any], key: str) -> int | None:
    value = _payload_field(payload, key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"stored {key!r} must be an integer or null")
    return value


def _payload_bool(payload: dict[str, Any], key: str) -> bool:
    value = _payload_field(payload, key)
    if not isinstance(value, bool):
        raise TypeError(f"stored {key!r} must be a boolean")
    return value


def _payload_list(payload: dict[str, Any], key: str) -> list[Any]:
    value = _payload_field(payload, key)
    if not isinstance(value, list):
        raise TypeError(f"stored {key!r} must be a list")
    return value


def _backcompat_optional_text(payload: dict[str, Any], key: str) -> str | None:
    """Decode an optional field added by a later outcome rules version.

    Stored observations written by earlier rules versions do not carry the
    field at all; it decodes to ``None`` instead of failing, so historical
    payloads stay readable without ever being rewritten.
    """

    if key not in payload:
        return None
    return _payload_optional_text(payload, key)


def _backcompat_optional_bool(
    payload: dict[str, Any], key: str, *, default: bool
) -> bool:
    if key not in payload:
        return default
    return _payload_bool(payload, key)


def _backcompat_optional_int(
    payload: dict[str, Any], key: str, *, default: int
) -> int:
    if key not in payload:
        return default
    return _payload_int(payload, key)


def _backcompat_gaps(payload: dict[str, Any]) -> tuple[CandleGap, ...]:
    """Decode ``resolution_missing_ranges`` tolerating pre-resolution payloads."""

    if "resolution_missing_ranges" not in payload:
        return ()
    return tuple(
        CandleGap(
            start=_payload_datetime(gap, "start"),
            end=_payload_datetime(gap, "end"),
            missing_count=_payload_int(gap, "missing_count"),
        )
        for gap in _payload_list(payload, "resolution_missing_ranges")
    )


def _payload_direction(payload: dict[str, Any]) -> Direction:
    value = _payload_text(payload, "direction")
    if value not in ("bullish", "bearish"):
        raise ValueError("stored direction must be 'bullish' or 'bearish'")
    return value  # type: ignore[return-value]


def journal_record_field_names() -> tuple[str, ...]:
    """Field order of ``JournalRecord``; used by storage round-trip tests."""

    return tuple(field.name for field in fields(JournalRecord))
