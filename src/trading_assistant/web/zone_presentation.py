"""Display-only presentation of support/resistance zones.

The detector in ``market_structure.levels`` is authoritative and is NOT changed
here: its zones feed multi-timeframe context and pattern references, which feed
qualification. This module only decides how the already-computed zones are
*shown*:

* Position is taken from the zone's own band and the close the detector used
  (``latest_close``), never from the zone centre. A band that contains the close
  is ``price_inside``, not support or resistance. The detector's ``role`` field
  (centre versus close) is kept verbatim as ``raw_role`` and is explained when it
  disagrees with the display position.
* Bands that overlap each other are merged for display, so two 1H zones and a 4H
  zone at the same price read as one shaded area. Every contributing zone stays
  listed as a member with its own bounds and counts, the touch counts are shown
  as an explicit sum (``touch_count``), and the number of *separate instants*
  those touches happened on is reported beside it (``distinct_test_timestamps``).
  A single candle can be both a swing high and a swing low of its own window, so
  two swing evidences can share one instant; when they do, the difference is
  reported (``shared_candle_tests``) instead of being hidden inside a "touches"
  figure that would read as distinct tests.
* Only the nearest bands above and below price are shown by default, plus bands
  containing price. Everything else is counted as hidden, not deleted.
* Age is measured in candles of the viewed timeframe. Bands whose last test is
  older than ``FADE_AFTER_CANDLES`` are marked ``faded`` so they read as history.
* When the caller supplies stored events, a band also carries the read-only
  facts of the events whose own stored reference IS that zone (exact band
  identity, never numeric similarity). Nothing is inferred: a band is a level in
  its own right whether or not any event ever referenced it.

Malformed stored rows are never guessed at and never crash the view: a zone whose
bounds are not finite, inverted, or undated is skipped and counted in
``skipped_invalid``, and an unusable touch count falls back to the length of the
stored source-swing list (``touch_count_basis``) rather than becoming a zero.

Every field is derived from stored zone data. Nothing here is a strength score, a
probability, or a trade instruction.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from trading_assistant.market_data.timeframes import timeframe_to_milliseconds
from trading_assistant.web.zone_evidence import (
    EMPTY_LINKAGE as _EMPTY_LINKAGE,
)
from trading_assistant.web.zone_evidence import (
    NOT_EVALUATED_LINKAGE as _NOT_EVALUATED_LINKAGE,
)
from trading_assistant.web.zone_evidence import (
    link_events_to_bands,
)

POSITION_ABOVE = "above_price"
POSITION_BELOW = "below_price"
POSITION_INSIDE = "price_inside"

#: Nearest displayed bands on each side of price (bands containing price are always shown).
DISPLAY_PER_SIDE = 2
#: Upper bound on bands containing price, so a wide cluster cannot flood the chart.
DISPLAY_INSIDE_MAX = 3
#: A band whose last test is older than this many viewed-timeframe candles is faded.
FADE_AFTER_CANDLES = 100

#: Where a band's touch figure came from. Both are the detector's own stored numbers.
TOUCH_BASIS_STORED = "stored_touch_count"
TOUCH_BASIS_SOURCE_LIST = "source_swing_timestamps"
TOUCH_BASIS_SUMMED = "sum_of_member_zones"
TOUCH_BASIS_UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class _Band:
    """One display band: exactly the stored bounds of the zones that formed it."""

    low: Decimal
    high: Decimal
    touches: int | None
    touch_basis: str
    high_touches: int
    low_touches: int
    first_seen: datetime
    last_tested: datetime
    source_timestamps: tuple[str, ...]
    raw_roles: tuple[str, ...]
    members: tuple[dict[str, Any], ...] = field(default=())

    @property
    def member_count(self) -> int:
        return len(self.members)


def _parse_dec(value: Any) -> Decimal | None:
    """A finite Decimal, or None when the stored value is unusable.

    Non-finite values (``NaN``, ``Infinity``) are refused here rather than
    propagated: comparing them raises, and no real band can have one.
    """

    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        return None
    return parsed if parsed.is_finite() else None


def _parse_int(value: Any) -> int | None:
    """A non-negative integer, or None when the stored value is unusable."""

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(value) if value.is_integer() and value >= 0 else None
    if isinstance(value, str):
        text = value.strip()
        if not text or not text.lstrip("+-").isdigit():
            return None
        parsed = int(text)
        return parsed if parsed >= 0 else None
    return None


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _source_timestamps(zone: dict[str, Any]) -> tuple[str, ...]:
    raw = zone.get("source_swing_timestamps")
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(str(item) for item in raw)


def position_of(low: Decimal, high: Decimal, close: Decimal) -> str:
    """Where the close sits relative to a band. Boundaries count as inside."""

    if close < low:
        return POSITION_ABOVE
    if close > high:
        return POSITION_BELOW
    return POSITION_INSIDE


def _merge(bands: list[_Band]) -> list[_Band]:
    """Merge bands whose price ranges overlap (touching edges count as overlap).

    Merging is a display convenience only: the member zones stay listed inside
    the merged band, so the aggregate basis of every number stays inspectable.
    """

    merged: list[_Band] = []
    for band in sorted(bands, key=lambda b: (b.low, b.high)):
        if merged and band.low <= merged[-1].high:
            last = merged[-1]
            touches = (
                None
                if last.touches is None or band.touches is None
                else last.touches + band.touches
            )
            merged[-1] = _Band(
                low=min(last.low, band.low),
                high=max(last.high, band.high),
                touches=touches,
                touch_basis=(
                    last.touch_basis
                    if last.member_count == 0 and band.member_count == 0
                    else TOUCH_BASIS_SUMMED
                    if touches is not None
                    else TOUCH_BASIS_UNKNOWN
                ),
                high_touches=last.high_touches + band.high_touches,
                low_touches=last.low_touches + band.low_touches,
                first_seen=min(last.first_seen, band.first_seen),
                last_tested=max(last.last_tested, band.last_tested),
                source_timestamps=last.source_timestamps + band.source_timestamps,
                raw_roles=last.raw_roles + band.raw_roles,
                members=last.members + band.members,
            )
        else:
            merged.append(band)
    return merged


def _band_from_zone(zone: dict[str, Any], timeframe: str) -> _Band | None:
    """One stored zone as a display band, or None when its stored bounds are unusable."""

    low = _parse_dec(zone.get("band_low"))
    high = _parse_dec(zone.get("band_high"))
    last = _parse_dt(zone.get("last_tested_timestamp"))
    first = _parse_dt(zone.get("first_observed_timestamp"))
    if low is None or high is None or low > high or last is None or first is None:
        return None
    timestamps = _source_timestamps(zone)
    stored_touches = _parse_int(zone.get("touch_count"))
    if stored_touches is None:
        # The detector stores one source timestamp per counted touch, so the
        # list length is the same fact when the count itself is missing.
        touches = len(timestamps) if timestamps else None
        basis = TOUCH_BASIS_SOURCE_LIST if touches is not None else TOUCH_BASIS_UNKNOWN
    else:
        touches = stored_touches
        basis = TOUCH_BASIS_STORED
    high_touches = _parse_int(zone.get("high_source_count")) or 0
    low_touches = _parse_int(zone.get("low_source_count"))
    if low_touches is None:
        low_touches = max(0, (touches or 0) - high_touches) if touches is not None else 0
    return _Band(
        low=low,
        high=high,
        touches=touches,
        touch_basis=basis,
        high_touches=high_touches,
        low_touches=low_touches,
        first_seen=first,
        last_tested=last,
        source_timestamps=timestamps,
        raw_roles=(str(zone.get("role", "")),),
        members=(
            {
                "id": f"{timeframe}:{low}:{high}",
                "band_low": str(low),
                "band_high": str(high),
                "touch_count": touches,
                "touch_count_basis": basis,
                "last_tested": last.isoformat().replace("+00:00", "Z"),
                "role": str(zone.get("role", "")),
                "source_timeframe": timeframe,
            },
        ),
    )


def _cross_zone_shared_instants(members: Sequence[dict[str, Any]], zone_timestamps: dict[str, tuple[str, ...]]) -> int:
    """How many instants appear in more than one member zone's own source list."""

    seen: dict[str, int] = {}
    for member in members:
        for instant in set(zone_timestamps.get(str(member.get("id")), ())):
            seen[instant] = seen.get(instant, 0) + 1
    return sum(1 for count in seen.values() if count > 1)


