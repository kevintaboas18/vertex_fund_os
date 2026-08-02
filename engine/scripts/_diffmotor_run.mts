// Ejecuta SUS validation.ts / levels.ts / structure.ts, tal cual, en Node.
//
// Se usa el quitado de tipos NATIVO de Node (`--experimental-strip-types`) en vez
// de un transpilador a base de regex: garantiza que solo desaparecen los tipos y
// que ni una línea de lógica cambia. Es lo que hace fiable este diferencial.
import fs from 'node:fs';
import { validationScore } from './validation.ts';
import { findLevels } from './levels.ts';
import { structureScore } from './structure.ts';
import { classifyFlow, aggressionScore, convictionScore, unusualityScore,
         detectClusters } from './flow.ts';

const C = JSON.parse(fs.readFileSync(process.env.MOTOR_CASOS!, 'utf8'));
// El guard de `typeof` no es cosmético: en JS `null * 1e6` es 0, así que un
// `Math.round(x * 1e6) / 1e6` sin él convertía todos los `null` en ceros y el
// comparador los denunciaba como diferencias del port que no existían.
// Los no finitos viajan como texto: JSON no los tiene, y desde que el corpus
// lleva entradas malformadas son alcanzables (un `assetPrice` de 1e308 produce
// un MFE infinito en los dos lados). El lado Python usa la misma convención.
const r6 = (x: any) => {
  if (typeof x !== 'number') return x;
  if (!Number.isFinite(x)) return Number.isNaN(x) ? 'NaN' : (x > 0 ? 'Inf' : '-Inf');
  // `x * 1e6` desborda a Infinity aunque `x` sea finito (1e308·1e6). Sin este
  // segundo control el valor sale como Infinity, `JSON.stringify` lo vuelve
  // `null` y el comparador lo lee como una diferencia que no existe.
  const y = x * 1e6;
  if (!Number.isFinite(y)) return y > 0 ? 'Inf' : '-Inf';
  return Math.round(y) / 1e6;
};

const validation = C.validation.map((c: any) => {
  try {
    const r = validationScore({ flows: c.flows, bars: c.bars, now: new Date(c.now),
      horizon: c.horizon, ...(c.thresholdPct !== null ? { thresholdPct: c.thresholdPct } : {}) });
    return { score: r.score, hr: r.hitRate, sp: r.speed, whr: r.weightedHitRate,
             mfe: r.avgMfe, mae: r.avgMae, thr: r.thresholdPct, cov: r.coverage,
             verdict: r.verdict, nout: r.outcomes.length, dirs: r.byDirection,
             ids: r.outcomes.slice(0, 6).map((o: any) => o.id),
             det: r.outcomes.slice(0, 6).map((o: any) => [o.resolved, o.validated,
               o.sessionsObserved, o.daysToValidate, r6(o.mfePct), r6(o.maePct)]) };
  } catch (e: any) { return { ERROR: e.constructor.name }; }
});

const levels = C.levels.map((c: any) => {
  try {
    const r = findLevels({ bars: c.bars, spot: c.spot, now: new Date(c.now), chain: c.chain,
      flows: c.flows, gex: c.gex, tolerancePct: c.tolerancePct, rangePct: c.rangePct });
    const m = (a: any[]) => a.map((l: any) => [r6(l.price), l.kind, r6(l.strength),
      r6(l.distancePct), [l.sources.touches, l.sources.lastTouch, l.sources.openInterest,
        r6(l.sources.notional), r6(l.sources.flowPremium), r6(l.sources.netGex)],
      l.flipped, l.why]);
    return { spot: r.spot, sup: m(r.supports), res: m(r.resistances),
             ks: r.keySupport ? r6(r.keySupport.price) : null,
             kr: r.keyResistance ? r6(r.keyResistance.price) : null, tol: r.tolerancePct };
  } catch (e: any) { return { ERROR: e.constructor.name }; }
});

