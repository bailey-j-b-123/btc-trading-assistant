"""Chart geometry v1 — twelve-family chart evidence, analysis only.

This package adds the **eight missing chart-pattern families** to BRAIN's
evidence vocabulary, next to the four classical geometries that already live in
:mod:`trading_assistant.pattern_liquidity.classical_patterns` (double top,
double bottom, head and shoulders, inverse head and shoulders), which are left
completely untouched:

``bull_flag``, ``bear_flag``, ``rising_wedge``, ``falling_wedge``,
``ascending_triangle``, ``descending_triangle``, ``symmetrical_triangle``,
``cup_and_handle``

Every family is an explicit geometric **definition** over genuinely closed OHLCV
candles and Step 3 confirmed swings, evaluated with exact
:class:`~decimal.Decimal` arithmetic and pinned by tests. Definitions are not
validated probabilities: nothing here measures, estimates, or implies how often a
geometry resolves in any direction, how far it moves, or whether describing it is
profitable. Wedges and triangles in particular describe a contraction and the
direction *actually observed* when a closed candle leaves it — never a guaranteed
bullish or bearish outcome.

Hard boundaries of this package
-------------------------------
* **Analysis only.** It is pure: no I/O, no persistence, no database or
  repository access, no wall-clock reads, no randomness, no network, and no
  mutation of its inputs.
* **Not a decision input.** It must never feed setup qualification, trade
  planning, journaling, forward testing, or statistics, and it never persists
  anything on its own. Integration into read-only chart evidence happens outside
  this package, after independent review.
* **No execution surface.** It contains no order, account, credential, or
  execution concept of any kind.
* **No future candles, no invented data.** Only candles closed at ``as_of`` and
  only swings confirmed by then are used; missing history stays missing and is
  reported as a gap-driven rejection rather than being bridged; no display shape
  is generated, no detector record is fabricated, and no target price or
  confidence value exists anywhere in these records.

Public surface
--------------
:func:`detect_chart_geometry`
    The single entry point.
:class:`ChartGeometryParameters`
    The validated chart-geometry-v1 definitions, with :meth:`fingerprint`.
:class:`ChartGeometrySnapshot`, :class:`ChartGeometryFormation`,
:class:`ChartGeometryEvent`, :class:`FormationGeometry`, :class:`BoundaryLine`,
:class:`GeometryComponent`, :class:`GeometryMetric`
    Immutable evidence records.
:class:`ChartGeometryFamily`
    The eight family names.
"""

from trading_assistant.chart_geometry.detector import detect_chart_geometry
from trading_assistant.chart_geometry.families import (
    DEFINITION_BY_FAMILY,
    FAMILY_DEFINITIONS,
    ChartGeometryFamily,
    FamilyDefinition,
    minimum_component_count,
)
from trading_assistant.chart_geometry.lines import BoundaryLine, Convergence
from trading_assistant.chart_geometry.models import (
    ChartGeometryDiagnostics,
    ChartGeometryEvent,
    ChartGeometryFormation,
    ChartGeometrySnapshot,
    FormationGeometry,
    GeometryComponent,
    GeometryMetric,
    RejectionCount,
)
from trading_assistant.chart_geometry.parameters import (
    CHART_GEOMETRY_RULES_VERSION,
    ChartGeometryParameters,
    chart_geometry_fingerprint,
)

__all__ = [
    "CHART_GEOMETRY_RULES_VERSION",
    "DEFINITION_BY_FAMILY",
    "FAMILY_DEFINITIONS",
    "BoundaryLine",
    "ChartGeometryDiagnostics",
    "ChartGeometryEvent",
    "ChartGeometryFamily",
    "ChartGeometryFormation",
    "ChartGeometryParameters",
    "ChartGeometrySnapshot",
    "Convergence",
    "FamilyDefinition",
    "FormationGeometry",
    "GeometryComponent",
    "GeometryMetric",
    "RejectionCount",
    "chart_geometry_fingerprint",
    "detect_chart_geometry",
    "minimum_component_count",
]