def present_zones(
    zones: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None,
    *,
    timeframe: str,
    as_of: datetime | None,
    latest_close: Any,
    events: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the display bands for one timeframe's stored zones.

    Returns a dict with ``bands`` (what to draw, nearest first per side), counts
    that explain what was merged, hidden or skipped, and the rules applied. When
    the close or stored zones are unusable, returns no bands with an explicit
    ``reason``. ``events`` is optional; when given, each band carries the stored
    events whose own reference is exactly that band (see :mod:`zone_evidence`).
    """

    rules = {
        "position": "from band edges versus the detector's latest close",
        "merge": "overlapping bands merged for display; member zones stay listed",
        "touch_count": (
            "sum of the member zones' own stored counts (confirmed swing extremes); "
            "distinct_test_timestamps counts the separate instants they occurred on"
        ),
        "selection": f"nearest {DISPLAY_PER_SIDE} above and below, up to {DISPLAY_INSIDE_MAX} containing price",
        "fade_after_candles": FADE_AFTER_CANDLES,
    }
    close = _parse_dec(latest_close)
    if close is None or not zones:
        return {
            "timeframe": timeframe,
            "bands": [],
            "merged_count": 0,
            "hidden_count": 0,
            "presented_count": 0,
            "total_stored": len(zones or ()),
            "skipped_invalid": 0,
            "rules": rules,
            "reason": "no_zones" if not zones else "no_close",
        }

    interval_ms = timeframe_to_milliseconds(timeframe)
    bands: list[_Band] = []
    skipped = 0
    for zone in zones:
        if not isinstance(zone, dict):
            skipped += 1
            continue
        band = _band_from_zone(zone, timeframe)
        if band is None:
            skipped += 1
            continue
        bands.append(band)

    merged = _merge(bands)
    zone_timestamps = {
        str(member.get("id")): tuple(
            str(item) for item in band.source_timestamps
        )
        for band in merged
        for member in band.members
    }
    described: list[dict[str, Any]] = []
    for band in merged:
        position = position_of(band.low, band.high, close)
        # A band below the close is support; a band above the close is resistance.
        if position == POSITION_BELOW:
            distance = close - band.high
            display_role = "support"
        elif position == POSITION_ABOVE:
            distance = band.low - close
            display_role = "resistance"
        else:
            distance = Decimal(0)
            display_role = "price_inside"
        age_candles = None
        if as_of is not None:
            age_ms = int((as_of - band.last_tested).total_seconds() * 1000)
            age_candles = max(0, age_ms // interval_ms)
        distinct_instants = len(set(band.source_timestamps))
        shared_candle_tests = (
            max(0, band.touches - distinct_instants) if band.touches is not None else None
        )
        described.append(
            {
                "id": f"{timeframe}:{band.low}:{band.high}",
                "position": position,
                "display_role": display_role,
                "band_low": str(band.low),
                "band_high": str(band.high),
                "center": str((band.low + band.high) / 2),
                "band_width_pct": str(
                    ((band.high - band.low) / band.low * Decimal(100)).quantize(Decimal("0.01"))
                    if band.low
                    else Decimal(0)
                ),
                "touch_count": band.touches,
                "touch_count_basis": band.touch_basis,
                "swing_high_count": band.high_touches,
                "swing_low_count": band.low_touches,
                "source_swing_count": len(band.source_timestamps),
                "distinct_test_timestamps": distinct_instants,
                "shared_candle_tests": shared_candle_tests,
                "shared_timestamps_across_member_zones": _cross_zone_shared_instants(
                    band.members, zone_timestamps
                ),
                "isolated": band.touches == 1,
                "first_seen": band.first_seen.isoformat().replace("+00:00", "Z"),
                "last_tested": band.last_tested.isoformat().replace("+00:00", "Z"),
                "age_candles": age_candles,
                "faded": age_candles is not None and age_candles > FADE_AFTER_CANDLES,
                "source_timeframe": timeframe,
                "latest_close": str(close),
                "source_swing_timestamps": list(band.source_timestamps),
                "merged_zone_count": band.member_count,
                "member_zones": [dict(member) for member in band.members],
                "raw_roles": list(band.raw_roles),
                # Distance to the nearest edge of the band, against the close
                # this view is classified by (0 while price is inside).
                "distance_from_close": str(distance),
                "distance_pct_from_close": str(
                    (distance / close * Decimal(100)).quantize(Decimal("0.01"))
                    if close
                    else Decimal(0)
                ),
            }
        )

    inside = sorted(
        (b for b in described if b["position"] == POSITION_INSIDE),
        key=lambda b: (-(b["touch_count"] or 0), b["band_low"]),
    )
    above = sorted(
        (b for b in described if b["position"] == POSITION_ABOVE),
        key=lambda b: (Decimal(b["distance_from_close"]), -(b["touch_count"] or 0)),
    )
    below = sorted(
        (b for b in described if b["position"] == POSITION_BELOW),
        key=lambda b: (Decimal(b["distance_from_close"]), -(b["touch_count"] or 0)),
    )
    shown_inside = inside[:DISPLAY_INSIDE_MAX]
    shown = shown_inside + above[:DISPLAY_PER_SIDE] + below[:DISPLAY_PER_SIDE]
    # The linkage is only claimed when stored events were actually supplied to
    # check against; otherwise it says so, instead of reporting "nothing".
    linked = (
        link_events_to_bands(shown, events=events, as_of=as_of)
        if events is not None
        else {}
    )
    for item in shown:
        item["linked_events"] = linked.get(
            item["id"],
            dict(_NOT_EVALUATED_LINKAGE) if events is None else dict(_EMPTY_LINKAGE),
        )
    shown.sort(key=lambda b: (Decimal(b["band_low"]), Decimal(b["band_high"])), reverse=True)

    return {
        "timeframe": timeframe,
        "bands": shown,
        "merged_count": len(bands) - len(merged),
        "hidden_count": len(described) - len(shown),
        # What the backend presented, before any caller-side display cap.
        "presented_count": len(described),
        "hidden_above": max(0, len(above) - DISPLAY_PER_SIDE),
        "hidden_below": max(0, len(below) - DISPLAY_PER_SIDE),
        "hidden_inside": max(0, len(inside) - DISPLAY_INSIDE_MAX),
        "total_stored": len(zones),
        "skipped_invalid": skipped,
        "valid_zone_count": len(bands),
        "rules": rules,
        "reason": None,
    }


__all__ = [
    "DISPLAY_INSIDE_MAX",
    "DISPLAY_PER_SIDE",
    "FADE_AFTER_CANDLES",
    "POSITION_ABOVE",
    "POSITION_BELOW",
    "POSITION_INSIDE",
    "link_events_to_bands",
    "position_of",
    "present_zones",
]
