"""Historical hierarchy replay: the same no-lookahead evaluation, in the past.

Replaying the hierarchy over stored history exists for exactly the purposes the
project already documents for historical data:

* code correctness;
* lookahead detection;
* robustness across regimes;
* falsification;
* catastrophic failure discovery;
* execution sanity.

It is NOT proof of edge, and it is never used to optimise or parameter-mine
the hierarchy. Every replayed evaluation is the identical pure function the
live runner uses, evaluated at a past decision boundary with only the candles
that had fully closed by that boundary — so a replayed snapshot is exactly
what the live system would have known at that instant.
"""

from __future__ import annotations

import logging
from datetime import datetime

from trading_assistant.market_data.timeframes import require_utc_datetime
from trading_assistant.market_structure.candles import interval_for_timeframe
from trading_assistant.multi_timeframe.models import HierarchySnapshot
from trading_assistant.multi_timeframe.service import MultiTimeframeService

logger = logging.getLogger(__name__)


def replay_hierarchy(
    service: MultiTimeframeService,
    *,
    symbol: str | None = None,
    start: datetime,
    end: datetime,
    record: bool = False,
    recorded_at: datetime | None = None,
) -> tuple[HierarchySnapshot, ...]:
    """Evaluate the hierarchy at every closed execution boundary in ``[start, end]``.

    ``start`` and ``end`` are decision instants (execution-timeframe candle
    closes). Each evaluation is independent and uses only candles closed by
    that instant, so replaying can never see a candle that closed later. With
    ``record=True`` each evaluation is also appended to the immutable ledger
    (idempotently), which is what historical stress testing audits.
    """

    start_utc = require_utc_datetime(start, field_name="start")
    end_utc = require_utc_datetime(end, field_name="end")
    if end_utc < start_utc:
        raise ValueError("end must not be before start")
    execution_tf = service.hierarchy.execution.timeframe
    interval = interval_for_timeframe(execution_tf)
    cursor = start_utc
    snapshots: list[HierarchySnapshot] = []
    while cursor <= end_utc:
        snapshot = service.evaluate(symbol=symbol, decision_time=cursor)
        if record:
            service.record(snapshot, recorded_at=recorded_at)
        snapshots.append(snapshot)
        cursor += interval
    return tuple(snapshots)


__all__ = ["replay_hierarchy"]
