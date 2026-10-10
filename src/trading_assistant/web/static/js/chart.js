/**
 * Candlestick chart wrapper for the vendored Lightweight Charts v4 bundle.
 * Confirmed series input is the backend's stored-candle payload. A separate
 * ghost series may show public forming OHLC; it never enters confirmed rows. Chart instances, observers, and price-line handles
 * are explicitly disposed when the dashboard is refreshed or unmounted.
 */

const UP = "#2fbf7f";
const DOWN = "#e0565b";
const GRID = "#1a2130";
const TEXT = "#707b91";

export const OVERLAY_COLORS = {
  zones: "#5b9bd5",
  range: "#b58cf0",
  equalLevels: "#e8a33d",
  entry: "#2fbf7f",
  stop: "#e0565b",
  invalidation: "#e8a33d",
  targets: "#5b9bd5",
  swings: "#707b91",
  reference: "#8ea0bd",
};

function rowsFromPayload(payload) {
  if (Array.isArray(payload)) return payload;
  if (payload && Array.isArray(payload.candles)) return payload.candles;
  return [];
}

function finiteNumber(value) {
  if (value === null || value === undefined || value === "") return null;
  if (typeof value === "string" && value.trim() === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function validRow(row) {
  if (!Array.isArray(row) || row.length < 5) return null;
  const timestamp = finiteNumber(row[0]);
  const open = finiteNumber(row[1]);
  const high = finiteNumber(row[2]);
  const low = finiteNumber(row[3]);
  const close = finiteNumber(row[4]);
  if ([timestamp, open, high, low, close].some((value) => value === null)) return null;
  if (timestamp <= 0 || high < low || open < low || open > high || close < low || close > high) return null;
  return { timestamp, open, high, low, close, volume: finiteNumber(row[5]) };
}

/**
 * The real API returns { candles: [[timestamp_ms, open, high, low, close,
 * volume], ...] }. The dashboard also passes that same row array directly.
 * Invalid/missing rows are omitted; values are not interpolated or rounded.
 */
export function toChartCandles(payload) {
  const mapped = [];
  for (const row of rowsFromPayload(payload)) {
    const candle = validRow(row);
    if (!candle) continue;
    mapped.push({
      time: Math.floor(candle.timestamp / 1000),
      open: candle.open,
      high: candle.high,
      low: candle.low,
      close: candle.close,
    });
  }
  return mapped;
}

export const FALLBACK_CHART_WIDTH = 640;
export const FALLBACK_CHART_HEIGHT = 420;

/**
 * Create the price chart, surviving a mount that happens before layout.
 *
 * Root cause of the blank chart: this used to return null permanently when
 * the container measured 0x0 at mount time (stylesheet still loading,
 * cached HTML with a stale stylesheet, hidden ancestor), so valid stored
 * candles never reached the library and the chart never recovered. Now a
 * zero measurement only selects explicit fallback dimensions; the resize
 * handler below corrects to the real layout as soon as it exists. Only a
 * missing chart library or a missing container still returns null.
 */
export function createPriceChart(container, { height } = {}) {
  const library = typeof window === "undefined" ? null : window.LightweightCharts;
  if (!library || !container) return null;

  const measuredWidth = Number(container.clientWidth) || 0;
  const measuredHeight = Number(container.clientHeight) || Number(height) || 0;
  const explicitHeight = Number(height) || 0;
  const initialWidth = measuredWidth > 0 ? measuredWidth : FALLBACK_CHART_WIDTH;
  const initialHeight = measuredHeight > 0
    ? measuredHeight
    : explicitHeight > 0 ? explicitHeight : FALLBACK_CHART_HEIGHT;

  const computedFont = typeof getComputedStyle === "function"
    ? getComputedStyle(document.documentElement).getPropertyValue("--mono").trim()
    : "";
  let chart = null;
  let series;
  let formingSeries;
  let volume;
  try {
    chart = library.createChart(container, {
      width: initialWidth,
      height: initialHeight,
      autoSize: false,
      layout: {
        background: { type: "solid", color: "transparent" },
        textColor: TEXT,
        fontFamily: computedFont || "monospace",
        fontSize: 11,
      },
      grid: {
        vertLines: { color: GRID },
        horzLines: { color: GRID },
      },
      crosshair: {
        mode: 0,
        vertLine: { color: "#3a4560" },
        horzLine: { color: "#3a4560" },
      },
      rightPriceScale: { borderColor: "#222b3b" },
      timeScale: { borderColor: "#222b3b", timeVisible: true, secondsVisible: false },
      handleScroll: true,
      handleScale: true,
    });
    series = chart.addCandlestickSeries({
      upColor: UP,
      downColor: DOWN,
      wickUpColor: UP,
      wickDownColor: DOWN,
      borderVisible: false,
    });
    // Independent temporary series: never append/update the stored confirmed series.
    formingSeries = chart.addCandlestickSeries({
      upColor: "rgba(91,155,213,0.26)", downColor: "rgba(232,163,61,0.26)",
      wickUpColor: "rgba(91,155,213,0.85)", wickDownColor: "rgba(232,163,61,0.85)",
      borderVisible: true, borderUpColor: "#5b9bd5", borderDownColor: "#e8a33d",
      priceLineVisible: false, lastValueVisible: false,
    });
    volume = chart.addHistogramSeries({
      priceFormat: { type: "volume" },
      priceScaleId: "volume",
    });
    chart.priceScale("volume").applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } });
  } catch (error) {
    chart?.remove?.();
    throw error;
  }

  const handle = {
    chart,
    series,
    formingSeries,
    volume,
    container,
    priceLineHandles: [],
    // Chart evidence (markers + pattern line series) and event subscriptions are
    // tracked separately so a timeframe switch or refresh removes exactly them.
    evidenceSeries: [],
    eventUnsubscribers: [],
    resizeObserver: null,
    resizeListener: null,
    zoneLayer: null,
    zoneBandsState: null,
    zoneUnsubscribers: [],
    destroyed: false,
  };
  const resize = () => {
    if (handle.destroyed) return;
    const width = Number(container.clientWidth) || 0;
    const nextHeight = Number(container.clientHeight) || 0;
    if (width > 0 && nextHeight > 0) chart.resize(width, nextHeight);
    renderZoneBands(handle);
  };

  if (typeof ResizeObserver === "function") {
    handle.resizeObserver = new ResizeObserver(resize);
    handle.resizeObserver.observe(container);
  } else if (typeof window !== "undefined" && window.addEventListener) {
    handle.resizeListener = resize;
    window.addEventListener("resize", resize);
  }
  resize();
  if ((initialWidth === FALLBACK_CHART_WIDTH || initialHeight === FALLBACK_CHART_HEIGHT) &&
      typeof requestAnimationFrame === "function") {
    // Late stylesheet or layout: re-measure once on the next frame so a
    // fallback-sized chart snaps to the real container even where no
    // ResizeObserver exists to report the change.
    requestAnimationFrame(resize);
  }
  return handle;
}

