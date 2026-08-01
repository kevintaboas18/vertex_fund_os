#!/usr/bin/env python3
"""Auditoría del port de Tito contra el motor de Víctor.

    python engine/scripts/auditar_tito.py

Comprueba, sin red y en segundos, que el port sigue fiel al original:
umbrales de las 6 tablas de puntuación, fuentes de datos, las reglas
innegociables (cono de 2σ, salvaguarda de liquidez, None vs 0), la memoria,
los parámetros de la gráfica, las noticias y que ninguna credencial se filtre
en un mensaje de error.

La sección de cobertura de módulos necesita el repo de Víctor clonado al lado;
apúntalo con TITO_ROOT=/ruta/a/agente-tito-metralleta. Sin esa variable se
salta esa sección y el resto corre igual.
"""
from __future__ import annotations
import json, os, re, subprocess, sys, tempfile, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

VERTEX = Path(os.environ.get("VERTEX_ROOT") or Path(__file__).resolve().parents[2])
TITO = Path(os.environ.get("TITO_ROOT", "")) / "web" if os.environ.get("TITO_ROOT") else None
sys.path.insert(0, str(VERTEX / "engine"))
sys.path.insert(0, str(VERTEX / "engine" / "tests"))

OK, FAIL, WARN = [], [], []
def chk(cond, msg, warn_if_false=False):
    (OK if cond else (WARN if warn_if_false else FAIL)).append(msg)
    print(f"  {'✓' if cond else ('!' if warn_if_false else '✗')} {msg}")

def sec(t): print(f"\n\033[1m── {t} ──\033[0m")

API = (VERTEX / "vertex_api.py").read_text()
HTML = (VERTEX / "vertex_fund_os_platform.html").read_text()
RENDER = (VERTEX / "render.yaml").read_text()
TITO_DIR = VERTEX / "engine" / "wbj" / "tito"

# ─────────────────────────────────────────────────────────────────────
sec("1. Cobertura de módulos (lib/*.ts de Víctor → wbj/tito/*.py)")
MAPA = {
    "blackScholes.ts": "black_scholes.py", "conditions.ts": "conditions.py",
    "expectedMove.ts": "expected_move.py", "flow.ts": "flow.py", "gex.ts": "gex.py",
    "gexHeatmap.ts": "gex_heatmap.py", "ivcontext.ts": "ivcontext.py",
    "levels.ts": "levels.py", "marketsnack.ts": "marketsnack.py",
    "massive.ts": "massive.py", "occ.ts": "occ.py", "prediction.ts": "prediction.py",
    "risk.ts": "risk.py", "structure.ts": "structure.py", "validation.ts": "validation.py",
    "chainStore.ts": "stores.py", "ivStore.ts": "stores.py",
    "predictionStore.ts": "stores.py", "store.ts": "stores.py",
    "compute.ts": "compute.py", "barsStore.ts": "bars_store.py",
    "types.ts": "(dataclasses)",
    "chartGeometry.ts": "(JS en el HTML)", "news.ts": "news.py",
}
FUERA = {  # deferidos a propósito, no son del motor de análisis
    "wheel.ts", "wheelAfford.ts", "wheelUniverse.ts", "earnings.ts",
    "watchlist.ts", "watchlistLocal.ts", "watchlistStore.ts", "outboxStore.ts",
}
libs = {p.name for p in TITO.glob("lib/*.ts") if ".test." not in p.name} if TITO and TITO.exists() else set()
if not libs:
    print("  · saltada: define TITO_ROOT con el repo de Víctor para comparar módulo a módulo")
for lib in sorted(libs - FUERA):
    dest = MAPA.get(lib)
    if dest is None:
        chk(False, f"{lib:<22} → SIN PORTAR", warn_if_false=True)
    elif dest.startswith("("):
        chk(True, f"{lib:<22} → {dest}")
    else:
        chk((TITO_DIR / dest).exists(), f"{lib:<22} → {dest}")
print(f"  · {len(FUERA)} módulos fuera de alcance a propósito (Wheel, watchlist, broker)")

# ─────────────────────────────────────────────────────────────────────
sec("2. Umbrales del scorecard (contra las tablas de Víctor)")
from wbj.tito import flow as F, structure as S, ivcontext as IV, validation as V, risk as R
from wbj.tito.prediction import WEIGHTS, HORIZONS, CALIBRATION

chk(sum(WEIGHTS.values()) == 100, f"los 6 pesos suman 100 {tuple(WEIGHTS.values())}")
chk(WEIGHTS == {"aggression":20,"conviction":20,"unusuality":20,
                "structure":15,"iv_context":10,"validation":15}, "pesos 20/20/20/15/10/15")
chk([h["days"] for h in HORIZONS] == [10,20,30], "HORIZONS = 10/20/30")
chk((F.BIG_PREMIUM, F.CONVICTION_PREMIUM, F.CONVICTION_DELTA) == (1_000_000, 100_000, 0.6),
    "flags: $1M · $100K · |Δ|>0.60")
chk((F.REPEAT_WINDOW_SEC, F.REPEAT_MIN_COUNT) == (300, 3), "repetidas: ≥3 en 5 min")
chk(F.LEAP_DTE == 90 and F.UNUSUAL_TOTAL == 24 and F.UNUSUAL_TRADE_THRESHOLD == 7,
    "LEAP 90d · inusual 24/30 · institucional 7/10")
chk([F.volume_score(n,0) for n in (150,100,50,20)] == [10,8,6,4], "volumen 150/100/50/20 → 10/8/6/4")
chk([F.expiry_score(d) for d in (320,120,90,60,30,7)] == [10,10,8,7,5,2],
    "vencimiento 320/120/90/60/30/<30 → 10/10/8/7/5/2")
