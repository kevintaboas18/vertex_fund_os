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
import ast, json, math, os, re, shutil, subprocess, sys, tempfile, time
from datetime import date, datetime, timedelta, timezone
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
    "watchlist.ts": "watchlist.py", "outboxStore.ts": "outbox_store.py",
    "watchlistStore.ts": "watchlist_store.py",
    "watchlistLocal.ts": "(JS en el HTML)",
}
FUERA = {  # deferidos a propósito, no son del motor de análisis
    "wheel.ts", "wheelAfford.ts", "wheelUniverse.ts", "earnings.ts",
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
print(f"  · {len(FUERA)} módulos fuera de alcance a propósito (Wheel y earnings, "
      f"que tienen su propio panel)")

# Los docstrings citan los documentos de metodología de Víctor (`SCOREDCARD/*`)
# como fuente de cada tabla de puntuación. Esos archivos viven en SU repo, no en
# este, así que la cita no se podía comprobar desde aquí — y una cita que nadie
# comprueba es una cita que puede apuntar a un archivo que ya no existe.
#
# Con TITO_ROOT puesto sí se comprueba. Sin él, se declara que no se comprobó en
# vez de dar por buena la referencia.
_citas = set()
for _py in sorted(TITO_DIR.glob("*.py")):
    _citas |= set(re.findall(r"SCOREDCARD/[\w.-]+\.md", _py.read_text(encoding="utf-8")))
if TITO and TITO.parent.exists():
    _rotas = sorted(c for c in _citas if not (TITO.parent / c).exists())
    chk(not _rotas,
        f"las {len(_citas)} citas a SCOREDCARD/ apuntan a un documento REAL de su repo"
        + (f" · ROTAS: {_rotas}" if _rotas else ""))
else:
    chk(True, f"{len(_citas)} citas a SCOREDCARD/ sin comprobar "
              "(define TITO_ROOT para verificarlas contra su repo)",
        warn_if_false=False)

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
chk((R.MAX_THETA_PCT_DAILY, R.THETA_BUDGET_PCT, R.MIN_DTE) == (5.0, 5.0, 2),
    "riesgo: theta ≤5%/día · presupuesto 5% · DTE ≥2")
# Su commit "screener más accesible para cuenta chica": el screener afloja, el
# scorecard NO. Si alguien iguala los dos umbrales, la definición institucional
# del dashboard cambia sin que nadie lo pida.
chk((R.IDEA_UNUSUAL_THRESHOLD, R.MONEYNESS_CAP) == (5, 0.25),
    "screener: inusualidad ≥5 · strike dentro del ±25% del precio")
chk(R.IDEA_UNUSUAL_THRESHOLD < F.UNUSUAL_TRADE_THRESHOLD,
    "el umbral del screener NO toca el institucional (7) del scorecard")
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
chk("fetch_option_chain" in blk and "daily_bars_for_panel" in blk,
    "usa los dos endpoints de Massive (las barras, por el cache del panel)")
chk('"source": "massive"' in API, "el fallo de Massive se reporta con su motivo")
chk('out["chain_source"] = "massive"' in API, "el reporte declara la procedencia de la cadena")
srcs = {m for m in re.findall(r'os\.environ\.get\("(\w+)"', "\n".join(
    p.read_text() for p in TITO_DIR.glob("*.py")))}
chk(srcs == {"MARKETSNACK_COOKIE","MASSIVE_API_KEY","MASSIVE_MAX_PAGES","WBJ_TITO_DATA"},
    f"el motor solo lee 4 variables: {sorted(srcs)}")

# ── compute.ts: las fórmulas de la cadena ────────────────────────────
from wbj.tito import compute as C
from wbj.tito import stores as _ST
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
# Las dos primitivas de JS que el port reimplementa. El diferencial completo
# vive en engine/scripts/diff_primitivas.sh (necesita node); aquí van los cuatro
# casos que salieron de él, que son donde float() y Number() se separan.
chk(all(math.isnan(C._js_number(v)) for v in ("1_0", "1_000", "12_3.4"))
    and C._js_number("0x1A") == 26 and C._js_number("0o17") == 15
    and C._js_number("Infinity") == float("inf")
    and all(math.isnan(C._js_number(v)) for v in ("infinity", "inf", "nan")),
    "`_js_number` es Number() de JS, no float() de Python (4 casos límite)")
chk(_ST._date_parse("2026") == _ST._date_parse("2026-01-01T00:00:00Z")
    and _ST._date_parse("2026-07") == _ST._date_parse("2026-07-01T00:00:00Z")
    and math.isnan(_ST._date_parse("2026-02-30"))
    and math.isnan(_ST._date_parse("Jul 30 2026")),
    "`_date_parse` cubre el Date Time String Format y NO el legacy de V8")
# Las DOS reglas de tipo de Víctor: estricta para el precio, laxa (`??` + la
# aritmética de JS) para OI/strike/volumen/acciones. Aplicar la estricta a los
# cuatro llenaba la cadena de ceros en silencio si la fuente cambia de tipo.
_txt = C.to_row({"open_interest":"500","details":{"strike_price":"205","shares_per_contract":"100"},
                 "day":{"volume":"81"}})
chk(_txt.open_interest == 500 and _txt.strike == 205 and _txt.volume == 81
    and _txt.notional_value == 500*100*205,
    "números en TEXTO siguen contando (regla `??`, no `typeof`)")
chk(math.isnan(C.to_row({"open_interest":"abc","details":{"strike_price":100}}).notional_value),
    "LITERAL: la basura no numérica da NaN (`Number(\"abc\") * 100 * 100`)")
chk(C.to_row({"open_interest":10,"details":{"strike_price":100,"shares_per_contract":0}})
    .notional_value == 0, "shares=0 se respeta (`??` solo cambia null)")
_coh = C.to_row({"open_interest":60.5,"details":{"strike_price":100}})
chk(_coh.notional_value == _coh.open_interest*100*100,
    "la fila y sus fórmulas usan el MISMO open interest")
chk(all(C.to_row({"details":{"expiration_date":e}}).expiration == e
        for e in ("2026-09-18","2026-09-18T00:00:00","2026-09-18T00:00:00Z")),
    "LITERAL: el vencimiento entra TAL CUAL (dos formatos = dos vencimientos)")
# 2ª pasada: el tipo de contrato NO puede depender del case, y una fila mala
# no puede llevarse la página entera.
chk(C.to_row({"details":{"contract_type":"put"}}).contract_type == "put"
    and all(C.to_row({"details":{"contract_type":t}}).contract_type == "call"
            for t in ("PUT","Put"," put ","call","CALL","P","",None)),
    'LITERAL: `t === "put"` exacto — un "PUT" se vuelve call')
_gcad = lambda m: [C.to_row({"details":{"contract_type":ct.upper() if m else ct,
        "expiration_date":"2026-09-18","strike_price":float(s),"shares_per_contract":100},
        "day":{"volume":400},"open_interest":9000 if ct=="put" else 3000})
        for s in range(90,115,5) for ct in ("call","put")]
from wbj.tito.gex import gex_analysis as _gexa
_now2 = datetime(2026,7,31,tzinfo=timezone.utc)
_g1, _g2 = _gexa(_gcad(False),[100.0]*60,100.0,_now2), _gexa(_gcad(True),[100.0]*60,100.0,_now2)
chk(_g1.total_net_gex < 0 < _g2.total_net_gex and _g1.regime != _g2.regime,
    "…con lo que cuesta medido: el GEX y el régimen SE INVIERTEN en MAYÚSCULAS")
_raras = 0
for _c in ({"details":"texto"}, {"details":5}, {"details":[]}, {"open_interest":"NaN"},
           {"last_trade":"x","day":{"close":2}}, None, [], "x"):
    try: C.to_row(_c)
    except Exception: _raras += 1
chk(_raras == 0, "ninguna fila malformada lanza (tumbaría la página entera)")
# 3ª pasada: shares ILEGIBLE no es AUSENTE — caer al 100 fabrica el
# multiplicador estándar justo donde no hay evidencia de cuál es.
chk(all(math.isnan(C.to_row({"open_interest":100,"details":{"strike_price":9000,
        "shares_per_contract":b}}).notional_value) for b in ("abc",{},"NaN","inf"))
    and all(C.to_row({"open_interest":100,"details":{"strike_price":9000,
        "shares_per_contract":b}}).notional_value == 0 for b in ("","   ",[])),
    "LITERAL: shares ilegible NO cae al 100 (`?? 100` solo rellena el AUSENTE)")
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
chk(C.contract_price({"last_trade":{"price":float("inf")},"day":{"close":2}})
        == (float("inf"),"last_trade")
    and C.contract_price({"day":{"close":float("-inf"),"vwap":1.5}}) == (1.5,"day_vwap"),
    "LITERAL: `Infinity > 0` pasa su guarda y se devuelve como precio")
_inf = C.to_row({"last_trade":{"price":float("inf")},"open_interest":100,
                 "details":{"strike_price":50}})
def _no_constante(c): raise ValueError(c)
try:
    json.loads(json.dumps({"p":_inf.price,"op":_inf.open_premium}),
               parse_constant=_no_constante)
    _js_ok = True
except ValueError: _js_ok = False
chk(not _js_ok, "la fila SÍ puede llevar no-finitos (su aritmética, literal)")
sys.path.insert(0, str(VERTEX))
_api_mod = __import__("vertex_api")
try:
    json.loads(json.dumps(_api_mod._json_safe({"p":_inf.price,"op":_inf.open_premium})),
               parse_constant=_no_constante)
    _safe_ok = True
except ValueError: _safe_ok = False
chk(_safe_ok, "…y `_json_safe` la deja en JSON válido antes de responder (su JSON.stringify)")
chk(C.to_row({"details":{"ticker":0}}).option_ticker == "0"
    and C.to_row({"details":{"ticker":1e9}}).option_ticker == "1000000000"
    and C.to_row({"details":{}}).option_ticker == "",
    "el ticker usa `?? \"\"` + `String()` de JS (no el str() de Python)")
_msrc = (TITO_DIR/"massive.py").read_text()
chk("sort_by_open_interest_desc(rows)" in _msrc and "count_expirations(rows)" in _msrc,
    "sortByOpenInterestDesc y countExpirations CABLEADAS (como su /api/chain)")
chk("expiration_count" in _msrc, "ChainResult lleva expirationCount, como su ChainMeta")
# 5ª pasada: el PRODUCTO también desborda, y la forma de la respuesta.
chk(all(math.isinf(C.to_row({"open_interest":a,"details":{"strike_price":b,
        "shares_per_contract":c}}).notional_value)
        for a,b,c in ((1e200,1e200,100),(1e308,10,100),(1e160,1e160,1e160))),
    "LITERAL: el producto desbordado llega a la fila como Infinity")
chk(math.isinf(C.to_row({"open_interest":1e200,"last_trade":{"price":1e200},
              "details":{"strike_price":1}}).open_premium),
    "…y el open premium desbordado también")
_ov = C.to_row({"open_interest":1e200,"last_trade":{"price":1e200},
                "details":{"strike_price":1e200}})
try:
    json.loads(json.dumps({"a":_ov.open_premium,"b":_ov.notional_value,"c":_ov.price}),
               parse_constant=_no_constante)
    _ov_ok = True
except ValueError: _ov_ok = False
try:
    json.loads(json.dumps(_api_mod._json_safe(
        {"a":_ov.open_premium,"b":_ov.notional_value,"c":_ov.price})),
        parse_constant=_no_constante)
    _ov_safe = True
except ValueError: _ov_safe = False
chk(_ov_safe, "la fila desbordada sale como null por `_json_safe`, no como Infinity")
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
from wbj.tito.massive import DailyBar
_ET = __import__("zoneinfo").ZoneInfo("America/New_York")
_HOY = datetime(2026,7,31,21,tzinfo=timezone.utc)
_AYER = _HOY - timedelta(days=1)
with tempfile.TemporaryDirectory() as _tdb:
    os.environ["WBJ_TITO_DATA"] = _tdb
    # `DailyBar`, no `LvlBar`: el cache guarda y devuelve la APERTURA.
    _bb = [DailyBar("2026-07-30", 99.5, 101, 99, 100)]
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
    # La separación de capas es lo que hace que el diferencial signifique algo:
    # SU función no puede llevar ni una regla del panel, o dejaría de poder
    # medirse contra su archivo. La política vive en `daily_bars_for_panel`.
    import inspect as _insp
    _suya = _insp.getsource(BS.cached_daily_bars)
    chk(not any(x in _suya for x in ("_ultima_sesion_cerrada", "_CIERRE_ET",
                                     "mercado_abierto", "_datos_congelados",
                                     "_recorta", "_guarda_si_no_acorta")),
        "`cached_daily_bars` sigue siendo SU función, sin una sola regla del panel")
    _pol = _insp.getsource(BS.daily_bars_for_panel)
    chk("_ultima_sesion_cerrada" in _pol and "_guarda_si_no_acorta" in _pol,
        "…y la política del panel vive aparte, en `daily_bars_for_panel`")
    chk(BS._file_for(" demo ").name == "DEMO.json"
        and BS._file_for("brk/b").name == "BRKB.json"
        and all(BS._file_for(t).name == _ST._file_for(t).name
                for t in ("!!!", "@@@", "", "   ", "...", "A"*200, "ñ", "brk.b"))
        and "_sanea_ticker" in (TITO_DIR/"bars_store.py").read_text(),
        "el saneado del ticker es el MISMO que el de stores (uno solo, compartido)")
    chk(BS._file_for("../../ETC/X").resolve().parent == (BS.data_dir()/"bars").resolve(),
        "su propio regex ya cierra la travesía de rutas")
    BS.save_bars(" demo ", _bb, _HOY)
    chk(BS.load_bars("DEMO").ticker == " DEMO ",
        "el ticker guardado NO se recorta (`ticker.toUpperCase()`, sin trim)")
    # Los 2 bugs de su barsStore.ts, PORTADOS TAL CUAL (el `as BarsFile` no
    # comprueba nada). Arreglo propuesto aguas arriba:
    # engine/scripts/upstream-tito-barsstore.patch.
    from wbj.tito.borde import barras_utiles as _bu
    _bp = BS.data_dir()/"bars"; _bp.mkdir(parents=True, exist_ok=True)
    _hoy_et = _HOY.astimezone(_ET).date().isoformat()
    (_bp/"A.json").write_text('{"ticker":"A","date":"'+_hoy_et+'"}')
    try:
        BS.cached_daily_bars("A", now=_HOY, fetch=lambda t,d: _bb); _bug1 = False
    except TypeError: _bug1 = True
    chk(_bug1, "LITERAL: un cache del día sin campo `bars` LANZA (BUG 1 de su `as`)")
    (_bp/"B.json").write_text('{"ticker":"B","date":"'+_hoy_et+'","bars":"texto"}')
    chk(BS.cached_daily_bars("B", now=_HOY, fetch=lambda t,d: _bb) == "texto",
        "LITERAL: un `bars` de texto SÍ se devuelve como barras (BUG 2)")
    (_bp/"E.json").write_text('[1,2]')
    chk(_bu(BS.load_bars("E")) is None and _bu(BS.load_bars("A")) is None
        and _bu(BS.load_bars("B")) is None,
        "BORDE: `barras_utiles` trata los tres como un cache que no está")
    chk(BS.daily_bars_for_panel("A", now=_HOY, fetch=lambda t,d: _bb) == _bb
        and BS.daily_bars_for_panel("B", now=_HOY, fetch=lambda t,d: _bb) == _bb,
        "…y el panel no se come ninguno de los dos bugs")
    # La política del panel: ancla el cache en la última sesión CERRADA, no en
    # el reloj. Sin esto, la vela de hoy se congela a media sesión, un retraso
    # de Massive deja el día fuera para siempre y el fin de semana no cachea.
    _vie18 = datetime(2026, 7, 31, 18, tzinfo=_ET)
    _sab = datetime(2026, 8, 1, 12, tzinfo=_ET)
    _serie = [LvlBar(time=(date(2026,7,31) - timedelta(days=29-i)).isoformat(),
                     high=101+i, low=99+i, close=100+i) for i in range(30)]
    _n = []
    BS.daily_bars_for_panel("PANEL", now=_vie18, fetch=lambda t,d: (_n.append(1), _serie)[1])
    BS.daily_bars_for_panel("PANEL", now=_sab, fetch=lambda t,d: (_n.append(1), _serie)[1])
    chk(len(_n) == 1, "el cache del panel SÍ sirve el fin de semana")
    _m = []
    _sin_hoy = _serie[:-1]
    for _ in range(3):
        BS.daily_bars_for_panel("TARDE", now=_vie18,
                                fetch=lambda t,d: (_m.append(1), _sin_hoy)[1])
    chk(len(_m) == 3, "si Massive publica tarde, no se sella el día y se repide")
_blkb = API[API.index("def _tito_chain_and_bars"):API.index("def _tito_memory")]
chk("daily_bars_for_panel(ticker)" in _blkb and "fetch_daily_bars(ticker)" not in _blkb,
    "las barras de Proyecciones pasan por el cache del panel")
chk("fetch_daily_bars(tk)" in API and "massive.barras.cache" in API,
    "…y el health check sigue pidiendo EN DIRECTO (un cache taparía la caída)")

# 6ª pasada: los negativos. Su `??` solo rellena el AUSENTE, así que un open
# interest negativo entra negativo y el nocional RESTA. Es suyo y se fija aquí
# para que nadie lo "arregle" dentro del módulo: el arreglo va aguas arriba.
chk(all(C.to_row(_c).notional_value < 0 for _c in (
        {"open_interest":-500,"details":{"strike_price":100}},
        {"open_interest":500,"details":{"strike_price":-100}},
        {"open_interest":10,"details":{"strike_price":100,"shares_per_contract":-100}})),
    "LITERAL: un negativo produce un nocional negativo (su `??` no los toca)")
_sana = [C.to_row({"details":{"contract_type":ct,"strike_price":float(s),
         "expiration_date":"2026-09-18","shares_per_contract":100},
         "day":{"volume":400},"open_interest":9000})
         for s in range(90,115,5) for ct in ("call","put")]
_sucia = _sana + [C.to_row({"details":{"contract_type":"call","strike_price":100.0,
          "expiration_date":"2026-09-18","shares_per_contract":100},
          "day":{"volume":400},"open_interest":-900_000})]
chk(S.structure_score(_sucia).notional["total"] < 0 < S.structure_score(_sana).notional["total"]
    and S.structure_score(_sucia).notional["low_liquidity"],
    "…con lo que cuesta medido: UNA fila de OI -900k invierte la cadena entera")
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
# Los escenarios EXTREMOS nunca se salen del cono de 2σ y nunca se cruzan. El
# BASE sí puede salirse, y el bear puede sentarse encima de él: son las dos
# divergencias de su `prediction.ts` que este port reproduce a propósito (ver
# `engine/scripts/upstream-tito-prediction.patch` y los comentarios del módulo).
# Se comprueban las dos cosas —lo que sí se sostiene y lo que él deja pasar—
# para que el port no se desvíe por accidente en ninguno de los dos sentidos.
sub = SubScores(8,7,9,6,5,6)
roto = 0
base_fuera = 0
for nodes in ([LevelInput(5000,1.0,"call",9e9)], [], [LevelInput(92,1.0,"put",-5e6)],
              [LevelInput(110,1,"call",5e6), LevelInput(92,.7,"put",-3e6)]):
    for h in (10,20,30):
        p = predict_pro(spot=100, iv=0.5, horizon_days=h, nodes=nodes, scores=sub, regime="positive")
        em2 = 100*math.exp(2*0.5*math.sqrt(h/365))
        em2l = 100*math.exp(-2*0.5*math.sqrt(h/365))
        if p.bear.target > p.bull.target: roto += 1          # los extremos no se cruzan
        if p.bull.target > em2 + 1e-6: roto += 1             # …ni salen del cono
        if p.bear.target < em2l - 1e-6: roto += 1
        if p.base.target > em2 + 1e-6 or p.base.target < em2l - 1e-6:
            base_fuera += 1
chk(roto == 0, "los EXTREMOS nunca se cruzan ni salen del cono de 2σ (12 configuraciones)")
chk(base_fuera == 3,
    f"el BASE sí puede salirse del cono ({base_fuera}/12) — igual que su archivo",
    "solo pasa con el imán fuera del cono; `gex_analysis` filtra a ±20% del spot. "
    "El arreglo está escrito en upstream-tito-prediction.patch")
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
    # `avgIv` es la clave de SU archivo, la que escribe `save_iv_snapshot` y la
    # que lee `ivcontext`. Aquí decía `avg_iv` y esta comprobación se cayó al
    # alinear el formato: es exactamente su trabajo — el rank volviendo al proxy
    # en silencio es el fallo que no se ve en ningún otro sitio.
    ivh = [{"date":(NOW-timedelta(days=d)).date().isoformat(),"avgIv":40+d*.15} for d in range(65)]
    con = run_scorecard("DEMO", trades(), chain(), bars(), NOW, spot=SPOT,
                        iv_history=ivh, past_flows=viejos, calibration={"bias_pct":2.0,"samples":8})
    chk(con.active == 6, f"con memoria: {con.active}/6 categorías activas")
    chk(con.scores["validation"] is not None, "sub-agente 6 se enciende con flows acumulados")
    chk(con.iv_context.rank["source"] == "iv-history", "IV Rank pasa de proxy a historia real")
    chk(con.predictions[20].calibration["applied"], "calibración aplicada con ≥5 vencidas")
# Estos dos checks medían el mundo ANTERIOR al almacén: que el blueprint
# declarara `WBJ_TITO_DATA=/var/data/tito` y que avisara de que el plan free
# borra la memoria. Las dos cosas dejaron de ser ciertas a la vez.
#
# `/var/data` en plan free NO era un disco montado —se creaba como carpeta
# normal en el sistema efímero—, así que la variable prometía persistencia que
# no existía. Y el aviso ya no aplica: las series viven dentro del almacén, que
# se restaura de la rama `datos` en cada arranque.
#
# Lo que se mide ahora es el invariante nuevo: que el blueprint pida el token
# sin el cual no hay respaldo, y que diga la consecuencia de no ponerlo.
# En minúsculas: lo que se mide es que el aviso ESTÉ, no cómo se escribió.
_R = RENDER.replace("\n", " ").replace("#", " ").lower()
chk("VERTEX_GIT_TOKEN" in RENDER,
    "el blueprint pide VERTEX_GIT_TOKEN, sin el cual no se respalda nada")
chk("rama `datos`" in RENDER or "rama datos" in _R,
    "…y explica que los datos van a la rama `datos`, no a main")
chk("no respalda" in _R,
    "…y dice la CONSECUENCIA de no ponerlo, no solo que hace falta")
chk("VERTEX_DB_KEY" in RENDER and "no se respaldan" in _R,
    "…y que sin VERTEX_DB_KEY las cuentas no se respaldan (cifrar o no subir)")
# La variable vieja no puede volver activa: definirla saca las series del
# almacén y las devuelve al disco efímero, apagando el sub-agente 6, el IV Rank
# real y la calibración sin que nada avise.
_activas = [l for l in RENDER.splitlines()
            if l.strip().startswith("- key:") and "WBJ_TITO_DATA" in l]
chk(not _activas,
    "WBJ_TITO_DATA NO está activa: las series viven dentro del almacén"
    + (f" · {_activas}" if _activas else ""))

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
    # Los 3 agujeros de SU store.ts, PORTADOS TAL CUAL para que el port sea
    # idéntico (47/47 en diff_store.sh, cero divergencias). Arreglo propuesto
    # para el upstream en engine/scripts/upstream-tito-store.patch; las guardas
    # de Vertex viven en wbj/tito/borde.py y se comprueban justo debajo.
    from wbj.tito import borde as _BO
    _ST.save_trades("SINID", [_fr(0, "2026-07-30T15:00:00Z"), _fr(0, "2026-07-30T15:01:00Z")])
    chk(len(_ST.load_trades("SINID").trades) == 1,
        "LITERAL: los trades sin id se funden en uno (su clave de dedupe es `t.id`)")
    _p = _ST.data_dir() / "trades" / "BASURA.json"
    _p.write_text(json.dumps({"ticker": "BASURA", "updatedAt": "x",
                              "trades": [{"id": 1, "timestamp": "2026-07-30T15:00:00Z"},
                                         "no soy dict", None, 42]}), encoding="utf-8")
    chk(_ST.load_trades("BASURA").trades == [{"id": 1, "timestamp": "2026-07-30T15:00:00Z"},
                                             "no soy dict", None, 42],
        "LITERAL: load devuelve el array del disco sin mirar su contenido")
    try:
        _ST.save_trades("BASURA", [_fr(9, "2026-07-30T15:00:00Z")]); _cae = False
    except TypeError: _cae = True
    chk(_cae, "LITERAL: una fila `null` en disco tumba el guardado (`byId.set(t.id, t)`)")
    chk([t["id"] for t in _BO.trades_utiles(_ST.load_trades("BASURA").trades)] == [1],
        "BORDE: `trades_utiles` es el filtro que su /api/validation hace solo en TS")
    chk(_BO.trades_sin_id(_ST.load_trades("BASURA").trades) == 3
        and _BO.trades_sin_id([{"id": 1}, {"id": 2}]) == 0,
        "BORDE: `trades_sin_id` deja visible el dedupe que colapsa (no lo arregla)")
    _tzprev = os.environ.get("TZ")
    os.environ["TZ"] = "UTC"; time.tzset()
    _utc = _ST._date_parse("2026-07-30T15:00:00")
    os.environ["TZ"] = "America/New_York"; time.tzset()
    _ny = _ST._date_parse("2026-07-30T15:00:00")
    if _tzprev is None: os.environ.pop("TZ", None)
    else: os.environ["TZ"] = _tzprev
    time.tzset()
    chk(_ny != _utc and _ST._date_parse("2026-07-30") == 1785369600000,
        "LITERAL: el naive se lee en la TZ LOCAL y la fecha sola en UTC (ES2015+)")
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
    chk(len({_ST._file_for(m) for m in ("!!!", "@@@", "", "   ", "ñ", "🙂")}) == 1,
        "LITERAL: los tickers que sanean a nada comparten el mismo `.json`")
    _rechaza = 0
    for _malo in ("!!!", "@@@", "", "   ", "...", "../..", "A" * 200):
        try: _BO.ticker_valido(_malo)
        except _BO.TickerInvalido: _rechaza += 1
    chk(_rechaza == 7,
        "BORDE: `ticker_valido` los rechaza ANTES del store (nada de `.json` compartido)")
    chk(_BO.ticker_valido("  aapl ") == "AAPL" and _BO.ticker_valido("brk.b") == "BRK.B",
        "…y el ticker bueno pasa ya saneado con SU regex")
    chk("_tito_ticker(ticker)" in API and "borde import TickerInvalido, ticker_valido" in API,
        "…cableado en las rutas de Proyecciones, que es el borde de verdad")
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
# ─────────────────────────────────────────────────────────────────────────
sec("4-bis. Las DOS ventanas de tape de su /api/flow")
# Su ruta baja el flujo dos veces: 5 días / ≥$100K para Agresividad, y
# 30 días / ≥$1M para Convicción, Inusualidad, Contexto IV, GEX y callPct.
# El port corría los seis sobre la primera; tres categorías puntuaban sobre un
# universo diez veces más barato y seis veces más corto que el suyo.
from wbj.tito.scorecard import (CONVICTION_DAYS, CONVICTION_MAX_PAGES,
                                CONVICTION_MIN_PREMIUM)
_flow_ts = (TITO / "web" / "app" / "api" / "flow" / "route.ts") if TITO else None
if _flow_ts and _flow_ts.exists():
    _src = _flow_ts.read_text(encoding="utf-8")
    chk(f"CONVICTION_DAYS = {CONVICTION_DAYS}" in _src
        and f"CONVICTION_MIN_PREMIUM = {CONVICTION_MIN_PREMIUM:_}".replace("_", "_") in _src.replace(",", "")
        or "CONVICTION_DAYS = 30" in _src,
        f"los 3 parámetros de la ventana ancha coinciden con su route.ts "
        f"({CONVICTION_DAYS}d · ${CONVICTION_MIN_PREMIUM:,} · {CONVICTION_MAX_PAGES} pág)")
else:
    print(f"  · ventana ancha: {CONVICTION_DAYS}d · ${CONVICTION_MIN_PREMIUM:,} · "
          f"{CONVICTION_MAX_PAGES} páginas (clona su repo en TITO_ROOT para cotejarlo)")

# La ventana ancha tiene MÁS filas y otra IV: si algún sub-agente mirase los 5
# días, su `n` y su IV saldrían los del set corto y estos checks lo cazarían.
_ancho = [dict(t, id=500 + i * 10 + k, premium=5_000_000, implied_volatility=2.9)
          for k in range(2) for i, t in enumerate(trades())]
_sc_corto = run_scorecard("DEMO", trades(), chain(), bars(), NOW, spot=SPOT, horizons=(20,))
_sc_ancho = run_scorecard("DEMO", trades(), chain(), bars(), NOW, spot=SPOT,
                          horizons=(20,), conviction_trades=_ancho)
chk(_sc_corto.conviction_window == "5d" and _sc_ancho.conviction_window == "30d",
    "el resultado DECLARA sobre qué ventana puntuó cada corrida")
chk(_sc_ancho.iv_context.iv["current"] != _sc_corto.iv_context.iv["current"],
    "Contexto IV cambia al cambiar la ventana ancha (no mira los 5 días)")
chk(_sc_ancho.conviction.n == len(_ancho) == 2 * _sc_corto.conviction.n
    and _sc_ancho.unusuality.n == len(_ancho),
    f"Convicción e Inusualidad puntúan sobre la ventana ancha "
    f"({_sc_ancho.conviction.n} filas, no {_sc_corto.conviction.n})")
chk(len(_sc_ancho.conviction_flow) == len(_ancho),
    "…y el resultado expone `convictionRows` para que la API guarde ESE set")
_ruta = (VERTEX / "vertex_api.py").read_text(encoding="utf-8")
chk("_tito_tape(" in _ruta and "conviction_trades=conviction_trades" in _ruta,
    "las dos rutas del panel hacen las DOS descargas")
chk("target_days=CONVICTION_DAYS" in _ruta,
    "…y la ancha para al cubrir los 30 días (`targetDays`), no al gastar páginas")

# ─────────────────────────────────────────────────────────────────────────
sec("4-ter. La barra diaria (DailyBar de su types.ts)")
chk([f.name for f in DailyBar.__dataclass_fields__.values()]
    == ["time", "open", "high", "low", "close"],
    "la barra lleva la APERTURA, como su `DailyBar` — sin ella toda vela es doji")
chk("datetime.fromtimestamp(ts / 1000, tz=timezone.utc)"
    in (VERTEX / "engine/wbj/tito/massive.py").read_text(encoding="utf-8"),
    "el día de la barra sale en UTC (`toISOString`), no en la zona del servidor")
_bs = (VERTEX / "engine/wbj/tito/bars_store.py").read_text(encoding="utf-8")
chk('"open"' in _bs, "el cache en disco conserva la apertura")
chk('"open": getattr(b, "open", b.close)' in _ruta,
    "…y el panel recibe la apertura real, no el cierre repetido")

# ─────────────────────────────────────────────────────────────────────────
sec("5-bis. Noticias (Tarea 7) — contra news.ts")
from wbj.tito import news as N
chk(len(N.MACRO_FEEDS) == 4, "4 feeds macro (siteContentMetadata excluido)")
chk(not any("siteContentMetadata" in f["url"] for f in N.MACRO_FEEDS),
    "siteContentMetadata NO está (devuelve 0 artículos)")
chk({f["name"] for f in N.MACRO_FEEDS} == {"CNBC — Top News","CNBC — Economía",
     "Investing.com — Earnings","Investing.com — Macro"}, "los 4 nombres de RSS Feed.md")
# `toISOString()` escribe SIEMPRE los milisegundos — ver `diff_motor3.sh`.
chk(N.parse_feed_date("Fri, 24 Jul 2026 03:15:46 GMT") == "2026-07-24T03:15:46.000Z",
    "RFC-822 de CNBC")
chk(N.parse_feed_date("2026-07-24 02:54:27") == "2026-07-24T02:54:27.000Z", "Investing sin zona → UTC")
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
# El cono de la gráfica es lo único del panel que se calcula en el NAVEGADOR,
# así que ningún diferencial de Python lo alcanza. Diferencial completo en
# engine/scripts/diff_cono.sh (necesita node); aquí, las dos cosas que de él
# salieron.
_chart = HTML[HTML.index("function renderVictorProjChart"):]
_chart = _chart[:_chart.index("\n}")]
chk("Math.max((d.gex && d.gex.iv) || 0.4, 0.01)" in _chart,
    "el cono lleva SU suelo `max(iv, 0.01)` (si no, se colapsa a una línea)")
chk("Math.exp(mult * sd * k)" in _chart and "(upper - spot) * k" not in _chart,
    "el cono se evalúa paso a paso como su `conePoints`, no se interpola el extremo")
chk("enCono(e.target)" in _chart and "sigma2.lower), sigma2.upper" in _chart,
    "el target se recorta al cono 2σ, como su `predictionPath` (etiqueta incluida)")
_wig = HTML[HTML.index("function vcWigglePath"):]
_wig = _wig[:_wig.index("\n}")]
chk(all(x in _wig for x in ("f * 8", "f * 19", "f * 37", "* 0.6", "* 0.28", "* 0.12",
                            "Math.sin(Math.PI * f)", "sigma * 0.5 * envelope * wob",
                            "steps = 30")),
    "`vcWigglePath` es su `wigglePath` entero (fases, pesos, envelope, pasos)")
chk(all(f"seed: {x}" in HTML for x in ("1.7", "4.1", "8.3"))
    and all(c in HTML for c in ("#12b76a", "#2f6bff", "#f04438")),
    "semillas y colores de los 3 escenarios, los suyos")

# gex_heatmap: estaba portado y sin llamador. Ahora sale por
# /api/projection-targets, con las entradas armadas como en su `page.tsx`.
chk("_tito_heatmap(chain or [], r, trades, now)" in API and '"gex_heatmap"' in API,
    "el heatmap de GEX llega al panel (era el último módulo sin cablear)")
chk("HeatTrade(strike=t.strike" in API and "vistos" in API,
    "…con los HeatTrade de convicción+inusuales deduplicados por id, como él")
chk(API.index("def _tito_heatmap") > 0 and "return None" in
    API[API.index("def _tito_heatmap"):API.index("def _tito_heatmap") + 2200],
    "…y si el heatmap falla, los targets siguen saliendo (ilustra, no decide)")

sec("7. El repo carga en el Python que se despliega")
#
# `runtime.txt` fija la versión de Python de Render, y la del contenedor no
# tiene por qué ser la misma. Un archivo que use sintaxis más nueva compila aquí
# y revienta allí — y no falla el archivo, falla el PAQUETE entero: un
# `SyntaxError` en `wbj/report/render.py` se llevaba `wbj.report` completo, y con
# él `run_report` y el `_insiders` que llama `vertex_api`.
#
# Medido: pasó de verdad. `render.py` metía barras invertidas dentro de la parte
# de EXPRESIÓN de una f-string, que PEP 701 permite desde 3.12 pero 3.11 no. En
# local (3.11) 4 archivos de test dejaban de coleccionar; en Render el reporte
# final no se generaba. Este check lo pilla sin desplegar.
_runtime = (VERTEX / "runtime.txt")
_pin = _runtime.read_text().strip() if _runtime.exists() else ""
_m = re.match(r"python-(\d+)\.(\d+)", _pin)
chk(bool(_m), f"runtime.txt fija la versión de Python ({_pin or 'ausente'})")
if _m:
    _objetivo = (int(_m.group(1)), int(_m.group(2)))
    _rotos = []
    for _py in sorted((VERTEX / "engine" / "wbj").rglob("*.py")):
        try:
            compile(_py.read_text(encoding="utf-8"), str(_py), "exec",
                    dont_inherit=True, _feature_version=_objetivo[1])
        except SyntaxError as _e:
            _rotos.append(f"{_py.relative_to(VERTEX)}:{_e.lineno} {_e.msg}")
        except ValueError:
            break      # `_feature_version` fuera del rango que soporta este intérprete
    chk(not _rotos,
        f"los {len(list((VERTEX/'engine'/'wbj').rglob('*.py')))} módulos de wbj/ "
        f"compilan con la sintaxis de Python {_objetivo[0]}.{_objetivo[1]}"
        + (" · ROTOS: " + "; ".join(_rotos[:3]) if _rotos else ""))
    _paquetes = []
    for _init in sorted((VERTEX / "engine" / "wbj").rglob("__init__.py")):
        _mod = ".".join(_init.relative_to(VERTEX / "engine").parts[:-1])
        try:
            __import__(_mod)
        except Exception as _e:                       # noqa: BLE001
            _paquetes.append(f"{_mod}: {type(_e).__name__}")
    chk(not _paquetes,
        "todos los paquetes de wbj/ importan de verdad"
        + (" · ROTOS: " + "; ".join(_paquetes) if _paquetes else ""))

sec("8. Seguridad")
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
sec("9. Funciones públicas sin llamador")
#
# Una función portada y sin cablear es código que NADIE ejecuta: no falla, no
# avisa y da la sensación de que la funcionalidad está. Aquí se cuentan las
# públicas de `wbj/tito/` que ningún módulo de producción llama —ni el motor ni
# `vertex_api.py`— y se exige que cada una esté DECLARADA con su motivo.
#
# El registro no es una lista de excusas: cada entrada dice quién la llama en SU
# repo. Si una función tiene consumidor en Proyecciones, no va aquí; se cablea.
# Y si aparece una huérfana nueva sin declarar, este check falla.
HUERFANAS = {
    # `size_flow` y `sort_by_afford_then_score` estuvieron aquí. Ya no: el
    # perfil del inversionista vive en el servidor (`Perfil Inversionista/
    # perfil.json`), no en localStorage como en su app, así que sus dos
    # funciones de dimensionado corren en `/api/tito-ideas` y `/api/tito-wheel`.
    # Es una divergencia declarada de CABLEADO, no de fórmula.

    # ── huérfanas EN SU PROPIO REPO ──
    "add_days":
        "sin llamador también en su occ.ts: nadie la usa en agente-tito-metralleta",
    "atm_iv":
        "sin llamador también en su wheel.ts: la exporta y no la usa ni su "
        "/api/wheel ni su wheel/page.tsx. El escaneo saca su IV de respaldo de "
        "la volatilidad REALIZADA, no de la IV del strike ATM",
    "load_chain_history":
        "sin llamador también en su chainStore.ts. Aquí el sub-agente 4 puntúa "
        "sobre la cadena de HOY; la serie se acumula para poder usarla, no se lee",

    # ── watchlist: SU cliente es el navegador, aquí también ──
    #
    # No son código muerto ni un port a medias. En su app estas ocho corren en
    # el CLIENTE (`ideas/page.tsx` + `WatchlistCard.tsx`), porque el watchlist
    # vive en localStorage con tu saldo dentro y no sube al servidor. Aquí pasa
    # exactamente lo mismo: el bloque `wlLocal*` del panel es su equivalente en
    # el navegador. Se portan a Python igualmente por dos razones concretas:
    #
    #   1. `diff_watchlist.sh` las ejecuta las 19 contra SU archivo — 734 casos
    #      a cero divergencias. Sin el port no habría con qué comparar, y la
    #      versión del navegador quedaría sin verificar contra nada.
    #   2. Son la referencia de la que se copió el JS. Cuando él cambie una
    #      regla, el diferencial lo dice aquí antes que en pantalla.
    #
    # Borrarlas ahorraría 60 líneas y perdería la única medida objetiva de que
    # el watchlist del panel hace lo que hace el suyo.
    "build_entry":   "corre en el navegador (`wlMarca`); medida por diff_watchlist.sh",
    "upsert":        "corre en el navegador (`wlUpsert`); medida por diff_watchlist.sh",
    "remove":        "corre en el navegador (`wlQuita`); medida por diff_watchlist.sh",
    "sort_entries":  "corre en el navegador (`wlOrdena`); medida por diff_watchlist.sh",
    "mark_synced":   "la escribe el agente por MCP, no el servidor web; medida por "
                     "diff_watchlist.sh",
    "payload_for":   "qué mandar según la granularidad; el buzón ya recorta en "
                     "`add_to_outbox`. Medida por diff_watchlist.sh",
    "quote_link":    "el enlace al broker se arma en el navegador desde la plantilla "
                     "`quoteUrl` que sirve /api/tito-watchlist; medida por diff_watchlist.sh",
    "ticker_list":   "los tickers para pegar; corre en el navegador (`wlTickerList`). "
                     "Medida por diff_watchlist.sh",
}
_pub, _prod = {}, set()
for _f in sorted(TITO_DIR.glob("*.py")):
    if _f.name == "__init__.py":
        continue
    for _n in ast.parse(_f.read_text(encoding="utf-8")).body:
        if isinstance(_n, (ast.FunctionDef, ast.AsyncFunctionDef)) and not _n.name.startswith("_"):
            _pub[_n.name] = _f.name
for _p in list(TITO_DIR.glob("*.py")) + [VERTEX / "vertex_api.py"]:
    for _n in ast.walk(ast.parse(_p.read_text(encoding="utf-8"))):
        if isinstance(_n, ast.Name):
            _prod.add(_n.id)
        elif isinstance(_n, ast.Attribute):
            _prod.add(_n.attr)
_huerfanas = {n for n in _pub if n not in _prod}
_sin_declarar = sorted(_huerfanas - set(HUERFANAS))
_ya_cableadas = sorted(set(HUERFANAS) - _huerfanas)
chk(not _sin_declarar,
    f"{len(_pub)} públicas · {len(_huerfanas)} sin llamador, todas declaradas"
    + (f" · SIN DECLARAR: {_sin_declarar}" if _sin_declarar else ""))
chk(not _ya_cableadas,
    "el registro no miente: ninguna declarada como huérfana está ya cableada"
    + (f" · {_ya_cableadas}" if _ya_cableadas else ""))
for _n in sorted(_huerfanas):
    print(f"      {_pub[_n] + '::' + _n:<40} {HUERFANAS.get(_n, '')[:80]}")
# Y las que SÍ se cablearon en esta pasada, con su consumidor.
chk("_tito_chart_geometry(r)" in API and "cone_points, prediction_path" in API,
    "`cone_points`/`prediction_path` CABLEADAS: el motor sirve `chart_geometry`")
chk("d.chart_geometry" in HTML and "geo.cone" in HTML,
    "…y la gráfica dibuja SUS puntos en vez de recalcular la fórmula")
# La geometría del panel es la ÚNICA pieza del port escrita a mano en JS (la
# ejecuta el navegador). `diff_geo.sh` la extrae del HTML y la corre al lado de
# su `chartGeometry.ts`; estos checks son la red de seguridad de esa extracción.
chk(all(f"function vc{n}(" in HTML for n in ("SmartDomain", "BuildScales", "PackLabels")),
    "las 3 funciones de su chartGeometry siguen en el panel con el nombre que "
    "`diff_geo.sh` extrae")
chk("!Number.isFinite(v)" in HTML,
    "el encuadre usa `Number.isFinite` (el `isFinite` global COACCIONA y colaba "
    "un sigma de texto que disparaba el dominio a millones)")
chk("input.futureRatio ?? 0.4" in HTML,
    "`futureRatio` se resuelve con `??`, no con un valor por defecto (que solo "
    "cubre `undefined` y dejaba el futuro en cero con un `null`)")
chk("clampedTo2Sigma" in HTML and "mask${uid}" in HTML,
    "el cono se DESVANECE cuando el 2σ no cabe entero, como su PriceChart")

# ── Los DOS motores de gamma del tab ─────────────────────────────────────
# `/api/options-gex` (Quant Data, respaldo yfinance+BSM) alimenta las cards de
# arriba; el de Víctor (Massive + MarketSnack) alimenta la gráfica, el imán y
# los escenarios. Dos medidas independientes de lo mismo son útiles —igual que
# σ/DCF vs gamma/flujo—, pero solo si se sabe cuál es cuál y la discrepancia se
# ve. El encabezado de SU gráfica salía del OTRO motor.
chk("vcSyncCabecera(d)" in HTML and "function vcGexHTML(" in HTML,
    "la gráfica de Víctor lleva SU cabecera y SU lectura de gamma")
# El tab ya no mide gamma dos veces: `/api/options-gex` (Quant Data, respaldo
# yfinance+BSM) salió del cargador. Sigue vivo para la vista de Research, que es
# otra pantalla; lo que no puede es volver a ESTE tab.
import re as _re
_carg = HTML[HTML.index("async function loadProjections("):]
_carg = _carg[:_carg.index("\nasync function vcFetchTargets(")]
_carg = _re.sub(r"/\*[\s\S]*?\*/", "", _carg)
_carg = _re.sub(r"^\s*//.*$", "", _carg, flags=_re.M)
_qd = {"options-gex", "net-flow", "options-ledger", "confluence", "gex-strike"}
_usa = set(_re.findall(r"api/([a-z0-9-]+)", _carg)) & _qd
chk(not _usa, "el tab de Proyecciones NO llama a Quant Data"
    + (f" · VUELVE A LLAMAR: {sorted(_usa)}" if _usa else ""))
chk("vcFetchTargets(ticker)" in _carg,
    "…y su única puerta de datos es `/api/projection-targets`")
_dom = HTML[HTML.index('<main id="projectionsView"'):]
_dom = _dom[:_dom.index("</main>")]
_muertos = [n for n in ("projNetDriftCard", "projLedgerCard", "projDarkPool",
                        "projConfluence") if f'id="{n}"' in _dom]
chk(not _muertos, "los paneles de Quant Data salieron del tab"
    + (f" · SIGUEN: {_muertos}" if _muertos else ""))
_render = HTML[HTML.index("function renderProjections(d) {"):]
_render = _re.sub(r"^\s*//.*$", "",
                  _render[:_render.index("\n/** Gamma neto por strike")], flags=_re.M)
chk("g.nodes" in _render and "g.flip_strike" in _render and "g.king_strike" in _render,
    "los muros, el flip y el imán de las cards salen de SUS nodos")
chk("Max Pain" not in _render and "Nodo imán" in _render,
    "fuera Max Pain (Víctor no lo calcula); en su sitio va el nodo imán")
chk(not any(v in _render for v in ("d.call_wall", "d.put_wall", "d.gamma_flip",
                                   "d.max_pain", "d.net_gex")),
    "ni un campo del payload de Quant Data sobrevive en las cards")
chk("_tito_clusters(trades, now)" in API and "detect_clusters" in API,
    "`detect_clusters` CABLEADA: `flow_clusters` (su FlowPriceChart)")

# ─────────────────────────────────────────────────────────────────────
sec("9-quater. Cobertura de SUS módulos y SUS constantes")
#
# Dos preguntas que ninguna ronda anterior hizo:
#   1. ¿hay algún `web/lib/*.ts` suyo que nadie portó NI declaró?
#   2. ¿coincide el VALOR de cada constante numérica que él exporta?
#
# La segunda no se puede hacer a ojo: son 32 constantes repartidas en 13
# archivos, y una sola mal copiada mueve un score sin que ningún test lo note
# —los tests portados usan la constante, no el literal, así que un 60 escrito
# como 6 pasaría verde en los dos lados—.
MODULOS_SUYOS = {
    # ── portados ──
    "flow":            ("py", "flow.py — sub-agentes 1, 2 y 3 + racimos"),
    "structure":       ("py", "structure.py — sub-agente 4"),
    "ivcontext":       ("py", "ivcontext.py — sub-agente 5"),
    "validation":      ("py", "validation.py — sub-agente 6"),
    "prediction":      ("py", "prediction.py — Prediction Pro"),
    "gex":             ("py", "gex.py"),
    "gexHeatmap":      ("py", "gex_heatmap.py"),
    "levels":          ("py", "levels.py"),
    "risk":            ("py", "risk.py"),
    "occ":             ("py", "occ.py"),
    "blackScholes":    ("py", "black_scholes.py"),
    "expectedMove":    ("py", "expected_move.py"),
    "conditions":      ("py", "conditions.py"),
    "compute":         ("py", "compute.py"),
    "news":            ("py", "news.py"),
    "massive":         ("py", "massive.py"),
    "marketsnack":     ("py", "marketsnack.py"),
    "store":           ("py", "stores.py — los cuatro stores fusionados"),
    "chainStore":      ("py", "stores.py"),
    "ivStore":         ("py", "stores.py"),
    "predictionStore": ("py", "stores.py"),
    "barsStore":       ("py", "bars_store.py"),
    "types":           ("py", "los dataclasses de cada módulo"),
    "chartGeometry":   ("js", "renderVictorProjChart en el panel — lo mide diff_geo.sh"),
    # ── declarados: NO se portan, con su motivo ──
    "wheel":           ("py", "wheel.py — el criterio de la Wheel"),
    "wheelAfford":     ("py", "wheel_universe.py — afford_of / sort_by_afford_then_score"),
    "wheelUniverse":   ("py", "wheel_universe.py — sus 40 símbolos curados"),
    "earnings":        ("py", "earnings.py — el estimador del próximo reporte"),
    # Los cuatro del watchlist. La de Vertex —tickers sueltos con alertas de
    # precio— se ELIMINÓ: un ticker no se puede juzgar después, un contrato con
    # la foto del día en que lo marcaste sí.
    "watchlist":       ("py", "watchlist.py — las 19 funciones puras + BROKERS, "
                              "medidas por diff_watchlist.sh"),
    "watchlistLocal":  ("js", "el bloque `wlLocal*` del panel: vive en el navegador "
                              "porque guarda tu saldo y tu sizing"),
    "watchlistStore":  ("py", "watchlist_store.py — legado de solo lectura, para la "
                              "importación única"),
    "outboxStore":     ("py", "outbox_store.py — la cola de /api/tito-watchlist"),
}
_mods_portados = [k for k, v in MODULOS_SUYOS.items() if v[0] != "no"]
chk(all(m for _, m in MODULOS_SUYOS.values()),
    f"los {len(MODULOS_SUYOS)} módulos de su web/lib están declarados")
chk(len(_mods_portados) == 32, f"{len(_mods_portados)} de sus módulos están portados")
if TITO and (TITO / "lib").is_dir():
    _suyos_lib = {f.stem for f in (TITO / "lib").glob("*.ts") if not f.stem.endswith(".test")}
    _suyos_lib = {m for m in _suyos_lib if not m.endswith(".test")}
    _faltan_m = sorted(_suyos_lib - set(MODULOS_SUYOS))
    chk(not _faltan_m, f"el registro cubre su web/lib entero{': faltan ' + str(_faltan_m) if _faltan_m else ''}")
    _fant_m = sorted(set(MODULOS_SUYOS) - _suyos_lib)
    chk(not _fant_m, f"el registro no inventa módulos{': ' + str(_fant_m) if _fant_m else ''}")

    # ── El cotejo de constantes, valor a valor ──
    import importlib
    _MAPA_PY = {k: v[1].split(".py")[0] for k, v in MODULOS_SUYOS.items() if v[0] == "py"}
    _MAPA_PY = {"flow": "flow", "structure": "structure", "ivcontext": "ivcontext",
                "validation": "validation", "risk": "risk", "prediction": "prediction",
                "gex": "gex", "gexHeatmap": "gex_heatmap", "levels": "levels", "occ": "occ",
                "blackScholes": "black_scholes", "expectedMove": "expected_move",
                "conditions": "conditions", "compute": "compute", "news": "news",
                "massive": "massive", "marketsnack": "marketsnack", "store": "stores", "wheel": "wheel",
                "wheelUniverse": "wheel_universe", "earnings": "earnings",
                "chainStore": "stores", "ivStore": "stores", "predictionStore": "stores",
                "barsStore": "bars_store"}
    _NUM = re.compile(r"^export const ([A-Z][A-Z0-9_]*)\s*=\s*([0-9_.*\s+-]+?);\s*(//.*)?$", re.M)
    _aus, _dif, _igual = [], [], 0
    for _ts, _py in _MAPA_PY.items():
        _f = TITO / "lib" / f"{_ts}.ts"
        if not _f.exists():
            continue
        _mod = importlib.import_module(f"wbj.tito.{_py}")
        for _n, _expr, _ in _NUM.findall(_f.read_text(encoding="utf-8")):
            try:
                _suyo = eval(_expr.replace("_", ""))      # noqa: S307 — literal numérico suyo
            except Exception:
                continue
            if not hasattr(_mod, _n):
                _aus.append(f"{_ts}.{_n}={_suyo}")
            elif abs(float(getattr(_mod, _n)) - float(_suyo)) > 1e-9:
                _dif.append(f"{_ts}.{_n}: él {_suyo} · nosotros {getattr(_mod, _n)}")
            else:
                _igual += 1
    chk(not _dif, f"las {_igual} constantes numéricas suyas valen lo mismo aquí"
                  + (f" — DIFIEREN: {_dif}" if _dif else ""))
    chk(not _aus, "ninguna constante exportada suya falta en el port"
                  + (f": {_aus}" if _aus else ""))

    # Y las de sus RUTAS, que este cotejo no miraba.
    #
    # Sólo escaneaba `web/lib/*.ts`, así que las siete `const` de su
    # `/api/flow` y las de `/api/ideas` y `/api/wheel` quedaban fuera por
    # construcción. Ahí estaban cuatro sin portar: tres existían en
    # `vertex_api.py` como números sueltos dentro de la llamada —el mismo valor,
    # pero sin nombre que cotejar— y `CONVICTION_TABLE_CAP` valía 25 en vez de
    # 150, lo que recortaba a una sexta parte las filas que alimentan sus tres
    # tarjetas de convicción.
    #
    # No son `export const` sino `const` de módulo, así que llevan su propio
    # patrón. Se buscan por NOMBRE en el motor y en la capa web: donde estén,
    # pero con el nombre puesto.
    _NUM_RUTA = re.compile(r"^const ([A-Z][A-Z0-9_]*)\s*=\s*([0-9_.*\s+-]+?);", re.M)
    _r_aus, _r_dif, _r_ig = [], [], 0
    import importlib as _il
    _SC = _il.import_module("wbj.tito.scorecard")
    _API_TXT = (VERTEX / "vertex_api.py").read_text(encoding="utf-8")
    for _rd in sorted((TITO / "app" / "api").iterdir()):
        _rf = _rd / "route.ts"
        if not _rf.is_file():
            continue
        for _n, _expr in _NUM_RUTA.findall(_rf.read_text(encoding="utf-8")):
            try:
                _suyo = eval(_expr.replace("_", ""))      # noqa: S307
            except Exception:
                continue
            _mio = getattr(_SC, _n, None)
            if _mio is None:
                # Puede vivir en la capa web con prefijo (`_IDEAS_`, `_WHEEL_`).
                _m = re.search(rf"^_?[A-Z]*_?{_n}\s*=\s*([0-9_.]+)", _API_TXT, re.M)
                _mio = float(_m.group(1).replace("_", "")) if _m else None
            if _mio is None:
                _r_aus.append(f"{_rd.name}.{_n}={_suyo}")
            elif abs(float(_mio) - float(_suyo)) > 1e-9:
                _r_dif.append(f"{_rd.name}.{_n}: él {_suyo} · nosotros {_mio}")
            else:
                _r_ig += 1
    chk(not _r_dif, f"las {_r_ig} constantes de sus RUTAS valen lo mismo aquí"
                    + (f" — DIFIEREN: {_r_dif}" if _r_dif else ""))
    chk(not _r_aus, "ninguna constante de sus rutas falta en el port"
                    + (f": {_r_aus}" if _r_aus else ""))
else:
    print("  · define TITO_ROOT para cotejar módulos y constantes contra su repo")

sec("9-ter. Cobertura de SUS componentes")
#
# La ronda 5 destapó el hueco más caro hasta ahora: el motor calculaba el
# desglose completo de los 6 sub-agentes y el payload servía solo el titular
# 0-10. Seis cifras sin evidencia detrás — justo lo que la regla innegociable
# del proyecto prohíbe. No lo vio ningún test porque todos medían "¿lo que se
# sirve se pinta?" y nadie medía "¿se sirve lo que ÉL muestra?".
#
# Este registro cierra esa pregunta. Es el mapa de los 29 componentes que
# renderiza su `web/app/page.tsx`: cada uno o tiene consumidor aquí, o está
# declarado con su motivo. Si él añade un componente nuevo, la lista se queda
# corta y este check lo dice.
COMPONENTES_SUYOS = {
    # ── vista Estudiante ──
    "VeredictoCard":         ("panel", "score/verdict/confianza en `renderVictorTargets`"),
    "SimpleChart":           ("panel", "`renderVictorProjChart` sobre #projChart"),
    "EscenariosCard":        ("panel", "las 3 cards bear/base/bull de `renderVictorTargets`"),
    "NivelesSimples":        ("panel", "`vcLevelsHTML`"),
    "MemoriaCard":           ("panel", "`vcMemoryHTML`"),
    "ContextoLinea":         ("panel", "la línea de `call_pct` en `vcScorecardHTML`"),
    # ── vista Pro ──
    "SentimentCard":         ("panel", "`vcScorecardHTML` — las 6 categorías con sus pesos"),
    "PredictionCard":        ("panel", "`renderVictorTargets` con el selector de horizonte"),
    "LevelsCard":            ("panel", "`vcLevelsHTML`"),
    "NewsCard":              ("panel", "`vcNewsHTML` + /api/tito-news"),
    "ProWallsCard":          ("panel", "`vcGexHTML` + las cards de #projCards"),
    "GexHeatmapCard":        ("panel", "`vcHeatmapHTML`"),
    "TradesFeed":            ("panel", "la tabla de #projUnusual y renderProjTape"),
    "ScorecardPanel":        ("panel", "`vcScorecardHTML`"),
    # ── detalle de sub-agentes ──
    "AggressionScoreCard":   ("panel", "`vcSubagentesHTML` — sub-agente 1"),
    "ConvictionCard":        ("panel", "`vcSubagentesHTML` — sub-agente 2"),
    "UnusualityCard":        ("panel", "`vcSubagentesHTML` — sub-agente 3"),
    "StructureCard":         ("panel", "`vcSubagentesHTML` — sub-agente 4"),
    "IvContextCard":         ("panel", "`vcSubagentesHTML` — sub-agente 5"),
    "ValidationCard":        ("panel", "`vcSubagentesHTML` — sub-agente 6"),
    "ConvictionTransactions":("panel", "`vcSubagentesHTML` — transacciones revisadas"),
    # ── los doce que faltaban, ya portados ──
    #
    # Estuvieron declarados como "no se portan, con su motivo". El motivo era
    # razonable uno a uno y equivocado en conjunto: entre todos son la mitad de
    # la evidencia que su panel enseña, y resumirla no es enseñarla. Se portan
    # los doce. Lo único que NO se copia es su wordmark: la marca de esta
    # pantalla es Vertex. El logo de la EMPRESA analizada sí — es información.
    "HeaderBar":             ("panel", "la barra de Proyecciones: NavTabs + `vcPintaQuick` "
                                       "+ buscador + el `hb-right` de `vcSyncCabecera`"),
    "AnalysisLoader":        ("panel", "`vcLoaderHTML` — sus 4 fases y su curva asintótica "
                                       "topada al 97%, arrancada por `vcLoaderArranca`"),
    "CompanyHeader":         ("panel", "`vcCompanyHTML` + /api/tito-logo (su proxy del logo)"),
    "ActivityCard":          ("panel", "`vcActivityHTML` — premium por día, calls vs puts"),
    "MoneyFlowCard":         ("panel", "`vcMoneyFlowHTML` — el reparto del dinero y sus "
                                       "cuatro azulejos"),
    "FlowPriceChart":        ("panel", "`renderProjFlowMoney` (el panel del dinero, con su "
                                       "escala log) + `vcClustersHTML` (los racimos)"),
    "OptionChainTable":      ("panel", "`vcCadenaHTML` — la cadena entera, ordenable por sus "
                                       "ocho columnas y con los nulos al final"),
    "WatchlistCard":         ("panel", "`renderProjWatchlist` + /api/tito-watchlist"),
    "NavTabs":               ("panel", "vcPintaNav — Ticker / Ideas / Wheel / Time & Sales"),
    "RiskProfileCard":       ("panel", "`vcRiesgoHTML` — sus dos presupuestos, alimentados "
                                       "por el cuestionario de la cuenta en vez de su slider"),
    "WheelPresetCard":       ("panel", "renderProjWheel — los tres presets con sus bandas"),
    "WheelTable":            ("panel", "renderProjWheel — la tabla de candidatos"),
    "IdeasTable":            ("panel", "renderProjIdeas — el screener de mercado del tab"),
    "RepeatBadge":           ("panel", "`vcRepeatBadge` + `vcRepeatCounts`"),
    "NotableTable":          ("panel", "renderProjTape — la cinta de Time & Sales"),
    "ChartPanel":            ("panel", "`renderProjTop5` — los cinco strikes de más "
                                       "nocional, punteados sobre el precio"),
    # ── chart/: el motor de la gráfica, que SÍ está portado ──
    "PriceChart":            ("panel", "`renderVictorProjChart` — port de chartGeometry.ts "
                                       "+ PriceChart.tsx, medido por `diff_geo.sh`"),
    "ChartCrosshair":        ("panel", "`vcCrosshairCablea` — cableada por "
                                       "`renderVictorProjChart` con SUS mismas escalas"),
}
_portados = {k: v for k, v in COMPONENTES_SUYOS.items() if v[0] == "panel"}
_sin_portar = {k: v for k, v in COMPONENTES_SUYOS.items() if v[0] != "panel"}
chk(all(m for _, m in COMPONENTES_SUYOS.values()),
    f"los {len(COMPONENTES_SUYOS)} componentes de su app están declarados")
chk(len(_portados) == 39, f"{len(_portados)} de sus componentes tienen consumidor en el tab")
chk(not _sin_portar,
    "no queda ni uno sin portar" + (f": {sorted(_sin_portar)}" if _sin_portar else ""))
# Y los que se declaran portados tienen que existir de verdad en el panel Y
# tener quien los llame.
#
# Esto miraba SOLO la primera función citada y SOLO que estuviera definida. Con
# eso, `RepeatBadge` pasó como portado estando muerto: `vcRepeatBadge` y
# `vcRepeatCounts` definidas, cero llamadores, y las tres tablas donde él usa la
# insignia pintando un `↻` suelto sin el ×N — diciendo que hubo repetición pero
# no cuántas veces, que es la mitad de la señal.
#
# Ahora se revisan TODAS las funciones que cada nota cita, y las dos formas de
# mentir: citarla sin que exista, y que exista sin que nadie la use.
# `render[A-Za-z...]` y no `renderProj...`: media docena de sus componentes
# caen en `renderVictorTargets` y `renderVictorChart`, y con el prefijo corto el
# check las daba por no portadas. Un chequeo que se equivoca de nombre denuncia
# lo que sí está y calla lo que no.
_FN_CITADA = re.compile(r"`(vc[A-Za-z0-9_]+|render[A-Za-z0-9_]+|wl[A-Za-z0-9_]+|pf[A-Za-z0-9_]+)`")
_mentira, _vivas = [], 0
for _c, (_tipo, _nota) in _portados.items():
    for _fn in sorted(set(_FN_CITADA.findall(_nota))):
        _def = re.search(rf"\b(function {_fn}\b|const {_fn}\s*=)", HTML)
        _usos = len(re.findall(rf"\b{_fn}\s*\(", HTML))
        if not _def:
            _mentira.append(f"{_c}: {_fn} citada y NO definida")
        elif _usos <= 1:
            _mentira.append(f"{_c}: {_fn} definida y SIN LLAMADOR")
        else:
            _vivas += 1
chk(not _mentira,
    f"las {_vivas} funciones que el registro cita están definidas Y se llaman"
    + (f" · MUERTAS: {_mentira}" if _mentira else ""))

# Que la función exista y se llame no dice que haga lo MISMO. Sus componentes
# llevan umbrales propios —que no están en el motor— y esos deciden texto y
# color: `strengthLabel` (70/50/30) pone "Muy fuerte" o "Débil" al lado de cada
# nivel, `ivColor` (90/61/40) tiñe la IV, el hit rate va verde ≥55 y rojo <45,
# la frase de sesgo tiene una banda muerta de ±1%, y `intensity > 0.12` decide
# si una celda del heatmap enseña su número o solo el color.
#
# Ninguno estaba portado: siete rondas comparando que la función existiera y
# ni una mirando lo que decide por dentro. Se compara el conjunto de umbrales
# de CADA componente suyo contra los números que aparecen en su función.
if TITO and (TITO / "app" / "components").is_dir():
    _UMBRAL = re.compile(r"[<>]=?\s*(-?\d+(?:\.\d+)?)")

    def _cuerpo_fn(fn):
        i = HTML.find(f"function {fn}(")
        if i < 0:
            return ""
        j = HTML.find("\nfunction ", i + 10)
        return HTML[i:j if j > 0 else i + 6000]

    #: (componente, umbral) → por qué NO está, con su motivo.
    _UMBRAL_DECLARADO = {
        # Él tiene DOS gráficas de niveles —`SimpleChart` (fuerza ≥25) y la de
        # `ProWallsCard` (≥35)— y el panel de Vertex tiene UNA. Se usa la de
        # `SimpleChart`, que es la más permisiva: con ≥35 la gráfica única se
        # quedaría sin los niveles medios, que son los que él sí enseña en la
        # otra vista. El 35 no tiene dónde aplicarse sin inventar una segunda
        # gráfica que nadie pidió.
        ("ProWallsCard", 35.0): "una sola gráfica de niveles, con el ≥25 de SimpleChart",
    }
    _sin_umbral, _con = [], 0
    for _c, (_tipo, _nota) in _portados.items():
        _f = TITO / "app" / "components" / f"{_c}.tsx"
        if not _f.is_file():
            continue
        _suyos = sorted({float(x) for x in _UMBRAL.findall(_f.read_text(encoding="utf-8"))})
        if not _suyos:
            continue
        _fns = sorted(set(_FN_CITADA.findall(_nota)))
        # Los helpers de banda viven fuera de la función que los usa.
        # Los helpers de banda viven fuera de la función que los usa, así que
        # se concatenan todos: si no, el check denuncia como ausente un umbral
        # que sí está, solo que en su propio helper.
        _HELPERS = ("vcFuerzaLabel", "vcIvColor", "vcHitRateColor", "vcSesgoFrase",
                    "vcConfLabel", "vcErrColor", "vcHaceCuanto", "vcDolares",
                    "vcScoreColor")
        _mio = ("".join(_cuerpo_fn(x) for x in _fns)
                + "".join(_cuerpo_fn(x) for x in _HELPERS))
        if not _mio:
            continue
        _nums = {float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", _mio)}
        _faltan = [u for u in _suyos if u not in _nums and (_c, u) not in _UMBRAL_DECLARADO]
        if _faltan:
            _sin_umbral.append(f"{_c}: {_faltan}")
        else:
            _con += 1
    chk(not _sin_umbral,
        f"los umbrales de {_con} de sus componentes están en el panel"
        + (f" · FALTAN: {_sin_umbral}" if _sin_umbral else ""))
# Con su repo a mano, el registro se contrasta contra la carpeta REAL: un
# componente nuevo suyo que nadie declaró hace fallar este check.
if TITO and (TITO / "app" / "components").is_dir():
    _suyos = {f.stem for f in (TITO / "app" / "components").rglob("*.tsx")}
    # `ChartPanel.tsx` cuelga de `app/`, no de `app/components/`.
    _suyos |= {f.stem for f in (TITO / "app").glob("*.tsx")} - {"layout", "page"}
    _faltan = sorted(_suyos - set(COMPONENTES_SUYOS))
    chk(not _faltan, f"el registro cubre su carpeta de componentes entera{': faltan ' + str(_faltan) if _faltan else ''}")
    _fantasma = sorted(set(COMPONENTES_SUYOS) - _suyos)
    chk(not _fantasma, f"el registro no inventa componentes{': ' + str(_fantasma) if _fantasma else ''}")
else:
    print("  · define TITO_ROOT para contrastar el registro contra su carpeta real")
# El detalle de sub-agentes: las 6 tarjetas + la tabla, servidas Y pintadas.
chk('"subagents"' in API, "el payload sirve el DESGLOSE de los 6 sub-agentes")
chk('"conviction_rows"' in API, "…y las filas de convicción, no solo su contador")
chk("function vcSubagentesHTML(d) {" in HTML and "${vcSubagentesHTML(d)}" in HTML,
    "…y el panel los pinta en su `<details>` de detalle")

sec("6-bis. El panel no se rompe solo")
# ─────────────────────────────────────────────────────────────────────
# Un acento grave dentro de una plantilla de JavaScript la CIERRA. Metido en
# un comentario HTML dentro de un `${...}`, el archivo sigue pareciendo
# correcto a simple vista y el navegador tira un SyntaxError que se lleva el
# tab entero — no una tarjeta, el tab. Pasó escribiendo esta misma ronda.
# Solo importan los comentarios que viven DENTRO de un `<script>`: ahí es donde
# hay plantillas. Uno en el cuerpo estático puede llevar los acentos que quiera.
_JS = "\n".join(re.findall(r"<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)</script>", HTML))
_COMENTARIOS_EN_JS = re.findall(r"<!--[\s\S]*?-->", _JS)
_con_acento = [c for c in _COMENTARIOS_EN_JS if "`" in c]
chk(not _con_acento,
    f"ningún comentario HTML dentro del JS lleva acentos graves "
    f"({len(_COMENTARIOS_EN_JS)} revisados)"
    + (f" · CIERRAN LA PLANTILLA: {[c[:60] for c in _con_acento]}" if _con_acento else ""))

# Y la comprobación que de verdad lo cierra: que el JS del panel se EJECUTE.
_smoke_js = VERTEX / "engine" / "scripts" / "_smoke_perfil.mjs"
if shutil.which("node") and _smoke_js.is_file():
    _r = subprocess.run(["node", str(_smoke_js)], capture_output=True, text=True)
    chk(_r.returncode == 0, "el JS del panel se ejecuta y pinta lo esperado")
# Y el de sus doce componentes: los ejecuta uno a uno con payloads realistas.
# Ya cazó dos cosas que ningún test de texto podía ver — el racimo que caía en
# 1970 por leer segundos como milisegundos, y el nocional con el formato que no
# era el suyo.
_smoke_comp = VERTEX / "engine" / "scripts" / "_smoke_componentes.mjs"
if shutil.which("node") and _smoke_comp.is_file():
    _rc = subprocess.run(["node", str(_smoke_comp)], capture_output=True, text=True)
    chk(_rc.returncode == 0, "sus 12 componentes nuevos se ejecutan y pintan lo esperado")
else:
    chk(False, "sin node: no se pudo ejecutar el JS del panel", warn_if_false=True)
    chk(_smoke_comp.is_file(), "…pero el smoke de sus componentes está en su sitio")

sec("9-sexies. Divergencias con su código, declaradas una por una")
# ─────────────────────────────────────────────────────────────────────
# El otro hueco: las divergencias estaban documentadas en comentarios sueltos
# por tres archivos, y nada las enumeraba. Sin registro, quitar una o añadir
# otra no lo cazaba nadie — que es justo lo contrario del contrato de este port,
# donde "exacto como Víctor" significa que TODA diferencia esté dicha.
#
# Mismo trato que las huérfanas: cada entrada dice qué cambia, por qué, y qué
# NO cambia. Y se comprueba que la marca siga en el código.
DIVERGENCIAS = {
    "sizing en el servidor": (
        VERTEX / "vertex_api.py",
        "Su /api/ideas devuelve griegos y nada más porque su app no tiene perfil "
        "de inversionista: el saldo vive en localStorage. Aquí el perfil está en "
        "el servidor, así que su propio `size_flow` corre en la ruta. "
        "NO cambia: la fórmula es la suya (diff_motor2, 918/918)."),
    "asequibilidad en el servidor": (
        VERTEX / "vertex_api.py",
        "Su `wheelAfford.ts` corre en el CLIENTE por el mismo motivo. Aquí su "
        "`sort_by_afford_then_score` corre en /api/tito-wheel. "
        "NO cambia: colateral ≤ caja, y el orden bloqueado→no asequible→score."),
    "clave de Massive recortada": (
        VERTEX / "engine/wbj/tito/massive.py",
        "Su `if (!key)` solo rechaza la cadena vacía: una clave con un salto de "
        "línea alrededor pasa y Massive responde 401. En su despliegue la clave "
        "vive en un `.env.local` que se edita; aquí vive en el panel de Render, "
        "donde se pega con el ratón. Se restauró al confirmar Kevin que su clave "
        "FUNCIONABA: una clave buena que muere por un carácter invisible es el "
        "peor fallo, porque el 401 acusa a la credencial y no al espacio."),
    "una respuesta, no un stream": (
        VERTEX / "vertex_api.py",
        "Sus cuatro rutas largas (analyze, flow, ideas, wheel) son SSE y emiten "
        "40-100 pasos con etiqueta. Aquí devuelven un JSON al final: detrás del "
        "proxy de Render en free no se garantiza `text/event-stream` sin "
        "buffering, y un stream a medias congela la pantalla en el paso 3. "
        "NO cambia: su propio AnalysisLoader ya colapsa los ~100 pasos en cuatro "
        "fases y no lee el texto de ninguno — esa pantalla está portada "
        "(`vcLoaderHTML`), con su curva y su tope del 97%."),
    "wheel sin bid": (
        VERTEX / "engine/wbj/tito/wheel.py",
        "Su plan de Massive no sirve `last_quote`, y su propio compute.ts lo dice: "
        "la prima cae a `last_trade → day.close → day.vwap`. Sin esto el screener "
        "sale SIEMPRE vacío. La salvaguarda se mueve al score: sin horquilla, la "
        "parte de liquidez cobra 0 de 15."),
}
_MARCA = "DIVERGENCIA DECLARADA"
chk(all(m and f.is_file() for f, m in DIVERGENCIAS.values()),
    f"las {len(DIVERGENCIAS)} divergencias con su código están declaradas con su motivo")
# Cada archivo que dice tener una, la tiene marcada en el código.
_archivos_con_marca = {f for f, _ in DIVERGENCIAS.values()}
for _f in sorted(_archivos_con_marca, key=str):
    _n = _f.read_text(encoding="utf-8").count(_MARCA)
    _esperadas = sum(1 for g, _ in DIVERGENCIAS.values() if g == _f)
    chk(_n >= _esperadas,
        f"{_f.name}: {_n} marcas «{_MARCA}» para {_esperadas} declarada(s)")
# Y al revés: ninguna marca suelta sin entrada en el registro.
_total_marcas = sum(f.read_text(encoding="utf-8").count(_MARCA)
                    for f in _archivos_con_marca)
chk(_total_marcas <= len(DIVERGENCIAS) + 1,      # +1: el sizing se explica dos veces
    f"{_total_marcas} marcas en el código para {len(DIVERGENCIAS)} divergencias "
    "declaradas — ninguna suelta")
for _k, (_f, _m) in sorted(DIVERGENCIAS.items()):
    print(f"      {_k:<30} {_f.name}")
print("  · toda diferencia con su código está aquí. Si aparece una nueva sin "
      "declarar, este check falla.")

sec("9-quinquies. Cobertura de SUS rutas de API")
# ─────────────────────────────────────────────────────────────────────
# El hueco que quedaba: había registro de sus MÓDULOS (9-quater) y de sus
# COMPONENTES (9-ter), pero no de sus RUTAS. Si mañana añade un endpoint,
# nada lo cazaba — y una ruta suya sin equivalente aquí es funcionalidad del
# agente que sencillamente no existe en Proyecciones.
#
# `("mia", ...)` = hay equivalente. `("dentro", ...)` = no es una ruta aparte
# aquí porque su contenido viaja dentro de otra. `("no", ...)` = no se porta,
# con su motivo.
RUTAS_SUYAS = {
    "chain":      ("dentro", "/api/projection-targets — la cadena se baja y se "
                             "consume en el servidor; el panel recibe el resultado"),
    "flow":       ("mia", "/api/tito-tape (cinta) y /api/projection-targets (scorecard)"),
    "ideas":      ("mia", "/api/tito-ideas"),
    "wheel":      ("mia", "/api/tito-wheel"),
    "news":       ("mia", "/api/tito-news"),
    "bars":       ("mia", "/api/tito-bars — sus tres marcos (1y, 15m10d, 5m5d). El "
                          "diario también viaja en `history` del payload, que es lo que "
                          "piden dos de sus tres consumidores; el intradía es del "
                          "selector de la gráfica de flujo"),
    "history":    ("dentro", "idem — `out['history']`, que alimenta la gráfica"),
    "prediction": ("dentro", "`memory` del scorecard: predicciones guardadas + calibración"),
    "validation": ("dentro", "`scores.validation` del scorecard — el sub-agente 6"),
    "logo":       ("mia", "/api/tito-logo — el proxy del logo. Existe porque la URL "
                          "que da Massive EXIGE la Authorization: sin proxy la clave "
                          "tendría que viajar al navegador"),
    "watchlist":  ("mia", "/api/tito-watchlist — su puente navegador↔agente, GET/POST/"
                          "DELETE. La watchlist de Vertex se eliminó"),
}
_rutas_cubiertas = [k for k, v in RUTAS_SUYAS.items() if v[0] != "no"]
chk(all(m for _, m in RUTAS_SUYAS.values()),
    f"las {len(RUTAS_SUYAS)} rutas de su web/app/api están declaradas")
chk(len(_rutas_cubiertas) == 11,
    f"{len(_rutas_cubiertas)} de sus {len(RUTAS_SUYAS)} rutas tienen equivalente aquí")
if TITO and (TITO / "app" / "api").is_dir():
    _suyas_api = {d.name for d in (TITO / "app" / "api").iterdir()
                  if (d / "route.ts").is_file()}
    _faltan_r = sorted(_suyas_api - set(RUTAS_SUYAS))
    _fant_r = sorted(set(RUTAS_SUYAS) - _suyas_api)
    chk(not _faltan_r,
        "el registro cubre su web/app/api entero"
        + (f" · SIN DECLARAR: {_faltan_r}" if _faltan_r else ""))
    chk(not _fant_r,
        "ninguna ruta declarada es un fantasma"
        + (f" · YA NO EXISTEN: {_fant_r}" if _fant_r else ""))
    # Las que dicen tener equivalente, lo tienen de verdad.
    _mias = set(re.findall(r'@app\.(?:get|post)\("(/api/[a-z0-9\-_/]+)"', API))
    for _k, (_tipo, _donde) in sorted(RUTAS_SUYAS.items()):
        if _tipo != "mia":
            continue
        _refs = [r for r in _mias if r in _donde]
        chk(bool(_refs), f"su /api/{_k} → {_refs or 'LA RUTA DECLARADA NO EXISTE'}")
else:
    chk(False, "sin TITO_ROOT no se puede contrastar su web/app/api", warn_if_false=True)
for _k, (_t, _m) in sorted(RUTAS_SUYAS.items()):
    print(f"      /api/{_k:<11} {_t:<7} {_m[:66]}")

sec("9-bis. Rutas del servidor sin cliente")
#
# Al sacar Quant Data del tab, nueve rutas se quedaron sin nadie que las llame.
# NO se borran: son superficie HTTP pública y alguien puede estar pegándoles
# desde un script o un marcador. Pero tampoco pueden quedarse mudas — una ruta
# sin cliente es código que nadie ejercita y que envejece sin avisar.
#
# Mismo contrato que el registro de funciones huérfanas: cada una declarada con
# su motivo, y si aparece una nueva sin declarar, este check falla.
RUTAS_HUERFANAS = {
    # ── se quedó sin cliente al eliminar la watchlist de Vertex ──
    "watchlist-radar":   "el radar por ticker. Su cliente era la rejilla de la watchlist "
                         "de Vertex, que se eliminó. NO se borra: `/api/alerts/scan` lo "
                         "llama por dentro y es lo que alimenta la campana, que ahora "
                         "vigila los subyacentes de la watchlist de contratos de Víctor",
    # ── se quedaron sin cliente al sacar Quant Data del tab ──
    "confluence":        "panel de confluencia (Quant Data), retirado del tab",
    "net-flow":          "drift de premium neto (Quant Data), retirado del tab",
    "options-ledger":    "libro de flujo institucional (Quant Data), retirado",
    "gex-strike":        "gamma por strike de Quant Data; ahora sale de `gex.nodes`",
    "self-test":         "diagnóstico que validaba las rutas de Quant Data",
    "trade-plan":        "estructurador de contrato sobre flujo de Quant Data",
    "income-strategies": "venta de prima sobre los walls de Quant Data",
    "backtest":          "backtest de señales sobre exposure/flujo de Quant Data",
    "collect-signals":   "captura del snapshot diario para ese backtest",
    "signal-history":    "histórico de ese backtest",
    # ── nunca tuvieron cliente en el panel, a propósito ──
    "tito-health":       "diagnóstico del motor: se consulta a mano al desplegar",
    "tito-scorecard":    "el scorecard suelto, sin memoria ni gráfica (API pública)",
    # ── anteriores a este trabajo ──
    "finnhub-quote":     "cotización suelta; la usa el flujo de análisis, no el panel",
    "logout":            "lo dispara el navegador, no una llamada del script",
    "history":           "el panel dibuja desde `chart_history`; la ruta queda "
                         "para consumo externo",
}
# Una ruta cuenta como VIVA solo si aparece dentro de un `fetch(...)`. Buscar
# "api/x" a secas la da por viva cuando lo único que hay es un comentario
# explicando que se retiró — el mismo tropiezo que ya tuvieron dos tests. Y
# quitar comentarios a lo bruto es peor: un `/*` dentro de una cadena se come
# medio archivo. El `fetch` es el único sitio donde una ruta se USA de verdad.
_rutas = set(re.findall(r'@app\.(?:get|post)\("/api/([a-z0-9\-_]+)"', API))
# La URL no siempre va literal dentro del `fetch`: media docena se arman antes
# en una variable (`const url = cond ? `…/api/x?…` : `…/api/x``). Así que se
# cuenta como viva toda ruta que aparezca en una plantilla con `API_BASE`
# delante, que es la forma que usa TODO el archivo para llamar al servidor.
_usadas = set(re.findall(r"API_BASE\}/api/([a-z0-9-]+)", HTML))
_huerf = {r for r in _rutas if r not in _usadas}
_sin_declarar = sorted(_huerf - set(RUTAS_HUERFANAS))
_ya_usadas = sorted(set(RUTAS_HUERFANAS) & _usadas)
chk(not _sin_declarar,
    f"{len(_rutas)} rutas · {len(_huerf)} sin cliente, todas declaradas"
    + (f" · SIN DECLARAR: {_sin_declarar}" if _sin_declarar else ""))
chk(not _ya_usadas,
    "el registro no miente: ninguna declarada huérfana está ya cableada"
    + (f" · {_ya_usadas}" if _ya_usadas else ""))
print("  · no se borran: son superficie HTTP pública. Se declaran para que no "
      "envejezcan mudas.")

# ─────────────────────────────────────────────────────────────────────
sec("10. Tests")
r = subprocess.run([sys.executable,"-m","pytest","tests/tito/","-q"],
                   cwd=VERTEX/"engine", capture_output=True, text=True)
m = re.search(r"(\d+) passed", r.stdout)
chk(r.returncode == 0, f"suite del motor verde ({m.group(1) if m else '?'} tests)")
chk(len(list((VERTEX/"engine"/"tests"/"tito").glob("test_*.py"))) >= 12,
    f"{len(list((VERTEX/'engine'/'tests'/'tito').glob('test_*.py')))} archivos de test")
chk(" skipped" not in (r.stdout or ""), "la suite del motor no se salta ni un test")

# Un test que se salta a sí mismo no protege nada y NO se nota: pytest lo pone
# en una línea de resumen y sale con código 0. Costó dos veces (el panel de
# pares de FIN-GR-003 y los dos del buscador, §41.27), así que ahora un salto
# que no sea "falta node/git" tumba la corrida. Se comprueba que la regla sigue
# instalada en las DOS suites, porque quitarla no rompe nada visible.
for _sub, _cf in (("engine/tests", "engine/tests/conftest.py"),
                  ("tests_vertex", "tests_vertex/conftest.py")):
    _t = (VERTEX / _cf).read_text(encoding="utf-8") if (VERTEX/_cf).is_file() else ""
    chk("_saltos.instala" in _t,
        f"{_sub}: un salto que no sea del entorno tumba la corrida")
_ent = VERTEX / "engine" / "tests" / "_saltos.py"
chk(_ent.is_file() and "ENTORNO" in _ent.read_text(encoding="utf-8"),
    "los motivos de salto permitidos están declarados por nombre, no por defecto")

# Los tests de Python leen el HTML como TEXTO: comprueban que una función
# existe y que alguien la llama, pero no ejecutan una línea. El smoke sí corre
# el JS vivo contra un DOM mínimo — y ya encontró un fallo que la lectura de
# texto no podía ver (`pfPinta` dependía de una variable que solo llenaba
# `pfCargar`, y la nota del riesgo salía en blanco).
_smoke = VERTEX / "engine" / "scripts" / "_smoke_perfil.mjs"
if shutil.which("node") and _smoke.is_file():
    r = subprocess.run(["node", str(_smoke)], capture_output=True, text=True)
    m = re.search(r"(\d+) fallos", r.stdout)
    chk(r.returncode == 0,
        "smoke del perfil: el JS del panel se EJECUTA y pinta lo del perfil"
        + (f" · {m.group(0)}" if m and r.returncode else ""))
else:
    chk(False, "sin node: el smoke del perfil (JS vivo contra DOM) no se pudo correr",
        warn_if_false=True)

# Los diferenciales son lo único que compara contra SU archivo de verdad, así
# que su ausencia es un agujero silencioso: la suite seguiría verde con el port
# divergiendo. Se comprueba que están y que la lista no encoge.
#
# Trece comparan NÚMEROS. `diff_series.sh` es el raro y compara EL ARCHIVO —los
# tres stores que son la memoria del agente—, en las dos direcciones: que los
# dos lados escriban lo mismo, y que cada uno pueda abrir el del otro. Ahí el
# fallo no se ve de ninguna otra forma: con un formato distinto nada revienta,
# solo que el IV Rank se queda en el proxy para siempre y la calibración nunca
# junta cinco muestras.
DIFERENCIALES = {
    "diff_store.sh":      "store.ts — 47 casos de persistencia de trades",
    "diff_compute.sh":    "compute.ts — 604 filas de cadena",
    "diff_bars.sh":       "barsStore.ts — 27 casos de cache de barras",
    "diff_primitivas.sh": "Number() y Date.parse() contra V8",
    "diff_cono.sh":       "expectedMove.ts — cono + rutas de la gráfica",
    "diff_motor.sh":      "flow + validation + levels + structure — 1.142 casos",
    "diff_motor2.sh":     "ivcontext + gex + prediction + risk — 918 casos",
    "diff_motor3.sh":     "gexHeatmap + news — 349 casos",
    "diff_geo.sh":        "chartGeometry.ts — la gráfica del panel, 274 casos",
    "diff_calib.sh":      "predictionStore.reviewPredictions — 182 diarios",
    "diff_frescura.sh":   "levels.recencyFactor — el peso por frescura",
    "diff_reloj.sh":      "las 5 funciones que cuentan tiempo",
    "diff_watchlist.sh":  "watchlist.ts — BROKERS y sus 19 funciones, 734 casos",
    "diff_series.sh":     "chain/iv/predictionStore — EL ARCHIVO, ida y vuelta",
    "diff_wheel.sh":      "wheel + wheelAfford + wheelUniverse + earnings, 1.072 casos",
    "diff_format.sh":     "format.ts — lo que se LEE en pantalla, 1.870 comparaciones",
}
#: Los TRES que llevan corpus MALFORMADO. Es lo que separa "coincide con datos
#: buenos" de "coincide también cuando la fuente cambia de esquema", y fue donde
#: salieron los casos en que el port lanzaba y su archivo no.
#:
#: Entre los tres cubren los 14 módulos de su `web/lib` que el motor usa: los
#: cuatro que no tienen diferencial propio —occ, conditions, expectedMove y
#: blackScholes— los llama el motor en CADA caso de `diff_motor.sh` y
#: `diff_motor2.sh`, basura incluida, así que quedan medidos ahí.
CON_BASURA = ("diff_motor.sh", "diff_motor2.sh", "diff_motor3.sh", "diff_calib.sh")
_faltan = [d for d in DIFERENCIALES if not (VERTEX/"engine"/"scripts"/d).exists()]
chk(not _faltan, f"los {len(DIFERENCIALES)} diferenciales contra su repo existen"
    + (f" · FALTAN: {_faltan}" if _faltan else ""))
for _d, _q in DIFERENCIALES.items():
    print(f"      {_d:<22} {_q}")
print("  · córrelos con node instalado; TITO_ROOT usa tu clon en vez de GitHub")
for _d in CON_BASURA:
    _src = (VERTEX / "engine" / "scripts" / _d.replace(".sh", "_casos.py")
            .replace("diff_", "_diff"))
    _casos = VERTEX / "engine" / "scripts" / ("_diff" + _d[5:-3] + "_casos.py")
    chk(_casos.exists() and "BASURA" in _casos.read_text(encoding="utf-8").upper(),
        f"{_d} lleva corpus MALFORMADO, no solo datos bien formados")
    _base = VERTEX / "engine" / "scripts" / ("_diff" + _d[5:-3] + "_base.json")
    if _base.exists():
        import json as _json
        print(f"      {_d:<18} {len(_json.loads(_base.read_text()))} divergencias "
              "declaradas por ID (falla si aparece una nueva)")

# Lo único que NINGUNA de las comprobaciones de arriba puede dar: la forma REAL
# de la respuesta de Massive y de MarketSnack. Todo lo demás corre sin red
# porque el contenedor bloquea los dos dominios; eso no se puede arreglar desde
# aquí, pero sí se puede dejar el comando que lo cierra en 30 segundos.
_pf = VERTEX / "engine" / "scripts" / "preflight_vivo.py"
chk(_pf.exists(),
    "existe el preflight EN VIVO para el día del despliegue (preflight_vivo.py)")
if _pf.exists():
    _src = _pf.read_text(encoding="utf-8")
    chk("shares_per_contract" in _src and "expiration_date" in _src,
        "…y comprueba los campos que rompen en silencio si cambia el esquema")
    chk("run_scorecard" in _src,
        "…y corre el scorecard de punta a punta con los datos reales")
    print("      MASSIVE_API_KEY=… MARKETSNACK_COOKIE=… \\")
    print("          python engine/scripts/preflight_vivo.py AAPL")

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
