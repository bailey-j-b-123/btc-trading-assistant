/**
 * Kraken v2 public trades + OHLC feed for the chart's forming candle only.
 * This module has no stored-candle API, engine import, database, or write path.
 * Closed candles stay in the confirmed series; this feed is never trading input.
 */
export const KRAKEN_PUBLIC_WS = "wss://ws.kraken.com/v2";
export const FORMING_INTERVALS = Object.freeze({ "5m": 5, "15m": 15, "1h": 60, "4h": 240 });
export const FORMING_STALE_MS = 45000;
export const FORMING_CONNECT_TIMEOUT_MS = 15000;
const MAX_BUFFERED_TRADES = 5000;
const MAX_FUTURE_TRADE_MS = 5000;

export function currentBucketMs(timeframe, nowMs) {
  const minutes = FORMING_INTERVALS[timeframe];
  if (!minutes || !Number.isFinite(nowMs) || nowMs <= 0) return null;
  const duration = minutes * 60000;
  return Math.floor(nowMs / duration) * duration; // epoch-anchored UTC rule used by market_data/timeframes.py
}

/** A missing confirmed predecessor is allowed; a current/future stored candle is never overwritten. */
export function canShowForming(timeframe, confirmedOpenMs, nowMs) {
  const bucket = currentBucketMs(timeframe, nowMs);
  return bucket !== null && (confirmedOpenMs === null || confirmedOpenMs === undefined ||
    (Number.isSafeInteger(confirmedOpenMs) && confirmedOpenMs < bucket));
}

function parseUtc(value) {
  if (typeof value !== "string") return null;
  const match = /^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,9}))?Z$/.exec(value);
  if (!match) return null;
  const secondMs = Date.parse(`${match[1]}Z`);
  if (!Number.isFinite(secondMs)) return null;
  const fraction = (match[2] || "").padEnd(9, "0");
  const nanos = BigInt(secondMs) * 1000000n + BigInt(fraction || "0");
  const ms = secondMs + Number(fraction.slice(0, 3) || 0);
  return { ms, nanos };
}

