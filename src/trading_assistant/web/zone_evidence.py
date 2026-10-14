"""Read-only links between displayed support/resistance bands and stored events.

This module answers one factual question for the operator and for BRAIN's future
reasoning layer: *did a recorded breakout, failed breakout, sweep or retest use
this stored zone as its own reference level?*

Two separate facts, never conflated
-----------------------------------
A stored pattern-liquidity event carries a ``Reference``. When that reference is
a **zone** reference, ``pattern_liquidity.references`` builds it from the zone's
own ``band_low``/``band_high`` — the same stored numbers the display band is
built from. Zones are re-derived for every analysis frame, so a zone's bounds
*evolve* as more swings confirm: the band on screen today can be a wider version
of the level an older event was detected against. The two facts are therefore
reported separately:

``exact link`` (``linked``/``events``/``counts``)
    The event's reference is a ``zone`` reference whose stored bounds **equal**
    the band's own bounds — or, for a band merged from several zones, the bounds
    of one of its member zones. This is identity of the recorded band: no
    rounding, no tolerance, no "near enough" match. A swing, range-high or
    range-low reference is never re-labelled as a zone, even at the same price.

``contained reference`` (``contained_*``)
    The event's reference bounds lie **inside** the band's (or a member zone's)
    bounds without being identical: a smaller, earlier version of the same level,
    kept by every event as the bounds it was actually detected with. This is an
    observation, never identity — the payload says which band contained it and
    the event keeps its own recorded bounds and instant.

An event whose reference is outside every displayed band, or of a non-zone type,
is reported as neither. Both classes respect causality: an event is only
reported when it was already knowable at ``as_of`` (``known_at <= as_of``), so a
band is never shown as having been referenced by something the backend had not
recorded yet.

Nothing else is inferred. A band with no reported events is still a stored zone
built from confirmed swings — events are extra context, never a requirement for a
level to exist. A breakout that merely happens to sit near a band is reported as
no link *and* no contained reference, and the reason (``reference_type``) stays
visible in the payload.

What a report contains (all of it stored, none of it scored)
------------------------------------------------------------
For every reported event: its own id, kind, direction or state, the instant
BRAIN recorded it, the candle it happened on, its reference id, the close it was
detected with, the event's own recorded reference bounds, and which displayed
band bounds the match was made against. ``position_at_event`` is taken from the
event's own recorded close against its **own** recorded reference bounds, so a
zone that was resistance when the event happened and is support now shows both
facts side by side, with no claim that a "flip" occurred.

The result is a plain dict, safe to serialise, and it changes nothing: no
detector, threshold, stage, plan or journal record is touched.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

#: The linkage of a band that was searched and matched no stored event.
EMPTY_LINKAGE: dict[str, Any] = {
    "linked": False,
    "event_count": 0,
    "events": [],
    "counts": {"breakout": 0, "failed_breakout": 0, "sweep": 0, "retest": 0},
    "reference_ids": [],
    "matched_band_bounds": [],
    "contained_count": 0,
    "contained_events": [],
    "contained_counts": {"breakout": 0, "failed_breakout": 0, "sweep": 0, "retest": 0},
    "contained_reference_ids": [],
    "contained_band_bounds": [],
    "contained_note": None,
    "note": "no stored event used this zone as its own reference level",
}

#: The linkage of a band on a view where stored events were not available to check.
#: Reported as not evaluated, never as "nothing referenced it".
NOT_EVALUATED_LINKAGE: dict[str, Any] = {
    "linked": False,
    "event_count": None,
    "events": [],
    "counts": None,
    "reference_ids": [],
    "matched_band_bounds": [],
    "contained_count": None,
    "contained_events": [],
    "contained_counts": None,
    "contained_reference_ids": [],
    "contained_band_bounds": [],
    "contained_note": None,
    "note": "stored events were not available on this view, so no linkage was checked",
}

_EVENT_KINDS = ("breakout", "failed_breakout", "sweep", "retest")

#: The note a band carries when its exact zone was referenced by no stored event,
#: but smaller earlier references were recorded inside its price area. It states
#: both facts so the stricter identity rule can never read as "nothing happened
#: here" while a stored event's own band lies inside the displayed band.
CONTAINED_NOTE = (
    "no stored event used this exact zone as its own reference band, but stored "
    "events hold smaller recorded references inside this band's price area; their "
    "own bounds and instants are listed with each event"
)


def _decimal(value: Any) -> Decimal | None:
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        return None
    return parsed if parsed.is_finite() else None


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    return value if isinstance(value, str) else None


def _position_of(close: Decimal, low: Decimal, high: Decimal) -> str:
    if close < low:
        return "above_price"
    if close > high:
        return "below_price"
    return "price_inside"


def _event_close(event: dict[str, Any]) -> Decimal | None:
    """The close the event's own detector recorded, whichever field carries it."""

    for key in ("breakout_close", "reclaim_close", "close"):
        value = _decimal(event.get(key))
        if value is not None:
            return value
    return None


