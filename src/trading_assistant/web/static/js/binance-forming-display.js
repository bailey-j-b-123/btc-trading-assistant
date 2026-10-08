/**
 * Public Binance Spot kline stream: temporary chart pixels ONLY.
 * This module has no stored-candle API, engine import, database, or write path.
 * Binance sends one kline event per interval update; the event flag `x` marks
 * whether the kline is FINAL. Only a still-forming kline (`x: false`) may be
 * drawn as the ghost candle — a final kline belongs to closed history and is
 * never shown here, and no old bucket is ever copied into the confirmed series
 * when a boundary passes.
 *
 * Bucket alignment, the adjacency rule against stored closed candles, and the
 * staleness bound are shared with the Kraken module so both venues follow the
 * same epoch-anchored UTC rule (see forming-display.js).
 */

import {
  canShowForming,
  currentBucketMs,
  FORMING_INTERVALS,
  FORMING_STALE_MS,
} from "./forming-display.js";

export const BINANCE_PUBLIC_WS = "wss://stream.binance.com:9443";
export const BINANCE_INTERVALS = Object.freeze({ "5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h" });
export const BINANCE_STREAM_SYMBOL = "BTCUSDT"; // the dashboard ghost is BTC/USDT only

/** Binance spot stream symbols are the concatenated base+quote, uppercased. */
export function binanceStreamSymbol(symbol) {
  return String(symbol || "").toUpperCase().replace(/[^A-Z0-9]/g, "");
}

/** Raw-stream path subscription: wss://stream.binance.com:9443/ws/btcusdt@kline_5m */
export function binanceKlineStreamUrl(symbol, timeframe) {
  const interval = BINANCE_INTERVALS[timeframe];
  const streamSymbol = binanceStreamSymbol(symbol);
  if (!interval || !streamSymbol) return null;
  return `${BINANCE_PUBLIC_WS}/ws/${streamSymbol.toLowerCase()}@kline_${interval}`;
}

