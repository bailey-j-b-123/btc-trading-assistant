"""Reference provenance: can the stored record account for every level a setup uses?

The Component #1 required-window gate (:mod:`trading_assistant.market_data.integrity`)
proves that the *trailing closes* a decision indexes are all present. That is a
necessary condition and it is not a sufficient one, because the levels those
candles are tested against are not produced by the trailing window alone. A
reference level (a confirmed swing, a clustered zone, an active range) is
carried in the analysis' own catalog for as long as it stays relevant, and the
evidence attached to it — earlier touches, breakouts, failures and sweeps of the
same level — accumulates from the first candle the analysis loaded. So a
decision can depend on evidence arbitrarily far back, while the record only
starts where ingestion started.

That asymmetry is the V1 defect recorded in
``docs/audits/BRAIN_INDEPENDENT_SYSTEM_AUDIT_PR42.md`` and measured in
``docs/decision-invariance.md``: appending older candles can change a
recomputed decision near the start of stored history even when the required
window was fully stored. This module makes the *provable part* of that statement
explicit at the instant of the decision instead of leaving it implicit:

* for every live setup, collect the open times of the evidence its reference
  actually rests on — the swings that formed it, the zone's or range's own
  formation and touch record, and every catalog event recorded against the same
  reference id;
* a reference is *accountable* at a boundary only when that oldest evidence
  sits far enough inside stored history for its own formation to have been
  observed: a swing needs its left window, and a level that was already being
  tested by the very first stored candle was formed before the record began.

``TRUNCATED`` therefore means "this level's provenance starts at the edge of the
stored record, so older candles could have formed or placed it differently", and
a plan proposed on such a level is withheld: it is not evidence of anything
except warm-up. Nothing here estimates the missing history, invents a level, or
changes any breakout, retest, target or stop rule: no parameter is retuned and
no ledger row is rewritten.

Two limits are stated deliberately, because overstating this gate would be worse
than recording it:

* Withholding protects a decision from depending on a level the record cannot
  account for. It cannot recover a decision that a *shorter* record never saw at
  all: a boundary with a truncated record can still under-report opportunities,
  and this module reports that separately as :attr:`inventory_at_history_start`
  rather than pretending the boundary is settled.
* An empty or unusable window yields ``UNAVAILABLE``, never a silent
  ``COMPLETE``: absence of evidence about provenance is not evidence of
  provenance.

All timestamps are UTC candle opens aligned to the timeframe; only closed
candles are ever considered, exactly as in the analysis this module describes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Iterable

from trading_assistant.market_data.timeframes import (
    require_utc_datetime,
    timeframe_to_milliseconds,
)
from trading_assistant.setup_qualification.models import (
    QualificationFrame,
    QualificationSnapshot,
    SetupResult,
    SetupState,
)

#: Version of the reference-provenance contract, recorded with every forward
#: decision so cycles evaluated before and after this rule are never merged into
#: one cohort silently.
REFERENCE_PROVENANCE_RULES_VERSION = "reference-provenance-v1"

#: Setup states whose plans can be recorded, and therefore the only states this
#: gate has anything to say about.
_LIVE_STATES = (SetupState.WATCH, SetupState.QUALIFIED)


class ProvenanceStatus(StrEnum):
    """Whether stored history can account for the evidence a decision used.

    ``COMPLETE``
        Every live setup's reference provenance lies strictly inside stored
        history, and the record held enough structure for the level clustering
        the decision used: no plan is withheld for provenance reasons.
    ``TRUNCATED``
        At least one consumed reference's oldest evidence sits at the start of
        the stored record, so older candles could have formed or placed that
        level differently. Plans on the affected setups are withheld.
    ``UNAVAILABLE``
        No frame, no snapshot, or no stored candle: provenance cannot be judged,
        which is reported as such and never read as ``COMPLETE``.
    """

    COMPLETE = "COMPLETE"
    TRUNCATED = "TRUNCATED"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class SetupProvenance:
    """The provenance verdict for one live setup's own reference evidence."""

    setup_id: str
    setup_state: SetupState
    reference_id: str | None
    reference_type: str | None
    status: ProvenanceStatus
    oldest_evidence_open: datetime | None
    earliest_accountable_open: datetime | None
    reasons: tuple[str, ...]

    @property
    def blocked(self) -> bool:
        return self.status is not ProvenanceStatus.COMPLETE

    def to_json_dict(self) -> dict[str, object]:
        return {
            "setup_id": self.setup_id,
            "setup_state": self.setup_state.value,
            "reference_id": self.reference_id,
            "reference_type": self.reference_type,
            "status": self.status.value,
            "oldest_evidence_open": _iso(self.oldest_evidence_open),
            "earliest_accountable_open": _iso(self.earliest_accountable_open),
            "reasons": list(self.reasons),
            "blocked": self.blocked,
        }


