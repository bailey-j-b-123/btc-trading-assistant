import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import {
  setCandles,
  setFormingCandle,
  toChartCandles,
} from "../../src/trading_assistant/web/static/js/chart.js";
import {
  canShowForming,
  createFormingStream,
  currentBucketMs,
  FORMING_INTERVALS,
  FORMING_STALE_MS,
  parseFormingOhlc,
  parseKrakenTrades,
} from "../../src/trading_assistant/web/static/js/forming-display.js";

const NOW = Date.parse("2026-10-06T13:03:00Z");
const CONFIRMED = [[Date.parse("2026-10-06T12:00:00Z"), "100", "102", "99", "101", "5"]];
const makeOhlc = (timeframe, now = NOW, values = {}, type = "snapshot") => ({
  channel: "ohlc",
  type,
  timestamp: new Date(now).toISOString(),
  data: [{
    symbol: "BTC/USDT",
    interval: FORMING_INTERVALS[timeframe],
    interval_begin: new Date(currentBucketMs(timeframe, now)).toISOString(),
    open: 101,
    high: 105,
    low: 100,
    close: 104,
    volume: 2.5,
    trades: 4,
    ...values,
  }],
});
const makeTrade = (id, timestamp, price, qty = 0.25, extra = {}) => ({
  symbol: "BTC/USDT",
  trade_id: id,
  timestamp: new Date(timestamp).toISOString(),
  price,
  qty,
  side: "buy",
  ...extra,
});
const ack = (channel) => ({ method: "subscribe", success: true, result: { channel } });

class FakeSocket {
  static instances = [];
  constructor(url) {
    this.url = url;
    this.sent = [];
    this.closed = false;
    this.readyState = 0;
    FakeSocket.instances.push(this);
  }
  send(data) { this.sent.push(JSON.parse(data)); }
  open() { this.readyState = 1; this.onopen?.(); }
  close() {
    this.readyState = 3;
    this.closed = true;
    this.onclose?.();
  }
  emit(message) { this.onmessage?.({ data: JSON.stringify(message) }); }
  emitRaw(data) { this.onmessage?.({ data }); }
}

function makeHarness(timeframe = "1h", { Socket = FakeSocket } = {}) {
  FakeSocket.instances = [];
  let now = NOW;
  const candles = [];
  const prices = [];
  const statuses = [];
  const rollovers = [];
  const clockTicks = [];
  const retries = [];
  const cancelledRetries = [];
  const stoppedIntervals = [];
  const stream = createFormingStream({
    timeframe,
    Socket,
    now: () => now,
    onCandle: (candle) => candles.push(candle),
    onPrice: (price) => prices.push(price),
    onStatus: (status, detail) => statuses.push({ status, detail }),
    onRollover: (rollover) => rollovers.push(rollover),
    every: (fn) => { clockTicks.push(fn); return clockTicks.length; },
    stopEvery: (id) => stoppedIntervals.push(id),
    schedule: (fn, delay) => { retries.push({ fn, delay }); return retries.length; },
    cancel: (id) => cancelledRetries.push(id),
  });
  return {
    stream, candles, prices, statuses, rollovers, clockTicks, retries,
    cancelledRetries, stoppedIntervals,
    now: () => now,
    setNow(value) { now = value; },
    socket() { return Socket.instances?.at(-1) || null; },
  };
}

function startReady(h, timeframe = "1h", baselineTime = NOW) {
  h.stream.start();
  const socket = h.socket();
  socket.open();
  assert.equal(socket.url, "wss://ws.kraken.com/v2");
  assert.deepEqual(socket.sent, [
    { method: "subscribe", params: { channel: "trade", symbol: ["BTC/USDT"], snapshot: true } },
    { method: "subscribe", params: { channel: "ohlc", symbol: ["BTC/USDT"], interval: FORMING_INTERVALS[timeframe], snapshot: true } },
  ]);
  socket.emit(ack("trade"));
  socket.emit(ack("ohlc"));
  socket.emit(makeOhlc(timeframe, baselineTime));
  return socket;
}

function seedTradeSnapshot(socket, timestamp, id = 10, price = 104, qty = 0.5) {
  socket.emit({ channel: "trade", type: "snapshot", data: [makeTrade(id, timestamp, price, qty)] });
}

