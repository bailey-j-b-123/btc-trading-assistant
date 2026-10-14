"""Read-only links between displayed support/resistance bands and stored events.

This module answers one factual question for the operator and for BRAIN's future
reasoning layer: *did a recorded breakout, failed breakout, sweep or retest use
this stored zone as its own reference level?*

How a link is established (and how it never is)
-----------------------------------------------
A stored pattern-liquidity event carries a ``Reference``. When that reference is
a **zone** reference, ``pattern_liquidity.references`` builds it from the zone's
own ``band_low``/``band_high`` — the same stored numbers the display band is
built from. A link therefore requires *exact band identity*:

* the event's ``reference_type`` must be ``"zone"`` (a swing, range-high or
  range-low reference is never re-labelled as a zone, even at the same price);
* the event's reference ``band_low`` and ``band_high`` must equal the member
  zone's stored bounds exactly, as decimals (no rounding, no tolerance, no
  "near enough" match);
* the event must already have been knowable at ``as_of`` (``known_at <= as_of``),
  so a band is never shown as having been referenced by something the backend had
  not recorded yet.

**This rule is deliberately narrow, and its consequence is stated here rather
than hidden** (BRAIN-AUDIT-0006): a zone's stored bounds are re-derived by the
detector on every analysis frame — the cluster evolves as further swings confirm —
while each historical event keeps the bounds it was detected with. A band that
was broken or retested weeks ago therefore usually has *different* bounds now, so
the exact-identity rule reports **no link** for it even though the same level is
plainly involved. Nothing here calls that a link: ``linked`` stays exact.

What the module does instead of an unsupported negative is report the overlap it
can actually measure. Every band also carries:

* ``overlapping`` / ``overlapping_count`` / ``overlapping_counts`` /
  ``overlapping_events`` — the stored zone-referenced events knowable at
  ``as_of`` whose reference bounds *overlap* the band (or one of its member
  zones) without being identical to it, each with its own relation
  (``contains``, ``contained_by`` or ``overlaps``) and the bounds the event
  really used;
* ``overlap_note`` — a plain statement of what that measurement is, so a caller
  cannot mistake it for a link.

Nothing else is inferred. A band with no linked events is still a stored zone
built from confirmed swings — events are extra context, never a requirement for a
level to exist. A breakout that merely happens to sit near a band is reported as
no link, and the reason (``reference_type``) stays visible in the payload.

What a link reports (all of it stored, none of it scored)
---------------------------------------------------------
For each linked event: its own id, kind, direction or state, the instant BRAIN
recorded it, the candle it happened on, its reference id, the close it was
detected with, and where price sat relative to the band at that instant
(``position_at_event``, taken from the event's own recorded close). That last
field is what makes a later role change checkable rather than asserted: a zone
that was resistance when the event happened and is support now shows both facts
side by side, with no claim that a "flip" occurred.

The result is a plain dict, safe to serialise, and it changes nothing: no
detector, threshold, stage, plan or journal record is touched.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

#: What a measured overlap means, in the payload itself. It never claims a link.
OVERLAP_NOTE = (
    "stored events whose zone reference overlaps this band but is not identical to it; the band's "
    "bounds are re-derived on each analysis frame while an event keeps the bounds it was detected "
    "with, so an overlap is reported and is NOT treated as the same level"
)

_EMPTY_OVERLAP: dict[str, Any] = {
    "overlapping": False,
    "overlapping_count": 0,
    "overlapping_events": [],
    "overlapping_counts": {"breakout": 0, "failed_breakout": 0, "sweep": 0, "retest": 0},
    "overlap_note": None,
}

#: The linkage of a band that was searched and matched no stored event.
EMPTY_LINKAGE: dict[str, Any] = {
    "linked": False,
    "event_count": 0,
    "events": [],
    "counts": {"breakout": 0, "failed_breakout": 0, "sweep": 0, "retest": 0},
    "reference_ids": [],
    "matched_band_bounds": [],
    "note": "no stored event used this zone as its own reference level",
    **_EMPTY_OVERLAP,
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
    "note": "stored events were not available on this view, so no linkage was checked",
    **_EMPTY_OVERLAP,
}

_EVENT_KINDS = ("breakout", "failed_breakout", "sweep", "retest")


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


def _link_record(event: dict[str, Any], bounds: tuple[Decimal, Decimal]) -> dict[str, Any]:
    low, high = bounds
    close = _event_close(event)
    kind = str(event.get("evidence_kind") or event.get("kind") or "")
    record: dict[str, Any] = {
        "id": event.get("id"),
        "kind": kind,
        "known_at": _iso(event.get("known_at")),
        "time": _iso(event.get("time")),
        "time_ms": event.get("time_ms"),
        "reference_id": event.get("reference_id"),
        "reference_type": event.get("reference_type"),
        "band_low": str(low),
        "band_high": str(high),
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


def _empty_entry() -> dict[str, Any]:
    """A fresh, fully-shaped linkage entry: no shared nested containers."""

    entry = dict(EMPTY_LINKAGE)
    entry["counts"] = dict(EMPTY_LINKAGE["counts"])
    entry["overlapping_counts"] = dict(_EMPTY_OVERLAP["overlapping_counts"])
    return entry


def _overlap_relation(
    low: Decimal, high: Decimal, targets: Sequence[tuple[str, str]]
) -> str | None:
    """How a reference's bounds relate to a band that is NOT bound-identical to it.

    ``None`` means no overlap at all, which is the only case in which a band may
    be reported as having no stored reference of any kind.
    """

    for target_low, target_high in targets:
        t_low, t_high = Decimal(target_low), Decimal(target_high)
        if low == t_low and high == t_high:
            continue
        if low < t_high and high > t_low:
            if low >= t_low and high <= t_high:
                return "contained_by"
            if low <= t_low and high >= t_high:
                return "contains"
            return "overlaps"
    return None


def link_events_to_bands(
    bands: Sequence[dict[str, Any]],
    *,
    events: Iterable[dict[str, Any]] | None,
    as_of: datetime | None = None,
) -> dict[str, dict[str, Any]]:
    """Map each band id to the stored events whose reference is exactly that band.

    ``bands`` are presented bands (each carrying ``id``, ``band_low``,
    ``band_high`` and, when merged, ``member_zones``). ``events`` are stored
    event projections in the chart-evidence shape. An event links to a band when
    its reference is a zone reference whose stored bounds equal the band's own
    bounds — or, for a band merged from several zones, the bounds of one of its
    member zones, which is where the event's reference actually came from.

    Each entry also reports the zone-referenced events that overlap the band
    without being identical to it (``overlapping``, ``overlapping_count``,
    ``overlapping_counts``, ``overlapping_events``, ``overlap_note``). They are
    explicitly NOT links: the band's bounds are re-derived per analysis frame
    while an event keeps the bounds it was detected with, so a historical
    reference to the same level usually differs at one edge. Reporting that
    overlap is what stops a caller from printing an unsupported absolute
    negative for such a band (BRAIN-AUDIT-0006).
    """

    if not bands:
        return {}
    supplied = [event for event in (events or ()) if isinstance(event, dict)]
    result: dict[str, dict[str, Any]] = {str(band.get("id")): _empty_entry() for band in bands}
    if not supplied:
        return result

    # Index the events by their exact stored band bounds. Only zone references
    # can link to a zone: every other reference type is kept out here.
    index: dict[tuple[str, str], list[dict[str, Any]]] = {}
    candidates: list[tuple[tuple[str, str], dict[str, Any]]] = []
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
        key = (str(low), str(high))
        index.setdefault(key, []).append(event)
        candidates.append((key, event))

    if not candidates:
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
        for bounds in targets:
            for event in index.get(bounds, ()):
                matched.append(_link_record(event, (Decimal(bounds[0]), Decimal(bounds[1]))))
                if {"band_low": bounds[0], "band_high": bounds[1]} not in matched_bounds:
                    matched_bounds.append({"band_low": bounds[0], "band_high": bounds[1]})
        # Overlapping-but-not-identical references: measured, reported, never called a link.
        overlapping: list[dict[str, Any]] = []
        for key, event in candidates:
            if key in targets:
                continue  # identical bounds are handled above as a link
            relation = _overlap_relation(Decimal(key[0]), Decimal(key[1]), targets)
            if relation is None:
                continue
            record = _link_record(event, (Decimal(key[0]), Decimal(key[1])))
            record["overlap_relation"] = relation
            overlapping.append(record)

        entry = result[band_id]
        if matched:
            matched.sort(key=lambda item: (str(item.get("known_at") or ""), str(item.get("id") or "")))
            counts = {kind: 0 for kind in _EVENT_KINDS}
            for item in matched:
                kind = str(item.get("kind"))
                if kind in counts:
                    counts[kind] += 1
            reference_ids: list[str] = []
            for item in matched:
                reference_id = item.get("reference_id")
                if reference_id and reference_id not in reference_ids:
                    reference_ids.append(str(reference_id))
            entry.update(
                {
                    "linked": True,
                    "event_count": len(matched),
                    "events": matched,
                    "counts": counts,
                    "reference_ids": reference_ids,
                    "matched_band_bounds": matched_bounds,
                    "first_known_at": matched[0].get("known_at"),
                    "last_known_at": matched[-1].get("known_at"),
                    "note": "these stored events used this zone's own band as their reference level",
                }
            )
        if overlapping:
            overlapping.sort(
                key=lambda item: (str(item.get("known_at") or ""), str(item.get("id") or ""))
            )
            overlap_counts = {kind: 0 for kind in _EVENT_KINDS}
            for item in overlapping:
                kind = str(item.get("kind"))
                if kind in overlap_counts:
                    overlap_counts[kind] += 1
            entry.update(
                {
                    "overlapping": True,
                    "overlapping_count": len(overlapping),
                    "overlapping_events": overlapping,
                    "overlapping_counts": overlap_counts,
                    "overlap_note": OVERLAP_NOTE,
                }
            )
    return result


__all__ = [
    "EMPTY_LINKAGE",
    "NOT_EVALUATED_LINKAGE",
    "OVERLAP_NOTE",
    "link_events_to_bands",
]