function positiveNumber(value) {
  if ((typeof value !== "number" && typeof value !== "string") || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

function nonNegativeNumber(value) {
  if ((typeof value !== "number" && typeof value !== "string") || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
}

function parseTradeId(value) {
  if (typeof value === "number" && Number.isSafeInteger(value) && value >= 0) return BigInt(value);
  if (typeof value === "string" && /^\d+$/.test(value)) {
    try { return BigInt(value); } catch { return null; }
  }
  return null;
}

/** Parse only actual Kraken v2 BTC/USDT trade messages. */
export function parseKrakenTrades(message, nowMs = Date.now()) {
  if (message?.channel !== "trade" || !["snapshot", "update"].includes(message.type) ||
      !Array.isArray(message.data)) return [];
  const trades = [];
  for (const row of message.data) {
    if (row?.symbol !== "BTC/USDT") continue;
    const price = positiveNumber(row.price);
    const quantity = positiveNumber(row.qty);
    const stamp = parseUtc(row.timestamp);
    const tradeId = parseTradeId(row.trade_id);
    if (price === null || quantity === null || !stamp || !tradeId ||
        stamp.ms > nowMs + MAX_FUTURE_TRADE_MS) continue;
    trades.push({
      price,
      quantity,
      side: row.side === "buy" || row.side === "sell" ? row.side : null,
      timestamp: row.timestamp,
      timestampMs: stamp.ms,
      timestampNs: stamp.nanos,
      tradeId,
    });
  }
  trades.sort((left, right) => {
    if (left.timestampNs !== right.timestampNs) return left.timestampNs < right.timestampNs ? -1 : 1;
    if (left.tradeId === right.tradeId) return 0;
    return left.tradeId < right.tradeId ? -1 : 1;
  });
  return trades;
}

function parseOhlcRow(row, timeframe, bucketMs) {
  if (row?.symbol !== "BTC/USDT" || row.interval !== FORMING_INTERVALS[timeframe]) return null;
  const begin = parseUtc(row.interval_begin);
  if (!begin || begin.ms !== bucketMs) return null;
  const open = positiveNumber(row.open);
  const high = positiveNumber(row.high);
  const low = positiveNumber(row.low);
  const close = positiveNumber(row.close);
  const volume = nonNegativeNumber(row.volume);
  if ([open, high, low, close, volume].some((value) => value === null) ||
      high < low || open < low || open > high || close < low || close > high ||
      !Number.isInteger(row.trades) || row.trades < 0) return null;
  return {
    time: begin.ms / 1000,
    open,
    high,
    low,
    close,
    volume,
    trades: row.trades,
  };
}

/** Parse a Kraken OHLC message only when its row belongs to the current UTC bucket. */
export function parseFormingOhlc(message, timeframe, nowMs = Date.now()) {
  if (message?.channel !== "ohlc" || !["snapshot", "update"].includes(message.type) ||
      !Array.isArray(message.data)) return null;
  const bucket = currentBucketMs(timeframe, nowMs);
  if (bucket === null) return null;
  for (const row of message.data) {
    const candle = parseOhlcRow(row, timeframe, bucket);
    if (candle) return candle;
  }
  return null;
}

function fingerprint(trade) {
  return `${trade.timestampNs}/${trade.tradeId}/${trade.price}/${trade.quantity}`;
}

function candleAfterTrades(baseline, trades) {
  const candle = { ...baseline };
  const ordered = [...trades.values()].sort((left, right) =>
    left.timestampNs < right.timestampNs ? -1 : left.timestampNs > right.timestampNs ? 1 : 0);
  for (const trade of ordered) {
    candle.high = Math.max(candle.high, trade.price);
    candle.low = Math.min(candle.low, trade.price);
    candle.close = trade.price;
    candle.volume += trade.quantity;
    candle.trades += 1;
  }
  return candle;
}

/**
 * A single reconnecting Kraken socket. Both channels are subscribed on each
 * connection: OHLC supplies a trustworthy seed/repair point, while individual
 * trades drive the displayed last price and immediately update the forming bar.
 */
export function createFormingStream({
  timeframe,
  onCandle = () => {},
  onPrice = () => {},
  onStatus = () => {},
  onRollover = () => {},
  Socket = typeof window === "undefined" ? undefined : window.WebSocket,
  now = () => Date.now(),
  schedule = (fn, delay) => setTimeout(fn, delay),
  cancel = clearTimeout,
  every = (fn, delay) => setInterval(fn, delay),
  stopEvery = clearInterval,
}) {
  let socket = null;
  let stopped = false;
  let retryTimer = null;
  let clockTimer = null;
  let reconnectAttempts = 0;
  let status = "CONNECTING";
  let statusDetail = "Opening Kraken public trade and OHLC subscriptions.";
  let connectionOpenedAt = null;
  let tradeAck = false;
  let ohlcAck = false;
  let tradeSnapshotSeen = false;
  let lastTrade = null;
  let lastTradeId = null;
  let seenTradeKeys = new Set();
  let baseline = null;
  let baselineEventNs = null;
  let pendingTrades = new Map();
  let currentCandle = null;
  let observedBucketMs = currentBucketMs(timeframe, now());
  let dataGap = null;

  function statusInfo(reason = statusDetail) {
    const tradeAgeMs = lastTrade ? Math.max(0, now() - lastTrade.timestampMs) : null;
    return {
      reason,
      reconnectAttempt: reconnectAttempts,
      lastTradeAt: lastTrade?.timestamp || null,
      lastTradeAgeMs: tradeAgeMs,
      connected: Boolean(socket && socket.readyState !== 3),
      dataGap,
    };
  }

  function emitStatus(next, reason, force = false) {
    if (stopped) return;
    const changed = status !== next || statusDetail !== reason;
    status = next;
    statusDetail = reason || statusDetail;
    if (changed || force) onStatus(status, statusInfo(statusDetail));
  }

  function clearForming(reason = "Waiting for a current-bucket Kraken OHLC baseline.") {
    baseline = null;
    baselineEventNs = null;
    pendingTrades.clear();
    currentCandle = null;
    onCandle(null);
    if (socket && socket.readyState === 1) emitStatus("STALE", reason);
  }

  function hasFreshTrade() {
    return Boolean(lastTrade && now() - lastTrade.timestampMs < FORMING_STALE_MS &&
      lastTrade.timestampMs <= now() + MAX_FUTURE_TRADE_MS);
  }

  function refreshStatus() {
    if (stopped) return;
    if (!socket) {
      if (status !== "DISCONNECTED" && status !== "CONNECTING") {
        emitStatus("DISCONNECTED", "Kraken WebSocket is not connected.");
      }
      return;
    }
    const opened = socket.readyState === 1;
    if (!opened) {
      emitStatus("CONNECTING", "Connecting to Kraken public trade and OHLC channels.");
      return;
    }
    const bucket = currentBucketMs(timeframe, now());
    const baselineCurrent = Boolean(baseline && baseline.time * 1000 === bucket);
    if (tradeAck && ohlcAck && tradeSnapshotSeen && baselineCurrent && hasFreshTrade() && !dataGap) {
      reconnectAttempts = 0;
      emitStatus("LIVE", "Kraken trade updates are current; forming candle is display-only.");
      return;
    }
    const waitingTooLong = connectionOpenedAt !== null && now() - connectionOpenedAt >= FORMING_CONNECT_TIMEOUT_MS;
    const staleTrade = Boolean(lastTrade && !hasFreshTrade());
    const reason = dataGap || (staleTrade
      ? `Last Kraken trade is ${Math.floor((now() - lastTrade.timestampMs) / 1000)}s old.`
      : !tradeAck || !ohlcAck
        ? "Waiting for Kraken subscription acknowledgements."
        : !tradeSnapshotSeen
          ? "Waiting for the Kraken trade snapshot."
          : !baselineCurrent
            ? "Waiting for the current-bucket Kraken OHLC baseline."
            : "Waiting for a recent Kraken trade.");
    emitStatus(waitingTooLong || staleTrade || dataGap ? "STALE" : "CONNECTING", reason);
  }

  function scheduleReconnect() {
    if (stopped || retryTimer !== null) return;
    const delay = Math.min(30000, 1000 * (2 ** Math.min(reconnectAttempts, 5)));
    reconnectAttempts += 1;
    retryTimer = schedule(() => {
      retryTimer = null;
      connect();
    }, delay);
  }

  function discardConnection(connection, reason) {
    if (stopped || socket !== connection) return;
    socket = null;
    connectionOpenedAt = null;
    tradeAck = false;
    ohlcAck = false;
    tradeSnapshotSeen = false;
    lastTradeId = null;
    seenTradeKeys = new Set();
    dataGap = reason || "Kraken stream interrupted; waiting for resynchronization.";
    clearForming(dataGap);
    connection.onopen = connection.onmessage = connection.onerror = connection.onclose = null;
    try { connection.close(); } catch { /* socket may already have closed */ }
    emitStatus("DISCONNECTED", dataGap, true);
    scheduleReconnect();
  }

  function handleClose(connection, reason = "Kraken WebSocket closed; reconnecting.") {
    if (stopped || socket !== connection) return;
    socket = null;
    connectionOpenedAt = null;
    tradeAck = false;
    ohlcAck = false;
    tradeSnapshotSeen = false;
    lastTradeId = null;
    seenTradeKeys = new Set();
    clearForming(reason);
    emitStatus("DISCONNECTED", reason, true);
    scheduleReconnect();
  }

  function updatePrice(trade) {
    if (lastTrade && trade.timestampNs < lastTrade.timestampNs) return;
    lastTrade = trade;
    onPrice({
      price: trade.price,
      quantity: trade.quantity,
      side: trade.side,
      timestamp: trade.timestamp,
      tradeId: trade.tradeId.toString(),
    });
  }

  function mergeTradeIntoCandle(trade) {
    const bucket = currentBucketMs(timeframe, now());
    if (bucket === null || Math.floor(trade.timestampMs / (FORMING_INTERVALS[timeframe] * 60000)) *
        (FORMING_INTERVALS[timeframe] * 60000) !== bucket) return;
    if (!baseline || baseline.time * 1000 !== bucket) {
      pendingTrades.set(fingerprint(trade), trade);
      while (pendingTrades.size > MAX_BUFFERED_TRADES) {
        pendingTrades.delete(pendingTrades.keys().next().value);
      }
      return;
    }
    if (baselineEventNs !== null && trade.timestampNs <= baselineEventNs) return;
    pendingTrades.set(fingerprint(trade), trade);
    currentCandle = candleAfterTrades(baseline, pendingTrades);
    onCandle({ ...currentCandle });
  }

  function processTradeMessage(message, connection) {
    const trades = parseKrakenTrades(message, now());
    if (message.type === "snapshot") tradeSnapshotSeen = true;
    // Kraken trade_id is the sequence source. Validate batched updates in that
    // order even if timestamp precision/order differs; candle values are
    // separately replayed by exchange timestamp in candleAfterTrades().
    const sequenceOrdered = message.type === "update"
      ? [...trades].sort((left, right) => left.tradeId === right.tradeId ? 0 :
        left.tradeId < right.tradeId ? -1 : 1)
      : trades;
    for (const trade of sequenceOrdered) {
      const key = fingerprint(trade);
      if (seenTradeKeys.has(key)) continue;
      const isUpdate = message.type === "update";
      if (isUpdate && lastTradeId !== null && trade.tradeId <= lastTradeId) continue;
      if (isUpdate && lastTradeId !== null && trade.tradeId > lastTradeId + 1n) {
        discardConnection(connection, `Kraken trade sequence gap: expected ${lastTradeId + 1n}, received ${trade.tradeId}. Resynchronizing.`);
        return false;
      }
      seenTradeKeys.add(key);
      if (seenTradeKeys.size > MAX_BUFFERED_TRADES) {
        const oldest = seenTradeKeys.values().next().value;
        seenTradeKeys.delete(oldest);
      }
      if (lastTradeId === null || trade.tradeId > lastTradeId) lastTradeId = trade.tradeId;
      updatePrice(trade);
      mergeTradeIntoCandle(trade);
    }
    if (message.type === "snapshot") dataGap = null;
    refreshStatus();
    return true;
  }

  function processOhlcMessage(message) {
    const bucket = currentBucketMs(timeframe, now());
    if (bucket === null) return;
    let selected = null;
    for (const row of message.data || []) {
      selected = parseOhlcRow(row, timeframe, bucket);
      if (selected) break;
    }
    if (!selected) {
      refreshStatus();
      return;
    }
    const eventStamp = parseUtc(message.timestamp);
    if (!eventStamp) {
      refreshStatus();
      return;
    }
    if (baselineEventNs !== null && eventStamp.nanos < baselineEventNs) return;
    baseline = selected;
    baselineEventNs = eventStamp.nanos;
    for (const [key, trade] of pendingTrades) {
      if (trade.timestampNs <= baselineEventNs ||
          Math.floor(trade.timestampMs / (FORMING_INTERVALS[timeframe] * 60000)) *
            (FORMING_INTERVALS[timeframe] * 60000) !== bucket) {
        pendingTrades.delete(key);
      }
    }
    currentCandle = candleAfterTrades(baseline, pendingTrades);
    onCandle({ ...currentCandle });
    if (message.type === "snapshot" || message.type === "update") dataGap = null;
    refreshStatus();
  }

  function onMessage(event, connection) {
    if (stopped || socket !== connection) return;
    let message;
    try { message = JSON.parse(event.data); } catch {
      discardConnection(connection, "Invalid JSON from Kraken; reconnecting.");
      return;
    }
    if (message?.method === "subscribe") {
      if (message.success === false) {
        const channel = message.result?.channel || "unknown";
        discardConnection(connection, `Kraken rejected the ${channel} subscription.`);
        return;
      }
      const channel = message.result?.channel;
      if (channel === "trade") tradeAck = true;
      if (channel === "ohlc") ohlcAck = true;
      refreshStatus();
      return;
    }
    if (message?.channel === "trade") {
      processTradeMessage(message, connection);
    } else if (message?.channel === "ohlc" && Array.isArray(message.data)) {
      processOhlcMessage(message);
    } else if (message?.channel === "heartbeat") {
      refreshStatus(); // transport heartbeat is not a substitute for a fresh trade
    } else if (message?.channel === "status" && message?.data?.[0]?.system === "maintenance") {
      discardConnection(connection, "Kraken reports maintenance; reconnecting when available.");
    }
  }

  function connect() {
    if (stopped) return;
    if (!FORMING_INTERVALS[timeframe] || typeof Socket !== "function") {
      dataGap = "Kraken WebSocket is unavailable in this browser.";
      emitStatus("DISCONNECTED", dataGap, true);
      return;
    }
    emitStatus("CONNECTING", "Opening Kraken public trade and OHLC subscriptions.", true);
    tradeAck = false;
    ohlcAck = false;
    tradeSnapshotSeen = false;
    lastTradeId = null;
    seenTradeKeys = new Set();
    connectionOpenedAt = null;
    try {
      const connection = new Socket(KRAKEN_PUBLIC_WS);
      socket = connection;
      connection.onopen = () => {
        if (stopped || socket !== connection) return;
        connectionOpenedAt = now();
        try {
          connection.send(JSON.stringify({ method: "subscribe", params: {
            channel: "trade", symbol: ["BTC/USDT"], snapshot: true,
          } }));
          connection.send(JSON.stringify({ method: "subscribe", params: {
            channel: "ohlc", symbol: ["BTC/USDT"], interval: FORMING_INTERVALS[timeframe], snapshot: true,
          } }));
        } catch {
          discardConnection(connection, "Could not subscribe to Kraken channels.");
          return;
        }
        refreshStatus();
      };
      connection.onmessage = (event) => onMessage(event, connection);
      connection.onerror = () => {
        if (!stopped && socket === connection) discardConnection(connection, "Kraken WebSocket error; reconnecting.");
      };
      connection.onclose = () => handleClose(connection);
    } catch {
      socket = null;
      emitStatus("DISCONNECTED", "Could not open the Kraken WebSocket; retrying.", true);
      scheduleReconnect();
    }
  }

  function checkClock() {
    if (stopped) return;
    const nowMs = now();
    const bucket = currentBucketMs(timeframe, nowMs);
    if (bucket !== null && bucket !== observedBucketMs) {
      observedBucketMs = bucket;
      clearForming("A new UTC candle interval began; waiting for its Kraken baseline.");
      pendingTrades = new Map([...pendingTrades].filter(([, trade]) =>
        Math.floor(trade.timestampMs / (FORMING_INTERVALS[timeframe] * 60000)) *
          (FORMING_INTERVALS[timeframe] * 60000) === bucket));
      onRollover({ timeframe, bucketOpenMs: bucket, detectedAtMs: nowMs });
    }
    if (socket && socket.readyState === 1) {
      const openedTooLong = connectionOpenedAt !== null && nowMs - connectionOpenedAt >= FORMING_CONNECT_TIMEOUT_MS;
      if (openedTooLong && (!tradeAck || !ohlcAck || !tradeSnapshotSeen ||
          !baseline || baseline.time * 1000 !== bucket)) {
        emitStatus("STALE", "Kraken did not provide a complete current-bucket snapshot in time.");
      } else if (lastTrade && !hasFreshTrade()) {
        emitStatus("STALE", `Last Kraken trade is ${Math.floor((nowMs - lastTrade.timestampMs) / 1000)}s old.`);
      } else {
        refreshStatus();
      }
    }
  }

  return {
    start() {
      if (stopped || clockTimer !== null) return;
      emitStatus("CONNECTING", "Opening Kraken public trade and OHLC subscriptions.", true);
      onCandle(null);
      clockTimer = every(checkClock, 1000);
      clockTimer?.unref?.();
      connect();
    },
    stop() {
      if (stopped) return;
      stopped = true;
      if (retryTimer !== null) cancel(retryTimer);
      retryTimer = null;
      if (clockTimer !== null) stopEvery(clockTimer);
      clockTimer = null;
      if (socket) {
        const previous = socket;
        socket = null;
        previous.onopen = previous.onmessage = previous.onerror = previous.onclose = null;
        try { previous.close(); } catch { /* already closed */ }
      }
      baseline = null;
      pendingTrades.clear();
      currentCandle = null;
    },
  };
}
