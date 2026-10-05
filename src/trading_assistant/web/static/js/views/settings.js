/**
 * Settings view: presentation-safe preferences only.
 *
 * Everything editable here lives in this browser (localStorage) and never
 * changes deterministic engine rules. Unsafe categories are listed as
 * unavailable by design, matching the backend contract.
 */

import { api } from "../api.js";
import { clearNode, DEFAULT_PREFS, el, errorState, loadPrefs, savePrefs, spinner, toast } from "../util.js";

export async function renderSettings(view) {
  clearNode(view).append(spinner("Loading settings…"));
  let settings;
  try {
    settings = await api.settings();
  } catch (error) {
    clearNode(view).append(errorState(error.message));
    return;
  }
  clearNode(view);

  const prefs = loadPrefs();
  const defaults = settings.configured_defaults || {};
  const timeframes = defaults.supported_timeframes || [];

  const symbolInput = el("input", {
    class: "input-inline",
    value: prefs.preferredSymbol || defaults.symbol || "",
    placeholder: defaults.symbol || "BTC/USDT",
    "aria-label": "Preferred symbol",
    style: { minWidth: "180px" },
  });
  const timeframeSelect = el("select", { class: "select-inline", "aria-label": "Preferred timeframe" }, [
    el("option", { value: "", text: `Backend default (${defaults.default_timeframe || "1h"})` }),
    ...timeframes.map((tf) => el("option", { value: tf, text: tf, selected: prefs.preferredTimeframe === tf || undefined })),
  ]);

  const overlayDefs = [
    ["zones", "Support / resistance zones"],
    ["range", "Detected range boundaries"],
    ["equalLevels", "Equal-high / equal-low clusters"],
    ["planLevels", "Plan entry / stop / targets"],
    ["swings", "Confirmed swing points"],
  ];

  const switchControl = (key, label, description) => {
    const button = el("button", {
      class: "switch",
      type: "button",
      role: "switch",
      "aria-checked": String(prefs.overlays[key] === true),
      "aria-label": label,
    });
    button.addEventListener("click", () => {
      const next = loadPrefs();
      next.overlays[key] = !(next.overlays[key] === true);
      savePrefs(next);
      button.setAttribute("aria-checked", String(next.overlays[key]));
    });
    return el("div", { class: "setting-row" }, [
      el("div", {}, [el("div", { class: "k", text: label }), el("div", { class: "d", text: description })]),
      button,
    ]);
  };

  const detailSelect = el(
    "select",
    {
      class: "select-inline",
      "aria-label": "Explanation detail level",
      onchange: (event) => {
        const next = loadPrefs();
        next.explanationDetail = event.target.value;
        savePrefs(next);
        toast("Explanation detail preference saved in this browser.", "green");
      },
    },
    [
      ["standard", "Standard — sections visible"],
      ["expanded", "Expanded — same as standard"],
      ["compact", "Compact — sections collapsed"],
    ].map(([value, text]) => el("option", { value, text, selected: prefs.explanationDetail === value || undefined })),
  );

  view.append(
    el("div", { class: "settings-grid" }, [
      el("div", { class: "card" }, [
        el("h3", { class: "card-title", text: "Presentation preferences" }),
        el("div", { class: "chart-note", text: settings.presentation_storage_note }),
        el("div", { class: "setting-row" }, [
          el("div", {}, [
            el("div", { class: "k", text: "Preferred symbol" }),
            el("div", { class: "d", text: "The dashboard opens with this symbol. Backend default: " + (defaults.symbol || "UNKNOWN") + ". Any exchange symbol string is accepted; data appears only when candles are stored." }),
          ]),
          symbolInput,
        ]),
        el("div", { class: "setting-row" }, [
          el("div", {}, [
            el("div", { class: "k", text: "Preferred timeframe" }),
            el("div", { class: "d", text: "Restricted to the configured supported timeframes." }),
          ]),
          timeframeSelect,
        ]),
        el("div", { class: "setting-row" }, [
          el("div", {}, [
            el("div", { class: "k", text: "Explanation detail" }),
            el("div", { class: "d", text: "How Step 9 explanation sections are presented." }),
          ]),
          detailSelect,
        ]),
        el("div", { class: "modal-actions", style: { justifyContent: "flex-start" } }, [
          el("button", {
            class: "btn btn-primary",
            type: "button",
            text: "Save preferences",
            onclick: () => {
              const next = loadPrefs();
              next.preferredSymbol = symbolInput.value.trim();
              next.preferredTimeframe = timeframeSelect.value;
              savePrefs(next);
              toast("Preferences saved in this browser.", "green");
            },
          }),
          el("button", {
            class: "btn btn-quiet",
            type: "button",
            text: "Reset to defaults",
            onclick: () => {
              savePrefs(structuredClone(DEFAULT_PREFS));
              toast("Preferences reset.", "neutral");
              renderSettings(view);
            },
          }),
        ]),
      ]),
      el("div", { class: "card" }, [
        el("h3", { class: "card-title", text: "Chart overlays" }),
        ...overlayDefs.map(([key, label]) =>
          switchControl(key, label, "Draws the engine's stored level on the qualification-timeframe chart when present."),
        ),
      ]),
    ]),
    el("div", { class: "card" }, [
      el("h3", { class: "card-title", text: "Configured backend defaults (read-only)" }),
      el("div", { class: "kv", style: { marginTop: "8px" } }, [
        el("span", { class: "k", text: "symbol" }), el("span", { class: "v mono", text: defaults.symbol || "UNKNOWN" }),
        el("span", { class: "k", text: "exchange" }), el("span", { class: "v mono", text: defaults.exchange || "UNKNOWN" }),
        el("span", { class: "k", text: "default timeframe" }), el("span", { class: "v mono", text: defaults.default_timeframe || "UNKNOWN" }),
        el("span", { class: "k", text: "supported timeframes" }), el("span", { class: "v mono", text: (defaults.supported_timeframes || []).join(", ") || "UNKNOWN" }),
      ]),
      el("div", { class: "chart-note", text: "These come from application configuration (environment). They are shown, not edited, from the UI." }),
    ]),
    el("div", { class: "card" }, [
      el("h3", { class: "card-title", text: "Unavailable by design" }),
      el("div", { class: "chart-note", text: settings.unavailable_note }),
      el("div", { class: "unavailable-list", style: { marginTop: "10px" } }, (settings.unavailable_by_design || []).map((item) => el("span", { class: "tag", text: item }))),
    ]),
  );
}
