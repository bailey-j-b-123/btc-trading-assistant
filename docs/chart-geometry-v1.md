# Chart Geometry v1

`trading_assistant.chart_geometry` describes eight geometric pattern families
from confirmed swing pivots in closed OHLCV candles. It is **analysis-only
chart evidence**. The definitions are deterministic rules, not a validated
probability model: they do not estimate how often a pattern resolves in any
direction, a likely move, a target, or profitability. In particular, wedges and
triangles describe contraction and any direction actually observed after the
formation becomes knowable; they do not forecast a bullish or bearish outcome.

This package does not feed setup qualification, planning, risk, journaling,
execution, or outcomes, and it does not persist records. Any integration into
read-only chart evidence is outside this package and requires independent
review. Existing classical pattern detectors are unchanged.

## API and input contract

```python
from datetime import datetime
from trading_assistant.chart_geometry import (
    ChartGeometryParameters,
    detect_chart_geometry,
)

snapshot = detect_chart_geometry(
    candles,
    exchange="binance",
    symbol="BTC/USDT",
    timeframe="1h",
    as_of=datetime.now().astimezone(),  # supply the caller's explicit instant
    parameters=ChartGeometryParameters(),
)
```

The actual `as_of` must be supplied by the caller as an aware datetime; the
illustrative expression above is not used internally. The detector reads no
clock. `candles` must be chronologically ordered `Candle` values for the given
exchange, symbol, and fixed-duration timeframe. The detector does not sort,
repair, or resample them. Unsorted or duplicate timestamps, non-integral spacing,
unsupported timeframes, naive `as_of`, or a candle whose instrument differs
from the requested scope raise `ValueError`.

A candle opened at `t` is eligible only if `t + interval <= as_of`. Only that
closed prefix is passed to the existing Step 3 confirmed-swing rules, including
the configured left/right windows and tie policy. Each component retains its
original pivot timestamp and the actual confirmation instant and confirmation
candle timestamp. A swing that is not confirmed by `as_of` is not a component.

The closed prefix is also checked with Step 2's existing `validate_ohlcv_rows`:
OHLC values must be finite and positive, the high/low must bound the open and
close, and volume must be finite and non-negative. Invalid closed rows raise
`CandleValidationError` with the Step 2 issue report. Unclosed future rows are
excluded before this OHLCV-value check, so mutations to their prices or volume
do not affect the snapshot; their sequence order, spacing, and instrument scope
are still validated.

A missing candle is never synthesized. Candidate windows use consecutive
confirmed swings; the detector never skips a pivot or bridges a candle gap to
make a pattern fit. A gap inside a candidate's component range rejects it. A
formation's later lifecycle is evaluated only along the contiguous run starting
at its first component. If that run ends at a gap, the formation remains in its
last known state and reports lifecycle truncation. Missing history before a
fully observed component window does not shift or re-time its pivots.

## Version and parameters

The rules cohort is `chart-geometry-v1`. `ChartGeometryParameters` is frozen,
validates its ranges and relationships, and provides a canonical SHA-256
fingerprint. The fingerprint includes the version and every parameter, so a
threshold change creates a distinct identity cohort. The default values below
are conservative, configurable **definition choices**, not values fitted to
historical performance.

| Parameter | Default | Meaning |
|---|---:|---|
| `swing_left_window` | 2 candles | Left side of the Step 3 swing window |
| `swing_right_window` | 2 candles | Right side required before a swing is confirmed |
| `swing_tie_policy` | `strict` | Equal extremes do not create tied swings |
| `min_span_candles` / `max_span_candles` | 4 / 240 | Inclusive first-to-last component span |
| `min_boundary_gap_pct` | 0.05% | Minimum positive separation of the two boundaries at component points |
| `min_boundary_travel_pct` | 0.5% | Minimum absolute boundary travel that counts as sloped |
| `flat_boundary_max_travel_pct` | 0.25% | Maximum absolute boundary travel that counts as flat; strictly below the sloped threshold |
| `breakout_tolerance_pct` | 0.1% | Close must exceed a frozen boundary by more than this boundary-relative tolerance |
| `pole_min_change_pct` | 3% | Minimum directional pole move relative to its starting pivot |
| `pole_min_candles` / `pole_max_candles` | 3 / 60 | Inclusive pole length |
| `flag_min_retrace_pct` / `flag_max_retrace_pct` | 10% / 50% | Inclusive consolidation retracement of the pole move |
| `flag_min_consolidation_candles` / `flag_max_consolidation_candles` | 3 / 60 | Inclusive flag-body span |
| `cup_min_depth_pct` | 5% | Minimum deepest trough below the rim line |
| `cup_rim_tolerance_pct` | 2% | Maximum difference between left and right rim pivots |
| `cup_min_recovery_pct` | 90% | Minimum share of the descent recovered by the right rim |
| `cup_trough_base_tolerance_pct` | 1% | Maximum difference between the two trough lows |
| `cup_min_interior_prominence_pct` | 10% | Minimum interior high prominence relative to cup depth |
| `cup_min_leg_candles` | 3 | Minimum left-rim-to-trough and trough-to-right-rim leg lengths |
| `handle_min_retrace_pct` / `handle_max_retrace_pct` | 5% / 60% | Inclusive handle pullback as a share of cup depth |
| `handle_min_candles` / `handle_max_candles` | 3 / 60 | Inclusive handle span |

