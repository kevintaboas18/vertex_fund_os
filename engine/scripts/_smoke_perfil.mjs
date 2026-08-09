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
  return { renderProjIdeas, renderProjWheel, vcCabeceraPerfil, fmtMoney, pfPinta, pfTab, pfPreguntaHTML, pintaExplicacion };`)();

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
// Su WheelPresetCard: las REGLAS de salida.
chk(/Cierra al <b[^>]*>50%/.test(wheel), 'dice a qué % de la prima se cierra');
chk(/Rola a los <b[^>]*>21 d/.test(wheel), 'dice a cuántos días se rola');
chk(/Delta objetivo/.test(wheel) && /Vencimiento/.test(wheel), 'y la banda del preset');
// Su wheel-grid + wheel-outcomes.
chk(/Colch&oacute;n/.test(wheel), 'hay columna de colchón');
chk(/Si expira sin valor/.test(wheel) && /Si te asignan/.test(wheel)
    && /Si se desploma 20%/.test(wheel), 'los tres escenarios de qué pasa si');
chk(/&Delta; <b[^>]*>-0.25/.test(wheel), 'el delta del contrato');
chk(/IV <b[^>]*>35.0%/.test(wheel), 'la IV con su fuente');
chk(/OI <b[^>]*>600/.test(wheel), 'el open interest');
chk((wheel.match(/vc-esc/g) || []).length >= 2, 'cada candidato lleva su fila de escenarios');

// ── EL CUESTIONARIO ──────────────────────────────────────────────────────
// Las preguntas llegan del servidor, no están escritas en el HTML: el smoke
// las inyecta igual que lo haría `/api/perfil`.
const PREGUNTAS = [
  { id:'objetivos', seccion:'Objetivos', pregunta:'¿Qué buscas con este dinero?',
    ayuda:'Puedes marcar más de uno.', tipo:'multi', campo:'objetivos',
    opciones:[{valor:'crecimiento',label:'Crecimiento de capital',detalle:''},
              {valor:'ingresos',label:'Generación de ingresos',detalle:''}],
    defecto:['crecimiento'] },
  { id:'tolerancia', seccion:'Tolerancia al riesgo', pregunta:'¿Cuánto puedes perder?',
    ayuda:'Es el techo del sizing.', tipo:'opcion', campo:'tolerancia',
    opciones:[{valor:'moderado',label:'Moderado · 5%',detalle:''},
              {valor:'agresivo',label:'Agresivo · 15%',detalle:''}],
    defecto:'agresivo' },
  { id:'capital', seccion:'Capital', pregunta:'¿Con cuánto capital operas?',
    ayuda:'Solo el dinero destinado a esto.', tipo:'numero', campo:'capital', defecto:1000 },
  { id:'max_posicion_pct', seccion:'Reglas', pregunta:'¿Cuánto puede ocupar una posición?',
    ayuda:'Cuánto puedes DESPLEGAR.', tipo:'rango_pct', campo:'max_posicion_pct', defecto:[20,30] },
  { id:'texto', seccion:'En mis palabras', pregunta:'¿Algo más?',
    ayuda:'Contexto para la tesis.', tipo:'texto_largo', campo:'texto', defecto:'',
    opcional:true },
];

globalThis.VX_USUARIO = { id:'u1', nombre:'Kevin', email:'k@x.com' };

const PERFIL = (extra) => ({
  perfil:{ nombre:'Kevin', email:'k@x.com', capital:1000, tolerancia:'agresivo',
           objetivos:['crecimiento'], max_posicion_pct:[20,30], texto:'',
           riesgo_pct:15, riesgo_por_trade:150, respondidas:[], modo:'default',
           respuestas:{ capital:1000, tolerancia:'agresivo', objetivos:['crecimiento'],
                        max_posicion_pct:[20,30], texto:'' },
           actualizado:'2026-08-06T12:00:00', ...(extra||{}) },
  usuario:{ id:'u1', nombre:'Kevin', email:'k@x.com' },
  preguntas: PREGUNTAS,
  archivos:{ para_los_agentes:'Perfil Inversionista/usuarios/Kevin-u1.md' } });

// ── MODO POR DEFECTO: sin preguntas ──────────────────────────────────────
api.pfPinta(PERFIL());
console.log('\n── Perfil por defecto ──────────────────────────────────');
chk(store['pfModo'].innerHTML.includes('Perfil por defecto'), 'ofrece los dos modos');
chk(store['pfModo'].innerHTML.includes('Personalizado'), 'y el personalizado');
chk(store['pfModo'].innerHTML.includes('activa'), 'marca cuál está elegido');
chk(store['pfPreguntas'].innerHTML === '', 'NO salen las preguntas');
chk(store['pfProgreso'].classList.contains('hidden'), 'ni la barra de progreso');

// ── PERSONALIZADO: aparecen ──────────────────────────────────────────────
api.pfPinta(PERFIL({ modo:'personalizado', respondidas:['capital'] }));
const preg = store['pfPreguntas'].innerHTML;
console.log('\n── Personalizado ───────────────────────────────────────');
chk(PREGUNTAS.every(p => preg.includes(p.pregunta)), 'ahora sí, TODAS las preguntas');
chk(PREGUNTAS.every(p => preg.includes(p.ayuda)), 'cada una explica para qué sirve');
chk(!store['pfProgreso'].classList.contains('hidden'), 'y la barra de progreso');
chk((preg.match(/valor heredado/g) || []).length === 3,
    'las 3 obligatorias sin contestar se marcan como heredadas');
chk((preg.match(/>contestada</g) || []).length === 1, 'la contestada se distingue');
chk((preg.match(/>opcional</g) || []).length === 1,
    'la opcional en blanco dice «opcional», no «valor heredado»');
chk(preg.includes('data-preg="tolerancia"') && preg.includes('data-opt="agresivo"'),
    'las opciones llevan el id en data-, no en un onclick');
chk(!preg.includes('onclick'), 'ningún id de pregunta va dentro de un onclick');
chk(preg.includes('type="number"'), 'el capital es un campo numérico');
chk(preg.includes('data-parte="0"') && preg.includes('data-parte="1"'), 'el rango son dos campos');
chk(preg.includes('<textarea'), 'el texto libre es un área de texto');

const prog = store['pfProgreso'].innerHTML;
chk(prog.includes('1 de 4'), 'el progreso cuenta solo las OBLIGATORIAS (4 de 5)');
chk(/Kevin/.test(prog), 'explica de quién es el valor heredado');

// El formulario enseña lo que TÚ escribiste, no lo efectivo. En modo por
// defecto los dos difieren, y confundirlos haría desaparecer tus respuestas.
//
// `respondidas` va poblado a propósito: es lo que manda el servidor
// (`perfil_desde_respuestas` mete ahí cada id que contestas) y es lo que
// distingue «esto lo escribí yo» de «esto es el valor de Kevin». `respuestas`
// no sirve para distinguirlo: trae UNA entrada por pregunta siempre, contestada
// o no, así que mirarlo a él daba por contestado todo el cuestionario.
api.pfPinta(PERFIL({ capital:1000, respuestas:{ capital:250000, tolerancia:'moderado',
                     objetivos:['ingresos'], max_posicion_pct:[5,10], texto:'' },
                     respondidas:['capital','tolerancia','objetivos','max_posicion_pct'],
                     modo:'personalizado' }));
chk(store['pfPreguntas'].innerHTML.includes('value="250000"'),
    'en modo por defecto, el formulario sigue enseñando TUS respuestas guardadas');

// La otra mitad de la misma regla: lo que NO has contestado sale en blanco.
// Antes salía con la respuesta de Kevin ya marcada y parecía elegida por ti.
api.pfPinta(PERFIL({ modo:'personalizado', respondidas:[] }));
const _blanco = store['pfPreguntas'].innerHTML;
chk(!/class="[^"]*pf-opt[^"]*activa/.test(_blanco),
    'sin contestar, ninguna opción sale premarcada');
chk(!/type="number"[^>]*value="(?!")[^"]+"/.test(_blanco),
    'sin contestar, ninguna casilla sale rellena');

// Contestar las obligatorias deja el perfil COMPLETO aunque la opcional siga
// en blanco. Si contara en el denominador, no se llegaria nunca al 100%.
api.pfPinta(PERFIL({ modo:'personalizado',
                     respondidas:['objetivos','tolerancia','capital','max_posicion_pct'] }));
const prog2 = store['pfProgreso'].innerHTML;
chk(prog2.includes('4 de 4') && prog2.includes('100%'),
    'sin escribir la opcional, el perfil llega al 100%');
chk(!/perfil de referencia|Kevin/.test(prog2), 'ya no advierte de que hereda nada');

console.log('\n── Información de la cuenta ────────────────────────────');
const cuenta = store['pfCuenta'].innerHTML;
chk(cuenta.includes('k@x.com'), 'muestra el email');
chk(cuenta.includes('$1,000'), 'muestra el capital entero');
chk(cuenta.includes('$150'), 'muestra el riesgo por operación');
chk(store['pfArchivos'].innerHTML.includes('Kevin-u1.md'), 'dice en qué archivo acaba TU perfil');

// ── franja sin perfil ────────────────────────────────────────────────────
// ── LA EXPLICACIÓN EN PALABRAS ───────────────────────────────────────────
api.pintaExplicacion({
  resumen: 'NVDA saca 78/100. Con tu capital de $1,000 y un tope del 30%, esto\nson $300 como mucho en una posición.',
  riesgo_para_ti: 'Ya tienes exposición a semiconductores; esto no diversifica.',
}, 'gemini-2.5-flash');
const ex = store['qtExplicacion'].innerHTML;
console.log('\n── Explicación en palabras ─────────────────────────────');
chk(!store['qtExplicacion'].classList.contains('hidden'), 'el panel se muestra');
chk(ex.includes('78/100') && ex.includes('$1,000'), 'pinta el texto del LLM');
chk(/riesgo para ti/i.test(ex), 'la clave se lee como titulo, sin guiones bajos');
chk(ex.includes('gemini-2.5-flash'), 'dice de qué proveedor salió');
chk(/no los calcula/.test(ex), 'declara que solo EXPLICA, no calcula');

// Un fallo de formato del proveedor no puede dejar el panel en blanco.
api.pintaExplicacion('Texto suelto sin secciones.');
chk(store['qtExplicacion'].innerHTML.includes('Texto suelto sin secciones.'),
    'si el proveedor devuelve texto plano, se pinta igual');

// ── Tablas cuadradas ─────────────────────────────────────────────────────
//
// Mismo control que en `_smoke_componentes.mjs`, aquí para Ideas y Wheel, que
// son las dos tablas que este smoke sí pinta. `vcTablaResponsive` copia la
// etiqueta de la columna N a la celda N: si sobran celdas, en el móvil todas
// las de la derecha salen con el nombre de la columna equivocada.
console.log('\n── Tablas cuadradas (encabezado = celdas) ───────────────');
for (const id of ['projIdeas', 'projWheel']) {
  const h = (store[id] && store[id].innerHTML) || '';
  const tablas = [...h.matchAll(/<table class="vc-t[\s\S]*?<\/table>/g)].map(m => m[0]);
  chk(tablas.length > 0, `${id}: pinta al menos una tabla`);
  tablas.forEach((t, i) => {
    const ths = (t.match(/<th\b/g) || []).length;
    const cuerpo = t.slice(t.indexOf('<tbody'));
    const filas = [...cuerpo.matchAll(/<tr\b([^>]*)>([\s\S]*?)<\/tr>/g)]
      .filter(m => !/vc-esc/.test(m[1]));
    const malas = filas.map(m => {
      let n = 0;
      for (const c of m[2].matchAll(/<td\b([^>]*)>/g)) {
        const cs = /colspan="(\d+)"/.exec(c[1]);
        n += cs ? Number(cs[1]) : 1;
      }
      return n;
    }).filter(n => n !== ths);
    chk(!malas.length && filas.length > 0,
        `${id}[${i}]: ${ths} encabezados y ${malas.length ? malas.join('/') : ths} celdas por fila`);
  });
}

console.log('\n── Bordes ──────────────────────────────────────────────');
chk(api.vcCabeceraPerfil(null, 0) === '', 'sin perfil no se pinta franja');
chk(api.fmtMoney(1049) === '$1,049' && api.fmtMoney(null) === '—', 'fmtMoney exacto');

console.log(fallos ? `\n\x1b[31m${fallos} fallos\x1b[0m` : '\n\x1b[32mtodo verde\x1b[0m');
process.exit(fallos ? 1 : 0);
