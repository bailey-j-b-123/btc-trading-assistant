/**
 * Browser entry point. The default experience is the single trading dashboard;
 * the older read-only views remain available to existing direct links.
 */

import { disposeDashboard, renderDashboard } from "./views/dashboard.js";
import { renderJournal } from "./views/journal.js";
import { renderLive } from "./views/live.js";
import { renderSettings } from "./views/settings.js";
import { renderStatistics } from "./views/statistics.js";
import { renderValidation } from "./views/validation.js";

const ROUTES = {
  dashboard: (view) => renderDashboard(view),
  journal: (view, parts) => {
    const journalId = parts[1] ? decodeURIComponent(parts[1]) : null;
    return renderJournal(view, { journalId });
  },
  live: (view) => renderLive(view),
  statistics: (view) => renderStatistics(view),
  validation: (view) => renderValidation(view),
  settings: (view) => renderSettings(view),
};

const TITLES = {
  dashboard: "BTC/USDT",
  journal: "Journal",
  live: "Live / Paper",
  statistics: "Statistics",
  validation: "Historical Validation",
  settings: "Settings",
};

function currentRoute() {
  const hash = location.hash || "#/dashboard";
  const parts = hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  const name = parts[0] && ROUTES[parts[0]] ? parts[0] : "dashboard";
  return { name, parts };
}

function render() {
  const { name, parts } = currentRoute();
  const view = document.getElementById("view");
  if (!view) return;

  disposeDashboard();
  view.className = "view";
  document.title = `Trading Assistant — ${TITLES[name] || TITLES.dashboard}`;
  Promise.resolve(ROUTES[name](view, parts)).catch((error) => {
    view.textContent = "";
    const block = document.createElement("div");
    block.className = "state-block";
    const big = document.createElement("div");
    big.className = "big";
    big.textContent = "View failed to load";
    const detail = document.createElement("div");
    detail.textContent = error instanceof Error ? error.message : String(error);
    block.append(big, detail);
    view.append(block);
  });
  view.focus({ preventScroll: true });
  window.scrollTo({ top: 0 });
}

window.addEventListener("hashchange", render);
window.addEventListener("DOMContentLoaded", () => {
  const refresh = document.getElementById("refresh-dashboard");
  refresh?.addEventListener("click", render);
  render();
});
