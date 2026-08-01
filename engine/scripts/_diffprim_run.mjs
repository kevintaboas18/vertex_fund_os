// `Number()` y `Date.parse()` de V8 sobre el corpus. Sin lógica portada.
import fs from 'node:fs';
const { casos } = JSON.parse(fs.readFileSync(process.env.PRIM_CASOS, 'utf8'));
const out = casos.map(v => {
  const n = Number(v);
  const d = typeof v === 'string' ? Date.parse(v) : NaN;
  return { v,
           num: Number.isFinite(n) ? n : String(n),
           dp: Number.isNaN(d) ? 'NaN' : d };
});
fs.writeFileSync(process.env.PRIM_OUT, JSON.stringify(out));
console.log('  Node evaluó', out.length, 'valores');