chk([F.order_size_score(p) for p in (6e6,2e6,7e5,3e5,15e4)] == [10,8,7,5,3], "tamaño de orden")
chk([F.delta_score(d) for d in (.9,.75,.65,.55,.3)] == [10,8,7,5,0], "delta")
chk([F.theta_score(t) for t in (.5,2,4,9)] == [10,8,5,0], "theta % diario")
chk([F.gamma_score(g) for g in (.02,.1,.3,.005)] == [10,8,4,2], "gamma (zona institucional)")
chk([F.spread_score(p) for p in (1.5,2,8,15)] == [10,7,4,0], "spread")
chk([F.dominance_score(p) for p in (85,75,65,57,52,49)] == [10,8,6,4,2,0], "dominancia")
chk([S.notional_score(n) for n in (7e8,2e8,7e7,3e7,1e7)] == [10,8,6,4,2], "nocional por strike")
chk((S.LOW_LIQUIDITY_NOTIONAL, S.STRIKE_DOMINANCE_PCT, S.TOP_STRIKES_CONSIDERED)
    == (25_000_000, 60, 5), "baja liquidez $25M · dominancia 60% · top-5")
chk([IV.iv_points(v).points for v in (120,95,75,50,35,25)] == [6,5,8,10,5,2], "bandas de IV")
chk([IV.iv_rank_points(r).points for r in (10,20,40,60,80,100)] == [2,10,8,5,1,0], "bandas de IV Rank")
chk(IV.MIN_IV_HISTORY_DAYS == 60, "IV Rank real a los 60 días")
chk((V.MOVE_THRESHOLD_PCT, V.THRESHOLD_ATR_MULTIPLE, V.HORIZON_SESSIONS, V.BACKTEST_TARGET_DAYS)
    == (2.0, 1.5, 20, 60), "validación: piso 2% · ATR×1.5 · 20 sesiones · 60 días")
chk((R.MAX_THETA_PCT_DAILY, R.THETA_BUDGET_PCT, R.MIN_DTE) == (5.0, 5.0, 7),
    "riesgo: theta ≤5%/día · presupuesto 5% · DTE ≥7")
chk(CALIBRATION == {"min_samples":5,"gain":0.6,"cap_pct":3.0}, "calibración: 5 muestras · 60% · ±3%")

# ─────────────────────────────────────────────────────────────────────
sec("3. Fuentes de datos")
chk('BASE_URL = "https://app.marketsnack.com"' in (TITO_DIR/"marketsnack.py").read_text(),
    "tape ← MarketSnack (app.marketsnack.com)")
chk('BASE_URL = "https://api.massive.com"' in (TITO_DIR/"massive.py").read_text(),
    "cadena ← Massive (api.massive.com)")
i, j = API.index("def _tito_chain_and_bars"), API.index("def _tito_memory")
blk = API[i:j]
chk("yf.Ticker" not in blk and "tk.option_chain" not in blk, "sin yfinance en la ruta de Tito")
chk("fetch_option_chain" in blk and "fetch_daily_bars" in blk, "usa los dos endpoints de Massive")
chk('"source": "massive"' in API, "el fallo de Massive se reporta con su motivo")
chk('out["chain_source"] = "massive"' in API, "el reporte declara la procedencia de la cadena")
srcs = {m for m in re.findall(r'os\.environ\.get\("(\w+)"', "\n".join(
    p.read_text() for p in TITO_DIR.glob("*.py")))}
chk(srcs == {"MARKETSNACK_COOKIE","MASSIVE_API_KEY","MASSIVE_MAX_PAGES","WBJ_TITO_DATA"},
    f"el motor solo lee 4 variables: {sorted(srcs)}")

# ── compute.ts: las fórmulas de la cadena ────────────────────────────
from wbj.tito import compute as C
from wbj.tito.structure import ChainRow
chk(C.contract_price({"last_trade":{"price":1.25},"day":{"close":1.1,"vwap":1.05}})
    == (1.25,"last_trade"), "precio: prefiere last_trade")
chk(C.contract_price({"day":{"close":1.1,"vwap":1.05}}) == (1.1,"day_close")
    and C.contract_price({"day":{"vwap":1.05}}) == (1.05,"day_vwap")
    and C.contract_price({}) == (None,"none"), "cascada last_trade → close → vwap → none")
chk(C.contract_price({"last_trade":{"price":0},"day":{"close":2}}) == (2,"day_close"),
    "ignora precios no positivos")
chk(C.contract_price({"last_trade":{"price":"1.25"},"day":{"close":2}}) == (2,"day_close"),
    'un precio en texto no cuenta (typeof === "number")')
chk(C.open_premium(60,1.25) == 75 and C.open_premium(60,None) is None,
    "open premium = OI × precio, None si no hay precio")
chk(C.notional_value(60,205) == 60*100*205 and C.notional_value(10,50,10) == 10*10*50,
    "nocional respeta shares_per_contract")
_aj = C.to_row({"details":{"strike_price":250,"shares_per_contract":10,
                           "expiration_date":"2026-09-18","contract_type":"call"},
                "open_interest":30_000})
chk(_aj.notional_value == 30_000*10*250,
    "un contrato AJUSTADO no se valora como uno normal (era 10× de más)")
chk(C.to_row({}).contract_type == "call" and C.to_row({}).open_premium is None,
    "contract_type ausente → call; campos faltantes → 0/None")
_rr2 = [ChainRow("call","2026-09-18",100.0,oi,0,0.0) for oi in (5,100,27)]
chk([r.open_interest for r in C.sort_by_open_interest_desc(_rr2)] == [100,27,5]
    and _rr2[0].open_interest == 5, "orden por OI desc sin mutar el original")
chk(C.count_expirations([ChainRow("call",e,100.0,0,0,0.0)
    for e in ("2026-07-22","2026-07-22","2026-08-21","")]) == 2, "cuenta vencimientos distintos")
chk("to_row(c)" in (TITO_DIR/"massive.py").read_text(),
    "massive usa compute.to_row (una sola conversión, como el original)")
# Las DOS reglas de tipo de Víctor: estricta para el precio, laxa (`??` + la
# aritmética de JS) para OI/strike/volumen/acciones. Aplicar la estricta a los
# cuatro llenaba la cadena de ceros en silencio si la fuente cambia de tipo.
_txt = C.to_row({"open_interest":"500","details":{"strike_price":"205","shares_per_contract":"100"},
                 "day":{"volume":"81"}})
chk(_txt.open_interest == 500 and _txt.strike == 205 and _txt.volume == 81
    and _txt.notional_value == 500*100*205,
    "números en TEXTO siguen contando (regla `??`, no `typeof`)")
