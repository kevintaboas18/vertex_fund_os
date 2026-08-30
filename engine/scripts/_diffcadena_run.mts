// Ejecuta SU `massive.ts` —el cliente de la cadena— con la red sustituida.
//
// Lo único que se cambia de su archivo es de dónde salen los bytes: se pisa
// `globalThis.fetch` con una función que devuelve las páginas del corpus, en
// orden. Ni una línea de su lógica se toca: la paginación, el tope, el precio
// del subyacente y el acumulado los sigue decidiendo él.
import fs from 'node:fs';
import { fetchOptionChain, fetchDailyBars, fetchBars } from './massive.ts';

const TODO = JSON.parse(fs.readFileSync(process.env.CADENA_CASOS!, 'utf8'));
const C = TODO.cadenas;

const r6 = (x: any) => {
  if (typeof x !== 'number') return x;
  if (!Number.isFinite(x)) return Number.isNaN(x) ? 'NaN' : (x > 0 ? 'Inf' : '-Inf');
  const y = x * 1e6;
  if (!Number.isFinite(y)) return y > 0 ? 'Inf' : '-Inf';
  return Math.round(y) / 1e6;
};

/** Sirve las páginas del caso una a una y apunta las URL pedidas. */
function conRed(paginas: any[], urls: string[]) {
  let i = 0;
  (globalThis as any).fetch = async (url: string) => {
    urls.push(String(url));
    const p = paginas[Math.min(i, paginas.length - 1)];
    i += 1;
    return {
      ok: true, status: 200,
      json: async () => p,
      text: async () => JSON.stringify(p),
    } as any;
  };
}

/** La URL sin las dos fechas, que dependen del reloj y no de la lógica. */
const sinFechas = (u: string) =>
  u.replace(/\d{4}-\d{2}-\d{2}\/\d{4}-\d{2}-\d{2}/, '<desde>/<hasta>');

// SECUENCIAL, no `Promise.all`. El stub vive en `globalThis.fetch`, que es uno
// solo: con los casos en paralelo cada uno pisa el del anterior y las páginas
// se sirven cruzadas. Se vio de la peor forma —el diferencial acusó al port de
// 37 divergencias que eran de este archivo— y por eso queda escrito aquí.
const cadena: any[] = [];
for (const caso of C) {
  cadena.push(await (async (caso: any) => {
  const urls: string[] = [];
  conRed(caso.paginas, urls);
  if (caso.maxPages === null || caso.maxPages === undefined) {
    delete process.env.MASSIVE_MAX_PAGES;
  } else {
    process.env.MASSIVE_MAX_PAGES = String(caso.maxPages);
  }
  const vistas: [number, number][] = [];
  try {
    const r = await fetchOptionChain('AAPL', {
      onPage: (p, n) => { vistas.push([p, n]); },
    });
    return {
      nombre: caso.nombre,
      underlyingPrice: r6(r.underlyingPrice),
      pages: r.pages,
      truncated: r.truncated,
      // Los strikes y vencimientos CRUDOS, en orden: es lo que su bucle
      // acumuló, antes de que ninguna fórmula los toque.
      crudos: r.contracts.map((x: any) =>
        [r6(x?.details?.strike_price), x?.details?.expiration_date ?? null]),
      progreso: vistas,
      urls,
    };
  } catch (e: any) {
    return { nombre: caso.nombre, ERROR: e?.constructor?.name ?? 'Error',
             mensaje: e?.message ?? '' };
  }
  })(caso));
}

// Las barras: el MISMO corpus que lee el lado Python, servido desde el JSON.
const BARRAS = TODO.barras;

// Su `fetchDailyBars` LANZA con un `t` que no sea número: `new Date("175…")`
// es una fecha inválida y `toISOString()` tira `RangeError`. No es un fallo del
// montaje —es lo que hace su archivo— así que se apunta como resultado, igual
// que el corpus malformado de los otros diferenciales apunta los suyos.
const barras: any[] = [];
for (let i = 0; i < BARRAS.length; i += 1) {
  barras.push(await (async (pag: any, i: number) => {
  const out: any = { i };
  const urls: string[] = [];
  conRed([pag], urls);
  try {
    const d = await fetchDailyBars('AAPL', 365);
    out.diarias = d.map((b: any) =>
      [b.time, r6(b.open), r6(b.high), r6(b.low), r6(b.close)]);
  } catch (e: any) { out.diariasError = e?.constructor?.name ?? 'Error'; }
  out.urlDiaria = sinFechas(urls[0] ?? '');

  const urls2: string[] = [];
  conRed([pag], urls2);
  try {
    const t = await fetchBars('AAPL', 15, 'minute', 10);
    out.marco = t.map((b: any) =>
      [r6(b.time), r6(b.open), r6(b.high), r6(b.low), r6(b.close)]);
  } catch (e: any) { out.marcoError = e?.constructor?.name ?? 'Error'; }
  out.urlMarco = sinFechas(urls2[0] ?? '');
  return out;
  })(BARRAS[i], i));
}

fs.writeFileSync(process.env.CADENA_OUT!,
                 JSON.stringify({ cadena, barras }, null, 1));
console.log(`  Víctor procesó ${cadena.length} cadenas · ${barras.length} de barras`);
