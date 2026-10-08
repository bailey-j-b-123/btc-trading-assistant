import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { currentBucketMs, FORMING_STALE_MS } from "../../src/trading_assistant/web/static/js/forming-display.js";
import { BINANCE_PUBLIC_WS } from "../../src/trading_assistant/web/static/js/binance-forming-display.js";
import { KRAKEN_PUBLIC_WS } from "../../src/trading_assistant/web/static/js/forming-display.js";
import { canShowForming, createFormingStreamForExchange, marketDataProvider,
  MARKET_DATA_PROVIDERS, providerLabel, supportsFormingCandle } from "../../src/trading_assistant/web/static/js/market-data-provider.js";

const NOW = Date.parse("2026-10-06T13:03:00Z");

class Socket {
  static instances = [];
  constructor(url) { this.url = url; this.sent = []; this.closed = false; Socket.instances.push(this); }
  send(data) { this.sent.push(JSON.parse(data)); }
  close() { this.closed = true; this.onclose?.(); }
  emit(message) { this.onmessage?.({ data: JSON.stringify(message) }); }
}

function streamOptions(timeframe = "1h") {
  return {
    timeframe,
    confirmedOpenMs: currentBucketMs(timeframe, NOW) - 60 * 60000,
    Socket,
    now: () => NOW,
    onCandle() {}, onStatus() {},
    every: () => 1, stopEvery() {}, schedule: () => 1, cancel() {},
  };
}

test("provider registry keeps exchange identities separate", () => {
  assert.equal(marketDataProvider("kraken").id, "kraken");
  assert.equal(marketDataProvider("binance").id, "binance");
  assert.equal(marketDataProvider(" BINANCE ").id, "binance", "lookup is trimmed/lowercased");
  assert.equal(marketDataProvider("coinbase"), null);
  assert.equal(marketDataProvider(""), null);
  assert.equal(marketDataProvider(null), null);
  assert.equal(marketDataProvider(undefined), null);
  assert.deepEqual(Object.keys(MARKET_DATA_PROVIDERS), ["kraken", "binance"]);
  assert.equal(providerLabel("kraken"), "KRAKEN OHLC");
  assert.equal(providerLabel("binance"), "BINANCE KLINE");
  assert.equal(providerLabel("coinbase"), "PUBLIC MARKET DATA");
});

test("every hierarchy timeframe is covered by both venues", () => {
  for (const exchange of ["kraken", "binance"]) {
    for (const timeframe of ["5m", "15m", "1h", "4h"]) {
      assert.equal(supportsFormingCandle(exchange, timeframe), true, `${exchange} ${timeframe}`);
    }
    assert.equal(supportsFormingCandle(exchange, "1d"), false);
  }
  assert.equal(supportsFormingCandle("coinbase", "1h"), false);
});

test("kraken provider streams the Kraken public OHLC socket with a subscribe message", () => {
  Socket.instances = [];
  const stream = createFormingStreamForExchange("kraken", streamOptions("1h"));
  assert.ok(stream);
  stream.start();
  const socket = Socket.instances.at(-1);
  assert.equal(socket.url, KRAKEN_PUBLIC_WS);
  socket.onopen();
  assert.deepEqual(socket.sent[0], { method: "subscribe", params: {
    channel: "ohlc", symbol: ["BTC/USDT"], interval: 60, snapshot: true,
  } });
  stream.stop();
  assert.equal(socket.closed, true);
});

test("binance provider streams the Binance public kline socket with no subscribe message", () => {
  Socket.instances = [];
  const stream = createFormingStreamForExchange("binance", streamOptions("4h"));
  assert.ok(stream);
  stream.start();
  const socket = Socket.instances.at(-1);
  assert.equal(socket.url, `${BINANCE_PUBLIC_WS}/ws/btcusdt@kline_4h`);
  socket.onopen?.();
  assert.deepEqual(socket.sent, []);
  stream.stop();
  assert.equal(socket.closed, true);
});

test("an unknown exchange gets no public stream at all", () => {
  Socket.instances = [];
  assert.equal(createFormingStreamForExchange("coinbase", streamOptions()), null);
  assert.equal(createFormingStreamForExchange("", streamOptions()), null);
  assert.equal(Socket.instances.length, 0, "no socket is opened for an unknown venue");
});

test("shared UTC bucket and staleness rules are re-exported from one module", () => {
  assert.equal(currentBucketMs("5m", NOW), Date.parse("2026-10-06T13:00:00Z"));
  assert.equal(FORMING_STALE_MS, 45000);
  assert.equal(canShowForming("1h", currentBucketMs("1h", NOW) - 3600000, NOW), true);
  assert.equal(canShowForming("1h", currentBucketMs("1h", NOW), NOW), false);
});

test("provider module has no API or persistence path into engine decisions", () => {
  const source = readFileSync(new URL("../../src/trading_assistant/web/static/js/market-data-provider.js", import.meta.url), "utf8");
  for (const forbidden of ["/api/dashboard", "/api/market/candles", "setCandles(", "fetch(", "localStorage", "indexedDB"]) {
    assert.ok(!source.includes(forbidden), forbidden);
  }
});