/** Recent candles shown on first load; the user can zoom out to the full window. */
export const DEFAULT_VISIBLE_CANDLES = 120;
/** Phones get fewer candles so each candle and its marker stays readable. */
export const DEFAULT_VISIBLE_CANDLES_NARROW = 60;

/** Tablets get an intermediate window. Each tier is a presentation choice; the data window is unchanged. */
export const DEFAULT_VISIBLE_CANDLES_TABLET = 90;

function defaultVisibleCandles() {
  const matches = (query) => typeof globalThis.matchMedia === "function" && globalThis.matchMedia(query).matches;
  if (matches("(max-width: 640px)")) return DEFAULT_VISIBLE_CANDLES_NARROW;
  if (matches("(max-width: 1040px)")) return DEFAULT_VISIBLE_CANDLES_TABLET;
  return DEFAULT_VISIBLE_CANDLES;
}

export function setCandles(handle, payload) {
  if (!handle || handle.destroyed) return;
  const rows = rowsFromPayload(payload);
  const candles = toChartCandles(rows);
  handle.series.setData(candles);
  // Apply the default view once per chart: later refreshes must not reset a
  // zoom or pan the user has made.
  if (!handle.initialRangeApplied && candles.length > 0 && handle.chart && typeof handle.chart.timeScale === "function") {
    const timeScale = handle.chart.timeScale();
    if (typeof timeScale.setVisibleLogicalRange === "function") {
      const last = candles.length - 1;
      const visible = defaultVisibleCandles();
      timeScale.setVisibleLogicalRange({ from: Math.max(0, last - visible + 1), to: last + 3 });
    }
    handle.initialRangeApplied = true;
  }

  const volumeRows = [];
  for (const row of rows) {
    const candle = validRow(row);
    if (!candle || candle.volume === null) continue;
    volumeRows.push({
      time: Math.floor(candle.timestamp / 1000),
      value: candle.volume,
      color: candle.close >= candle.open ? "rgba(47,191,127,0.28)" : "rgba(224,86,91,0.28)",
    });
  }
  if (handle.volume && typeof handle.volume.setData === "function") handle.volume.setData(volumeRows);
  renderZoneBands(handle);
}

