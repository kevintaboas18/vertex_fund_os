// Ejecuta SUS validation.ts / levels.ts / structure.ts, tal cual, en Node.
//
// Se usa el quitado de tipos NATIVO de Node (`--experimental-strip-types`) en vez
// de un transpilador a base de regex: garantiza que solo desaparecen los tipos y
// que ni una línea de lógica cambia. Es lo que hace fiable este diferencial.
import fs from 'node:fs';
import { validationScore } from './validation.ts';
import { findLevels } from './levels.ts';
import { structureScore } from './structure.ts';

const C = JSON.parse(fs.readFileSync(process.env.MOTOR_CASOS!, 'utf8'));
const r6 = (x: number) => Math.round(x * 1e6) / 1e6;

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

fs.writeFileSync(process.env.MOTOR_OUT!, JSON.stringify({ validation, levels, structure }));
console.log(`  Víctor procesó ${validation.length} validation · ${levels.length} levels · ${structure.length} structure`);
