// ══════════════════════════════════════════════════════════════════════════
//  SMOKE DE SUS DOCE COMPONENTES — ejecuta el JS VIVO del HTML, no una copia.
//
//  Mismo motivo que `_smoke_perfil.mjs`, que ya encontró tres fallos que
//  ningún test de texto podía ver: una función puede existir, estar llamada y
//  reventar igual porque lee una clave que el payload no trae. Aquí se corren
//  los doce componentes con payloads realistas y se mira el HTML que producen.
//
//      node engine/scripts/_smoke_componentes.mjs
// ══════════════════════════════════════════════════════════════════════════
import fs from 'node:fs';

const html = fs.readFileSync(new URL('../../vertex_fund_os_platform.html', import.meta.url), 'utf8');
const blocks = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)].map(m => m[1]);
const src = blocks.sort((a, b) => b.length - a.length)[0];

const store = {};
const mkEl = id => ({
  id, innerHTML: '', value: '', innerText: '', dataset: {}, style: {}, textContent: '',
  classList: {
    _s: new Set(), add(...c) { c.forEach(x => this._s.add(x)); },
    remove(...c) { c.forEach(x => this._s.delete(x)); },
    toggle(c, f) { f ? this._s.add(c) : this._s.delete(c); },
    contains(c) { return this._s.has(c); },
  },
  querySelectorAll: () => [], querySelector: () => null, appendChild() {},
  closest: () => null, getContext: () => ({}),
  getBoundingClientRect: () => ({ left: 0, top: 0, width: 900, height: 440 }),
});
globalThis.document = {
  getElementById: id => (store[id] ||= mkEl(id)),
  querySelectorAll: () => [], addEventListener() {},
  createElement: () => mkEl('nuevo'),
  documentElement: { classList: { contains: () => false, toggle() {} }, lang: '' },
};
globalThis.window = { location: { protocol: 'https:' }, matchMedia: () => ({ addEventListener() {} }), addEventListener() {} };
// El watchlist vive en localStorage: aquí es de verdad, para poder marcar y
// desmarcar y ver que la tabla cambia.
const mem = {};
globalThis.localStorage = {
  getItem: k => (k in mem ? mem[k] : null),
  setItem: (k, v) => { mem[k] = String(v); },
  removeItem: k => { delete mem[k]; },
};
globalThis.fetch = async () => { throw new Error('sin red'); };
let graficas = [];
globalThis.Chart = function (ctx, cfg) { graficas.push(cfg); return { destroy() {}, update() {} }; };
globalThis.Chart.register = () => {};
globalThis.window.Chart = globalThis.Chart;   // el panel comprueba `window.Chart`
globalThis.lucide = { createIcons() {} };
// `navigator` ya existe en Node 22 y es de solo lectura: se le cuelga el
// portapapeles en vez de reemplazarlo entero.
if (!globalThis.navigator.clipboard) {
  Object.defineProperty(globalThis.navigator, 'clipboard', { value: { writeText: async () => {} } });
}

const api = new Function(src + `
  return { vcCompanyHTML, vcLoaderHTML, vcActivityHTML, vcMoneyFlowHTML,
           vcCadenaHTML, vcAbreCadena, vcOrdenaCadena, vcPintaCadena,
           renderProjTop5, renderProjFlowMoney, vcRiesgoHTML,
           vcRepeatKey, vcRepeatCounts, vcRepeatBadge,
           wlLocalCarga, wlLocalGuarda, wlUpsert, wlUnderlyings, wlTickerList,
           wlEtiquetaCola, renderProjWatchlist, wlAplica, vcCrosshairCablea,
           vcSyncCabecera, renderProjections };`)();

let fallos = 0;
const chk = (ok, msg) => { console.log((ok ? '  \x1b[32m✓\x1b[0m ' : '  \x1b[31m✗\x1b[0m ') + msg); if (!ok) fallos++; };
const sec = t => console.log('\n── ' + t + ' ' + '─'.repeat(Math.max(2, 56 - t.length)));

const DIA = (d, h) => `2026-08-0${d}T${h}:00:00Z`;