/** Temporary, explicitly unconfirmed overlay. Never touches handle.series or volume. */
export function setFormingCandle(handle, candle) {
  if (!handle || handle.destroyed || !handle.formingSeries) return;
  if (candle === null) { handle.formingSeries.setData([]); return; }
  if (!Number.isSafeInteger(candle.time) || candle.time <= 0 ||
      [candle.open, candle.high, candle.low, candle.close].some((v) => !Number.isFinite(v) || v <= 0) ||
      candle.high < candle.low || candle.open < candle.low || candle.open > candle.high ||
      candle.close < candle.low || candle.close > candle.high) return;
  handle.formingSeries.setData([{
    time: candle.time, open: candle.open, high: candle.high, low: candle.low, close: candle.close,
  }]);
}

function compactLineTitle(group) {
  const titles = [...new Set(group.map((entry) => entry.title))];
  const priority = (title) => {
    if (/^Zone (low|high)$/.test(title)) return 0;
    if (["Entry", "Stop", "Invalidation"].includes(title)) return 1;
    if (/^T\d+$/.test(title)) return 2;
    if (/^(Support|Resistance) /.test(title)) return 3;
    if (/^Range /.test(title)) return 4;
    if (/^Equal /.test(title)) return 5;
    return 6;
  };
  titles.sort((left, right) => priority(left) - priority(right));
  if (titles.length <= 2) return titles.join(" / ");
  return `${titles.slice(0, 2).join(" / ")} / ${titles.length - 2} more`;
}

function priceLine(price, { color, title, style = 2, width = 1 }) {
  return {
    price,
    color,
    title,
    lineWidth: width,
    lineStyle: style, // Lightweight Charts v4: 0 solid, 1 dotted, 2 dashed, 3 large-dash, 4 sparse.
    axisLabelVisible: true,
    crosshairMarkerVisible: false,
  };
}

export function clearOverlays(handle) {
  if (!handle || !Array.isArray(handle.priceLineHandles)) return;
  const lines = handle.priceLineHandles.splice(0);
  if (!handle.series || typeof handle.series.removePriceLine !== "function") return;
  for (const line of lines) {
    try {
      handle.series.removePriceLine(line);
    } catch {
      // Continue cleaning the remaining tracked handles if the library removed
      // one while the chart was being torn down.
    }
  }
}

export function destroyPriceChart(handle) {
  if (!handle || handle.destroyed) return;
  clearOverlays(handle);
  clearEvidence(handle);
  clearZoneBands(handle);
  unsubscribeChartEvents(handle);
  handle.destroyed = true;
  handle.resizeObserver?.disconnect();
  if (handle.resizeListener && typeof window !== "undefined") {
    window.removeEventListener?.("resize", handle.resizeListener);
  }
  handle.chart?.remove?.();
}

