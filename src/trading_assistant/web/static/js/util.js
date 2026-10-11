/**
 * DOM utilities. Every text path goes through textContent — innerHTML is
 * never used for data, so untrusted journal notes and explanation text
 * cannot inject markup.
 */
import { DEFAULT_LAYERS, normalizeLayers, overlayPrefsFromLayers } from "./evidence.js";

/**
 * Realm-safe DOM-node check. `instanceof Node` fails for nodes from another
 * document, which would then be stringified into "[object HTMLDivElement]";
 * nodeType is true for any DOM node regardless of origin.
 */
function isDomNode(value) {
  if (typeof value !== "object" || value === null) return false;
  if (typeof value.nodeType === "number") return true;
  return typeof Node !== "undefined" && value instanceof Node;
}

export function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === null || value === undefined) continue;
    if (key === "class") node.className = value;
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (key === "text") {
      // A node passed as text would render as the literal string
      // "[object HTMLDivElement]". Fail fast: nodes are children, not text.
      if (isDomNode(value)) {
        throw new Error(`el(): prop "text" received a DOM node (${value.nodeName || "unknown"}); pass it as a child instead`);
      }
      node.textContent = value;
    }
    else if (key === "html") throw new Error("innerHTML assignment is forbidden");
    else if (key === "style" && typeof value === "object") Object.assign(node.style, value);
    else if (key === "value") node.value = value;
    else if (typeof value === "boolean") {
      if (value) node.setAttribute(key, "");
    } else node.setAttribute(key, String(value));
  }
  const list = Array.isArray(children) ? children : [children];
  for (const child of list) {
    if (child === null || child === undefined) continue;
    node.append(isDomNode(child) ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function clearNode(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

export function toast(message, tone = "neutral", timeout = 4200) {
  const root = document.getElementById("toast-root");
  if (!root) return;
  const node = el("div", { class: "toast", role: "status", dataset: { tone } }, [message]);
  root.append(node);
  setTimeout(() => node.remove(), timeout);
}

export function spinner(label = "Loading…") {
  return el("div", { class: "state-block", role: "status", "aria-busy": "true" }, [
    el("div", { class: "spinner", "aria-hidden": "true" }),
    el("span", { text: label }),
  ]);
}

export function emptyState(big, small) {
  return el("div", { class: "state-block" }, [
    el("div", { class: "big", text: big }),
    small ? el("div", { text: small }) : null,
  ]);
}

export function errorState(message) {
  return el("div", { class: "state-block", role: "alert" }, [
    el("div", { class: "big", text: "Something went wrong" }),
    el("div", { text: message || "The backend returned an unexpected error." }),
  ]);
}

export function openModal(contentNode, { onBackdrop = () => {} } = {}) {
  const root = document.getElementById("modal-root");
  const backdrop = el("div", { class: "modal-backdrop", role: "presentation" });
  const modal = el("div", { class: "modal", role: "dialog", "aria-modal": "true" }, [contentNode]);
  backdrop.append(modal);
  const close = () => {
    document.removeEventListener("keydown", onKey);
    backdrop.remove();
  };
  const onKey = (event) => {
    if (event.key === "Escape") close();
  };
  backdrop.addEventListener("pointerdown", (event) => {
    if (event.target === backdrop) {
      onBackdrop();
      close();
    }
  });
  document.addEventListener("keydown", onKey);
  root.append(backdrop);
  const focusable = modal.querySelector("button, [href], input, select, textarea");
  if (focusable) focusable.focus();
  return close;
}

/** Tiny reactive-ish localStorage preferences store (presentation only). */
const PREFS_KEY = "ta.presentation.v1"; // preserve symbol/timeframe while migrating chart-only defaults
export const CHART_DEFAULTS_VERSION = 3;

/**
 * Two persisted views of the same chart switches:
 *  - `layers`: the switches the layer toggles show (see evidence.js LAYER_DEFS).
 *  - `overlays`: the legacy keys chart.applyOverlays draws from, kept so its
 *    stored contract and tests stay unchanged.
 * The UI writes both through setLayerPref(), so they never disagree in practice.
 */
export const DEFAULT_PREFS = Object.freeze({
  preferredSymbol: "",
  preferredTimeframe: "",
  chartDefaultsVersion: CHART_DEFAULTS_VERSION,
  layers: Object.freeze({ ...DEFAULT_LAYERS }),
  overlays: Object.freeze({
    zones: false,
    range: false,
    equalLevels: false, // Liquidity: its own switch, never driven by a layer
    planLevels: true,
    swings: false,
  }),
  explanationDetail: "standard", // "standard" | "expanded" | "compact"
});

/** Version-2 stored overlays become layer switches, so a user's earlier choices survive. */
function legacyOverlaysToLayers(overlays) {
  const legacy = { ...DEFAULT_PREFS.overlays, ...(overlays || {}) };
  return normalizeLayers({
    levels: legacy.zones === true || legacy.range === true,
    plan: legacy.planLevels !== false,
  });
}

const OVERLAY_KEYS = ["zones", "range", "equalLevels", "planLevels", "swings"];

export function loadPrefs() {
  try {
    const raw = localStorage.getItem(PREFS_KEY);
    if (!raw) return structuredClone(DEFAULT_PREFS);
    const parsed = JSON.parse(raw);
    const stored = parsed && typeof parsed === "object" ? parsed : {};
    const current = stored.chartDefaultsVersion === CHART_DEFAULTS_VERSION && stored.layers;
    let layers;
    let overlays;
    if (current) {
      layers = normalizeLayers(stored.layers);
      // Stored overlay booleans are honoured on top of the layer-derived values.
      const storedOverlays = stored.overlays && typeof stored.overlays === "object" ? stored.overlays : {};
      overlays = { equalLevels: false, ...overlayPrefsFromLayers(layers) };
      for (const key of OVERLAY_KEYS) {
        if (typeof storedOverlays[key] === "boolean") overlays[key] = storedOverlays[key];
      }
    } else {
      if (stored.chartDefaultsVersion === 2) {
        layers = legacyOverlaysToLayers(stored.overlays);
        // Liquidity was never a layer; a version-2 choice carries over unchanged.
        overlays = { ...overlayPrefsFromLayers(layers), equalLevels: stored.overlays?.equalLevels === true };
      } else {
        // Pre-version-2 storage: only the plan-level default was carried over.
        layers = normalizeLayers({ plan: stored.overlays?.planLevels !== false });
        overlays = { equalLevels: false, ...overlayPrefsFromLayers(layers) };
      }
    }
    return {
      ...structuredClone(DEFAULT_PREFS),
      ...stored,
      chartDefaultsVersion: CHART_DEFAULTS_VERSION,
      layers,
      overlays: { ...overlays, swings: false },
    };
  } catch {
    return structuredClone(DEFAULT_PREFS);
  }
}

/** Set one chart layer and the legacy keys it drives together, then persist. Liquidity is untouched. */
export function setLayerPref(prefs, layerId, value) {
  const next = structuredClone(prefs);
  next.layers = normalizeLayers({ ...next.layers, [layerId]: value === true });
  const derived = overlayPrefsFromLayers(next.layers);
  next.overlays = { ...next.overlays, zones: derived.zones, range: derived.range, planLevels: derived.planLevels, swings: false };
  next.chartDefaultsVersion = CHART_DEFAULTS_VERSION;
  savePrefs(next);
  return next;
}

export function savePrefs(prefs) {
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify(prefs));
  } catch {
    /* storage unavailable: presentation prefs simply do not persist */
  }
}
