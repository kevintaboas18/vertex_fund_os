// Ejecuta SUS gexHeatmap.ts y news.ts, tal cual, en Node.
//
// Son los dos únicos módulos de su `web/lib` que ningún otro diferencial toca:
// los demás (occ, conditions, expectedMove, blackScholes) los llama el motor en
// cada caso de `diff_motor.sh` / `diff_motor2.sh` y ya quedan medidos ahí.
//
// Mismo montaje que los otros dos runners: quitado de tipos NATIVO de Node, así
// que solo desaparecen los tipos y ni una línea de lógica cambia.
import fs from 'node:fs';
import { gexHeatmap } from './gexHeatmap.ts';
import { decodeEntities, parseFeedDate, parseRss, companyAliases,
         mentionsCompany, recencyWeight, newsBias, flowBias,
         contradictionFlag } from './news.ts';

const C = JSON.parse(fs.readFileSync(process.env.MOTOR3_CASOS!, 'utf8'));

const r6 = (x: any) => {
  if (typeof x !== 'number') return x;
  if (!Number.isFinite(x)) return Number.isNaN(x) ? 'NaN' : (x > 0 ? 'Inf' : '-Inf');
  const y = x * 1e6;
  if (!Number.isFinite(y)) return y > 0 ? 'Inf' : '-Inf';
  return Math.round(y) / 1e6;
};

// `r6` en profundidad: `JSON.stringify(NaN)` es `null`, así que sin esto un NaN
// suyo llegaría al comparador indistinguible de un "sin dato".
const d6 = (x: any): any =>
  Array.isArray(x) ? x.map(d6)
    : (x && typeof x === 'object'
        ? Object.fromEntries(Object.entries(x).map(([k, v]) => [k, d6(v)]))
        : r6(x));

const protegido = (fn: () => any) => {
  try { return d6(fn()); } catch (e: any) { return { ERROR: e.constructor.name }; }
};

const heatmap = C.heatmap.map((c: any) => protegido(() => {
  const r = gexHeatmap({ ...c, now: new Date(c.now) });
  const cel = (x: any) => x == null ? null
    : [x.strike, x.expiration, x.netGex, x.callGex, x.putGex, x.openInterest, x.intensity];
  return {
    spot: r.spot, iv: r.iv, totalNetGex: r.totalNetGex, maxAbsCell: r.maxAbsCell,
    nExp: r.expirations.length, nStrikes: r.strikes.length, nCells: r.cells.length,
    exps: r.expirations.slice(0, 4).map((e: any) =>
      [e.expiration, e.dte, e.netGex, e.openInterest]),
    strikes: r.strikes.slice(0, 4).map((s: any) =>
      [s.strike, s.netGex, s.callGex, s.putGex, s.openInterest, s.distancePct]),
    cells: r.cells.slice(0, 6).map(cel),
    calientePos: cel(r.hottestPositive), calienteNeg: cel(r.hottestNegative),
  };
}));

const N = C.news;
const ahora = new Date(C.now);
const news = {
  entidades: N.entidades.map((s: any) => protegido(() => decodeEntities(s))),
  fechas: N.fechas.map((s: any) => protegido(() => parseFeedDate(s))),
  rss: N.rss.map((x: any) => protegido(() => parseRss(x, 'pub').map((i: any) =>
    [i.id, i.title, i.url, i.publisher, i.publishedUtc, i.description,
     i.sentiment, i.reasoning, i.layer]))),
  alias: N.alias.map(([t, n]: any) => protegido(() => companyAliases(t, n))),
  menciones: N.menciones.map(([t, a]: any) => protegido(() => mentionsCompany(t, a))),
  frescura: N.frescura.map(([p, n]: any) => protegido(() => recencyWeight(p, new Date(n)))),
  lotes: N.lotes.map((it: any) => protegido(() => {
    const b = newsBias(it, ahora);
    return [b.bias, b.score, b.positive, b.negative, b.neutral];
  })),
  flowPct: N.flowPct.map((p: any) => protegido(() => flowBias(p))),
  contradiccion: N.contradiccion.map(([f, s]: any) => protegido(() => {
    const c = contradictionFlag(f, s);
    return [c.kind, c.title, c.detail];
  })),
};

fs.writeFileSync(process.env.MOTOR3_OUT!, JSON.stringify({ heatmap, news }));
console.log(`  Víctor procesó ${heatmap.length} heatmap ·`
          + ` ${Object.values(news).reduce((a: number, v: any) => a + v.length, 0)} news`);