def _link_record(
    event: dict[str, Any],
    bounds: tuple[Decimal, Decimal],
    *,
    match: str,
    linked_via: tuple[str, str],
) -> dict[str, Any]:
    """One reported event with its own stored facts and how it was matched.

    ``bounds`` are the event's own recorded reference bounds (identical to the
    band's for an exact link, the smaller earlier band for a contained one), so
    ``position_at_event`` always reads against the level the event really used.
    ``linked_via`` is the displayed band or member-zone bounds the match was made
    against, which makes a containment decision checkable rather than asserted.
    """

    low, high = bounds
    close = _event_close(event)
    kind = str(event.get("evidence_kind") or event.get("kind") or "")
    record: dict[str, Any] = {
        "id": event.get("id"),
        "kind": kind,
        "match": match,
        "known_at": _iso(event.get("known_at")),
        "time": _iso(event.get("time")),
        "time_ms": event.get("time_ms"),
        "reference_id": event.get("reference_id"),
        "reference_type": event.get("reference_type"),
        "band_low": str(low),
        "band_high": str(high),
        "linked_via_bounds": {"band_low": linked_via[0], "band_high": linked_via[1]},
        "position_at_event": None if close is None else _position_of(close, low, high),
        "close_at_event": None if close is None else str(close),
    }
    if kind == "breakout":
        record["direction"] = event.get("direction")
        record["penetration_pct"] = event.get("penetration_pct")
        record["confirmation_candles"] = event.get("confirmation_candles")
    elif kind == "failed_breakout":
        record["direction"] = event.get("direction")
        record["breakout_id"] = event.get("breakout_id")
        record["elapsed_candles"] = event.get("elapsed_candles")
    elif kind == "sweep":
        record["direction"] = event.get("direction")
        record["extreme"] = event.get("extreme")
    elif kind == "retest":
        record["state"] = event.get("state")
        record["direction"] = event.get("direction")
        record["breakout_id"] = event.get("breakout_id")
        record["distance_from_band"] = event.get("distance_from_band")
        record["elapsed_candles"] = event.get("elapsed_candles")
    return record


def _contained_in(
    reference_low: Decimal, reference_high: Decimal, low: Decimal, high: Decimal
) -> bool:
    """Whether an event's own reference bounds lie inside a displayed band's bounds.

    Closed intervals on both sides: a reference that touches a band edge exactly
    (for example a single-price reference sitting on the band's low) is inside.
    The comparison is numeric on the parsed decimals, so equivalent decimal
    spellings never hide or invent a containment.
    """

    return reference_low >= low and reference_high <= high


