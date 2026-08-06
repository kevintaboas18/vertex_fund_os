// Lado Víctor del diferencial de `store.ts`: corre SU archivo, tal cual, en Node.
//
// `_diffstore_victor.mjs` lo genera `diff_store.sh` desde su .ts quitando solo
// los tipos. Aquí no hay lógica portada: solo se le dan los casos y se anota lo
// que devuelve.
//
// Convención: `undefined` se normaliza a `null` antes de serializar. Python no
// tiene `undefined`, así que sin esto `JSON.stringify` se comería las claves y
// el diff marcaría diferencias que solo son de lenguaje.
import fs from 'node:fs';
import { loadTrades, saveTrades } from './_diffstore_victor.mjs';

const casos = JSON.parse(fs.readFileSync(process.env.ST_CASOS, 'utf8'));
const D = process.env.ST_DIR;
const U = v => (v === undefined ? null : v);

const T = (d, h = 15, m = 0) =>
  `2026-07-${String(d).padStart(2, '0')}T${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:00Z`;
const lote = n => Array.from({ length: n }, (_, i) => ({
  id: i + 1, timestamp: T(1 + (i % 30), 10, i % 60), symbol: 'X', type: 'call',
  strike: 100, expiration: '2026-12-18', assetPrice: 95, premium: 5e5,
  aggression: 'ask', expiry_status: 'vigente',
}));

// Forma del `updatedAt`, no su valor: es la hora de pared y nunca coincidiría.
const forma = v => (typeof v === 'string'
  ? (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(v) ? 'ISO_MS_Z' : 'otro:' + v)
  : U(v));

const out = [];
for (const c of casos) {
  fs.rmSync(D, { recursive: true, force: true });
  fs.mkdirSync(D + '/trades', { recursive: true });
  const r = { caso: c.nombre };
  try {
    if (c.raw !== undefined) fs.writeFileSync(D + '/trades/' + c.rawFile, c.raw, 'utf8');
    if (c.op === 'load') {
      const v = await loadTrades(c.ticker);
      r.res = v === null || v === undefined ? null : {
        ticker: U(v.ticker),
        updatedAt: U(v.updatedAt),
        n: Array.isArray(v.trades) ? v.trades.length : null,
        ids: Array.isArray(v.trades) ? v.trades.map(t => U(t && t.id)) : null,
      };
    } else {
      const rows = c.op === 'saveN' ? lote(c.n) : c.rows;
      const s1 = await saveTrades(c.ticker, rows);
      let s2 = null;
      if (c.op === 'save2') s2 = await saveTrades(c.ticker, c.rows2);
      const v = await loadTrades(c.ticker);
      const res = s => s && { total: s.total, added: s.added, firstSeen: U(s.firstSeen) };
      r.res = {
        s1: res(s1), s2: res(s2),
        ticker: v && U(v.ticker),
        updatedAt: v && forma(v.updatedAt),
        ids: v && v.trades.slice(0, 8).map(t => U(t && t.id)),
        n: v && v.trades.length,
        // El campo que se recalcula cada corrida: comprueba que el análisis más
        // reciente pisa al viejo.
        estado: v && U(v.trades.filter(t => t && t.id === 1).map(t => t.expiry_status)[0]),
      };
      r.archivos = fs.readdirSync(D + '/trades').filter(f => !f.endsWith('.lock')).sort();
      r.claves = r.archivos.length
        ? Object.keys(JSON.parse(fs.readFileSync(D + '/trades/' + r.archivos[0], 'utf8'))).sort()
        : null;
    }
  } catch (e) {
    r.res = { ERROR: e.constructor.name };
  }
  out.push(r);
}
fs.writeFileSync(process.env.ST_OUT, JSON.stringify(out));
console.log('  Víctor procesó', out.length, 'casos');
