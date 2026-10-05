"""Same-kind equal levels reuse Step 3's anchored, non-transitive zone clustering."""

from trading_assistant.market_structure.analysis import TimeframeStructureAnalysis
from trading_assistant.market_structure.levels import LevelParameters, detect_zones
from trading_assistant.market_structure.swings import SwingKind
from trading_assistant.pattern_liquidity.events import EqualLevelCluster, identity
from trading_assistant.pattern_liquidity.parameters import PatternLiquidityParameters


def detect_equal_levels(
    context: TimeframeStructureAnalysis,
    instrument: tuple[str, str, str],
    parameters: PatternLiquidityParameters,
) -> tuple[EqualLevelCluster, ...]:
    results = []
    for kind in SwingKind:
        swings = tuple(s for s in context.confirmed_swings if s.kind == kind)
        zones = detect_zones(
            swings,
            as_of=context.as_of,
            parameters=LevelParameters(
                lookback_swings=parameters.equal_lookback_swings,
                min_touches=parameters.equal_min_members,
                max_zones=parameters.equal_lookback_swings,
                tolerance_pct=parameters.equal_tolerance_pct,
            ),
        ).zones
        for zone in zones:
            members = tuple(
                s for s in swings if s.timestamp in zone.source_swing_timestamps
            )
            type_ = "equal_high" if kind == SwingKind.HIGH else "equal_low"
            known = sorted(s.confirmed_at for s in members)
            results.append(
                EqualLevelCluster(
                    identity(instrument, type_, members),
                    type_,
                    members,
                    zone.band_low,
                    zone.band_high,
                    zone.center,
                    context.as_of,
                    context.as_of,
                    known[0],
                    known[-1],
                    len(members),
                    parameters,
                )
            )
    return tuple(results)
