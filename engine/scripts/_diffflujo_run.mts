// Ejecuta SU `marketsnack.ts` con la red sustituida por el corpus.
//
// Igual que `_diffcadena_run.mts`: se pisa `globalThis.fetch` y nada más. La
// paginación, las cuatro condiciones de parada y la URL las sigue decidiendo
// su archivo. SECUENCIAL, nunca en paralelo: el stub es uno solo y los casos
// en paralelo se pisan las páginas entre ellos.
import fs from 'node:fs';
import { fetchFlow, fetchMarketFlow } from './marketsnack.ts';

const C = JSON.parse(fs.readFileSync(process.env.FLUJO_CASOS!, 'utf8'));

// El reloj, clavado: `targetDays` se mide contra `Date.now()` y con el reloj
// real las dos ejecuciones caerían en instantes distintos.
const AHORA = 1_756_339_200_000;
Date.now = () => AHORA;

function conRed(paginas: any[], urls: string[]) {
  let i = 0;
  (globalThis as any).fetch = async (url: string) => {
    urls.push(String(url));
    const p = paginas[Math.min(i, paginas.length - 1)];
    i += 1;
    return { ok: true, status: 200, json: async () => p,
             text: async () => JSON.stringify(p) } as any;
  };
}

const salida: any[] = [];
for (const caso of C) {
  const urls: string[] = [];
  conRed(caso.paginas, urls);
  const vistas: [number, number][] = [];
  const o = caso.opts ?? {};
  const opts: any = { onPage: (p: number, n: number) => { vistas.push([p, n]); } };
  for (const k of ['period', 'maxPages', 'minPremium', 'targetDays']) {
    if (o[k] !== undefined && o[k] !== null) opts[k] = o[k];
  }
  try {
    const r = o.symbol ? await fetchFlow(o.symbol, opts) : await fetchMarketFlow(opts);
    salida.push({
      nombre: caso.nombre, pages: r.pages, truncated: r.truncated,
      trades: r.trades.length, progreso: vistas, urls,
    });
  } catch (e: any) {
    salida.push({ nombre: caso.nombre, ERROR: e?.constructor?.name ?? 'Error',
                  mensaje: e?.message ?? '', urls });
  }
}

fs.writeFileSync(process.env.FLUJO_OUT!, JSON.stringify(salida, null, 1));
console.log(`  Víctor procesó ${salida.length} casos de flujo`);