/** Apply exact deterministic overlays. All created lines are tracked. */
export function applyOverlays(handle, payload = {}) {
  if (!handle || handle.destroyed || !handle.series || typeof handle.series.createPriceLine !== "function") return;
  if (!Array.isArray(handle.priceLineHandles)) handle.priceLineHandles = [];
  clearOverlays(handle);
  const safePayload = payload && typeof payload === "object" ? payload : {};
  const overlays = safePayload.overlays && typeof safePayload.overlays === "object"
    ? safePayload.overlays
    : {};
  const prefs = safePayload.prefs && typeof safePayload.prefs === "object"
    ? safePayload.prefs.overlays || {}
    : {};
  const plan = safePayload.plan && safePayload.plan.state === "PLANNABLE"
    ? safePayload.plan
    : null;
  // Group only exact-equal prices so coincident deterministic levels do not
  // obscure candles with overlapping axis labels. The visible title stays
  // short; numeric values are never averaged, rounded, or moved.
  const byPrice = new Map();

  const add = (value, options) => {
    const price = finiteNumber(value);
    if (price === null) return;
    const key = String(price);
    const group = byPrice.get(key) || [];
    if (!group.some((entry) => entry.title === options.title)) {
      group.push({ title: options.title, color: options.color, style: options.style ?? 2, width: options.width ?? 1 });
    }
    byPrice.set(key, group);
  };

  const flush = () => {
    for (const [key, group] of byPrice) {
      const first = group[0];
      const line = handle.series.createPriceLine(priceLine(Number(key), {
        color: first.color,
        title: compactLineTitle(group),
        style: first.style,
        width: first.width,
      }));
      handle.priceLineHandles.push(line);
    }
    byPrice.clear();
  };

  // Support/resistance zones are drawn as shaded bands by setZoneBands(), from the backend's
  // display bands. They are no longer drawn as boundary price lines, which read as clutter and
  // used the detector's centre-vs-close label (a band spanning the close was called resistance).

  if (prefs.range === true && overlays.range && typeof overlays.range === "object") {
    add(overlays.range.range_low, { color: OVERLAY_COLORS.range, title: "Range low", style: 3 });
    add(overlays.range.range_high, { color: OVERLAY_COLORS.range, title: "Range high", style: 3 });
  }

  if (prefs.equalLevels === true) {
    for (const cluster of Array.isArray(overlays.equal_levels) ? overlays.equal_levels : []) {
      add(cluster.level, {
        color: OVERLAY_COLORS.equalLevels,
        title: cluster.type === "equal_high" ? "Equal highs" : "Equal lows",
        style: 1,
      });
    }
  }

  // Ghosted bounds of a known backend reference only on its setup timeframe.
  // No series.update(), future time coordinate, generated path, or new price.
  const band = safePayload.scenarioBand;
  if (band && Number.isFinite(band.low) && Number.isFinite(band.high) &&
      band.low > 0 && band.high >= band.low) {
    add(band.low, { color: OVERLAY_COLORS.reference, title: "Zone low", style: 2 });
    add(band.high, { color: OVERLAY_COLORS.reference, title: "Zone high", style: 2 });
  }

  if (prefs.swings === true) {
    for (const swing of Array.isArray(overlays.swings) ? overlays.swings : []) {
      const value = swing.price !== undefined ? swing.price : swing.level;
      add(value, {
        color: OVERLAY_COLORS.swings,
        title: swing.kind === "high" ? "Swing high" : "Swing low",
        style: 1,
      });
    }
  }

  if (prefs.planLevels !== false && plan) {
    const entry = plan.entry ? plan.entry.value : null;
    const stop = plan.stop ? plan.stop.value : null;
    const invalidation = plan.invalidation ? plan.invalidation.value : null;
    add(entry, { color: OVERLAY_COLORS.entry, title: "Entry", style: 0 });
    add(stop, { color: OVERLAY_COLORS.stop, title: "Stop", style: 0 });
    const invalidationPrice = finiteNumber(invalidation);
    if (invalidationPrice !== null) {
      add(invalidationPrice, { color: OVERLAY_COLORS.invalidation, title: "Invalidation", style: 2 });
    }
    (Array.isArray(plan.targets) ? plan.targets : []).forEach((target, index) => {
      add(target.level ? target.level.value : null, {
        color: OVERLAY_COLORS.targets,
        title: `T${index + 1}`,
        style: 0,
      });
    });
  }
  flush();
}

