/**
 * Candlestick chart wrapper for the vendored Lightweight Charts v4 bundle.
 * Input is the backend's stored-candle payload; this module never creates
 * market rows or levels. Chart instances, observers, and price-line handles
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
    volume,
    container,
    priceLineHandles: [],
    resizeObserver: null,
    resizeListener: null,
    destroyed: false,
  };
  const resize = () => {
    if (handle.destroyed) return;
    const width = Number(container.clientWidth) || 0;
    const nextHeight = Number(container.clientHeight) || 0;
    if (width > 0 && nextHeight > 0) chart.resize(width, nextHeight);
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

export function setCandles(handle, payload) {
  if (!handle || handle.destroyed) return;
  const rows = rowsFromPayload(payload);
  const candles = toChartCandles(rows);
  handle.series.setData(candles);

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
  // Group pending lines by exact price: several titles at one price become a
  // single axis label ("support high + equal highs") instead of overlapping
  // labels. Nothing is dropped — every level still contributes its title.
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
        title: group.map((entry) => entry.title).join(" + "),
        style: first.style,
        width: first.width,
      }));
      handle.priceLineHandles.push(line);
    }
    byPrice.clear();
  };

  if (prefs.zones === true) {
    for (const zone of Array.isArray(overlays.zones) ? overlays.zones : []) {
      const role = zone.role === "support" ? "support" : zone.role === "resistance" ? "resistance" : "zone";
      add(zone.band_low, { color: OVERLAY_COLORS.zones, title: `${role} low`, style: 1 });
      add(zone.band_high, { color: OVERLAY_COLORS.zones, title: `${role} high`, style: 1 });
    }
  }

  if (prefs.range === true && overlays.range && typeof overlays.range === "object") {
    add(overlays.range.range_low, { color: OVERLAY_COLORS.range, title: "range low", style: 3 });
    add(overlays.range.range_high, { color: OVERLAY_COLORS.range, title: "range high", style: 3 });
  }

  if (prefs.equalLevels === true) {
    for (const cluster of Array.isArray(overlays.equal_levels) ? overlays.equal_levels : []) {
      add(cluster.level, {
        color: OVERLAY_COLORS.equalLevels,
        title: cluster.type === "equal_high" ? "equal highs" : "equal lows",
        style: 1,
      });
    }
  }

  // Ghosted bounds of a known backend reference only on its setup timeframe.
  // No series.update(), future time coordinate, generated path, or new price.
  const band = safePayload.scenarioBand;
  if (band && Number.isFinite(band.low) && Number.isFinite(band.high) &&
      band.low > 0 && band.high >= band.low) {
    add(band.low, { color: OVERLAY_COLORS.reference, title: "scenario reference low · not prediction", style: 2 });
    add(band.high, { color: OVERLAY_COLORS.reference, title: "scenario reference high · not prediction", style: 2 });
  }

  if (prefs.swings === true) {
    for (const swing of Array.isArray(overlays.swings) ? overlays.swings : []) {
      const value = swing.price !== undefined ? swing.price : swing.level;
      add(value, {
        color: OVERLAY_COLORS.swings,
        title: swing.kind === "high" ? "swing high" : "swing low",
        style: 1,
      });
    }
  }

  if (prefs.planLevels !== false && plan) {
    const entry = plan.entry ? plan.entry.value : null;
    const stop = plan.stop ? plan.stop.value : null;
    const invalidation = plan.invalidation ? plan.invalidation.value : null;
    add(entry, { color: OVERLAY_COLORS.entry, title: "entry", style: 0 });
    add(stop, { color: OVERLAY_COLORS.stop, title: "protective stop", style: 0 });
    const invalidationPrice = finiteNumber(invalidation);
    const stopPrice = finiteNumber(stop);
    if (invalidationPrice !== null && invalidationPrice !== stopPrice) {
      add(invalidationPrice, { color: OVERLAY_COLORS.invalidation, title: "invalidation", style: 2 });
    }
    (Array.isArray(plan.targets) ? plan.targets : []).forEach((target, index) => {
      add(target.level ? target.level.value : null, {
        color: OVERLAY_COLORS.targets,
        title: `target ${index + 1}`,
        style: 0,
      });
    });
  }
  flush();
}
