// Offline contract probe; optional argument is an exported snapshot root.
import { pathToFileURL } from 'node:url';
import path from 'node:path';
const root = path.resolve(process.argv[2] || '.');
const { systemHealthViewModel } = await import(pathToFileURL(path.join(root, 'src/trading_assistant/web/static/js/topbar.js')));
const dashboard = {freshness: {status: 'CURRENT'}, market: {complete: true}};
for (const [name, age, recorded] of [
  ['recent control', 5, '2026-10-14T08:00:00Z'],
  ['seven days stale', 604800, '2026-10-07T08:00:00Z'],
  ['missing heartbeat', null, null],
  ['future heartbeat', -3600, '2026-10-14T09:00:00Z'],
]) {
  const forward = {status: {market_data: {data_health: 'CURRENT'}, sample: {pending_catch_up_boundaries: 0},
    runner: {status: 'IDLE', last_error: null, pending_boundaries: 0, recorded_at: recorded, heartbeat_age_seconds: age}}};
  const result = systemHealthViewModel(dashboard, forward);
  console.log(JSON.stringify({case: name, age, recorded, ...result}));
}
