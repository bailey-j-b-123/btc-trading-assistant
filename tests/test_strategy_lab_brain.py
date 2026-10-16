"""Research exceptions leave original facts, availability and rules unchanged."""

from dataclasses import replace
from decimal import Decimal as D
import pytest
from test_multi_timeframe import _snapshot
from trading_assistant.multi_timeframe.models import HierarchyAlignment, ExecutionState
from trading_assistant.setup_qualification.models import SetupFamily
from trading_assistant.strategy_lab.brain import policy_decision, BrainPolicy
from trading_assistant.strategy_lab.entry_policy import PullbackEntry
from trading_assistant.strategy_lab.stop_quality import StopQuality


@pytest.mark.parametrize("entry_policy,stop_quality", [(None,None),(PullbackEntry(),None),(None,StopQuality(D(".25"),D(1),D(".25"),D(1),14))])
def test_actual_four_timeframe_cohort_rolls_back_and_resumes_exactly(tmp_path, entry_policy, stop_quality):
    import csv
    import io
    import zipfile
    import hashlib
    import json
    from datetime import UTC, datetime, timedelta
    from trading_assistant.strategy_lab.brain import compare_brain
    from trading_assistant.strategy_lab.store import Store
    from trading_assistant.strategy_lab.data import acquire

    def archive(month, tf, minutes):
        start = datetime.fromisoformat(month + "-01T00:00:00+00:00")
        end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        stream = io.StringIO()
        writer = csv.writer(stream)
        at = start
        while at < end:
            stamp = int(at.timestamp() * 1000)
            writer.writerow(
                [
                    stamp,
                    "100",
                    "102",
                    "99",
                    "101",
                    "10",
                    stamp + minutes * 60000 - 1,
                    "1000",
                    "1",
                    "1",
                    "100",
                    "0",
                ]
            )
            at += timedelta(minutes=minutes)
        name = f"BTCUSDT-{tf}-{month}"
        body = io.BytesIO()
        with zipfile.ZipFile(body, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(name + ".csv", stream.getvalue())
        raw = body.getvalue()
        return raw, (hashlib.sha256(raw).hexdigest() + "  " + name + ".zip").encode()

    a, b = Store(tmp_path / "a"), Store(tmp_path / "b")
    for month in ("2023-12", "2024-01"):
        for tf, minutes in (("4h", 240), ("1h", 60), ("15m", 15), ("5m", 5)):
            raw, checksum = archive(month, tf, minutes)
            for store in (a, b):
                acquire(
                    store,
                    [month],
                    tf,
                    download=lambda url: checksum if url.endswith(".CHECKSUM") else raw,
                )
    kwargs = dict(
        start=datetime(2024, 1, 1, tzinfo=UTC),
        end=datetime(2024, 1, 1, 0, 10, tzinfo=UTC),
        prepare=False,
        entry_policy=entry_policy,
        stop_quality=stop_quality,
    )
    full = compare_brain(a, **kwargs)
    assert full["complete"] and full["result"]["total_processed"] == 3

    def fail():
        raise RuntimeError("injected hierarchy transaction interruption")

    with pytest.raises(RuntimeError, match="injected"):
        compare_brain(b, fault=fail, diagnostics=True, **kwargs)
    assert b.db.execute("SELECT count(*) FROM brain_observations").fetchone()[0] == 0
    assert (
        json.loads(b.db.execute("SELECT state FROM brain_runs").fetchone()[0])["next"]
        == 0
    )
    assert b.db.execute("SELECT count(*) FROM candidate_diagnostic_coverage").fetchone()[0] == 0
    assert not compare_brain(b, max_boundaries=1, diagnostics=True, **kwargs)["complete"]
    b.close()
    b = Store(tmp_path / "b")
    recovered = compare_brain(b, diagnostics=True, **kwargs)
    assert recovered["id"] == full["id"]  # opt-in never changes the trading cohort definition
    assert recovered["result"]["counts"] == full["result"]["counts"]
    assert recovered["result"]["performance"] == full["result"]["performance"]
    assert compare_brain(b, diagnostics=True, **kwargs)["deduplicated"]
    assert b.db.execute("SELECT count(*) FROM candidate_diagnostic_coverage").fetchone()[0] == 3
    assert [
        r[0]
        for r in a.db.execute(
            "SELECT payload FROM brain_observations ORDER BY boundary"
        )
    ] == [
        r[0]
        for r in b.db.execute(
            "SELECT payload FROM brain_observations ORDER BY boundary"
        )
    ]
    with pytest.raises(ValueError, match="no backfill"):
        compare_brain(a, diagnostics=True, **kwargs)
    a.close()
    b.close()


def counter_snapshot():
    s = _snapshot()
    return replace(
        s,
        alignment=HierarchyAlignment.COUNTER_TREND,
        counter_trend=True,
        setup=replace(
            s.setup, direction="bullish", family=SetupFamily.LIQUIDITY_REVERSAL.value
        ),
        execution=replace(
            s.execution, state=ExecutionState.TRIGGERED, latest_close=D("103")
        ),
    )


def test_frozen_a1_blocks_a2_requires_real_reversal_a3_removes_only_directional_veto():
    s = counter_snapshot()
    before = s.to_json_dict()
    assert policy_decision(s, "A1")["decision"] == "awaiting_confirmation"
    for p in ("A2", "A3"):
        v = policy_decision(s, p)
        assert v["decision"] == "plannable" and v["actual_counter_trend"]
        assert v["actual_alignment"] == "counter_trend"
    ordinary = replace(s, setup=replace(s.setup, family="breakout_retest_continuation"))
    assert policy_decision(ordinary, "A2")["decision"] != "plannable"
    assert policy_decision(ordinary, "A3")["decision"] == "plannable"
    armed = replace(s, execution=replace(s.execution, state=ExecutionState.ARMED))
    assert policy_decision(armed, "A2")["decision"] != "plannable"
    assert s.to_json_dict() == before
    with pytest.raises(ValueError):
        BrainPolicy("invented")


def test_research_exceptions_never_override_gaps_staleness_or_unknown_context():
    s = counter_snapshot()
    for bad in (
        replace(s, context=replace(s.context, missing_candle_count=1)),
        replace(s, execution=replace(s.execution, stale=True)),
        replace(s, alignment=HierarchyAlignment.UNKNOWN),
    ):
        for p in ("A1", "A2", "A3"):
            assert policy_decision(bad, p)["decision"] != "plannable"
