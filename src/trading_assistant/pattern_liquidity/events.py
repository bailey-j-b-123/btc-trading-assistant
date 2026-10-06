"""Immutable, JSON-serializable evidence; no trade qualification fields."""

import json
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from typing import Literal

from trading_assistant.market_data.types import Candle
from trading_assistant.market_structure.levels import SupportResistanceZone
from trading_assistant.market_structure.ranges import RangeDetection
from trading_assistant.market_structure.snapshot import to_jsonable
from trading_assistant.market_structure.swings import SwingPoint
from trading_assistant.market_structure.volatility import VolatilityContext
from trading_assistant.market_structure.volume import VolumeContext
from trading_assistant.pattern_liquidity.parameters import PatternLiquidityParameters

Direction = Literal["bullish", "bearish"]


#: Bound for the identity memo below: a full multi-year replay holds tens of
#: thousands of distinct references, so this comfortably covers the working
#: set while keeping worst-case memory in the tens of megabytes.
_IDENTITY_MEMO_MAX = 32768

#: LRU memo of computed identities, keyed by ``repr`` of the inputs.
#:
#: The chronological replay re-identifies the same structural references once
#: per bar (measured: ~79k calls, ~2k distinct, over 720 candles), so
#: memoizing is worth several seconds per snapshot. The key must be ``repr``,
#: never ``hash`` or equality: ``to_jsonable`` renders ``Decimal('1.10')`` as
#: ``'1.10'`` (spelling-sensitive) while ``hash`` is numeric, so distinct
#: spellings are distinct identities that only ``repr`` keeps apart. Values
#: are pure SHA-256 digests, so concurrent recomputation or eviction changes
#: timing only, never any returned id; every ``move_to_end``/``popitem`` race
#: degrades to a recomputation, never to a wrong answer.
_identity_memo: OrderedDict[str, str] = OrderedDict()


def identity(*parts: object) -> str:
    """Versioned SHA-256 of canonical structural identity, never random UUIDs."""
    key = repr(parts)
    cached = _identity_memo.get(key)
    if cached is not None:
        try:
            _identity_memo.move_to_end(key)
        except KeyError:
            pass  # evicted concurrently; the cached digest is still correct
        return cached
    payload = json.dumps(to_jsonable(parts), sort_keys=True, separators=(",", ":"))
    value = sha256(("pattern-liquidity-v1:" + payload).encode()).hexdigest()
    _identity_memo[key] = value
    if len(_identity_memo) > _IDENTITY_MEMO_MAX:
        _identity_memo.popitem(last=False)
    return value


@dataclass(frozen=True, slots=True)
class Reference:
    id: str
    type: Literal["swing_high", "swing_low", "zone", "range_high", "range_low"]
    band_low: Decimal
    band_high: Decimal
    known_at: datetime
    swings: tuple[SwingPoint, ...]
    zone: SupportResistanceZone | None = None
    range: RangeDetection | None = None


@dataclass(frozen=True, slots=True)
class Breakout:
    id: str
    direction: Direction
    reference: Reference
    previous_candle: Candle
    candle: Candle
    known_at: datetime
    confirmation_candles: tuple[Candle, ...]
    breakout_close: Decimal
    penetration: Decimal
    penetration_pct: Decimal
    penetration_atr: Decimal | None
    volatility: VolatilityContext
    volume: VolumeContext
    parameters: PatternLiquidityParameters


@dataclass(frozen=True, slots=True)
class FailedBreakout:
    id: str
    breakout: Breakout
    candle: Candle
    known_at: datetime
    reentry_distance: Decimal
    elapsed_candles: int
    elapsed_seconds: int
    evidence_candles: tuple[Candle, ...]
    parameters: PatternLiquidityParameters


@dataclass(frozen=True, slots=True)
class Sweep:
    id: str
    direction: Literal["above", "below"]
    reference: Reference
    previous_candle: Candle
    candle: Candle
    known_at: datetime
    extreme: Decimal
    penetration: Decimal
    penetration_pct: Decimal
    penetration_atr: Decimal | None
    reclaim_close: Decimal
    volatility: VolatilityContext
    volume: VolumeContext
    parameters: PatternLiquidityParameters


@dataclass(frozen=True, slots=True)
class Retest:
    id: str
    breakout: Breakout
    state: Literal["observed", "held", "failed"]
    candle: Candle
    known_at: datetime
    distance_from_band: Decimal
    elapsed_candles: int
    evidence_candles: tuple[Candle, ...]
    parameters: PatternLiquidityParameters


@dataclass(frozen=True, slots=True)
class EqualLevelCluster:
    id: str
    type: Literal["equal_high", "equal_low"]
    members: tuple[SwingPoint, ...]
    band_low: Decimal
    band_high: Decimal
    center: Decimal
    first_known_at: datetime
    known_at: datetime
    first_member_confirmed_at: datetime
    latest_member_confirmed_at: datetime
    member_count: int
    parameters: PatternLiquidityParameters


@dataclass(frozen=True, slots=True)
class PatternGeometry:
    matching_extremes_difference_pct: Decimal
    depth_pct: Decimal
    head_prominence_pct: Decimal | None
    neckline_difference_pct: Decimal
    span_candles: int


@dataclass(frozen=True, slots=True)
class ChartPattern:
    id: str
    pattern_id: str
    type: Literal[
        "double_top",
        "double_bottom",
        "head_and_shoulders",
        "inverse_head_and_shoulders",
    ]
    state: Literal["formed", "confirmed", "invalidated"]
    components: tuple[SwingPoint, ...]
    neckline: Decimal
    invalidation_level: Decimal
    geometry: PatternGeometry
    formation_timestamp: datetime
    formed_at: datetime
    known_at: datetime
    confirmation_timestamp: datetime | None
    evidence_candles: tuple[Candle, ...]
    parameters: PatternLiquidityParameters


Event = Breakout | FailedBreakout | Sweep | Retest | EqualLevelCluster | ChartPattern
