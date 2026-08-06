// Compara la geometría del PANEL contra SU `chartGeometry.ts`, en el mismo Node.
//
// Los dos lados son JavaScript, así que aquí no hay semánticas de lenguaje que
// traducir: lo único que se mide es si la transcripción a mano dice lo mismo
// que su archivo. Es la última pieza del port que no se comparaba contra nada.
import fs from 'node:fs';
import { smartDomain, buildScales, packLabels } from './chartGeometry.ts';
import { vcSmartDomain, vcBuildScales, vcPackLabels } from './panel.mjs';

const C = JSON.parse(fs.readFileSync(process.env.GEO_CASOS!, 'utf8'));

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

const prot = (fn: () => any) => {
  try { return d6(fn()); } catch (e: any) { return { ERROR: e.constructor.name }; }
};

const fallos: any[] = [];
let n = 0;

function compara(bloque: string, i: number, suyo: any, panel: any) {
  n += 1;
  const a = JSON.stringify(suyo), b = JSON.stringify(panel);
  if (a !== b) fallos.push({ caso: `${bloque}#${i}`, victor: a, panel: b });
}

// ── smartDomain ────────────────────────────────────────────────────────────
// Solo se comparan `min`/`max`: `clampedTo2Sigma` es un campo que el panel no
// devuelve, y eso se reporta aparte como omisión, no como número distinto.
C.dominio.forEach((c: any, i: number) => {
  const s = prot(() => { const r = smartDomain(c); return { min: r.min, max: r.max }; });
  const p = prot(() => { const r = vcSmartDomain(c); return { min: r.min, max: r.max }; });
  compara('dominio', i, s, p);
});

// ── buildScales ────────────────────────────────────────────────────────────
// Se comparan los campos que el panel también devuelve, y las funciones se
// evalúan en puntos fijos: una función no se puede comparar, su imagen sí.
const foto = (r: any, c: any) => ({
  plotLeft: r.plotLeft, plotRight: r.plotRight, plotTop: r.plotTop,
  plotBottom: r.plotBottom, xNow: r.xNow,
  nCandles: r.candles.length,
  candles: r.candles.slice(0, 4).map((k: any) =>
    [k.x, k.w, k.cx, k.yOpen, k.yClose, k.yHigh, k.yLow, k.up]),
  ultima: r.candles.length
    ? (k => [k.x, k.w, k.cx, k.yOpen, k.yClose, k.yHigh, k.yLow, k.up])(
        r.candles[r.candles.length - 1])
    : null,
  y: [0, 90, 100, 110, 1e9, -1e9].map((v) => r.yOfPrice(v)),
  xf: [-1, 0, 1, 10, 20, 1e9].map((t) => r.xOfFuture(t)),
  domain: { min: r.domain.min, max: r.domain.max },
});
C.escalas.forEach((c: any, i: number) => {
  const s = prot(() => foto(buildScales(c), c));
  const p = prot(() => foto(vcBuildScales(c), c));
  compara('escalas', i, s, p);
});

// ── packLabels ─────────────────────────────────────────────────────────────
C.etiquetas.forEach((c: any, i: number) => {
  const s = prot(() => packLabels(c.items, c.opts).map((x: any) => [x.id, x.y, x.yAnchor]));
  const p = prot(() => vcPackLabels(c.items, c.opts).map((x: any) => [x.id, x.y, x.yAnchor]));
  compara('etiquetas', i, s, p);
});

// ── Campos que su geometría devuelve y el panel no ─────────────────────────
const dom = smartDomain(C.dominio[0]);
const esc = buildScales(C.escalas[0]);
const panelDom = vcSmartDomain(C.dominio[0]);
const panelEsc = vcBuildScales(C.escalas[0]);
const faltan = [
  ...Object.keys(dom).filter((k) => !(k in panelDom)).map((k) => `smartDomain.${k}`),
  ...Object.keys(esc).filter((k) => !(k in panelEsc)).map((k) => `buildScales.${k}`),
];

fs.writeFileSync(process.env.GEO_OUT!, JSON.stringify({ n, fallos, faltan }));