The minimum component and alternating-pivot requirements are fixed by family:

| Family | Minimum confirmed swings | Required geometry | Confirmation side / failure side |
|---|---:|---|---|
| `bull_flag` | 5 | Low → high pole, then low → high → low constrained flag body | above / below |
| `bear_flag` | 5 | High → low pole, then high → low → high constrained flag body | below / above |
| `ascending_triangle` | 4 | Flat upper line and rising lower line | either / none |
| `descending_triangle` | 4 | Falling upper line and flat lower line | either / none |
| `symmetrical_triangle` | 4 | Falling upper line and rising lower line | either / none |
| `rising_wedge` | 4 | Both lines rise; the lower line rises faster and the gap contracts | either / none |
| `falling_wedge` | 4 | Both lines fall; the upper line falls faster and the gap contracts | either / none |
| `cup_and_handle` | 8 | Two rims around a rounded two-low trough, followed by a later constrained three-swing handle | above / below |

For all five converging families, the upper line is anchored on the two actual
confirmed swing highs and the lower line on the two actual confirmed swing lows.
Both lines must be ordered at every component, separated by the configured
minimum gap, non-parallel, strictly converging, and have an apex strictly after
the last component. A rectangle, widening shape, parallel boundary, crossed
boundary, or shape outside the eight explicit v1 definitions is rejected with a
reason rather than relabelled as a pattern.

A flag's first two swings are its pole. The pole must move in the family
direction by at least the configured minimum. Its three body swings must remain
inside the pole range, retrace a bounded share of the pole, and have
non-expanding boundaries that do not continue travelling with the pole. A
pattern without that real prior pole is not a flag.

A cup and handle requires an explicit left rim, two comparable trough lows with
a prominent interior high, adequate descent and ascent lengths, a recovered
right rim, and then a later three-swing handle below that rim. The handle must
stay inside the bounded retracement/span rules and have non-expanding boundaries.
A shallow or unrounded cup, a V-shaped trough, a missing handle, or a handle
that rises to/through the right rim is rejected.

## Geometry, events, and identity

Boundary lines are exact `Decimal` lines through two actual component pivots.
They are not regression fits or smoothed estimates. Their stored anchor times
and prices are retained, and reported geometry metrics are recomputed from those
anchors. The package creates no display shape, detector record, target, zone,
neckline, confidence, or probability.

Each immutable formation ID is scoped to exchange, symbol, timeframe, family,
rules version, parameter fingerprint, and each component's role, pivot kind,
original pivot time, exact price, and actual confirmation instant. Event IDs are
a separate identity space: they include the formation ID and lifecycle event
facts, so an event ID is not a formation ID. Frozen geometry has a stable digest
shared by all events of that formation.

The first event is `formed`, known at the close that confirms the last required
component. A later closed candle must close strictly after that instant before
it can transition the lifecycle. Event anchors are real supplied closed
candles; boundary values are evaluated at each anchor's own open time. Each
event records causal `known_at`, observed direction where applicable, exact
boundary values, penetration where applicable, and a reason plus factual detail.

A close beyond a directional family's continuation side confirms it; a close
beyond its failure side invalidates it. Either side confirms a converging family,
recording only the direction actually observed. If frozen boundaries meet or
cross before a transition, the formation is invalidated for
`boundaries_converged_or_crossed`. The first terminal transition wins: an
invalidated formation ID cannot later confirm or resurrect, and a confirmed ID
cannot emit later lifecycle events. Geometry is never re-fitted as new candles
arrive; later confirmed swings can create a separate formation with a separate
ID.

## Diagnostics and tests

`ChartGeometrySnapshot` and its nested records are immutable. Its diagnostics
report the closed/excluded candle counts, required minimum history, confirmed
swing and candidate-window counts, accepted formations, lifecycle outcomes,
gap truncations, and exact rejection reasons. `to_json_dict()` provides the
stable JSON-compatible projection; decimal values are serialized as exact
strings.

The tests use **offline synthetic candles only**. They verify positive and
negative geometry independently for all eight families, exact Decimal lines and
metrics, minimum history, gaps, causal chronology, prefix equivalence at every
closed boundary, extreme future-candle mutation, frozen geometry, terminal
invalidation, deterministic IDs including a fresh process, no input mutation,
instrument validation, and package dependency boundaries. These fixtures are
not historical market data and do not represent trading decisions or outcomes.

```bash
.venv/bin/python -m pytest tests/test_chart_geometry_families.py tests/test_chart_geometry_contract.py -q
ruff check --isolated --select E4,E7,E9,F src tests
```
