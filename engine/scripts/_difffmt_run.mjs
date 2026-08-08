// Lado Víctor del diferencial de `format.ts`, y el mío, en el MISMO proceso.
//
// Los dos lados son JavaScript y los dos usan el `Intl` del motor, así que
// compararlos en dos procesos distintos mediría el ICU de cada uno en vez de
// medir el código. Aquí corren en el mismo Node, sobre los mismos casos, y la
// única diferencia posible es la que hay entre su `format.ts` y el panel.
//
// El lado del panel NO se reescribe: `diff_format.sh` recorta el bloque de
// formateadores del HTML tal cual y lo carga. Si alguien lo cambia en el HTML,
// aquí se nota; si lo cambiara sólo en una copia del diferencial, no valdría de
// nada.
import fs from 'node:fs';

const C = JSON.parse(fs.readFileSync(process.env.FMT_CASOS, 'utf8'));
const SUYO = await import(process.env.FMT_SUYO);
const MIO = await import(process.env.FMT_MIO);

// `NaN`/`Infinity` viajan como texto porque JSON no los tiene.
const num = v => (v === 'NaN' ? NaN
  : v === 'Infinity' ? Infinity
  : v === '-Infinity' ? -Infinity : v);

const filas = [];
const anota = (bloque, entrada, suyo, mio) => filas.push({ bloque, entrada, suyo, mio });
const seguro = fn => { try { return fn(); } catch (e) { return 'ERROR:' + e.constructor.name; } };

for (const crudo of C.numeros) {
  const v = num(crudo);
  const e = String(crudo);
  // `null` es el caso declarado: él pinta "$NaN", el panel pinta "—". Se anota
  // igual para que la divergencia se vea y se cuente, no para taparla.
  anota('money', e, seguro(() => SUYO.money.format(v)), seguro(() => MIO.fmtAbbr(v)));
  anota('money0', e, seguro(() => SUYO.money0.format(v)), seguro(() => MIO.fmtMoney(v)));
  anota('int', e, seguro(() => SUYO.int.format(v)), seguro(() => MIO.VC_INT.format(v)));
  anota('px', e, seguro(() => SUYO.px.format(v)), seguro(() => MIO.VC_PX.format(v)));
  anota('pct', e, seguro(() => SUYO.pct.format(v)), seguro(() => MIO.VC_PCT.format(v)));
}

for (const ts of C.instantes) {
  anota('timeET', ts, seguro(() => SUYO.timeET(ts)), seguro(() => MIO.vcTimeET(ts)));
  anota('dateET', ts, seguro(() => SUYO.dateET(ts)), seguro(() => MIO.vcDateET(ts)));
  anota('timeOf', ts, seguro(() => SUYO.timeOf(ts)), seguro(() => MIO.vcTimeOf(ts)));
  anota('dateOf', ts, seguro(() => SUYO.dateOf(ts)), seguro(() => MIO.vcDateOf(ts)));
}

for (const s of C.unix) {
  anota('hmET', String(s), seguro(() => SUYO.hmET(s)), seguro(() => MIO.vcHmET(s)));
}

fs.writeFileSync(process.env.FMT_OUT, JSON.stringify(filas));