const D = {
  ok: true, ticker: 'WULF', spot: 18.42, score: 62, verdict: 'Alcista moderado',
  gex: { regime: 'positive', iv: 0.55, nodes: [], direction: 'up' },
  company: {
    ticker: 'WULF', name: 'TeraWulf Inc.', exchange: 'NASDAQ', sector: 'Data Processing',
    price: 18.42, change: 0.61, change_percent: 3.42, market_cap: 7.2e9,
    day_volume: 41_200_000, day_low: 17.88, day_high: 18.71, prev_close: 17.81,
    employees: null, has_logo: true,
  },
  structure: { vol_oi: { pct: 63.2, exceeded: 12, considered: 19 } },
  subagents: { conviction: { spread: { avg_pct: 1.44 } } },
  unusual: [{ id: 'u1' }, { id: 'u2' }, { id: 'u3' }],
  conviction_rows: [
    { id: 't1', underlying: 'WULF', type: 'call', strike: 20, expiration: '2027-01-15', premium: 2_400_000, aggression: 'ask', timestamp: DIA(5, 14), repeated: true },
    { id: 't2', underlying: 'WULF', type: 'call', strike: 20, expiration: '2027-01-15', premium: 900_000, aggression: 'ask', timestamp: DIA(5, 15), repeated: true },
    { id: 't3', underlying: 'WULF', type: 'put', strike: 15, expiration: '2026-12-18', premium: 1_100_000, aggression: 'bid', timestamp: DIA(6, 16), repeated: false },
    { id: 't4', underlying: 'WULF', type: 'call', strike: 25, expiration: '2027-01-15', premium: 400_000, aggression: 'mid', timestamp: DIA(7, 14), repeated: false },
  ],
  flow_clusters: [{ start_sec: Date.parse(DIA(5, 14)) / 1000, end_sec: Date.parse(DIA(5, 15)) / 1000, count: 3, premium: 3_300_000, bet: 'alcista' }],
  history: [
    { time: '2026-08-03', open: 17.2, high: 17.9, low: 17.0, close: 17.6 },
    { time: '2026-08-04', open: 17.6, high: 18.2, low: 17.4, close: 18.0 },
    { time: '2026-08-05', open: 18.0, high: 18.7, low: 17.8, close: 18.42 },
  ],
  chain: [
    { option_ticker: 'O:WULF270115C00020000', contract_type: 'call', expiration: '2027-01-15', strike: 20, open_interest: 12000, volume: 3400, price: 4.15, open_premium: 49_800_000, notional_value: 24_000_000 },
    { option_ticker: 'O:WULF261218P00015000', contract_type: 'put', expiration: '2026-12-18', strike: 15, open_interest: 8000, volume: 1200, price: null, open_premium: null, notional_value: 12_000_000 },
    { option_ticker: 'O:WULF270115C00025000', contract_type: 'call', expiration: '2027-01-15', strike: 25, open_interest: 5000, volume: 900, price: 2.05, open_premium: 10_250_000, notional_value: 12_500_000 },
  ],
  chain_meta: { contract_count: 3, expiration_count: 2, truncated: false, capped: false, shown: 3 },
  top_contracts: [
    { option_ticker: 'O:WULF270115C00020000', contract_type: 'call', expiration: '2027-01-15', strike: 20, open_interest: 12000, volume: 3400, price: 4.15, open_premium: 49_800_000, notional_value: 24_000_000 },
    { option_ticker: 'O:WULF270115C00025000', contract_type: 'call', expiration: '2027-01-15', strike: 25, open_interest: 5000, volume: 900, price: 2.05, open_premium: 10_250_000, notional_value: 12_500_000 },
  ],
};
globalThis.window._vcData = D;

// ── CompanyHeader ────────────────────────────────────────────────────────
sec('CompanyHeader');
const ch = api.vcCompanyHTML(D);
chk(ch.includes('TeraWulf Inc.'), 'pinta el nombre real de la empresa');
chk(ch.includes('/api/tito-logo?ticker=WULF'), 'pide el logo por SU ruta de proxy, no a Massive directo');
chk(!/api\.massive\.com/.test(ch), 'la URL de Massive NO llega al navegador');
chk(ch.includes('onerror'), 'si no hay logo cae a las iniciales en vez de dejar un hueco');
chk(ch.includes('$18.42'), 'el precio sale con dos decimales');
chk(ch.includes('+3.42%'), 'la variación lleva signo SIEMPRE (signDisplay de su `pct`)');
chk(ch.includes('$17.88 – $18.71'), 'el rango del día es un RANGO, no un valor');
chk(ch.includes('NASDAQ · Data Processing'), 'bolsa y sector, unidos con su separador');
chk(ch.includes('Empleados') && ch.includes('—'), 'un campo sin dato dice —, no 0');
chk(api.vcCompanyHTML({ ticker: 'X' }) === '', 'sin ficha no pinta nada');