chk(C.to_row({"open_interest":"abc","details":{"strike_price":100}}).notional_value == 0,
    "la basura no numérica cae al fallback, no a NaN")
chk(C.to_row({"open_interest":10,"details":{"strike_price":100,"shares_per_contract":0}})
    .notional_value == 0, "shares=0 se respeta (`??` solo cambia null)")
_coh = C.to_row({"open_interest":60.5,"details":{"strike_price":100}})
chk(_coh.notional_value == _coh.open_interest*100*100,
    "la fila y sus fórmulas usan el MISMO open interest")
chk(all(C.to_row({"details":{"expiration_date":e}}).expiration == "2026-09-18"
        for e in ("2026-09-18","2026-09-18T00:00:00","2026-09-18T00:00:00Z")),
    "el vencimiento queda canónico (es la clave de agrupación del sub-agente 4)")
# 2ª pasada: el tipo de contrato NO puede depender del case, y una fila mala
# no puede llevarse la página entera.
chk(all(C.to_row({"details":{"contract_type":t}}).contract_type == "put"
        for t in ("put","PUT","Put"," put ")),
    'un "PUT" sigue siendo put (si no, el GEX cambia de signo)')
chk(all(C.to_row({"details":{"contract_type":t}}).contract_type == "call"
        for t in ("call","CALL","P","",None)), "lo que no es put es call")
_gcad = lambda m: [C.to_row({"details":{"contract_type":ct.upper() if m else ct,
        "expiration_date":"2026-09-18","strike_price":float(s),"shares_per_contract":100},
        "day":{"volume":400},"open_interest":9000 if ct=="put" else 3000})
        for s in range(90,115,5) for ct in ("call","put")]
from wbj.tito.gex import gex_analysis as _gexa
_now2 = datetime(2026,7,31,tzinfo=timezone.utc)
_g1, _g2 = _gexa(_gcad(False),[100.0]*60,100.0,_now2), _gexa(_gcad(True),[100.0]*60,100.0,_now2)
chk(_g1.total_net_gex == _g2.total_net_gex and _g1.regime == _g2.regime,
    "el GEX y el régimen no cambian con la cadena en MAYÚSCULAS")
_raras = 0
for _c in ({"details":"texto"}, {"details":5}, {"details":[]}, {"open_interest":"NaN"},
           {"last_trade":"x","day":{"close":2}}, None, [], "x"):
    try: C.to_row(_c)
    except Exception: _raras += 1
chk(_raras == 0, "ninguna fila malformada lanza (tumbaría la página entera)")
# 3ª pasada: shares ILEGIBLE no es AUSENTE — caer al 100 fabrica el
# multiplicador estándar justo donde no hay evidencia de cuál es.
chk(all(C.to_row({"open_interest":100,"details":{"strike_price":9000,
        "shares_per_contract":b}}).notional_value == 0
        for b in ("abc","","   ",[],{},"NaN")),
    "shares_per_contract ilegible NO cae al 100 (no se inventa el multiplicador)")
chk(C.to_row({"open_interest":100,"details":{"strike_price":9000}}).notional_value
    == 100*100*9000
    and C.to_row({"open_interest":100,"details":{"strike_price":9000,
        "shares_per_contract":None}}).notional_value == 100*100*9000,
    "shares_per_contract ausente o null SÍ cae al 100 (`?? 100`)")
chk(C.to_row({"open_interest":True,"details":{"strike_price":100}}).notional_value == 100*100
    and C.to_row({"open_interest":False,"details":{"strike_price":100}}).notional_value == 0
    and C.contract_price({"last_trade":{"price":True},"day":{"close":2}}) == (2,"day_close"),
    "booleanos: la regla laxa los convierte, la estricta los rechaza")
# 4ª pasada: un precio infinito no puede acabar en el JSON, y las dos funciones
# que faltaba cablear.
chk(C.contract_price({"last_trade":{"price":float("inf")},"day":{"close":2}}) == (2,"day_close")
    and C.contract_price({"last_trade":{"price":float("inf")}}) == (None,"none"),
    "un precio infinito cae al siguiente de la cascada (no contamina el JSON)")
_inf = C.to_row({"last_trade":{"price":float("inf")},"open_interest":100,
                 "details":{"strike_price":50}})
def _no_constante(c): raise ValueError(c)
try:
    json.loads(json.dumps({"p":_inf.price,"op":_inf.open_premium}),
               parse_constant=_no_constante)
    _js_ok = True
except ValueError: _js_ok = False
chk(_js_ok, "la fila siempre serializa a JSON estricto")
chk(C.to_row({"details":{"ticker":0}}).option_ticker == "0"
    and C.to_row({"details":{}}).option_ticker == "",
    "el ticker usa `?? \"\"`, no `or \"\"` (solo el ausente cae a vacío)")
_msrc = (TITO_DIR/"massive.py").read_text()
chk("sort_by_open_interest_desc(rows)" in _msrc and "count_expirations(rows)" in _msrc,
    "sortByOpenInterestDesc y countExpirations CABLEADAS (como su /api/chain)")
chk("expiration_count" in _msrc, "ChainResult lleva expirationCount, como su ChainMeta")
# 5ª pasada: el PRODUCTO también desborda, y la forma de la respuesta.
chk(all(C.to_row({"open_interest":a,"details":{"strike_price":b,
        "shares_per_contract":c}}).notional_value == 0
        for a,b,c in ((1e200,1e200,100),(1e308,10,100),(1e160,1e160,1e160))),
    "un nocional desbordado no llega a la fila (entradas finitas, producto inf)")
chk(C.to_row({"open_interest":1e200,"last_trade":{"price":1e200},
              "details":{"strike_price":1}}).open_premium is None,
    "un open premium desbordado tampoco")
_ov = C.to_row({"open_interest":1e200,"last_trade":{"price":1e200},
                "details":{"strike_price":1e200}})
try:
    json.loads(json.dumps({"a":_ov.open_premium,"b":_ov.notional_value,"c":_ov.price}),
               parse_constant=_no_constante)
    _ov_ok = True