function finitePositive(value) {
  if ((typeof value !== "string" && typeof value !== "number") || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

/**
 * No OHLC interpolation: numbers and the open timestamp come exclusively from
 * Binance. A final kline (`x: true`) is closed history — it is rejected here so
 * the ghost series can never duplicate or pre-empt a stored closed candle.
 */
export function parseFormingKline(message, timeframe, nowMs, confirmedOpenMs) {
  if (message?.e !== "kline" || !canShowForming(timeframe, confirmedOpenMs, nowMs)) return null;
  const kline = message.k;
  if (!kline || typeof kline !== "object") return null;
  // Reject replayed/delayed market events; the event clock must be fresh.
  const eventMs = Number.isInteger(message.E) ? message.E : NaN;
  if (!Number.isFinite(eventMs) || eventMs > nowMs + 5000 || nowMs - eventMs >= FORMING_STALE_MS) return null;
  if (message.s !== BINANCE_STREAM_SYMBOL || kline.s !== BINANCE_STREAM_SYMBOL) return null;
  if (kline.i !== BINANCE_INTERVALS[timeframe]) return null;
  if (kline.x !== false) return null; // final kline = closed history, never the ghost
  const bucket = currentBucketMs(timeframe, nowMs);
  const intervalMs = FORMING_INTERVALS[timeframe] * 60_000;
  const beginMs = Number.isInteger(kline.t) ? kline.t : NaN;
  const closeMs = Number.isInteger(kline.T) ? kline.T : NaN;
  if (!Number.isFinite(beginMs) || beginMs !== bucket || beginMs <= confirmedOpenMs ||
      closeMs !== beginMs + intervalMs - 1) return null;
  const values = [kline.o, kline.h, kline.l, kline.c];
  const prices = values.map(finitePositive);
  if (prices.some((value) => value === null)) return null;
  const [open, high, low, close] = prices;
  if (high < low || open < low || open > high || close < low || close > high) return null;
  if ((typeof kline.v !== "string" && typeof kline.v !== "number") || kline.v === "" ||
      !Number.isFinite(Number(kline.v)) || Number(kline.v) < 0) return null;
  if (!Number.isInteger(kline.n) || kline.n < 0) return null;
  return { time: beginMs / 1000, open, high, low, close, volume: Number(kline.v),
    trades: kline.n }; // trade count is metadata, not a decision input
}

/** One socket for the viewed timeframe. Stop closes it before switching. */
export function createBinanceFormingStream({ timeframe, confirmedOpenMs, onCandle, onStatus,
  Socket = typeof window === "undefined" ? undefined : window.WebSocket, now = () => Date.now(),
  schedule = (fn, delay) => setTimeout(fn, delay), cancel = clearTimeout,
  every = (fn, delay) => setInterval(fn, delay), stopEvery = clearInterval,
}) {
  let socket = null;
  let stopped = false;
  let retry = null;
  let clock = null;
  let attempts = 0;
  let lastUpdate = 0;
  let lastCandle = null;

  function discard(status) {
    if (stopped) return;
    lastCandle = null;
    lastUpdate = 0;
    onCandle(null);
    onStatus(status);
  }
  function checkClock() {
    if (!lastCandle) return;
    const bucket = currentBucketMs(timeframe, now());
    if (lastCandle.time * 1000 !== bucket) {
      // Crossing the close boundary never calls the stored-candle setter.
      discard("STALE");
    } else if (now() - lastUpdate >= FORMING_STALE_MS) {
      discard("STALE");
    }
  }
  function reconnect() {
    if (stopped || retry !== null) return;
    // Bounded exponential retry, never a tight connection loop.
    const delay = Math.min(30000, 1000 * (2 ** Math.min(attempts++, 5)));
    retry = schedule(() => { retry = null; connect(); }, delay);
  }
  function connect() {
    if (stopped) return;
    const url = binanceKlineStreamUrl(BINANCE_STREAM_SYMBOL, timeframe);
    if (typeof Socket !== "function" || !url) {
      discard("UNAVAILABLE");
      return;
    }
    try {
      const connection = new Socket(url);
      socket = connection;
      // A raw stream subscribes through its URL path; nothing is sent on open.
      connection.onmessage = (event) => {
        if (stopped || socket !== connection) return;
        let message;
        try { message = JSON.parse(event.data); } catch { discard(lastFingerprint ? "STALE" : "UNAVAILABLE"); return; }
        if (message?.e !== "kline") return; // not a kline event: nothing to draw
        const candle = parseFormingKline(message, timeframe, now(), confirmedOpenMs);
        if (!candle) {
          // A kline event that is final, stale or from an old bucket means the
          // ghost is gone; closed history is never drawn from this socket.
          discard(lastFingerprint ? "STALE" : "UNAVAILABLE");
          return;
        }
        lastFingerprint = fingerprint(candle);
        attempts = 0;
        lastUpdate = now();
        lastCandle = candle;
        onCandle(candle);
        onStatus("CURRENT", lastUpdate);
      };
      connection.onerror = () => { if (!stopped && socket === connection) discard(lastFingerprint ? "STALE" : "UNAVAILABLE"); };
      connection.onclose = () => {
        if (stopped || socket !== connection) return;
        socket = null;
        discard(lastFingerprint ? "STALE" : "UNAVAILABLE");
        reconnect();
      };
    } catch {
      discard("UNAVAILABLE");
      reconnect();
    }
  }
  let lastFingerprint = null;
  const fingerprint = (c) => `${c.time}/${c.open}/${c.high}/${c.low}/${c.close}/${c.volume}/${c.trades}`;
  return {
    start() {
      if (stopped || clock !== null) return;
      onStatus("UNAVAILABLE");
      clock = every(checkClock, 1000);
      clock?.unref?.();
      connect();
    },
    stop() {
      stopped = true;
      if (retry !== null) cancel(retry);
      if (clock !== null) stopEvery(clock);
      if (socket) {
        const previous = socket;
        socket = null;
        previous.onopen = previous.onmessage = previous.onerror = previous.onclose = null;
        previous.close();
      }
    },
  };
}
