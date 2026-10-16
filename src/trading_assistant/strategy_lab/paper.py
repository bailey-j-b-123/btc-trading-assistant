"""Independent forward-paper observation. Fixed public GETs; no order interface."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from hashlib import sha256
import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from trading_assistant.market_data.validation import validate_ohlcv_rows
from trading_assistant.market_data.timeframes import timeframe_to_milliseconds
from .campaign import schema
from .models import (
    Strategy,
    Execution,
    canonical,
    identity,
    decimal_context,
    research_code_fingerprint,
)
from .simulation import initial_state, step
from .store import exclusive


class FeedNotCurrent(ValueError):
    """Public closed data is delayed; retry without inventing a current bar."""


def public_closed(timeframe, *, now, fetch=None):
    if timeframe not in {"1h", "4h"}:
        raise ValueError("forward paper supports verified1h/4h families only")
    url = "https://api.binance.com/api/v3/klines?" + urlencode(
        {"symbol": "BTCUSDT", "interval": timeframe, "limit": 1000}
    )
    if fetch is None:
        with urlopen(
            Request(url, headers={"User-Agent": "BRAIN-strategy-lab-public-paper/1"}),
            timeout=15,
        ) as response:
            if not response.url.startswith("https://api.binance.com/"):
                raise ValueError("unexpected public-feed redirect")
            raw = response.read(1024 * 1024 + 1)
    else:
        raw = fetch(url)
    if len(raw) > 1024 * 1024:
        raise ValueError("oversize public candle response")
    rows = json.loads(raw)
    interval = timeframe_to_milliseconds(timeframe)
    boundary = int(now.timestamp() * 1000) // interval * interval
    if not isinstance(rows, list) or len(rows) > 1000:
        raise ValueError("malformed public feed")
    for row in rows:
        if (
            not isinstance(row, list)
            or len(row) != 12
            or type(row[0]) is not int
            or row[6] != row[0] + interval - 1
        ):
            raise ValueError("public candle open/close interval mismatch")
    report = validate_ohlcv_rows(
        rows,
        exchange="binance",
        symbol="BTC/USDT",
        timeframe=timeframe,
        closed_through=datetime.fromtimestamp((boundary - interval) / 1000, tz=UTC),
    )
    if not report.is_valid:
        raise ValueError("public feed is invalid")
    if (
        not report.candles
        or int(report.candles[-1].timestamp.timestamp() * 1000) + interval != boundary
    ):
        raise FeedNotCurrent("public feed is not current through its latest close")
    if any(
        b.timestamp - a.timestamp != timedelta(milliseconds=interval)
        for a, b in zip(report.candles, report.candles[1:])
    ):
        raise ValueError("public feed has gaps")
    return report.candles, {
        "source_url": url,
        "sha256": sha256(raw).hexdigest(),
        "observed_at": now.isoformat(),
        "latest_closed_open": report.candles[-1].timestamp.isoformat(),
        "count": len(report.candles),
        "timeframe": timeframe,
    }


def paper_schema(store):
    schema(store)
    store.db.executescript("""
      CREATE TABLE IF NOT EXISTS lab_live_candles(timeframe TEXT NOT NULL,opened TEXT NOT NULL,
        payload TEXT NOT NULL,PRIMARY KEY(timeframe,opened));
      CREATE TABLE IF NOT EXISTS public_feed_receipts(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS paper_state(candidate TEXT PRIMARY KEY REFERENCES promotions(candidate),state TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS paper_events(candidate TEXT NOT NULL REFERENCES promotions(candidate),id TEXT NOT NULL,
        kind TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(candidate,id));
    """)


def strategy_from_json(raw):
    values = dict(raw)
    for key in ("stop_atr", "reward_r", "rsi_low"):
        values[key] = D(values[key])
    return Strategy(**values)


def execution_from_json(raw):
    values = dict(raw)
    for key in Execution.__dataclass_fields__:
        if key not in {"max_positions", "sizing", "ambiguous_policy"}:
            values[key] = D(values[key])
    return Execution(**values)


@exclusive
@decimal_context
def poll_paper(store, *, now=None, fetch=None, timeframes=None, fault=None):
    real_clock = now is None
    now = now or datetime.now(UTC)
    if now.utcoffset() is None or now.utcoffset().total_seconds() != 0:
        raise ValueError("UTC observation instant required")
    paper_schema(store)
    promotions = [
        dict(r) for r in store.db.execute("SELECT * FROM promotions ORDER BY candidate")
    ]
    if any("directional_execution" in json.loads(p["definition"]) for p in promotions):
        raise ValueError("directional experiments are research-only; no forward Spot promotion")
    scopes = set(
        timeframes
        or [json.loads(p["definition"])["strategy"]["timeframe"] for p in promotions]
    )
    # Explicit scope allows public-feed connectivity verification with0candidates.
    receipts = []
    new_rows = 0
    completed = 0
    missed = 0
    for tf in sorted(scopes):
        candles, receipt = public_closed(tf, now=now, fetch=fetch)
        observed = datetime.now(UTC) if real_clock else now
        interval_ms = timeframe_to_milliseconds(tf)
        if (
            int(candles[-1].timestamp.timestamp() * 1000) + interval_ms
            != int(observed.timestamp() * 1000) // interval_ms * interval_ms
        ):
            raise FeedNotCurrent(
                "public request crossed a close boundary; retry current data"
            )
        receipt["observed_at"] = observed.isoformat()
        store.db.execute("BEGIN IMMEDIATE")
        try:
            for c in candles:
                payload = canonical(c)
                old = store.db.execute(
                    "SELECT payload FROM lab_live_candles WHERE timeframe=? AND opened=?",
                    (tf, c.timestamp.isoformat()),
                ).fetchone()
                if old and old[0] != payload:
                    raise ValueError("a previously recorded live candle changed")
                if not old:
                    store.db.execute(
                        "INSERT INTO lab_live_candles VALUES(?,?,?)",
                        (tf, c.timestamp.isoformat(), payload),
                    )
                    new_rows += 1
            store.db.execute(
                "INSERT OR IGNORE INTO public_feed_receipts VALUES(?,?)",
                (identity(receipt), canonical(receipt)),
            )
            if fault:
                fault()
            store.db.execute("COMMIT")
        except BaseException:
            store.db.execute("ROLLBACK")
            raise
        receipts.append(receipt)
        for promotion in promotions:
            definition = json.loads(promotion["definition"])
            if definition.get("decision_source_sha256") != research_code_fingerprint():
                raise ValueError(
                    "promoted source no longer matches frozen research; paper rules cannot silently change"
                )
            s = strategy_from_json(definition["strategy"])
            if s.timeframe != tf:
                continue
            e = execution_from_json(definition["execution"])
            row = store.db.execute(
                "SELECT state FROM paper_state WHERE candidate=?",
                (promotion["candidate"],),
            ).fetchone()
            state = json.loads(row[0]) if row else initial_state(e)
            promoted = datetime.fromisoformat(promotion["promoted_at"])
            interval = timedelta(milliseconds=timeframe_to_milliseconds(tf))
            events = []
            for i, c in enumerate(candles):
                if (
                    c.timestamp + interval < promoted
                    or state["last_timestamp"]
                    and c.timestamp <= datetime.fromisoformat(state["last_timestamp"])
                ):
                    continue
                latest = i == len(candles) - 1
                if not latest:
                    missed += 1
                state, produced = step(
                    state, candles, i, s, e, signal_enabled=latest, recorded_at=observed
                )
                recorded = datetime.now(UTC) if real_clock else observed
                if state["pending"] and any(
                    event["kind"] == "signal" for event in produced
                ):
                    state["pending"]["not_before"] = recorded.isoformat()
                    state["pending"]["actual_decision_time"] = recorded.isoformat()
                if not latest:
                    produced.append(
                        {
                            "kind": "missed_decision",
                            "reason": "downtime_no_backdated_signal",
                            "candle_open": c.timestamp.isoformat(),
                        }
                    )
                for event in produced:
                    event.update(
                        actual_recorded_at=recorded.isoformat(),
                        strategy_fingerprint=s.fingerprint,
                        source_sha256=receipt["sha256"],
                        mode="forward_paper_only",
                        decision_source_sha256=research_code_fingerprint(),
                    )
                    if event["kind"] == "signal":
                        event["actual_decision_time"] = recorded.isoformat()
                events.extend(produced)
                completed += 1
            store.db.execute("BEGIN IMMEDIATE")
            try:
                for event in events:
                    store.db.execute(
                        "INSERT OR IGNORE INTO paper_events VALUES(?,?,?,?)",
                        (
                            promotion["candidate"],
                            identity(event),
                            event["kind"],
                            canonical(event),
                        ),
                    )
                if fault:
                    fault()
                store.db.execute(
                    "INSERT INTO paper_state VALUES(?,?) ON CONFLICT(candidate) DO UPDATE SET state=excluded.state",
                    (promotion["candidate"], canonical(state)),
                )
                store.db.execute("COMMIT")
            except BaseException:
                store.db.execute("ROLLBACK")
                raise
    return {
        "mode": "public_forward_paper_only",
        "candidates": len(promotions),
        "processed_boundaries": completed,
        "missed_decisions_not_backfilled": missed,
        "new_public_candles": new_rows,
        "feed_receipts": receipts,
        "no_live_profitability_claim": True,
    }