except ValueError: _ov_ok = False
chk(_ov_ok, "la fila desbordada sigue serializando a JSON estricto")
import wbj.tito.massive as _MA
_orig_get = _MA._get
os.environ.setdefault("MASSIVE_API_KEY", "x"*32)
_limpios = 0
for _p in ([{"details":{}}], None, "texto", {"results":"t"}, {"results":{"a":1}}, {"results":5}):
    _MA._get = (lambda *a, _r=_p, **k: _r)
    try: _MA.fetch_option_chain("DEMO")
    except _MA.MassiveError: _limpios += 1
    except Exception: pass
_MA._get = (lambda *a, **k: {"status":"OK"})
try: _sin = _MA.fetch_option_chain("DEMO").rows == []
except Exception: _sin = False
_MA._get = _orig_get
chk(_limpios == 6, f"toda respuesta mal formada sale como MassiveError ({_limpios}/6)")
chk(_sin, "`results` ausente es cadena vacía, no un error")

# ── barsStore.ts: cache de barras por día de mercado ──────────────────
# Traducción literal. Las cuatro reglas que llegué a añadir aquí (horario de
# sesión, campo `days`, recorte de ventana, cache viejo al fallar la red) están
# QUITADAS: ninguna estaba en el original y cada una trajo su propio fallo.
from wbj.tito import bars_store as BS
from wbj.tito.levels import LvlBar
_ET = __import__("zoneinfo").ZoneInfo("America/New_York")
_HOY = datetime(2026,7,31,21,tzinfo=timezone.utc)
_AYER = _HOY - timedelta(days=1)
with tempfile.TemporaryDirectory() as _tdb:
    os.environ["WBJ_TITO_DATA"] = _tdb
    _bb = [LvlBar("2026-07-30",101,99,100)]
    _n = []
    def _f(t, days): _n.append((t, days)); return _bb
    BS.cached_daily_bars("D", now=_HOY, fetch=_f)
    BS.cached_daily_bars("D", now=_HOY, fetch=_f)
    BS.cached_daily_bars("D", now=_HOY, fetch=_f)
    chk(_n == [("D", 365)], f"el cache ahorra la red y pasa `days` tal cual ({len(_n)} llamadas)")
    _n.clear()
    for _h in (8, 11, 15, 18):   # pre-market, sesión, sesión, cerrada
        BS.cached_daily_bars("HOR", now=datetime(2026,7,31,_h,tzinfo=_ET), fetch=_f)
    chk(len(_n) == 1, "el cache vale para TODO el día de mercado (sin regla de horario)")
    _c = BS.load_bars("D")
    chk(_c is not None and _c.date == "2026-07-31" and _c.ticker == "D",
        "BarsFile = {ticker, date, bars}, como su interface")
    _n.clear()
    BS.cached_daily_bars("AY", now=_AYER, fetch=_f)
    BS.cached_daily_bars("AY", now=_HOY, fetch=_f)
    chk(len(_n) == 2, "un cache de ayer no vale para hoy")
    _n.clear()
    BS.cached_daily_bars("VAC", now=_HOY, fetch=lambda t, d: [])
    BS.cached_daily_bars("VAC", now=_HOY, fetch=_f)
    chk(len(_n) == 1, "un cache vacío no cuenta como cache (`bars.length > 0`)")
    def _boom(t, days): raise RuntimeError("caído")
    chk(BS.cached_daily_bars("NUEVO", now=_HOY, fetch=_boom) == [],
        "si falla la red devuelve [] (`.catch(() => [])`, sin usar cache viejo)")
    BS.save_bars("VIEJO", _bb, _AYER)
    BS.cached_daily_bars("VIEJO", now=_HOY, fetch=_boom)
    chk(BS.load_bars("VIEJO").bars == _bb, "y no borra el cache que ya había")
    _src = (TITO_DIR/"bars_store.py").read_text()
    chk(not any(x in _src for x in ("mercado_abierto","_datos_congelados","_recorta",
                                    'days: int = 0','"days"')),
        "sin las 4 reglas que no están en el original")
    chk(BS._file_for("!!!").name == ".json" and BS._file_for(" demo ").name == "DEMO.json"
        and BS._file_for("brk/b").name == "BRKB.json",
        "el saneado del ticker es el suyo, sin guardas extra")
    chk(BS._file_for("../../ETC/X").resolve().parent == (BS.data_dir()/"bars").resolve(),
        "su propio regex ya cierra la travesía de rutas")
    BS.save_bars(" demo ", _bb, _HOY)
    chk(BS.load_bars("DEMO").ticker == " DEMO ",
        "el ticker guardado NO se recorta (`ticker.toUpperCase()`, sin trim)")
    # Los 2 BUGS de su barsStore.ts, REPLICADOS a propósito para que el port sea
    # idéntico. Arreglo propuesto: engine/scripts/upstream-tito-barsstore.patch.
    _bp = BS.data_dir()/"bars"; _bp.mkdir(parents=True, exist_ok=True)
    _hoy_et = _HOY.astimezone(_ET).date().isoformat()
    (_bp/"A.json").write_text('{"ticker":"A","date":"'+_hoy_et+'"}')
    try:
        BS.cached_daily_bars("A", now=_HOY, fetch=lambda t,d: _bb); _b1 = False
    except TypeError:
        _b1 = True
    chk(_b1, "BUG 1 de Víctor replicado: cache sin `bars` lanza TypeError")
    (_bp/"B.json").write_text('{"ticker":"B","date":"'+_hoy_et+'","bars":"texto"}')
    chk(BS.cached_daily_bars("B", now=_HOY, fetch=lambda t,d: _bb) == "texto",
        "BUG 2 de Víctor replicado: un `bars` de texto se devuelve tal cual")
    (_bp/"E.json").write_text('[1,2]')
    _e = BS.load_bars("E")
    chk(_e is not None and _e.ticker is None and _e.bars is None,
        "load_bars no valida nada (`JSON.parse(raw) as BarsFile`)")
_blkb = API[API.index("def _tito_chain_and_bars"):API.index("def _tito_memory")]
chk("cached_daily_bars" not in _blkb and "fetch_daily_bars(ticker)" in _blkb,
    'no cableado fuera de Wheel ("fetchDailyBars sigue sin cache para el resto de rutas")')

