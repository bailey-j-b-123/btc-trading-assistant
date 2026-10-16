"""Default-deny research access to sealed 2025; not a Python security sandbox."""

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime

_custody = ContextVar("research_holdout_custody", default=None)
_start = datetime(2025, 1, 1, tzinfo=UTC)
_end = datetime(2026, 1, 1, tzinfo=UTC)


def require_period(store, start, end, timeframe, *, candidate=None, stage=None):
    """Call before archive/cache access or experiment admission, not after reads."""
    if (
        any(
            t.tzinfo is None or t.utcoffset().total_seconds() != 0 for t in (start, end)
        )
        or start >= end
    ):
        raise ValueError("ordered explicit UTC research period required")
    if max(start, _start) >= min(end, _end):
        return
    custody = _custody.get()
    if custody is not None:
        owner, a, b, tf, frozen = custody
        if (
            owner is store
            and a <= start < end <= b
            and tf == timeframe
            and (candidate is None or (candidate == frozen and stage == "holdout"))
        ):
            return
    raise ValueError(
        "sealed 2025 requires the explicitly approved frozen campaign custody path"
    )


@contextmanager
def _approved_campaign_period(store, start, end, timeframe, candidate):
    """Internal only: entered after campaign approval AND one-use custody checks.

    Never expose this as a CLI flag, stage label, configuration boolean or grant.
    A caller able to edit/invoke arbitrary Python can bypass application checks;
    this prevents accidental access through supported research entry points.
    """
    token = _custody.set((store, start, end, timeframe, candidate))
    try:
        yield
    finally:
        _custody.reset(token)