test("UTC bucket alignment and rollover use exact epoch-anchored 5M/15M/1H/4H boundaries", () => {
  assert.deepEqual(Object.fromEntries(Object.keys(FORMING_INTERVALS).map((timeframe) =>
    [timeframe, new Date(currentBucketMs(timeframe, NOW)).toISOString()])), {
    "5m": "2026-10-06T13:00:00.000Z",
    "15m": "2026-10-06T13:00:00.000Z",
    "1h": "2026-10-06T13:00:00.000Z",
    "4h": "2026-10-06T12:00:00.000Z",
  });
  assert.equal(currentBucketMs("5m", Date.parse("2026-10-06T13:05:00Z")), Date.parse("2026-10-06T13:05:00Z"));
  for (const timeframe of Object.keys(FORMING_INTERVALS)) {
    const open = currentBucketMs(timeframe, NOW);
    assert.deepEqual(parseFormingOhlc(makeOhlc(timeframe), timeframe, NOW), {
      time: open / 1000,
      open: 101,
      high: 105,
      low: 100,
      close: 104,
      volume: 2.5,
      trades: 4,
    });
    assert.equal(canShowForming(timeframe, open, NOW), false, "a same-bucket stored candle must not be overwritten");
    assert.equal(canShowForming(timeframe, open - FORMING_INTERVALS[timeframe] * 60000, NOW), true);
    assert.equal(canShowForming(timeframe, null, NOW), true, "a live bar can be displayed with no stored history");
  }
});

test("Kraken OHLC parser rejects wrong symbol, timeframe, bucket and malformed rows", () => {
  const previous = currentBucketMs("5m", NOW) - 300000;
  for (const message of [
    makeOhlc("5m", NOW, { symbol: "BTC/USD" }),
    makeOhlc("5m", NOW, { interval: 15 }),
    makeOhlc("5m", NOW, { interval_begin: new Date(previous).toISOString() }),
    makeOhlc("5m", NOW, { interval_begin: "2026-10-06T13:00:00" }),
    makeOhlc("5m", NOW, { high: 99 }),
    makeOhlc("5m", NOW, { close: "NaN" }),
    makeOhlc("5m", NOW, { volume: null }),
    makeOhlc("5m", NOW, { trades: -1 }),
    { channel: "ticker", type: "update", data: makeOhlc("5m").data },
  ]) {
    assert.equal(parseFormingOhlc(message, "5m", NOW), null);
  }
});

test("trade parser accepts only actual valid BTC/USDT Kraken trade rows", () => {
  const parsed = parseKrakenTrades({ channel: "trade", type: "update", data: [
    makeTrade(12, NOW + 200, "103.5", "0.125"),
    makeTrade(11, NOW + 100, 102, 0.25),
    makeTrade(13, NOW, 104, 0.25, { symbol: "ETH/USDT" }),
    makeTrade(14, NOW, "NaN", 0.25),
    makeTrade(15, NOW, 104, 0),
    { ...makeTrade(16, NOW, 104), trade_id: "not-an-id" },
    makeTrade(17, NOW + 10000, 104),
  ] }, NOW);
  assert.deepEqual(parsed.map((trade) => [trade.tradeId.toString(), trade.price, trade.quantity]), [
    ["11", 102, 0.25], ["12", 103.5, 0.125],
  ]);
});

