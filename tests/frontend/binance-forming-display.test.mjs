import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { setCandles, setFormingCandle, toChartCandles } from "../../src/trading_assistant/web/static/js/chart.js";
import { canShowForming, currentBucketMs, FORMING_INTERVALS,
  FORMING_STALE_MS } from "../../src/trading_assistant/web/static/js/forming-display.js";
import { BINANCE_INTERVALS, BINANCE_PUBLIC_WS, binanceKlineStreamUrl, binanceStreamSymbol,
  createBinanceFormingStream, parseFormingKline } from "../../src/trading_assistant/web/static/js/binance-forming-display.js";

const NOW = Date.parse("2026-10-06T13:03:00Z");
const CONFIRMED = [[Date.parse("2026-10-06T12:00:00Z"), "100", "102", "99", "101", "5"]];

/** A raw Binance spot kline event; `x: false` means the kline is still forming. */
const kline = (timeframe, now = NOW, values = {}, klineValues = {}) => ({
  e: "kline", E: now, s: "BTCUSDT",
  k: {
    t: currentBucketMs(timeframe, now), T: currentBucketMs(timeframe, now) + FORMING_INTERVALS[timeframe] * 60000 - 1,
    s: "BTCUSDT", i: BINANCE_INTERVALS[timeframe], f: 100, L: 109,
    o: "101", h: "105", l: "100", c: "104", v: "2.5", n: 4, x: false,
    q: "260", V: "1.25", Q: "130", B: "0",
    ...klineValues,
  },
  ...values,
});

test("stream symbol and URL follow the Binance raw-stream path subscription", () => {
  assert.equal(binanceStreamSymbol("BTC/USDT"), "BTCUSDT");
  assert.equal(binanceStreamSymbol("btc/usdt"), "BTCUSDT");
  assert.equal(binanceKlineStreamUrl("BTC/USDT", "5m"),
    "wss://stream.binance.com:9443/ws/btcusdt@kline_5m");
  assert.equal(binanceKlineStreamUrl("BTC/USDT", "4h"),
    "wss://stream.binance.com:9443/ws/btcusdt@kline_4h");
  assert.equal(binanceKlineStreamUrl("BTC/USDT", "1d"), null, "unsupported interval has no stream");
  assert.equal(binanceKlineStreamUrl("", "5m"), null);
  assert.ok(BINANCE_PUBLIC_WS.startsWith("wss://stream.binance.com"));
  assert.deepEqual(Object.keys(BINANCE_INTERVALS), ["5m", "15m", "1h", "4h"]);
});

test("a forming Binance kline parses for every hierarchy timeframe with shared UTC buckets", () => {
  assert.deepEqual(Object.fromEntries(Object.keys(BINANCE_INTERVALS).map((tf) =>
    [tf, new Date(currentBucketMs(tf, NOW)).toISOString()])), {
    "5m": "2026-10-06T13:00:00.000Z", "15m": "2026-10-06T13:00:00.000Z",
    "1h": "2026-10-06T13:00:00.000Z", "4h": "2026-10-06T12:00:00.000Z",
  });
  for (const tf of Object.keys(BINANCE_INTERVALS)) {
    const begin = currentBucketMs(tf, NOW);
    const result = parseFormingKline(kline(tf), tf, NOW, begin - FORMING_INTERVALS[tf] * 60000);
    assert.deepEqual(result, { time: begin / 1000, open: 101, high: 105, low: 100,
      close: 104, volume: 2.5, trades: 4 });
    assert.equal(canShowForming(tf, begin, NOW), false, "stored same-bucket row blocks live duplicate");
    assert.equal(canShowForming(tf, begin - 3 * FORMING_INTERVALS[tf] * 60000, NOW), false,
      "do not conflate old/historical stored charts with live current bucket");
  }
});