@dataclass(frozen=True, slots=True)
class ReferenceProvenanceAssessment:
    """Boundary-local provenance verdict for one instrument/timeframe close."""

    exchange: str
    symbol: str
    timeframe: str
    decision_time: datetime
    status: ProvenanceStatus
    first_stored_open: datetime | None
    loaded_window_start: datetime | None
    swing_left_window: int
    swing_count: int
    level_lookback_swings: int
    setups: tuple[SetupProvenance, ...]
    advisory_reasons: tuple[str, ...]
    inventory_at_history_start: bool
    summary: str

    @property
    def complete(self) -> bool:
        return self.status is ProvenanceStatus.COMPLETE

    @property
    def blocked_setup_ids(self) -> frozenset[str]:
        """Live setups whose Step 6 plan must not be recorded at this close.

        A verdict the gate could not reach (``UNAVAILABLE``) withholds planning
        exactly as a provenance failure does: an unverifiable level is not a
        verified one, and confidence is never inferred from missing evidence.
        """

        return frozenset(
            entry.setup_id for entry in self.setups if entry.blocked
        )

    def entry_for(self, setup_id: str) -> SetupProvenance | None:
        for entry in self.setups:
            if entry.setup_id == setup_id:
                return entry
        return None

    def blocks(self, setup: SetupResult) -> bool:
        """Whether this setup's plan must be withheld for provenance reasons."""

        entry = self.entry_for(setup.id)
        return entry is not None and entry.blocked

    def reason_for(self, setup: SetupResult) -> str:
        """The recorded explanation for one withheld plan (never generic)."""

        entry = self.entry_for(setup.id)
        if entry is None:
            return self.summary
        return "; ".join(entry.reasons) if entry.reasons else self.summary

    def to_json_dict(self) -> dict[str, object]:
        return {
            "rules_version": REFERENCE_PROVENANCE_RULES_VERSION,
            "exchange": self.exchange,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "decision_time": _iso(self.decision_time),
            "status": self.status.value,
            "first_stored_open": _iso(self.first_stored_open),
            "loaded_window_start": _iso(self.loaded_window_start),
            "swing_left_window": self.swing_left_window,
            "swing_count": self.swing_count,
            "level_lookback_swings": self.level_lookback_swings,
            "inventory_at_history_start": self.inventory_at_history_start,
            "blocked_setup_ids": sorted(self.blocked_setup_ids),
            "setups": [entry.to_json_dict() for entry in self.setups],
            "advisory_reasons": list(self.advisory_reasons),
            "summary": self.summary,
        }


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat().replace("+00:00", "Z")


def _unavailable(
    *,
    exchange: str,
    symbol: str,
    timeframe: str,
    decision_time: datetime,
    reason: str,
    first_stored_open: datetime | None = None,
) -> ReferenceProvenanceAssessment:
    return ReferenceProvenanceAssessment(
        exchange=exchange,
        symbol=symbol,
        timeframe=timeframe,
        decision_time=require_utc_datetime(decision_time, field_name="decision_time"),
        status=ProvenanceStatus.UNAVAILABLE,
        first_stored_open=first_stored_open,
        loaded_window_start=None,
        swing_left_window=0,
        swing_count=0,
        level_lookback_swings=0,
        setups=(),
        advisory_reasons=(reason,),
        inventory_at_history_start=False,
        summary=reason,
    )


def _reference_of(event: Any) -> Any | None:
    """The structural level one seed event was detected against, if any.

    Read through the documented attributes of the Step 4 event union rather than
    by type: a breakout or sweep carries ``reference``, a failed breakout or a
    retest carries the ``breakout`` it belongs to. Anything else (a chart
    pattern, an equal-level cluster) is not a level-seeding event and is
    reported as having no reference, never as a reference of unknown origin.
    """

    if event is None:
        return None
    reference = getattr(event, "reference", None)
    if reference is None:
        breakout = getattr(event, "breakout", None)
        reference = None if breakout is None else getattr(breakout, "reference", None)
    if (
        reference is None
        or getattr(reference, "id", None) is None
        or not hasattr(reference, "swings")
    ):
        return None
    return reference


