import fs from 'node:fs';
import { loadBars, saveBars, cachedDailyBars, _setFetch } from './victor.mjs';

const casos = JSON.parse(fs.readFileSync(process.env.BS_CASOS, 'utf8'));
const out = [];
const B = (n, base=100) => Array.from({length:n}, (_,i) => ({
  time: new Date(Date.UTC(2026,6,1+i)).toISOString().slice(0,10),
  high: base+i+1, low: base+i-1, close: base+i }));

for (const c of casos) {
  const r = { caso: c.nombre };
  fs.rmSync(process.env.BS_DIR, { recursive: true, force: true });   // mismo aislamiento que el port
  fs.mkdirSync(process.env.BS_DIR, { recursive: true });
  try {
    // estado inicial
    if (c.pre) { await saveBars(c.pre.ticker, B(c.pre.n), new Date(c.pre.now)); }
    if (c.raw !== undefined) {
      fs.mkdirSync(process.env.BS_DIR + '/bars', { recursive: true });
      fs.writeFileSync(process.env.BS_DIR + '/bars/' + c.rawFile, c.raw, 'utf8');
    }
    if (c.op === 'load') {
      const v = await loadBars(c.ticker);
      r.res = v === null ? null : { ticker: v.ticker, date: v.date, n: (v.bars||[]).length };
    } else if (c.op === 'save') {
      await saveBars(c.ticker, B(c.n ?? 3), new Date(c.now));
      const v = await loadBars(c.ticker);
      r.res = v === null ? null : { ticker: v.ticker, date: v.date, n: (v.bars||[]).length };
      r.archivos = fs.existsSync(process.env.BS_DIR+'/bars')
        ? fs.readdirSync(process.env.BS_DIR+'/bars').filter(f=>!f.endsWith('.lock')).sort() : [];
    } else if (c.op === 'cached') {
      let pedidos = [];
      _setFetch(async (t, d) => { pedidos.push([t,d]); if (c.falla) throw new Error('red'); return B(c.n ?? 3); });
      const bars = await cachedDailyBars(c.ticker, c.days ?? 365, new Date(c.now));
      r.res = { n: bars.length, pedidos };
    }
  } catch (e) { r.res = { ERROR: e.constructor.name }; }
  out.push(r);
}
fs.writeFileSync(process.env.BS_OUT, JSON.stringify(out, null, 0));
console.log('  Víctor procesó', out.length, 'casos');
