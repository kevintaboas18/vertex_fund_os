// Ejecuta SU `reviewPredictions` —el motor de calibración— tal cual, en Node.
//
// Es la función que decide cuánto se corrige el target base por el sesgo
// histórico. Si diverge, el motor se auto-corrige hacia el lado equivocado y
// nada en el reporte lo delata: el número sale igual de bonito.
//
// `predictionStore.ts` toca disco en `loadJournal`/`savePrediction`, pero
// `reviewPredictions` es PURA, así que se importa y se llama sin tocar `fs`.
import fs from 'node:fs';
import { reviewPredictions } from './predictionStore.ts';

const C = JSON.parse(fs.readFileSync(process.env.CALIB_CASOS!, 'utf8'));

const r6 = (x: any): any => {
  if (typeof x !== 'number') return x;
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

const out = C.map((c: any) => {
  try {
    const r = reviewPredictions(c.snapshots, c.bars, new Date(c.now));
    return d6({
      matured: r.maturedCount, mae: r.meanAbsErrorPct, bias: r.biasPct,
      touch: r.baseTouchRate, dir: r.directionHitRate, best: r.bestCounts,
      n: r.evals.length,
      evals: r.evals.slice(0, 4).map((e: any) => [
        e.date, e.horizonDays, e.sessions, e.matured, e.actualClose, e.actualHigh,
        e.actualLow, e.baseErrorPct, e.baseAbsErrorPct, e.baseTouched,
        e.bullTouched, e.bearTouched, e.directionHit, e.best]),
    });
  } catch (e: any) { return { ERROR: e.constructor.name }; }
});

fs.writeFileSync(process.env.CALIB_OUT!, JSON.stringify(out));
console.log(`  Víctor procesó ${out.length} diarios`);
