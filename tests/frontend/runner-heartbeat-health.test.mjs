import assert from 'node:assert/strict';
import test from 'node:test';
import { systemHealthViewModel, runnerDetailsViewModel } from '../../src/trading_assistant/web/static/js/topbar.js';
const dashboard = {freshness:{status:'CURRENT'}, market:{complete:true}};
function forward(state, age, confirmed) {
  const status = {
    market_data:{data_health:'CURRENT'}, sample:{pending_catch_up_boundaries:0},
    runner:{status:'IDLE', pending_boundaries:0, last_error:null, heartbeat_age_seconds:age},
  };
  if (state !== undefined) status.runner_liveness = {
    state, alive_confirmed:confirmed, heartbeat_status:'IDLE', age_seconds:age,
    tolerance_seconds:360, detail:'Server-owned review fixture', cadence_source:'policy_floor',
  };
  return {status};
}
test('stale heartbeat is not healthy even with current market and no backlog', () => {
  assert.equal(systemHealthViewModel(dashboard, forward('STALE_UNCONFIRMED',1800,false)).healthy,false);
});
test('a one-second future heartbeat is not healthy', () => {
  const payload=forward('CLOCK_INCONSISTENT',-1,false);
  payload.status.runner.recorded_at='2026-10-06T12:00:01Z';
  payload.status.runner.heartbeat_age_seconds=0; // legacy field clamps negative ages
  assert.equal(systemHealthViewModel(dashboard, payload).healthy,false);
  assert.match(runnerDetailsViewModel(payload).heartbeat,/1s ahead of server/);
});
test('absence of a server liveness verdict cannot certify health', () => {
  assert.equal(systemHealthViewModel(dashboard, forward(undefined,10,undefined)).healthy,false);
});
test('a current server-confirmed heartbeat can be healthy', () => {
  assert.equal(systemHealthViewModel(dashboard, forward('CONFIRMED_ALIVE',10,true)).healthy,true);
});
test('contradictory or malformed confirmations cannot certify health', () => {
  for (const [age,confirmed] of [[-0.000001,true],[361,true],[10,'true'],[null,true]]) {
    assert.equal(systemHealthViewModel(dashboard, forward('CONFIRMED_ALIVE',age,confirmed)).healthy,false);
  }
});
test('health consumes server evidence without reading the browser clock', () => {
  const oldNow=Date.now;
  Date.now=()=>{throw new Error('browser clock must not determine liveness');};
  try {
    assert.equal(systemHealthViewModel(dashboard, forward('CONFIRMED_ALIVE',10,true)).healthy,true);
  } finally {Date.now=oldNow;}
});