test("live ticks update price and forming OHLC wick/volume without mutating confirmed rows", () => {
  let confirmed = null;
  let forming = null;
  let confirmedVolume = null;
  let formingVolume = null;
  const handle = {
    series: { setData: (data) => { confirmed = data; } },
    formingSeries: { setData: (data) => { forming = data; } },
    volume: { setData: (data) => { confirmedVolume = data; } },
    formingVolume: { setData: (data) => { formingVolume = data; } },
    formingTime: null,
  };
  setCandles(handle, CONFIRMED);
  const originalCandles = structuredClone(confirmed);
  const originalVolume = structuredClone(confirmedVolume);
  const h = makeHarness();
  const socket = startReady(h);
  const tickTime = NOW + 200;
  h.setNow(tickTime);
  seedTradeSnapshot(socket, tickTime);
  assert.equal(h.statuses.at(-1).status, "LIVE");
  assert.equal(h.prices.at(-1).price, 104);
  assert.equal(h.candles.at(-1).open, 101);
  assert.equal(h.candles.at(-1).close, 104);
  assert.equal(h.candles.at(-1).volume, 3);
  setFormingCandle(handle, h.candles.at(-1));
  assert.deepEqual(forming, [{ time: h.candles.at(-1).time, open: 101, high: 105, low: 100, close: 104 }]);
  assert.deepEqual(formingVolume, [{ time: h.candles.at(-1).time, value: 3, color: "rgba(47,191,127,0.72)" }]);

  const later = NOW + 400;
  h.setNow(later);
  socket.emit({ channel: "trade", type: "update", data: [makeTrade(11, later, 108, 0.2)] });
  assert.equal(h.prices.at(-1).price, 108);
  assert.equal(h.candles.at(-1).high, 108);
  assert.equal(h.candles.at(-1).close, 108);
  assert.equal(h.candles.at(-1).volume, 3.2);
  assert.equal(h.candles.at(-1).trades, 6);
  setFormingCandle(handle, h.candles.at(-1));
  assert.equal(forming[0].high, 108);
  assert.equal(formingVolume[0].value, 3.2);
  assert.deepEqual(confirmed, originalCandles);
  assert.deepEqual(confirmedVolume, originalVolume);

  // A full OHLC update already includes that tick. It repairs the baseline and
  // removes the buffered tick from the delta, so quantity/count are not double-added.
  socket.emit(makeOhlc("1h", later, { high: 108, close: 108, volume: 2.7, trades: 5 }, "update"));
  assert.equal(h.candles.at(-1).volume, 2.7);
  assert.equal(h.candles.at(-1).trades, 5);
  assert.deepEqual(confirmed, originalCandles);
  assert.deepEqual(confirmedVolume, originalVolume);
  h.stream.stop();
});

test("forming candle rolls to a new UTC bucket, clears the old ghost and waits for a new baseline", () => {
  const h = makeHarness("5m");
  const socket = startReady(h, "5m");
  h.setNow(NOW + 100);
  seedTradeSnapshot(socket, NOW + 100, 20);
  const first = h.candles.at(-1);
  assert.equal(first.time * 1000, Date.parse("2026-10-06T13:00:00Z"));
  h.setNow(Date.parse("2026-10-06T13:05:00Z"));
  h.clockTicks[0]();
  assert.equal(h.candles.at(-1), null);
  assert.equal(h.rollovers.length, 1);
  assert.equal(h.rollovers[0].bucketOpenMs, Date.parse("2026-10-06T13:05:00Z"));
  socket.emit(makeOhlc("5m", h.now(), { interval_begin: "2026-10-06T13:00:00Z" }, "update"));
  assert.equal(h.candles.at(-1), null, "old bucket updates never survive the boundary");
  socket.emit(makeOhlc("5m", h.now(), { interval_begin: "2026-10-06T13:05:00Z", open: 105, high: 106, low: 104, close: 105, volume: 0.4, trades: 1 }));
  h.setNow(h.now() + 1000);
  socket.emit({ channel: "trade", type: "update", data: [makeTrade(21, h.now(), 106, 0.1)] });
  assert.equal(h.candles.at(-1).time * 1000, Date.parse("2026-10-06T13:05:00Z"));
  assert.equal(h.candles.at(-1).open, 105);
  assert.equal(h.candles.at(-1).high, 106);
  assert.equal(h.candles.at(-1).close, 106);
  h.stream.stop();
});

test("stale trade state is explicit even while the WebSocket transport remains open", () => {
  const h = makeHarness();
  const socket = startReady(h);
  seedTradeSnapshot(socket, NOW + 100);
  assert.equal(h.statuses.at(-1).status, "LIVE");
  h.setNow(NOW + FORMING_STALE_MS + 200);
  h.clockTicks[0]();
  assert.equal(h.statuses.at(-1).status, "STALE");
  assert.match(h.statuses.at(-1).detail.reason, /old|recent/i);
  assert.equal(h.candles.at(-1).close, 104, "stale real candle values are retained only as stale display data");
  h.stream.stop();
});

