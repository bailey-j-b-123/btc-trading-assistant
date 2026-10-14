import fs from 'node:fs';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
const root = path.resolve(process.argv[2] || '.');
const {breakoutStages} = await import(pathToFileURL(path.join(root, 'src/trading_assistant/web/static/js/breakout_stages.js')));
const payload = JSON.parse(fs.readFileSync(new URL('../evidence/retest-detector-input.json', import.meta.url)));
const result = breakoutStages(payload);
const records = payload.evidence.retests.filter(r => r.breakout_id === result.breakoutId);
console.log(JSON.stringify({provenance: payload.provenance, records, renderedRetest: result.stages[1]}, null, 2));