// ---------------------------------------------------------------------------
// Chart evidence: markers and pattern lines drawn from the pure evidence model
// (evidence.js). Every object created here is tracked on the handle and removed
// by clearEvidence(), so switching timeframe or refreshing never leaves a stale
// marker, line series, or subscription behind.
// ---------------------------------------------------------------------------

/** Remove every evidence marker, line series and price line this module added. */
export function clearEvidence(handle) {
  if (!handle) return;
  if (handle.series && typeof handle.series.setMarkers === "function") {
    try {
      handle.series.setMarkers([]);
    } catch {
      // The series may already be torn down; nothing else is tracked for it.
    }
  }
  const lines = Array.isArray(handle.evidenceSeries) ? handle.evidenceSeries.splice(0) : [];
  for (const line of lines) {
    try {
      handle.chart?.removeSeries?.(line);
    } catch {
      // Keep removing the remaining tracked series.
    }
  }
}

/**
 * Draw an evidence model: markers on the confirmed candle series and one line
 * series per pattern segment. Nothing is interpolated: points are the exact
 * candle times that the model already verified exist on screen.
 */
export function setEvidence(handle, model) {
  if (!handle || handle.destroyed) return;
  clearEvidence(handle);
  const markers = Array.isArray(model?.markers)
    ? model.markers.filter((marker) => Number.isFinite(marker.time)).sort((a, b) => a.time - b.time)
    : [];
  if (handle.series && typeof handle.series.setMarkers === "function") {
    handle.series.setMarkers(markers);
  }
  if (!handle.chart || typeof handle.chart.addLineSeries !== "function") return;
  for (const line of Array.isArray(model?.patternLines) ? model.patternLines : []) {
    // Lightweight Charts requires strictly ascending times: keep one point per time.
    const byTime = new Map();
    for (const point of line.points || []) {
      if (Number.isFinite(point.time) && Number.isFinite(point.value)) byTime.set(point.time, point.value);
    }
    const data = [...byTime.entries()].sort((a, b) => a[0] - b[0]).map(([time, value]) => ({ time, value }));
    if (data.length < 2) continue;
    const series = handle.chart.addLineSeries({
      color: line.color,
      lineWidth: line.lineWidth || 1,
      lineStyle: line.lineStyle ?? 0,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    });
    series.setData(data);
    handle.evidenceSeries.push(series);
  }
}

/**
 * Subscribe to candle clicks and crosshair moves. Returns an unsubscribe
 * function; subscriptions are also released by destroyPriceChart().
 * onClick receives the exact bar time (seconds) under the cursor, or null.
 * onCrosshair receives the confirmed bar's OHLC and volume under the cursor.
 */
export function subscribeChartEvents(handle, { onClick, onCrosshair } = {}) {
  if (!handle || handle.destroyed || !handle.chart) return () => {};
  const chart = handle.chart;
  const unsubscribers = [];
  if (typeof onClick === "function" && typeof chart.subscribeClick === "function") {
    const listener = (param) => {
      const time = param && Number.isFinite(param.time) ? Number(param.time) : null;
      onClick(time);
    };
    chart.subscribeClick(listener);
    unsubscribers.push(() => chart.unsubscribeClick?.(listener));
  }
  if (typeof onCrosshair === "function" && typeof chart.subscribeCrosshairMove === "function") {
    const listener = (param) => {
      const bar = param?.seriesData?.get?.(handle.series) || null;
      const volumeBar = handle.volume ? param?.seriesData?.get?.(handle.volume) || null : null;
      onCrosshair(bar && Number.isFinite(bar.open)
        ? { time: Number(param.time), open: bar.open, high: bar.high, low: bar.low, close: bar.close, volume: volumeBar?.value ?? null }
        : null);
    };
    chart.subscribeCrosshairMove(listener);
    unsubscribers.push(() => chart.unsubscribeCrosshairMove?.(listener));
  }
  const release = () => {
    for (const undo of unsubscribers.splice(0)) {
      try { undo(); } catch { /* chart already removed */ }
    }
  };
  handle.eventUnsubscribers.push(release);
  return release;
}

