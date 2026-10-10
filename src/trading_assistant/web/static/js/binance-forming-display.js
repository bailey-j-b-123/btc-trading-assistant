/**
 * Binance Spot public kline stream: temporary chart pixels ONLY.
 * This module has no stored-candle API, engine import, database, or write path.
 * A final kline (x: true) is closed history and is never drawn here or promoted
 * into the confirmed series. The payload is accepted only for the current,
 * epoch-aligned UTC bucket beside recent stored history.
 */

import {
  canShowForming,
  currentBucketMs,
  FORMING_INTERVALS,
  FORMING_STALE_MS,
} from "./forming-display.js";

export const BINANCE_PUBLIC_WS = "wss://stream.binance.com:9443";
export const BINANCE_INTERVALS = Object.freeze({ "5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h" });
export const BINANCE_STREAM_SYMBOL = "BTCUSDT";

/** Accept only the dashboard's supported BTC/USDT spot instrument. */
export function binanceStreamSymbol(symbol) {
  if (typeof symbol !== "string") return null;
  const compact = symbol.trim().toUpperCase().replace(/[\s/_-]/g, "");
  return compact === BINANCE_STREAM_SYMBOL ? BINANCE_STREAM_SYMBOL : null;
}

/** Public raw-stream endpoint for one supported chart timeframe. */
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

function finiteNonNegative(value) {
  if ((typeof value !== "string" && typeof value !== "number") || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
}

/** Validate one in-progress Binance kline; reject final, stale, misaligned or malformed rows. */
export function parseFormingKline(message, timeframe, nowMs, confirmedOpenMs) {
  if (message?.e !== "kline" || !canShowForming(timeframe, confirmedOpenMs, nowMs)) return null;
  const kline = message.k;
  if (!kline || typeof kline !== "object") return null;

  const eventMs = Number.isSafeInteger(message.E) ? message.E : NaN;
  if (!Number.isFinite(eventMs) || eventMs > nowMs + 5_000 || nowMs - eventMs >= FORMING_STALE_MS) return null;
  if (message.s !== BINANCE_STREAM_SYMBOL || kline.s !== BINANCE_STREAM_SYMBOL) return null;
  if (kline.i !== BINANCE_INTERVALS[timeframe] || kline.x !== false) return null;

  const bucket = currentBucketMs(timeframe, nowMs);
  const intervalMs = FORMING_INTERVALS[timeframe] * 60_000;
  const beginMs = Number.isSafeInteger(kline.t) ? kline.t : NaN;
  const closeMs = Number.isSafeInteger(kline.T) ? kline.T : NaN;
  if (bucket === null || !Number.isFinite(beginMs) || beginMs !== bucket ||
      beginMs <= confirmedOpenMs || closeMs !== beginMs + intervalMs - 1) return null;

  const prices = [kline.o, kline.h, kline.l, kline.c].map(finitePositive);
  if (prices.some((value) => value === null)) return null;
  const [open, high, low, close] = prices;
  const volume = finiteNonNegative(kline.v);
  if (high < low || open < low || open > high || close < low || close > high ||
      volume === null || !Number.isSafeInteger(kline.n) || kline.n < 0) return null;

  return {
    time: beginMs / 1000,
    open,
    high,
    low,
    close,
    volume,
    trades: kline.n,
  };
}

/** One public socket for the viewed chart timeframe; switching closes the old one. */
export function createBinanceFormingStream({
  symbol = "BTC/USDT",
  timeframe,
  confirmedOpenMs,
  onCandle,
  onStatus,
  Socket = typeof window === "undefined" ? undefined : window.WebSocket,
  now = () => Date.now(),
  schedule = (fn, delay) => setTimeout(fn, delay),
  cancel = clearTimeout,
  every = (fn, delay) => setInterval(fn, delay),
  stopEvery = clearInterval,
}) {
  let socket = null;
  let stopped = false;
  let retry = null;
  let clock = null;
  let attempts = 0;
  let lastUpdate = 0;
  let lastCandle = null;
  let hasReceivedValidCandle = false;

  function discard(status) {
    if (stopped) return;
    lastCandle = null;
    lastUpdate = 0;
    onCandle(null);
    onStatus(status);
  }

  function unavailableOrStale() {
    return hasReceivedValidCandle ? "STALE" : "UNAVAILABLE";
  }

  function checkClock() {
    if (!lastCandle) return;
    const bucket = currentBucketMs(timeframe, now());
    if (lastCandle.time * 1000 !== bucket || now() - lastUpdate >= FORMING_STALE_MS) {
      // Crossing a close boundary clears the display-only series; it never
      // calls the stored-candle setter or turns the ghost into history.
      discard("STALE");
    }
  }

  function reconnect() {
    if (stopped || retry !== null) return;
    const delay = Math.min(30_000, 1_000 * (2 ** Math.min(attempts++, 5)));
    retry = schedule(() => {
      retry = null;
      connect();
    }, delay);
  }

  function connect() {
    if (stopped) return;
    const url = binanceKlineStreamUrl(symbol, timeframe);
    if (typeof Socket !== "function" || !url) {
      discard("UNAVAILABLE");
      return;
    }

    try {
      const connection = new Socket(url);
      socket = connection;
      connection.onmessage = (event) => {
        if (stopped || socket !== connection) return;
        let message;
        try {
          message = JSON.parse(event.data);
        } catch {
          discard(unavailableOrStale());
          return;
        }
        if (message?.e !== "kline") return;

        const candle = parseFormingKline(message, timeframe, now(), confirmedOpenMs);
        if (!candle) {
          discard(unavailableOrStale());
          return;
        }

        attempts = 0;
        hasReceivedValidCandle = true;
        lastUpdate = now();
        lastCandle = candle;
        onCandle(candle);
        onStatus("CURRENT", lastUpdate);
      };
      connection.onerror = () => {
        if (!stopped && socket === connection) {
          discard(unavailableOrStale());
          try { connection.close(); } catch { /* onclose/retry is best-effort */ }
        }
      };
      connection.onclose = () => {
        if (stopped || socket !== connection) return;
        socket = null;
        discard(unavailableOrStale());
        reconnect();
      };
    } catch {
      discard(unavailableOrStale());
      reconnect();
    }
  }

  return {
    start() {
      if (stopped || clock !== null) return;
      onStatus("UNAVAILABLE");
      clock = every(checkClock, 1_000);
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
