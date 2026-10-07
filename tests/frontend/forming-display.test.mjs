import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { setCandles, setFormingCandle, toChartCandles } from "../../src/trading_assistant/web/static/js/chart.js";
import { canShowForming, createFormingStream, currentBucketMs, FORMING_INTERVALS,
  parseFormingOhlc, FORMING_STALE_MS } from "../../src/trading_assistant/web/static/js/forming-display.js";

const NOW = Date.parse("2026-10-06T13:03:00Z");
const CONFIRMED = [[Date.parse("2026-10-06T12:00:00Z"), "100", "102", "99", "101", "5"]];
const ohlc = (timeframe, now = NOW, values = {}) => ({
  channel: "ohlc", type: "update", timestamp: new Date(now).toISOString(), data: [{
    symbol: "BTC/USDT", interval: FORMING_INTERVALS[timeframe],
    interval_begin: new Date(currentBucketMs(timeframe, now)).toISOString(),
    open: 101, high: 105, low: 100, close: 104, volume: 2.5, trades: 4,
    ...values,
  }],
});

test("UTC bucket alignment matches fixed epoch-anchored 5M/15M/1H/4H intervals", () => {
  assert.deepEqual(Object.fromEntries(Object.keys(FORMING_INTERVALS).map((tf) =>
    [tf, new Date(currentBucketMs(tf, NOW)).toISOString()])), {
    "5m": "2026-10-06T13:00:00.000Z", "15m": "2026-10-06T13:00:00.000Z",
    "1h": "2026-10-06T13:00:00.000Z", "4h": "2026-10-06T12:00:00.000Z",
  });
  for (const tf of Object.keys(FORMING_INTERVALS)) {
    const begin = currentBucketMs(tf, NOW);
    const result = parseFormingOhlc(ohlc(tf), tf, NOW, begin - FORMING_INTERVALS[tf] * 60000);
    assert.deepEqual(result, { time: begin / 1000, open: 101, high: 105, low: 100,
      close: 104, volume: 2.5, trades: 4 });
    assert.equal(canShowForming(tf, begin, NOW), false, "stored same-bucket row blocks live duplicate");
    assert.equal(canShowForming(tf, begin - 3 * FORMING_INTERVALS[tf] * 60000, NOW), false,
      "do not conflate old/historical stored charts with live current bucket");
  }
});

test("wrong symbol, timeframe, old/future/malformed OHLC never yields a candle", () => {
  const previous = currentBucketMs("5m", NOW) - 300000;
  for (const message of [
    ohlc("5m", NOW, { symbol: "BTC/USD" }),
    ohlc("5m", NOW, { interval: 15 }),
    ohlc("5m", NOW, { interval_begin: new Date(previous).toISOString() }),
    ohlc("5m", NOW, { interval_begin: new Date(NOW + 300000).toISOString() }),
    ohlc("5m", NOW, { interval_begin: "2026-10-06T13:00:00" }),
    ohlc("5m", NOW, { high: 99 }), ohlc("5m", NOW, { close: "NaN" }),
    ohlc("5m", NOW, { volume: null }), ohlc("5m", NOW, { trades: -1 }),
    { ...ohlc("5m"), timestamp: new Date(NOW - 60000).toISOString() },
    { channel: "ticker", type: "update", data: ohlc("5m").data },
  ]) {
    assert.equal(parseFormingOhlc(message, "5m", NOW, previous), null);
  }
  assert.equal(parseFormingOhlc(ohlc("5m"), "5m", NOW, currentBucketMs("5m", NOW)), null);
});

test("two independent chart series: live OHLC can change but stored confirmed history cannot", () => {
  let confirmed = null;
  let forming = null;
  const handle = {
    series: { setData: (data) => { confirmed = data; } },
    formingSeries: { setData: (data) => { forming = data; } },
    volume: { setData() {} },
  };
  setCandles(handle, CONFIRMED);
  const original = structuredClone(confirmed);
  const first = parseFormingOhlc(ohlc("1h"), "1h", NOW, CONFIRMED[0][0]);
  setFormingCandle(handle, first);
  assert.deepEqual(forming, [{ time: first.time, open: 101, high: 105, low: 100, close: 104 }]);
  setFormingCandle(handle, { ...first, close: 103 });
  assert.equal(forming[0].close, 103);
  assert.deepEqual(confirmed, original);
  assert.deepEqual(confirmed, toChartCandles(CONFIRMED));
  setFormingCandle(handle, null);
  assert.deepEqual(forming, []);
  assert.deepEqual(confirmed, original);
  // Only an explicit stored-candle payload can add a confirmed close after
  // the normal ingestion process; the ghost never calls this setter itself.
  const newlyStored = [...CONFIRMED,
    [Date.parse("2026-10-06T13:00:00Z"), "101", "105", "100", "104", "7"]];
  setCandles(handle, newlyStored);
  assert.deepEqual(confirmed, toChartCandles(newlyStored));
  assert.deepEqual(forming, []);
});

