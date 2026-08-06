// Las 5 funciones de tiempo de SUS libs, evaluadas en V8.
import fs from 'node:fs';
import { daysToExpiration, daysBetween, dteOf, recencyWeight, clusterSec } from './reloj.mjs';

const c = JSON.parse(fs.readFileSync(process.env.RJ_CASOS, 'utf8'));
const now = new Date(c.now);
// JSON no tiene NaN: viajan como texto, igual que en el lado Python.
const j = v => (typeof v === 'number' && !Number.isFinite(v))
  ? (Number.isNaN(v) ? 'NaN' : (v > 0 ? 'Inf' : '-Inf')) : v;

const out = {
  dte:  c.fechas.map(f => j(daysToExpiration(f, now))),
  heat: c.fechas.map(f => j(dteOf(f, now))),
  betw: c.pares.map(([a, b]) => j(daysBetween(a, b))),
  news: c.stamps.map(t => j(recencyWeight(t, now))),
  clus: c.stamps.map(t => j(clusterSec(t))),
};
fs.writeFileSync(process.env.RJ_OUT, JSON.stringify(out));
console.log(`  Víctor evaluó ${Object.values(out).reduce((n, v) => n + v.length, 0)} valores`);