# 6ª pasada: un nocional que RESTA es peor que uno que falta.
chk(all(C.to_row(_c).notional_value >= 0 for _c in (
        {"open_interest":-500,"details":{"strike_price":100}},
        {"open_interest":500,"details":{"strike_price":-100}},
        {"day":{"volume":-9}},
        {"open_interest":10,"details":{"strike_price":100,"shares_per_contract":-100}})),
    "ningún negativo produce un nocional negativo")
_sana = [C.to_row({"details":{"contract_type":ct,"strike_price":float(s),
         "expiration_date":"2026-09-18","shares_per_contract":100},
         "day":{"volume":400},"open_interest":9000})
         for s in range(90,115,5) for ct in ("call","put")]
_sucia = _sana + [C.to_row({"details":{"contract_type":"call","strike_price":100.0,
          "expiration_date":"2026-09-18","shares_per_contract":100},
          "day":{"volume":400},"open_interest":-900_000})]
chk(S.structure_score(_sana).score == S.structure_score(_sucia).score
    and not S.structure_score(_sucia).notional["low_liquidity"],
    "una fila con OI negativo no tira la cadena entera")
# El diferencial completo vive en engine/scripts/diff_compute.sh (necesita node
# + el repo de Víctor); aquí solo se avisa de que existe.
if TITO and TITO.exists():
    print("  · diferencial de 604 casos: engine/scripts/diff_compute.sh "
          "(última corrida: sin diferencias reales)")

# ─────────────────────────────────────────────────────────────────────
sec("4. Reglas innegociables")
from wbj.tito.expected_move import LevelInput
from wbj.tito.prediction import SubScores, predict_pro
import math
sub = SubScores(8,7,9,6,5,6)
roto = 0
for nodes in ([LevelInput(5000,1.0,"call",9e9)], [], [LevelInput(92,1.0,"put",-5e6)],
              [LevelInput(110,1,"call",5e6), LevelInput(92,.7,"put",-3e6)]):
    for h in (10,20,30):
        p = predict_pro(spot=100, iv=0.5, horizon_days=h, nodes=nodes, scores=sub, regime="positive")
        em2 = 100*math.exp(2*0.5*math.sqrt(h/365))
        em2l = 100*math.exp(-2*0.5*math.sqrt(h/365))
        if not (p.bear.target < p.base.target < p.bull.target): roto += 1
        if p.bull.target > em2 + 1e-6: roto += 1
        if p.base.target > em2 + 1e-6 or p.base.target < em2l - 1e-6: roto += 1
        if p.bear.target < em2l - 1e-6: roto += 1
chk(roto == 0, "orden estricto + LOS TRES dentro del cono de 2σ (12 configuraciones)")
p_il = predict_pro(spot=100, iv=.5, horizon_days=20, nodes=[LevelInput(105,1,"call",1e6)],
                   scores=sub, regime="positive", low_liquidity=True)
chk(p_il.confidence == 0 and "NO FIABLE" in p_il.caveat, "baja liquidez → confianza 0 + NO FIABLE")
from wbj.tito.risk import RiskProfile, size_flow
from tests.tito.test_risk import row as rrow
chk(size_flow(rrow(), RiskProfile(10_000,4), 20, low_liquidity=True).max_contracts == 0,
    "cadena ilíquida → no se dimensiona")
from tests.tito.test_scorecard import bars, chain, trades, NOW, SPOT
from wbj.tito.scorecard import run_scorecard
sin = run_scorecard("DEMO", [], chain(), bars(), NOW, spot=SPOT)
chk(sin.scores["aggression"] is None, "categoría sin evidencia → None, nunca 0")
chk(any("NO es comparable" in w for w in sin.warnings), "avisa que un score parcial no es comparable")
chk(any("SALVAGUARDA DE LIQUIDEZ" in w for w in
        run_scorecard("D", [], [S.ChainRow("call","2026-09-18",100.0,5,1,50_000)],
                      bars(), NOW, spot=SPOT).warnings), "salvaguarda de liquidez en warnings")

# ─────────────────────────────────────────────────────────────────────
sec("5. Memoria (los 3 que no arrancan sin persistencia)")
with tempfile.TemporaryDirectory() as td:
    os.environ["WBJ_TITO_DATA"] = td
    viejos = [{"id":900+i,"timestamp":(NOW-timedelta(days=40-i)).isoformat(),"type":"call",
               "strike":100.0,"expiration":"2026-12-18","asset_price":95.0,
               "premium":500_000,"aggression":"ask"} for i in range(12)]
    ivh = [{"date":(NOW-timedelta(days=d)).date().isoformat(),"avg_iv":40+d*.15} for d in range(65)]
    con = run_scorecard("DEMO", trades(), chain(), bars(), NOW, spot=SPOT,
                        iv_history=ivh, past_flows=viejos, calibration={"bias_pct":2.0,"samples":8})
    chk(con.active == 6, f"con memoria: {con.active}/6 categorías activas")
    chk(con.scores["validation"] is not None, "sub-agente 6 se enciende con flows acumulados")
    chk(con.iv_context.rank["source"] == "iv-history", "IV Rank pasa de proxy a historia real")
    chk(con.predictions[20].calibration["applied"], "calibración aplicada con ≥5 vencidas")
chk("WBJ_TITO_DATA" in RENDER and "/var/data/tito" in RENDER, "WBJ_TITO_DATA declarado en Render")
chk("BORRA en cada reinicio" in RENDER.replace("\n","").replace("#","").replace("      "," "),
    "el blueprint avisa de que el plan free borra la memoria")

# ── store.ts: la forma del almacén de trades es la suya, no una lista pelada ──
from wbj.tito import stores as _ST
from wbj.tito.flow import FlowFlags, FlowRow, TradeScores
from dataclasses import replace as _replace
chk(_ST.MAX_PER_TICKER == 5000, "tope por CANTIDAD: 5000 por ticker (no por días)")
chk(not hasattr(_ST, "TRADES_DAYS"), "sin ventana temporal en trades (store.ts no la tiene)")
chk({f.name for f in _ST.SaveResult.__dataclass_fields__.values()}
    == {"total", "added", "first_seen"}, "SaveResult { total, added, firstSeen }")
chk({f.name for f in _ST.StoredTrades.__dataclass_fields__.values()}
    == {"ticker", "updated_at", "trades"}, "StoredTrades { ticker, updatedAt, trades }")