// ── AnalysisLoader ───────────────────────────────────────────────────────
sec('AnalysisLoader');
const pcts = [0, 5, 20, 60, 200].map(n => {
  const m = api.vcLoaderHTML('WULF', n).match(/(\d+)%<\/span>/);
  return m ? Number(m[1]) : -1;
});
chk(pcts.every((v, i) => i === 0 || v >= pcts[i - 1]), 'el progreso solo SUBE: ' + pcts.join(' → '));
chk(pcts[pcts.length - 1] <= 97, 'se topa en 97% hasta que termina (su tope)');
chk(api.vcLoaderHTML('WULF', 0).includes('Conectando con el mercado'), 'arranca en la fase 1');
chk(api.vcLoaderHTML('WULF', 200).includes('Calculando escenarios y niveles'), 'termina en la fase 4');
chk(api.vcLoaderHTML(null, 3).includes('el ticker'), 'sin ticker no dice «null»');

// ── ActivityCard ─────────────────────────────────────────────────────────
sec('ActivityCard');
const ac = api.vcActivityHTML(D);
chk(ac.includes('Unusual Options Activity'), 'su título');
chk(ac.includes('Hoy'), 'el último día se llama «Hoy»');
chk(ac.includes('#12b76a') && ac.includes('#f97066'), 'verde calls / rojo puts, sus colores');
chk(/Trades inusuales detectados[\s\S]{0,120}>3</.test(ac), 'el contador de inusuales sale del payload');
chk(api.vcActivityHTML({ conviction_rows: [] }) === '', 'sin filas no pinta una tarjeta vacía');
// El orden por timestamp real, no por la etiqueta "Aug 5": con etiquetas
// alfabéticas, agosto iría antes que julio.
const cruzado = api.vcActivityHTML({
  ...D, conviction_rows: [
    { type: 'call', premium: 100, timestamp: '2026-08-05T14:00:00Z' },
    { type: 'call', premium: 100, timestamp: '2026-07-30T14:00:00Z' },
  ],
});
chk(cruzado.indexOf('Jul 30') < cruzado.indexOf('Hoy'), 'julio va antes que agosto (ordena por timestamp, no por etiqueta)');

// ── MoneyFlowCard ────────────────────────────────────────────────────────
sec('MoneyFlowCard');
const mf = api.vcMoneyFlowHTML(D);
const alc = Number((mf.match(/(\d+)% alcista/) || [])[1]);
const baj = Number((mf.match(/(\d+)% bajista/) || [])[1]);
chk(alc + baj === 100, `las dos mitades suman 100 exacto (${alc} + ${baj})`);
chk(mf.includes('Ratio Put/Call'), 'el azulejo del Put/Call');
chk(mf.includes('Trade más grande'), 'el azulejo del trade más grande');
chk(mf.includes('$2.40M'), '…con SU formato compacto de dos decimales ($2.40M, no $2.4M)');
chk(mf.includes('1.44%'), 'el spread promedio sale de `subagents.conviction`');
chk(mf.includes('63%'), 'volumen>OI sale de `structure.vol_oi`');
chk(api.vcMoneyFlowHTML({ ticker: 'X', conviction_rows: [] }) === '', 'sin filas, nada');

// ── RepeatBadge ──────────────────────────────────────────────────────────
sec('RepeatBadge');
const cuentas = api.vcRepeatCounts(D.conviction_rows);
chk(cuentas.get(api.vcRepeatKey(D.conviction_rows[0])) === 2, 'cuenta 2 golpes al mismo contrato');
chk(cuentas.get(api.vcRepeatKey(D.conviction_rows[2])) === 1, 'y 1 al que no se repite');
chk(api.vcRepeatKey({ underlying: 'A', strike: 1, type: 'call', expiration: null })
    === api.vcRepeatKey({ underlying: 'A', strike: 1, type: 'call' }),
    'sin vencimiento, `undefined` y `null` son el MISMO contrato (su `?? ""`)');
