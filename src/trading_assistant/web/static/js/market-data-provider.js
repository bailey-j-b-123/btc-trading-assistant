/**
 * Exchange-aware selection of the public, display-only market-data provider.
 *
 * The dashboard's confirmed series always comes from the backend's stored
 * closed candles; this module only picks which PUBLIC venue supplies the
 * temporary forming-candle ghost and how it is labelled. Exchange identities
 * stay separate: a Binance deployment streams Binance klines and never falls
 * back to Kraken (or vice versa), and an unrecognised exchange gets no stream
 * at all — the stored chart is shown unchanged instead of a wrong-venue ghost.
 *
 * This module has no stored-candle API, engine import, database, or write path.
 */

import {
  canShowForming,
  createFormingStream,
  currentBucketMs,
  FORMING_INTERVALS,
  FORMING_STALE_MS,
} from "./forming-display.js";
import {
  BINANCE_INTERVALS,
  createBinanceFormingStream,
} from "./binance-forming-display.js";

export const KRAKEN_PROVIDER = Object.freeze({
  id: "kraken",
  label: "KRAKEN OHLC",
  intervals: FORMING_INTERVALS,
  createStream: createFormingStream,
});

export const BINANCE_PROVIDER = Object.freeze({
  id: "binance",
  label: "BINANCE KLINE",
  intervals: BINANCE_INTERVALS,
  createStream: createBinanceFormingStream,
});

export const MARKET_DATA_PROVIDERS = Object.freeze({
  kraken: KRAKEN_PROVIDER,
  binance: BINANCE_PROVIDER,
});

/** Provider descriptor for an exchange id (case-insensitive), or null. */
export function marketDataProvider(exchange) {
  if (typeof exchange !== "string") return null;
  return MARKET_DATA_PROVIDERS[exchange.trim().toLowerCase()] ?? null;
}

/** Status-badge label for the exchange's public forming-candle stream. */
export function providerLabel(exchange) {
  return marketDataProvider(exchange)?.label ?? "PUBLIC MARKET DATA";
}

/** Whether the exchange's public stream covers the viewed timeframe. */
export function supportsFormingCandle(exchange, timeframe) {
  const provider = marketDataProvider(exchange);
  return Boolean(provider && Object.hasOwn(provider.intervals, timeframe));
}

/** Create the exchange's forming stream, or null when the exchange is unknown. */
export function createFormingStreamForExchange(exchange, options) {
  const provider = marketDataProvider(exchange);
  return provider ? provider.createStream(options) : null;
}

// Shared display rules (epoch-anchored UTC buckets, adjacency to the stored
// closed series, staleness bound) are re-exported so views import one module.
export { canShowForming, currentBucketMs, FORMING_STALE_MS };
