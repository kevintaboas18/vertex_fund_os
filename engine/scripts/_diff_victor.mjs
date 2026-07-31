import fs from 'node:fs';
import { contractPrice, toRow, sortByOpenInterestDesc, countExpirations } from '/tmp/victor_compute.mjs';
const casos = JSON.parse(fs.readFileSync('/tmp/casos.json','utf8'));
const norm = v => (typeof v === 'number' && !Number.isFinite(v)) ? String(v) : v;
const out = casos.map(c => {
  let r, cp;
  try { r = toRow(c); } catch(e) { r = {ERROR: e.constructor.name}; }
  try { cp = contractPrice(c); } catch(e) { cp = {ERROR: e.constructor.name}; }
  if (r.ERROR) return {row:r, cp};
  return {row:{
    optionTicker: r.optionTicker, contractType: r.contractType, expiration: r.expiration,
    strike: norm(r.strike), openInterest: norm(r.openInterest), volume: norm(r.volume),
    price: norm(r.price), priceSource: r.priceSource,
    openPremium: norm(r.openPremium), notionalValue: norm(r.notionalValue),
  }, cp: cp.ERROR ? cp : {price: norm(cp.price), source: cp.source}};
});
fs.writeFileSync('/tmp/victor_out.json', JSON.stringify(out));
console.log('  Víctor procesó', out.length, '· con excepción:', out.filter(o=>o.row.ERROR).length);
