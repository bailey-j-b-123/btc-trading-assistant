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

// Approved dashboard design: a trade plan reads blue entry, red stop / risk and
// green targets / reward. These are display colours only — the plan's own levels
// and the engine that produced them are untouched.
export const OVERLAY_COLORS = {
  zones: "#5b9bd5",
  range: "#b58cf0",
  equalLevels: "#e8a33d",
  entry: "#5b9bd5",
  stop: "#e0565b",
  invalidation: "#e8a33d",
  targets: "#2fbf7f",
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
/** Price-axis label: two decimals with thousands separators (e.g. 89,000.00). Display only. */
export function formatPriceAxis(price) {
  const value = Number(price);
  if (!Number.isFinite(value)) return "";
  return value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

const PRICE_AXIS_FORMAT = { type: "custom", minMove: 0.01, formatter: formatPriceAxis };

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
      priceFormat: PRICE_AXIS_FORMAT,
    });
    // Independent temporary series: never append/update the stored confirmed series.
    formingSeries = chart.addCandlestickSeries({
      upColor: "rgba(91,155,213,0.26)", downColor: "rgba(232,163,61,0.26)",
      wickUpColor: "rgba(91,155,213,0.85)", wickDownColor: "rgba(232,163,61,0.85)",
      borderVisible: true, borderUpColor: "#5b9bd5", borderDownColor: "#e8a33d",
      priceLineVisible: false, lastValueVisible: false,
      priceFormat: PRICE_AXIS_FORMAT,
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
    breakoutAnnoLayer: null,
    breakoutAnnoState: null,
    annoUnsubscribers: [],
    // Raw loaded rows (ascending). Older pages are merged into these, never replaced.
    loadedRows: [],
    historyUnsubscribe: null,
    destroyed: false,
  };
  const resize = () => {
    if (handle.destroyed) return;
    const width = Number(container.clientWidth) || 0;
    const nextHeight = Number(container.clientHeight) || 0;
    if (width > 0 && nextHeight > 0) chart.resize(width, nextHeight);
    renderZoneBands(handle);
    renderBreakoutAnnotations(handle);
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
/** Empty space kept to the right of the newest candle, on first load and when restoring a window. */
export const DEFAULT_EDGE_MARGIN = 3;

function defaultVisibleCandles() {
  const matches = (query) => typeof globalThis.matchMedia === "function" && globalThis.matchMedia(query).matches;
  if (matches("(max-width: 640px)")) return DEFAULT_VISIBLE_CANDLES_NARROW;
  if (matches("(max-width: 1040px)")) return DEFAULT_VISIBLE_CANDLES_TABLET;
  return DEFAULT_VISIBLE_CANDLES;
}

export function setCandles(handle, payload) {
  if (!handle || handle.destroyed) return;
  const rows = rowsFromPayload(payload);
  handle.loadedRows = rows;
  const candles = toChartCandles(rows);
  // Bar count of the series, so a refresh can keep the same right-edge offset (see dashboard view).
  handle.barCount = candles.length;
  handle.lastBarTime = candles.length ? candles[candles.length - 1].time : null;
  handle.series.setData(candles);
  // Apply the default view once per chart: later refreshes must not reset a
  // zoom or pan the user has made.
  if (!handle.initialRangeApplied && candles.length > 0 && handle.chart && typeof handle.chart.timeScale === "function") {
    const timeScale = handle.chart.timeScale();
    if (typeof timeScale.setVisibleLogicalRange === "function") {
      const last = candles.length - 1;
      const visible = defaultVisibleCandles();
      timeScale.setVisibleLogicalRange({ from: Math.max(0, last - visible + 1), to: last + DEFAULT_EDGE_MARGIN });
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

/**
 * Replace the loaded rows after older candles were prepended.
 *
 * The visible logical range is index-based, so prepending `added` candles shifts every visible
 * index by `added`. Shifting the range by the same amount keeps the same candles on screen: the
 * user's zoom, pan and place are preserved.
 */
export function setCandlesPreservingView(handle, rows, added) {
  if (!handle || handle.destroyed) return;
  const timeScale = handle.chart && typeof handle.chart.timeScale === "function" ? handle.chart.timeScale() : null;
  const before = timeScale && typeof timeScale.getVisibleLogicalRange === "function"
    ? timeScale.getVisibleLogicalRange()
    : null;
  setCandles(handle, rows);
  if (before && added > 0 && timeScale && typeof timeScale.setVisibleLogicalRange === "function") {
    timeScale.setVisibleLogicalRange({ from: before.from + added, to: before.to + added });
  }
}

/**
 * The viewed range in time, so a data refresh can put back the same zoom and place.
 *
 * Time, not bar index, is used: a rolling window shifts every index when a new candle arrives. A
 * view at the live edge (right side at or past the newest candle) keeps following it with the same
 * right margin. A view in history stays on the same instants.
 */
export function captureChartView(handle) {
  const timeScale = handle && !handle.destroyed && handle.chart && typeof handle.chart.timeScale === "function"
    ? handle.chart.timeScale()
    : null;
  if (!timeScale || typeof timeScale.getVisibleRange !== "function") return null;
  const range = timeScale.getVisibleRange();
  const lastBar = Number.isFinite(handle.lastBarTime) ? handle.lastBarTime : null;
  if (!range || !Number.isFinite(range.from) || !Number.isFinite(range.to) || lastBar === null) return null;
  return { from: range.from, to: range.to, lastBar, atEdge: range.to >= lastBar };
}

/** Put back a view captured by captureChartView, after setCandles has loaded the new data. */
export function restoreChartView(handle, captured) {
  const timeScale = handle && !handle.destroyed && handle.chart && typeof handle.chart.timeScale === "function"
    ? handle.chart.timeScale()
    : null;
  if (!captured || !timeScale || typeof timeScale.setVisibleRange !== "function") return;
  const newLast = Number.isFinite(handle.lastBarTime) ? handle.lastBarTime : null;
  if (newLast === null) return;
  if (captured.atEdge) {
    const width = captured.to - captured.from;
    const to = newLast + (captured.to - captured.lastBar);
    timeScale.setVisibleRange({ from: to - width, to });
    return;
  }
  timeScale.setVisibleRange({ from: captured.from, to: captured.to });
}

/**
 * The visible index window, expressed the only way that survives a change of series length:
 * the number of candles on screen (the zoom) and how far the right edge sits from the newest
 * loaded candle (the place).
 *
 * A timeframe switch loads a different time base, so the same instants no longer mean the same
 * candles, and the candle count changes too (a 1H view may hold 500 candles where the 5M view
 * held 200). Absolute indices therefore cannot be carried across: they would leave the operator
 * staring at ancient history after a switch. The width and the distance from the live edge can.
 * Used only for timeframe changes; refreshes use captureChartView.
 */
export function captureLogicalView(handle) {
  const timeScale = handle && !handle.destroyed && handle.chart && typeof handle.chart.timeScale === "function"
    ? handle.chart.timeScale()
    : null;
  if (!timeScale || typeof timeScale.getVisibleLogicalRange !== "function") return null;
  const bars = Number.isFinite(handle.barCount) ? handle.barCount : 0;
  if (bars <= 0) return null;
  const range = timeScale.getVisibleLogicalRange();
  if (!range || !Number.isFinite(range.from) || !Number.isFinite(range.to) || range.to <= range.from) return null;
  return {
    width: range.to - range.from,
    // Positive at the live edge (the usual case), larger when the window is panned into history.
    edgeOffset: range.to - (bars - 1),
  };
}

/**
 * Put back a window captured by captureLogicalView on the series that is loaded now.
 *
 * The same zoom (candle count) and the same distance from the newest candle are restored, then
 * clamped to what exists: the window never starts before the first candle and never runs more
 * than a small margin past the newest one, exactly like the default view.
 */
export function restoreLogicalView(handle, captured) {
  const timeScale = handle && !handle.destroyed && handle.chart && typeof handle.chart.timeScale === "function"
    ? handle.chart.timeScale()
    : null;
  if (!captured || !timeScale || typeof timeScale.setVisibleLogicalRange !== "function") return;
  const bars = Number.isFinite(handle.barCount) ? handle.barCount : 0;
  if (bars <= 0) return;
  const width = Math.min(Math.max(Number(captured.width) || 0, 1), bars);
  const lastIndex = bars - 1;
  const edgeOffset = Number.isFinite(Number(captured.edgeOffset)) ? Number(captured.edgeOffset) : 1;
  const maxTo = lastIndex + DEFAULT_EDGE_MARGIN;
  let to = Math.min(lastIndex + edgeOffset, maxTo);
  let from = to - width;
  if (from < 0) {
    from = 0;
    to = Math.min(width, maxTo);
  }
  timeScale.setVisibleLogicalRange({ from, to });
}

/**
 * The visible-range API of a live chart, or null when there is nothing to move.
 * Kept separate from the logical-view helpers above because those carry a view
 * across a RELOAD; these move the view the operator is already looking at.
 */
function liveTimeScale(handle) {
  return handle && !handle.destroyed && handle.chart && typeof handle.chart.timeScale === "function"
    ? handle.chart.timeScale()
    : null;
}

/** Empty candles kept either side of a framed span, so the last event is not on the edge. */
export const FRAME_PAD_CANDLES = 6;

/**
 * Bring one bar into view, keeping the operator's current zoom.
 *
 * The number of candles on screen is read from the chart and put back around the
 * requested bar, so jumping to a stored event never changes how far the operator
 * is zoomed in — only where the window sits. Presentation only: no row,
 * timeframe, layer or stored value is touched.
 *
 * @returns {{from: number, to: number}|null} the applied logical range.
 */
export function focusBarOnChart(handle, index) {
  const timeScale = liveTimeScale(handle);
  if (!timeScale || typeof timeScale.setVisibleLogicalRange !== "function") return null;
  const bars = Number.isFinite(handle.barCount) ? handle.barCount : 0;
  if (bars <= 0 || !Number.isFinite(index)) return null;
  const current = typeof timeScale.getVisibleLogicalRange === "function"
    ? timeScale.getVisibleLogicalRange()
    : null;
  const measured = current && Number.isFinite(current.from) && Number.isFinite(current.to) && current.to > current.from
    ? current.to - current.from
    : null;
  const width = Math.min(Math.max(measured ?? defaultVisibleCandles(), 1), bars);
  const maxTo = bars - 1 + DEFAULT_EDGE_MARGIN;
  let from = Math.round(Number(index) - width / 2);
  let to = from + width;
  if (from < 0) {
    from = 0;
    to = Math.min(width, maxTo);
  }
  if (to > maxTo) {
    to = maxTo;
    from = Math.max(0, to - width);
  }
  const range = { from, to };
  timeScale.setVisibleLogicalRange(range);
  return range;
}

/**
 * Frame a span of bars: the one control that DOES change the zoom, because
 * fitting the stored events is exactly what was asked for. Used by the
 * Breakouts & Retests "frame events" control, never automatically.
 *
 * @returns {{from: number, to: number}|null} the applied logical range.
 */
export function frameBarsOnChart(handle, fromIndex, toIndex, { pad = FRAME_PAD_CANDLES } = {}) {
  const timeScale = liveTimeScale(handle);
  if (!timeScale || typeof timeScale.setVisibleLogicalRange !== "function") return null;
  const bars = Number.isFinite(handle.barCount) ? handle.barCount : 0;
  if (bars <= 0 || !Number.isFinite(fromIndex) || !Number.isFinite(toIndex)) return null;
  const low = Math.min(fromIndex, toIndex);
  const high = Math.max(fromIndex, toIndex);
  const margin = Number.isFinite(pad) ? Math.max(0, Math.trunc(pad)) : 0;
  const maxTo = bars - 1 + DEFAULT_EDGE_MARGIN;
  let from = Math.max(0, Math.trunc(low) - margin);
  let to = Math.min(maxTo, Math.trunc(high) + margin);
  if (to <= from) {
    from = Math.max(0, Math.min(from, maxTo - 1));
    to = Math.min(maxTo, from + 1);
  }
  const range = { from, to };
  timeScale.setVisibleLogicalRange(range);
  return range;
}

// ---------------------------------------------------------------------------
// Breakout / Retest / Continuation callouts
//
// Three numbered callouts with an arrow to the candle each step belongs to.
// They are drawn as DOM over the canvas, positioned from the chart's own
// coordinates, exactly like the zone bands above: nothing here changes a
// series, a candle or a stored value.
// ---------------------------------------------------------------------------

/**
 * Where the callouts sit, following the approved layout:
 *
 *   1 Breakout     - above the breakout candle, arrow pointing down at it
 *   2 Retest       - below and to the right of its candle, arrow back up at it
 *   3 Continuation - above the later price action, arrow pointing down at it
 *
 * Each callout is placed against the candle its step belongs to, not in a fixed
 * band across the top of the chart, so it stays visible with the candles it
 * marks. Two callouts on the same side that would collide are stepped further
 * from their candles until they clear each other.
 */
const ANNO_PLACE = Object.freeze({ above: "above", below: "below" });
/** Clear space kept between a callout and the candle(ish) point it marks. */
const ANNO_GAP = 26;
/** How far to the right the retest callout is offset from its candle. */
const ANNO_NUDGE_RIGHT = 92;
/** Extra vertical step when two callouts on the same side would overlap. */
const ANNO_STEP = 6;
/** Gap between the arrowhead and the point it marks. */
const ANNO_ARROW_GAP = 7;
/** Keep every callout this far inside the pane so nothing is clipped. */
const ANNO_EDGE_PAD = 6;

/** Two boxes overlap when they share both a horizontal and a vertical span. */
function annoOverlaps(a, b) {
  return a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
}

function createBreakoutAnnoLayer(handle) {
  const container = handle.container;
  if (typeof document === "undefined" || !container || handle.breakoutAnnoLayer || typeof container.appendChild !== "function") return;
  if (typeof globalThis.getComputedStyle === "function" && globalThis.getComputedStyle(container).position === "static") {
    container.style.position = "relative";
  }
  const layer = document.createElement("div");
  layer.className = "breakout-anno-layer";
  layer.setAttribute?.("aria-hidden", "true");
  container.appendChild(layer);
  handle.breakoutAnnoLayer = layer;
  const timeScale = handle.chart?.timeScale?.();
  const render = () => renderBreakoutAnnotations(handle);
  if (timeScale && typeof timeScale.subscribeVisibleLogicalRangeChange === "function") {
    timeScale.subscribeVisibleLogicalRangeChange(render);
    handle.annoUnsubscribers.push(() => timeScale.unsubscribeVisibleLogicalRangeChange?.(render));
  }
  if (typeof handle.chart?.subscribeCrosshairMove === "function") {
    handle.chart.subscribeCrosshairMove(render);
    handle.annoUnsubscribers.push(() => handle.chart.unsubscribeCrosshairMove?.(render));
  }
}

/**
 * Draw the three numbered callouts. `stages` come from breakout_stages.js: each
 * carries the exact candle (bar index and time) and price it belongs to, so a
 * callout is drawn only on that candle and never snapped to a neighbour.
 */
export function setBreakoutAnnotations(handle, stages, { onSelect } = {}) {
  if (!handle || handle.destroyed || !handle.container) return;
  if (typeof handle.container.appendChild !== "function" || typeof document === "undefined") return;
  createBreakoutAnnoLayer(handle);
  handle.breakoutAnnoState = {
    stages: (Array.isArray(stages) ? stages : []).filter((stage) => stage && typeof stage === "object"),
    onSelect: typeof onSelect === "function" ? onSelect : null,
  };
  renderBreakoutAnnotations(handle);
}

/** Remove every callout and its listeners. Safe to call repeatedly. */
export function clearBreakoutAnnotations(handle) {
  if (!handle) return;
  for (const unsubscribe of handle.annoUnsubscribers || []) {
    try {
      unsubscribe();
    } catch {
      // The chart may already be removed; nothing else to release.
    }
  }
  handle.annoUnsubscribers = [];
  handle.breakoutAnnoState = null;
  if (handle.breakoutAnnoLayer) {
    handle.breakoutAnnoLayer.remove?.();
    handle.breakoutAnnoLayer = null;
  }
}

/** Position every callout from the current coordinates. Pure reads; no data change. */
export function renderBreakoutAnnotations(handle) {
  if (!handle || handle.destroyed || !handle.breakoutAnnoLayer) return;
  const { series, chart } = handle;
  const timeScale = chart?.timeScale?.();
  if (!series || typeof series.priceToCoordinate !== "function" || !timeScale) return;
  if (typeof timeScale.timeToCoordinate !== "function") return;
  const layer = handle.breakoutAnnoLayer;
  const width = Number(handle.container?.clientWidth) || 0;
  const height = Number(handle.container?.clientHeight) || 0;
  const { stages, onSelect } = handle.breakoutAnnoState || { stages: [], onSelect: null };
  layer.textContent = "";
  const drawn = stages.filter((stage) => Number.isFinite(stage.barIndex) && stage.barIndex >= 0);
  let offView = 0;
  /** Boxes already placed, in layer coordinates, so the next callout can clear them. */
  const placed = [];
  drawn.forEach((stage) => {
    const x = timeScale.timeToCoordinate(stage.timeSeconds);
    const price = Number.isFinite(Number(stage.price)) ? Number(stage.price) : null;
    const y = price === null ? null : series.priceToCoordinate(price);
    if (x === null || x === undefined || y === null || y === undefined) {
      offView += 1;
      return;
    }
    if (x < 0 || x > width) {
      offView += 1;
      return;
    }
    const targetX = Math.min(Math.max(x, 2), Math.max(2, width - 2));
    const targetY = Math.min(Math.max(y, 2), Math.max(2, height - 2));
    // The retest callout sits to the right of its candle; the others sit over it.
    const nudge = stage.nudge === "right" ? ANNO_NUDGE_RIGHT : 0;
    const belowFirst = stage.place === ANNO_PLACE.below;

    const badge = document.createElement("button");
    badge.type = "button";
    badge.className = "breakout-anno";
    badge.dataset.stage = stage.key;
    badge.dataset.tone = stage.tone || "neutral";
    // An unrecorded step is drawn as an outline with a dashed leader line, so a
    // pending continuation never looks like a confirmed one.
    badge.dataset.recorded = String(stage.recorded === true);
    badge.style.setProperty("--anno-tone", "var(--blue)");
    badge.setAttribute?.("aria-label", `${stage.title}: ${stage.status}. ${stage.detail}`);
    badge.title = `${stage.title} — ${stage.status}. ${stage.detail}`;
    const numberBadge = document.createElement("span");
    numberBadge.className = "breakout-anno-num";
    numberBadge.textContent = String(stage.number);
    badge.appendChild(numberBadge);
    const title = document.createElement("span");
    title.className = "breakout-anno-title";
    title.textContent = stage.title;
    badge.appendChild(title);
    const status = document.createElement("span");
    status.className = "breakout-anno-status";
    status.textContent = stage.status;
    badge.appendChild(status);
    if (onSelect) badge.addEventListener("click", (event) => {
      event.stopPropagation();
      onSelect(stage);
    });
    // Hidden at the origin first: the real size is needed before it can be
    // placed, and getting the size wrong is how a callout ends up clipped.
    badge.style.left = "0px";
    badge.style.top = "0px";
    badge.style.visibility = "hidden";
    layer.appendChild(badge);
    const badgeW = badge.offsetWidth || 0;
    const badgeH = badge.offsetHeight || 0;

    const maxLeft = Math.max(ANNO_EDGE_PAD, width - badgeW - ANNO_EDGE_PAD);
    const badgeX = Math.min(Math.max(targetX + nudge - badgeW / 2, ANNO_EDGE_PAD), maxLeft);
    const restY = (side) => (side === ANNO_PLACE.below
      ? targetY + ANNO_GAP
      : targetY - ANNO_GAP - badgeH);

    // Prefer the side the layout gives this step; if the callout cannot fit
    // there without covering another one, use the other side.
    const sides = belowFirst ? [ANNO_PLACE.below, ANNO_PLACE.above] : [ANNO_PLACE.above, ANNO_PLACE.below];
    let side = sides[0];
    let badgeY = restY(side);
    let box = null;
    for (const attemptSide of sides) {
      let y = restY(attemptSide);
      for (let guard = 0; guard < 24; guard += 1) {
        const candidate = { left: badgeX, top: y, right: badgeX + badgeW, bottom: y + badgeH };
        const hit = placed.find((other) => annoOverlaps(candidate, other));
        if (!hit) break;
        // Step further away from the candle the callout belongs to.
        y = attemptSide === ANNO_PLACE.below
          ? hit.bottom + ANNO_STEP
          : hit.top - badgeH - ANNO_STEP;
      }
      const fits = y >= ANNO_EDGE_PAD && y + badgeH <= height - ANNO_EDGE_PAD;
      if (fits) {
        side = attemptSide;
        badgeY = y;
        box = { left: badgeX, top: y, right: badgeX + badgeW, bottom: y + badgeH };
        break;
      }
    }
    if (!box) {
      // No clear space: sit it just inside the edge on the preferred side
      // rather than dropping the callout.
      side = sides[0];
      badgeY = Math.min(Math.max(restY(side), ANNO_EDGE_PAD), Math.max(ANNO_EDGE_PAD, height - badgeH - ANNO_EDGE_PAD));
      box = { left: badgeX, top: badgeY, right: badgeX + badgeW, bottom: badgeY + badgeH };
    }
    badge.style.left = `${badgeX}px`;
    badge.style.top = `${badgeY}px`;
    badge.style.visibility = "";
    badge.dataset.place = side;
    placed.push(box);

    // The line starts on the edge of the callout facing its candle and ends
    // just short of the candle itself, with the arrowhead on the end of it.
    const startX = badgeX + badgeW / 2;
    const startY = side === ANNO_PLACE.below ? badgeY : badgeY + badgeH;
    const endY = side === ANNO_PLACE.below ? targetY + ANNO_ARROW_GAP : targetY - ANNO_ARROW_GAP;
    const endX = targetX;
    const length = Math.hypot(endX - startX, endY - startY);
    if (length > 2) {
      const angle = Math.atan2(endY - startY, endX - startX);
      const line = document.createElement("div");
      line.className = "breakout-anno-line";
      line.style.left = `${startX}px`;
      line.style.top = `${startY}px`;
      line.style.width = `${length}px`;
      line.style.transform = `rotate(${angle}rad)`;
      if (stage.recorded !== true) line.dataset.pending = "true";
      // The three callouts are blue by design; the status inside each one
      // carries the tone of what the backend recorded.
      line.style.setProperty("--anno-tone", "var(--blue)");
      layer.appendChild(line);
      const arrow = document.createElement("div");
      arrow.className = "breakout-anno-arrow";
      arrow.style.left = `${endX}px`;
      arrow.style.top = `${endY}px`;
      arrow.style.transform = `translate(-50%, -50%) rotate(${angle}rad)`;
      if (stage.recorded !== true) arrow.dataset.pending = "true";
      arrow.style.setProperty("--anno-tone", "var(--blue)");
      layer.appendChild(arrow);
    }
  });
  if (offView) {
    const note = document.createElement("div");
    note.className = "breakout-anno-offview";
    note.textContent = `${offView} callout${offView === 1 ? "" : "s"} outside the visible window`;
    layer.appendChild(note);
  }
}

/**
 * Reposition both DOM overlays (zone bands and callouts) from the current
 * coordinates. The chart library notifies its own subscribers when the operator
 * pans or zooms, but not when the view is set programmatically — so a jump or a
 * frame calls this directly instead of waiting for a notification that never
 * comes.
 */
export function redrawOverlays(handle) {
  renderZoneBands(handle);
  renderBreakoutAnnotations(handle);
}

/**
 * Call onNearStart(range) when the visible range moves close to the oldest loaded candle.
 * Only one listener per chart: a later call replaces the earlier one.
 */
export function setHistoryListener(handle, onNearStart) {
  if (!handle || handle.destroyed) return;
  handle.historyUnsubscribe?.();
  handle.historyUnsubscribe = null;
  const timeScale = handle.chart && typeof handle.chart.timeScale === "function" ? handle.chart.timeScale() : null;
  if (typeof onNearStart !== "function" || !timeScale || typeof timeScale.subscribeVisibleLogicalRangeChange !== "function") return;
  const listener = (range) => {
    if (!handle.destroyed) onNearStart(range);
  };
  timeScale.subscribeVisibleLogicalRangeChange(listener);
  handle.historyUnsubscribe = () => timeScale.unsubscribeVisibleLogicalRangeChange?.(listener);
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
  if (!handle) return;
  if (handle.setupBand) {
    handle.setupBand = null;
    renderZoneBands(handle);
  }
  if (Array.isArray(handle.planAreas) && handle.planAreas.length) {
    handle.planAreas = [];
    renderZoneBands(handle);
  }
  if (!Array.isArray(handle.priceLineHandles)) return;
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
  if (handle) {
    clearBreakoutAnnotations(handle);
  }
  if (!handle || handle.destroyed) return;
  clearOverlays(handle);
  clearEvidence(handle);
  clearZoneBands(handle);
  unsubscribeChartEvents(handle);
  handle.historyUnsubscribe?.();
  handle.historyUnsubscribe = null;
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
  // The setup reference is a faint shaded band in the zone layer, not two full-width price lines.
  const band = safePayload.scenarioBand;
  handle.setupBand = band && Number.isFinite(band.low) && Number.isFinite(band.high) &&
      band.low > 0 && band.high >= band.low
    ? { low: band.low, high: band.high }
    : null;
  // Plan risk and reward areas: the region between entry and stop (what the plan
  // risks) and between entry and the final target (what it targets). Shaded only
  // from the plan's own levels — entry stays blue, risk red, reward green, in line
  // with the approved design. No area is drawn when a level is missing.
  handle.planAreas = prefs.planLevels !== false && plan ? planAreasFor(plan) : [];
  if (handle.setupBand || handle.planAreas.length) createZoneLayer(handle);
  renderZoneBands(handle);

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
/** The setup reference (backend reference bounds, display only): a neutral, unlabelled-by-role band. */
const SETUP_BAND_STYLE = { fill: "rgba(143, 166, 255, 0.07)", edge: "#8fa6ff" };
// Plan risk / reward shading. Same faintness convention as the bands above: the
// fill marks the region, the price lines mark the exact levels.
const PLAN_AREA_STYLE = {
  risk: { fill: "rgba(224, 86, 91, 0.07)", edge: "#e0565b" },
  reward: { fill: "rgba(47, 191, 127, 0.07)", edge: "#2fbf7f" },
};

/**
 * The plan's own risk and reward regions, as {kind, low, high} rectangles.
 *
 * Risk is entry-to-stop and reward is entry-to-the-last-target, both measured
 * from the levels the plan already carries. A region with a missing or equal
 * bound is omitted rather than guessed, so a partial plan shades only what it
 * really states. Display only: nothing here changes the plan.
 */
export function planAreasFor(plan) {
  const entry = finiteNumber(plan?.entry?.value);
  if (entry === null) return [];
  const targets = Array.isArray(plan.targets) ? plan.targets : [];
  const lastTarget = targets.length ? finiteNumber(targets[targets.length - 1]?.level?.value) : null;
  const stop = finiteNumber(plan?.stop?.value);
  const area = (kind, bound) => {
    if (bound === null || bound === entry) return null;
    return { kind, low: Math.min(entry, bound), high: Math.max(entry, bound) };
  };
  return [area("risk", stop), area("reward", lastTarget)].filter(Boolean);
}
// Fills are deliberately faint: candles must stay the focus (PR #38 follow-up). Edges are drawn at
// reduced strength in CSS. Higher-timeframe bands use dashed edges and no fill (see styles.css).
const ZONE_STYLE = {
  support: { fill: "rgba(46, 196, 182, 0.06)", edge: "#2ec4b6", short: "S" },
  resistance: { fill: "rgba(239, 83, 80, 0.06)", edge: "#ef5350", short: "R" },
  price_inside: { fill: "rgba(245, 166, 35, 0.08)", edge: "#f5a623", short: "IN" },
  // The band the breakout sequence is read against: a neutral blue-grey wash
  // rather than a directional colour. The step being described is the breakout,
  // so the band it broke is a reference level, not a bullish or bearish zone.
  reference: { fill: "rgba(122, 156, 196, 0.16)", edge: "#8fb4d9", short: "Z" },
};

function createZoneLayer(handle) {
  const container = handle.container;
  if (typeof document === "undefined" || !container || handle.zoneLayer || typeof container.appendChild !== "function") return;
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
  if (!handle || handle.destroyed || !handle.zoneLayer) return;
  const series = handle.series;
  if (!series || typeof series.priceToCoordinate !== "function") return;
  const layer = handle.zoneLayer;
  const height = Number(handle.container?.clientHeight) || 0;
  const { bands, onSelect } = handle.zoneBandsState || { bands: [], onSelect: undefined };
  layer.textContent = "";
  let offView = 0;
  const labels = [];
  // Plan risk / reward areas sit under the bands and their labels.
  for (const area of Array.isArray(handle.planAreas) ? handle.planAreas : []) {
    const style = PLAN_AREA_STYLE[area.kind];
    if (!style) continue;
    const yHigh = series.priceToCoordinate(area.high);
    const yLow = series.priceToCoordinate(area.low);
    if (yHigh === null || yLow === null || yHigh === undefined || yLow === undefined) continue;
    const top = Math.min(yHigh, yLow);
    const bottom = Math.max(yHigh, yLow);
    if (bottom < 0 || top > height) continue;
    const clampedTop = Math.max(0, top);
    const node = document.createElement("div");
    node.className = `plan-area plan-area--${area.kind}`;
    node.setAttribute?.("aria-hidden", "true");
    node.style.top = `${clampedTop}px`;
    node.style.height = `${Math.max(2, Math.min(height || bottom, bottom) - clampedTop)}px`;
    node.style.setProperty("--zone-fill", style.fill);
    node.style.setProperty("--zone-edge", style.edge);
    layer.appendChild(node);
  }
  if (handle.setupBand) {
    const yHigh = series.priceToCoordinate(handle.setupBand.high);
    const yLow = series.priceToCoordinate(handle.setupBand.low);
    if (yHigh !== null && yLow !== null && yHigh !== undefined && yLow !== undefined) {
      const top = Math.min(yHigh, yLow);
      const bottom = Math.max(yHigh, yLow);
      if (bottom < 0 || top > height) {
        offView += 1;
      } else {
        const clampedTop = Math.max(0, top);
        const node = document.createElement("div");
        node.className = "zone-band zone-band--setup";
        node.setAttribute?.("aria-hidden", "true");
        node.style.top = `${clampedTop}px`;
        node.style.height = `${Math.max(2, Math.min(height || bottom, bottom) - clampedTop)}px`;
        node.style.setProperty("--zone-fill", SETUP_BAND_STYLE.fill);
        node.style.setProperty("--zone-edge", SETUP_BAND_STYLE.edge);
        layer.appendChild(node);
        const caption = document.createElement("div");
        caption.className = "zone-band-setup-label";
        caption.textContent = "Setup zone";
        caption.setAttribute?.("aria-hidden", "true");
        caption.style.top = `${clampedTop + 2}px`;
        layer.appendChild(caption);
      }
    }
  }
  for (const band of bands) {
    // Position is where the band sits relative to price; the style key is the display role.
    // `reference` is set by the caller for the band a sequence is read against.
    const styleKey = band.tone === "reference"
      ? "reference"
      : band.position === "below_price"
        ? "support"
        : band.position === "above_price"
          ? "resistance"
          : "price_inside";
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
    node.className = `zone-band zone-band--${band.position || "price_inside"}${band.tone === "reference" ? " zone-band--reference" : ""}${band.htf ? " zone-band--htf" : ""}${band.faded ? " zone-band--faded" : ""}`;
    node.setAttribute?.("aria-hidden", "true");
    node.style.top = `${clampedTop}px`;
    // A stored band keeps its own thickness; a band that is a single stored
    // level is given a minimum so it still reads as a zone, not a hairline.
    const minHeight = Number.isFinite(Number(band.minHeightPx)) ? Math.max(2, Math.trunc(Number(band.minHeightPx))) : 2;
    node.style.height = `${Math.max(minHeight, clampedBottom - clampedTop)}px`;
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
    label.textContent = band.label || `${style.short}${tf ? ` ${tf}` : ""} · ${band.touch_count ?? "?"}×`;
    label.setAttribute("aria-label", band.label
      ? `${band.label} ${band.band_low} to ${band.band_high}, ${tf || "viewed timeframe"}. Open explanation.`
      : `${band.display_role || band.position} zone ${band.band_low} to ${band.band_high}, ${tf || "viewed timeframe"}, ${band.touch_count ?? "unknown"} touches. Open explanation.`);
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