def _reference_evidence(reference: Any) -> list[datetime]:
    """Open times the level itself was built from (never an estimate)."""

    opens = [swing.timestamp for swing in reference.swings]
    zone = getattr(reference, "zone", None)
    if zone is not None:
        opens.append(zone.first_observed_timestamp)
        opens.extend(zone.source_swing_timestamps)
    detected_range = getattr(reference, "range", None)
    if detected_range is not None:
        opens.append(detected_range.start_timestamp)
        opens.extend(detected_range.upper_touch_timestamps)
        opens.extend(detected_range.lower_touch_timestamps)
    return opens


def _catalog_index(
    events: Iterable[Any],
) -> tuple[dict[str, Any], dict[str, datetime]]:
    """Index the loaded evidence catalog once: by event id and by reference id.

    The per-reference map holds the *oldest* candle recorded against each
    reference. A level's touch/breakout/failure record accumulates from the
    first candle the analysis loaded, so that oldest event is the earliest
    moment the record can show the level mattered. An event on the very first
    stored candle therefore proves the level was already active when the record
    began — exactly the provenance this gate cannot vouch for.
    """

    by_id: dict[str, Any] = {}
    earliest: dict[str, datetime] = {}
    for event in events:
        event_id = getattr(event, "id", None)
        if event_id is not None:
            by_id[event_id] = event
        reference = _reference_of(event)
        if reference is None:
            continue
        candle = getattr(event, "candle", None)
        open_time = None if candle is None else candle.timestamp
        if open_time is None:
            continue
        current = earliest.get(reference.id)
        if current is None or open_time < current:
            earliest[reference.id] = open_time
    return by_id, earliest


