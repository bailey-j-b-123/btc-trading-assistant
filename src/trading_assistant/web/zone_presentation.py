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
  zone at the same price read as one shaded area. Touch counts are summed and the
  merged members are listed.
* Only the nearest bands above and below price are shown by default, plus bands
  containing price. Everything else is counted as hidden, not deleted.
* Age is measured in candles of the viewed timeframe. Bands whose last test is
  older than ``FADE_AFTER_CANDLES`` are marked ``faded`` so they read as history.

Every field is derived from stored zone data. Nothing here is a strength score,
a probability, or a trade instruction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from trading_assistant.market_data.timeframes import timeframe_to_milliseconds

POSITION_ABOVE = "above_price"
POSITION_BELOW = "below_price"
POSITION_INSIDE = "price_inside"

#: Nearest displayed bands on each side of price (bands containing price are always shown).
DISPLAY_PER_SIDE = 2
#: Upper bound on bands containing price, so a wide cluster cannot flood the chart.
DISPLAY_INSIDE_MAX = 3
#: A band whose last test is older than this many viewed-timeframe candles is faded.
FADE_AFTER_CANDLES = 100


@dataclass(frozen=True, slots=True)
class _Band:
    low: Decimal
    high: Decimal
    touches: int
    high_touches: int
    low_touches: int
    first_seen: datetime
    last_tested: datetime
    source_timestamps: tuple[str, ...]
    raw_roles: tuple[str, ...]
    member_count: int


def _parse_dec(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001 - malformed stored value: skip, never guess
        return None


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def position_of(low: Decimal, high: Decimal, close: Decimal) -> str:
    """Where the close sits relative to a band. Boundaries count as inside."""

    if close < low:
        return POSITION_ABOVE
    if close > high:
        return POSITION_BELOW
    return POSITION_INSIDE


def _merge(bands: list[_Band]) -> list[_Band]:
    """Merge bands whose price ranges overlap (touching edges count as overlap)."""

    merged: list[_Band] = []
    for band in sorted(bands, key=lambda b: (b.low, b.high)):
        if merged and band.low <= merged[-1].high:
            last = merged[-1]
            merged[-1] = _Band(
                low=min(last.low, band.low),
                high=max(last.high, band.high),
                touches=last.touches + band.touches,
                high_touches=last.high_touches + band.high_touches,
                low_touches=last.low_touches + band.low_touches,
                first_seen=min(last.first_seen, band.first_seen),
                last_tested=max(last.last_tested, band.last_tested),
                source_timestamps=last.source_timestamps + band.source_timestamps,
                raw_roles=last.raw_roles + band.raw_roles,
                member_count=last.member_count + band.member_count,
            )
        else:
            merged.append(band)
    return merged


def present_zones(
    zones: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None,
    *,
    timeframe: str,
    as_of: datetime | None,
    latest_close: Any,
) -> dict[str, Any]:
    """Build the display bands for one timeframe's stored zones.

    Returns a dict with ``bands`` (what to draw, nearest first per side), counts
    that explain what was merged or hidden, and the rules applied. When the close
    or stored zones are unusable, returns no bands with an explicit ``reason``.
    """

    rules = {
        "position": "from band edges versus the detector's latest close",
        "merge": "overlapping bands merged for display",
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
            "total_stored": len(zones or ()),
            "rules": rules,
            "reason": "no_zones" if not zones else "no_close",
        }

    interval_ms = timeframe_to_milliseconds(timeframe)
    bands: list[_Band] = []
    skipped = 0
    for zone in zones:
        low = _parse_dec(zone.get("band_low"))
        high = _parse_dec(zone.get("band_high"))
        last = _parse_dt(zone.get("last_tested_timestamp"))
        first = _parse_dt(zone.get("first_observed_timestamp"))
        if low is None or high is None or low > high or last is None or first is None:
            skipped += 1
            continue
        bands.append(
            _Band(
                low=low,
                high=high,
                touches=int(zone.get("touch_count") or 0),
                high_touches=int(zone.get("high_source_count") or 0),
                low_touches=int(zone.get("low_source_count") or 0),
                first_seen=first,
                last_tested=last,
                source_timestamps=tuple(str(t) for t in zone.get("source_swing_timestamps") or ()),
                raw_roles=(str(zone.get("role", "")),),
                member_count=1,
            )
        )

    merged = _merge(bands)
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
                "distance": distance,
                "touch_count": band.touches,
                "swing_high_count": band.high_touches,
                "swing_low_count": band.low_touches,
                "isolated": band.touches <= 1,
                "first_seen": band.first_seen.isoformat().replace("+00:00", "Z"),
                "last_tested": band.last_tested.isoformat().replace("+00:00", "Z"),
                "age_candles": age_candles,
                "faded": age_candles is not None and age_candles > FADE_AFTER_CANDLES,
                "source_timeframe": timeframe,
                "latest_close": str(close),
                "source_swing_timestamps": list(band.source_timestamps),
                "merged_zone_count": band.member_count,
                "raw_roles": list(band.raw_roles),
            }
        )

    inside = sorted((b for b in described if b["position"] == POSITION_INSIDE), key=lambda b: -b["touch_count"])
    above = sorted((b for b in described if b["position"] == POSITION_ABOVE), key=lambda b: (b["distance"], -b["touch_count"]))
    below = sorted((b for b in described if b["position"] == POSITION_BELOW), key=lambda b: (b["distance"], -b["touch_count"]))
    shown_inside = inside[:DISPLAY_INSIDE_MAX]
    shown = shown_inside + above[:DISPLAY_PER_SIDE] + below[:DISPLAY_PER_SIDE]
    for item in shown:
        item.pop("distance", None)
    shown.sort(key=lambda b: (Decimal(b["band_low"]), Decimal(b["band_high"])), reverse=True)

    return {
        "timeframe": timeframe,
        "bands": shown,
        "merged_count": len(bands) - len(merged),
        "hidden_count": len(described) - len(shown),
        "total_stored": len(zones),
        "skipped_invalid": skipped,
        "rules": rules,
        "reason": None,
    }
