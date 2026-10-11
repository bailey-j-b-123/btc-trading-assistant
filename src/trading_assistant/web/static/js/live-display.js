/**
 * Live Binance BTC/USDT last-trade price, display only.
 *
 * No candle series, engine request, stored-candle read, or persistence happens here. The quote is
 * never a closed candle and is never passed to analysis, qualification, planning, paper
 * observations or recorded signals. The badge states exactly one of three things:
 *   CURRENT      a valid quote was received recently
 *   STALE        the last valid quote is old or the latest request failed (shown, but labelled)
 *   UNAVAILABLE  no valid quote has been received
 */
import { el } from "./util.js";

export const LIVE_STALE_MS = 45000;
export const LIVE_POLL_MS = 20000;
export const LIVE_CLOCK_MS = 5000;

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

/** Pure text for the widget, so the labels are testable without a DOM. */
export function liveQuoteView(model, { checking = false } = {}) {
  if (checking && model.status === "UNAVAILABLE" && !model.fetchedAt) {
    return { status: "CHECKING", value: "—", note: "Checking the Binance Spot public ticker…" };
  }
  const stamp = model.fetchedAt ? new Date(model.fetchedAt).toISOString().replace("T", " ").slice(0, 19) : null;
  const money = model.price === null || model.price === undefined ? null
    : `$${model.price.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  if (model.status === "CURRENT") {
    return {
      status: "CURRENT",
      value: money,
      note: `Binance Spot last trade · received ${stamp} UTC · refreshes every ${LIVE_POLL_MS / 1000} s · display only`,
    };
  }
  if (model.status === "STALE") {
    return {
      status: "STALE",
      value: money,
      note: stamp
        ? `Last valid quote ${stamp} UTC · not current; reconnecting · display only`
        : "Last valid quote is not current · display only",
    };
  }
  return {
    status: "UNAVAILABLE",
    value: "—",
    note: "No valid Binance Spot quote is available. The confirmed chart and analysis are unchanged.",
  };
}

export function mountLiveDisplay(symbol) {
  const badge = el("span", { class: "live-badge", dataset: { status: "CHECKING" }, text: "CHECKING" });
  const node = el("section", { class: "live-quote", role: "status", "aria-label": "Live Binance BTC/USDT price, display only" });
  const label = el("div", { class: "live-quote-kicker" }, [
    el("span", { class: "live-quote-title", text: "LIVE PRICE · BINANCE SPOT LAST TRADE" }),
    badge,
  ]);
  const value = el("strong", { class: "live-quote-value", text: "—" });
  const note = el("span", { class: "live-quote-note", text: "Checking the Binance Spot public ticker…" });
  node.append(label, value, note);
  if (symbol !== "BTC/USDT") {
    node.dataset.freshness = "UNAVAILABLE";
    badge.dataset.status = "UNAVAILABLE";
    badge.textContent = "UNAVAILABLE";
    value.textContent = "—";
    note.textContent = "Live quote is only available for BTC/USDT.";
    return { node, start() {}, destroy() {} };
  }
  let last = null;
  let stopped = false;
  let disconnected = false;
  let pending = false;
  let attempted = false;
  let timer = null;
  let staleTimer = null;
  let controller = null;
  function paint() {
    if (stopped) return;
    const model = liveQuoteModel(last);
    const status = disconnected && model.status === "CURRENT" ? "STALE" : model.status;
    const view = liveQuoteView({ ...model, status }, { checking: !attempted });
    node.dataset.freshness = view.status;
    badge.dataset.status = view.status;
    badge.textContent = view.status;
    value.textContent = view.value ?? "—";
    value.dataset.stale = String(view.status === "STALE");
    note.textContent = view.note;
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
      // Invalid or unavailable responses never overwrite the last valid quote.
      if (["CURRENT", "STALE"].includes(liveQuoteModel(payload).status)) {
        last = payload;
        disconnected = false;
      } else {
        disconnected = true;
      }
    } catch {
      // No fallback source and no fabricated ticks: an old quote is labelled STALE from now on.
      disconnected = true;
    } finally {
      attempted = true;
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
      staleTimer = setInterval(paint, LIVE_CLOCK_MS);
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