def assess_reference_provenance(
    *,
    exchange: str,
    symbol: str,
    timeframe: str,
    decision_time: datetime,
    frame: QualificationFrame | None,
    snapshot: QualificationSnapshot | None,
    first_stored_open: datetime | None,
) -> ReferenceProvenanceAssessment:
    """Judge whether stored history accounts for each live setup's levels.

    Read-only over the caller's own objects: the frame's Step 4 evidence catalog
    and structural parameters, the Step 5 snapshot's live setups, and the oldest
    stored candle open for this exact instrument and timeframe. Nothing is
    re-analysed, re-queried, or retuned, so the verdict can never disagree with
    the decision it describes.
    """

    decision = require_utc_datetime(decision_time, field_name="decision_time")
    if frame is None or snapshot is None:
        return _unavailable(
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            decision_time=decision,
            reason=(
                "no Step 3/4 frame or Step 5 snapshot exists at this close, so "
                "reference provenance cannot be judged"
            ),
            first_stored_open=first_stored_open,
        )
    if first_stored_open is None:
        return _unavailable(
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            decision_time=decision,
            reason=(
                "no stored candle exists for this instrument/timeframe, so the age "
                "of the reference evidence cannot be judged"
            ),
        )
    first_stored = require_utc_datetime(first_stored_open, field_name="first_stored_open")

    structure = frame.patterns.structure
    structure_parameters = structure.parameters
    swing_parameters = structure_parameters.swings
    interval = timedelta(milliseconds=timeframe_to_milliseconds(timeframe))
    # A swing is only confirmed once its left window has closed, so that lead-in
    # is the minimum history a swing-derived level needs to be accountable.
    swing_lead_in = swing_parameters.left_window * interval
    levels_lookback = structure_parameters.levels.lookback_swings
    equal_lookback = frame.patterns.parameters.equal_lookback_swings
    required_lookback = max(levels_lookback, equal_lookback)

    events = tuple(frame.patterns.events())
    events_by_id, events_by_reference = _catalog_index(events)
    swings = structure.confirmed_swings
    swing_opens = tuple(swing.timestamp for swing in swings)
    inventory_at_start = bool(
        swing_opens and swing_opens[0] <= first_stored + swing_lead_in
    )

    entries: list[SetupProvenance] = []
    for setup in snapshot.setups:
        if setup.state not in _LIVE_STATES:
            continue
        seed = events_by_id.get(setup.seed_event_id)
        reference = _reference_of(seed)
        if reference is None:
            entries.append(
                SetupProvenance(
                    setup_id=setup.id,
                    setup_state=setup.state,
                    reference_id=setup.reference_id,
                    reference_type=None,
                    status=ProvenanceStatus.UNAVAILABLE,
                    oldest_evidence_open=None,
                    earliest_accountable_open=None,
                    reasons=(
                        "the seed event for this setup is not in the loaded evidence "
                        "catalog, so its reference provenance cannot be verified",
                    ),
                )
            )
            continue
        opens = _reference_evidence(reference)
        catalog_open = events_by_reference.get(reference.id)
        if catalog_open is not None:
            opens.append(catalog_open)
        if not opens:
            entries.append(
                SetupProvenance(
                    setup_id=setup.id,
                    setup_state=setup.state,
                    reference_id=reference.id,
                    reference_type=reference.type,
                    status=ProvenanceStatus.UNAVAILABLE,
                    oldest_evidence_open=None,
                    earliest_accountable_open=None,
                    reasons=(
                        "the reference carries no stored candle evidence, so its "
                        "provenance cannot be verified",
                    ),
                )
            )
            continue
        oldest = min(opens)
        # A swing extreme needs its left window stored before it, because an
        # older candle could hold the extreme instead. A level that was already
        # being tested — or already observed as a zone member — by the very
        # first stored candle needs no lead-in at all: that is itself the proof
        # the record started mid-formation.
        lead_in_candles = (
            swing_parameters.left_window
            if reference.type in ("swing_high", "swing_low")
            else 1
        )
        accountable_from = first_stored + lead_in_candles * interval
        if oldest < accountable_from:
            entries.append(
                SetupProvenance(
                    setup_id=setup.id,
                    setup_state=setup.state,
                    reference_id=reference.id,
                    reference_type=reference.type,
                    status=ProvenanceStatus.TRUNCATED,
                    oldest_evidence_open=oldest,
                    earliest_accountable_open=accountable_from,
                    reasons=(
                        f"the {timeframe} {reference.type.replace('_', ' ')} level this setup "
                        f"depends on is first evidenced at {oldest.isoformat()} while stored "
                        f"history for this instrument/timeframe starts at "
                        f"{first_stored.isoformat()}: the level was already forming (or was "
                        "already being tested) when the record began, so older candles could "
                        "have placed or formed it differently and its provenance cannot be "
                        "verified",
                    ),
                )
            )
        else:
            entries.append(
                SetupProvenance(
                    setup_id=setup.id,
                    setup_state=setup.state,
                    reference_id=reference.id,
                    reference_type=reference.type,
                    status=ProvenanceStatus.COMPLETE,
                    oldest_evidence_open=oldest,
                    earliest_accountable_open=accountable_from,
                    reasons=(),
                )
            )

    blocked = [entry for entry in entries if entry.status is ProvenanceStatus.TRUNCATED]
    unverifiable = [entry for entry in entries if entry.status is ProvenanceStatus.UNAVAILABLE]
    advisory: list[str] = []
    if len(swings) < required_lookback:
        advisory.append(
            f"the level clustering at this close had {len(swings)} confirmed swing(s) "
            f"available for a {required_lookback}-swing lookback, so the zone set itself is "
            "bounded by the start of stored history (reported, never used to withhold: "
            "trailing clustering is unaffected by older candles once enough swings are stored)"
        )
    if inventory_at_start:
        advisory.append(
            "the earliest confirmed swing sits at the start of stored history, so this "
            "boundary's level inventory cannot be shown to include every level that existed: "
            "setups that depended on older levels were never visible here. Opportunities "
            "missed for that reason are a limit of the record, not a settled no-trade verdict"
        )

    if blocked:
        status = ProvenanceStatus.TRUNCATED
        summary = (
            f"{len(blocked)} of {len(entries)} live setup(s) at this close depend on a "
            "reference whose provenance starts at the edge of stored history; plans for "
            "them are withheld as warm-up, not recorded as a market conclusion"
        )
    elif unverifiable:
        status = ProvenanceStatus.TRUNCATED
        summary = (
            f"{len(unverifiable)} of {len(entries)} live setup(s) could not have their "
            "reference provenance verified at this close; their plans are withheld rather "
            "than recorded on unverified evidence"
        )
    else:
        status = ProvenanceStatus.COMPLETE
        summary = (
            "every live setup at this close rests on reference evidence that stored history "
            "accounts for; no plan is withheld for provenance reasons"
        )
    return ReferenceProvenanceAssessment(
        exchange=exchange,
        symbol=symbol,
        timeframe=timeframe,
        decision_time=decision,
        status=status,
        first_stored_open=first_stored,
        loaded_window_start=structure.window_start_timestamp,
        swing_left_window=swing_parameters.left_window,
        swing_count=len(swings),
        level_lookback_swings=required_lookback,
        setups=tuple(entries),
        advisory_reasons=tuple(advisory),
        inventory_at_history_start=inventory_at_start,
        summary=summary,
    )


__all__ = [
    "REFERENCE_PROVENANCE_RULES_VERSION",
    "ProvenanceStatus",
    "ReferenceProvenanceAssessment",
    "SetupProvenance",
    "assess_reference_provenance",
]