class Socket {
  static instances = [];
  constructor(url) { this.url = url; this.sent = []; this.closed = false; Socket.instances.push(this); }
  send(data) { this.sent.push(JSON.parse(data)); }
  close() { this.closed = true; this.onclose?.(); }
  emit(message) { this.onmessage?.({ data: JSON.stringify(message) }); }
}

function harness(timeframe = "1h") {
  Socket.instances = [];
  let now = NOW;
  const draws = [], statuses = [], ticks = [], retries = [];
  const stream = createFormingStream({
    timeframe, confirmedOpenMs: currentBucketMs(timeframe, NOW) - FORMING_INTERVALS[timeframe] * 60000,
    Socket, now: () => now,
    onCandle: (c) => draws.push(c), onStatus: (status) => statuses.push(status),
    every: (fn) => { ticks.push(fn); return 1; }, stopEvery() {},
    schedule: (fn, delay) => { retries.push({ fn, delay }); return retries.length; }, cancel() {},
  });
  return { stream, draws, statuses, ticks, retries, setNow(value) { now = value; },
    socket() { return Socket.instances.at(-1); } };
}

test("one public stream per view; updates vary the ghost, boundary never promotes it", () => {
  const h = harness();
  h.stream.start();
  assert.equal(h.socket().url, "wss://ws.kraken.com/v2");
  h.socket().onopen();
  assert.deepEqual(h.socket().sent[0], { method: "subscribe", params: {
    channel: "ohlc", symbol: ["BTC/USDT"], interval: 60, snapshot: true,
  } });
  h.socket().emit(ohlc("1h"));
  h.socket().emit(ohlc("1h", NOW, { close: 105, volume: 3, trades: 5 }));
  assert.equal(h.draws.at(-1).close, 105);
  assert.equal(h.statuses.at(-1), "CURRENT");
  h.setNow(Date.parse("2026-10-06T14:00:00Z"));
  h.ticks[0]();
  assert.equal(h.draws.at(-1), null);
  assert.equal(h.statuses.at(-1), "STALE");
  assert.equal(h.draws.filter(Boolean).length, 2); // none was promoted at close
  h.stream.stop();
  assert.equal(h.socket().closed, true);
});

test("stale, malformed and disconnected streams remove the ghost; retry is bounded", () => {
  const h = harness("5m");
  h.stream.start();
  h.socket().onopen();
  h.socket().emit(ohlc("5m"));
  h.setNow(NOW + FORMING_STALE_MS + 1); // same bucket is impossible for 5m? 13:03 +45s remains 13:03:45
  h.ticks[0]();
  assert.equal(h.draws.at(-1), null);
  assert.equal(h.statuses.at(-1), "STALE");
  h.socket().emit(ohlc("5m", NOW, { low: 106 }));
  assert.equal(h.draws.at(-1), null);
  h.socket().close();
  assert.equal(h.retries[0].delay, 1000);
  for (let i = 0; i < 7; i++) {
    const retry = h.retries.at(-1);
    retry.fn();
    h.socket().close();
  }
  assert.ok(h.retries.every(({ delay }) => delay <= 30000));
  h.stream.stop();
});

test("forming module has no API or persistence path into engine decisions", () => {
  const source = readFileSync(new URL("../../src/trading_assistant/web/static/js/forming-display.js", import.meta.url), "utf8");
  for (const forbidden of ["/api/dashboard", "/api/market/candles", "setCandles(", "fetch(", "localStorage", "indexedDB", "WebSocketAuth"]) {
    assert.ok(!source.includes(forbidden), forbidden);
  }
});


test("disconnect before a first valid Kraken candle is unavailable, not a guessed price", () => {
  const h = harness("15m");
  h.stream.start();
  h.socket().close();
  assert.equal(h.statuses.at(-1), "UNAVAILABLE");
  assert.equal(h.draws.at(-1), null);
  assert.equal(h.retries.length, 1);
  h.stream.stop();
});
