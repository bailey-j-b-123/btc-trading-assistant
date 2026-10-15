"""Offline forward-controller tests. Injected promotion is software-test ONLY."""

from dataclasses import asdict, replace
from datetime import timedelta
from decimal import Decimal as D
import json
import pytest
from test_strategy_lab import candles
from trading_assistant.strategy_lab.models import Strategy, Execution, canonical
from trading_assistant.strategy_lab.models import research_code_fingerprint
from trading_assistant.strategy_lab.strategies import decide
from trading_assistant.strategy_lab.paper import paper_schema, poll_paper, public_closed
from trading_assistant.strategy_lab.store import Store


def payload(c):
    return json.dumps(
        [
            [
                int(x.timestamp.timestamp() * 1000),
                str(x.open),
                str(x.high),
                str(x.low),
                str(x.close),
                str(x.volume),
                int(x.timestamp.timestamp() * 1000) + 3599999,
                "10",
                1,
                "1",
                "10",
                "0",
            ]
            for x in c
        ]
    ).encode()


def test_public_feed_is_scoped_closed_fresh_and_never_substitutes_a_fake_current_candle():
    c = candles(70)
    now = c[-1].timestamp + timedelta(hours=1, minutes=1)
    loaded, receipt = public_closed("1h", now=now, fetch=lambda url: payload(c))
    assert loaded == c and receipt["count"] == 70
    with pytest.raises(ValueError, match="current"):
        public_closed("1h", now=now + timedelta(hours=1), fetch=lambda url: payload(c))
    with pytest.raises(ValueError, match="interval"):
        rows = json.loads(payload(c))
        rows[-1][6] -= 1
        public_closed("1h", now=now, fetch=lambda url: json.dumps(rows).encode())
    with pytest.raises(ValueError):
        public_closed("5m", now=now, fetch=lambda url: payload(c))


def test_real_controller_records_no_candidate_as_no_trades_and_duplicate_feed_rows_are_stable(
    tmp_path,
):
    c = candles(70)
    now = c[-1].timestamp + timedelta(hours=1, minutes=1)
    store = Store(tmp_path / "lab")
    one = poll_paper(store, now=now, fetch=lambda url: payload(c), timeframes=["1h"])
    again = poll_paper(store, now=now, fetch=lambda url: payload(c), timeframes=["1h"])
    assert one["candidates"] == 0 and one["new_public_candles"] == 70
    assert again["new_public_candles"] == 0 and again["processed_boundaries"] == 0
    assert store.db.execute("SELECT count(*) FROM paper_events").fetchone()[0] == 0
    bad = list(c)
    bad[0] = replace(c[0], close=c[0].close + D(".1"))
    with pytest.raises(ValueError, match="changed"):
        poll_paper(store, now=now, fetch=lambda url: payload(bad), timeframes=["1h"])
    store.close()


def test_actual_signal_receipt_precedes_fill_and_restart_does_not_duplicate_decisions(
    tmp_path,
):
    c = candles(200)
    s = Strategy()
    e = Execution()
    index = next(
        i
        for i in range(s.warmup, len(c))
        if decide(c[: i + 1], s, known_at=c[i].timestamp + timedelta(hours=1))
    )
    at = c[index].timestamp + timedelta(hours=1, minutes=1)
    store = Store(tmp_path / "lab")
    paper_schema(store)
    # A fixture-controller seed, never a claim this candidate passed real research.
    store.db.execute(
        "INSERT INTO promotions VALUES(?,?,?,?)",
        (
            s.fingerprint,
            canonical(
                {
                    "strategy": asdict(s),
                    "execution": asdict(e),
                    "decision_source_sha256": research_code_fingerprint(),
                }
            ),
            canonical({"SYNTHETIC_TEST_ONLY": True}),
            (at - timedelta(minutes=2)).isoformat(),
        ),
    )
    first = poll_paper(store, now=at, fetch=lambda url: payload(c[: index + 1]))
    assert first["processed_boundaries"] == 1
    state = json.loads(store.db.execute("SELECT state FROM paper_state").fetchone()[0])
    assert state["pending"] and state["filled"] == 0 and state["processed_bars"] == 1
    next_at = at + timedelta(hours=1)
    poll_paper(store, now=next_at, fetch=lambda url: payload(c[: index + 2]))
    state = json.loads(store.db.execute("SELECT state FROM paper_state").fetchone()[0])
    assert state["filled"] == 0, "cannot fill before actual signal receipt"
    store.close()
    store = Store(tmp_path / "lab")
    later = at + timedelta(hours=8)
    recovered = poll_paper(store, now=later, fetch=lambda url: payload(c[: index + 9]))
    assert recovered["missed_decisions_not_backfilled"] >= 1
    state = json.loads(store.db.execute("SELECT state FROM paper_state").fetchone()[0])
    if state["position"]:
        assert state["position"]["entry_at"] >= at.isoformat()
    events = [
        json.loads(r[0]) for r in store.db.execute("SELECT payload FROM paper_events")
    ]
    signals = [v for v in events if v["kind"] == "signal"]
    assert len(signals) == 1
    count = len(events)
    again = poll_paper(store, now=later, fetch=lambda url: payload(c[: index + 9]))
    assert (
        again["processed_boundaries"] == 0
        and store.db.execute("SELECT count(*) FROM paper_events").fetchone()[0] == count
    )
    assert store.status()["integrity"] == "ok"
    store.close()


def test_public_ingestion_rollback(tmp_path):
    c = candles(70)
    store = Store(tmp_path / "lab")
    now = c[-1].timestamp + timedelta(hours=1, minutes=1)

    def fail():
        raise RuntimeError("simulated database interruption")

    with pytest.raises(RuntimeError):
        poll_paper(
            store, now=now, fetch=lambda url: payload(c), timeframes=["1h"], fault=fail
        )
    assert store.db.execute("SELECT count(*) FROM lab_live_candles").fetchone()[0] == 0
    assert (
        poll_paper(store, now=now, fetch=lambda url: payload(c), timeframes=["1h"])[
            "new_public_candles"
        ]
        == 70
    )
    store.close()
