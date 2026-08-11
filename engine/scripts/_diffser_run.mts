// Lado Víctor del diferencial de SERIES: corre SUS tres stores, tal cual, en Node.
//
// Se usa el quitado de tipos NATIVO (`--experimental-strip-types`) en vez de un
// transpilador a base de regex: garantiza que solo desaparecen los tipos y que
// ni una línea de lógica cambia. Es lo que hace fiable este diferencial.
//
// Sus `DATA_DIR` son `path.join(process.cwd(), "data", …)`, fijados al cargar el
// módulo. Por eso el script se invoca DOS veces con directorios de trabajo
// distintos y no hay forma de mezclar los dos árboles en un solo proceso:
//
//   SER_MODO=escribe  cwd = el suyo    → guarda con SU código y vuelca el archivo
//   SER_MODO=lee      cwd = el de Python → lee con SU código lo que escribió el port
//
// El segundo modo es el que de verdad importa: no comprueba que dos archivos se
// parezcan, comprueba que SU app puede abrir el archivo del port y ver dentro
// los mismos días. Un formato "parecido" que su `loadIvHistory` rechaza deja el
// IV Rank en el proxy para siempre sin un solo error en el log.
import fs from 'node:fs';
import { structureScore } from './structure.ts';
import { ivContextScore } from './ivcontext.ts';
import { saveChainSnapshot, loadChainHistory } from './chainStore.ts';
import { saveIvSnapshot, loadIvHistory } from './ivStore.ts';
import { savePrediction, loadJournal } from './predictionStore.ts';

const C = JSON.parse(fs.readFileSync(process.env.SER_CASOS!, 'utf8'));
const MODO = process.env.SER_MODO!;

// `undefined` → `null`: Python no lo tiene y `JSON.stringify` se comería la
// clave, de modo que el comparador marcaría una diferencia que solo es de
// lenguaje. Misma convención que los demás diferenciales.
const U = (v: any) => (v === undefined ? null : v);

// A DIFERENCIA de los demás diferenciales, aquí NO se redondea a 6 decimales.
// Lo que se compara es el archivo, y un archivo se compara entero: si los dos
// lados guardan bits distintos, eso ES la divergencia, no ruido que tapar.
//
// Redondear además metía diferencias falsas. `Math.round` de JS redondea los
// empates hacia arriba y el `round` de Python hacia el par, así que un
// `totalNotional` de 1.794.425.266,8500004 —el mismo double en los dos lados—
// salía como .850001 en uno y .85 en el otro solo por pasar por el redondeo.
// Los no finitos no hacen falta como texto: `_write` del port ya los convierte
// a `null` igual que `JSON.stringify`, y eso también se compara.

/** El archivo CRUDO del disco, no lo que devuelve el `save`. Es lo que se compara. */
function archivo(sub: string, ticker: string): any {
  try {
    return JSON.parse(fs.readFileSync(`${process.cwd()}/data/${sub}/${ticker}.json`, 'utf8'));
  } catch {
    return null;   // no escribió: es un resultado, no un fallo del runner
  }
}

const out: any = { chain: [], iv: [], predictions: [] };

if (MODO === 'escribe') {
  for (const c of C.chain) {
    for (const s of c.saves) {
      await saveChainSnapshot(c.ticker, structureScore(s.rows) as any, new Date(s.now));
    }
    out.chain.push({ ticker: c.ticker, archivo: archivo('chain', c.ticker) });
  }

  for (const c of C.iv) {
    for (const s of c.saves) {
      const score = ivContextScore({ rows: s.rows, closes: s.closes, ivHistory: [] });
      await saveIvSnapshot(c.ticker, score as any, new Date(s.now));
    }
    out.iv.push({ ticker: c.ticker, archivo: archivo('iv', c.ticker) });
  }

  for (const c of C.predictions) {
    for (const s of c.saves) await savePrediction(c.ticker, s.snap, new Date(s.now));
    out.predictions.push({ ticker: c.ticker, archivo: archivo('predictions', c.ticker) });
  }
} else {
  // Lo mismo que hacen sus rutas: `hist?.snapshots ?? []`.
  const visto = (h: any) => (h == null ? null : {
    ticker: U(h.ticker),
    updatedAt: U(h.updatedAt),
    snapshots: U(Array.isArray(h.snapshots) ? h.snapshots : null),
  });
  for (const c of C.chain) {
    out.chain.push({ ticker: c.ticker, visto: visto(await loadChainHistory(c.ticker)) });
  }
  for (const c of C.iv) {
    out.iv.push({ ticker: c.ticker, visto: visto(await loadIvHistory(c.ticker)) });
  }
  for (const c of C.predictions) {
    out.predictions.push({ ticker: c.ticker, visto: visto(await loadJournal(c.ticker)) });
  }
}

fs.writeFileSync(process.env.SER_OUT!, JSON.stringify(out));
