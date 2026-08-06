// Compara la fórmula del cono del HTML contra su expectedMove, en V8.
import fs from 'node:fs';
import { expectedMove } from './em.mjs';
const STEPS = 24;
let peor = 0, n = 0;
for (const iv0 of [0, 0.001, 0.005, 0.0099, 0.01, 0.05, 0.2, 0.45, 0.8, 1.2, 2.0, 3.0]) {
  for (const h of [10, 20, 30]) {
    for (const spot of [5, 100, 850]) {
      const iv = Math.max(iv0 || 0.4, 0.01);          // el suelo del HTML
      const sd = iv * Math.sqrt(Math.max(h, 0) / 365);
      for (let i = 0; i <= STEPS; i++) {
        const f = i / STEPS, k = Math.sqrt(f);
        for (const m of [1, 2]) {
          const em = expectedMove(spot, iv0 || 0.4, h * f);
          const suyoU = m === 1 ? em.upper1 : em.upper2;
          const suyoL = m === 1 ? em.lower1 : em.lower2;
          peor = Math.max(peor,
            Math.abs(suyoU - spot * Math.exp(m * sd * k)) / spot,
            Math.abs(suyoL - spot * Math.exp(-m * sd * k)) / spot);
          n += 2;
        }
      }
    }
  }
}
console.log(`  ${n} puntos del cono · desvío relativo máximo ${(peor * 100).toExponential(2)} %`);
if (peor < 1e-12) console.log("  el cono de la gráfica es IDÉNTICO a su expectedMove/conePoints");
else { console.log("  ✗ el cono NO coincide con el suyo"); process.exit(1); }

// Segunda pasada: los puntos que sirve el MOTOR. Desde que
// `/api/projection-targets` manda `chart_geometry`, lo que la gráfica dibuja
// sale de `cone_points`/`prediction_path` de Python, no de la fórmula de
// arriba. Sin esto el diferencial verificaría una copia que ya no se usa.
{
  const py = JSON.parse(fs.readFileSync(process.env.CONO_PY_OUT, 'utf8'));
  let peorPy = 0, m = 0;
  for (const caso of py) {
    const { spot, iv, days, cone, path, target } = caso;
    for (const c of cone) {
      const em = expectedMove(spot, iv, c.t);
      peorPy = Math.max(peorPy,
        Math.abs(em.upper1 - c.upper1) / spot, Math.abs(em.lower1 - c.lower1) / spot,
        Math.abs(em.upper2 - c.upper2) / spot, Math.abs(em.lower2 - c.lower2) / spot);
      m += 4;
    }
    // `predictionPath`: ruta en √t, recortada al cono de 2σ.
    const emH = expectedMove(spot, iv, days);
    const clamped = Math.min(Math.max(target, emH.lower2), emH.upper2);
    const n = path.length - 1;
    for (let i = 0; i <= n; i++) {
      const f = i / n;
      const suyo = spot + (clamped - spot) * Math.sqrt(f);
      peorPy = Math.max(peorPy, Math.abs(suyo - path[i].price) / spot);
      m += 1;
    }
  }
  console.log(`  ${m} puntos del motor · desvío relativo máximo ${(peorPy * 100).toExponential(2)} %`);
  if (peorPy < 1e-12) console.log("  la geometría que sirve el motor es IDÉNTICA a su expectedMove");
  else { console.log("  ✗ la geometría del motor NO coincide con la suya"); process.exit(1); }
}