with tempfile.TemporaryDirectory() as td:
    os.environ["WBJ_TITO_DATA"] = td
    def _fr(i, ts, status="vigente"):
        return FlowRow(id=i, symbol="X", underlying="X", type="call", strike=100.0,
                       expiration="2026-12-18", dte=50, price=2.0, size=10, side="AT_ASK",
                       aggression="ask", asset_price=95.0, bid=1.9, ask=2.1, premium=5e5,
                       delta=.6, gamma=.03, theta=-.05, vega=.1, theta_pct_daily=2.5,
                       iv=.45, open_interest=1000, volume=500, score=8, sentiment="bullish",
                       timestamp=ts, condition_code=None, condition_name=None,
                       flags=FlowFlags(), scores=TradeScores(), expiry_status=status)
    chk(_ST.load_trades("NADA") is None, "sin historial → None, no lista vacía")
    _ST.save_trades("DEMO", [_fr(1, "2026-07-28T15:00:00Z"), _fr(2, "2026-07-30T15:00:00Z")])
    _r = _ST.save_trades("DEMO", [_fr(1, "2026-07-28T15:00:00Z", "expirado"),
                                  _fr(3, "2026-07-29T15:00:00Z")])
    chk(_r.added == 1 and _r.total == 3, "added cuenta solo las nuevas")
    chk(_r.first_seen == "2026-07-28T15:00:00Z", "firstSeen = el trade más antiguo")
    _rows = _ST.load_trades("DEMO").trades
    chk([t["id"] for t in _rows] == [2, 3, 1], "orden descendente por timestamp")
    chk(next(t for t in _rows if t["id"] == 1)["expiry_status"] == "expirado",
        "el análisis más reciente gana (expiryStatus se recalcula)")
    chk({"score", "flags", "scores", "gamma", "open_interest"} <= set(_rows[0]),
        "guarda el análisis COMPLETO, no un recorte de 8 campos")
    chk(_ST._file_for("../../ETC/X").resolve().parent
        == (_ST.data_dir() / "trades").resolve(), "el ticker no se escapa del directorio")
    # Los 3 agujeros de SU store.ts, replicados a propósito para que el port sea
    # idéntico (47/47 en diff_store.sh). Arreglo propuesto para el upstream en
    # engine/scripts/upstream-tito-store.patch.
    _ST.save_trades("SINID", [_fr(0, "2026-07-30T15:00:00Z"), _fr(0, "2026-07-30T15:01:00Z")])
    chk(len(_ST.load_trades("SINID").trades) == 1,
        "BUG replicado: sin id el historial colapsa a un trade (clave del Map = t.id)")
    _p = _ST.data_dir() / "trades" / "BASURA.json"
    _p.write_text(json.dumps({"ticker": "BASURA", "updatedAt": "x",
                              "trades": [{"id": 1, "timestamp": "2026-07-30T15:00:00Z"},
                                         "no soy dict", None, 42]}), encoding="utf-8")
    chk(_ST.load_trades("BASURA").trades
        == [{"id": 1, "timestamp": "2026-07-30T15:00:00Z"}, "no soy dict", None, 42],
        "load no mira dentro del array (`Array.isArray(parsed.trades) ? parsed : null`)")
    try:
        _ST.save_trades("BASURA", [_fr(9, "2026-07-30T15:00:00Z")]); _tumba = False
    except TypeError: _tumba = True
    chk(_tumba, "BUG replicado: una fila `null` en disco tumba el guardado entero")
    _tzprev = os.environ.get("TZ")
    os.environ["TZ"] = "UTC"; time.tzset()
    _utc = _ST._date_parse("2026-07-30T15:00:00")
    os.environ["TZ"] = "America/New_York"; time.tzset()
    _ny = _ST._date_parse("2026-07-30T15:00:00")
    if _tzprev is None: os.environ.pop("TZ", None)
    else: os.environ["TZ"] = _tzprev
    time.tzset()
    chk(_ny - _utc == 4 * 3600 * 1000 and _ST._date_parse("2026-07-30") == 1785369600000,
        "Date.parse literal: fecha-hora sin offset en LOCAL, fecha sola en UTC")
    # 2ª pasada de auditoría: concurrencia, tickers degenerados y portabilidad.
    import threading as _th
    _b = datetime(2026, 7, 30, tzinfo=timezone.utc)
    def _esc(lote):
        _ST.save_trades("RACE", [_fr(i, (_b + timedelta(seconds=i)).isoformat().replace("+00:00", "Z"))
                                 for i in lote])
    _hs = [_th.Thread(target=_esc, args=(range(k * 50, k * 50 + 50),)) for k in range(8)]
    for _h in _hs: _h.start()
    for _h in _hs: _h.join()
    _n = len(_ST.load_trades("RACE").trades)
    chk(_n == 400, f"8 escrituras concurrentes no se pisan ({_n}/400)")
    chk(all("_exclusive(path)" in _s for _s in [
            (TITO_DIR/"stores.py").read_text()]) and
        (TITO_DIR/"stores.py").read_text().count("_exclusive(path)") == 4,
        "los 4 stores escriben bajo cerrojo")
    chk({_ST._file_for(_m).name for _m in ("!!!", "@@@", "", "   ")} == {".json"},
        "BUG replicado: los tickers que sanean a nada comparten `.json`")
    chk(_ST._file_for("A" * 200).name == "A" * 200 + ".json",
        "BUG replicado: sin tope de longitud, el nombre va tal cual al FS")
    chk(_ST.load_trades("!!!") is None, "leer con ticker inservible no lanza")
    _d2 = Path(os.environ["WBJ_TITO_DATA"]) / "no-existe-aun"
    os.environ["WBJ_TITO_DATA"] = str(_d2)
    _ST.load_trades("NUNCA"); _ST.load_iv_history("NUNCA")
    chk(not _d2.exists(), "leer no crea directorios (sin efectos secundarios)")
chk("except ImportError" in (TITO_DIR/"stores.py").read_text()
    and "import fcntl" in (TITO_DIR/"stores.py").read_text(),
    "fcntl es opcional: el módulo carga en Windows")
