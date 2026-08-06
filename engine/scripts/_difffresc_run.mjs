// `recencyFactor` de SU levels.ts, evaluado en V8 sobre la rejilla de casos.
import fs from 'node:fs';
import { recencyFactor } from './rf.mjs';

const casos = JSON.parse(fs.readFileSync(process.env.FR_CASOS, 'utf8'));
const out = casos.map(c => recencyFactor(c.last_touch, new Date(c.now)));
fs.writeFileSync(process.env.FR_OUT, JSON.stringify(out));
console.log(`  Víctor evaluó ${out.length} casos`);
