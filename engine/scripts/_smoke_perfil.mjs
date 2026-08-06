// ══════════════════════════════════════════════════════════════════════════
//  SMOKE DEL PERFIL — ejecuta el JS VIVO del HTML, no una copia.
//
//  Los tests de Python leen el HTML como TEXTO: comprueban que una función
//  existe y que alguien la llama. Eso no ejecuta nada. Esto sí: monta un DOM
//  mínimo, corre `renderProjIdeas`, `renderProjWheel` y `pfPinta` con payloads
//  realistas, y mira el HTML que producen.
//
//  Encontró un fallo real que la lectura de texto no podía ver: `pfPinta`
//  dependía de una variable que solo llenaba `pfCargar`, así que la nota del
//  riesgo por operación salía en blanco.
//
//      node engine/scripts/_smoke_perfil.mjs
// ══════════════════════════════════════════════════════════════════════════
import fs from 'node:fs';
const html = fs.readFileSync(new URL('../../vertex_fund_os_platform.html', import.meta.url),'utf8');
const blocks = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)].map(m=>m[1]);
// El grande (Proyecciones) + el del perfil: los dos son código VIVO.
const src = blocks.sort((a,b)=>b.length-a.length)[0]
          + String.fromCharCode(10) + blocks.find(b => b.includes('function pfPinta('));

const store = {};
const mkEl = id => ({ id, innerHTML:'', value:'', innerText:'', dataset:{}, style:{},
  classList:{ _s:new Set(), add(...c){c.forEach(x=>this._s.add(x))},
              remove(...c){c.forEach(x=>this._s.delete(x))},
              toggle(c,f){ f?this._s.add(c):this._s.delete(c) },
              contains(c){return this._s.has(c)} },
  querySelectorAll:()=>[], querySelector:()=>null, appendChild(){}, closest:()=>null });
globalThis.document = { getElementById: id => (store[id] ||= mkEl(id)),
                        querySelectorAll: () => [], addEventListener(){},
                        documentElement:{ classList:{ contains:()=>false, toggle(){} }, lang:'' } };
globalThis.window = { location:{ protocol:'https:' }, matchMedia:()=>({addEventListener(){}}), addEventListener(){} };
globalThis.localStorage = { getItem:()=>null, setItem(){}, removeItem(){} };
globalThis.fetch = async () => { throw new Error('sin red'); };
globalThis.Chart = function(){ return { destroy(){}, update(){} }; };
globalThis.Chart.register = () => {};
globalThis.lucide = { createIcons(){} };


// Se extraen las funciones que se quieren ejercitar, con sus dependencias.
const api = new Function(src + `
  return { renderProjIdeas, renderProjWheel, vcCabeceraPerfil, fmtMoney, pfPinta, pfTab };`)();

let fallos = 0;
const chk = (ok, msg) => { console.log((ok?'  \x1b[32m✓\x1b[0m ':'  \x1b[31m✗\x1b[0m ') + msg); if(!ok) fallos++; };

const perfil = { capital: 1000, tolerancia: 'agresivo', riesgo_pct: 15,
                 riesgo_por_trade: 150, caben: 1 };

// ── IDEAS ────────────────────────────────────────────────────────────────
api.renderProjIdeas({
  perfil, tickers: 2, with_history: 0, scanned: 400, pages: 1, truncated: false,
  min_premium: 100000, moneyness_cap: 0.25, rejected: {},
  ideas: [
    { id:'a', ticker:'F', type:'call', strike:12, dte:30, premium:150000,
      aggression:'ask', theta_pct_daily:1.2, unusual_score:9, history:null,
      sizing:{ max_contracts:1, cost_per_contract:35, total_cost:35,
               cost_pct_of_account:3.5, binding:'riesgo', blocked:null } },
    { id:'b', ticker:'TSLA', type:'call', strike:420, dte:20, premium:900000,
      aggression:'bid', theta_pct_daily:2.1, unusual_score:12, history:null,
      sizing:{ max_contracts:0, cost_per_contract:950, total_cost:0,
               cost_pct_of_account:0, binding:'capital', blocked:'no cabe ni uno' } },
  ]});
const ideas = store['projIdeas'].innerHTML;
console.log('\n── Ideas ───────────────────────────────────────────────');
chk(ideas.includes('$1,000'), 'el capital se pinta ENTERO, no abreviado');
chk(ideas.includes('1 de 2 te caben'), 'dice cuántas te caben');
chk(/1&times;\s*<span[^>]*>\$35</.test(ideas), 'la que cabe muestra 1× y su coste');
chk(ideas.includes('no cabe'), 'la que no cabe se marca…');
chk(ideas.includes('TSLA'), '…pero NO se esconde');
chk(ideas.indexOf('>F<') < ideas.indexOf('TSLA'), 'la que cabe va primero');
chk(ideas.includes("switchView('perfilView')"), 'lleva al editor de perfil');