# 3ª pasada: el cerrojo no puede colgar un worker ni aflojar la exclusividad.
import threading as _th2, time as _t2
_p3 = _ST._path("iv", "REENT")
_ok3 = []
def _anida():
    with _ST._exclusive(_p3):
        with _ST._exclusive(_p3):
            _ok3.append(True)
_t3 = _th2.Thread(target=_anida, daemon=True); _t3.start(); _t3.join(timeout=10)
chk(not _t3.is_alive() and _ok3 == [True], "el cerrojo anidado no cuelga el worker")
_dentro, _solapes = [], []
def _entra():
    with _ST._exclusive(_ST._path("iv", "EXCL")):
        _dentro.append(1)
        if len(_dentro) > 1: _solapes.append(1)
        _t2.sleep(0.02); _dentro.pop()
_hs3 = [_th2.Thread(target=_entra) for _ in range(6)]
for _h in _hs3: _h.start()
for _h in _hs3: _h.join(timeout=10)
chk(not _solapes, "la reentrada no aflojó la exclusividad entre hilos")
# 4ª pasada: el archivo tiene que ser JSON válido para CUALQUIERA, no solo Python.
with tempfile.TemporaryDirectory() as _td4:
    os.environ["WBJ_TITO_DATA"] = _td4
    from dataclasses import replace as _rep
    _ST.save_trades("NAN", [_rep(_fr(1, "2026-07-30T15:00:00Z"), iv=float("nan"),
                                 delta=float("inf")),
                            _fr(2, "2026-07-30T15:01:00Z")])
    _crudo = (_ST.data_dir() / "trades" / "NAN.json").read_text()
    def _boom(c): raise ValueError(c)
    try:
        json.loads(_crudo, parse_constant=_boom); _val = True
    except ValueError: _val = False
    chk(_val and "NaN" not in _crudo,
        "un NaN no deja el archivo en JSON inválido (como JSON.stringify → null)")
    _t = {r["id"]: r for r in _ST.load_trades("NAN").trades}
    chk(len(_t) == 2 and _t[1]["iv"] is None and _t[2]["iv"] == 0.45,
        "el trade con el campo roto sobrevive; los sanos, intactos")
    _r = _ST.save_trades("DUP", [_fr(1, "2026-07-30T15:00:00Z"),
                                 _fr(1, "2026-07-30T15:00:00Z"),
                                 _fr(2, "2026-07-30T15:01:00Z")])
    chk((_r.total, _r.added) == (2, 2), "el mismo id dos veces en una llamada cuenta una")
    _mismo = "2026-07-30T15:00:00Z"
    _ST.save_trades("EST", [_fr(i, _mismo) for i in (1, 2, 3)])
    _o1 = [t["id"] for t in _ST.load_trades("EST").trades]
    _ST.save_trades("EST", [])
    chk(_o1 == [t["id"] for t in _ST.load_trades("EST").trades] == [1, 2, 3],
        "orden estable con timestamps idénticos")
    # 5ª pasada: el reintento de saneado en el PEOR caso (el roto va al final,
    # o sea máximo contenido escrito antes de que salte el allow_nan=False).
    _b5 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _rr = [_fr(i, (_b5 + timedelta(minutes=i)).isoformat().replace("+00:00", "Z"))
           for i in range(1500)]
    _rr[0] = _rep(_rr[0], iv=float("nan"))
    _ST.save_trades("W1", _rr)
    _c5 = (_ST.data_dir() / "trades" / "W1.json").read_text()
    try:
        json.loads(_c5, parse_constant=_boom); _v5 = True
    except ValueError: _v5 = False
    chk(_v5 and _c5.count('{"ticker"') == 1 and _c5.endswith("}"),
        "el reintento de saneado no deja cola del intento fallido")
    chk(len(_ST.load_trades("W1").trades) == 1500, "y no pierde ni un trade (1500/1500)")
    # Compatibilidad hacia adelante: campos que hoy no existen.
    (_ST.data_dir() / "trades" / "FUT.json").write_text(json.dumps(
        {"ticker": "FUT", "updated_at": "x", "trades": [{"id": 1}],
         "schema_version": 9, "campo_del_futuro": {"a": 1}}), encoding="utf-8")
    chk(len(_ST.load_trades("FUT").trades) == 1,
        "un archivo con campos futuros se lee igual (compat hacia adelante)")
chk('"motivo"' in API and "_empty(" in API,
    "si la memoria se apaga, el payload dice por qué (nada de degradar mudo)")

# ─────────────────────────────────────────────────────────────────────
sec("5-bis. Noticias (Tarea 7) — contra news.ts")
from wbj.tito import news as N
chk(len(N.MACRO_FEEDS) == 4, "4 feeds macro (siteContentMetadata excluido)")
chk(not any("siteContentMetadata" in f["url"] for f in N.MACRO_FEEDS),
    "siteContentMetadata NO está (devuelve 0 artículos)")
chk({f["name"] for f in N.MACRO_FEEDS} == {"CNBC — Top News","CNBC — Economía",
     "Investing.com — Earnings","Investing.com — Macro"}, "los 4 nombres de RSS Feed.md")
chk(N.parse_feed_date("Fri, 24 Jul 2026 03:15:46 GMT") == "2026-07-24T03:15:46Z", "RFC-822 de CNBC")
chk(N.parse_feed_date("2026-07-24 02:54:27") == "2026-07-24T02:54:27Z", "Investing sin zona → UTC")
chk([N.recency_weight(d, datetime(2026,7,24,12,tzinfo=timezone.utc)) for d in
     ("2026-07-24T06:00:00Z","2026-07-22T12:00:00Z","2026-07-20T12:00:00Z","2026-06-01T12:00:00Z")]
    == [1,0.6,0.3,0.1], "frescura 1 / 0.6 / 0.3 / 0.1")
chk([N.flow_bias(x) for x in (93,60,50,40,7)] ==
    ["bullish","bullish","neutral","bearish","bearish"], "flow_bias 60/40")
chk("F" not in N.company_aliases("F","Ford Motor Company"), "descarta tickers de 1-2 letras")
chk(N.mentions_company("the tsla in lowercase", ["TSLA"]) is None, "el ticker exige mayúsculas")
chk(N.mentions_company("Why tesla stock crashed",
    N.company_aliases("TSLA","Tesla, Inc.")) == "Tesla", "el nombre NO exige mayúsculas")
