// Ejecuta SUS ivcontext.ts / gex.ts / prediction.ts / risk.ts, tal cual, en Node.
//
// Mismo montaje que `_diffmotor_run.mts`: quitado de tipos NATIVO de Node, así
// que solo desaparecen los tipos y ni una línea de lógica cambia.
import fs from 'node:fs';
import { ivContextScore } from './ivcontext.ts';
import { gexAnalysis } from './gex.ts';
import { predictPro } from './prediction.ts';
import { sizeFlow, isTradeableIdea, budgetsOf, withinMoneyness } from './risk.ts';

const C = JSON.parse(fs.readFileSync(process.env.MOTOR2_CASOS!, 'utf8'));

// Los no finitos viajan como texto: JSON no los tiene y el corpus malformado
// los alcanza. El lado Python usa la misma convención.
const r6 = (x: any) => {
  if (typeof x !== 'number') return x;
  if (!Number.isFinite(x)) return Number.isNaN(x) ? 'NaN' : (x > 0 ? 'Inf' : '-Inf');
  const y = x * 1e6;
  if (!Number.isFinite(y)) return y > 0 ? 'Inf' : '-Inf';
  return Math.round(y) / 1e6;
};

const ivcontext = C.ivcontext.map((c: any) => {
  try {
    const r = ivContextScore(c);
    return {
      score: r.score,
      iv: [r6(r.iv.current), r6(r.iv.simpleAvg), r6(r.iv.min), r6(r.iv.max),
           r.iv.contracts, r.iv.points, r.iv.band, r.iv.special],
      rank: [r6(r.rank.value), r.rank.source, r.rank.days, r6(r.rank.low),
             r6(r.rank.high), r6(r.rank.reference), r.rank.points, r.rank.band],
      byExp: r.byExpiration.slice(0, 4).map((e: any) =>
        [e.expiration, r6(e.avgIv), e.trades, r6(e.maxIv), r6(e.premium)]),
      top: r.topContracts.slice(0, 3).map((t: any) => [t.symbol, r6(t.iv)]),
      frontSkew: r6(r.frontSkew), regime: r.regime, note: r.note,
    };
  } catch (e: any) { return { ERROR: e.constructor.name }; }
});

const gex = C.gex.map((c: any) => {
  try {
    const r = gexAnalysis({ ...c, now: new Date(c.now) });
    return {
      spot: r6(r.spot), iv: r6(r.iv), regime: r.regime, direction: r.direction,
      totalNetGex: r6(r.totalNetGex), flipStrike: r6(r.flipStrike),
      kingStrike: r6(r.kingStrike), confidence: r6(r.confidence),
      lowLiquidity: r.lowLiquidity, n: r.n,
      nodes: r.nodes.slice(0, 5).map((x: any) => [r6(x.strike), r6(x.netGex),
        r6(x.callGex), r6(x.putGex), r6(x.tradePremium), r6(x.concentration), x.side]),
    };
  } catch (e: any) { return { ERROR: e.constructor.name }; }
});

const prediction = C.prediction.map((c: any) => {
  try {
    const r = predictPro(c);
    const s = (x: any) => [x.kind, r6(x.target), r6(x.changePct), r6(x.probability),
                           x.driver];
    return {
      horizonDays: r6(r.horizonDays), spot: r6(r.spot), iv: r6(r.iv),
      bear: s(r.bear), base: s(r.base), bull: s(r.bull),
      score: r6(r.score), active: r.active, confidence: r6(r.confidence),
      direction: r.direction, summary: r.summary, caveat: r.caveat,
      calibration: [r.calibration.applied, r6(r.calibration.shiftPct),
                    r.calibration.samples],
      nLevels: r.levels.length,
      levels: r.levels.slice(0, 3).map((l: any) => [r6(l.strike), r6(l.touch),
        r6(l.band), r6(l.magnet), l.side]),
    };
  } catch (e: any) { return { ERROR: e.constructor.name }; }
});

const risk = C.risk.map((c: any) => {
  try {
    const r = sizeFlow(c.row, c.profile, c.horizonDays, c.ctx ?? {});
    return {
      maxContracts: r6(r.maxContracts), binding: r.binding,
      costPerContract: r6(r.costPerContract), totalCost: r6(r.totalCost),
      costPctOfAccount: r6(r.costPctOfAccount), burnDays: r6(r.burnDays),
      thetaBurnPerContract: r6(r.thetaBurnPerContract),
      totalBurn: r6(r.totalBurn), burnPctOfAccount: r6(r.burnPctOfAccount),
      fullyDecays: r.fullyDecays, blocked: r.blocked ?? null,
      tradeable: isTradeableIdea(c.row),
      // El default de `cap` solo entra si el argumento es `undefined`: pasarlo
      // siempre convertiría un caso sin `cap` en uno con `cap: undefined`,
      // que aquí da lo mismo pero no en un `null` explícito. Se respeta la aridad.
      cercano: 'cap' in c ? withinMoneyness(c.row, c.cap) : withinMoneyness(c.row),
      budgets: (() => { const b = budgetsOf(c.profile); return [r6(b.premium), r6(b.theta)]; })(),
    };
  } catch (e: any) { return { ERROR: e.constructor.name }; }
});

fs.writeFileSync(process.env.MOTOR2_OUT!,
                 JSON.stringify({ ivcontext, gex, prediction, risk }));
console.log(`  Víctor procesó ${ivcontext.length} ivcontext · ${gex.length} gex ·`
          + ` ${prediction.length} prediction · ${risk.length} risk`);