// ── WHEEL ────────────────────────────────────────────────────────────────
const cand = (t, strike, col, aff, short) => ({
  ticker:t, strike, expiration:'2026-09-18', dte:38, spot:100, delta:-0.25,
  iv:0.35, iv_source:'implicita', open_interest:600, spread_pct:5, blocked:false,
  block_reason:null, afford:{ affordable:aff, shortfall:short },
  premium:{ price:2.1, source:'bid', raw:2.1 },
  metrics:{ credit:210, collateral:col, return_pct:2.2, annualized_pct:21,
            breakeven:92, cushion_pct:7, prob_expire_worthless:65 },
  score:{ total:80, annualized:{points:20,max:30,band:'ok',why:'x'},
          iv_rank:{points:15,max:20,band:'ok',why:'x'}, cushion:{points:20,max:25,band:'ok',why:'x'},
          liquidity:{points:15,max:15,band:'ok',why:'x'}, earnings:{points:10,max:10,band:'ok',why:'x'} } });
api.renderProjWheel({
  perfil:{...perfil, caben:1}, preset:'Balanceado', preset_id:'balanceado',
  preset_explain:'x', presets:[{id:'balanceado',label:'Balanceado',explain:'x',
    delta_min:0.2,delta_max:0.3,dte_min:30,dte_max:45,take_profit_pct:50,roll_dte:21}],
  quotes_missing:0, blocked_summary:[], blocked_total:0, scanned:40, failed:0,
  with_candidates:2, rejected:[], degraded:false,
  candidates:[ cand('F', 9, 900, true, 0), cand('NVDA', 95, 9500, false, 8500) ]});
const wheel = store['projWheel'].innerHTML;
console.log('\n── Wheel ───────────────────────────────────────────────');
chk(wheel.includes('$1,000'), 'el capital se pinta entero');
chk(wheel.includes('Te cabe'), 'hay columna de asequibilidad');
chk(wheel.includes('faltan $8,500'), 'dice cuánto falta para el colateral de NVDA');
chk(wheel.indexOf('>F<') < wheel.indexOf('NVDA'), 'lo asequible va primero');
chk(wheel.includes('NVDA'), 'lo que no cabe NO se esconde');

// ── PANTALLA DE PERFIL ───────────────────────────────────────────────────
api.pfPinta({ perfil:{ nombre:'Kevin', email:'k@x.com', capital:1000,
  tolerancia:'agresivo', horizonte:'1-3 años', texto:'Sin cripto.',
  max_posicion_pct:[20,30], riesgo_pct:15, riesgo_por_trade:150, actualizado:'2026-08-06T12:00:00' },
  tolerancias:[{id:'agresivo',label:'Agresivo',riesgo_pct:15,que_significa:'x'},
               {id:'moderado',label:'Moderado',riesgo_pct:5,que_significa:'y'}],
  archivos:{ estructurado:'Perfil Inversionista/perfil.json',
             para_los_agentes:'Perfil Inversionista/Kevin.md' } });
console.log('\n── Pantalla de perfil ──────────────────────────────────');
chk(store['pfNombre'].value === 'Kevin', 'el nombre se rellena');
chk(store['pfEmail'].value === 'k@x.com', 'el email se rellena');
chk(store['pfCapital'].value === 1000, 'el capital se rellena');
chk(store['pfTexto'].value === 'Sin cripto.', 'el texto libre se rellena');
chk(store['pfPosMin'].value === 20 && store['pfPosMax'].value === 30, 'el tope por posición se rellena');
chk(store['pfTolerancias'].innerHTML.includes('data-tol="agresivo"'), 'las bandas se pintan');
chk(!store['pfTolerancias'].innerHTML.includes('onclick'), 'el id NO va dentro de un onclick');
chk(store['pfRiesgoNota'].innerHTML.includes('$150'), 'la nota calcula el riesgo por operación');
chk(store['pfArchivos'].innerHTML.includes('Kevin.md'), 'dice dónde acaba el perfil');

// ── franja sin perfil ────────────────────────────────────────────────────
console.log('\n── Bordes ──────────────────────────────────────────────');
chk(api.vcCabeceraPerfil(null, 0) === '', 'sin perfil no se pinta franja');
chk(api.fmtMoney(1049) === '$1,049' && api.fmtMoney(null) === '—', 'fmtMoney exacto');

console.log(fallos ? `\n\x1b[31m${fallos} fallos\x1b[0m` : '\n\x1b[32mtodo verde\x1b[0m');
process.exit(fallos ? 1 : 0);
