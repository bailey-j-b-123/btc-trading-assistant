"""``detect_chart_geometry`` — the read-only entry point of chart-geometry-v1.

The call is pure: it reads the candles it is given, derives confirmed swings with
the unchanged Step 3 rules, describes every geometry that is actually present,
evaluates each frozen formation's lifecycle against later closed candles, and
returns an immutable snapshot. It performs no I/O, no persistence, no wall-clock
reads, no randomness, and no network access, and it never mutates its inputs.

Anti-lookahead contract
-----------------------
* Only candles whose full interval had closed at ``as_of`` are used. Candles that
  were still forming are excluded and counted, never inspected.
* Components are Step 3 confirmed swings, so no pivot is used before its actual
  ``confirmed_at`` instant.
* A formation's ``formed_at`` is the latest confirmation instant of its
  components; only candles closing strictly after that instant can confirm or
  invalidate it.
* Freezing: every family has a fixed-size component window over confirmed swings,
  and a confirmed swing never changes once Step 3 has reported it. A formation's
  two boundary lines are therefore anchored on the same two real pivots forever:
  later candles can add *new* formations (with new ids) but can never re-fit,
  widen, shift, or extend an already-described one. The candidate set only grows
  with the closed prefix, which the prefix-equivalence tests pin.
* Terminal invalidation: once a formation id is invalidated it can never be
  confirmed, re-formed, or resurrected by later candles.

What this is not
----------------
This package describes geometry. It does not qualify setups, plan trades, size
positions, compute targets, estimate probabilities, or claim that any described
geometry is profitable or likely to resolve in a given direction. It persists
nothing: it has no database, repository, or file access of any kind.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from trading_assistant.chart_geometry.checks import (
    REASON_GAP_AFTER_FORMATION,
    CandleIndex,
    RejectionTally,
)
from trading_assistant.chart_geometry.converging import converging_candidates
from trading_assistant.chart_geometry.cup_and_handle import cup_and_handle_candidates
from trading_assistant.chart_geometry.families import (
    ChartGeometryFamily,
    minimum_component_count,
)
from trading_assistant.chart_geometry.flags import flag_candidates
from trading_assistant.chart_geometry.identity import formation_identity
from trading_assistant.chart_geometry.lifecycle import lifecycle_events
from trading_assistant.chart_geometry.models import (
    CandidateFormation,
    ChartGeometryDiagnostics,
    ChartGeometryEvent,
    ChartGeometryFormation,
    ChartGeometrySnapshot,
    RejectionCount,
)
from trading_assistant.chart_geometry.parameters import (
    CHART_GEOMETRY_RULES_VERSION,
    ChartGeometryParameters,
)
from trading_assistant.market_data.timeframes import require_utc_datetime
from trading_assistant.market_data.types import Candle
from trading_assistant.market_structure.candles import (
    interval_for_timeframe,
    ordered_candles,
)
from trading_assistant.market_structure.swings import SwingParameters, detect_swings

#: Families in the documented order used for every sorted output of this package.
FAMILY_ORDER: tuple[ChartGeometryFamily, ...] = tuple(ChartGeometryFamily)

#: The smallest component count of any family; used for the advisory
#: ``required_candle_count`` diagnostic.
SMALLEST_COMPONENT_COUNT = min(
    minimum_component_count(family) for family in ChartGeometryFamily
)


def _require_instrument(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def detect_chart_geometry(
    candles: Iterable[Candle],
    *,
    exchange: str,
    symbol: str,
    timeframe: str,
    as_of: datetime,
    parameters: ChartGeometryParameters | None = None,
) -> ChartGeometrySnapshot:
    """Describe every chart-geometry-v1 formation knowable from closed candles.

    ``candles`` must be genuinely closed OHLCV rows for exactly one instrument,
    strictly ordered by open time and spaced by whole multiples of the timeframe
    interval (the existing Step 3 validation is applied unchanged and raises
    rather than repairing anything). ``exchange``, ``symbol``, and ``timeframe``
    scope the identities and must match every candle. ``as_of`` is the causal
    instant: only candles that had fully closed by then are used, and only
    confirmed swings knowable by then can become components.

    Returns an immutable :class:`ChartGeometrySnapshot`.
    """

    resolved = parameters if parameters is not None else ChartGeometryParameters()
    scoped_exchange = _require_instrument(exchange, name="exchange")
    scoped_symbol = _require_instrument(symbol, name="symbol")
    scoped_timeframe = _require_instrument(timeframe, name="timeframe")
    interval = interval_for_timeframe(scoped_timeframe)
    as_of_utc = require_utc_datetime(as_of, field_name="as_of")

    provided = tuple(candles)
    validated = ordered_candles(provided, interval=interval)
    for candle_index, candle in enumerate(validated):
        if (candle.exchange, candle.symbol, candle.timeframe) != (
            scoped_exchange,
            scoped_symbol,
            scoped_timeframe,
        ):
            raise ValueError(
                f"candle {candle_index} must belong to the exchange, symbol, and timeframe "
                "given to detect_chart_geometry"
            )
    closed = tuple(c for c in validated if c.timestamp + interval <= as_of_utc)
    excluded_future = len(validated) - len(closed)

    swing_parameters = SwingParameters(
        left_window=resolved.swing_left_window,
        right_window=resolved.swing_right_window,
        tie_policy=resolved.swing_tie_policy,
    )
    swing_result = detect_swings(
        closed, interval=interval, as_of=as_of_utc, parameters=swing_parameters
    )
    swings = swing_result.swings

    index = CandleIndex.build(closed, interval)
    tally = RejectionTally()
    candidates: list[CandidateFormation] = []
    candidates.extend(
        converging_candidates(swings, index=index, parameters=resolved, tally=tally)
    )
    candidates.extend(flag_candidates(swings, index=index, parameters=resolved, tally=tally))
    candidates.extend(
        cup_and_handle_candidates(swings, index=index, parameters=resolved, tally=tally)
    )

    # Detection order is fixed (converging families, flags, cup and handle) and
    # every window is a fixed-size slice of the confirmed-swing sequence, so the
    # accepted set is deterministic and grows monotonically with the closed
    # prefix. No candidate is ever re-fitted by a later candle.
    accepted = sorted(
        candidates,
        key=lambda candidate: (
            candidate.formed_at,
            candidate.components[0].timestamp,
            candidate.family.value,
        ),
    )
    fingerprint = resolved.fingerprint()
    formations: list[ChartGeometryFormation] = []
    events: list[ChartGeometryEvent] = []
    truncated_count = 0
    for candidate in accepted:
        formation_id = formation_identity(
            exchange=scoped_exchange,
            symbol=scoped_symbol,
            timeframe=scoped_timeframe,
            family=candidate.family,
            parameters_fingerprint=fingerprint,
            components=candidate.components,
        )
        # A formation may only ever be advanced by candles that follow its first
        # component without a single missing candle in between. The run stops at
        # the first hole; nothing beyond it is inspected, so no state transition
        # is ever taken across unseen history.
        run = index.contiguous_run_from(candidate.components[0].timestamp)
        truncated = bool(run) and bool(closed) and run[-1].timestamp != closed[-1].timestamp
        if truncated:
            truncated_count += 1
        evaluation_candles = tuple(
            candle for candle in run if candle.timestamp + interval > candidate.formed_at
        )
        lifecycle = lifecycle_events(
            candidate,
            formation_id,
            index=index,
            parameters=resolved,
            evaluation_candles=evaluation_candles,
        )
        terminal = lifecycle[-1]
        formations.append(
            ChartGeometryFormation(
                formation_id=formation_id,
                family=candidate.family,
                state=terminal.state,
                exchange=scoped_exchange,
                symbol=scoped_symbol,
                timeframe=scoped_timeframe,
                rules_version=CHART_GEOMETRY_RULES_VERSION,
                parameters_fingerprint=fingerprint,
                components=candidate.components,
                geometry=candidate.geometry,
                breakout_side=candidate.breakout_side,
                failure_side=candidate.failure_side,
                formed_at=candidate.formed_at,
                known_at=terminal.known_at,
                first_component_time=candidate.geometry.first_component_time,
                last_component_time=candidate.geometry.last_component_time,
                span_candles=candidate.geometry.span_candles,
                observed_direction=terminal.observed_direction,
                terminal_reason=None if terminal.state == "formed" else terminal.reason,
                lifecycle_truncated=truncated,
                lifecycle_truncation_reason=(
                    REASON_GAP_AFTER_FORMATION if truncated else None
                ),
                events=lifecycle,
            )
        )
        events.extend(lifecycle)

    family_rank = {family: position for position, family in enumerate(FAMILY_ORDER)}
    formations.sort(
        key=lambda formation: (
            formation.formed_at,
            family_rank[formation.family],
            formation.first_component_time,
            formation.formation_id,
        )
    )
    events.sort(
        key=lambda event: (
            event.known_at,
            family_rank[event.family],
            event.formation_id,
            event.state,
        )
    )

    required_candles = (
        SMALLEST_COMPONENT_COUNT + resolved.swing_left_window + resolved.swing_right_window
    )
    diagnostics = ChartGeometryDiagnostics(
        closed_candle_count=len(closed),
        excluded_future_candle_count=excluded_future,
        required_candle_count=required_candles,
        insufficient_history=len(closed) < required_candles,
        confirmed_swing_count=len(swings),
        candidate_window_count=tally.windows_evaluated,
        accepted_candidate_count=tally.accepted,
        formation_count=len(formations),
        formed_only_count=sum(1 for f in formations if f.state == "formed"),
        gap_truncated_count=truncated_count,
        confirmed_count=sum(1 for f in formations if f.state == "confirmed"),
        invalidated_count=sum(1 for f in formations if f.state == "invalidated"),
        rejections=tuple(
            RejectionCount(family, reason, count)  # type: ignore[arg-type]
            for family, reason, count in tally.counts()
        ),
    )
    snapshot = ChartGeometrySnapshot(
        exchange=scoped_exchange,
        symbol=scoped_symbol,
        timeframe=scoped_timeframe,
        as_of=as_of_utc,
        rules_version=CHART_GEOMETRY_RULES_VERSION,
        parameters_fingerprint=fingerprint,
        interval_seconds=int(interval.total_seconds()),
        parameters=resolved,
        formations=tuple(formations),
        events=tuple(events),
        diagnostics=diagnostics,
    )
    return snapshot