chk(api.vcRepeatBadge(1).includes('×2'), 'nunca enseña ×1 (su Math.max(count, 2))');
chk(api.vcRepeatBadge(7).includes('×7'), 'y respeta el conteo real');

// ── OptionChainTable ─────────────────────────────────────────────────────
sec('OptionChainTable');
chk(!api.vcCadenaHTML(D).includes('<table'), 'arranca plegada: 600+ filas no se abren solas');
api.vcAbreCadena();
const cad = api.vcCadenaHTML(D);
chk(cad.includes('<table'), 'al abrirla se pinta la tabla');
chk(cad.includes('3</b> contratos') || cad.includes('>3</b> contratos'), 'la cabecera dice cuántos contratos');
chk(cad.includes('2</b> vencimientos'), '…y cuántos vencimientos');
chk(cad.includes('n/a'), 'un precio ausente dice n/a, no 0');
chk(cad.includes('TOTAL'), 'lleva su fila de totales');
// Los nulos SIEMPRE al final, en las dos direcciones. Es su regla y es la que
// impide que «sin precio» se lea como «precio bajo».
api.vcOrdenaCadena('price');
const desc = store['projChain'].innerHTML;
api.vcOrdenaCadena('price');
const asc = store['projChain'].innerHTML;
const posNa = h => h.indexOf('n/a');
chk(posNa(desc) > desc.indexOf('4.15'), 'orden desc: el n/a queda por debajo del precio real');
chk(posNa(asc) > asc.indexOf('2.05'), 'orden asc TAMBIÉN: el nulo no sube al primer puesto');
chk(api.vcCadenaHTML({}) === '', 'sin cadena, nada');

// ── ChartPanel (top 5 por nocional) ──────────────────────────────────────
sec('ChartPanel');
graficas = [];
api.renderProjTop5(D);
const t5 = store['projTop5'].innerHTML;
chk(t5.includes('Top 5 por Notional Value'), 'su título');
chk(t5.includes('#1 · C 20.00'), 'la leyenda numera y etiqueta el contrato');
chk(t5.includes('$24.00M'), 'el nocional en su formato compacto');
chk(graficas.length === 1, 'dibuja UNA gráfica');
chk(graficas[0].data.datasets.length === 3, 'una serie de precio + una línea por contrato');
chk(graficas[0].data.datasets[1].borderDash != null, 'los strikes van PUNTEADOS, no sólidos');
graficas = [];
api.renderProjTop5({ ticker: 'X', top_contracts: [], history: [] });
chk(store['projTop5'].innerHTML === '' && graficas.length === 0, 'sin contratos no deja una gráfica vacía');

// ── FlowPriceChart (el panel del dinero) ─────────────────────────────────
sec('FlowPriceChart');
graficas = [];
api.renderProjFlowMoney(D);
chk(store['projFlowMoney'].innerHTML.includes('Flujo notable sobre el precio'), 'su título');
chk(graficas.length === 1, 'dibuja el histograma');
const [ask, bid] = graficas[0].data.datasets;
chk(ask.data.some(v => v > 0), 'la compra va hacia ARRIBA');
chk(bid.data.some(v => v < 0), 'la venta va hacia ABAJO (histograma espejo)');
chk(ask.data.every(v => v == null || v <= 3), 'la escala es logarítmica: un día de $2,4M no se sale');
// El día del racimo va en el color brillante. Sin convertir `start_sec` a
// milisegundos, `new Date(1754…)` cae en 1970 y NINGÚN día se marcaría.
chk(ask.backgroundColor.includes('#8cffbc'), 'el día con racimo va en el color brillante');
chk(ask.backgroundColor.some(c => c.startsWith('rgba')), '…y los demás, apagados');
graficas = [];
api.renderProjFlowMoney({ ticker: 'X', conviction_rows: [] });
chk(store['projFlowMoney'].innerHTML === '', 'sin flujo, nada');

