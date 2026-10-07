/**
 * Public Kraken v2 OHLC stream: temporary chart pixels ONLY.
 * This module has no stored-candle API, engine import, database, or write path.
 * Kraken sends a current-bucket snapshot and then OHLC updates on trades.
 * Never copy an old bucket into the confirmed series when a boundary passes.
 */
export const KRAKEN_PUBLIC_WS = "wss://ws.kraken.com/v2";
export const FORMING_INTERVALS = Object.freeze({ "5m": 5, "15m": 15, "1h": 60, "4h": 240 });
export const FORMING_STALE_MS = 45000;

export function currentBucketMs(timeframe, nowMs) {
  const minutes = FORMING_INTERVALS[timeframe];
  if (!minutes || !Number.isFinite(nowMs) || nowMs <= 0) return null;
  const duration = minutes * 60000;
  return Math.floor(nowMs / duration) * duration; // same epoch-anchored UTC rule as market_data/timeframes.py
}

/** Only adjacent stored history may be combined visually with the current bucket. */
export function canShowForming(timeframe, confirmedOpenMs, nowMs) {
  const bucket = currentBucketMs(timeframe, nowMs);
  const duration = FORMING_INTERVALS[timeframe] * 60000;
  return bucket !== null && Number.isSafeInteger(confirmedOpenMs) &&
    confirmedOpenMs < bucket && confirmedOpenMs >= bucket - duration * 2;
}

/** No OHLC interpolation: numbers and open timestamp come exclusively from Kraken. */
export function parseFormingOhlc(message, timeframe, nowMs, confirmedOpenMs) {
  if (message?.channel !== "ohlc" || !["snapshot", "update"].includes(message.type) ||
      !Array.isArray(message.data) || !canShowForming(timeframe, confirmedOpenMs, nowMs)) return null;
  // Reject replayed/delayed market messages; a fresh snapshot still cannot
  // prove the timestamp of its last individual trade (see README limitation).
  const eventMs = typeof message.timestamp === "string" && /Z$/.test(message.timestamp)
    ? Date.parse(message.timestamp) : NaN;
  if (!Number.isFinite(eventMs) || eventMs > nowMs + 5000 || nowMs - eventMs >= FORMING_STALE_MS) return null;
  const interval = FORMING_INTERVALS[timeframe];
  const bucket = currentBucketMs(timeframe, nowMs);
  for (const row of message.data) {
    if (row?.symbol !== "BTC/USDT" || row.interval !== interval) continue;
    const beginMs = typeof row.interval_begin === "string" && /Z$/.test(row.interval_begin)
      ? Date.parse(row.interval_begin) : NaN;
    if (!Number.isFinite(beginMs) || beginMs !== bucket || beginMs <= confirmedOpenMs) continue;
    const values = [row.open, row.high, row.low, row.close];
    if (values.some((value) => (typeof value !== "number" && typeof value !== "string") ||
        value === "" || !Number.isFinite(Number(value)) || Number(value) <= 0)) return null;
    const [open, high, low, close] = values.map(Number);
    if (high < low || open < low || open > high || close < low || close > high ||
        (typeof row.volume !== "number" && typeof row.volume !== "string") ||
        row.volume === "" || !Number.isFinite(Number(row.volume)) || Number(row.volume) < 0 ||
        !Number.isInteger(row.trades) || row.trades < 0) return null;
    return { time: beginMs / 1000, open, high, low, close, volume: Number(row.volume),
      trades: row.trades }; // trade count is metadata, not a decision input
  }
  return null;
}

/** One socket for the viewed timeframe. Stop closes it before switching. */
export function createFormingStream({ timeframe, confirmedOpenMs, onCandle, onStatus,
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
  let stale = false;

  function discard(status) {
    if (stopped) return;
    lastCandle = null;
    lastUpdate = 0;
    stale = true;
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
    if (typeof Socket !== "function" || !FORMING_INTERVALS[timeframe]) {
      discard("UNAVAILABLE");
      return;
    }
    try {
      const connection = new Socket(KRAKEN_PUBLIC_WS);
      socket = connection;
      connection.onopen = () => {
        if (stopped || socket !== connection) return;
        try {
          connection.send(JSON.stringify({ method: "subscribe", params: {
            channel: "ohlc", symbol: ["BTC/USDT"], interval: FORMING_INTERVALS[timeframe], snapshot: true,
          } }));
        } catch {
          discard("UNAVAILABLE");
          connection.close();
        }
      };
      connection.onmessage = (event) => {
        if (stopped || socket !== connection) return;
        let message;
        try { message = JSON.parse(event.data); } catch { discard(lastFingerprint ? "STALE" : "UNAVAILABLE"); return; }
        if (message?.method === "subscribe" && message.success === false) {
          discard("UNAVAILABLE");
          connection.close();
          return;
        }
        if (message?.channel !== "ohlc") return; // heartbeat / subscription ack
        const candle = parseFormingOhlc(message, timeframe, now(), confirmedOpenMs);
        if (!candle) {
          // A snapshot may contain only older buckets; do not show them.
          const hasCurrent = Array.isArray(message.data) && message.data.some((row) => row?.symbol === "BTC/USDT" &&
            row.interval === FORMING_INTERVALS[timeframe] &&
            Date.parse(row.interval_begin) === currentBucketMs(timeframe, now()));
          if (hasCurrent || message.type !== "snapshot") discard(lastFingerprint ? "STALE" : "UNAVAILABLE");
          return;
        }
        // Re-subscribing to an unchanged snapshot does not un-freeze an old candle.
        if (stale && message.type === "snapshot" && lastFingerprint === fingerprint(candle)) return;
        lastFingerprint = fingerprint(candle);
        stale = false;
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