def link_events_to_bands(
    bands: Sequence[dict[str, Any]],
    *,
    events: Iterable[dict[str, Any]] | None,
    as_of: datetime | None = None,
) -> dict[str, dict[str, Any]]:
    """Map each band id to the stored events whose reference is that band — or lies inside it.

    ``bands`` are presented bands (each carrying ``id``, ``band_low``,
    ``band_high`` and, when merged, ``member_zones``). ``events`` are stored
    event projections in the chart-evidence shape.

    Two independent facts are returned per band, never merged into one figure:

    * ``linked``/``event_count``/``events``/``counts`` — the event's own ``zone``
      reference has **exactly** the band's bounds, or the bounds of one of the
      band's member zones.
    * ``contained_count``/``contained_events``/``contained_counts`` — the event's
      own ``zone`` reference lies **inside** the band's (or a member zone's)
      bounds: a smaller, earlier version of the same level. Reported as an
      observation with its own recorded bounds, never as identity.
    """

    if not bands:
        return {}
    supplied = [event for event in (events or ()) if isinstance(event, dict)]
    result: dict[str, dict[str, Any]] = {str(band.get("id")): dict(EMPTY_LINKAGE) for band in bands}
    if not supplied:
        return result

    # Index the events by their exact stored band bounds. Only zone references
    # can link to a zone: every other reference type is kept out here. The same
    # filtered candidates are reused for the containment pass, so both classes
    # obey identical kind, type, bound and causality rules.
    index: dict[tuple[str, str], list[dict[str, Any]]] = {}
    candidates: list[tuple[dict[str, Any], Decimal, Decimal]] = []
    for event in supplied:
        if str(event.get("evidence_kind") or event.get("kind") or "") not in _EVENT_KINDS:
            continue
        if str(event.get("reference_type") or "") != "zone":
            continue
        known_at = _parse_dt(event.get("known_at"))
        if as_of is not None:
            if known_at is None or known_at > as_of:
                continue
        low = _decimal(event.get("band_low"))
        high = _decimal(event.get("band_high"))
        if low is None or high is None:
            continue
        index.setdefault((str(low), str(high)), []).append(event)
        candidates.append((event, low, high))

    if not index:
        return result

    for band in bands:
        band_id = str(band.get("id"))
        targets: list[tuple[str, str]] = []
        members = band.get("member_zones")
        if isinstance(members, (list, tuple)) and members:
            for member in members:
                if not isinstance(member, dict):
                    continue
                low = _decimal(member.get("band_low"))
                high = _decimal(member.get("band_high"))
                if low is not None and high is not None:
                    targets.append((str(low), str(high)))
        else:
            low = _decimal(band.get("band_low"))
            high = _decimal(band.get("band_high"))
            if low is not None and high is not None:
                targets.append((str(low), str(high)))

        matched: list[dict[str, Any]] = []
        matched_bounds: list[dict[str, str]] = []
        exact_events: list[dict[str, Any]] = []
        for bounds in targets:
            for event in index.get(bounds, ()):
                matched.append(
                    _link_record(
                        event,
                        (Decimal(bounds[0]), Decimal(bounds[1])),
                        match="exact",
                        linked_via=bounds,
                    )
                )
                exact_events.append(event)
                if {"band_low": bounds[0], "band_high": bounds[1]} not in matched_bounds:
                    matched_bounds.append({"band_low": bounds[0], "band_high": bounds[1]})

        # Containment pass: only events that were not already reported as exact
        # links, and only reported once even when several member zones contain
        # them (the first containing target, lowest-first, is the reported one).
        contained: list[dict[str, Any]] = []
        contained_bounds: list[dict[str, str]] = []
        for event, reference_low, reference_high in candidates:
            if any(event is already for already in exact_events):
                continue
            for bounds in targets:
                if not _contained_in(reference_low, reference_high, Decimal(bounds[0]), Decimal(bounds[1])):
                    continue
                contained.append(
                    _link_record(
                        event,
                        (reference_low, reference_high),
                        match="contained",
                        linked_via=bounds,
                    )
                )
                if {"band_low": bounds[0], "band_high": bounds[1]} not in contained_bounds:
                    contained_bounds.append({"band_low": bounds[0], "band_high": bounds[1]})
                break

        if not matched and not contained:
            continue

        matched.sort(key=lambda item: (str(item.get("known_at") or ""), str(item.get("id") or "")))
        contained.sort(key=lambda item: (str(item.get("known_at") or ""), str(item.get("id") or "")))
        counts = _counts_for(matched)
        contained_counts = _counts_for(contained)
        reference_ids = _reference_ids(matched)
        contained_reference_ids = _reference_ids(contained)
        result[band_id] = {
            "linked": bool(matched),
            "event_count": len(matched),
            "events": matched,
            "counts": counts,
            "reference_ids": reference_ids,
            "matched_band_bounds": matched_bounds,
            "first_known_at": matched[0].get("known_at") if matched else None,
            "last_known_at": matched[-1].get("known_at") if matched else None,
            "contained_count": len(contained),
            "contained_events": contained,
            "contained_counts": contained_counts,
            "contained_reference_ids": contained_reference_ids,
            "contained_band_bounds": contained_bounds,
            "contained_first_known_at": contained[0].get("known_at") if contained else None,
            "contained_last_known_at": contained[-1].get("known_at") if contained else None,
            "contained_note": CONTAINED_NOTE if contained and not matched else None,
            "note": (
                "these stored events used this zone's own band as their reference level"
                if matched
                else EMPTY_LINKAGE["note"]
            ),
        }
    return result


def _counts_for(records: Sequence[dict[str, Any]]) -> dict[str, int]:
    """Per-kind counts of one reported class (exact links, or contained references)."""

    counts = {kind: 0 for kind in _EVENT_KINDS}
    for item in records:
        kind = str(item.get("kind"))
        if kind in counts:
            counts[kind] += 1
    return counts


def _reference_ids(records: Sequence[dict[str, Any]]) -> list[str]:
    """Distinct stored reference ids of one reported class, in report order."""

    ids: list[str] = []
    for item in records:
        reference_id = item.get("reference_id")
        if reference_id and reference_id not in ids:
            ids.append(str(reference_id))
    return ids


__all__ = ["CONTAINED_NOTE", "EMPTY_LINKAGE", "NOT_EVALUATED_LINKAGE", "link_events_to_bands"]
