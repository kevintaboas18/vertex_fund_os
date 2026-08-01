// Compara la fórmula del cono del HTML contra su expectedMove, en V8.
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
