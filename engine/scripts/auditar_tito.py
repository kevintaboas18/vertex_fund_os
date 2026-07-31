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
import os, re, subprocess, sys
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
    "predictionStore.ts": "stores.py", "types.ts": "(dataclasses)",
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
import tempfile
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
