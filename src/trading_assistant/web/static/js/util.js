/**
 * DOM utilities. Every text path goes through textContent — innerHTML is
 * never used for data, so untrusted journal notes and explanation text
 * cannot inject markup.
 */

export function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === null || value === undefined) continue;
    if (key === "class") node.className = value;
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (key === "text") node.textContent = value;
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
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
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
const PREFS_KEY = "ta.presentation.v1";

export const DEFAULT_PREFS = Object.freeze({
  preferredSymbol: "",
  preferredTimeframe: "",
  overlays: Object.freeze({
    zones: true,
    range: true,
    equalLevels: true,
    planLevels: true,
    swings: false,
  }),
  explanationDetail: "standard", // "standard" | "expanded" | "compact"
});

export function loadPrefs() {
  try {
    const raw = localStorage.getItem(PREFS_KEY);
    if (!raw) return structuredClone(DEFAULT_PREFS);
    const parsed = JSON.parse(raw);
    return {
      ...structuredClone(DEFAULT_PREFS),
      ...parsed,
      overlays: { ...DEFAULT_PREFS.overlays, ...(parsed.overlays || {}) },
    };
  } catch {
    return structuredClone(DEFAULT_PREFS);
  }
}

export function savePrefs(prefs) {
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify(prefs));
  } catch {
    /* storage unavailable: presentation prefs simply do not persist */
  }
}
