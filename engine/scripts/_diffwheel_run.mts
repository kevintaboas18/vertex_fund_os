// Ejecuta SUS wheel.ts / wheelAfford.ts / earnings.ts, tal cual, en Node.
//
// Se usa el quitado de tipos NATIVO (`--experimental-strip-types`) en vez de un
// transpilador a base de regex: garantiza que solo desaparecen los tipos y que
// ni una línea de lógica cambia.
//
// `earnings.ts` tiene además funciones de I/O (`fetchFilingDates`,
// `earningsForTicker`) que llaman a Massive. NO se tocan: el contenedor bloquea
// ese dominio y, sobre todo, no son lo que decide nada — las dos puras
// (`estimateNextEarnings`, `earningsFlag`) sí.
import fs from 'node:fs';
import {
  wheelMetrics, pickPremium, spreadPctOf, liquidityBlock, scoreCandidate,
  atmIv, wheelCandidates, WHEEL_PRESETS, HAIRCUT, MAX_SPREAD_PCT, MIN_OI,
} from './wheel.ts';
import { affordOf, sortByAffordThenScore } from './wheelAfford.ts';
import { estimateNextEarnings, earningsFlag } from './earnings.ts';
import { WHEEL_UNIVERSE } from './wheelUniverse.ts';

const C = JSON.parse(fs.readFileSync(process.env.WH_CASOS!, 'utf8'));

const r6 = (x: any): any => {
  if (typeof x !== 'number') return x === undefined ? null : x;
  if (!Number.isFinite(x)) return Number.isNaN(x) ? 'NaN' : (x > 0 ? 'Inf' : '-Inf');
  const y = x * 1e6;
  if (!Number.isFinite(y)) return y > 0 ? 'Inf' : '-Inf';
  return Math.round(y) / 1e6;
};
const d6 = (x: any): any =>
  Array.isArray(x) ? x.map(d6)
    : (x && typeof x === 'object'
        ? Object.fromEntries(Object.entries(x).map(([k, v]) => [k, d6(v)]))
        : r6(x));
const seguro = (fn: () => any) => {
  try { return d6(fn()); } catch (e: any) { return { ERROR: e.constructor.name }; }
};

const out: any = {};

// Las constantes también: un preset con otro rango de delta elige otros strikes
// y no lo delataría ningún caso, porque los dos lados usarían el suyo.
out.constantes = d6({
  presets: WHEEL_PRESETS, haircut: HAIRCUT,
  maxSpreadPct: MAX_SPREAD_PCT, minOi: MIN_OI,
  universo: WHEEL_UNIVERSE,
});

out.metrics = C.metrics.map((c: any) => seguro(() => wheelMetrics(c)));
out.premium = C.premium.map((c: any) =>
  seguro(() => pickPremium({ bid: c.bid, ask: c.ask, lastTrade: c.lastTrade, model: c.model })));
out.spread = C.spread.map((c: any) => seguro(() => spreadPctOf(c.bid, c.ask)));
out.liquidity = C.liquidity.map((c: any) =>
  seguro(() => liquidityBlock({ bid: c.bid, ask: c.ask, openInterest: c.openInterest })));
out.score = C.score.map((c: any) => seguro(() => scoreCandidate(c)));
out.atmIv = C.atmIv.map((c: any) => seguro(() => atmIv(c.rows, c.spot)));

out.candidates = C.candidates.map((c: any) => seguro(() => {
  const filas = wheelCandidates({ ...c, preset: (WHEEL_PRESETS as any)[c.preset] });
  return {
    n: filas.length,
    // Las 8 primeras enteras: el orden es parte del resultado, porque es lo
    // que decide qué ve el usuario arriba de la tabla.
    filas: filas.slice(0, 8).map((f: any) => [
      f.strike, f.expiration, f.dte, f.delta, f.iv, f.ivSource, f.openInterest,
      f.spreadPct, f.blocked, f.blockReason,
      f.premium && [f.premium.price, f.premium.source, f.premium.raw],
      f.metrics && [f.metrics.credit, f.metrics.collateral, f.metrics.returnPct,
                    f.metrics.annualizedPct, f.metrics.breakeven,
                    f.metrics.cushionPct, f.metrics.probExpireWorthless],
      f.score && [f.score.total, f.score.annualized.points, f.score.ivRank.points,
                  f.score.cushion.points, f.score.liquidity.points,
                  f.score.earnings.points],
    ]),
  };
}));

out.afford = C.afford.map((c: any) => seguro(() => {
  // Se reconstruyen candidatos mínimos: `affordOf` solo mira `metrics.collateral`,
  // `blocked` y `score.total`, así que darle más sería adornar el caso.
  const filas = c.rows.map((r: any, i: number) => ({
    ticker: 'X', strike: r.strike, expiration: '2026-09-18', dte: 30, spot: 100,
    delta: -0.2, iv: 0.5, ivSource: 'implicita', openInterest: 500, spreadPct: 5,
    premium: null, blocked: r.blocked, blockReason: null,
    metrics: { credit: 0, collateral: r.strike * 100, returnPct: 0, annualizedPct: 0,
               breakeven: 0, cushionPct: 0, probExpireWorthless: 0 },
    score: r.score, _i: i,
  }));
  const ord = sortByAffordThenScore(filas as any, c.cash);
  return {
    orden: ord.map((f: any) => f._i),
    afford: ord.map((f: any) => [f.afford.affordable, f.afford.shortfall]),
    sueltos: filas.map((f: any) => {
      const a = affordOf(f as any, c.cash);
      return [a.affordable, a.shortfall];
    }),
  };
}));

out.earnings = C.earnings.map((c: any) =>
  seguro(() => estimateNextEarnings(c.filings, new Date(c.now))));
out.earningsFlag = C.earningsFlag.map((c: any) => seguro(() => earningsFlag({
  nextEarnings: c.next, expiration: c.expiration, frontSkew: c.frontSkew,
})));

fs.writeFileSync(process.env.WH_OUT!, JSON.stringify(out));