const structure = C.structure.map((rows: any) => {
  try {
    const r = structureScore(rows), st = r.strikes;
    return { score: r.score, notional: r.notional, volOI: r.volOI,
             strikes: { dominantCount: st.dominantCount, consideredCount: st.consideredCount,
               points: st.points, callPct: st.callPct, putPct: st.putPct,
               dominantSide: st.dominantSide,
               top: st.top.slice(0, 4).map((t: any) => [r6(t.strike), r6(t.notional), t.side,
                 t.dominant, r6(t.dominancePct), t.openInterest, t.volume]) },
             exps: r.expirations.slice(0, 4).map((e: any) => [e.expiration, e.contracts, r6(e.notional)]) };
  } catch (e: any) { return { ERROR: e.constructor.name }; }
});

const nowFlow = new Date(C.flow.now);
const flow = C.flow.lotes.map((lote: any) => {
  try {
    const cf = classifyFlow(lote, nowFlow);
    const A = aggressionScore(cf.interesting);
    const V = convictionScore(cf.interesting);
    const U = unusualityScore(cf.interesting);
    const CL = detectClusters(cf.interesting);
    return {
      n: cf.interesting.length, nAll: cf.rows.length,
      ids: cf.interesting.slice(0, 8).map((r: any) => r.id),
      filas: cf.interesting.slice(0, 5).map((r: any) => [r.id, r.type, r.strike, r.dte,
        r.aggression, r.expiryStatus, r.sentiment, r6(r.premium), r6(r.thetaPctDaily),
        r.flags.big, r.flags.convDelta, r.flags.aboveAsk, r.flags.belowBid, r.flags.mid,
        r.flags.leap, r.flags.repeated, r.flags.multileg, r.flags.simultaneous,
        r.flags.exceededOI, r.unusual, r.interesting, r.conditionCode, r.conditionName,
        r.underlying, r.expiration, r6(r.assetPrice), r6(r.iv), r6(r.delta),
        r.scores.volume, r.scores.timing, r.scores.repetition, r.scores.total]),
      agg: { score: A.score, ratio: r6(A.ratio), premiumAsk: r6(A.premiumAsk),
             premiumBid: r6(A.premiumBid), premiumMid: r6(A.premiumMid), n: A.n },
      conv: { score: V.score, n: V.n,
        spread: { avgPct: r6(V.spread.avgPct), points: V.spread.points, wideCount: V.spread.wideCount },
        dominance: { askPct: r6(V.dominance.askPct), bidPct: r6(V.dominance.bidPct),
                     dominantPct: r6(V.dominance.dominantPct), side: V.dominance.side,
                     points: V.dominance.points },
        execution: { points: V.execution.points, avgRaw: r6(V.execution.avgRaw),
                     counts: V.execution.counts } },
      unu: { score: U.score, unusualCount: U.unusualCount, n: U.n,
             avgByParam: Object.fromEntries(Object.entries(U.avgByParam).map(([k, v]: any) => [k, r6(v)])),
             top: U.top.slice(0, 3).map((t: any) => [t.row.id, t.scores.total, t.scores.size,
               t.scores.delta, t.scores.theta, t.scores.gamma, t.scores.leg, t.scores.expiry]) },
      clus: CL.slice(0, 3).map((c: any) => [c.startSec, c.endSec, c.count, r6(c.premium),
        c.direction, r6(c.unidirectionality), c.score, r6(c.callPremium), r6(c.putPremium),
        c.bet, c.betLabel]),
    };
  } catch (e: any) { return { ERROR: e.constructor.name, msg: String(e.message).slice(0, 70) }; }
});

fs.writeFileSync(process.env.MOTOR_OUT!, JSON.stringify({ validation, levels, structure, flow }));
console.log(`  Víctor procesó ${flow.length} flow · ${validation.length} validation ·`
          + ` ${levels.length} levels · ${structure.length} structure`);