test("final klines, wrong symbol/interval/bucket and invalid OHLC never yield a ghost candle", () => {
  const previous = currentBucketMs("5m", NOW) - 300000;
  for (const message of [
    kline("5m", NOW, {}, { x: true }), // FINAL kline is closed history, never the ghost
    kline("5m", NOW, { s: "ETHUSDT" }),
    kline("5m", NOW, {}, { s: "ETHUSDT" }),
    kline("5m", NOW, {}, { i: "15m" }),
    kline("5m", NOW, {}, { t: previous }),
    kline("5m", NOW, {}, { t: NOW + 300000 }),
    kline("5m", NOW, {}, { T: currentBucketMs("5m", NOW) + 60000 - 1 }),
    kline("5m", NOW, {}, { t: "1791285600000" }),
    kline("5m", NOW, {}, { h: "99" }), // high < low
    kline("5m", NOW, {}, { o: "0" }), // non-positive open
    kline("5m", NOW, {}, { c: "NaN" }),
    kline("5m", NOW, {}, { v: "-1" }),
    kline("5m", NOW, {}, { v: null }),
    kline("5m", NOW, {}, { n: -1 }),
    kline("5m", NOW, {}, { n: 1.5 }),
    kline("5m", NOW, { E: NOW - FORMING_STALE_MS }), // stale event
    kline("5m", NOW, { E: NOW + 6000 }), // future event
    kline("5m", NOW, { E: "now" }),
    { ...kline("5m"), e: "trade" },
    { ...kline("5m"), k: null },
    { e: "kline", E: NOW, s: "BTCUSDT" },
  ]) {
    assert.equal(parseFormingKline(message, "5m", NOW, previous), null);
  }
  assert.equal(parseFormingKline(kline("5m"), "5m", NOW, currentBucketMs("5m", NOW)), null,
    "a stored candle in the current bucket is never duplicated by the ghost");
});

test("two independent chart series: live kline can change but stored confirmed history cannot", () => {
  let confirmed = null;
  let forming = null;
  const handle = {
    series: { setData: (data) => { confirmed = data; } },
    formingSeries: { setData: (data) => { forming = data; } },
    volume: { setData() {} },
  };
  setCandles(handle, CONFIRMED);
  const original = structuredClone(confirmed);
  const first = parseFormingKline(kline("1h"), "1h", NOW, CONFIRMED[0][0]);
  setFormingCandle(handle, first);
  assert.deepEqual(forming, [{ time: first.time, open: 101, high: 105, low: 100, close: 104 }]);
  setFormingCandle(handle, { ...first, close: 103 });
  assert.equal(forming[0].close, 103);
  assert.deepEqual(confirmed, original);
  assert.deepEqual(confirmed, toChartCandles(CONFIRMED));
  setFormingCandle(handle, null);
  assert.deepEqual(forming, []);
  assert.deepEqual(confirmed, original);
  // Only an explicit stored-candle payload can add a confirmed close after the
  // normal ingestion process; the ghost never calls this setter itself.
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
  const stream = createBinanceFormingStream({
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
  assert.equal(h.socket().url, "wss://stream.binance.com:9443/ws/btcusdt@kline_1h");
  assert.deepEqual(h.socket().sent, [], "a raw stream subscribes through its URL, nothing is sent");
  h.socket().emit(kline("1h"));
  h.socket().emit(kline("1h", NOW, {}, { c: "105", v: "3", n: 5 }));
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

test("a final kline event removes the ghost: closed history is never drawn from this socket", () => {
  const h = harness("15m");
  h.stream.start();
  h.socket().emit(kline("15m"));
  assert.equal(h.statuses.at(-1), "CURRENT");
  // The same bucket arrives with x: true (the kline just closed).
  h.socket().emit(kline("15m", NOW, {}, { x: true }));
  assert.equal(h.draws.at(-1), null);
  assert.equal(h.statuses.at(-1), "STALE");
  // A late forming event for the previous bucket cannot resurrect it either.
  h.socket().emit(kline("15m", NOW, {}, { t: currentBucketMs("15m", NOW) - 900000, x: false }));
  assert.equal(h.draws.at(-1), null);
  h.stream.stop();
});

test("stale, malformed and disconnected streams remove the ghost; retry is bounded", () => {
  const h = harness("5m");
  h.stream.start();
  h.socket().emit(kline("5m"));
  h.setNow(NOW + FORMING_STALE_MS + 1);
  h.ticks[0]();
  assert.equal(h.draws.at(-1), null);
  assert.equal(h.statuses.at(-1), "STALE");
  h.socket().emit(kline("5m", NOW, {}, { l: "106" }));
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

test("disconnect before a first valid Binance kline is unavailable, not a guessed price", () => {
  const h = harness("15m");
  h.stream.start();
  h.socket().close();
  assert.equal(h.statuses.at(-1), "UNAVAILABLE");
  assert.equal(h.draws.at(-1), null);
  assert.equal(h.retries.length, 1);
  h.stream.stop();
});

test("forming module has no API or persistence path into engine decisions", () => {
  const source = readFileSync(new URL("../../src/trading_assistant/web/static/js/binance-forming-display.js", import.meta.url), "utf8");
  for (const forbidden of ["/api/dashboard", "/api/market/candles", "setCandles(", "fetch(", "localStorage", "indexedDB", "WebSocketAuth"]) {
    assert.ok(!source.includes(forbidden), forbidden);
  }
});
