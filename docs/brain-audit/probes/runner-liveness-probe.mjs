// BRAIN AUDIT probe (independent): runner liveness - can the repaired UI still show green,
// and can it go green on unproven/malformed evidence?  Read-only; no server or database.
// Usage: node runner-liveness-probe.mjs <repo-root>
import { pathToFileURL } from 'node:url';
import path from 'node:path';

const root = path.resolve(process.argv[2] || '.');
const { systemHealthViewModel } = await import(
  pathToFileURL(path.join(root, 'src/trading_assistant/web/static/js/topbar.js'))
);

const dashboard = { freshness: { status: 'CURRENT' }, market: { complete: true } };
const base = {
  status: {
    market_data: { data_health: 'CURRENT' },
    sample: { pending_catch_up_boundaries: 0 },
    runner: { status: 'IDLE', last_error: null, pending_boundaries: 0, recorded_at: '2026-10-14T08:00:00Z', heartbeat_age_seconds: 5 },
  },
};
const cases = [
  ['proven alive', { state: 'CONFIRMED_ALIVE', alive_confirmed: true, age_seconds: 5, tolerance_seconds: 360, detail: 'within tolerance' }],
  ['proven alive but age over tolerance', { state: 'CONFIRMED_ALIVE', alive_confirmed: true, age_seconds: 4000, tolerance_seconds: 360, detail: 'inconsistent' }],
  ['claims alive, flag false', { state: 'CONFIRMED_ALIVE', alive_confirmed: false, age_seconds: 5, tolerance_seconds: 360, detail: 'flag false' }],
  ['claims alive, no numbers', { state: 'CONFIRMED_ALIVE', alive_confirmed: true, detail: 'no numbers' }],
  ['stale unconfirmed', { state: 'STALE_UNCONFIRMED', alive_confirmed: false, age_seconds: 604800, tolerance_seconds: 360, detail: 'stale' }],
  ['clock inconsistent', { state: 'CLOCK_INCONSISTENT', alive_confirmed: false, age_seconds: -3600, tolerance_seconds: 360, detail: 'future' }],
  ['failed', { state: 'FAILED', alive_confirmed: false, age_seconds: 30, tolerance_seconds: 360, detail: 'error' }],
  ['stopped', { state: 'STOPPED', alive_confirmed: false, age_seconds: 30, tolerance_seconds: 360, detail: 'stopped' }],
  ['no liveness object (legacy payload)', null],
  ['bogus state', { state: 'TOTALLY_FINE', alive_confirmed: true, age_seconds: 1, tolerance_seconds: 360 }],
];

for (const [name, liveness] of cases) {
  const forward = structuredClone(base);
  if (liveness) forward.status.runner_liveness = liveness;
  const result = systemHealthViewModel(dashboard, forward);
  console.log(JSON.stringify({ case: name, healthy: result.healthy, tone: result.tone, label: result.label }));
}