// ── RiskProfileCard ──────────────────────────────────────────────────────
sec('RiskProfileCard');
const rp = api.vcRiesgoHTML({ capital: 1000, tolerancia: 'agresivo', riesgo_pct: 15,
                              riesgo_por_trade: 150, theta_budget_pct: 5, theta_budget: 50 });
chk(rp.includes('$1,000'), 'el capital sale ENTERO, no abreviado');
chk(rp.includes('15%') && rp.includes('agresivo'), 'el riesgo por trade con su etiqueta');
chk(rp.includes('$150'), 'el techo de capital por operación');
// El de theta es un % de la CUENTA (5% de 1.000 = 50), no del riesgo por
// operación (que daría $7,50 y descartaría contratos perfectamente operables).
chk(rp.includes('$50'), 'la quema de theta es un % de la CUENTA, no del riesgo por trade');
chk(rp.includes('Editar perfil'), 'manda a editarlo a su sitio: una sola verdad sobre tu capital');
chk(api.vcRiesgoHTML(null) === '', 'sin perfil, nada');

// ── WatchlistCard + watchlistLocal ───────────────────────────────────────
sec('WatchlistCard');
const E = (sym, tk, strike) => ({
  symbol: sym, ticker: tk, type: 'call', strike, expiration: '2027-01-15',
  addedAt: '2026-08-05T12:00:00.000Z', entrySpot: 18.42, entryPrice: 4.15,
  entryDte: 160, entryPremium: 2400000, entryThetaPctDaily: -0.9,
  maxContracts: 1, binding: 'riesgo', accountSizeAtEntry: 1000,
  tolerancePctAtEntry: 15, brokerSync: null,
});
api.wlLocalGuarda([E('W20', 'WULF', 20), E('A250', 'AAPL', 250)]);
chk(api.wlLocalCarga().length === 2, 'el watchlist persiste en localStorage');
chk(JSON.stringify(api.wlUnderlyings()) === '["AAPL","WULF"]', 'los subyacentes, únicos y ordenados (los que vigila la campana)');
chk(api.wlTickerList(api.wlLocalCarga()) === 'AAPL, WULF', 'la lista para pegar en el broker');
// `upsert` NO pisa la foto original — esa es la que da valor al histórico.
const conFoto = api.wlUpsert(api.wlLocalCarga(), { ...E('W20', 'WULF', 20), entrySpot: 999 });
chk(conFoto.find(x => x.symbol === 'W20').entrySpot === 18.42, 'volver a marcar NO pisa el spot de entrada original');
chk(conFoto.length === 2, '…y no duplica la fila');
localStorage.setItem('tito.watchlist.broker', 'robinhood');
api.wlAplica({
  brokers: [
    { id: 'robinhood', name: 'Robinhood', kind: 'mcp', granularity: 'contracts', caveat: 'ojo con los spreads', quoteUrl: 'https://robinhood.com/stocks/__T__' },
    { id: 'schwab', name: 'Schwab / thinkorswim', kind: 'link', granularity: 'underlying_only', caveat: null, quoteUrl: 'https://www.schwab.com/research/stocks/quotes/summary/__T__' },
    { id: 'none', name: 'Solo en Tito', kind: 'none', granularity: 'none', caveat: null, quoteUrl: null },
  ],
  pending: [{ ticker: 'WULF', broker: 'robinhood', symbol: 'W20', type: 'call', strike: 20, expiration: '2027-01-15', addedAt: '2026-08-05T12:00:00.000Z', syncedAt: null }],
  failed: [], lastSyncedAt: null,
});
const wl = store['projWatchlist'].innerHTML;
chk(wl.includes('Mi watchlist'), 'su título');
chk(wl.includes('2 contratos guardados'), 'cuenta los contratos, no los tickers');
chk(wl.includes('$20.00C'), 'la etiqueta del contrato: strike + C/P');
chk(wl.includes('$1,000 al 15%'), 'guarda TU sizing del momento, que es lo que se juzga después');
chk(wl.includes('nunca coloca una orden'), 'lo dice explícitamente, como él');
chk(wl.includes('ojo con los spreads'), 'el caveat del broker se enseña');
chk(wl.includes('Pendiente de sincronizar'), 'la cola pendiente se anuncia…');
chk(wl.includes('WULF $20 CALL 2027-01-15'), '…con el contrato ENTERO, no solo el ticker');
chk(wl.includes('Robinhood'), 'el selector trae los brokers del servidor');
// El enlace del broker sale de la plantilla del servidor, no de una URL escrita
// aquí: añadir un broker es tocar `watchlist.py`, no el HTML en dos sitios.
localStorage.setItem('tito.watchlist.broker', 'schwab');
api.renderProjWatchlist();
const wlLink = store['projWatchlist'].innerHTML;
chk(wlLink.includes('https://www.schwab.com/research/stocks/quotes/summary/WULF'),
    'con un broker `link`, la fila abre SU página del ticker');