test("disconnect reconnects with bounded backoff and a fresh pair of subscriptions", () => {
  const h = makeHarness();
  const socket = startReady(h);
  seedTradeSnapshot(socket, NOW + 100);
  assert.equal(h.statuses.at(-1).status, "LIVE");
  socket.close();
  assert.equal(h.statuses.at(-1).status, "DISCONNECTED");
  assert.equal(h.candles.at(-1), null);
  assert.equal(h.retries[0].delay, 1000);
  h.retries[0].fn();
  const reconnected = h.socket();
  assert.notEqual(reconnected, socket);
  reconnected.open();
  assert.equal(reconnected.sent.length, 2);
  reconnected.emit(ack("trade"));
  reconnected.emit(ack("ohlc"));
  reconnected.emit(makeOhlc("1h", NOW + 5000));
  h.setNow(NOW + 5100);
  seedTradeSnapshot(reconnected, h.now(), 40, 110, 0.1);
  assert.equal(h.statuses.at(-1).status, "LIVE");
  assert.equal(h.prices.at(-1).price, 110);
  reconnected.close();
  assert.ok(h.retries.every(({ delay }) => delay <= 30000));
  h.stream.stop();
});

test("trade sequence gaps force a stale/disconnected resync rather than silently omitting ticks", () => {
  const h = makeHarness();
  const socket = startReady(h);
  seedTradeSnapshot(socket, NOW + 100, 50);
  const priceCount = h.prices.length;
  socket.emit({ channel: "trade", type: "update", data: [makeTrade(52, NOW + 200, 120)] });
  assert.equal(h.prices.length, priceCount, "a tick after a detected gap is not presented as complete live data");
  assert.equal(h.statuses.at(-1).status, "DISCONNECTED");
  assert.match(h.statuses.at(-1).detail.reason, /sequence gap/);
  assert.equal(h.candles.at(-1), null);
  assert.equal(h.retries[0].delay, 1000);
  h.stream.stop();
});

test("subscription errors and malformed socket messages disconnect and retry", () => {
  const h = makeHarness();
  h.stream.start();
  const socket = h.socket();
  socket.open();
  socket.emit({ method: "subscribe", success: false, result: { channel: "trade" } });
  assert.equal(h.statuses.at(-1).status, "DISCONNECTED");
  assert.equal(h.retries.length, 1);
  h.stream.stop();
});

test("stop cancels timers, closes the socket and prevents callbacks after cleanup", () => {
  const h = makeHarness();
  const socket = startReady(h);
  seedTradeSnapshot(socket, NOW + 100);
  socket.close(); // creates a scheduled reconnect
  const priorStatuses = h.statuses.length;
  const priorCandles = h.candles.length;
  h.stream.stop();
  assert.equal(h.cancelledRetries.length, 1);
  assert.deepEqual(h.stoppedIntervals, [1]);
  h.retries[0].fn(); // canceled callback must not open another connection
  assert.equal(FakeSocket.instances.length, 1);
  socket.emit(makeOhlc("1h", NOW + 1000));
  assert.equal(h.statuses.length, priorStatuses);
  assert.equal(h.candles.length, priorCandles);
});

test("forming module has no API or persistence path into BRAIN decisions", () => {
  const source = readFileSync(new URL("../../src/trading_assistant/web/static/js/forming-display.js", import.meta.url), "utf8");
  for (const forbidden of ["/api/dashboard", "/api/market/candles", "setCandles(", "fetch(", "localStorage", "indexedDB", "WebSocketAuth"]) {
    assert.ok(!source.includes(forbidden), forbidden);
  }
});

test("closed bars remain empty without stored payload and forming timestamps never promote on stop", () => {
  let confirmed = null;
  let ghost = null;
  const handle = { series: { setData: (rows) => { confirmed = rows; } }, formingSeries: { setData: (rows) => { ghost = rows; } }, volume: { setData() {} }, formingVolume: { setData() {} }, formingTime: null };
  setCandles(handle, []);
  const original = structuredClone(confirmed);
  const h = makeHarness("15m");
  const socket = startReady(h, "15m");
  h.setNow(NOW + 100);
  seedTradeSnapshot(socket, h.now(), 5, 104);
  setFormingCandle(handle, h.candles.at(-1));
  assert.equal(ghost.length, 1);
  h.stream.stop();
  setFormingCandle(handle, null);
  assert.deepEqual(ghost, []);
  assert.deepEqual(confirmed, original);
  assert.equal(toChartCandles(CONFIRMED).length, 1, "only stored rows feed the confirmed mapping");
});
