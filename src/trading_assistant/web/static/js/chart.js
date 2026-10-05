/**
 * Candlestick chart wrapper around the vendored TradingView lightweight-charts
 * library. All series data comes from backend payloads; this module only maps
 * authoritative values to visual primitives and never invents candles.
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

export function toChartCandles(candles) {
  // Backend row: [timestamp_ms, open, high, low, close, volume] (strings).
  return (candles || []).map((row) => ({
    time: Math.floor(row[0] / 1000),
    open: Number(row[1]),
    high: Number(row[2]),
    low: Number(row[3]),
    close: Number(row[4]),
  }));
}

export function createPriceChart(container, { height } = {}) {
  if (!window.LightweightCharts) return null;
  const chart = window.LightweightCharts.createChart(container, {
    layout: {
      background: { type: "solid", color: "transparent" },
      textColor: TEXT,
      fontFamily: getComputedStyle(document.documentElement).getPropertyValue("--mono") || "monospace",
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
    height: height || container.clientHeight || 380,
    autoSize: true,
  });
  const series = chart.addCandlestickSeries({
    upColor: UP,
    downColor: DOWN,
    wickUpColor: UP,
    wickDownColor: DOWN,
    borderVisible: false,
  });
  const volume = chart.addHistogramSeries({
    priceFormat: { type: "volume" },
    priceScaleId: "volume",
  });
  chart.priceScale("volume").applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } });
  return { chart, series, volume };
}

export function setCandles(handle, candles) {
  if (!handle) return;
  handle.series.setData(toChartCandles(candles));
  handle.volume.setData(
    (candles || []).map((row) => ({
      time: Math.floor(row[0] / 1000),
      value: Number(row[5]),
      color: Number(row[4]) >= Number(row[1]) ? "rgba(47,191,127,0.28)" : "rgba(224,86,91,0.28)",
    })),
  );
}

function priceLine(price, { color, title, style = 2, width = 1 }) {
  return {
    price: Number(price),
    color,
    title,
    lineWidth: width,
    lineStyle: style, // 0 solid, 1 dotted, 2 dashed, 3 large-dash, 4 sparse
    axisLabelVisible: true,
    crosshairMarkerVisible: false,
  };
}

export function clearOverlays(handle) {
  if (!handle) return;
  for (const line of handle.series.priceLines()) handle.series.removePriceLine(line);
}

/** Apply deterministic overlays; every level must exist in the payload. */
export function applyOverlays(handle, { overlays = {}, plan = null, prefs = {} }) {
  if (!handle) return;
  clearOverlays(handle);
  const show = prefs.overlays || {};

  if (show.zones !== false) {
    for (const zone of overlays.zones || []) {
      const role =
        zone.role === "support" ? "support" : zone.role === "resistance" ? "resistance" : "zone";
      for (const [bound, label] of [
        [zone.band_low, `${role} low`],
        [zone.band_high, `${role} high`],
      ]) {
        if (bound === null || bound === undefined) continue;
        handle.series.createPriceLine(
          priceLine(bound, { color: OVERLAY_COLORS.zones, title: label, style: 1 }),
        );
      }
    }
  }

  if (show.range !== false && overlays.range) {
    for (const [bound, label] of [
      [overlays.range.range_low, "range low"],
      [overlays.range.range_high, "range high"],
    ]) {
      if (bound === null || bound === undefined) continue;
      handle.series.createPriceLine(
        priceLine(bound, { color: OVERLAY_COLORS.range, title: label, style: 3, width: 1 }),
      );
    }
  }

  if (show.equalLevels !== false) {
    for (const cluster of overlays.equal_levels || []) {
      if (cluster.level === null || cluster.level === undefined) continue;
      handle.series.createPriceLine(
        priceLine(cluster.level, {
          color: OVERLAY_COLORS.equalLevels,
          title: cluster.type === "equal_high" ? "equal highs" : "equal lows",
          style: 1,
        }),
      );
    }
  }

  if (show.swings === true) {
    for (const swing of overlays.swings || []) {
      const price = swing.price !== undefined ? swing.price : swing.level;
      if (price === null || price === undefined) continue;
      handle.series.createPriceLine(
        priceLine(price, {
          color: OVERLAY_COLORS.swings,
          title: swing.kind === "high" ? "swing H" : "swing L",
          style: 1,
        }),
      );
    }
  }

  if (show.planLevels !== false && plan) {
    const entry = plan.entry ? plan.entry.value : null;
    const stop = plan.stop ? plan.stop.value : null;
    const invalidation = plan.invalidation ? plan.invalidation.value : null;
    if (entry !== null && entry !== undefined) {
      handle.series.createPriceLine(
        priceLine(entry, { color: OVERLAY_COLORS.entry, title: "entry", style: 0, width: 1 }),
      );
    }
    if (stop !== null && stop !== undefined) {
      handle.series.createPriceLine(
        priceLine(stop, { color: OVERLAY_COLORS.stop, title: "protective stop", style: 0 }),
      );
    }
    if (
      invalidation !== null &&
      invalidation !== undefined &&
      String(invalidation) !== String(stop)
    ) {
      handle.series.createPriceLine(
        priceLine(invalidation, { color: OVERLAY_COLORS.invalidation, title: "invalidation", style: 2 }),
      );
    }
    (plan.targets || []).forEach((target, index) => {
      const level = target.level ? target.level.value : null;
      if (level === null || level === undefined) return;
      handle.series.createPriceLine(
        priceLine(level, { color: OVERLAY_COLORS.targets, title: `target ${index + 1}`, style: 0 }),
      );
    });
  }
}
