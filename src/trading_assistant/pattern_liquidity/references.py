"""Adapt existing Step 3 evidence, without recalculating structural levels."""

from trading_assistant.market_structure.analysis import TimeframeStructureAnalysis
from trading_assistant.pattern_liquidity.events import Reference, identity


def references(
    context: TimeframeStructureAnalysis, instrument: tuple[str, str, str]
) -> tuple[Reference, ...]:
    result = []
    for swing in context.confirmed_swings:
        kind = "swing_high" if swing.kind.value == "high" else "swing_low"
        result.append(
            Reference(
                identity(instrument, kind, swing),
                kind,
                swing.price,
                swing.price,
                swing.confirmed_at,
                (swing,),
            )
        )
    for zone in context.levels.zones:
        members = tuple(
            s
            for s in context.confirmed_swings
            if s.timestamp in zone.source_swing_timestamps
            and zone.band_low <= s.price <= zone.band_high
        )
        result.append(
            Reference(
                identity(instrument, "zone", members),
                "zone",
                zone.band_low,
                zone.band_high,
                context.as_of,
                members,
                zone=zone,
            )
        )
    active = context.active_range
    if active:
        members = tuple(
            s
            for s in context.confirmed_swings
            if (s.kind.value == "high" and s.timestamp in active.upper_touch_timestamps)
            or (s.kind.value == "low" and s.timestamp in active.lower_touch_timestamps)
        )
        for kind, price in (
            ("range_high", active.range_high),
            ("range_low", active.range_low),
        ):
            result.append(
                Reference(
                    identity(instrument, kind, price, members),
                    kind,
                    price,
                    price,
                    context.as_of,
                    members,
                    range=active,
                )
            )
    return tuple(result)