export function unsubscribeChartEvents(handle) {
  if (!handle || !Array.isArray(handle.eventUnsubscribers)) return;
  for (const release of handle.eventUnsubscribers.splice(0)) release();
}

/**
 * Thin, tracked price lines for higher-timeframe zones. Titles carry the source
 * timeframe so they can never be mistaken for the viewed timeframe's levels.
 * Lines are tracked in priceLineHandles and removed by clearOverlays().
 */
export function addHigherTimeframeLines(handle, entries) {
  if (!handle || handle.destroyed || !handle.series || typeof handle.series.createPriceLine !== "function") return;
  if (!Array.isArray(handle.priceLineHandles)) handle.priceLineHandles = [];
  for (const entry of Array.isArray(entries) ? entries : []) {
    const price = finiteNumber(entry.price);
    if (price === null || price <= 0) continue;
    handle.priceLineHandles.push(handle.series.createPriceLine(priceLine(price, {
      color: entry.color || OVERLAY_COLORS.reference,
      title: entry.title,
      style: 3,
      width: 1,
    })));
  }
}

// --- Support / resistance bands ---------------------------------------------------------
//
// Lightweight Charts v4 (vendored, unmodified) has no series primitives here, so each band is
// a plain element positioned from series.priceToCoordinate(). Bands re-position on range
// changes, crosshair moves and resizes. They never change candles or the price scale, and
// pointer events are off except on each band's label, so candles under a band stay clickable.

const ZONE_LABEL_MIN_GAP_PX = 16;
const ZONE_STYLE = {
  support: { fill: "rgba(46, 196, 182, 0.13)", edge: "#2ec4b6", short: "S" },
  resistance: { fill: "rgba(239, 83, 80, 0.13)", edge: "#ef5350", short: "R" },
  price_inside: { fill: "rgba(245, 166, 35, 0.16)", edge: "#f5a623", short: "IN" },
};

function createZoneLayer(handle) {
  const container = handle.container;
  if (typeof document === "undefined" || !container || handle.zoneLayer) return;
  if (typeof globalThis.getComputedStyle === "function" && globalThis.getComputedStyle(container).position === "static") {
    container.style.position = "relative";
  }
  const layer = document.createElement("div");
  layer.className = "zone-band-layer";
  container.appendChild(layer);
  handle.zoneLayer = layer;
  const timeScale = handle.chart?.timeScale?.();
  const render = () => renderZoneBands(handle);
  if (timeScale && typeof timeScale.subscribeVisibleLogicalRangeChange === "function") {
    timeScale.subscribeVisibleLogicalRangeChange(render);
    handle.zoneUnsubscribers.push(() => timeScale.unsubscribeVisibleLogicalRangeChange?.(render));
  }
  if (typeof handle.chart?.subscribeCrosshairMove === "function") {
    handle.chart.subscribeCrosshairMove(render);
    handle.zoneUnsubscribers.push(() => handle.chart.unsubscribeCrosshairMove?.(render));
  }
}

/**
 * Draw shaded support/resistance bands. `bands` are the backend's display bands (already merged,
 * selected and classified); each may carry `htf: true` for higher-timeframe bands, drawn dashed.
 * `onSelect(band)` runs when a band's label is clicked or activated by keyboard.
 */
export function setZoneBands(handle, bands, { onSelect } = {}) {
  if (!handle || handle.destroyed || !handle.container) return;
  // Feature-detect the DOM it needs; without it, bands are skipped and the chart still renders.
  if (typeof handle.container.appendChild !== "function" || typeof document === "undefined" || typeof document.createElement !== "function") return;
  createZoneLayer(handle);
  handle.zoneBandsState = {
    bands: Array.isArray(bands) ? bands.filter((band) => band && typeof band === "object") : [],
    onSelect: typeof onSelect === "function" ? onSelect : null,
  };
  renderZoneBands(handle);
}