chk(!wlLink.includes('__T__'), 'el hueco de la plantilla se sustituye, no se pinta');
localStorage.setItem('tito.watchlist.broker', 'robinhood');
api.wlLocalGuarda([]);
api.renderProjWatchlist();
chk(store['projWatchlist'].innerHTML.includes('Marca una idea con'), 'vacío, invita a marcar en vez de dejar un hueco');
chk(api.wlEtiquetaCola({ ticker: 'WULF', symbol: 'W20', type: 'call', strike: 20, expiration: '2027-01-15' })
    === 'WULF $20 CALL 2027-01-15', 'la etiqueta de la cola lleva el contrato entero…');
chk(api.wlEtiquetaCola({ ticker: 'WULF' }) === 'WULF', '…y solo el ticker en la cola vieja de solo-tickers');

// ── ChartCrosshair ───────────────────────────────────────────────────────
sec('ChartCrosshair');
const host = mkEl('grafica');
let cruzEl = null;
host.appendChild = e => { cruzEl = e; };
host.querySelector = () => cruzEl;
const geo = {
  width: 900, height: 440, plotLeft: 10, plotRight: 768, plotTop: 18, plotBottom: 410,
  bars: D.history,
  priceAt: y => 17 + (410 - y) / 392 * 2,
  barAt: x => x > 400 ? null : { label: '08-04', open: 17.6, high: 18.2, low: 17.4, close: 18.0 },
  daysAt: x => x <= 400 ? null : (x - 400) / 368 * 20,
};
api.vcCrosshairCablea(host, geo);
chk(typeof host.onmousemove === 'function', 'se cablea el movimiento del cursor');
host.onmousemove({ clientX: 200, clientY: 200 });
chk(cruzEl.innerHTML.includes('08-04'), 'dentro del histórico enseña la VELA…');
chk(cruzEl.innerHTML.includes('$17.40'), '…con su mínimo, no solo el cierre');
host.onmousemove({ clientX: 700, clientY: 200 });
chk(/en \d+ días/.test(cruzEl.innerHTML), 'pasado AHORA enseña a cuántos días cae');
chk(cruzEl.innerHTML.includes('right:'), 'y el tooltip se VOLTEA cerca del borde derecho');
host.onmousemove({ clientX: 200, clientY: 5 });
chk(cruzEl.style.display === 'none', 'fuera del área de dibujo se esconde');
host.onmouseleave();
chk(cruzEl.style.display === 'none', 'y al salir del lienzo, también');

// ── HeaderBar ────────────────────────────────────────────────────────────
sec('HeaderBar');
api.vcSyncCabecera(D);
const hb = store['projHbRight'].innerHTML;
chk(hb.includes('TeraWulf Inc.'), 'la barra lleva el nombre de la empresa cargada');
chk(hb.includes('$18.42'), '…su precio');
chk(hb.includes('+3.42%'), '…y la variación con signo');
chk(!/Tito Metralleta/i.test(hb), 'y NO lleva su marca: esta pantalla es Vertex');
api.vcSyncCabecera({ ticker: 'X', spot: 1 });
chk(store['projHbRight'].innerHTML === '', 'sin ficha, la barra se vacía en vez de quedarse con la anterior');

console.log('\n' + (fallos ? `\x1b[31m${fallos} FALLO(S)\x1b[0m` : '\x1b[32mtodo verde\x1b[0m'));
process.exit(fallos ? 1 : 0);
