"""Deterministic support/resistance zones derived from confirmed swings.

Every zone is a cluster of confirmed swing extremes whose prices are within a
configurable tolerance of the cluster anchor price. Each zone reports only
measurable fields (band, touch counts, first/last test timestamps, source swing
timestamps, distance from the latest close); no subjective strength label is
produced. Chronological anchoring and a documented tie-break keep the clustering
reproducible for identical candle data.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from trading_assistant.market_data.timeframes import require_utc_datetime
from trading_assistant.market_structure.numeric import (
    as_decimal,
    mean,
    percentage_of,
    percentage_width,
    quantize_derived,
    require_int,
    tolerance_band,
)
from trading_assistant.market_structure.swings import SwingKind, SwingPoint


class ZoneRole(StrEnum):
    """Position of a zone relative to the latest closed candle close."""

    SUPPORT = "support"
    RESISTANCE = "resistance"
    AT_PRICE = "at_price"


@dataclass(frozen=True, slots=True)
class LevelParameters:
    """Documented, configurable rules for zone clustering."""

    lookback_swings: int = 40
    tolerance_pct: Decimal = Decimal(1)
    min_touches: int = 1
    max_zones: int = 12

    def __post_init__(self) -> None:
        for name, minimum in (("lookback_swings", 1), ("min_touches", 1), ("max_zones", 1)):
            require_int(getattr(self, name), name=name, minimum=minimum)
        tolerance = as_decimal(self.tolerance_pct, name="tolerance_pct")
        if not Decimal(0) < tolerance < Decimal(100):
            raise ValueError("tolerance_pct must be greater than 0 and less than 100")
        object.__setattr__(self, "tolerance_pct", tolerance)
        if self.min_touches > self.lookback_swings:
            raise ValueError("min_touches must not exceed lookback_swings")


@dataclass(frozen=True, slots=True)
class SupportResistanceZone:
    """One clustered support/resistance zone with its swing evidence."""

    role: ZoneRole
    band_low: Decimal
    band_high: Decimal
    center: Decimal
    band_width: Decimal
    band_width_pct: Decimal
    touch_count: int
    high_source_count: int
    low_source_count: int
    first_observed_timestamp: datetime
    last_tested_timestamp: datetime
    source_swing_timestamps: tuple[datetime, ...]
    latest_close: Decimal | None
    distance_pct_from_latest_close: Decimal | None
    tolerance_pct: Decimal
    as_of: datetime

    @property
    def tested_as_support(self) -> bool:
        """Whether at least one swing low was observed in this zone."""

        return self.low_source_count > 0

    @property
    def tested_as_resistance(self) -> bool:
        """Whether at least one swing high was observed in this zone."""

        return self.high_source_count > 0


@dataclass(frozen=True, slots=True)
class LevelDetectionResult:
    """Clustered zones plus diagnostics for the clustering run."""

    zones: tuple[SupportResistanceZone, ...]
    parameters: LevelParameters
    as_of: datetime
    swing_count: int
    cluster_count: int
    discarded_below_min_touches: int
    discarded_beyond_max_zones: int

    @property
    def sufficient(self) -> bool:
        """Whether any confirmed swing was available for zone derivation."""

        return self.swing_count >= self.parameters.min_touches


@dataclass(slots=True)
class _Cluster:
    anchor_price: Decimal
    members: list[SwingPoint]


def _cluster_swings(
    swings: tuple[SwingPoint, ...],
    parameters: LevelParameters,
) -> list[_Cluster]:
    """Greedy chronological clustering around the first swing of each cluster.

    Each swing is tested against every existing cluster anchor in the order the
    clusters were created. It joins the nearest anchor within tolerance
    (ties go to the earliest-created cluster) or starts a new cluster.
    """

    clusters: list[_Cluster] = []
    for swing in swings:
        best_index: int | None = None
        best_distance: Decimal | None = None
        for index, cluster in enumerate(clusters):
            distance = abs(swing.price - cluster.anchor_price)
            if distance <= tolerance_band(cluster.anchor_price, parameters.tolerance_pct) and (
                best_distance is None or distance < best_distance
            ):
                best_index = index
                best_distance = distance
        if best_index is None:
            clusters.append(_Cluster(anchor_price=swing.price, members=[swing]))
        else:
            clusters[best_index].members.append(swing)
    return clusters


def _zone_role(center: Decimal, latest_close: Decimal | None) -> ZoneRole:
    if latest_close is None:
        return ZoneRole.AT_PRICE
    if center < latest_close:
        return ZoneRole.SUPPORT
    if center > latest_close:
        return ZoneRole.RESISTANCE
    return ZoneRole.AT_PRICE


def detect_zones(
    swings: Iterable[SwingPoint],
    *,
    as_of: datetime,
    latest_close: Decimal | None = None,
    parameters: LevelParameters | None = None,
) -> LevelDetectionResult:
    """Cluster confirmed swings into deterministic support/resistance zones."""

    resolved = parameters if parameters is not None else LevelParameters()
    as_of_utc = require_utc_datetime(as_of, field_name="as_of")
    confirmed = tuple(
        sorted(
            (swing for swing in swings if swing.confirmed_at <= as_of_utc),
            key=lambda swing: (swing.timestamp, swing.kind),
        )
    )
    considered = confirmed[-resolved.lookback_swings :]
    clusters = _cluster_swings(considered, resolved)

    zones: list[SupportResistanceZone] = []
    discarded_below_min_touches = 0
    for cluster in clusters:
        if len(cluster.members) < resolved.min_touches:
            discarded_below_min_touches += 1
            continue
        prices = [member.price for member in cluster.members]
        band_low = min(prices)
        band_high = max(prices)
        center = mean([band_low, band_high])
        high_source_count = sum(1 for member in cluster.members if member.kind is SwingKind.HIGH)
        low_source_count = len(cluster.members) - high_source_count
        timestamps = tuple(member.timestamp for member in cluster.members)
        zones.append(
            SupportResistanceZone(
                role=_zone_role(center, latest_close),
                band_low=band_low,
                band_high=band_high,
                center=center,
                band_width=quantize_derived(band_high - band_low),
                band_width_pct=percentage_width(band_high, band_low),
                touch_count=len(cluster.members),
                high_source_count=high_source_count,
                low_source_count=low_source_count,
                first_observed_timestamp=min(timestamps),
                last_tested_timestamp=max(timestamps),
                source_swing_timestamps=timestamps,
                latest_close=latest_close,
                distance_pct_from_latest_close=(
                    percentage_of(center - latest_close, latest_close) if latest_close else None
                ),
                tolerance_pct=resolved.tolerance_pct,
                as_of=as_of_utc,
            )
        )

    # Deterministic order: most-touched zones first, then most recently tested,
    # then lowest center. Sorting in two stable passes avoids float timestamps.
    zones.sort(key=lambda zone: zone.center)
    zones.sort(key=lambda zone: (zone.touch_count, zone.last_tested_timestamp), reverse=True)
    discarded_beyond_max_zones = max(0, len(zones) - resolved.max_zones)
    return LevelDetectionResult(
        zones=tuple(zones[: resolved.max_zones]),
        parameters=resolved,
        as_of=as_of_utc,
        swing_count=len(considered),
        cluster_count=len(clusters),
        discarded_below_min_touches=discarded_below_min_touches,
        discarded_beyond_max_zones=discarded_beyond_max_zones,
    )
