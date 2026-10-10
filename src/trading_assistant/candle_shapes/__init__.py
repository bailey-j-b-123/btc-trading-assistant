"""Descriptive single- and two-candle shape classification (display evidence).

These shapes describe what one or two CLOSED candles look like. They are
deliberately NOT trade signals: they never enter setup qualification, planning,
journaling, or forward outcome evaluation. Every rule is an explicit,
deterministic threshold documented in :mod:`parameters` and pinned by tests.
"""

from trading_assistant.candle_shapes.detector import (
    CandleShapeEvent,
    CandleShapeKind,
    candle_direction,
    detect_candle_shapes,
)
from trading_assistant.candle_shapes.parameters import CandleShapeParameters

__all__ = [
    "CandleShapeEvent",
    "CandleShapeKind",
    "CandleShapeParameters",
    "candle_direction",
    "detect_candle_shapes",
]
