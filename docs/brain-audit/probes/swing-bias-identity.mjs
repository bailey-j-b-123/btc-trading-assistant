// BRAIN AUDIT probe: can the Market Structure bias panel borrow another timeframe's trend?
// Uses the real production function; no server, database or network.
// Usage: node swing-bias-identity.mjs <repo-root>
import { pathToFileURL } from 'node:url';
import path from 'node:path';

const root = path.resolve(process.argv[2] || '.');
const { functionPanelModel } = await import(
  pathToFileURL(path.join(root, 'src/trading_assistant/web/static/js/function_panel.js'))
);

const engine1h = { available: true, timeframe: '1h', trend: { direction: 'bullish', reason: 'higher_highs_and_higher_lows' } };
const engine15m = { available: true, timeframe: '15m', trend: { direction: 'bullish', reason: 'higher_highs_and_higher_lows' } };

const cases = [
  ['A viewed 15m trend believable over 1h engine', { available: true, timeframe: '15m', swings: [], trend: { direction: 'bearish', reason: 'lower_highs_and_lower_lows' } }, engine1h],
  ['B viewed 15m available, no trend, engine is 1h', { available: true, timeframe: '15m', swings: [] }, engine1h],
  ['C viewed 15m available, no trend, engine is 15m', { available: true, timeframe: '15m', swings: [] }, engine15m],
  ['D viewed 15m unavailable, engine 1h bullish', { available: false, timeframe: '15m', reason: 'no stored candles', swings: [] }, engine1h],
  ['E viewed 15m unavailable, engine 15m bullish', { available: false, timeframe: '15m', reason: 'no stored candles', swings: [] }, engine15m],
  ['F viewed evidence has no timeframe, engine 1h', { available: true, swings: [] }, engine1h],
  ['G malformed direction "sideways", engine 1h', { available: true, timeframe: '15m', swings: [], trend: { direction: 'sideways', reason: 'x' } }, engine1h],
  ['H trend present but no reason, engine 1h', { available: true, timeframe: '15m', swings: [], trend: { direction: 'bearish' } }, engine1h],
  ['I engine itself unavailable, viewed available no trend', { available: true, timeframe: '15m', swings: [] }, { available: false, timeframe: '1h' }],
  ['J marketState missing entirely', { available: true, timeframe: '15m', swings: [] }, null],
];

for (const [name, evidence, marketState] of cases) {
  const model = functionPanelModel({ functionId: 'swings', evidence, bands: [], marketState: marketState || undefined });
  const bias = (model.facts || []).find((f) => f.label === 'Current bias');
  console.log(JSON.stringify({ case: name, chip: model.chip, bias: bias ? bias.value : null }));
}