_neg = N.news_bias([N.NewsItem(id="1",title="t",url="u",publisher="p",
    published_utc="2026-07-24T10:00:00Z",sentiment="negative")], datetime(2026,7,24,12,tzinfo=timezone.utc))
chk(N.contradiction_flag("bullish", _neg).kind == "conflict", "alcista vs noticias malas → conflicto")
chk(N.contradiction_flag("bearish", _neg).kind == "confirm", "misma dirección → confirmación")
chk(N.contradiction_flag("neutral", _neg).kind == "none", "flujo repartido → sin bandera")
chk("ThreadPoolExecutor" in (TITO_DIR/"news.py").read_text(), "feeds en PARALELO (como Promise.all)")
chk('@app.get("/api/tito-news")' in API, "ruta propia /api/tito-news (como su /api/news)")
_tb = API[API.index("def projection_targets"):API.index("def _tito_chart_levels")]
chk('out["news"]' not in _tb, "las noticias NO viajan dentro del payload del scorecard")
chk('"afecta_scorecard": False' in API, "declara que no toca los 100 pts")
chk("vcLoadNews" in HTML, "el panel carga las noticias por separado")

# ─────────────────────────────────────────────────────────────────────
sec("6. Gráfica y panel")
for k, v in (("PADDING","top: 18, right: 132, bottom: 30, left: 10"), ("CHIP_GAP","5"),
             ("CHIP_H","34"), ("CONE_STEPS","24"), ("FUTURE_RATIO","0.4")):
    chk(f"{k}:" in HTML, f"{k} presente ({v})")
for c in ("#12b76a","#2f6bff","#f04438","#98a2b3","rgba(47,107,255,0.10)","rgba(18,183,106,0.6)"):
    chk(c in HTML, f"color de Víctor {c}")
chk(not any(c in HTML.split("const VC = {")[1].split("function renderProjections")[0]
            for c in ("#10b981","#3b82f6","#ef4444")), "sin colores del render anterior")
chk("vcWigglePath" in HTML, "wigglePath portado (los escenarios oscilan)")
chk("vcNiceTicks" in HTML, "niceTicks portado (eje de precio)")
chk("'Esta semana'" in HTML and "'2 semanas'" in HTML, "etiquetas de horizonte en lenguaje llano")
chk("bars[-70:]" in API, "70 velas de histórico")
chk('horizons: str = "10,20,30"' in API, "endpoint sirve 10/20/30")

# Un solo dueño de #projChart. Mientras convivieron TradingView y el SVG de
# Víctor, el segundo borraba el primero y sus overlays: el panel dependía del
# orden de resolución de dos fetch. Estas comprobaciones lo dejan cerrado.
_owners = re.findall(r"(\w+)\(\s*'projChart'", HTML)
chk(set(_owners) == {"renderVictorProjChart"},
    f"un solo renderizador de #projChart ({', '.join(sorted(set(_owners))) or 'ninguno'})")
for _dead in ("buildTVChart('projChart'", "projLoadChart", "projRedrawOverlays",
              "projDrawTargets", "projDrawScenario", "projDrawDarkpoolLines",
              "projToggleTarget", "projChangeTf", "projTargetsData"):
    chk(_dead not in HTML, f"sin resto de TradingView: {_dead}")
chk(not re.search(r"<script[^>]+lightweight-charts", HTML),
    "la librería de TradingView ya no se carga")
# El área de research tiene su propio renderVictorChart(hostId); la del panel
# lleva sufijo para no pisarlo (misma global → gana la última declaración).
chk(len(re.findall(r"^function renderVictorChart\(", HTML, re.M)) == 1
    and "function renderVictorProjChart(" in HTML,
    "sin colisión entre el renderer de research y el de Proyecciones")

# ─────────────────────────────────────────────────────────────────────
sec("7. Seguridad")
chk("URAc4p9DJi6Z" not in subprocess.run(["git","grep","-I","-l","URAc4p9DJi6Z"],
    cwd=VERTEX, capture_output=True, text=True).stdout, "la API key pegada no está en el repo")
_C = "kEyS3cr3t4NoDebeSalirJamas"
from wbj.tito.massive import MassiveError, fetch_option_chain, fetch_daily_bars
from wbj.tito.marketsnack import MarketSnackError, fetch_flow
_leak = []
os.environ["MASSIVE_API_KEY"] = _C
for _fn in (fetch_option_chain, fetch_daily_bars):
    try: _fn("NVDA")
    except MassiveError as e: _leak.append(_C in str(e))
os.environ["MARKETSNACK_COOKIE"] = _C
try: fetch_flow("NVDA")
except MarketSnackError as e: _leak.append(_C in str(e))
chk(not any(_leak), "ninguna credencial se filtra en un mensaje de error (centinela, 3 rutas)")
chk("vertex.env" in (VERTEX/".gitignore").read_text(), "vertex.env gitignoreado")
chk("sync: false" in RENDER, "los secretos de Render no van en el blueprint")

# ─────────────────────────────────────────────────────────────────────
sec("8. Tests")
r = subprocess.run([sys.executable,"-m","pytest","tests/tito/","-q"],
                   cwd=VERTEX/"engine", capture_output=True, text=True)
m = re.search(r"(\d+) passed", r.stdout)
chk(r.returncode == 0, f"suite del motor verde ({m.group(1) if m else '?'} tests)")
chk(len(list((VERTEX/"engine"/"tests"/"tito").glob("test_*.py"))) >= 12,
    f"{len(list((VERTEX/'engine'/'tests'/'tito').glob('test_*.py')))} archivos de test")

# ─────────────────────────────────────────────────────────────────────
print(f"\n\033[1m{'='*66}\033[0m")
print(f"  \033[32m{len(OK)} OK\033[0m · \033[33m{len(WARN)} avisos\033[0m · \033[31m{len(FAIL)} fallos\033[0m")
if WARN:
    print("\n  Avisos (fuera de alcance o pendientes):")
    for w in WARN: print(f"    ! {w}")
if FAIL:
    print("\n  FALLOS:")
    for f in FAIL: print(f"    ✗ {f}")
print()
sys.exit(1 if FAIL else 0)
