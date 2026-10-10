/** Public quote display only. No candle series, engine request, or persistence. */
import { el } from "./util.js";

export const LIVE_STALE_MS = 45000;
export const LIVE_POLL_MS = 20000;

export function liveQuoteModel(payload, now = Date.now()) {
  const stamp = Date.parse(payload?.fetched_at);
  const price = payload?.price;
  const numeric = typeof price === "string" && price.trim() ? Number(price) : NaN;
  const valid = payload?.exchange === "binance" && Number.isFinite(numeric) &&
    numeric > 0 && Number.isFinite(stamp) && stamp <= now + 5000;
  const stale = valid && (payload?.status === "STALE" || now - stamp >= LIVE_STALE_MS);
  return {
    status: valid ? (stale ? "STALE" : payload?.status === "CURRENT" ? "CURRENT" : "UNAVAILABLE") : "UNAVAILABLE",
    price: valid ? numeric : null,
    fetchedAt: valid ? payload.fetched_at : null,
  };
}

export function mountLiveDisplay(symbol) {
  const node = el("div", { class: "live-quote", role: "status", "aria-label": "Live price display only" });
  const label = el("div", { class: "verdict-kicker", text: "LIVE PRICE · LAST TRADE · DISPLAY ONLY" });
  const value = el("strong", { class: "live-quote-value", text: "LIVE DATA UNAVAILABLE" });
  const freshness = el("span", { class: "chart-note", text: "Binance Spot public quote · no forming candles are shown" });
  node.append(label, value, freshness);
  if (symbol !== "BTC/USDT") {
    freshness.textContent = "Live quote only available for BTC/USDT";
    return { node, start() {}, destroy() {} };
  }
  let last = null;
  let stopped = false;
  let disconnected = false;
  let pending = false;
  let timer = null;
  let staleTimer = null;
  let controller = null;
  function paint() {
    if (stopped) return;
    const model = liveQuoteModel(last);
    const status = disconnected && model.status === "CURRENT" ? "STALE" : model.status;
    node.dataset.freshness = status;
    value.textContent = status === "CURRENT"
      ? `$${model.price.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
      : `LIVE DATA ${status}`;
    freshness.textContent = model.fetchedAt
      ? `Last valid fetch: ${new Date(model.fetchedAt).toISOString().replace("T", " ").slice(0, 19)} UTC · Binance Spot public ticker · no forming candles shown`
      : "Binance Spot public ticker unavailable · confirmed chart unchanged";
  }
  async function poll() {
    if (stopped || pending) return;
    pending = true;
    controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 5000);
    try {
      const response = await fetch("/api/market/live-price", { signal: controller.signal, headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error("Quote unavailable");
      const payload = await response.json();
      // Invalid/unavailable responses do not overwrite the last valid quote.
      if (["CURRENT", "STALE"].includes(liveQuoteModel(payload).status)) {
        last = payload;
        disconnected = false;
      } else disconnected = true;
    } catch {
      // No fallback source, no fabricated ticks; mark an old quote stale now.
      disconnected = true;
    } finally {
      clearTimeout(timeout);
      pending = false;
      controller = null;
      paint();
    }
  }
  return {
    node,
    start() {
      paint();
      void poll();
      timer = setInterval(() => { void poll(); }, LIVE_POLL_MS);
      staleTimer = setInterval(paint, 5000);
      timer.unref?.();
      staleTimer.unref?.();
    },
    destroy() {
      stopped = true;
      controller?.abort();
      clearInterval(timer);
      clearInterval(staleTimer);
    },
  };
}
