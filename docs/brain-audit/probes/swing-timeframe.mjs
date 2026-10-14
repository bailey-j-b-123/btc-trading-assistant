import {pathToFileURL} from 'node:url';
import path from 'node:path';
const root = path.resolve(process.argv[2] || '.');
const {functionPanelModel} = await import(pathToFileURL(path.join(root, 'src/trading_assistant/web/static/js/function_panel.js')));
const marketState = {available: true, timeframe: '1h', trend: {direction: 'bullish', reason: 'higher_highs_and_higher_lows'}};
for (const [name, evidence] of [
  ['15m bearish versus 1h bullish', {available: true, timeframe: '15m', swings: [], trend: {direction: 'bearish', reason: 'lower_highs_and_lower_lows'}}],
  ['15m unavailable versus 1h bullish', {available: false, timeframe: '15m', reason: 'no stored candles', swings: []}],
]) {
 const model = functionPanelModel({functionId: 'swings', evidence, bands: [], marketState});
 console.log(JSON.stringify({case: name, chip: model.chip, facts: model.facts}));
}