/** Remove every band and its listeners. Safe to call repeatedly. */
export function clearZoneBands(handle) {
  if (!handle) return;
  for (const unsubscribe of handle.zoneUnsubscribers || []) {
    try {
      unsubscribe();
    } catch {
      // The chart may already be removed; nothing else to release.
    }
  }
  handle.zoneUnsubscribers = [];
  handle.zoneBandsState = null;
  if (handle.zoneLayer) {
    handle.zoneLayer.remove?.();
    handle.zoneLayer = null;
  }
}

/** Position every band and label from the current coordinates. Pure reads; no data change. */
export function renderZoneBands(handle) {
  if (!handle || handle.destroyed || !handle.zoneLayer || !handle.zoneBandsState) return;
  const series = handle.series;
  if (!series || typeof series.priceToCoordinate !== "function") return;
  const layer = handle.zoneLayer;
  const height = Number(handle.container?.clientHeight) || 0;
  const { bands, onSelect } = handle.zoneBandsState;
  layer.textContent = "";
  let offView = 0;
  const labels = [];
  for (const band of bands) {
    // Position is where the band sits relative to price; the style key is the display role.
    const styleKey = band.position === "below_price" ? "support" : band.position === "above_price" ? "resistance" : "price_inside";
    const style = ZONE_STYLE[styleKey];
    const high = Number(band.band_high);
    const low = Number(band.band_low);
    if (!Number.isFinite(high) || !Number.isFinite(low)) continue;
    const yHigh = series.priceToCoordinate(high);
    const yLow = series.priceToCoordinate(low);
    if (yHigh === null || yLow === null || yHigh === undefined || yLow === undefined) continue;
    const top = Math.min(yHigh, yLow);
    const bottom = Math.max(yHigh, yLow);
    if (bottom < 0 || top > height) {
      offView += 1;
      continue;
    }
    const clampedTop = Math.max(0, top);
    const clampedBottom = Math.min(height || bottom, bottom);
    const node = document.createElement("div");
    node.className = `zone-band zone-band--${band.position || "price_inside"}${band.htf ? " zone-band--htf" : ""}${band.faded ? " zone-band--faded" : ""}`;
    node.setAttribute?.("aria-hidden", "true");
    node.style.top = `${clampedTop}px`;
    node.style.height = `${Math.max(2, clampedBottom - clampedTop)}px`;
    node.style.setProperty("--zone-fill", style.fill);
    node.style.setProperty("--zone-edge", style.edge);
    layer.appendChild(node);
    labels.push({ band, style, y: clampedTop + 2 });
  }
  // Label collision: stack labels that would overlap, keeping the nearest to its band edge first.
  labels.sort((a, b) => a.y - b.y);
  let previous = -Infinity;
  for (const entry of labels) {
    const y = Math.max(entry.y, previous + ZONE_LABEL_MIN_GAP_PX);
    previous = y;
    const { band, style } = entry;
    const label = document.createElement("button");
    label.type = "button";
    label.className = "zone-band-label";
    label.style.top = `${y}px`;
    label.style.setProperty("--zone-edge", style.edge);
    const tf = band.source_timeframe ? String(band.source_timeframe).toUpperCase() : "";
    label.textContent = `${style.short}${tf ? ` ${tf}` : ""} · ${band.touch_count ?? "?"}×`;
    label.setAttribute("aria-label", `${band.display_role || band.position} zone ${band.band_low} to ${band.band_high}, ${tf || "viewed timeframe"}, ${band.touch_count ?? "unknown"} touches. Open explanation.`);
    label.addEventListener("click", (event) => {
      event.stopPropagation();
      onSelect?.(band);
    });
    layer.appendChild(label);
  }
  if (offView) {
    const note = document.createElement("div");
    note.className = "zone-band-offview";
    note.textContent = `${offView} zone${offView === 1 ? "" : "s"} outside the visible price range`;
    layer.appendChild(note);
  }
}
