import os
import json
import math
import re
import time
import threading
import numpy as np
import requests
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
import yfinance as yf
from pydantic import BaseModel, Field
from google import genai
from google.genai import types
from urllib.parse import urlparse

# ─────────────────────────────────────────────────────────────────────────────
# CARGA DE CREDENCIALES
# Lee vertex.env (gitignored) hacia el entorno para que TODAS las API keys
# (GEMINI/OPENAI/FINNHUB/QUANTDATA/XAI/PLAID/SNAPTRADE…) queden disponibles
# vía os.environ. Nunca se imprime ni se commitea su contenido.
# ─────────────────────────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    _ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vertex.env")
    load_dotenv(_ENV_PATH)                 # no falla si el archivo no existe
except Exception:
    pass  # sin python-dotenv: se usan las variables ya presentes en el entorno

# Cliente Gemini (modelo principal de la IA). GEMINI_API_KEY vive en vertex.env.
# Si no hay key, client_gemini queda en None y los endpoints de IA degradan a
# sus respaldos (OpenAI/Grok) o devuelven un error limpio en vez de romper.
API_KEY = os.environ.get("GEMINI_API_KEY", "") or os.environ.get("GOOGLE_API_KEY", "")
try:
    client_gemini = genai.Client(api_key=API_KEY) if API_KEY else None
except Exception:
    client_gemini = None

app = FastAPI(title="Vertex Fund OS Core")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/", response_class=HTMLResponse)
def serve_frontend():
    """Sirve el agente Vertex COMPLETO (Dashboard, Reports, Portfolio, Proyecciones,
    Watchlist, Track Record). El análisis muestra los números de Victor (overlay)."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    html_path = os.path.join(base_dir, "vertex_fund_os_platform.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as file:
            return file.read()
    return "<h1>Vertex OS Error: Frontend no encontrado en el servidor.</h1>", 404


@app.get("/wbj")
def serve_wbj_terminal():
    """El análisis WBJ de Victor ya vive DENTRO del dashboard principal (sección
    'Análisis WBJ'); ya no hay vista aparte. Redirige a la raíz para no duplicar."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/")


@app.get("/legacy", response_class=HTMLResponse)
def serve_frontend_legacy():
    """Interfaz anterior (framework de 7 señales) por si se necesita comparar."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    html_path = os.path.join(base_dir, "vertex_fund_os_platform.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as file:
            return file.read()
    return "<h1>Legacy frontend no encontrado.</h1>", 404


# ─────────────────────────────────────────────────────────────────────────────
# PERSISTENCE — SQLite (long-term agent memory + accuracy tracker)
# ─────────────────────────────────────────────────────────────────────────────
import sqlite3
DB_PATH = os.environ.get("VERTEX_DB",
                         os.path.join(os.path.dirname(os.path.abspath(__file__)), "vertex.db"))

def _db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    try:
        conn = _db()
        conn.execute("""CREATE TABLE IF NOT EXISTS reports (
            report_id         TEXT PRIMARY KEY,
            ticker            TEXT NOT NULL,
            created_at        TEXT NOT NULL,
            created_ts        REAL NOT NULL,
            price_at_analysis REAL,
            fair_value        REAL,
            upside_pct        REAL,
            recommendation    TEXT,
            conviction        INTEGER,
            target_bull       REAL,
            target_base       REAL,
            target_bear       REAL,
            thesis            TEXT,
            signal_scores     TEXT
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_reports_ticker ON reports(ticker, created_ts)")
        # Migration: add signal_scores to pre-existing DBs (no-op if it already exists).
        try:
            conn.execute("ALTER TABLE reports ADD COLUMN signal_scores TEXT")
        except Exception:
            pass
        # #3 — per-horizon target (base) so we can score short-horizon targets, not just 12M.
        for _col in ("target_7d", "target_30d", "target_3m", "target_6m"):
            try:
                conn.execute(f"ALTER TABLE reports ADD COLUMN {_col} REAL")
            except Exception:
                pass
        # #4 — payload completo del reporte (JSON) para un archivo DURABLE y multi-dispositivo en el servidor.
        try:
            conn.execute("ALTER TABLE reports ADD COLUMN payload TEXT")
        except Exception:
            pass
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[DB] init error: {e}")

init_db()

# ── #5 — CACHÉ COMPARTIDO DE SERIES DE PRECIO ────────────────────────────────
# track-record, calibración, IC y portfolio-fit bajaban 1 año de historia por
# ticker cada uno, por separado. Este caché (TTL 1h) lo comparte y reduce el
# riesgo de ratelimit de yfinance.
_PRICE_SERIES_CACHE = {}

def _stooq_series(ticker, period="1y"):
    """#3 RESPALDO de historia diaria cuando yfinance falla (rate-limit/caída): Stooq CSV, gratis y sin key.
    Devuelve [(epoch, close)] filtrado al periodo. Best-effort: cualquier error → lista vacía."""
    try:
        sym = str(ticker).strip().lower()
        url = f"https://stooq.com/q/d/l/?s={sym}.us&i=d"
        r = requests.get(url, timeout=8)
        if r.status_code != 200 or not r.text or "Date" not in r.text[:64]:
            return []
        days = {"5y": 1825, "2y": 730, "1y": 365, "6mo": 183, "3mo": 92, "2mo": 62, "1mo": 31}.get(period, 365)
        cutoff = time.time() - days * 86400
        out = []
        for line in r.text.strip().splitlines()[1:]:
            parts = line.split(",")
            if len(parts) < 5:
                continue
            try:
                ts = datetime.strptime(parts[0], "%Y-%m-%d").timestamp()
                close = float(parts[4])
            except (ValueError, IndexError):
                continue
            if ts >= cutoff and close > 0:
                out.append((ts, close))
        return out
    except Exception:
        return []


def _resilient_history(stock, ticker, period):
    """Historia diaria OHLCV con respaldo Stooq: si yfinance falla o viene vacío (rate-limit/caída),
    reconstruye el DataFrame (Open/High/Low/Close/Volume) desde Stooq para que /api/analyze NO se caiga.
    Devuelve un DataFrame estilo yfinance o None si ninguna fuente respondió."""
    try:
        h = stock.history(period=period)
        if h is not None and not h.empty:
            return h
    except Exception:
        pass
    try:
        import pandas as pd
        sym = str(ticker).strip().lower()
        r = requests.get(f"https://stooq.com/q/d/l/?s={sym}.us&i=d", timeout=8)
        if r.status_code != 200 or not r.text or "Date" not in r.text[:64]:
            return None
        days = {"5y": 1825, "2y": 730, "1y": 365, "6mo": 183, "3mo": 92, "2mo": 62, "1mo": 31}.get(period, 365)
        cutoff = time.time() - days * 86400
        rows = []
        for line in r.text.strip().splitlines()[1:]:
            p = line.split(",")
            if len(p) < 6:
                continue
            try:
                d = datetime.strptime(p[0], "%Y-%m-%d")
                if d.timestamp() < cutoff:
                    continue
                rows.append((d, float(p[1]), float(p[2]), float(p[3]), float(p[4]), int(float(p[5] or 0))))
            except (ValueError, IndexError):
                continue
        if not rows:
            return None
        idx = pd.DatetimeIndex([x[0] for x in rows])
        return pd.DataFrame({"Open": [x[1] for x in rows], "High": [x[2] for x in rows],
                             "Low": [x[3] for x in rows], "Close": [x[4] for x in rows],
                             "Volume": [x[5] for x in rows]}, index=idx)
    except Exception:
        return None


def _cached_price_series(ticker, period="1y", ttl=3600):
    key = f"{str(ticker).upper()}|{period}"
    nowt = time.time()
    ent = _PRICE_SERIES_CACHE.get(key)
    if ent and nowt - ent[0] < ttl:
        return ent[1]
    series = []
    try:
        h = yf.Ticker(ticker).history(period=period)
        if h is not None and not h.empty and "Close" in h:
            series = [(idx.timestamp(), float(c)) for idx, c in h["Close"].items()]
    except Exception:
        series = []
    if not series:                                  # #3 respaldo: yfinance vacío → Stooq
        series = _stooq_series(ticker, period)
    _PRICE_SERIES_CACHE[key] = (nowt, series)
    return series

def _price_at(series, target_ts):
    """Primer cierre en/después de target_ts; si es futuro, el último; None si no hay serie."""
    if not series:
        return None
    for ts, c in series:
        if ts >= target_ts:
            return c
    return series[-1][1]

def save_report(report_id, ticker, price, fair_value, upside_pct, recommendation, conviction, targets, thesis, signal_scores=None):
    """Persist a report so the agent can remember it and we can score accuracy later.
    signal_scores: dict of the 7 Vertex signal dims -> {score, nota}; stored as JSON so
    we can later measure each signal's Information Coefficient (#3)."""
    try:
        t12 = (targets or {}).get("12m", {}) or {}
        def _hb(k):
            return ((targets or {}).get(k, {}) or {}).get("base")
        ss_json = json.dumps(signal_scores) if signal_scores else None
        conn = _db()
        conn.execute("""INSERT OR REPLACE INTO reports
            (report_id,ticker,created_at,created_ts,price_at_analysis,fair_value,upside_pct,
             recommendation,conviction,target_bull,target_base,target_bear,thesis,signal_scores,
             target_7d,target_30d,target_3m,target_6m)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (report_id, ticker,
             datetime.now().strftime("%Y-%m-%d %H:%M:%S"), datetime.now().timestamp(),
             price, fair_value, upside_pct, recommendation, conviction,
             t12.get("bull"), t12.get("base"), t12.get("bear"), (thesis or "")[:4000], ss_json,
             _hb("7d"), _hb("30d"), _hb("3m"), _hb("6m")))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[DB] save error: {e}")

def save_report_payload(report_id, payload):
    """#4 — guarda el JSON COMPLETO del reporte en el servidor para un archivo durable y multi-dispositivo.
    save_report() ya insertó la fila; aquí solo rellenamos la columna payload. Best-effort."""
    try:
        blob = json.dumps(_json_safe(payload))[:2_000_000]   # cap defensivo (~2MB)
        conn = _db()
        conn.execute("UPDATE reports SET payload=? WHERE report_id=?", (blob, report_id))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[DB] payload save error: {e}")

def get_prior_report(ticker, exclude_id=None):
    """Most recent PRIOR report for a ticker (for the agent's long-term memory)."""
    try:
        conn = _db()
        if exclude_id:
            row = conn.execute("SELECT * FROM reports WHERE ticker=? AND report_id!=? ORDER BY created_ts DESC LIMIT 1",
                               (ticker, exclude_id)).fetchone()
        else:
            row = conn.execute("SELECT * FROM reports WHERE ticker=? ORDER BY created_ts DESC LIMIT 1",
                               (ticker,)).fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception as e:
        print(f"[DB] read error: {e}")
        return None


def get_recent_reports(ticker, n=6, exclude_id=None):
    """#3 — Últimos N reportes del ticker (memoria profunda, no solo el último)."""
    try:
        conn = _db()
        if exclude_id:
            rows = conn.execute("SELECT * FROM reports WHERE ticker=? AND report_id!=? ORDER BY created_ts DESC LIMIT ?",
                                (ticker, exclude_id, n)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM reports WHERE ticker=? ORDER BY created_ts DESC LIMIT ?",
                                (ticker, n)).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[DB] read error: {e}")
        return []


def _deep_memory_block(ticker, current_price, exclude_id=None):
    """#3 — Bloque de memoria para el prompt: las llamadas previas del agente en este ticker
    CON su resultado ya madurado (precio hoy vs precio/target de entonces). Convierte la
    'memoria' de un solo reporte previo en aprendizaje real sobre su propio acierto histórico."""
    reps = get_recent_reports(ticker, n=6, exclude_id=exclude_id)
    if not reps:
        return "", None
    cur = _safe_num(current_price)
    lines, hits, n_scored = [], 0, 0
    for r in reps:
        base = r.get("price_at_analysis")
        rec = (r.get("recommendation") or "?").upper()
        when = (r.get("created_at") or "")[:10]
        conv = r.get("conviction")
        seg = f"  • {when}: {rec} (conv {conv}) @ ${base}"
        if base and cur:
            ret = (cur - base) / base * 100
            hit = _dir_hit(rec, ret)
            n_scored += 1
            hits += 1 if hit else 0
            tb = r.get("target_base")
            tgt = f", target base ${tb}" if tb else ""
            seg += f" → hoy ${round(cur,2)} ({ret:+.1f}%, {'ACERTÓ' if hit else 'FALLÓ'}{tgt})"
        lines.append(seg)
    hr = round(100 * hits / n_scored, 0) if n_scored else None
    header = (f"MEMORIA DEL AGENTE EN {ticker} — tus {len(reps)} llamadas previas"
              + (f" (acierto direccional histórico {hr:.0f}% en {n_scored} maduradas)" if hr is not None else "")
              + ":\n" + "\n".join(lines)
              + "\nUsa esto para mantener coherencia: si cambias de tesis vs tu llamada previa, justifícalo explícitamente.")
    meta = {"n_prior": len(reps), "hist_hit_rate": hr, "n_scored": n_scored}
    return header, meta


def get_open_options(ticker):
    """#3 — Posiciones de OPCIONES abiertas del usuario sobre este subyacente (option_holdings)."""
    try:
        conn = _db()
        rows = conn.execute("SELECT * FROM option_holdings WHERE UPPER(underlying)=? ORDER BY expiry ASC",
                            (str(ticker).upper(),)).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _open_options_block(ticker):
    """#3 — Bloque para el prompt con las opciones que el usuario YA tiene en este ticker, con su P&L,
    para que el agente recomiende GESTIÓN (rolar/cerrar/promediar), no solo abrir nuevo."""
    pos = get_open_options(ticker)
    if not pos:
        return "", []
    lines = []
    for p in pos:
        typ = (p.get("option_type") or "?").upper()
        strike = p.get("strike"); exp = (p.get("expiry") or "")[:10]
        qty = p.get("contracts"); avg = p.get("avg_price") or p.get("price")
        cur = p.get("price"); val = p.get("value")
        pnl = ""
        if avg and cur and avg > 0:
            pnl = f", P&L {((cur-avg)/avg*100):+.0f}%"
        lines.append(f"  • {typ} ${strike} exp {exp} ×{qty} (entrada ${avg}, hoy ${cur}{pnl}, valor ${val})")
    block = ("POSICIONES DE OPCIONES ABIERTAS DEL USUARIO EN " + str(ticker).upper() + ":\n"
             + "\n".join(lines)
             + "\nTu recomendación DEBE contemplar qué hacer con estas posiciones (mantener/rolar/cerrar/promediar), "
               "no solo si abrir nuevas. Si la tesis cambió, di explícitamente qué hacer con lo abierto.")
    return block, pos


def _init_portfolio_snapshot_db():
    try:
        conn = _db()
        conn.execute("""CREATE TABLE IF NOT EXISTS portfolio_holdings (
            ticker      TEXT PRIMARY KEY,
            name        TEXT,
            value       REAL,
            cost_basis  REAL,
            account_key TEXT,
            updated_at  TEXT
        )""")
        try:
            conn.execute("ALTER TABLE portfolio_holdings ADD COLUMN cost_basis REAL")
        except Exception:
            pass
        conn.execute("""CREATE TABLE IF NOT EXISTS option_holdings (
            id          TEXT PRIMARY KEY,
            underlying  TEXT,
            option_type TEXT,
            strike      REAL,
            expiry      TEXT,
            contracts   REAL,
            price       REAL,
            avg_price   REAL,
            value       REAL,
            updated_at  TEXT
        )""")
        conn.commit(); conn.close()
    except Exception as e:
        print(f"[DB] portfolio table init error: {e}")

_init_portfolio_snapshot_db()

def _init_signal_history_db():
    """Daily snapshots of the full signal set per ticker → forward backtesting of
    confluence direction and projection-target hit-rate."""
    try:
        conn = _db()
        conn.execute("""CREATE TABLE IF NOT EXISTS signal_snapshots (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker          TEXT NOT NULL,
            snap_date       TEXT NOT NULL,
            spot            REAL,
            confl_verdict   TEXT,
            confl_direction TEXT,
            confl_score     REAL,
            conv_bias       TEXT,
            conv_strength   REAL,
            net_premium     REAL,
            dark_bias       TEXT,
            call_wall       REAL,
            put_wall        REAL,
            gamma_flip      REAL,
            targets_json    TEXT,
            created_at      TEXT,
            UNIQUE(ticker, snap_date)
        )""")
        conn.commit(); conn.close()
    except Exception as e:
        print(f"[DB] signal history table init error: {e}")

_init_signal_history_db()

def save_portfolio_snapshot(positions, account_key="ALL"):
    """Replace the stored single-user equity book snapshot so the per-stock
    agent can be portfolio-aware."""
    try:
        conn = _db()
        conn.execute("DELETE FROM portfolio_holdings")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for p in positions:
            conn.execute(
                "INSERT OR REPLACE INTO portfolio_holdings (ticker,name,value,cost_basis,account_key,updated_at) VALUES (?,?,?,?,?,?)",
                (p["ticker"], p.get("name", p["ticker"]), float(p.get("value") or 0),
                 (float(p["cost_basis"]) if p.get("cost_basis") not in (None, "") else None),
                 account_key, now))
        conn.commit(); conn.close()
    except Exception as e:
        print(f"[DB] snapshot save error: {e}")

def get_portfolio_snapshot():
    """Return the stored equity book as list of {ticker,name,value}."""
    try:
        conn = _db()
        rows = conn.execute("SELECT ticker,name,value,cost_basis FROM portfolio_holdings ORDER BY value DESC").fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[DB] snapshot read error: {e}")
        return []

def save_options_snapshot(options):
    """Replace the stored option-positions snapshot (modular: any source — SnapTrade
    today, Unusual Whales later — feeds the same Greeks engine)."""
    try:
        conn = _db()
        conn.execute("DELETE FROM option_holdings")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for i, o in enumerate(options):
            oid = f"{o['underlying']}|{o['option_type']}|{o['strike']}|{o['expiry']}|{i}"
            conn.execute(
                "INSERT OR REPLACE INTO option_holdings "
                "(id,underlying,option_type,strike,expiry,contracts,price,avg_price,value,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (oid, o["underlying"], o["option_type"], float(o["strike"]), o["expiry"],
                 float(o.get("contracts") or 0), float(o.get("price") or 0),
                 (float(o["avg_price"]) if o.get("avg_price") not in (None, "") else None),
                 float(o.get("value") or 0), now))
        conn.commit(); conn.close()
    except Exception as e:
        print(f"[DB] options snapshot save error: {e}")

def get_options_snapshot():
    """Return the stored option book as a flat list of normalized positions."""
    try:
        conn = _db()
        rows = conn.execute(
            "SELECT underlying,option_type,strike,expiry,contracts,price,avg_price,value "
            "FROM option_holdings").fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[DB] options snapshot read error: {e}")
        return []

def build_memory_block(prior, current_price):
    """Text block injected into the Gemini prompt so the agent explains how its view evolved."""
    if not prior:
        return ""
    days_ago = int((datetime.now().timestamp() - prior.get("created_ts", 0)) / 86400)
    pv = prior.get("price_at_analysis")
    change = None
    if pv and current_price:
        try: change = round(((current_price - pv) / pv) * 100, 1)
        except Exception: change = None
    lines = [
        "",
        f"MEMORIA DEL AGENTE (tu propio analisis anterior de {prior.get('ticker')}):",
        f"- Hace {days_ago} dias ({prior.get('created_at')}) tu Fair Value fue ${prior.get('fair_value')}, "
        f"recomendacion {prior.get('recommendation')}, conviccion {prior.get('conviction')}/100.",
        f"- El precio entonces era ${pv}" + (f" (cambio {change:+}% hasta hoy)." if change is not None else "."),
        "INSTRUCCION DE MEMORIA: En 'tesis_inversion_completa' y 'recomendacion_porque', explica EXPLICITAMENTE "
        "como ha evolucionado tu vision desde ese reporte: si subiste o bajaste tu Fair Value y por que, si tu "
        "recomendacion cambia o se mantiene, y que datos nuevos lo justifican. Habla como un CIO que recuerda su tesis previa.",
    ]
    return "\n".join(lines)

def compute_memory_comparison(prior, current_price, current_fair, current_rec, current_conv):
    """Structured prior-vs-now comparison for the UI memory card."""
    if not prior:
        return {"has_prior": False}
    pv = prior.get("price_at_analysis")
    fv = prior.get("fair_value")
    def pct(a, b):
        try: return round(((a - b) / b) * 100, 1) if (a is not None and b) else None
        except Exception: return None
    return {
        "has_prior": True,
        "prior_date": prior.get("created_at"),
        "days_ago": int((datetime.now().timestamp() - prior.get("created_ts", 0)) / 86400),
        "prior_price": pv,
        "prior_fair_value": fv,
        "prior_recommendation": prior.get("recommendation"),
        "prior_conviction": prior.get("conviction"),
        "current_price": current_price,
        "current_fair_value": current_fair,
        "current_recommendation": current_rec,
        "current_conviction": current_conv,
        "price_change_pct": pct(current_price, pv),
        "fair_value_change_pct": pct(current_fair, fv),
        "recommendation_changed": (prior.get("recommendation") != current_rec),
    }


# ─────────────────────────────────────────────────────────────────────────────
# MULTI-MODEL CASCADE — 3rd-model arbiter (OpenAI if available, else Gemini Pro)
# ─────────────────────────────────────────────────────────────────────────────
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")  # optional independent tiebreaker

def _extract_verdict(text):
    """Pull a BUY/HOLD/SELL verdict out of a model's free text."""
    if not text:
        return None
    up = text.upper()
    # Prefer an explicit 'VEREDICTO: X'
    import re as _re
    m = _re.search(r"VEREDICTO[^A-Z]*([A-Z]+)", up)
    if m:
        v = m.group(1)
        if v in ("BUY", "HOLD", "SELL", "AVOID", "COMPRAR", "MANTENER", "VENDER"):
            return {"COMPRAR": "BUY", "MANTENER": "HOLD", "VENDER": "SELL"}.get(v, v)
    for v in ("BUY", "SELL", "AVOID", "HOLD"):
        if v in up:
            return v
    if "BULLISH" in up or "ALCISTA" in up: return "BUY"
    if "BEARISH" in up or "BAJISTA" in up: return "SELL"
    return None

def _arbiter_call(prompt):
    """Independent 3rd model. OpenAI GPT-4o if OPENAI_API_KEY set, otherwise Gemini 2.5 Pro."""
    if OPENAI_API_KEY:
        try:
            r = requests.post("https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
                json={"model": "gpt-4o", "messages": [{"role": "user", "content": prompt}],
                      "temperature": 0.2, "max_tokens": 900},
                timeout=60)
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"].strip(), "OpenAI GPT-4o"
        except Exception:
            pass
    try:
        resp = client_gemini.models.generate_content(
            model="gemini-2.5-pro", contents=prompt,
            config=types.GenerateContentConfig(temperature=0.2, max_output_tokens=1000))
        return (resp.text or "").strip(), "Gemini 2.5 Pro"
    except Exception as e:
        return f"[Arbitro no disponible: {e}]", "N/A"



# ─────────────────────────────────────────────────────────────────────────────
# INSTITUTIONAL TARGET ENGINE — DCF + Comparables + Technical Levels
# ─────────────────────────────────────────────────────────────────────────────

def calculate_institutional_targets(ticker: str, info: dict, hist) -> dict:
    try:
        closes = hist['Close'].values.astype(float)
        highs  = hist['High'].values.astype(float)
        lows   = hist['Low'].values.astype(float)
        price  = float(closes[-1])

        # ── 1. VOLATILIDAD HISTÓRICA ──────────────────────────
        if len(closes) > 5:
            log_returns = np.diff(np.log(closes))
            daily_vol   = float(np.std(log_returns))
            annual_vol  = daily_vol * math.sqrt(252)
        else:
            daily_vol  = 0.015
            annual_vol = 0.25

        # ── 2. ATR (Average True Range) ─────────
        if len(closes) > 14:
            trs = []
            for i in range(1, min(15, len(closes))):
                tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i]  - closes[i-1]))
                trs.append(tr)
            atr = float(np.mean(trs))
        else:
            atr = price * 0.02

        # ── 3. DCF SIMPLIFICADO ──────────────────────────────────────────────
        eps          = info.get("trailingEps") or info.get("forwardEps") or (price / 25)
        fwd_pe       = info.get("forwardPE") or info.get("trailingPE") or 22
        revenue_growth = info.get("revenueGrowth") or 0.12
        target_pe_base = min(max(float(fwd_pe) * (1 + revenue_growth * 0.5), 15), 80)
        dcf_fair_value = float(eps) * target_pe_base

        # ── 4. ANALYST CONSENSUS ANCHOR ─────────────────────────────────────
        analyst_high   = info.get("targetHighPrice")  or (price * 1.35)
        analyst_low    = info.get("targetLowPrice")   or (price * 0.75)
        analyst_mean   = info.get("targetMeanPrice")  or (price * 1.12)
        analyst_median = info.get("targetMedianPrice") or analyst_mean

        # ── 5. TARGETS POR TIMEFRAME ANCLADOS A VOLATILIDAD ─────────────
        
        # 7D
        sigma_7d = daily_vol * math.sqrt(7)
        bull_7d  = price * (1 + 1.2 * sigma_7d)
        bear_7d  = price * (1 - 1.2 * sigma_7d)
        base_7d  = price * (1 + 0.3 * sigma_7d)
        
        # 30D
        sigma_30d = daily_vol * math.sqrt(21)
        bull_30d  = price * (1 + 1.5 * sigma_30d) 
        bear_30d  = price * (1 - 1.5 * sigma_30d)
        base_30d  = price * (1 + 0.4 * sigma_30d)

        # 3M
        sigma_3m = annual_vol * math.sqrt(63/252)
        bull_3m  = price * (1 + 1.6 * sigma_3m)
        bear_3m  = price * (1 - 1.6 * sigma_3m)
        base_3m  = (price * (1 + revenue_growth * 0.25) + analyst_median * 0.3) / 1.3

        # 6M
        sigma_6m = annual_vol * math.sqrt(126/252)
        bull_6m  = min(price * (1 + 1.8 * sigma_6m), float(analyst_high) * 1.05)
        bear_6m  = max(price * (1 - 1.8 * sigma_6m), float(analyst_low) * 0.95)
        base_6m  = (dcf_fair_value * 0.5 + float(analyst_mean) * 0.5)
        base_6m  = (base_6m + price * (1 + 0.5 * sigma_6m)) / 2

        # 12M
        sigma_12m = annual_vol
        bull_12m  = (price * (1 + 2.0 * sigma_12m) * 0.5 + float(analyst_high) * 0.5)
        bear_12m  = (price * (1 - 1.5 * sigma_12m) * 0.5 + float(analyst_low)  * 0.5)
        base_12m  = (dcf_fair_value * 0.5 + float(analyst_median) * 0.5)
        bull_12m  = max(bull_12m, base_12m * 1.10)
        bear_12m  = min(bear_12m, base_12m * 0.88)

        def rnd(v): return round(float(v), 2)

        return {
            "methodology": {
                "annual_volatility_pct": round(annual_vol * 100, 2),
                "daily_vol_pct": round(daily_vol * 100, 3),
                "atr_14": rnd(atr),
                "dcf_fair_value": rnd(dcf_fair_value),
                "analyst_high": rnd(analyst_high),
                "analyst_low": rnd(analyst_low),
                "analyst_mean": rnd(analyst_mean),
                "analyst_median": rnd(analyst_median),
            },
            "targets": {
                "7d":  {"bull": rnd(bull_7d),  "base": rnd(base_7d),  "bear": rnd(bear_7d)},
                "30d": {"bull": rnd(bull_30d), "base": rnd(base_30d), "bear": rnd(bear_30d)},
                "3m":  {"bull": rnd(bull_3m),  "base": rnd(base_3m),  "bear": rnd(bear_3m)},
                "6m":  {"bull": rnd(bull_6m),  "base": rnd(base_6m),  "bear": rnd(bear_6m)},
                "12m": {"bull": rnd(bull_12m), "base": rnd(base_12m), "bear": rnd(bear_12m)},
            }
        }
    except Exception as e:
        price_f = float(hist['Close'].iloc[-1]) if not hist.empty else 100.0
        return {
            "methodology": {"error": str(e)},
            "targets": {
                "7d":  {"bull": round(price_f*1.02,2), "base": round(price_f*1.005,2), "bear": round(price_f*0.98,2)},
                "30d": {"bull": round(price_f*1.06,2), "base": round(price_f*1.02,2),  "bear": round(price_f*0.94,2)},
                "3m":  {"bull": round(price_f*1.12,2), "base": round(price_f*1.05,2),  "bear": round(price_f*0.90,2)},
                "6m":  {"bull": round(price_f*1.20,2), "base": round(price_f*1.09,2),  "bear": round(price_f*0.84,2)},
                "12m": {"bull": round(price_f*1.35,2), "base": round(price_f*1.14,2),  "bear": round(price_f*0.75,2)},
            }
        }


# ─────────────────────────────────────────────────────────────────────────────
# SCHEMA
# ─────────────────────────────────────────────────────────────────────────────

class SignalDim(BaseModel):
    score: int = Field(..., description="Puntuación 0-100 donde 100 = máximamente favorable/alcista para esta señal y 0 = máximamente desfavorable/bajista.")
    nota: str = Field(..., description="Justificación de UNA línea de la puntuación, citando el dato concreto que la sustenta.")

class SignalScores(BaseModel):
    flujo_institucional_opciones: SignalDim = Field(..., description="Flujo institucional de opciones: barridos/bloques grandes, Tipo A (transacción única $5M+), Tipo B (múltiples $1M+ en mismo contrato/strike/exp), delta de convicción 0.60-0.90. La señal de mayor peso.")
    fundamentales: SignalDim = Field(..., description="Calidad fundamental: crecimiento de ingresos, márgenes, FCF, balance, retornos de capital.")
    earnings: SignalDim = Field(..., description="Trayectoria de earnings: sorpresas, revisiones de EPS, guidance, proximidad del próximo reporte.")
    tecnicos: SignalDim = Field(..., description="Técnicos: tendencia, momentum, niveles clave, volumen, posición vs medias.")
    news_sec: SignalDim = Field(..., description="Noticias y filings SEC (8-K/10-Q/10-K) materiales recientes.")
    macro: SignalDim = Field(..., description="Macro: tasas, liquidez, ciclo, viento de cola/cara del sector.")
    riesgo: SignalDim = Field(..., description="Perfil de riesgo (100 = riesgo bien controlado/favorable, 0 = riesgo severo): valuación, concentración, fragilidad de la tesis.")

class TradeProbabilities(BaseModel):
    p_positive_12m: int = Field(..., description="Probabilidad 0-100 de un retorno positivo (>0%) a 12 meses, anclada en base-rates y la evidencia, no en optimismo.")
    p_touch_bull_12m: int = Field(..., description="Probabilidad 0-100 de que el precio toque el target Bull a 12m.")
    p_touch_bear_12m: int = Field(..., description="Probabilidad 0-100 de que el precio toque el target Bear a 12m.")
    p_up_10pct_3m: int = Field(..., description="Probabilidad 0-100 de un retorno >+10% en los próximos 3 meses.")
    rationale: str = Field(..., description="Una o dos líneas anclando estas probabilidades en base-rates históricos (cuántas veces movimientos así ocurren) y la evidencia concreta, evitando sobreconfianza.")

class VertexDeepAnalysis(BaseModel):
    biggest_pro: str = Field(..., description="El pro más grande y determinante para el crecimiento de la empresa.")
    biggest_risk: str = Field(..., description="El riesgo de ejecución o macroeconómico más severo en una oración.")
    watch_for: str = Field(..., description="Métrica, nivel clave o evento específico que se debe monitorear a corto plazo.")
    company_summary_simple: str = Field(..., description="Resumen de la compañía en palabras simples, qué hace, cómo gana dinero y por qué importa al inversor promedio.")
    analisis_numeros_actuales: str = Field(..., description="Resumen analítico de la capitalización de mercado y situación financiera actual.")
    crecimiento_yoy: str = Field(..., description="Detalle del comportamiento histórico reciente año tras año de ingresos y márgenes operativos.")
    crecimiento_proyectado: str = Field(..., description="Estimaciones de crecimiento para los próximos años impulsados por catalizadores de negocio.")
    sec_filing_10k: str = Field(..., description="Análisis profundo de los factores de riesgo y estados auditados declarados en el último reporte anual 10-K.")
    sec_filing_10q: str = Field(..., description="Análisis del rendimiento y balances del último reporte trimestral 10-Q.")
    sec_filing_8k: str = Field(..., description="Resumen de eventos materiales o comunicados urgentes reportados recientemente en el 8-K.")
    fair_value: float = Field(..., description="Valor justo esperado calculado matemáticamente en base al promedio ponderado de los targets a 1 año de Vertex y el precio objetivo medio de Wall Street.")
    upside_pct: float = Field(..., description="Porcentaje de crecimiento proyectado a 1 año desde el precio spot actual hasta el Fair Value futuro.")
    recommendation: str = Field(..., description="Recomendación final explícita (BUY, HOLD, SELL o AVOID) basada ESTRICTAMENTE en los targets a futuro a 1 año y el Fair Value futuro. NO bases tu decisión en el valor intrínseco actual.")
    conviccion_score: int = Field(..., description="Puntuación numérica estricta de convicción de la firma del 0 al 100.")
    conviccion_porque: str = Field(..., description="Justificación detallada y exhaustiva del porqué de esa puntuación numérica.")
    recomendacion_porque: str = Field(..., description="Explicación detallada de la acción sugerida y la lógica financiera basada en proyecciones futuras.")
    tesis_inversion_completa: str = Field(..., description="Tesis completa de inversión de la AI explicando detalladamente por qué es o no una buena asignación de capital.")
    tesis_riesgos: str = Field(..., description="Explicación y desglose analítico de los riesgos inherentes que podrían destruir la tesis.")
    analistas_consenso: str = Field(..., description="Visión general, targets promedio y qué dice el consenso actual de los analistas de Wall Street.")
    calculos_y_crecimiento_ai: str = Field(..., description="Detalle explícito de las proyecciones aritméticas, modelos aplicados y tasas de crecimiento estimadas.")
    posicion_competitiva: str = Field(..., description="Evaluación cualitativa del Moat o ventaja competitiva frente a rivales.")
    principales_competidores: str = Field(..., description="Lista de los principales competidores de la industria.")
    porque_mejor_peor_inversion: str = Field(..., description="Justificación exacta comparando márgenes, retornos de capital y múltiplos.")
    in_simple_terms: str = Field(..., description="Analogía simplificada del negocio para cualquier tipo de inversor.")
    should_you_buy_now: str = Field(..., description="Resumen definitivo de prudencia o compra agresiva en los niveles de cotización actuales.")
    the_bottom_line: str = Field(..., description="Conclusión ejecutiva final en una sola oración.")
    signal_scores: SignalScores = Field(..., description="Puntuación 0-100 de cada una de las 7 señales del framework Vertex (flujo de opciones, fundamentales, earnings, técnicos, news/SEC, macro, riesgo), cada una con su nota de una línea. Sé honesto y específico; estas puntuaciones se ponderan para la convicción final.")
    probabilities: TradeProbabilities = Field(..., description="Probabilidades calibradas del trade (positivo 12m, toca bull/bear, +10% en 3m) ancladas en base-rates. Se usan para dimensionar la posición vía Kelly fraccional.")

class BullCase(BaseModel):
    thesis: str = Field(..., description="La tesis ALCISTA más fuerte posible para esta acción, el steelman del caso comprador.")
    catalysts: str = Field(..., description="3-5 catalizadores concretos (lista en texto) que impulsarían la acción al alza.")
    why_underappreciated: str = Field(..., description="Por qué el mercado está subestimando esta oportunidad ahora mismo.")
    strongest_point: str = Field(..., description="El argumento alcista MÁS fuerte e irrefutable en una oración.")

class BearCase(BaseModel):
    thesis: str = Field(..., description="La tesis BAJISTA más fuerte posible, el steelman del caso vendedor/escéptico.")
    risks: str = Field(..., description="3-5 riesgos o señales de alarma concretos (lista en texto) que destruirían la tesis alcista.")
    what_breaks_it: str = Field(..., description="El escenario específico que rompe la tesis y cuánto downside implica.")
    strongest_point: str = Field(..., description="El argumento bajista MÁS fuerte e irrefutable en una oración.")

class DebateVerdict(BaseModel):
    winner: str = Field(..., description="Quién tiene el caso más fuerte: 'TORO', 'OSO' o 'EMPATE'.")
    lean: str = Field(..., description="Recomendación reconciliada final: BUY, HOLD, SELL o AVOID.")
    confidence: int = Field(..., description="Confianza 0-100 en el veredicto reconciliado, calibrada y honesta.")
    key_disagreement: str = Field(..., description="El punto central donde toro y oso discrepan — la verdadera variable que decide el trade.")
    what_would_flip: str = Field(..., description="La evidencia específica y observable que cambiaría tu recomendación (ej: 'pasa a SELL si el guidance de Q próximo baja >10%').")
    synthesis: str = Field(..., description="Síntesis equilibrada que reconcilia ambos casos en un veredicto accionable, sin sesgo de confirmación.")
    p_bull_correct: int = Field(..., description="Probabilidad 0-100 de que el caso TORO resulte correcto a 12 meses, anclada en base-rates.")

# Modelos para la sección Explore
class CommentRecord(BaseModel):
    platform: str
    text: str
    sentiment: str

class SourceRecord(BaseModel):
    twitter: int
    reddit: int
    news: int
    google: int
    yahoo: int
    seeking: int

class BuzzStock(BaseModel):
    ticker: str
    name: str
    sentiment: str
    sentimentScore: int
    totalMentions: int
    sources: SourceRecord
    summary: str
    keyThemes: list[str]
    comments: list[CommentRecord]
    aiThesis: str
    priceContext: str

class ExploreResponse(BaseModel):
    buzz_stocks: list[BuzzStock]


def get_clean_domain(url_string):
    if not url_string: return ""
    try:
        domain = urlparse(url_string).netloc.replace("www.", "")
        return domain
    except:
        return ""

def format_volume(vol):
    if not vol: return "N/A"
    if vol >= 1e9: return f"{vol/1e9:.2f}B"
    if vol >= 1e6: return f"{vol/1e6:.2f}M"
    if vol >= 1e3: return f"{vol/1e3:.2f}K"
    return str(vol)

def obtener_logo(ticker, website):
    domain = get_clean_domain(website)
    if domain:
        return f"https://logo.clearbit.com/{domain}"
    return f"https://ui-avatars.com/api/?name={ticker}&background=0B0E14&color=3b82f6&font-size=0.4&bold=true"


# ─────────────────────────────────────────────────────────────────────────────
# REDDIT + EARNINGS HELPERS (real-time context for the AI agent)
# ─────────────────────────────────────────────────────────────────────────────
def fetch_reddit_posts(ticker, limit=8):
    """Fetch real recent Reddit posts mentioning the ticker from finance subreddits.
    Uses Reddit public JSON API (no auth). Filters to last 90 days and sorts by
    upvotes so the AI gets the most credible/visible community discussion as context."""
    posts = []
    try:
        subreddits = "wallstreetbets+stocks+investing+options+StockMarket"
        url = f"https://www.reddit.com/r/{subreddits}/search.json"
        params = {
            "q": ticker,
            "restrict_sr": "on",
            "sort": "top",
            "t": "month",
            "limit": max(limit * 2, 16),
        }
        headers = {"User-Agent": "VertexFundOS/1.0 (market sentiment research)"}
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            cutoff = (datetime.now() - timedelta(days=90)).timestamp()
            for child in data.get("data", {}).get("children", []):
                p = child.get("data", {})
                created = p.get("created_utc", 0) or 0
                if created < cutoff:
                    continue
                posts.append({
                    "title":        (p.get("title", "") or "")[:240],
                    "text":         (p.get("selftext", "") or "")[:400],
                    "subreddit":    p.get("subreddit", ""),
                    "upvotes":      p.get("ups", 0) or 0,
                    "num_comments": p.get("num_comments", 0) or 0,
                    "date":         datetime.fromtimestamp(created).strftime("%Y-%m-%d"),
                })
            posts.sort(key=lambda x: x["upvotes"], reverse=True)
            posts = posts[:limit]
    except Exception:
        pass
    return posts


def format_reddit_context(posts):
    """Format Reddit posts into a literal text block to feed the AI as real context."""
    if not posts:
        return ""
    lines = []
    for p in posts:
        snippet = f" — {p['text']}" if p.get("text") else ""
        lines.append(
            f"[r/{p['subreddit']} · {p['date']} · {p['upvotes']} upvotes · "
            f"{p['num_comments']} comentarios] {p['title']}{snippet}"
        )
    return "\n".join(lines)


def fetch_earnings_info(stock, info):
    """Next earnings date, days until, and EPS estimate via yfinance calendar.
    Handles both dict (newer yfinance) and DataFrame (older) calendar formats."""
    out = {"next_date": None, "days_until": None, "eps_estimate": None, "label": "N/A"}
    try:
        next_date = None
        eps_est   = None
        cal = None
        try:
            cal = stock.calendar
        except Exception:
            cal = None

        if isinstance(cal, dict) and cal:
            ed = cal.get("Earnings Date")
            if isinstance(ed, (list, tuple)) and ed:
                next_date = ed[0]
            elif ed:
                next_date = ed
            eps_est = cal.get("Earnings Average") or cal.get("EPS Estimate")
        elif cal is not None and hasattr(cal, "loc"):
            try:
                next_date = cal.loc["Earnings Date"][0]
            except Exception:
                pass

        if eps_est in (None, ""):
            eps_est = info.get("forwardEps")

        nd = None
        if next_date is not None:
            try:
                if isinstance(next_date, str):
                    nd = datetime.strptime(next_date[:10], "%Y-%m-%d")
                elif hasattr(next_date, "year"):
                    nd = datetime(next_date.year, next_date.month, next_date.day)
            except Exception:
                nd = None

        if nd:
            days = (nd.date() - datetime.now().date()).days
            _meses = ["enero","febrero","marzo","abril","mayo","junio","julio",
                      "agosto","septiembre","octubre","noviembre","diciembre"]
            out["next_date"]    = nd.strftime("%Y-%m-%d")
            out["days_until"]   = days
            out["eps_estimate"] = round(float(eps_est), 2) if eps_est not in (None, "") else None
            out["label"]        = f"{nd.day} de {_meses[nd.month-1]} de {nd.year}"
    except Exception:
        pass
    return out



# ─────────────────────────────────────────────────────────────────────────────
# SEC EDGAR — Insider transactions (Form 4) + Institutional holders (13F)
# ─────────────────────────────────────────────────────────────────────────────
def fetch_earnings_history(stock, lookback=8):
    """#2 — Historial real de earnings para dar profundidad a la señal (20%): cuántos beats/misses,
    sorpresa media de EPS, y reacción del precio post-earnings (±% medio y sesgo). yfinance
    get_earnings_dates trae EPS estimado/reportado/sorpresa; el movimiento se calcula de la historia."""
    out = {"n": 0, "beats": 0, "misses": 0, "avg_surprise_pct": None,
           "avg_abs_move_pct": None, "avg_move_pct": None, "rows": []}
    try:
        ed = stock.get_earnings_dates(limit=24)
    except Exception:
        ed = None
    if ed is None or getattr(ed, "empty", True):
        return out
    closes = []
    try:
        px = stock.history(period="2y")
        if px is not None and not px.empty:
            try:
                idx = px.index.tz_localize(None)
            except (TypeError, AttributeError):
                idx = px.index
            closes = list(zip(list(idx), [float(c) for c in px["Close"].tolist()]))
    except Exception:
        closes = []

    def _move(ed_date):
        try:
            before = [c for (d, c) in closes if d.date() <= ed_date]
            after = [c for (d, c) in closes if d.date() > ed_date]
            if before and after:
                c0 = before[-1]; c1 = after[min(1, len(after) - 1)]
                if c0 > 0:
                    return round((c1 - c0) / c0 * 100, 1)
        except Exception:
            pass
        return None

    surprises, moves, rows = [], [], []
    for dt, r in ed.iterrows():
        try:
            rep = r.get("Reported EPS")
            if rep is None or (isinstance(rep, float) and math.isnan(rep)):
                continue                                   # futuro / aún sin reportar
            est = r.get("EPS Estimate")
            est_ok = est is not None and not (isinstance(est, float) and math.isnan(est))
            sp = r.get("Surprise(%)")
            spv = None if (sp is None or (isinstance(sp, float) and math.isnan(sp))) else round(float(sp), 1)
            beat = bool(float(rep) >= float(est)) if est_ok else None
            ed_date = dt.date() if hasattr(dt, "date") else None
            mv = _move(ed_date) if ed_date else None
            if spv is not None:
                surprises.append(spv)
            if mv is not None:
                moves.append(mv)
            if beat is True:
                out["beats"] += 1
            elif beat is False:
                out["misses"] += 1
            rows.append({"date": ed_date.strftime("%Y-%m-%d") if ed_date else str(dt)[:10],
                         "reported": round(float(rep), 2), "estimate": (round(float(est), 2) if est_ok else None),
                         "surprise_pct": spv, "beat": beat, "move_pct": mv})
            if len(rows) >= lookback:
                break
        except Exception:
            continue
    out["n"] = len(rows)
    out["rows"] = rows
    if surprises:
        out["avg_surprise_pct"] = round(sum(surprises) / len(surprises), 1)
    if moves:
        out["avg_abs_move_pct"] = round(sum(abs(m) for m in moves) / len(moves), 1)
        out["avg_move_pct"] = round(sum(moves) / len(moves), 1)
    return out


def _earnings_depth_block(eh, einfo):
    """Bloque de prompt para la señal de earnings con datos reales."""
    if not eh or not eh.get("n"):
        return ""
    parts = [f"{eh['beats']}/{eh['n']} beats en los últimos {eh['n']} reportes"]
    if eh.get("avg_surprise_pct") is not None:
        parts.append(f"sorpresa media de EPS {eh['avg_surprise_pct']:+.1f}%")
    if eh.get("avg_abs_move_pct") is not None:
        parts.append(f"reacción del precio ±{eh['avg_abs_move_pct']:.1f}% post-earnings "
                     f"(sesgo {eh.get('avg_move_pct', 0):+.1f}%)")
    nxt = ""
    if einfo and einfo.get("days_until") is not None:
        nxt = f" Próximo earnings en {einfo['days_until']}d"
        if einfo.get("eps_estimate") is not None:
            nxt += f" (EPS est. ${einfo['eps_estimate']})"
        nxt += "."
    return ("\nEARNINGS — HISTORIAL REAL (señal 20%): " + " · ".join(parts) + "." + nxt +
            " USO: calibra la señal de earnings con ESTO — beats consistentes con reacción alcista suben el "
            "score; misses o reacción negativa lo bajan. Si el próximo earnings cae dentro de tu horizonte, "
            "advierte el riesgo de IV crush en el plan de opciones.")


def _news_catalyst_context(noticias):
    """#3 — convierte titulares crudos en contexto con CATALIZADORES detectados (earnings, upgrades,
    guidance, M&A, SEC/8-K, demandas, productos) + resúmenes, en vez de solo títulos. Da más señal
    a la categoría News/SEC (10%) que una lista de titulares."""
    if not noticias:
        return "N/A", []
    cats = {
        "earnings": ["earnings", "eps", "results", "quarter", "beats", "misses", "guidance"],
        "rating": ["upgrade", "downgrade", "initiat", "price target", "overweight", "underweight", "buy rating", "sell rating"],
        "deal": ["acqui", "merger", "buyout", "stake", "deal", "partnership", "contract"],
        "regulatory": ["sec", "8-k", "lawsuit", "investigation", "probe", "antitrust", "fine", "settle"],
        "product": ["launch", "unveil", "approval", "fda", "recall", "chip", "ai "],
        "guidance": ["raises", "cuts", "outlook", "forecast", "warns"],
    }
    out_lines, tags_all = [], []
    for n in noticias[:6]:
        blob = (str(n.get("title", "")) + " " + str(n.get("summary", ""))).lower()
        tags = [c for c, kws in cats.items() if any(k in blob for k in kws)]
        tags_all.extend(tags)
        tag_s = f" [{'/'.join(tags)}]" if tags else ""
        summ = str(n.get("summary", "") or "")[:160]
        out_lines.append(f"· {n.get('title', '')}{tag_s}" + (f" — {summ}" if summ and summ != "No description available." else ""))
    uniq = sorted(set(tags_all))
    head = (f"Catalizadores detectados: {', '.join(uniq)}. " if uniq else "Sin catalizadores claros en titulares. ")
    return head + " ".join(out_lines), uniq


SEC_HEADERS = {"User-Agent": "Vertex Holding Group research contact@vertexholding.com",
               "Accept-Encoding": "gzip, deflate", "Host": "www.sec.gov"}
_SEC_TICKER_CACHE = {}

def _get_sec_cik(ticker):
    """Map a ticker to its zero-padded 10-digit SEC CIK (cached after first load)."""
    global _SEC_TICKER_CACHE
    try:
        if not _SEC_TICKER_CACHE:
            r = requests.get("https://www.sec.gov/files/company_tickers.json",
                             headers={"User-Agent": SEC_HEADERS["User-Agent"]}, timeout=15)
            if r.status_code == 200:
                for _, row in r.json().items():
                    _SEC_TICKER_CACHE[str(row.get("ticker", "")).upper()] = str(row.get("cik_str", "")).zfill(10)
        return _SEC_TICKER_CACHE.get(ticker.upper())
    except Exception:
        return None


def fetch_edgar_filings(ticker, limit=8):
    """Authoritative recent SEC filings for the company: Form 4 (insider) + Form 13F.
    Returns direct EDGAR links so the user can click through to the source filing."""
    out = {"cik": None, "form4": [], "form13f": []}
    try:
        cik = _get_sec_cik(ticker)
        if not cik:
            return out
        out["cik"] = cik
        r = requests.get(f"https://data.sec.gov/submissions/CIK{cik}.json",
                         headers={"User-Agent": SEC_HEADERS["User-Agent"]}, timeout=15)
        if r.status_code != 200:
            return out
        recent = r.json().get("filings", {}).get("recent", {})
        forms  = recent.get("form", [])
        dates  = recent.get("filingDate", [])
        accns  = recent.get("accessionNumber", [])
        docs   = recent.get("primaryDocument", [])
        cik_int = int(cik)
        for i, frm in enumerate(forms):
            acc_clean = accns[i].replace("-", "") if i < len(accns) else ""
            doc = docs[i] if i < len(docs) else ""
            url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_clean}/{doc}" if doc else \
                  f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type={frm}"
            entry = {"date": dates[i] if i < len(dates) else "", "form": frm, "url": url}
            if frm == "4" and len(out["form4"]) < limit:
                out["form4"].append(entry)
            elif frm in ("13F-HR", "13F-HR/A") and len(out["form13f"]) < limit:
                out["form13f"].append(entry)
        return out
    except Exception:
        return out


_8K_ITEMS = {
    "1.01": "acuerdo material (deal/contrato)", "1.02": "terminación de acuerdo material",
    "1.03": "bancarrota / receivership", "2.01": "completó adquisición o venta de activos",
    "2.02": "resultados de operación (earnings)", "2.03": "obligación financiera material (deuda)",
    "2.04": "aceleración de obligación financiera", "2.05": "costos por reestructuración/salida",
    "3.01": "aviso de delisting / incumplimiento de listado", "3.02": "venta no registrada de equity (posible dilución)",
    "4.01": "cambio de auditor", "4.02": "restatement (no confiar en EEFF previos)",
    "5.01": "cambio de control", "5.02": "cambio de directivos/junta (CEO/CFO/director)",
    "5.03": "cambio de estatutos / año fiscal", "5.07": "resultados de votación de accionistas",
    "7.01": "divulgación Reg FD", "8.01": "otro evento material",
    "9.01": "estados financieros y exhibits",
}

def fetch_recent_8k(ticker, lookback_days=45, limit=6):
    """8-K reales recientes de SEC EDGAR (submissions API) con sus ITEM codes traducidos a
    catalizadores. Da a la señal News/SEC (10%) eventos materiales VERIFICADOS (M&A, earnings,
    cambios de directivos, deuda, restatements) en vez de solo titulares de prensa."""
    out = []
    try:
        cik = _get_sec_cik(ticker)
        if not cik:
            return out
        r = requests.get(f"https://data.sec.gov/submissions/CIK{cik}.json",
                         headers={"User-Agent": SEC_HEADERS["User-Agent"]}, timeout=15)
        if r.status_code != 200:
            return out
        recent = r.json().get("filings", {}).get("recent", {})
        forms = recent.get("form", []); dates = recent.get("filingDate", [])
        items = recent.get("items", []); accns = recent.get("accessionNumber", [])
        docs = recent.get("primaryDocument", [])
        cik_int = int(cik)
        cutoff = (datetime.now() - timedelta(days=lookback_days)).date()
        for i, frm in enumerate(forms):
            if not str(frm).startswith("8-K"):
                continue
            try:
                fd = datetime.strptime(str(dates[i])[:10], "%Y-%m-%d").date()
            except Exception:
                continue
            if fd < cutoff:
                continue
            codes = [c.strip() for c in str(items[i] if i < len(items) else "").split(",") if c.strip()]
            human = [_8K_ITEMS.get(c, f"item {c}") for c in codes]
            acc = accns[i].replace("-", "") if i < len(accns) else ""
            doc = docs[i] if i < len(docs) else ""
            url = (f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc}/{doc}" if (acc and doc)
                   else f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=8-K")
            out.append({"date": fd.strftime("%Y-%m-%d"), "form": frm, "items": codes,
                        "items_desc": human, "url": url})
            if len(out) >= limit:
                break
    except Exception:
        return out
    return out


def _8k_catalyst_block(filings):
    """Bloque de prompt + tags de catalizador desde 8-K reales (señal News/SEC)."""
    if not filings:
        return "", []
    tags = sorted({d for f in filings for d in f.get("items_desc", [])})
    lines = [f"· {f['date']}: {', '.join(f.get('items_desc') or []) or '8-K'}" for f in filings[:6]]
    block = ("\n8-K RECIENTES (SEC EDGAR — eventos materiales VERIFICADOS, señal News/SEC 10%): "
             + " ".join(lines) + ". USO: un 8-K de earnings/M&A/cambio de CEO/deuda/restatement es un "
             "catalizador DURO — pondéralo por encima de titulares de prensa.")
    return block, tags


def fetch_insiders_data(stock, ticker):
    """Combine yfinance insider trades + institutional (13F-derived) holders + SEC EDGAR
    filing links into one structured payload for the AI agent and the UI."""
    data = {
        "transactions": [], "summary": {}, "institutional": [],
        "major_holders": {}, "edgar": {"cik": None, "form4": [], "form13f": []},
    }
    # ── Insider transactions (yfinance) ──────────────────────────────────────
    try:
        it = stock.insider_transactions
        if it is not None and hasattr(it, "empty") and not it.empty:
            cols = {c.lower(): c for c in it.columns}
            def col(*names):
                for n in names:
                    if n in cols: return cols[n]
                return None
            c_insider = col("insider")
            c_pos     = col("position")
            c_txn     = col("transaction")
            c_shares  = col("shares")
            c_value   = col("value")
            c_date    = col("start date", "date")
            buys_val = sells_val = 0.0
            buys_n = sells_n = 0
            for _, row in it.head(25).iterrows():
                txn   = str(row[c_txn]) if c_txn else ""
                shares = row[c_shares] if c_shares else None
                value  = row[c_value] if c_value else None
                dt     = row[c_date] if c_date else None
                try: dt_str = dt.strftime("%Y-%m-%d") if hasattr(dt, "strftime") else str(dt)[:10]
                except Exception: dt_str = str(dt)[:10] if dt is not None else ""
                is_buy  = "purchase" in txn.lower() or "buy" in txn.lower()
                is_sell = "sale" in txn.lower() or "sell" in txn.lower()
                v = float(value) if value not in (None, "") and not (isinstance(value, float) and math.isnan(value)) else 0.0
                if is_buy:  buys_val += v;  buys_n += 1
                if is_sell: sells_val += v; sells_n += 1
                data["transactions"].append({
                    "insider":     str(row[c_insider]) if c_insider else "",
                    "position":    str(row[c_pos]) if c_pos else "",
                    "transaction": txn,
                    "shares":      int(shares) if shares not in (None, "") and not (isinstance(shares, float) and math.isnan(shares)) else None,
                    "value":       round(v, 2) if v else None,
                    "date":        dt_str,
                    "is_buy":      is_buy, "is_sell": is_sell,
                })
            data["summary"] = {
                "buys_count": buys_n, "sells_count": sells_n,
                "buys_value": round(buys_val, 2), "sells_value": round(sells_val, 2),
                "net_value": round(buys_val - sells_val, 2),
                "signal": "BULLISH" if buys_val > sells_val else ("BEARISH" if sells_val > buys_val else "NEUTRAL"),
            }
    except Exception:
        pass
    # ── Institutional holders (13F-derived) ──────────────────────────────────
    try:
        inst = stock.institutional_holders
        if inst is not None and hasattr(inst, "empty") and not inst.empty:
            cols = {c.lower(): c for c in inst.columns}
            def icol(*names):
                for n in names:
                    if n in cols: return cols[n]
                return None
            c_holder = icol("holder")
            c_shares = icol("shares")
            c_value  = icol("value")
            c_pct    = icol("pctheld", "% out", "pctout")
            c_date   = icol("date reported", "datereported")
            for _, row in inst.head(15).iterrows():
                dt = row[c_date] if c_date else None
                try: dt_str = dt.strftime("%Y-%m-%d") if hasattr(dt, "strftime") else str(dt)[:10]
                except Exception: dt_str = str(dt)[:10] if dt is not None else ""
                pct_raw = row[c_pct] if c_pct else None
                try: pct = round(float(pct_raw) * 100, 2) if (pct_raw is not None and float(pct_raw) < 1) else (round(float(pct_raw), 2) if pct_raw is not None else None)
                except Exception: pct = None
                data["institutional"].append({
                    "holder": str(row[c_holder]) if c_holder else "",
                    "shares": int(row[c_shares]) if c_shares and row[c_shares] not in (None, "") else None,
                    "value":  round(float(row[c_value]), 2) if c_value and row[c_value] not in (None, "") else None,
                    "pct_held": pct, "date": dt_str,
                })
    except Exception:
        pass
    # ── Major holders breakdown (% insiders / % institutions) ────────────────
    try:
        mh = stock.major_holders
        if mh is not None and hasattr(mh, "empty") and not mh.empty:
            # Newer yfinance: DataFrame indexed by metric name with a 'Value' column
            try:
                idx = {str(i).lower(): i for i in mh.index}
                def mval(key):
                    for k, orig in idx.items():
                        if key in k:
                            v = mh.loc[orig]
                            v = v.iloc[0] if hasattr(v, "iloc") else v
                            return round(float(v) * 100, 2) if float(v) < 1 else round(float(v), 2)
                    return None
                data["major_holders"] = {
                    "pct_insiders":     mval("insiderspercentheld") or mval("insider"),
                    "pct_institutions": mval("institutionspercentheld") or mval("institutions"),
                }
            except Exception:
                pass
    except Exception:
        pass
    # ── SEC EDGAR authoritative filing links ─────────────────────────────────
    data["edgar"] = fetch_edgar_filings(ticker, limit=8)
    return data


def format_insiders_context(ins):
    """Condense the insider/institutional payload into a short text block for the AI prompt."""
    if not ins:
        return ""
    parts = []
    s = ins.get("summary") or {}
    if s:
        parts.append(
            f"INSIDERS (ultimas operaciones): {s.get('buys_count',0)} compras (${s.get('buys_value',0):,.0f}) "
            f"vs {s.get('sells_count',0)} ventas (${s.get('sells_value',0):,.0f}) | Neto: ${s.get('net_value',0):,.0f} | Señal: {s.get('signal','N/A')}"
        )
    mh = ins.get("major_holders") or {}
    if mh.get("pct_institutions") is not None:
        parts.append(f"Propiedad institucional: {mh.get('pct_institutions')}% | Insiders: {mh.get('pct_insiders','N/A')}%")
    inst = ins.get("institutional") or []
    if inst:
        top = ", ".join(f"{h['holder']} ({h.get('pct_held','?')}%)" for h in inst[:5] if h.get('holder'))
        if top:
            parts.append(f"Top tenedores institucionales (13F): {top}")
    return "\n".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/explore")
def get_market_buzz():
    """
    Real-time market buzz: ALL stocks mentioned 5+ times across X/Twitter, Reddit,
    Google, Yahoo Finance, news and web. Returns bullish/neutral/bearish with scores.
    Uses Gemini + Google Search grounding.
    """
    try:
        today_str = datetime.now().strftime("%A, %B %d, %Y")
        prompt = f"""You are a real-time market intelligence analyst for Vertex AI. Today is {today_str}.

TASK: Search X/Twitter, Reddit (r/wallstreetbets, r/stocks, r/investing, r/options, r/stockmarket),
StockTwits, Google Finance, Yahoo Finance, financial news (Bloomberg, Reuters, CNBC, MarketWatch,
Seeking Alpha, Barron's, TheStreet) and Google Trends RIGHT NOW.

Find EVERY stock ticker (equities, ETFs, crypto) that has been mentioned or discussed MORE THAN 5 TIMES
across these platforms in the last 24-72 hours. Do NOT limit the list — include ALL of them.
Expect between 20 and 40 tickers. Miss none. Include speculative plays, meme stocks, large caps,
small caps, ETFs, anything actively discussed.

For EACH ticker return EXACTLY this JSON structure (no markdown, no backticks, just raw JSON):

{{
  "buzz_stocks": [
    {{
      "ticker": "AAPL",
      "name": "Apple Inc.",
      "sector": "Technology",
      "totalMentions": 312,
      "sentiment": "bullish",
      "sentimentScore": 72,
      "change": "+1.23%",
      "summary": "2-3 sentences explaining WHY this is trending RIGHT NOW with specific details.",
      "keyThemes": ["AI chips", "earnings beat", "buyback"],
      "sources": {{
        "twitter": 120,
        "reddit": 85,
        "news": 60,
        "google": 30,
        "yahoo": 17
      }},
      "comments": [
        {{"user": "@handle", "platform": "X/Twitter", "text": "Specific realistic comment about this stock", "sentiment": "bullish", "likes": 245}},
        {{"user": "u/handle", "platform": "Reddit", "text": "Specific realistic comment about this stock", "sentiment": "bullish", "likes": 189}},
        {{"user": "StockTwits", "platform": "StockTwits", "text": "Specific realistic comment about this stock", "sentiment": "neutral", "likes": 44}}
      ],
      "aiThesis": "2-3 sentence AI investment thesis and speculation based on current buzz.",
      "priceContext": "Price action context: current price, recent move, key levels."
    }}
  ]
}}

CRITICAL RULES:
1. sentiment MUST be exactly one of: "bullish", "neutral", "bearish" (lowercase only)
2. sentimentScore: integer 0-100 (0=extremely bearish, 50=neutral, 100=extremely bullish)
3. Include ALL tickers with 5+ mentions — do NOT cap at 12 or any number
4. Be specific: real tickers, real reasons, real sentiment based on what people actually say today
5. Return ONLY the raw JSON object — absolutely no markdown, no ```json, no explanation text"""

        response = client_gemini.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.4,
                tools=[types.Tool(google_search=types.GoogleSearch())]
            )
        )

        raw = response.text.strip()
        if raw.startswith("```"):
            raw = re.sub(r'^```[a-z]*\n?', '', raw)
            raw = re.sub(r'\n?```$', '', raw)
        raw = raw.strip()

        data = json.loads(raw)
        stocks = data.get("buzz_stocks", [])

        # Normalize sentiment values to strict lowercase bullish/neutral/bearish
        for s in stocks:
            sent = str(s.get("sentiment", "neutral")).lower().strip()
            if any(x in sent for x in ["bull", "positiv", "alcist"]):
                s["sentiment"] = "bullish"
            elif any(x in sent for x in ["bear", "negativ", "bajist"]):
                s["sentiment"] = "bearish"
            else:
                s["sentiment"] = "neutral"
            # Ensure sentimentScore aligns with label
            score = int(s.get("sentimentScore", 50))
            if s["sentiment"] == "bullish"  and score < 55: score = max(score, 60)
            if s["sentiment"] == "bearish"  and score > 45: score = min(score, 40)
            if s["sentiment"] == "neutral"  and (score < 40 or score > 60): score = 50
            s["sentimentScore"] = score

        return stocks

    except json.JSONDecodeError:
        # Fallback without google_search
        try:
            today_str2 = datetime.now().strftime("%A, %B %d, %Y")
            prompt2 = f"""Today is {today_str2}. Generate a list of 25 stocks currently trending in financial social media and news.
Include large caps, growth stocks, meme stocks, and ETFs. Mix of bullish, neutral, and bearish.
Return ONLY valid JSON: {{"buzz_stocks":[{{"ticker":"","name":"","sector":"","totalMentions":0,"sentiment":"bullish","sentimentScore":70,"change":"","summary":"","keyThemes":[],"sources":{{"twitter":0,"reddit":0,"news":0,"google":0,"yahoo":0}},"comments":[{{"user":"","platform":"","text":"","sentiment":"bullish","likes":0}}],"aiThesis":"","priceContext":""}}]}}"""
            resp2 = client_gemini.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt2,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.5
                )
            )
            raw2 = resp2.text.strip()
            if raw2.startswith("```"):
                raw2 = re.sub(r'^```[a-z]*\n?', '', raw2)
                raw2 = re.sub(r'\n?```$', '', raw2)
            data2 = json.loads(raw2.strip())
            return data2.get("buzz_stocks", [])
        except Exception as ex2:
            raise HTTPException(status_code=500, detail=f"Explore error: {str(ex2)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/explore-screens")
def get_explore_screens(n: int = 6):
    """#Explore screens — pantallas fundamentales EN VIVO (Gemini + búsqueda) que complementan
    el Market Buzz (lo más mencionado) con calidad fundamental:
      • growing_yoy: creciendo año-tras-año
      • reducing_debt: reduciendo deuda
      • approaching_profitability: a punto de volverse rentables."""
    if not API_KEY:
        return {"ok": False, "error": "Falta GEMINI_API_KEY para los screens fundamentales."}
    try:
        n = max(3, min(int(n), 10))
        prompt = f"""Eres un analista cuantitativo. Usando datos REALES y recientes (BÚSCALOS con la herramienta
de búsqueda), arma CUATRO pantallas de acciones de EE.UU. (tickers reales), {n} por pantalla, priorizando nombres
líquidos y reconocibles:
1) "growing_yoy": empresas con crecimiento de ingresos/utilidades año-tras-año fuerte y sostenido.
2) "reducing_debt": empresas que están REDUCIENDO su deuda neta de forma notable.
3) "approaching_profitability": empresas a punto de volverse rentables (pérdidas que se achican rápido o breakeven cercano), o que cruzaron a rentabilidad recientemente.
4) "high_growth": empresas de ALTO crecimiento AUNQUE tengan valoración alta (caras/múltiplos elevados) — momentum de ingresos muy fuerte que justifica el precio.
Devuelve SOLO un objeto JSON crudo (sin markdown, sin ```), con esta forma EXACTA:
{{"growing_yoy":[{{"ticker":"","name":"","sector":"","metric":"cifra clave corta, ej. 'Ingresos +32% YoY'","note":"una frase de por qué"}}],
"reducing_debt":[{{"ticker":"","name":"","sector":"","metric":"ej. 'Deuda neta -$4B en 12m'","note":"una frase"}}],
"approaching_profitability":[{{"ticker":"","name":"","sector":"","metric":"ej. 'Margen op. -3% → +2% est.'","note":"una frase"}}],
"high_growth":[{{"ticker":"","name":"","sector":"","metric":"ej. 'Ingresos +55% YoY · P/S 18x'","note":"una frase de por qué vale aunque sea cara"}}]}}
Reglas duras: tickers reales; cifras reales y recientes; 'metric' corto (<= 6 palabras); 'note' una sola frase."""
        resp = client_gemini.models.generate_content(
            model='gemini-2.5-flash', contents=prompt,
            config=types.GenerateContentConfig(temperature=0.45,
                tools=[types.Tool(google_search=types.GoogleSearch())]))
        raw = (resp.text or "").strip()
        if raw.startswith("```"):
            raw = re.sub(r'^```[a-z]*\n?', '', raw)
            raw = re.sub(r'\n?```$', '', raw)
        data = json.loads(raw.strip())

        def _clean(lst):
            out = []
            for it in (lst or []):
                tk = str(it.get("ticker", "")).upper().strip()
                if tk:
                    it["ticker"] = tk
                    out.append(it)
            return out

        screens = {
            "growing_yoy": _clean(data.get("growing_yoy")),
            "reducing_debt": _clean(data.get("reducing_debt")),
            "approaching_profitability": _clean(data.get("approaching_profitability")),
            "high_growth": _clean(data.get("high_growth")),
        }
        return {"ok": True, "screens": screens,
                "generated_at": datetime.now().strftime('%m/%d/%Y, %I:%M:%S %p')}
    except HTTPException:
        raise
    except Exception as e:
        return {"ok": False, "error": f"No se pudieron generar los screens: {e}"}


@app.get("/api/explore-deep")
def get_explore_deep(ticker: str):
    """
    Deep dive on a trending ticker: uses Grok (xAI) to analyze what people are saying
    on X/Twitter, Reddit, web, news + AI thesis and speculation. Same 3-month window.
    """
    ticker_clean = ticker.upper().strip()
    company_name = ticker_clean
    ctx_str = f"Accion: {ticker_clean}"
    try:
        info = yf.Ticker(ticker_clean).info
        company_name = info.get("longName", ticker_clean)
        sector   = info.get("sector",   "N/A")
        industry = info.get("industry", "N/A")
        price    = info.get("currentPrice") or info.get("regularMarketPrice") or "N/A"
        prev     = info.get("previousClose")
        pct_chg  = round(((float(price) - float(prev)) / float(prev)) * 100, 2) if (price and prev and price != "N/A") else None
        mktcap   = info.get("marketCap")
        pe       = info.get("trailingPE") or "N/A"
        tgt      = info.get("targetMeanPrice")
        analysts = info.get("numberOfAnalystOpinions", 0)
        ctx_str  = (
            f"{company_name} ({ticker_clean}) | Sector: {sector} | Industry: {industry} | "
            f"Precio actual: ${price} | Market Cap: {'$'+str(round(mktcap/1e9,1))+'B' if mktcap else 'N/A'} | "
            f"P/E: {pe} | Target WS: {'$'+str(tgt) if tgt else 'N/A'} | Analistas: {analysts}"
        )
    except Exception:
        pass

    # Fetch REAL Reddit posts to feed Grok as literal context
    reddit_posts   = fetch_reddit_posts(ticker_clean, limit=8)
    reddit_context = format_reddit_context(reddit_posts)

    three_months_ago = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
    today_str        = datetime.now().strftime("%Y-%m-%d")

    _meses = ["enero","febrero","marzo","abril","mayo","junio","julio",
              "agosto","septiembre","octubre","noviembre","diciembre"]
    _s = datetime.now() - timedelta(days=90)
    _e = datetime.now()
    date_label = (f"{_s.day} de {_meses[_s.month-1]} de {_s.year} "
                  f"— {_e.day} de {_meses[_e.month-1]} de {_e.year}")

    system_msg = (
        "Eres el maximo analista de inteligencia de mercado y psicologia del inversor de Vertex AI. "
        "Tienes acceso en tiempo real a X/Twitter, Reddit, StockTwits, noticias financieras y la web. "
        f"REGLA CRITICA DE TIEMPO: SOLO analiza publicaciones, posts, tweets y noticias entre {three_months_ago} "
        f"y {today_str} (ultimos 90 dias). Ignora absolutamente todo lo anterior a {three_months_ago}. "
        "Eres extremadamente detallado, especifico, con ejemplos reales, citas con fecha aproximada, "
        "fuentes concretas, cifras y datos. Ademas de describir el sentimiento, ESPECULAS con tu propia "
        "tesis de inversion basada en lo que la comunidad esta diciendo. Respondes SIEMPRE en espanol."
    )

    reddit_block = reddit_context if reddit_context else "(No se pudieron extraer posts de Reddit en este momento; usa tu conocimiento general pero respeta la ventana de 90 dias.)"
    user_msg = f"""Haz un analisis PROFUNDO, EXHAUSTIVO y COMPLETO de {ticker_clean} ({company_name}).
Datos del activo: {ctx_str}
Periodo de analisis: {three_months_ago} a {today_str} (ultimos 3 meses UNICAMENTE).

POSTS REALES DE REDDIT (extraidos en vivo de r/wallstreetbets, r/stocks, r/investing, r/options, r/StockMarket — ordenados por upvotes para ponderar credibilidad). USA ESTOS POSTS REALES como evidencia principal de lo que la comunidad dice AHORA; citalos y analizalos especificamente:
{reddit_block}

Responde con EXACTAMENTE estas 7 secciones. Cada seccion debe ser MUY DETALLADA con datos reales,
ejemplos especificos, citas con fecha, cifras concretas y profundidad de analisis institucional:

**1. SENTIMIENTO ACTUAL — X/TWITTER, REDDIT, WEB (ULTIMOS 3 MESES)**
Cual es el estado emocional dominante de la comunidad sobre {ticker_clean} en este momento?
Da el % estimado Bulls vs Bears en cada plataforma (X/Twitter, Reddit, StockTwits, Web).
Como ha evolucionado el sentimiento semana a semana dentro de este periodo? Con fechas.
Que eventos de los ultimos 3 meses dispararon cambios de sentimiento especificos?

**2. QUE DICE LA GENTE — NARRATIVAS Y COMENTARIOS REALES**
Que narrativas y "stories" estan circulando sobre {ticker_clean} en X/Twitter, Reddit r/wallstreetbets,
r/investing, r/stocks y StockTwits en los ultimos 90 dias?
Cita o parafrasea comentarios y posts REALES y RECIENTES con fecha aproximada y plataforma.
Que argumentos concretos usan los bulls y los bears? Que frases se repiten mas?
Identifica si hay voces o cuentas influyentes moviendo la narrativa recientemente.

**3. NOTICIAS RECIENTES Y SU IMPACTO EN EL SENTIMIENTO**
Cuales son las noticias mas relevantes sobre {ticker_clean} en los ultimos 3 meses?
Para cada noticia relevante: titulo, fuente, fecha aproximada, y cual fue la reaccion emocional
de la comunidad (subida de optimismo, panico, confusion, etc.)
Que noticias proximas o eventos pendientes estan anticipando los inversores?

**4. CONVICCION DE LA COMUNIDAD Y SESGOS COGNITIVOS**
Que tan alta es la conviccion de los bulls y bears en este momento (0-100) y por que?
Que sesgos psicologicos dominan: FOMO, Efecto Manada, Sesgo de Confirmacion, Anclaje, Aversion a Perdida?
Hay capitulacion de bajistas, acumulacion silenciosa de institucionales, o euforia retail?
Cuanta "piel en el juego" parece tener la comunidad (posiciones grandes, opciones, etc.)?

**5. SENTIMIENTO vs FUNDAMENTALES**
El sentimiento actual esta alineado o desconectado de los fundamentales reales de {company_name}?
Esta el mercado siendo mas emocional que racional con {ticker_clean}?
Hay oportunidad contrarian (comprar cuando todos venden o vender cuando todos compran)?
Que dice el Fear & Greed Index del mercado y como aplica especificamente a {ticker_clean}?

**6. TESIS DE INVERSION — BASADA EN EL SENTIR COLECTIVO**
Basandote en TODA la actividad de la comunidad en los ultimos 3 meses, construye una tesis especulativa:
- Donde podria estar el precio en 3-6 meses si la narrativa actual se cumple?
- Cual es el escenario base, bull y bear segun lo que dice la gente?
- Que catalista especifico confirmaria o invalidaria la narrativa dominante?
- Que esta viendo la comunidad que Wall Street aun no ha valorado completamente?
- Es esto una señal de alerta por euforia excesiva, o una oportunidad de entrada temprana?

**7. VEREDICTO DEL AGENTE AI — ESPECULACION Y RECOMENDACION**
Como el maximo experto de Vertex AI: cual es tu evaluacion DEFINITIVA?
El momento psicologico actual favorece entrar, salir o esperar en {ticker_clean}?
Nivel de conviccion del agente AI (0-100) y por que.
La trampa psicologica mas grande que ve el agente en como la gente percibe a {ticker_clean} ahora.
Especulacion del agente: si todo va como la comunidad espera, donde puede estar el precio en 12 meses?
"""

    grok_ok    = False
    grok_text  = ""
    grok_error = ""

    try:
        resp = requests.post(
            "https://api.x.ai/v1/chat/completions",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {XAI_API_KEY}"},
            json={
                "model":       "grok-3",
                "messages": [
                    {"role": "system", "content": system_msg},
                    {"role": "user",   "content": user_msg}
                ],
                "max_tokens":  5000,
                "temperature": 0.45
            },
            timeout=130
        )
        if resp.status_code == 200:
            grok_text = resp.json()["choices"][0]["message"]["content"].strip()
            grok_ok   = True
        else:
            grok_error = f"Grok error {resp.status_code}: {resp.text[:400]}"
    except requests.exceptions.Timeout:
        grok_error = "Timeout: Grok tardó demasiado. Intenta de nuevo."
    except Exception as ex:
        grok_error = f"Error conectando con Grok: {str(ex)}"

    # Compute overall sentiment score from grok_text (same algo as /api/sentiment)
    score, label, color = 50, "NEUTRAL", "amber"
    if grok_ok and grok_text:
        txt = grok_text.lower()
        bulls = ["bullish","alcist","optimism","euforia","compra","subir","sube","rally","positiv",
                 "esperanza","confianza","buy","strong","crecimiento","potencial","conviction"]
        bears = ["bearish","bajist","pesimism","panico","miedo","venta","bajar","baja","negativ",
                 "ansiedad","temor","sell","riesgo","caida","preocupacion"]
        bc = sum(txt.count(w) for w in bulls)
        nc = sum(txt.count(w) for w in bears)
        tot = bc + nc
        if tot > 0:
            p = (bc / tot) * 100
            score = round(p)
            if   p >= 70: label, color = "MUY BULLISH", "emerald"
            elif p >= 55: label, color = "BULLISH",      "emerald"
            elif p >= 45: label, color = "NEUTRAL",      "amber"
            elif p >= 30: label, color = "BEARISH",      "red"
            else:         label, color = "MUY BEARISH",  "red"

    return {
        "ticker":            ticker_clean,
        "company_name":      company_name,
        "context":           ctx_str,
        "overall_score":     score,
        "overall_label":     label,
        "overall_color":     color,
        "grok_ok":           grok_ok,
        "grok_text":         grok_text,
        "grok_error":        grok_error,
        "date_range_label":  date_label,
        "window_start":      three_months_ago,
        "window_end":        today_str,
        "reddit_posts_count": len(reddit_posts),
        "reddit_posts":      reddit_posts,
        "generated_at":      datetime.now().strftime('%m/%d/%Y, %I:%M:%S %p')
    }




@app.get("/api/quote")
def get_quick_quote(ticker: str):
    ticker_clean = ticker.upper().strip()
    try:
        stock = yf.Ticker(ticker_clean)
        info = stock.info
        hist = stock.history(period="2d")
        if hist.empty:
            raise HTTPException(status_code=404, detail="No data")

        precio_actual   = info.get("currentPrice") or float(hist['Close'].iloc[-1])
        precio_anterior = float(hist['Close'].iloc[-2]) if len(hist) > 1 else precio_actual
        cambio_pct = ((precio_actual - precio_anterior) / precio_anterior) * 100

        volumen  = info.get("regularMarketVolume") or (int(hist['Volume'].iloc[-1]) if not hist.empty else 0)
        high_dia = info.get("dayHigh") or float(hist['High'].iloc[-1])
        low_dia  = info.get("dayLow")  or float(hist['Low'].iloc[-1])
        vwap_dia = info.get("vwap")    or ((high_dia + low_dia + precio_actual) / 3)
        logo_url = obtener_logo(ticker_clean, info.get("website", ""))

        # --- Precio extendido (after-hours / pre-market) ---
        mkt_state = (info.get("marketState") or "").upper()
        reg_close = info.get("regularMarketPrice") or precio_actual
        ext_price, ext_label = None, None
        if mkt_state.startswith("POST") or mkt_state == "CLOSED":
            ext_price, ext_label = info.get("postMarketPrice"), "After-Hours"
        elif mkt_state.startswith("PRE"):
            ext_price, ext_label = info.get("preMarketPrice"), "Pre-Market"
        after_hours = None
        if ext_price and reg_close:
            after_hours = {
                "price": round(float(ext_price), 2),
                "change_pct": round((float(ext_price) - float(reg_close)) / float(reg_close) * 100.0, 2),
                "label": ext_label,
            }

        return {
            "ticker": ticker_clean,
            "nombre_completo": info.get("longName", ticker_clean),
            "precio": round(precio_actual, 2),
            "cambio_pct": round(cambio_pct, 2),
            "volumen": format_volume(volumen),
            "vwap": round(vwap_dia, 2),
            "high": round(high_dia, 2),
            "low": round(low_dia, 2),
            "after_hours": after_hours,
            "precio_fuente": "yfinance",
            "as_of": datetime.now().strftime('%I:%M:%S %p'),
            "logo_url": logo_url
        }
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/news")
def get_company_news(ticker: str):
    ticker_clean = ticker.upper().strip()
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        url = f"https://query2.finance.yahoo.com/v1/finance/search?q={ticker_clean}&newsCount=25"
        resp = requests.get(url, headers=headers, timeout=5)
        
        noticias_formateadas = []
        
        if resp.status_code == 200:
            data = resp.json()
            for item in data.get("news", []):
                pub_ts = item.get("providerPublishTime", 0)
                pub_time = datetime.fromtimestamp(pub_ts).strftime('%Y-%m-%d %I:%M %p') if pub_ts else "Reciente"
                noticias_formateadas.append({
                    "title": item.get("title", "No Title"),
                    "publisher": item.get("publisher", "Yahoo Finance"),
                    "publish_time": pub_time,
                    "publish_ts": pub_ts,
                    "summary": item.get("summary", "No description available."),
                    "link": item.get("link", "")
                })
        
        if not noticias_formateadas:
            stock = yf.Ticker(ticker_clean)
            raw_news = stock.news if stock.news else []
            for item in raw_news:
                pub_ts = item.get("providerPublishTime", 0)
                pub_time = datetime.fromtimestamp(pub_ts).strftime('%Y-%m-%d %I:%M %p') if pub_ts else "Reciente"
                noticias_formateadas.append({
                    "title": item.get("title", "No Title"),
                    "publisher": item.get("publisher", "Yahoo Finance"),
                    "publish_time": pub_time,
                    "publish_ts": pub_ts,
                    "summary": item.get("summary", "No description available."),
                    "link": item.get("link", "")
                })

        vistos = set()
        feed_final = []
        for n in noticias_formateadas:
            if n["link"] not in vistos:
                vistos.add(n["link"])
                feed_final.append(n)

        feed_final.sort(key=lambda x: x["publish_ts"], reverse=True)
        return {"ticker": ticker_clean, "total": len(feed_final), "noticias": feed_final}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/history")
def get_price_history(ticker: str, period: str = "1mo", interval: str = "1d"):
    ticker_clean = ticker.upper().strip()
    interval = (interval or "1d").lower()
    is_intraday = interval in ("1h", "10m")
    if not is_intraday:
        interval = "1d"
        valid_periods = ["7d", "1mo", "3mo", "6mo", "1y"]
        if period not in valid_periods:
            period = "1mo"
        yf_interval = "1d"
    elif interval == "1h":
        yf_interval = "60m"
        if period not in ("5d", "1mo", "3mo", "6mo"):
            period = "1mo"
    else:  # "10m" → se baja 5m y se reagrupa a 10 minutos
        yf_interval = "5m"
        if period not in ("1d", "5d", "1mo"):
            period = "5d"
    try:
        stock = yf.Ticker(ticker_clean)
        hist = stock.history(period=period, interval=yf_interval)
        if hist.empty:
            raise HTTPException(status_code=404, detail="No data")

        # 10m: reagrupa las velas de 5m a 10 minutos (yfinance no tiene 10m nativo)
        if interval == "10m":
            hist = hist.resample("10min").agg({
                "Open": "first", "High": "max", "Low": "min",
                "Close": "last", "Volume": "sum",
            }).dropna(subset=["Open", "High", "Low", "Close"])
            if hist.empty:
                raise HTTPException(status_code=404, detail="No data")

        precios_hist = [round(float(x), 2) for x in hist['Close'].tolist()]
        fechas_hist  = [x.strftime("%b %d %H:%M" if is_intraday else "%b %d") for x in hist.index.tolist()]

        # OHLC + Volume for candlestick chart (TradingView format)
        # Diario → time = "YYYY-MM-DD" (string) · Intradía → time = epoch segundos (UTC) que pide lightweight-charts
        ohlc_hist    = []
        volumen_hist = []
        for idx, row in hist.iterrows():
            if is_intraday:
                # lightweight-charts pinta el epoch en UTC; sumamos el offset del huso para que el eje muestre la hora de mercado (ET)
                off = idx.utcoffset()
                t = int(idx.timestamp()) + (int(off.total_seconds()) if off else 0)
            else:
                t = idx.strftime("%Y-%m-%d")
            ohlc_hist.append({
                "time":  t,
                "open":  round(float(row['Open']), 2),
                "high":  round(float(row['High']), 2),
                "low":   round(float(row['Low']), 2),
                "close": round(float(row['Close']), 2),
            })
            volumen_hist.append({
                "time":  t,
                "value": int(row['Volume']) if not math.isnan(row['Volume']) else 0,
                "up":    bool(row['Close'] >= row['Open']),
            })

        return {"ticker": ticker_clean, "period": period, "interval": interval,
                "intraday": is_intraday, "precios": precios_hist,
                "fechas": fechas_hist, "ohlc": ohlc_hist, "volumen": volumen_hist}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _ols_beta(y, X):
    """OLS coefficients for y ~ X (X already includes intercept column)."""
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    return beta


def _residualize(y, base):
    """Remove the linear effect of `base` (1D) from `y` (1D); return the residual.
    Used to orthogonalize factors against the market so betas are incremental."""
    X = np.column_stack([np.ones(len(base)), base])
    b = _ols_beta(y, X)
    return y - X @ b


def compute_ticker_vs_portfolio(ticker, holdings, lookback="1y", add_weight=0.05):
    """How a candidate ticker fits the user's CURRENT book: ownership, correlation
    to the book, beta to the book, standalone vol, diversification verdict."""
    import pandas as pd
    eq = [h for h in holdings if h.get("ticker") and float(h.get("value") or 0) > 0]
    if not eq:
        return None
    total = sum(float(h["value"]) for h in eq)
    weights = {h["ticker"]: float(h["value"]) / total for h in eq}
    held = list(weights.keys())
    already = ticker in weights
    cur_w = round(weights.get(ticker, 0.0) * 100, 1)

    syms = list(set(held + [ticker]))
    closes = {}
    for s in syms:
        try:
            h = yf.Ticker(s).history(period=lookback)
            if not h.empty and "Close" in h:
                closes[s] = h["Close"]
        except Exception:
            pass

    base = {"already_held": already, "current_weight_pct": cur_w,
            "book_positions": len(held), "corr_to_book": None, "beta_to_book": None,
            "ticker_vol_pct": None, "most_correlated": None, "diversification": None}

    if ticker not in closes or not any(s in closes for s in held):
        base["note"] = "Datos de precio insuficientes."
        return base

    px = pd.DataFrame(closes).dropna(how="all")
    rets = px.pct_change().dropna(how="all")
    avail = [s for s in held if s in rets.columns]
    if not avail:
        base["note"] = "Sin retornos del book."
        return base

    wv = np.array([weights[t] for t in avail], dtype=float)
    wv = wv / wv.sum()
    Rb = rets[avail].dropna()
    common = Rb.index.intersection(rets[ticker].dropna().index)
    if len(common) < 30:
        base["note"] = "Historial comun insuficiente."
        return base

    book_ret = Rb.loc[common].values @ wv
    tk_ret = rets[ticker].loc[common].values
    ann = math.sqrt(252)
    base["ticker_vol_pct"] = round(float(np.std(tk_ret, ddof=1)) * ann * 100, 1)
    vb = float(np.var(book_ret, ddof=1))
    base["beta_to_book"] = round(float(np.cov(tk_ret, book_ret, ddof=1)[0, 1] / vb), 2) if vb > 0 else None
    corr_book = round(float(np.corrcoef(tk_ret, book_ret)[0, 1]), 2)
    base["corr_to_book"] = corr_book

    most = None
    for s in avail:
        if s == ticker:
            continue
        j = pd.concat([rets[ticker].rename("a"), rets[s].rename("b")], axis=1).dropna()
        if j.shape[0] >= 30:
            c = float(np.corrcoef(j["a"].values, j["b"].values)[0, 1])
            if most is None or c > most["corr"]:
                most = {"ticker": s, "corr": round(c, 2)}
    base["most_correlated"] = most
    base["diversification"] = "alta" if corr_book < 0.4 else "media" if corr_book < 0.7 else "baja"

    # ── #2 FACTOR DECOMPOSITION + MARGINAL CONTRIBUTION TO RISK (Aladdin-style) ──
    # Orthogonalized factor ETFs: market (SPY), AI/semis beyond market (SMH),
    # small-cap beyond market (IWM), rates beyond market (TLT),
    # AI-software beyond market AND beyond semis (IGV) — mide concentración temática de IA honestamente.
    try:
        fetf = {"mercado": "SPY", "ia_semis": "SMH", "small_cap": "IWM", "tasas": "TLT",
                "software_ia": "IGV"}
        fcl = {}
        for k, sym in fetf.items():
            try:
                fh = yf.Ticker(sym).history(period=lookback)
                if not fh.empty and "Close" in fh:
                    fcl[k] = fh["Close"].pct_change()
            except Exception:
                pass
        if len(fcl) >= 2:
            cand_s = pd.Series(tk_ret, index=common)
            book_s = pd.Series(book_ret, index=common)
            alldf = pd.concat([cand_s.rename("cand"), book_s.rename("book"),
                               pd.DataFrame(fcl)], axis=1).dropna()
            order = [k for k in ["mercado", "ia_semis", "small_cap", "tasas", "software_ia"] if k in alldf.columns]
            if len(alldf) >= 40 and order:
                mkt = alldf["mercado"].values if "mercado" in alldf.columns else None
                facmat = {}
                for k in order:
                    v = alldf[k].values
                    facmat[k] = v if (k == "mercado" or mkt is None) else _residualize(v, mkt)
                # software/IA ⊥ semis también (Gram-Schmidt): aísla el riesgo de IA-software
                # que NO explica el mercado ni los semiconductores → evita colinealidad SMH/IGV.
                if "software_ia" in facmat and "ia_semis" in facmat:
                    facmat["software_ia"] = _residualize(facmat["software_ia"], facmat["ia_semis"])
                F = np.column_stack([np.ones(len(alldf))] + [facmat[k] for k in order])
                cand_b = _ols_beta(alldf["cand"].values, F)
                book_b = _ols_beta(alldf["book"].values, F)
                cand_betas = {order[i]: round(float(cand_b[i + 1]), 2) for i in range(len(order))}
                book_betas = {order[i]: round(float(book_b[i + 1]), 2) for i in range(len(order))}
                resid = alldf["cand"].values - F @ cand_b
                ss_res = float(np.sum(resid ** 2))
                yv = alldf["cand"].values
                ss_tot = float(np.sum((yv - yv.mean()) ** 2))
                r2 = round(1 - ss_res / ss_tot, 2) if ss_tot > 0 else None
                a = float(add_weight)
                delta_exp = {k: round(a * (cand_betas[k] - book_betas[k]), 3) for k in order}
                cand_dom = max(order, key=lambda k: abs(cand_betas[k]))
                book_dom = max(order, key=lambda k: abs(book_betas[k]))
                concentrates = (cand_dom == book_dom
                                and cand_betas[cand_dom] * book_betas[book_dom] > 0
                                and abs(book_betas[book_dom]) > 0.3)
                base["factors"] = {
                    "candidate_betas": cand_betas, "book_betas": book_betas,
                    "delta_exposure_at_add": delta_exp, "add_weight_pct": round(a * 100, 1),
                    "candidate_r2": r2, "candidate_dominant": cand_dom,
                    "book_dominant": book_dom, "concentrates_dominant": concentrates,
                    "factor_labels": {"mercado": "Mercado", "ia_semis": "IA/Semis",
                                      "small_cap": "Small-cap", "tasas": "Tasas",
                                      "software_ia": "Software/IA"},
                }
    except Exception:
        pass

    # Marginal contribution to risk: add candidate at weight a, book scaled to (1-a)
    try:
        ann2 = math.sqrt(252)
        sb = float(np.std(book_ret, ddof=1)) * ann2
        sc = float(np.std(tk_ret, ddof=1)) * ann2
        rho = float(corr_book)
        a = float(add_weight)
        snew = math.sqrt(max(((1 - a) ** 2) * sb ** 2 + (a ** 2) * sc ** 2
                             + 2 * a * (1 - a) * rho * sb * sc, 1e-12))
        d_vol = snew - sb
        wavg = (1 - a) * sb + a * sc
        div_ratio = round(snew / wavg, 3) if wavg > 0 else None
        mcr = a * (a * sc ** 2 + (1 - a) * rho * sb * sc) / snew if snew > 0 else 0.0
        mcr_share = round(mcr / snew * 100, 1) if snew > 0 else None
        var_1m_delta = round(1.645 * d_vol / math.sqrt(12) * 100, 2)
        conc_flag = bool((base.get("factors") or {}).get("concentrates_dominant"))
        verdict = ("concentra" if (rho >= 0.8 or conc_flag)
                   else "diversifica" if rho < 0.5 else "moderado")
        base["risk_contribution"] = {
            "book_vol_pct": round(sb * 100, 1), "new_vol_pct": round(snew * 100, 1),
            "delta_vol_pct": round(d_vol * 100, 2), "add_weight_pct": round(a * 100, 1),
            "mcr_share_pct": mcr_share, "diversification_ratio": div_ratio,
            "var_1m_95_delta_pct": var_1m_delta, "verdict": verdict,
        }
    except Exception:
        pass

    return base


def _recompute_risk_contribution(fit, add_weight):
    """#7 — Recalcula la contribución marginal al riesgo (MCR, Δvol, VaR, ratio de diversificación)
    al PESO REALMENTE RECOMENDADO, usando los números ya cacheados en `fit` (no re-descarga historia).
    Antes el MCR se mostraba a un 5% fijo que no correspondía al tamaño Kelly sugerido."""
    try:
        rc = (fit or {}).get("risk_contribution") or {}
        sb = float(rc.get("book_vol_pct") or 0) / 100.0
        sc = float(fit.get("ticker_vol_pct") or 0) / 100.0
        rho = float(fit.get("corr_to_book") if fit.get("corr_to_book") is not None else 0.0)
        a = max(0.0, min(float(add_weight), 1.0))
        if sb <= 0 or sc <= 0 or a <= 0:
            return rc
        snew = math.sqrt(max(((1 - a) ** 2) * sb ** 2 + (a ** 2) * sc ** 2
                             + 2 * a * (1 - a) * rho * sb * sc, 1e-12))
        d_vol = snew - sb
        wavg = (1 - a) * sb + a * sc
        div_ratio = round(snew / wavg, 3) if wavg > 0 else None
        mcr = a * (a * sc ** 2 + (1 - a) * rho * sb * sc) / snew if snew > 0 else 0.0
        mcr_share = round(mcr / snew * 100, 1) if snew > 0 else None
        var_1m_delta = round(1.645 * d_vol / math.sqrt(12) * 100, 2)
        conc = bool((fit.get("factors") or {}).get("concentrates_dominant"))
        verdict = ("concentra" if (rho >= 0.8 or conc) else "diversifica" if rho < 0.5 else "moderado")
        return {"book_vol_pct": round(sb * 100, 1), "new_vol_pct": round(snew * 100, 1),
                "delta_vol_pct": round(d_vol * 100, 2), "add_weight_pct": round(a * 100, 1),
                "mcr_share_pct": mcr_share, "diversification_ratio": div_ratio,
                "var_1m_95_delta_pct": var_1m_delta, "verdict": verdict,
                "at_recommended_weight": True}
    except Exception:
        return (fit or {}).get("risk_contribution") or {}


def format_portfolio_fit(fit):
    """Compact text block on how the ticker fits the user's book, for the AI prompt."""
    if not fit:
        return "N/A (sin portafolio cargado — analiza tu portafolio primero para activar la consciencia de cartera)"
    held = (f"YA lo tienes ({fit['current_weight_pct']}% del book de {fit['book_positions']} posiciones)"
            if fit.get("already_held") else f"NO esta en tu book ({fit['book_positions']} posiciones)")
    if fit.get("note"):
        return f"{held}; {fit['note']}"
    parts = [held]
    if fit.get("corr_to_book") is not None:
        parts.append(f"correlacion con tu portafolio {fit['corr_to_book']}")
    if fit.get("beta_to_book") is not None:
        parts.append(f"beta a tu book {fit['beta_to_book']}")
    if fit.get("ticker_vol_pct") is not None:
        parts.append(f"vol anual {fit['ticker_vol_pct']}%")
    if fit.get("diversification"):
        parts.append(f"diversificacion {fit['diversification']}")
    if fit.get("most_correlated"):
        parts.append(f"mas correlacionado con {fit['most_correlated']['ticker']} ({fit['most_correlated']['corr']})")
    return "; ".join(parts)


def format_factor_risk(fit):
    """Compact factor-exposure + marginal-risk block for the AI prompt (#2)."""
    if not fit:
        return "N/A"
    f = fit.get("factors")
    rc = fit.get("risk_contribution")
    if not f and not rc:
        return "N/A (historial insuficiente para descomposicion por factores)"
    out = []
    if f:
        lab = f.get("factor_labels", {})
        cb = f.get("candidate_betas", {})
        out.append("betas de factor del candidato [" +
                   ", ".join(f"{lab.get(k, k)} {v:+.2f}" for k, v in cb.items()) +
                   f"] (R2 {f.get('candidate_r2')})")
        out.append(f"factor dominante del candidato: {lab.get(f['candidate_dominant'], f['candidate_dominant'])}; "
                   f"de tu book: {lab.get(f['book_dominant'], f['book_dominant'])}")
        if f.get("concentrates_dominant"):
            out.append(f"** CONCENTRA tu factor ya dominante ({lab.get(f['book_dominant'], f['book_dominant'])}) — poca diversificacion **")
        de = f.get("delta_exposure_at_add", {})
        big = sorted(de.items(), key=lambda x: abs(x[1]), reverse=True)[:2]
        if big:
            out.append(f"mayor cambio de exposicion al anadir {f.get('add_weight_pct')}%: " +
                       ", ".join(f"{lab.get(k, k)} {v:+.3f}" for k, v in big))
    if rc:
        out.append(f"al anadir {rc['add_weight_pct']}%: vol del book {rc['book_vol_pct']}%->{rc['new_vol_pct']}% "
                   f"(D {rc['delta_vol_pct']:+}%), VaR-1m-95 D {rc['var_1m_95_delta_pct']:+}%, "
                   f"contribucion marginal al riesgo {rc.get('mcr_share_pct')}%, veredicto {rc.get('verdict')}")
    return "; ".join(out)


# ── OPTIONS INTELLIGENCE: GEX / gamma walls / max pain / positioning ──────────
# Computed FREE from the yfinance option chain (OI + IV per strike) + Black-Scholes
# gamma. No paid feed required. Modular by design: the returned dict carries empty
# slots for dark_pool / tape_flow so Unusual Whales can fill them later without
# changing any caller. (Real-time not needed — chain data is fine for GEX/levels.)
_GEX_CACHE = {}

def _safe_num(v, default=0.0):
    """float() that maps None / NaN / inf / bad values to a default (yfinance often
    returns NaN for missing OI/volume/IV, and 'nan or 0' is NaN since NaN is truthy)."""
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except (TypeError, ValueError):
        return default

def _json_safe(obj):
    """Recursively replace NaN/inf floats with None so json.dumps never 500s."""
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj

def _gamma_flip(rows, spot):
    """Spot price where net dealer gamma (recomputed across a grid) crosses zero."""
    if not rows:
        return None
    lo, hi, n = spot * 0.80, spot * 1.20, 41
    prev_tot = prev_S = None
    for i in range(n):
        S = lo + (hi - lo) * i / (n - 1)
        tot = 0.0
        for x in rows:
            sign = 1.0 if x["typ"] == "call" else -1.0
            g = _bs_greeks(S, x["K"], x["T"], x["iv"], 0.043, x["typ"])["gamma"]
            tot += g * x["oi"] * 100.0 * S * S * 0.01 * sign
        if prev_tot is not None and prev_tot * tot < 0 and (tot - prev_tot) != 0:
            return prev_S + (S - prev_S) * (0 - prev_tot) / (tot - prev_tot)
        prev_tot, prev_S = tot, S
    return None

def _max_pain(call_oi, put_oi):
    """Strike that minimizes total intrinsic payout to option holders (OI-weighted)."""
    strikes = sorted(set(list(call_oi.keys()) + list(put_oi.keys())))
    if not strikes:
        return None
    best, best_pay = None, None
    for Kx in strikes:
        pay = sum((Kx - Kc) * oi for Kc, oi in call_oi.items() if Kx > Kc)
        pay += sum((Kp - Kx) * oi for Kp, oi in put_oi.items() if Kx < Kp)
        if best_pay is None or pay < best_pay:
            best_pay, best = pay, Kx
    return best

def compute_gex(ticker, max_expiries=4, max_dte=60):
    """Dealer gamma exposure + key levels from the option chain (free, model-derived).
    Returns net GEX, gamma flip, call/put walls, max pain, P/C ratios and unusual-volume
    strikes (volume > OI = fresh positioning). Calls +, puts - (SqueezeMetrics-style)."""
    try:
        tk = yf.Ticker(ticker)
        h = tk.history(period="5d")
        if h.empty:
            return None
        spot = _safe_num(h["Close"].iloc[-1], 0.0)
        exps = list(tk.options or [])
    except Exception:
        return None
    if spot <= 0 or not exps:
        return None
    now = datetime.now()
    rows, used_exp = [], []
    for exp in exps:
        try:
            dte = (datetime.strptime(exp, "%Y-%m-%d") - now).days
        except Exception:
            continue
        if dte < 0 or dte > max_dte:
            continue
        if len(used_exp) >= max_expiries:
            break
        try:
            ch = tk.option_chain(exp)
        except Exception:
            continue
        T = max(dte / 365.0, 1.0 / 365.0)
        for df, typ in ((ch.calls, "call"), (ch.puts, "put")):
            for _, r in df.iterrows():
                try:
                    K = _safe_num(r["strike"]); oi = _safe_num(r.get("openInterest"))
                    vol = _safe_num(r.get("volume")); iv = _safe_num(r.get("impliedVolatility"))
                except Exception:
                    continue
                if K <= 0 or iv <= 0:
                    continue
                gm = _bs_greeks(spot, K, T, iv, 0.043, typ)["gamma"]
                rows.append({"exp": exp, "typ": typ, "K": K, "oi": oi, "vol": vol, "iv": iv, "T": T, "gamma": gm})
        used_exp.append(exp)
    if not rows:
        return None
    strike_gex, call_oi_by_K, put_oi_by_K = {}, {}, {}
    tot_call_oi = tot_put_oi = tot_call_vol = tot_put_vol = 0.0
    for x in rows:
        sign = 1.0 if x["typ"] == "call" else -1.0
        g = x["gamma"] * x["oi"] * 100.0 * spot * spot * 0.01 * sign
        strike_gex[x["K"]] = strike_gex.get(x["K"], 0.0) + g
        if x["typ"] == "call":
            call_oi_by_K[x["K"]] = call_oi_by_K.get(x["K"], 0.0) + x["oi"]
            tot_call_oi += x["oi"]; tot_call_vol += x["vol"]
        else:
            put_oi_by_K[x["K"]] = put_oi_by_K.get(x["K"], 0.0) + x["oi"]
            tot_put_oi += x["oi"]; tot_put_vol += x["vol"]
    net_gex = sum(strike_gex.values())
    calls_above = {k: v for k, v in call_oi_by_K.items() if k >= spot}
    puts_below = {k: v for k, v in put_oi_by_K.items() if k <= spot}
    call_wall = max(calls_above, key=calls_above.get) if calls_above else (max(call_oi_by_K, key=call_oi_by_K.get) if call_oi_by_K else None)
    put_wall = max(puts_below, key=puts_below.get) if puts_below else (max(put_oi_by_K, key=put_oi_by_K.get) if put_oi_by_K else None)
    flip = _gamma_flip(rows, spot)
    max_pain, max_pain_src = _max_pain_best(ticker, None, call_oi_by_K, put_oi_by_K)   # QD nativo → yfinance
    unusual = []
    for x in rows:
        if x["vol"] > max(x["oi"], 50) and x["vol"] >= 200:
            unusual.append({"strike": x["K"], "type": x["typ"], "exp": x["exp"],
                            "volume": int(x["vol"]), "oi": int(x["oi"]),
                            "vol_oi": round(x["vol"] / max(x["oi"], 1), 1)})
    unusual = sorted(unusual, key=lambda u: u["volume"], reverse=True)[:8]
    # Laddered resistance (call OI clusters above spot) and support (put OI clusters below)
    resistances = sorted(
        [{"strike": round(k, 2), "oi": int(v)} for k, v in call_oi_by_K.items() if k >= spot and v > 0],
        key=lambda x: x["oi"], reverse=True)[:4]
    resistances = sorted(resistances, key=lambda x: x["strike"])          # nearest-above first
    supports = sorted(
        [{"strike": round(k, 2), "oi": int(v)} for k, v in put_oi_by_K.items() if k <= spot and v > 0],
        key=lambda x: x["oi"], reverse=True)[:4]
    supports = sorted(supports, key=lambda x: x["strike"], reverse=True)   # nearest-below first
    dte_dominant = None
    if used_exp:
        try:
            dte_dominant = max((datetime.strptime(used_exp[0], "%Y-%m-%d") - now).days, 0)
        except Exception:
            dte_dominant = None
    return _json_safe({
        "ok": True, "ticker": ticker.upper(), "spot": round(spot, 2), "expiries_used": used_exp,
        "dte_dominant": dte_dominant,
        "net_gex": round(net_gex, 0),
        "net_gex_regime": ("positivo (precio anclado / mean-revert)" if net_gex >= 0
                           else "negativo (movimientos amplificados / tendencia)"),
        "gamma_flip": round(flip, 2) if flip else None,
        "call_wall": call_wall, "put_wall": put_wall, "max_pain": max_pain, "max_pain_source": max_pain_src,
        "resistances": resistances, "supports": supports,
        "pcr_oi": round(tot_put_oi / tot_call_oi, 2) if tot_call_oi else None,
        "pcr_vol": round(tot_put_vol / tot_call_vol, 2) if tot_call_vol else None,
        "total_call_oi": int(tot_call_oi), "total_put_oi": int(tot_put_oi),
        "strike_gex": {round(k, 2): round(v, 0) for k, v in sorted(strike_gex.items())},
        "unusual_activity": unusual,
        "dark_pool": None, "tape_flow": None,   # ← Unusual Whales fills these later
        "source": "computed (yfinance chain + BSM)",
        "generated_at": now.strftime('%m/%d/%Y, %I:%M:%S %p')})

def _gex_from_quantdata(ticker):
    """GEX 100% Quant Data: net GEX, gamma flip, call/put walls, max pain, perfil por strike y
    resistencias/soportes desde la exposición GAMMA de QD; Put/Call por PRIMA (net-flow) y por VOLUMEN +
    actividad inusual (golden/unusual/opening sweeps) desde el order-flow QD; spot de QD/Finnhub.
    pcr_oi/total OI quedan None (QD no expone el OI de cadena completa). Si QD no responde, get_gex_cached
    cae a yfinance+BSM. MISMA forma que compute_gex."""
    try:
        exp = quantdata_exposure(ticker, "GAMMA")
    except Exception:
        exp = None
    if not exp or not exp.get("by_strike"):
        return None
    rows = exp["by_strike"]
    spot = _safe_num(exp.get("stock_price")) or _safe_num(_live_spot(ticker))
    if spot <= 0:
        return None
    net_gex = sum(_safe_num(r.get("net")) for r in rows)
    walls = _qd_gex_walls(exp, spot) or {}
    flip = walls.get("gamma_flip")
    try:
        mp = quantdata_max_pain(ticker)
    except Exception:
        mp = None
    above = sorted([r for r in rows if _safe_num(r.get("strike")) >= spot and _safe_num(r.get("call")) > 0],
                   key=lambda r: _safe_num(r.get("call")), reverse=True)[:4]
    below = sorted([r for r in rows if _safe_num(r.get("strike")) <= spot and abs(_safe_num(r.get("put"))) > 0],
                   key=lambda r: abs(_safe_num(r.get("put"))), reverse=True)[:4]
    resistances = sorted([{"strike": round(_safe_num(r["strike"]), 2), "oi": None} for r in above],
                         key=lambda x: x["strike"])
    supports = sorted([{"strike": round(_safe_num(r["strike"]), 2), "oi": None} for r in below],
                      key=lambda x: x["strike"], reverse=True)
    now = datetime.now()
    # ── Put/Call + actividad inusual NATIVOS de Quant Data (llenan el bloque sin tocar yfinance) ──
    # P/C por PRIMA (dónde está el dinero) desde net-flow; P/C por VOLUMEN y la actividad inusual
    # (golden/unusual/opening sweeps, más rico que el vol>OI de yfinance) desde el order-flow reciente.
    pcr_premium = pcr_vol = None
    call_prem = put_prem = None
    try:
        nf = quantdata_net_flow(ticker, "today")
        if nf:
            call_prem = _safe_num(nf.get("call_total")); put_prem = _safe_num(nf.get("put_total"))
            if call_prem > 0:
                pcr_premium = round(put_prem / call_prem, 2)
    except Exception:
        pass
    unusual = []
    try:
        fw = quantdata_flow_window(ticker, days=3, min_premium=250_000, max_rows=150)
        cvol = pvol = 0.0
        for t in fw:
            cp = str(t.get("cp") or "").upper()
            sz = _safe_num(t.get("size"))
            if cp.startswith("C"):
                cvol += sz
            elif cp.startswith("P"):
                pvol += sz
            if t.get("golden") or t.get("unusual") or t.get("opening"):
                oi = int(_safe_num(t.get("oi")))
                vol = int(sz)
                unusual.append({"strike": t.get("strike"),
                                "type": ("call" if cp.startswith("C") else "put"),
                                "exp": t.get("exp"), "volume": vol, "oi": oi,
                                "vol_oi": round(vol / max(oi, 1), 1),
                                "golden": bool(t.get("golden")), "unusual": bool(t.get("unusual")),
                                "premium": _safe_num(t.get("premium"))})
        if cvol > 0:
            pcr_vol = round(pvol / cvol, 2)
        seen, dedup = set(), []
        for u in sorted(unusual, key=lambda x: (x.get("premium") or 0), reverse=True):
            k = (u["type"], u["strike"], u["exp"])
            if k in seen:
                continue
            seen.add(k); dedup.append(u)
        unusual = dedup[:8]
    except Exception:
        unusual = []
    return _json_safe({
        "ok": True, "ticker": ticker.upper(), "spot": round(spot, 2),
        "expiries_used": (exp.get("expirations") or [])[:4], "dte_dominant": None,
        "net_gex": round(net_gex, 0),
        "net_gex_regime": ("positivo (precio anclado / mean-revert)" if net_gex >= 0
                           else "negativo (movimientos amplificados / tendencia)"),
        "gamma_flip": round(flip, 2) if flip else None,
        "call_wall": walls.get("call_wall"), "put_wall": walls.get("put_wall"),
        "max_pain": mp, "max_pain_source": "quantdata" if mp else None,
        "resistances": resistances, "supports": supports,
        "pcr_oi": None, "pcr_vol": pcr_vol, "pcr_premium": pcr_premium,
        "net_premium_call": round(call_prem, 0) if call_prem is not None else None,
        "net_premium_put": round(put_prem, 0) if put_prem is not None else None,
        "total_call_oi": None, "total_put_oi": None,
        "strike_gex": {round(_safe_num(r["strike"]), 2): round(_safe_num(r.get("net")), 0) for r in rows},
        "unusual_activity": unusual,
        "dark_pool": None, "tape_flow": None,
        "source": "Quant Data (primario)",
        "generated_at": now.strftime('%m/%d/%Y, %I:%M:%S %p')})


def _et_now():
    """Hora actual en ET (America/New_York), con respaldo a EDT aproximado si no hay zoneinfo."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        return datetime.utcnow() - timedelta(hours=4)


def _gex_ttl(base_ttl=300):
    """TTL adaptativo del GEX: en los últimos 90 min de sesión (lun-vie) acorta a 45s para que el GEX 0DTE
    se refresque más rápido cerca del cierre, cuando el gamma 0DTE manda el pin y se mueve rápido."""
    try:
        et = _et_now()
        if et.weekday() < 5:
            mins = (16 - et.hour) * 60 - et.minute      # minutos hasta el cierre 16:00 ET
            if 0 < mins <= 90:
                return min(base_ttl, 45)
    except Exception:
        pass
    return base_ttl


def get_gex_cached(ticker, ttl=300, force=False):
    key = ticker.upper(); now = time.time()
    ttl = _gex_ttl(ttl)                       # 0DTE: refresco más rápido cerca del cierre
    ent = _GEX_CACHE.get(key)
    if ent and not force and now - ent[0] < ttl:
        return ent[1]
    # QuantData PRIMARIO (consistente con walls / max pain / flujo / gráfico de gamma, todos QD).
    try:
        val = _gex_from_quantdata(ticker)
    except Exception:
        val = None
    if val is None:                       # QD no respondió → respaldo yfinance + Black-Scholes
        try:
            val = compute_gex(ticker)
        except Exception:
            val = None
    _GEX_CACHE[key] = (now, val)
    return val

def format_options_intel(gex):
    """Compact GEX/levels block for the AI analyze prompt."""
    if not gex or not gex.get("ok"):
        return "N/A (cadena de opciones no disponible)"
    parts = [
        f"GEX neto {gex['net_gex']:,.0f} ({gex['net_gex_regime']})",
        (f"gamma flip ~${gex['gamma_flip']}" if gex.get("gamma_flip") else None),
        (f"call wall ${gex['call_wall']} (resistencia/iman arriba)" if gex.get("call_wall") else None),
        (f"put wall ${gex['put_wall']} (soporte abajo)" if gex.get("put_wall") else None),
        (f"max pain ${gex['max_pain']}" if gex.get("max_pain") else None),
        (f"P/C prima {gex['pcr_premium']}" if gex.get("pcr_premium") is not None else None),
        (f"P/C vol {gex['pcr_vol']}" if gex.get("pcr_vol") is not None else None),
        (f"P/C OI {gex['pcr_oi']}" if gex.get("pcr_oi") is not None else None),
    ]
    ua = gex.get("unusual_activity") or []
    if ua:
        parts.append("actividad inusual (flujo QD): " + ", ".join(
            f"{'⭐' if u.get('golden') else ''}{u['type']} ${u['strike']} {str(u['exp'])[5:]} vol {u['volume']} ({u['vol_oi']}x OI)" for u in ua[:3]))
    return "; ".join(p for p in parts if p)


# ── DIRECTIONAL TARGETS BY HORIZON (7/30/60/90/120d + 12m) ────────────────────
# Short horizons driven by options positioning (per-expiry GEX walls + flow bias +
# delta conviction); 12m is fundamental (the agent's DCF target, passed in).
def _nearest_expiry(exps, target_dte, now):
    best, bestdiff = None, 1e9
    for e in exps:
        try:
            dte = (datetime.strptime(e, "%Y-%m-%d") - now).days
        except Exception:
            continue
        if dte < 0:
            continue
        diff = abs(dte - target_dte)
        if diff < bestdiff:
            bestdiff, best = diff, (e, dte)
    return best

def _walls_for_expiry(tk, exp, spot):
    try:
        ch = tk.option_chain(exp)
    except Exception:
        return None
    call_oi, put_oi = {}, {}
    for df, typ in ((ch.calls, "call"), (ch.puts, "put")):
        for _, r in df.iterrows():
            K = _safe_num(r["strike"]); oi = _safe_num(r.get("openInterest"))
            if K <= 0 or oi <= 0:
                continue
            d = call_oi if typ == "call" else put_oi
            d[K] = d.get(K, 0.0) + oi
    ca = {k: v for k, v in call_oi.items() if k >= spot}
    pb = {k: v for k, v in put_oi.items() if k <= spot}
    cw = max(ca, key=ca.get) if ca else (max(call_oi, key=call_oi.get) if call_oi else None)
    pw = max(pb, key=pb.get) if pb else (max(put_oi, key=put_oi.get) if put_oi else None)
    return {"call_wall": cw, "put_wall": pw, "max_pain": _max_pain(call_oi, put_oi)}

def _classify_flow_delta(flow, spot, iv_hint=0.4):
    """% of flow premium in directional-conviction deltas (0.60–0.90) vs speculative
    (0.30–0.50). Uses the trade's delta if present, else computes it via Black-Scholes."""
    if not flow:
        return None
    now = datetime.now()
    dir_prem = spec_prem = 0.0
    for t in flow:
        try:
            prem = abs(_safe_num(t.get("premium")))
            if prem <= 0:
                continue
            dlt = t.get("delta")
            if dlt is None:
                K = _safe_num(t.get("strike"))
                cp = str(t.get("cp") or "").lower()
                cp = "call" if cp.startswith("c") else "put"
                try:
                    T = max((datetime.strptime(str(t.get("exp"))[:10], "%Y-%m-%d") - now).days / 365.0, 1/365.0)
                except Exception:
                    T = 30/365.0
                if K <= 0 or spot <= 0:
                    continue
                dlt = _bs_greeks(spot, K, T, iv_hint, 0.043, cp)["delta"]
            ad = abs(_safe_num(dlt))
            if 0.60 <= ad <= 0.90:
                dir_prem += prem
            elif 0.30 <= ad < 0.60:
                spec_prem += prem
        except Exception:
            continue
    tot = dir_prem + spec_prem
    return round(dir_prem / tot * 100, 0) if tot > 0 else None

def _qd_conviction(flow, oi_change_map=None):
    """Kevin's institutional-conviction filter on the Quant Data tape. Counts ONLY trades whose
    contract's OPEN INTEREST ACTUALLY GREW that session (real day-over-day ΔOI > 0 from the
    open-interest-change endpoint = position was added/accumulated — catches the multi-day builds
    that vol>OI misses, e.g. +30k on top of an existing 70k) AND are aggressive buys (side contains
    ASK), premium tiered by DTE: $1M (<=10d) · $5M (11-45d) · $10M (>45d). CALL=bullish, PUT=bearish;
    delta 0.60-0.90 = direccional, 0.30-0.59 = especulativo. If oi_change_map is None (endpoint
    unavailable) it falls back to the same-day vol>OI 'opening' proxy."""
    if not flow:
        return None
    now = datetime.now()
    use_oi = isinstance(oi_change_map, dict) and len(oi_change_map) > 0
    bull = bear = 0.0
    strong = []
    for t in flow:
        try:
            prem = abs(_safe_num(t.get("premium")))
            if prem <= 0:
                continue
            cp = str(t.get("cp") or "").upper()
            strike = _safe_num(t.get("strike"))
            exp = str(t.get("exp") or "")[:10]
            # --- real "added to open interest" gate (ΔOI day-after vs day-of) ---
            oi_chg = None
            if use_oi:
                cp_full = "CALL" if cp.startswith("C") else ("PUT" if cp.startswith("P") else cp)
                ent = oi_change_map.get(f"{cp_full}|{round(strike, 2)}|{exp}")
                if not ent or _safe_num(ent.get("change")) <= 0:
                    continue                          # OI did NOT grow → not an addition → ignore
                oi_chg = int(_safe_num(ent.get("change")))
            else:
                if not t.get("opening"):              # fallback: same-day vol>OI proxy
                    continue
            side = str(t.get("side") or "").upper()
            if "ASK" not in side:                 # only aggressive buys (ASK / ABOVE_ASK)
                continue
            dte = _safe_num(t.get("dte"))
            if dte <= 0:
                try:
                    dte = max((datetime.strptime(exp, "%Y-%m-%d") - now).days, 0)
                except Exception:
                    dte = 30
            thr = 1e6 if dte <= 10 else (5e6 if dte <= 45 else 10e6)
            if prem < thr:
                continue
            dlt = abs(_safe_num(t.get("delta")))
            if cp.startswith("C"):
                bull += prem
            elif cp.startswith("P"):
                bear += prem
            strong.append({
                "cp": "CALL" if cp.startswith("C") else ("PUT" if cp.startswith("P") else "?"),
                "strike": strike or None, "exp": t.get("exp"),
                "dte": int(dte), "premium": round(prem, 0), "side": side,
                "kind": t.get("kind"), "delta": round(dlt, 2) if dlt else None,
                "vol_oi": t.get("vol_oi"), "oi_change": oi_chg,
                "type": "direccional" if (0.60 <= dlt <= 0.90) else "especulativo",
            })
        except Exception:
            continue
    if not strong:
        return None
    tot = bull + bear
    strong.sort(key=lambda x: x["premium"], reverse=True)
    return {"bias": "alcista" if bull > bear else ("bajista" if bear > bull else "neutral"),
            "bull_premium": round(bull, 0), "bear_premium": round(bear, 0),
            "strength_pct": round(max(bull, bear) / tot * 100, 0) if tot > 0 else None,
            "qualifying": len(strong), "strong_trades": strong[:15],
            "oi_confirmed": use_oi}


def _flow_anchor_score(conv):
    """Convierte la convicción institucional COMPUTADA (dominancia + sesgo + nº de confirmaciones) en
    un score alcista 0-100 para anclar la señal de flujo (25%), en vez de dejar que el LLM la adivine.
    alcista→strength_pct · bajista→100-strength_pct · neutral→50; con pocas confirmaciones tira hacia 50."""
    if not conv or conv.get("strength_pct") is None:
        return None
    sp = float(conv["strength_pct"])
    bias = conv.get("bias")
    raw = sp if bias == "alcista" else ((100.0 - sp) if bias == "bajista" else 50.0)
    w = min(int(conv.get("qualifying") or 0) / 3.0, 1.0)   # 1 trade → medio jala; 3+ → jala completo
    return 50.0 + (raw - 50.0) * w


def _qd_conviction_prompt_block(conv):
    """Render the OI-confirmed conviction dict as a Spanish prompt block for the agent's reasoning.
    Empty string when there's no qualifying conviction so the prompt stays clean."""
    if not conv or not isinstance(conv, dict) or not conv.get("qualifying"):
        return ""
    bias = str(conv.get("bias", "neutral"))
    strg = conv.get("strength_pct")
    bull = _safe_num(conv.get("bull_premium"))
    bear = _safe_num(conv.get("bear_premium"))
    metodo = ("confirmados por crecimiento REAL del open interest (ΔOI>0, comparando OI día-después vs "
              "día-de la transacción — no un proxy)" if conv.get("oi_confirmed")
              else "marcados como apertura por el proxy vol>OI del mismo día")
    lines = []
    for t in (conv.get("strong_trades") or [])[:8]:
        doi = t.get("oi_change")
        doi_txt = (f"ΔOI +{int(_safe_num(doi)):,} contratos" if doi is not None else "abre OI")
        lines.append(
            f"  - {t.get('cp')} ${t.get('strike')} vence {str(t.get('exp'))[:10]} ({t.get('dte')}d): "
            f"premium ${_safe_num(t.get('premium')):,.0f}, {doi_txt}, "
            f"delta {t.get('delta')} ({t.get('type')}){(', ' + str(t.get('kind'))) if t.get('kind') else ''}")
    trades_txt = "\n".join(lines)
    return (
        f"\nCONVICCIÓN INSTITUCIONAL CONFIRMADA POR OPEN INTEREST (Quant Data — la evidencia de flujo de MÁS "
        f"alta calidad que tienes): solo compras agresivas (ASK/above-ask) {metodo}, con premium por plazo "
        f"$1M≤10d / $5M≤45d / $10M>45d. Sesgo de convicción: {bias.upper()}"
        f"{(' · fuerza ' + str(int(strg)) + '%') if strg is not None else ''} "
        f"· {conv.get('qualifying')} trades calificados. Premium alcista (calls que abren OI) ${bull:,.0f} "
        f"vs bajista (puts que abren OI) ${bear:,.0f}.\nTrades de mayor convicción:\n{trades_txt}\n"
        f"INTERPRETACIÓN: son institucionales ABRIENDO posición nueva con dinero real (el open interest creció), "
        f"no cerrando ni rolando. Por eso pesa más que el premium neto suelto. Si este sesgo COINCIDE con tu tesis, "
        f"sube la puntuación de 'flujo institucional' (señal 25%) y CITA los contratos concretos "
        f"(strike/vencimiento/ΔOI/delta) en tu tesis. Si CONTRADICE tu tesis fundamental, NO lo ignores: "
        f"el smart money podría anticipar un catalizador — explica el conflicto en 'tesis_riesgos' y modera tu convicción.")


def _qd_confluence(conv, gex, darkpool, spot=None, dp_flow=None):
    """Confluence engine: do Kevin's three Quant Data pillars agree?
      1) Convicción (tape ΔOI)              — señal líder, peso 0.50
      2) GEX / posicionamiento de dealers   — walls + gamma flip, peso 0.30
      3) Dark pool (notional soporte/resist)— peso 0.20
    Returns verdict (confirmacion/divergencia/mixto/parcial/posicionamiento/neutral), a badge,
    a -1..1 score, per-signal votes (+1 alcista / -1 bajista / 0 neutral) and a summary.
    HONEST: dark-pool prints are sideless, so its vote is a POSITIONAL lean (dónde están los
    bloques vs el precio), not a buy/sell read."""
    spot = _safe_num(spot) or (_safe_num(gex.get("spot")) if isinstance(gex, dict) else 0)
    def _lab(v):
        return "alcista" if v > 0 else ("bajista" if v < 0 else "neutral")

    # 1) Convicción (tape ΔOI) — la señal líder
    va, da = 0, "sin trades de convicción"
    if isinstance(conv, dict) and conv.get("qualifying"):
        b = str(conv.get("bias"))
        va = 1 if b == "alcista" else (-1 if b == "bajista" else 0)
        s = conv.get("strength_pct")
        da = f"convicción {b}{(' ' + str(int(s)) + '%') if s is not None else ''} · {conv.get('qualifying')} trades ΔOI+"

    # 2) GEX / posicionamiento (walls + gamma flip)
    vb, db = 0, "GEX no disponible"
    if isinstance(gex, dict) and gex.get("ok"):
        cw, pw = _safe_num(gex.get("call_wall")), _safe_num(gex.get("put_wall"))
        flipn = _safe_num(gex.get("gamma_flip"))
        if cw and spot and spot > cw * 1.001:
            vb, db = 1, f"spot ${round(spot,2)} rompió el call wall ${cw} (resistencia superada)"
        elif pw and spot and spot < pw * 0.999:
            vb, db = -1, f"spot ${round(spot,2)} bajo el put wall ${pw} (soporte roto)"
        elif flipn and spot:
            if spot >= flipn:
                vb, db = 1, f"sobre el gamma flip ${flipn} (GEX+, los dips se soportan)"
            else:
                vb, db = -1, f"bajo el gamma flip ${flipn} (GEX−, movimientos amplificados)"
        else:
            vb, db = 0, "GEX sin gamma flip ni ruptura de wall (neutral)"

    # 3) Dark pool (posicional, sideless): notional en soporte vs resistencia
    vc, dc = 0, "sin niveles dark pool"
    # Preferimos el flujo DIRECCIONAL de prints (compra ASK vs venta BID) cuando está disponible;
    # si no, caemos al posicional (soporte/resistencia por notional).
    if isinstance(dp_flow, dict) and dp_flow.get("total_notional"):
        bN, sN = _safe_num(dp_flow.get("buy_notional")), _safe_num(dp_flow.get("sell_notional"))
        bM, sM2 = bN / 1e6, sN / 1e6
        if bN > sN * 1.15:
            vc, dc = 1, f"prints dark COMPRA (${bM:,.0f}M ask vs ${sM2:,.0f}M bid)"
        elif sN > bN * 1.15:
            vc, dc = -1, f"prints dark VENTA (${sM2:,.0f}M bid vs ${bM:,.0f}M ask)"
        else:
            vc, dc = 0, f"prints dark equilibrados (${bM:,.0f}M compra / ${sM2:,.0f}M venta)"
    elif isinstance(darkpool, list) and darkpool and spot:
        supp = sum(_safe_num(x.get("value")) for x in darkpool
                   if _safe_num(x.get("price")) and _safe_num(x.get("price")) < spot)
        resist = sum(_safe_num(x.get("value")) for x in darkpool
                     if _safe_num(x.get("price")) > spot)
        sM, rM = supp / 1e6, resist / 1e6
        if supp > resist * 1.2:
            vc, dc = 1, f"bloques en SOPORTE (${sM:,.0f}M abajo vs ${rM:,.0f}M arriba)"
        elif resist > supp * 1.2:
            vc, dc = -1, f"bloques en RESISTENCIA (${rM:,.0f}M arriba vs ${sM:,.0f}M abajo)"
        else:
            vc, dc = 0, f"dark pool equilibrado (${sM:,.0f}M soporte / ${rM:,.0f}M resistencia)"

    score = round(0.50 * va + 0.30 * vb + 0.20 * vc, 2)
    others = [vb, vc]
    agree = sum(1 for o in others if o != 0 and o == va) if va != 0 else 0
    oppose = sum(1 for o in others if o != 0 and o == -va) if va != 0 else 0

    if va != 0:
        direction = _lab(va)
        if oppose == 0 and agree >= 1:
            verdict = "confirmacion"
            badge = "ALTA CONVICCIÓN" if agree == 2 else "CONFIRMACIÓN"
        elif oppose >= 1 and agree == 0:
            verdict, badge = "divergencia", "DIVERGENCIA"
        elif oppose >= 1 and agree >= 1:
            verdict, badge = "mixto", "MIXTO"
        else:
            verdict, badge = "parcial", "PARCIAL"
    else:
        pos = vb + vc
        direction = _lab(1 if pos > 0 else (-1 if pos < 0 else 0))
        if pos != 0 and vb != 0 and vc != 0 and vb == vc:
            verdict, badge = "posicionamiento", "SOLO POSICIONAMIENTO"
        else:
            verdict, badge = "neutral", "SIN SEÑAL"

    sig = {"conviccion": {"vote": va, "label": _lab(va), "detail": da},
           "gex":        {"vote": vb, "label": _lab(vb), "detail": db},
           "darkpool":   {"vote": vc, "label": _lab(vc), "detail": dc}}

    if verdict == "confirmacion":
        summary = (f"Confluencia {direction.upper()}: las señales se confirman entre sí "
                   f"({'las 3 alineadas' if agree == 2 else 'convicción + 1 confirmación'}). Setup de mayor probabilidad.")
    elif verdict == "divergencia":
        summary = (f"DIVERGENCIA: la convicción de tape es {sig['conviccion']['label']} pero el posicionamiento la "
                   f"contradice. El smart money y los dealers/bloques no coinciden — reduce tamaño y espera confirmación.")
    elif verdict == "mixto":
        summary = (f"Señales mixtas sobre una convicción {sig['conviccion']['label']}: una confirma y otra contradice. "
                   f"Sesgo {direction} sin consenso.")
    elif verdict == "parcial":
        summary = (f"Solo hay convicción de tape ({sig['conviccion']['label']}); GEX y dark pool neutrales. "
                   f"Direccional sin confirmación de posicionamiento.")
    elif verdict == "posicionamiento":
        summary = f"Sin convicción de tape, pero el posicionamiento (GEX + dark pool) inclina {direction}."
    else:
        summary = "Sin señal clara de confluencia: los pilares no coinciden o faltan datos."

    return {"ok": True, "direction": direction, "verdict": verdict, "badge": badge,
            "score": score, "agree": agree, "oppose": oppose, "signals": sig, "summary": summary}


def _qd_confluence_prompt_block(confl):
    """Render the confluence verdict as a Spanish prompt block for the agent's reasoning."""
    if not confl or not isinstance(confl, dict) or not confl.get("ok"):
        return ""
    s = confl["signals"]
    return (
        f"\nCONFLUENCIA DE SEÑALES (motor Vertex — ¿coinciden tus 3 pilares de Quant Data?): "
        f"veredicto {confl['badge']} · dirección {confl['direction'].upper()} · score {confl['score']:+.2f}. "
        f"(1) Convicción tape: {s['conviccion']['detail']}. (2) GEX: {s['gex']['detail']}. "
        f"(3) Dark pool: {s['darkpool']['detail']}. {confl['summary']} "
        f"USO: cuando los 3 pilares se CONFIRMAN, sube tu convicción y tu probabilidad calibrada y dilo en la tesis; "
        f"cuando hay DIVERGENCIA, BÁJALAS y explica el conflicto en 'tesis_riesgos' (tape institucional vs "
        f"posicionamiento de dealers/bloques en desacuerdo suele preceder volatilidad o un head-fake).")


def _confluence_conviction_adj(composite, confl, recommendation):
    """#5: la CONFLUENCIA formal (convicción + GEX + dark pool) ajusta la CONVICCIÓN final del agente, no
    solo el prompt. Confirmación ALINEADA con la recomendación → boost modesto (×1.03, ×1.06 si las 3
    coinciden); divergencia (pilares se contradicen) o confirmación en dirección OPUESTA → haircut. Tope
    [0,100]. Devuelve (conviccion_ajustada, info, coherence_flag|None)."""
    if not isinstance(confl, dict) or composite is None:
        return composite, None, None
    verdict = confl.get("verdict"); cdir = confl.get("direction")
    rec = (recommendation or "").lower()
    adir = ("alcista" if ("compra" in rec or "buy" in rec)
            else ("bajista" if ("venta" in rec or "sell" in rec) else "neutral"))
    factor, reason, flag = 1.0, None, None
    aligned = (cdir in ("alcista", "bajista") and cdir == adir)
    opposed = (cdir in ("alcista", "bajista") and adir in ("alcista", "bajista") and cdir != adir)
    if verdict == "confirmacion":
        if aligned:
            factor = 1.06 if confl.get("badge") == "ALTA CONVICCIÓN" else 1.03
            reason = f"confluencia confirma {cdir} (alineada con la recomendación)"
        elif opposed:
            factor = 0.88
            reason = f"confluencia confirma {cdir}, OPUESTA a la recomendación ({adir})"
            flag = {"type": "confluencia_opuesta",
                    "detail": f"Las 3 señales de opciones confluyen {cdir}, pero la recomendación es {adir}."}
        else:
            factor, reason = 1.02, f"confluencia {cdir} (recomendación neutral)"
    elif verdict == "divergencia":
        factor, reason = 0.90, "divergencia: los pilares de opciones se contradicen entre sí"
    elif verdict == "mixto":
        factor, reason = 0.96, "confluencia mixta (sin consenso entre pilares)"
    adjusted = max(0.0, min(100.0, composite * factor))
    info = {"verdict": verdict, "direction": cdir, "badge": confl.get("badge"),
            "factor": round(factor, 3), "before": round(composite, 1), "after": round(adjusted, 1),
            "reason": reason}
    return adjusted, info, flag


def _qd_darkpool_prompt_block(darkpool, spot, dp_flow=None):
    """Render Quant Data dark-pool levels as a prompt block: top support (below spot) and
    resistance (above spot) zones by notional, plus the BUY/SELL proxy from prints when available."""
    if not darkpool or not isinstance(darkpool, list):
        return ""
    spot = _safe_num(spot)
    if not spot:
        return ""
    below = [x for x in darkpool if _safe_num(x.get("price")) and _safe_num(x.get("price")) < spot]
    above = [x for x in darkpool if _safe_num(x.get("price")) > spot]
    supp = sorted(below, key=lambda x: _safe_num(x.get("value")), reverse=True)[:4]
    resist = sorted(above, key=lambda x: _safe_num(x.get("value")), reverse=True)[:4]

    def _fmt(rows):
        return ", ".join(
            f"${round(_safe_num(r.get('price')), 2)} (${_safe_num(r.get('value'))/1e6:,.0f}M, "
            f"{int(_safe_num(r.get('size'))):,} sh)" for r in rows) or "—"
    tot_s = sum(_safe_num(x.get("value")) for x in below)
    tot_r = sum(_safe_num(x.get("value")) for x in above)
    flow_txt = ""
    if isinstance(dp_flow, dict) and dp_flow.get("total_notional"):
        bM = _safe_num(dp_flow.get("buy_notional")) / 1e6
        sM = _safe_num(dp_flow.get("sell_notional")) / 1e6
        mM = _safe_num(dp_flow.get("mid_notional")) / 1e6
        lean = dp_flow.get("lean_pct")
        flow_txt = (f" PROXY COMPRA/VENTA de los prints dark (tradeSide vs bid/ask): "
                    f"${bM:,.0f}M en el ASK (compra) vs ${sM:,.0f}M en el BID (venta), ${mM:,.0f}M al mid → "
                    f"sesgo {dp_flow.get('bias')}{(' (' + str(int(lean)) + ' net)') if lean is not None else ''}. "
                    f"El mid es neutral; el desbalance ask-vs-bid es la dirección real del dinero institucional.")
    return (
        f"\nDARK POOL (Quant Data — bloques off-exchange agregados por nivel, último mes): "
        f"SOPORTE (bajo el spot, posible acumulación): {_fmt(supp)}. "
        f"RESISTENCIA (sobre el spot, posible distribución): {_fmt(resist)}. "
        f"Notional total ${tot_s/1e6:,.0f}M en soporte vs ${tot_r/1e6:,.0f}M en resistencia.{flow_txt} "
        f"Los niveles agregados son posicionales (imán/soporte/resistencia que confirman o niegan los walls de GEX); "
        f"el proxy compra/venta sí da dirección. Intégralo en tus targets y en tu señal de flujo institucional.")


def _qd_netflow_prompt_block(nf):
    """Render the net-premium-over-time (net-flow) drift + trend as a prompt block."""
    if not nf or not isinstance(nf, dict) or not nf.get("series"):
        return ""
    cum = _safe_num(nf.get("cum_net"))
    win = {"today": "hoy (intradía)", "7d": "últimos 7 días", "30d": "últimos 30 días",
           "90d": "últimos 90 días"}.get(nf.get("window"), nf.get("window"))
    return (
        f"\nNET DRIFT EN EL TIEMPO (Quant Data net-flow, {win}): premium neto call−put acumulado "
        f"${cum:,.0f} → sesgo {nf.get('bias')}; tendencia {nf.get('trend')}. "
        f"USO: no mires solo el nivel — la TENDENCIA importa. 'acelerando (alcista)' = la presión compradora "
        f"de premium se intensifica (confirma momentum); 'desvaneciéndose' = el flujo pierde fuerza (cuidado con "
        f"agotamiento); 'revirtiendo' = el dinero está cambiando de lado (posible giro). Pondéralo en tu señal de flujo y en tu timing.")


def _gamma_flip_from_strikes(rows, spot):
    """Gamma flip / zero-gamma level desde el perfil net-por-strike de Quant Data.
    Método: cruce de signo del net gamma por strike MÁS CERCANO al spot, interpolado, sobre un
    perfil suavizado (ventana 3) y restringido a la zona near-money. Se ata a los walls
    (entre el put wall negativo y el call wall positivo) y NUNCA devuelve un nivel absurdo.
    Reemplaza dos métodos previos que fallaban: (a) el primer-cruce-acumulado desde el strike más
    bajo (daba flip $3.11 con subyacente $194), y (b) la reconstrucción por kernel auto-calibrado
    (el peso n/Γ(spot) explotaba para strikes lejanos y empujaba el flip ~+5% de más, p.ej. $766
    con spot $731). Devuelve el cruce de cero más cercano al spot, o None si no hay uno near-money."""
    if not rows or spot is None or spot <= 0:
        return None
    # near-money: mata el ruido de strikes profundamente OTM (origen de los flips absurdos)
    pts = sorted([(_safe_num(r.get("strike")), _safe_num(r.get("net"))) for r in rows
                  if r.get("strike") is not None and 0.85 * spot <= _safe_num(r.get("strike")) <= 1.15 * spot],
                 key=lambda x: x[0])
    if len(pts) < 3:
        return None
    ks = [k for k, _ in pts]
    ns = [n for _, n in pts]
    sm = []                                       # suavizado ventana 3 (reduce ruido de strike a strike)
    for i in range(len(ns)):
        a = ns[max(0, i - 1)]; b = ns[i]; c = ns[min(len(ns) - 1, i + 1)]
        sm.append((a + b + c) / 3.0)
    best = None
    for i in range(len(ks) - 1):
        n1, n2 = sm[i], sm[i + 1]
        if n1 == 0:
            cross = ks[i]
        elif n1 * n2 < 0:                          # cruce de signo (put-gamma → call-gamma): aquí el net pasa por cero
            cross = ks[i] + (ks[i + 1] - ks[i]) * (0 - n1) / (n2 - n1)
        else:
            continue
        if best is None or abs(cross - spot) < abs(best - spot):   # el cruce más cercano al spot
            best = cross
    if best is None or not (0.85 * spot <= best <= 1.15 * spot):   # clamp de sanidad: nunca un flip absurdo
        return None
    return round(best, 2)


def _flip_confidence(rows, spot):
    """Qué tan LIMPIO/confiable es el nivel del gamma flip. Métrica principal: PUREZA DE SEPARACIÓN — en el
    perfil near-money, qué fracción de strikes tiene net-gamma negativo POR DEBAJO del flip y positivo POR
    ENCIMA (un flip de libro separa limpiamente las dos zonas → pureza ~1.0; un perfil que oscila alrededor de
    cero → pureza ~0.5, frágil). Reporta además la 'sharpness' (lo pronunciada que es la transición).
    Devuelve {level, score 0-100, purity, sharpness} o None."""
    if not rows or spot is None or spot <= 0:
        return None
    pts = sorted([(_safe_num(r.get("strike")), _safe_num(r.get("net"))) for r in rows
                  if r.get("strike") is not None and 0.85 * spot <= _safe_num(r.get("strike")) <= 1.15 * spot],
                 key=lambda x: x[0])
    if len(pts) < 3:
        return None
    ks = [k for k, _ in pts]
    ns = [n for _, n in pts]
    sm = [(ns[max(0, i - 1)] + ns[i] + ns[min(len(ns) - 1, i + 1)]) / 3.0 for i in range(len(ns))]
    typ = (sum(abs(x) for x in sm) / len(sm)) or 1.0
    flip, sharp = None, 0.0                                  # localiza el cruce near-money más cercano al spot
    for i in range(len(sm) - 1):
        if sm[i] * sm[i + 1] < 0:
            kk = ks[i] + (ks[i + 1] - ks[i]) * (0 - sm[i]) / (sm[i + 1] - sm[i])
            jump = abs(sm[i + 1] - sm[i]) / typ
            if flip is None or abs(kk - spot) < abs(flip - spot):
                flip, sharp = kk, jump
    if flip is None:
        return None
    below = [n for k, n in pts if k < flip]
    above = [n for k, n in pts if k >= flip]
    tot = len(below) + len(above)
    ok = sum(1 for n in below if n <= 0) + sum(1 for n in above if n >= 0)
    purity = (ok / tot) if tot else 0.0
    score = max(5.0, min(100.0, 100.0 * purity))            # la pureza manda; la sharpness es informativa
    level = "alta" if score >= 75 else ("media" if score >= 55 else "baja")
    return {"level": level, "score": round(score), "purity": round(purity, 2), "sharpness": round(sharp, 2)}


def _qd_gex_walls(exposure, spot):
    """Derive call wall / put wall + zero-gamma pin from a Quant Data exposure dict (GAMMA, by_strike).
    El wall NO es el strike único con más gamma, sino el CENTRO del clúster de strikes contiguos con más
    gamma sumada (ventana ≈ ±1 strike → 2-3 strikes). Más robusto: una mecha aislada en un solo strike no
    desplaza el wall; gana la 'zona de pared' real donde se concentra el posicionamiento del dealer.
    El net devuelto es la suma del clúster (refleja la fuerza de la zona, no de un solo strike)."""
    if not exposure or not isinstance(exposure, dict):
        return None
    rows = [r for r in (exposure.get("by_strike") or []) if r.get("strike")]
    if not rows:
        return None
    # net agregado por strike + ventana = 1.5× el espaciado mediano de strikes (capta el strike ± su vecino inmediato)
    netmap = {}
    for r in rows:
        k = _safe_num(r.get("strike"))
        netmap[k] = netmap.get(k, 0.0) + _safe_num(r.get("net"))
    ks = sorted(netmap.keys())
    gaps = [ks[i + 1] - ks[i] for i in range(len(ks) - 1) if ks[i + 1] - ks[i] > 0]
    W = 1.5 * (sorted(gaps)[len(gaps) // 2] if gaps else 1.0)

    def _cluster(cands, want_max):
        best_k, best_key = None, None
        for k in cands:
            s = sum(n for kk, n in netmap.items() if abs(kk - k) <= W)   # suma del clúster contiguo
            key = (s, netmap.get(k, 0.0))   # desempate: a igual clúster, gana el strike con más gamma PROPIA (el pico real)
            if best_key is None or (key > best_key if want_max else key < best_key):
                best_key, best_k = key, k
        return (best_k, best_key[0]) if best_k is not None else (None, None)

    above = [k for k in ks if k > spot]
    below = [k for k in ks if k < spot]
    cw, cw_net = _cluster(above, True)   # call wall = clúster con MÁS gamma positiva arriba del spot
    pw, pw_net = _cluster(below, False)  # put wall  = clúster con MÁS gamma negativa abajo del spot
    flip = _gamma_flip_from_strikes(rows, spot)
    return {"call_wall": cw, "put_wall": pw,
            "call_wall_net": cw_net, "put_wall_net": pw_net,
            "gamma_flip": flip, "gamma_flip_confidence": (_flip_confidence(rows, spot) if flip else None),
            "max_pain": None}

_QD_EXPWALL_CACHE = {}
def _qd_exposure_walls(ticker, exp, spot, ttl=300):
    """Cached call/put wall from Quant Data GAMMA exposure for a single expiry."""
    key = f"{ticker.upper()}|{exp}"
    nowt = time.time()
    ent = _QD_EXPWALL_CACHE.get(key)
    if ent and nowt - ent[0] < ttl:
        return ent[1]
    w = _qd_gex_walls(quantdata_exposure(ticker, "GAMMA", expiration=exp), spot)
    _QD_EXPWALL_CACHE[key] = (nowt, w)
    return w

def _chain_metrics(tk, exp, spot):
    """ONE option_chain fetch → ATM IV (forward-looking) + TRUE max pain from full-chain open interest.
    Max pain needs the COMPLETE chain OI (every strike), which Quant Data's open-interest-change can't
    give (it only returns strikes whose OI changed), so we use the full yfinance chain here."""
    iv = mp = None
    mp_src = None
    try:
        ch = tk.option_chain(exp)
        ivs = []
        call_oi, put_oi = {}, {}
        for df, oid in ((ch.calls, call_oi), (ch.puts, put_oi)):
            if df is None or df.empty:
                continue
            d2 = df.dropna(subset=["impliedVolatility"])
            if not d2.empty:
                idx = (d2["strike"] - spot).abs().idxmin()
                v = float(d2.loc[idx, "impliedVolatility"])
                if 0.01 < v < 5.0:
                    ivs.append(v)
            for _, r in df.iterrows():
                K = _safe_num(r.get("strike")); oi = _safe_num(r.get("openInterest"))
                if K > 0 and oi > 0:
                    oid[K] = oid.get(K, 0.0) + oi
        if ivs:
            iv = sum(ivs) / len(ivs)
        # #7 — max pain por-vencimiento: QD nativo si honra el filtro (autodetectado), si no yfinance
        try:
            _sym = getattr(tk, "ticker", None) or ""
        except Exception:
            _sym = ""
        mp, mp_src = _max_pain_per_expiry_best(_sym, exp, call_oi, put_oi)
    except Exception:
        pass
    return {"iv": iv, "max_pain": mp, "max_pain_source": mp_src}


def _atm_iv(tk, exp, spot):
    """At-the-money implied vol for an expiry from the option chain (forward-looking).
    Returns annualized IV as a fraction, or None. Used so the expected-move band reflects what
    the market is PRICING (esp. around earnings), not just trailing realized vol."""
    try:
        ch = tk.option_chain(exp)
        ivs = []
        for df in (ch.calls, ch.puts):
            if df is None or df.empty:
                continue
            d2 = df.dropna(subset=["impliedVolatility"])
            if d2.empty:
                continue
            idx = (d2["strike"] - spot).abs().idxmin()
            iv = float(d2.loc[idx, "impliedVolatility"])
            if 0.01 < iv < 5.0:
                ivs.append(iv)
        if ivs:
            return sum(ivs) / len(ivs)
    except Exception:
        return None
    return None


def _next_earnings_date(tk):
    """Next earnings datetime from yfinance (or None). Lets targets flag horizons that cross a report."""
    try:
        cal = getattr(tk, "calendar", None)
        ed = None
        if isinstance(cal, dict):
            v = cal.get("Earnings Date")
            ed = v[0] if isinstance(v, (list, tuple)) and v else v
        elif cal is not None:
            try:
                ed = cal.loc["Earnings Date"][0]
            except Exception:
                ed = None
        if ed is None:
            return None
        if hasattr(ed, "to_pydatetime"):
            ed = ed.to_pydatetime()
        if isinstance(ed, datetime):
            return ed
        return datetime(ed.year, ed.month, ed.day)
    except Exception:
        return None


def _expected_move(spot, ann_vol, dte):
    """1-sigma expected move in $ over `dte` calendar days: spot · vol · sqrt(T)."""
    try:
        T = max(_safe_num(dte), 1) / 365.0
        return spot * ann_vol * math.sqrt(T)
    except Exception:
        return 0.0


def _clamp_target(level, spot, direction, em, k_max=1.5):
    """Bound a far-OTM wall to spot ± k_max·(expected move) so a deep strike can't become an
    absurd short-horizon target (e.g. $240 on a $368 spot in 30d). Returns (level, capped)."""
    if not level or spot <= 0 or em <= 0:
        return level, False
    if direction == "up":
        cap = spot + k_max * em
        if level > cap:
            return round(cap, 2), True
    elif direction == "down":
        cap = spot - k_max * em
        if level < cap:
            return round(cap, 2), True
    return level, False


_BT_MIN_HZ_N = 8    # muestra mínima por horizonte para confiar en su hit-rate
_BT_MIN_DIR_N = 12  # muestra mínima para confiar en el acierto direccional global


def _wilson_lower(hits, n, z=1.0):
    """Piso (lower bound) del intervalo de Wilson para una proporción binaria. Penaliza muestra chica
    automáticamente: n baja → intervalo ancho → piso bajo. z=1.0 ≈ 68% de confianza. Devuelve fracción 0-1."""
    if not n or n <= 0:
        return None
    p = max(0.0, min(1.0, hits / n))
    denom = 1.0 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)
    return max(0.0, (centre - margin) / denom)


def _confidence_from_hit(pct):
    """Map an empirical hit-rate (%) to a confidence label — replaces fixed rules with the
    ticker's real backtest accuracy when available."""
    if pct is None:
        return None
    if pct >= 65:
        return "muy alta"
    if pct >= 55:
        return "alta"
    if pct >= 45:
        return "media"
    if pct >= 35:
        return "media-baja"
    return "baja"


def compute_horizon_targets(ticker, net_premium=None, flow=None, ai_12m=None, qd_walls_fn=None, conviction=None, calibrate=True):
    """Targets at Hoy(0DTE)/7/30/60/90/120d + 12m. Levels anchored to Quant Data GEX walls (per expiry)
    when qd_walls_fn is provided, else to the computed chain. Direction driven by tape
    conviction (Kevin's tiers) → net premium → GEX, in that priority. El target 'Hoy' usa el
    vencimiento más cercano (0DTE si existe) + el flujo/convicción del día, mismo motor que el resto."""
    HZ = [0, 7, 30, 60, 90, 120]   # 0 = Hoy (0DTE / vencimiento más cercano)
    try:
        tk = yf.Ticker(ticker)
        h = tk.history(period="3mo")
        spot = _safe_num(h["Close"].iloc[-1], 0.0)
        exps = list(tk.options or [])
    except Exception:
        return None
    if spot <= 0 or not exps:
        return None
    # Annualized volatility from recent daily log-returns → drives the expected-move band.
    try:
        _r = np.log(h["Close"] / h["Close"].shift(1)).dropna()
        ann_vol = float(_r.std() * np.sqrt(252)) if len(_r) > 5 else 0.4
        if not (0.05 <= ann_vol <= 3.0):
            ann_vol = 0.4
    except Exception:
        ann_vol = 0.4
    earn_dt = _next_earnings_date(tk)   # #3 — flag horizons that cross a report
    now = datetime.now()
    # Direction priority: tape conviction > net premium > GEX magnet
    bias = "neutral"
    if conviction and conviction.get("bias") and conviction["bias"] != "neutral":
        bias = conviction["bias"]
    elif net_premium and isinstance(net_premium, dict):
        np_ = _safe_num(net_premium.get("net_premium"))
        bias = "alcista" if np_ > 0 else ("bajista" if np_ < 0 else "neutral")
    dir_pct = _classify_flow_delta(flow, spot) if flow else None
    conv_strength = conviction.get("strength_pct") if conviction else None
    strong_metric = conv_strength if conv_strength is not None else dir_pct

    # ── Dirección del horizonte "Hoy" con flujo PROPIO de la expiración 0DTE (no el agregado del día) ──
    # Kevin: el target de Hoy debe leer solo el tape de la 0DTE/vencimiento más cercano, también en dirección.
    bias0, strong0 = None, None
    try:
        _ne0 = _nearest_expiry(exps, 0, now)
        if _ne0 and _quantdata_ready():
            _nf0 = quantdata_net_flow(ticker, "today", _ne0[0])
            if _nf0 and _nf0.get("n"):
                bias0 = _nf0.get("bias") if _nf0.get("bias") != "neutral" else None
                _gross0 = _safe_num(_nf0.get("call_total")) + _safe_num(_nf0.get("put_total"))
                if _gross0 > 0:
                    strong0 = round(100.0 * abs(_safe_num(_nf0.get("cum_net"))) / _gross0)   # desbalance 0DTE (0–100)
    except Exception:
        bias0, strong0 = None, None

    # #4 — pull this ticker's own backtest so confidence reflects REAL accuracy, not fixed rules.
    bt_cal = None
    if calibrate:
        try:
            _bt = _backtest_cached(ticker)
            if _bt and _bt.get("ok") and _bt.get("n_snapshots"):
                _cd = _bt.get("confluence_direction") or {}
                bt_cal = {"hr": _bt.get("target_hit_rate") or {},
                          "dir_acc": _cd.get("accuracy_pct"), "dir_n": _cd.get("evaluated") or 0}
        except Exception:
            bt_cal = None

    targets, used_qd = [], False
    for i, hz in enumerate(HZ):
        ne = _nearest_expiry(exps, hz, now)
        if not ne:
            continue
        exp, dte = ne
        w, wsrc = None, "cadena"
        if qd_walls_fn:
            try:
                w = qd_walls_fn(exp, spot)
            except Exception:
                w = None
            if w:
                wsrc = "Quant Data GEX"; used_qd = True
        if not w:
            w = _walls_for_expiry(tk, exp, spot)
        if not w:
            continue
        _cm = _chain_metrics(tk, exp, spot)   # #2 IV + #5 max pain real (una sola bajada de cadena)
        iv = _cm["iv"]
        chain_mp = _cm["max_pain"]
        pin = chain_mp or w.get("gamma_flip")  # max pain real (OI completo) o, si no, el gamma flip
        # ── Dirección = mezcla de FLUJO y el IMÁN DE GAMMA dominante (no flujo-solo) ──
        cw, pw = w.get("call_wall"), w.get("put_wall")
        cwn, pwn = w.get("call_wall_net"), w.get("put_wall_net")
        # imán dominante: el wall con mayor |gamma neta| (o, si no se conoce, el más cercano al spot)
        magnet, mag_dir = None, None
        if cw and pw and cwn is not None and pwn is not None:
            magnet, mag_dir = (cw, "up") if abs(cwn) >= abs(pwn) else (pw, "down")
        elif cw and pw:
            magnet, mag_dir = (cw, "up") if abs(cw - spot) <= abs(spot - pw) else (pw, "down")
        elif cw:
            magnet, mag_dir = cw, "up"
        elif pw:
            magnet, mag_dir = pw, "down"
        flow_dir = (bias0 if (hz == 0 and bias0) else bias)   # Hoy usa el sesgo 0DTE propio si existe
        sm_use = strong0 if (hz == 0 and strong0 is not None) else strong_metric
        conflict = False
        if magnet is None:
            level = pin or cw or pw
            direction = "up" if (level and level > spot) else "down"
            basis = ("max pain" if chain_mp else ("pin gamma" if w.get("gamma_flip") else "estructura"))
        elif flow_dir == "neutral":
            direction, level, basis = mag_dir, magnet, "imán gamma"
        elif (flow_dir == "alcista" and mag_dir == "up") or (flow_dir == "bajista" and mag_dir == "down"):
            direction = mag_dir
            level = cw if mag_dir == "up" else pw
            basis = "flujo + gamma"
        else:
            # flujo y gamma se contradicen → el flujo manda SOLO si la convicción es fuerte (≥60%)
            conflict = True
            if sm_use is not None and sm_use >= 60:
                direction = "up" if flow_dir == "alcista" else "down"
                level = cw if flow_dir == "alcista" else pw
                basis = "flujo fuerte vs imán"
            else:
                direction, level, basis = mag_dir, magnet, "imán gamma vs flujo débil"
        # Sin reversión forzada; cada horizonte usa su propia estructura.
        vol_use = iv if iv else ann_vol
        em = _expected_move(spot, vol_use, dte)
        level, capped = _clamp_target(level, spot, direction, em)
        earnings_soon = bool(earn_dt and now <= earn_dt <= now + timedelta(days=hz))
        # Base confidence by horizon — fallback only while there's no backtest yet.
        conf = "alta" if hz <= 30 else ("media" if hz <= 90 else "media-baja")
        if sm_use is not None:
            if sm_use >= 60 and hz <= 60:
                conf = "muy alta"
            elif sm_use < 40 and hz <= 30:
                conf = "media"
        # #4 + rigor estadístico — calibra SOLO con muestra suficiente y mapea la confianza desde el
        # PISO de Wilson, no del % crudo (n baja → piso bajo → confianza menor, automáticamente).
        cal_pct = cal_lo = cal_n = None
        cal_low_sample = False
        if bt_cal:
            _hr = bt_cal["hr"].get(str(hz))
            if _hr and _hr.get("hit_rate_pct") is not None and _hr.get("total", 0) >= _BT_MIN_HZ_N:
                cal_n = int(_hr["total"]); cal_pct = _hr["hit_rate_pct"]
                cal_lo = round(_wilson_lower(round(cal_pct / 100.0 * cal_n), cal_n) * 100, 1)
            elif bt_cal.get("dir_acc") is not None and bt_cal.get("dir_n", 0) >= _BT_MIN_DIR_N:
                cal_n = int(bt_cal["dir_n"]); cal_pct = bt_cal["dir_acc"]
                cal_lo = round(_wilson_lower(round(cal_pct / 100.0 * cal_n), cal_n) * 100, 1)
            elif (_hr and _hr.get("total", 0) > 0) or bt_cal.get("dir_n", 0) > 0:
                cal_low_sample = True   # hay backtest pero muestra demasiado chica → NO calibramos
        _cal_conf = _confidence_from_hit(cal_lo) if cal_lo is not None else None
        _src = f"{wsrc} · {basis}"
        if capped:
            _src += " · ajustado a mov. esperado"
            if conf in ("muy alta", "alta"):
                conf = "media"  # capped a far wall → menos certeza del nivel exacto
        if _cal_conf:                                       # el backtest real (con muestra) manda sobre la regla
            conf = _cal_conf
            _src += f" · calibrado {cal_pct:.0f}% (piso {cal_lo:.0f}%, n={cal_n})"
        elif cal_low_sample:
            _src += " · n insuficiente → confianza por reglas"
        if earnings_soon:                                   # #3 — earnings en el horizonte = otro régimen
            _src += " · ⚠ earnings en el rango"
            if conf in ("muy alta", "alta"):
                conf = "media"
        if hz == 0:                                         # Hoy / 0DTE: ruido intrínseco salvo convicción fuerte
            _src = "0DTE · " + _src
            if bias0:
                _src += " · dir. flujo 0DTE propio"
            if conf in ("muy alta", "alta") and not (sm_use is not None and sm_use >= 60):
                conf = "media"
        targets.append({"label": ("Hoy" if hz == 0 else f"{hz}d"), "horizon_days": hz, "expiry": exp, "dte": dte,
                        "level": round(level, 2) if level else None, "direction": direction,
                        "confidence": conf, "capped": capped, "basis": basis, "conflict": conflict,
                        "calibrated_pct": (round(cal_pct, 0) if cal_pct is not None else None),
                        "calibrated_lo": (round(cal_lo, 0) if cal_lo is not None else None),
                        "calibrated_n": cal_n,
                        "expected_move": round(em, 2) if em else None,
                        "em_low": round(spot - em, 2) if em else None,
                        "em_high": round(spot + em, 2) if em else None,
                        "iv": (round(iv, 4) if iv else None), "vol_used": round(vol_use, 4),
                        "vol_source": ("IV" if iv else "histórica"),
                        "earnings_soon": earnings_soon,
                        "gamma_flip": w.get("gamma_flip"), "max_pain": chain_mp,
                        "call_wall": cw, "put_wall": pw,
                        "call_wall_net": cwn, "put_wall_net": pwn,
                        "max_pain_source": _cm.get("max_pain_source"),
                        "source": _src})
    if ai_12m and _safe_num(ai_12m) > 0:
        a = _safe_num(ai_12m)
        targets.append({"label": "12 meses", "horizon_days": 365, "level": round(a, 2),
                        "direction": "up" if a > spot else "down", "confidence": "fundamental",
                        "source": "DCF / fundamental del agente"})
    else:
        targets.append({"label": "12 meses", "horizon_days": 365, "level": None, "direction": None,
                        "confidence": "fundamental",
                        "source": "Corre la tesis AI para el target fundamental de 12m"})
    return _json_safe({"ok": True, "ticker": ticker.upper(), "spot": round(spot, 2),
                       "bias": bias, "directional_pct": dir_pct, "conviction": conviction,
                       "gex_source": "Quant Data" if used_qd else "computed (yfinance)",
                       "targets": targets,
                       "note": "Targets cortos (Hoy/0DTE–120d) guiados por GEX + convicción del tape; el de 12m es "
                               "fundamental. 'Hoy' usa el vencimiento más cercano + flujo del día. Escenarios probabilísticos, no predicciones.",
                       "generated_at": now.strftime('%m/%d/%Y, %I:%M:%S %p')})

_HZTGT_CACHE = {}
def get_horizon_targets_cached(ticker, net_premium=None, flow=None, ai_12m=None, ttl=300):
    key = f"{ticker.upper()}|{ai_12m}|{bool(net_premium)}"
    nowt = time.time()
    ent = _HZTGT_CACHE.get(key)
    if ent and nowt - ent[0] < ttl:
        return ent[1]
    val = compute_horizon_targets(ticker, net_premium, flow, ai_12m)
    _HZTGT_CACHE[key] = (nowt, val)
    return val


@app.get("/api/projection-targets")
def projection_targets(ticker: str, ai_12m: float = 0.0):
    """Directional targets by horizon. When Quant Data is configured, levels come from QD
    GEX (per expiry) and direction from tape conviction (Kevin's tiers)."""
    ready = _quantdata_ready()
    np_ = quantdata_net_premium(ticker) if ready else None
    fl = quantdata_flow(ticker) if ready else None
    oic = quantdata_oi_change(ticker) if ready else None          # real per-contract ΔOI
    oic_map = oic.get("map") if isinstance(oic, dict) else None
    conv = _qd_conviction(fl, oi_change_map=oic_map) if fl else None
    walls_fn = (lambda e, s: _qd_exposure_walls(ticker, e, s)) if ready else None
    t = compute_horizon_targets(ticker, np_, fl, (ai_12m or None),
                                qd_walls_fn=walls_fn, conviction=conv)
    if not t:
        return {"ok": False, "error": "No se pudieron proyectar targets (cadena no disponible)."}
    if isinstance(oic, dict) and oic.get("builds"):
        t["oi_builds"] = oic["builds"]                            # top OI accumulations for display
    return t


def _moneyness(K, spot, opt):
    """ATM / ITM / OTM para un strike dado. call: ITM=strike<spot · put: ITM=strike>spot."""
    if not K or not spot:
        return None
    d = (K - spot) if opt == "call" else (spot - K)   # d>0 = OTM, d<0 = ITM
    if abs(d) / spot < 0.01:
        return "ATM"
    return "OTM" if d > 0 else "ITM"


def _institutional_strike(rows, bias, near_dte=None, dte_tol=45):
    """Del flujo $1M+ direccional (ventana), encuentra el strike donde se concentró MÁS premium
    direccional para el sesgo dominante: alcista→CALLs comprados (lado ASK/above) · bajista→PUTs
    comprados. Si near_dte se da, des-pondera concentración de vencimientos lejanos. Devuelve
    {strike, premium, trades, cp, exp_top, total_strikes} o None."""
    if not rows or bias not in ("alcista", "bajista"):
        return None
    want_cp = "CALL" if bias == "alcista" else "PUT"
    buys = ("ASK", "ABOVE_ASK")
    agg = {}
    for r in rows:
        if (r.get("cp") or "").upper() != want_cp:
            continue
        if (r.get("side") or "").upper() not in buys:
            continue
        K = _safe_num(r.get("strike")); prem = _safe_num(r.get("premium"))
        if K <= 0 or prem <= 0:
            continue
        w = prem
        if near_dte is not None and r.get("dte") is not None:
            if abs(_safe_num(r.get("dte")) - near_dte) > dte_tol:
                w *= 0.35
        a = agg.setdefault(K, {"prem": 0.0, "n": 0, "exps": {}})
        a["prem"] += w; a["n"] += 1
        ex = r.get("exp")
        if ex:
            a["exps"][ex] = a["exps"].get(ex, 0.0) + prem
    if not agg:
        return None
    K_best = max(agg, key=lambda k: agg[k]["prem"])
    a = agg[K_best]
    exp_top = max(a["exps"], key=a["exps"].get) if a["exps"] else None
    return {"strike": round(K_best, 2), "premium": round(a["prem"], 0), "trades": a["n"],
            "cp": want_cp, "exp_top": exp_top, "total_strikes": len(agg)}


def _kevin_long_strike(anchor, spot, opt, atm):
    """Regla de Kevin: comprar ATM o ITM, NUNCA OTM. Ancla al strike institucional pero si está OTM
    lo jala a ATM; si la institución está ITM, respeta ese ITM (Kevin acepta igual o más ITM)."""
    if anchor is None or anchor <= 0:
        return atm
    if opt == "call":
        return min(anchor, atm)   # OTM (>spot) → ATM ; ITM (<spot) → se queda ITM
    return max(anchor, atm)        # put: OTM (<spot) → ATM ; ITM (>spot) → se queda ITM


def _build_debit_spread(spot, long_K, short_K, level, dte, iv, RF, opt, stop_frac, capital, budget, ref_entry_c, alloc=None):
    """Débito vertical compra long_K / vende short_K. Devuelve dict listo para el frontend o None."""
    width = (short_K - long_K) if opt == "call" else (long_K - short_K)
    if width <= 0:
        return None
    long_entry = _bs_price(spot, long_K, dte / 365.0, iv, RF, opt)
    short_entry = _bs_price(spot, short_K, dte / 365.0, iv, RF, opt)
    net_debit = long_entry - short_entry
    if net_debit <= 0:
        return None
    net_debit_c = net_debit * 100.0
    max_val_c = width * 100.0
    long_val = _bs_price(level, long_K, max(dte / 2.0, 0.5) / 365.0, iv, RF, opt)
    short_val = _bs_price(level, short_K, max(dte / 2.0, 0.5) / 365.0, iv, RF, opt)
    val_fast_c = min(max((long_val - short_val) * 100.0, 0.0), max_val_c)
    reward_fast_c = val_fast_c - net_debit_c
    be = (long_K + net_debit) if opt == "call" else (long_K - net_debit)
    risk_c = net_debit_c * stop_frac
    cap_for_n = min(float(capital), float(alloc)) if alloc is not None else float(capital)   # Kelly: limita prima desplegada
    n_cap = int(cap_for_n // net_debit_c) if net_debit_c > 0 else 0
    n_risk = int(budget // risk_c) if risk_c > 0 else 0
    n = max(0, min(n_cap, n_risk))
    lm, sm = _moneyness(long_K, spot, opt), _moneyness(short_K, spot, opt)
    return {"long_strike": long_K, "short_strike": short_K, "width": round(width, 2),
            "net_debit": round(net_debit, 2), "net_debit_contract": round(net_debit_c, 0),
            "max_profit_contract": round(max_val_c - net_debit_c, 0), "max_value_contract": round(max_val_c, 0),
            "breakeven": round(be, 2), "exit_value_fast": round(val_fast_c, 0),
            "reward_fast_contract": round(reward_fast_c, 0),
            "rr_fast": round(reward_fast_c / risk_c, 2) if risk_c > 0 else None,
            "stop_pct": round(stop_frac * 100, 0), "planned_risk_contract": round(risk_c, 0),
            "contracts": n, "n_cap": n_cap, "n_risk": n_risk,
            "fits_capital": bool(n_cap >= 1),
            "risk_pct_at_1": round(risk_c / float(capital) * 100, 1) if capital else None,
            "total_cost": round(net_debit_c * max(n, 1 if n_cap >= 1 else 0), 0),
            "cost_saving_pct": round((1 - net_debit_c / ref_entry_c) * 100, 0) if ref_entry_c > 0 else None,
            "long_moneyness": lm, "short_moneyness": sm, "combo": f"{lm}/{sm}"}


_QMAP_CACHE = {}
def _chain_quote_map(ticker, expiries, ttl=180):
    """Quotes REALES (bid/ask/mid + OI + volumen) por (expiry, CALL/PUT, strike) desde la cadena de yfinance.
    Permite que el plan de opciones use el FILL real (mid del bid/ask) y la LIQUIDEZ real (OI/spread) en vez del
    precio teórico Black-Scholes. Best-effort y cacheado: si yfinance no responde, devuelve {} y el plan cae al
    teórico sin romperse. El spread bid/ask en contratos chicos/OTM se come 10–30% de la prima — esto lo expone."""
    exps = sorted({str(e)[:10] for e in (expiries or []) if e})
    if not exps:
        return {}
    key = (ticker.upper(), tuple(exps))
    now = time.time()
    hit = _QMAP_CACHE.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    out = {}
    try:
        tk = yf.Ticker(ticker)
        for e in exps:
            try:
                ch = tk.option_chain(e)
            except Exception:
                continue
            for df, cp in ((getattr(ch, "calls", None), "CALL"), (getattr(ch, "puts", None), "PUT")):
                if df is None or getattr(df, "empty", True):
                    continue
                for _, r in df.iterrows():
                    K = _safe_num(r.get("strike"))
                    if K <= 0:
                        continue
                    bid = _safe_num(r.get("bid")); ask = _safe_num(r.get("ask")); last = _safe_num(r.get("lastPrice"))
                    mid = round((bid + ask) / 2, 4) if (bid > 0 and ask > 0) else (last if last > 0 else None)
                    out[(e, cp, round(K, 2))] = {"oi": int(_safe_num(r.get("openInterest"))),
                                                 "vol": int(_safe_num(r.get("volume"))),
                                                 "bid": (bid or None), "ask": (ask or None), "mid": mid}
    except Exception:
        return {}
    _QMAP_CACHE[key] = (now, out)
    return out

def _q_lookup(qmap, exp, cp, K):
    """Quote real para (exp, cp, K): match exacto o el strike listado MÁS CERCANO dentro de ~2.5%."""
    if not qmap or not exp:
        return None
    cp = str(cp).upper(); Kr = round(_safe_num(K), 2)
    hit = qmap.get((exp, cp, Kr))
    if hit:
        return hit
    cand = [(k, v) for (e, c, k), v in qmap.items() if e == exp and c == cp]
    if not cand:
        return None
    bk, bv = min(cand, key=lambda kv: abs(kv[0] - Kr))
    return bv if abs(bk - Kr) <= max(0.75, 0.025 * Kr) else None


_AICORR_CACHE = {}
def _ai_concentration(ticker, proxy="SMH", ttl=3600):
    """Correlación de retornos del ticker vs el complejo IA/semis (SMH) en ~6m. Advierte, al analizar o
    dimensionar, que el nombre añade poca diversificación a una cartera ya cargada de IA: ρ alto = es 'la misma
    apuesta'. Auto-contenido (no necesita el book): mide cuánto ES este nombre la apuesta de IA. Punto ciego real
    de un libro NVDA/AMD/PLTR/GOOGL donde la diversificación efectiva tiende a 1."""
    key = str(ticker).upper(); now = time.time()
    hit = _AICORR_CACHE.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    out = None
    try:
        if key != proxy:
            a = dict(_cached_price_series(ticker, period="6mo") or [])
            b = dict(_cached_price_series(proxy, period="6mo") or [])
            common = sorted(set(a) & set(b))
            if len(common) > 30:
                pa = [a[t] for t in common]; pb = [b[t] for t in common]
                ra = [(pa[i] / pa[i-1] - 1) for i in range(1, len(pa)) if pa[i-1] > 0]
                rb = [(pb[i] / pb[i-1] - 1) for i in range(1, len(pb)) if pb[i-1] > 0]
                n = min(len(ra), len(rb))
                if n > 20:
                    import numpy as np
                    rho = float(np.corrcoef(ra[-n:], rb[-n:])[0, 1])
                    if rho == rho:    # no NaN
                        lvl = "alta" if rho >= 0.7 else ("media" if rho >= 0.45 else "baja")
                        note = (f"ρ={round(rho,2)} con el complejo IA/semis (SMH): " + (
                            "este nombre ES la apuesta de IA — si ya tienes NVDA/AMD/PLTR/GOOGL u otros del clúster, tu "
                            "diversificación efectiva ≈ 1 posición; dimensiona como si SUMARAS a la misma posición, no a una nueva."
                            if rho >= 0.7 else
                            "correlación moderada con IA/semis — diversifica algo, pero vigila el solapamiento del libro."
                            if rho >= 0.45 else
                            "baja correlación con IA/semis — aporta diversificación real al libro."))
                        out = {"proxy": proxy, "rho": round(rho, 2), "level": lvl, "note": note}
    except Exception:
        out = None
    _AICORR_CACHE[key] = (now, out)
    return out


@app.get("/api/trade-plan")
def trade_plan_endpoint(ticker: str, capital: float = 500.0, risk_pct: float = 15.0,
                        horizons: str = "30,60,90", stop_pct: float = 25.0,
                        alloc_pct: float = None):
    """Convierte la SEÑAL (dirección + convicción + target por horizonte) en una OPERACIÓN de opción
    CONCRETA: contrato (CALL/PUT ≈ATM), prima de entrada (Black-Scholes con la IV del horizonte),
    valor proyectado de la opción si el subyacente llega al target (rápido vs al vencimiento), R:R,
    breakeven, stop (−20/−30% de la prima, con excepción de flujo Tipo A $5M+) y nº de contratos
    dimensionado a tu capital y presupuesto de riesgo. Complementa el trade_plan de equity del agente."""
    ticker = ticker.upper().strip()
    RF = 0.043
    stop_frac = max(min(float(stop_pct), 90.0), 1.0) / 100.0
    try:
        hz_list = [int(x) for x in str(horizons).split(",") if x.strip().isdigit()][:5] or [30, 60, 90]
    except Exception:
        hz_list = [30, 60, 90]
    ready = _quantdata_ready()
    np_ = quantdata_net_premium(ticker) if ready else None
    fl = quantdata_flow(ticker) if ready else None
    oic = quantdata_oi_change(ticker) if ready else None
    oic_map = oic.get("map") if isinstance(oic, dict) else None
    conv = _qd_conviction(fl, oi_change_map=oic_map) if fl else None
    walls_fn = (lambda e, s: _qd_exposure_walls(ticker, e, s)) if ready else None
    t = compute_horizon_targets(ticker, np_, fl, None, qd_walls_fn=walls_fn, conviction=conv, calibrate=True)
    if not t or not t.get("ok") or not t.get("targets"):
        return {"ok": False, "error": "Sin targets para estructurar (cadena/QD no disponible)."}
    spot = _safe_num(t.get("spot"))
    if spot <= 0:
        return {"ok": False, "error": "Sin spot disponible."}

    # IV vs volatilidad REALIZADA (proxy de VRP, sin necesitar IV-rank histórico): si la IV del
    # horizonte está cara vs la realizada, comprar prima larga es ineficiente → favorecer débito spread;
    # si está barata, la opción simple es más eficiente (más convexidad por el costo).
    realized_ann = None
    try:
        _ser = _cached_price_series(ticker, period="6mo")
        if _ser and len(_ser) > 20:
            _cl = [c for _, c in _ser]
            _rets = [math.log(_cl[i] / _cl[i - 1]) for i in range(1, len(_cl)) if _cl[i - 1] > 0]
            if len(_rets) > 10:
                _mean = sum(_rets) / len(_rets)
                _var = sum((x - _mean) ** 2 for x in _rets) / (len(_rets) - 1)
                realized_ann = (_var ** 0.5) * (252 ** 0.5)
    except Exception:
        realized_ann = None

    def _iv_regime(iv):
        if not realized_ann or realized_ann <= 0 or not iv:
            return None, None
        ratio = iv / realized_ann
        if ratio >= 1.25:
            return round(ratio, 2), "IV rica vs realizada → el débito spread es más eficiente (vendes vol cara)"
        if ratio <= 1.00:
            return round(ratio, 2), "IV barata vs realizada → la opción simple larga es más eficiente (más convexidad)"
        return round(ratio, 2), "IV en línea con la realizada → estructura por capital/preferencia"

    # Tipo A activo = una sola transacción ≥ $5M alineada con el sesgo dominante (excepción de stop de Kevin)
    tipo_a = False
    dom_bias = (conv or {}).get("bias")
    if conv and conv.get("strong_trades"):
        want = "CALL" if dom_bias == "alcista" else ("PUT" if dom_bias == "bajista" else None)
        for st in conv["strong_trades"]:
            if _safe_num(st.get("premium")) >= 5e6 and (want is None or st.get("cp") == want):
                tipo_a = True
                break

    # Anclaje institucional: dónde se acumularon MÁS millones direccionales (ventana 90d, $1M+).
    inst_rows = quantdata_flow_window(ticker, days=90, min_premium=1_000_000) if ready else None
    inst_overall = None
    if inst_rows and dom_bias in ("alcista", "bajista"):
        inst_overall = _institutional_strike(inst_rows, dom_bias)

    def strike_round(x):
        if x >= 100:
            return round(x / 5.0) * 5.0
        if x >= 25:
            return float(round(x))
        return round(x * 2) / 2.0

    by_hz = {x.get("horizon_days"): x for x in t["targets"]}
    short = [x for x in t["targets"] if x.get("horizon_days", 0) < 365 and x.get("level")]
    # Quotes reales de la cadena (mid del bid/ask + OI) para que la entrada y la liquidez NO sean teóricas
    _need_exp = sorted({x.get("expiry") for x in t["targets"] if x.get("expiry")})
    qmap = _chain_quote_map(ticker, _need_exp)
    plans = []
    for hz in hz_list:
        tg = by_hz.get(hz)
        if not (tg and tg.get("level") and tg.get("direction")):
            tg = min(short, key=lambda x: abs(x["horizon_days"] - hz)) if short else None
        if not tg or not tg.get("level") or not tg.get("direction"):
            continue
        direction = tg["direction"]
        opt = "call" if direction == "up" else "put"
        atm_K = strike_round(spot)
        bias_for_anchor = "alcista" if opt == "call" else "bajista"
        inst_hz = _institutional_strike(inst_rows, bias_for_anchor, near_dte=hz) if inst_rows else None
        anchor_K = (inst_hz["strike"] if inst_hz
                    else (inst_overall["strike"] if (inst_overall and inst_overall.get("cp") == opt.upper()) else None))
        K = _kevin_long_strike(anchor_K, spot, opt, atm_K)   # tu regla: ATM o ITM, nunca OTM
        long_mny = _moneyness(K, spot, opt)
        dte = int(tg.get("dte") or hz)
        iv = _safe_num(tg.get("vol_used")) or _safe_num(tg.get("iv"))
        if iv <= 0 or dte <= 0:
            continue
        entry_theo = _bs_price(spot, K, dte / 365.0, iv, RF, opt)    # prima teórica BSM (por acción)
        rq = _q_lookup(qmap, tg.get("expiry"), opt, K)               # fill REAL (mid) + liquidez de la cadena
        liq_oi = liq_vol = liq_ask = liq_spread = None
        if rq and rq.get("mid") and rq["mid"] > 0:
            entry = float(rq["mid"]); pricing_basis = "mid real (bid/ask)"
            liq_oi, liq_vol, liq_ask = rq.get("oi"), rq.get("vol"), rq.get("ask")
            if rq.get("bid") and rq.get("ask") and entry > 0:
                liq_spread = round((rq["ask"] - rq["bid"]) / entry * 100, 1)   # % del mid
        else:
            entry = entry_theo; pricing_basis = "teórico (sin quote en vivo)"
        if entry <= 0:
            continue
        entry_c = entry * 100.0
        level = _safe_num(tg["level"])
        # Valor de salida en el target bajo 2 supuestos de tiempo (theta):
        val_fast = _bs_price(level, K, max(dte / 2.0, 0.5) / 365.0, iv, RF, opt) * 100.0   # llega a mitad del horizonte
        intr = max(0.0, (level - K) if opt == "call" else (K - level)) * 100.0             # llega al vencimiento (solo intrínseco)
        breakeven = (K + entry) if opt == "call" else (K - entry)
        stop_price = entry * (1 - stop_frac)
        planned_risk_c = entry_c * stop_frac
        budget = max(float(capital), 0.0) * max(float(risk_pct), 0.0) / 100.0
        n_by_risk = int(budget // planned_risk_c) if planned_risk_c > 0 else 0
        n_by_cap = int(float(capital) // entry_c) if entry_c > 0 else 0
        if alloc_pct is not None:                       # Kelly del agente → dimensiona por CAPITAL desplegado en prima
            alloc_dollars = max(float(capital), 0.0) * max(float(alloc_pct), 0.0) / 100.0
            n_by_alloc = int(alloc_dollars // entry_c) if entry_c > 0 else 0
            contracts = max(0, min(n_by_alloc, n_by_cap))
        else:
            contracts = max(0, min(n_by_risk, n_by_cap))
        reward_fast_c = val_fast - entry_c
        reward_intr_c = intr - entry_c
        rr_fast = (reward_fast_c / planned_risk_c) if planned_risk_c > 0 else None
        rr_intr = (reward_intr_c / planned_risk_c) if planned_risk_c > 0 else None
        notes = []
        if tg.get("vol_source") != "IV":
            notes.append("IV histórica (proxy): la prima real puede diferir")
        if tg.get("earnings_soon"):
            notes.append("⚠ earnings en el horizonte: posible IV crush tras el evento")
        if tg.get("capped"):
            notes.append("target ajustado al movimiento esperado")
        if tg.get("conflict"):
            notes.append("flujo vs imán de gamma en conflicto")
        # --- Liquidez / fill real del contrato (qué tan fácil es ENTRAR y SALIR) ---
        if pricing_basis.startswith("mid real"):
            if liq_oi is not None and liq_oi < 50:
                notes.append(f"⚠ OI bajo ({liq_oi}) en el strike — difícil de cerrar sin mover el precio")
            if liq_spread is not None and liq_spread > 20:
                notes.append(f"⚠ spread ancho (~{liq_spread}% del mid) — el bid/ask te come prima al entrar y salir")
        else:
            notes.append("prima teórica (sin quote en vivo): el fill real puede diferir 10–30% en strikes finos")
        # --- Estructuras de débito para capital chico (dos variantes) ---
        step = 5.0 if spot >= 100 else (1.0 if spot >= 25 else 0.5)
        _alloc_d = (max(float(capital), 0.0) * max(float(alloc_pct), 0.0) / 100.0) if alloc_pct is not None else None
        # 1) Tu regla: largo ATM/ITM (K) · corto OTM hacia el target → ITM/OTM o ATM/OTM
        s_short = (max(strike_round(level), K + step) if opt == "call" else min(strike_round(level), K - step))
        spread = _build_debit_spread(spot, K, s_short, level, dte, iv, RF, opt, stop_frac, capital, budget, entry_c, alloc=_alloc_d)
        # 2) OTM/OTM barato tipo lotería: largo a mitad de camino al target, corto en/junto al target
        if opt == "call":
            l2 = max(strike_round((spot + level) / 2.0), strike_round(spot) + step)
            s2 = max(strike_round(level), l2 + step)
        else:
            l2 = min(strike_round((spot + level) / 2.0), strike_round(spot) - step)
            s2 = min(strike_round(level), l2 - step)
        spread_otm = _build_debit_spread(spot, l2, s2, level, dte, iv, RF, opt, stop_frac, capital, budget, entry_c, alloc=_alloc_d)
        # Sizing por CAPITAL (no solo por presupuesto de riesgo): qué estructura cabe de verdad en tu cuenta
        naked_fits = int(float(capital) // entry_c) >= 1
        if naked_fits:
            recommended = "opción simple"
        elif spread and spread.get("fits_capital"):
            recommended = f"débito spread {spread['combo']}"
        elif spread_otm and spread_otm.get("fits_capital"):
            recommended = "débito spread OTM/OTM"
        else:
            recommended = None
        if contracts == 0:
            if recommended == "opción simple":
                notes.append(f"Cabe 1 opción simple (${entry_c:,.0f}) pero excede tu presupuesto de riesgo {risk_pct:.0f}% — decides tú.")
            elif recommended and spread and spread.get("fits_capital"):
                notes.append(f"La opción simple no cabe en ${float(capital):,.0f} → usa el {recommended} (≈${spread['net_debit_contract']:,.0f}/contrato, riesgo {spread.get('risk_pct_at_1')}%).")
            elif recommended and spread_otm:
                notes.append(f"Solo cabe el {recommended} (≈${spread_otm['net_debit_contract']:,.0f}/contrato) en ${float(capital):,.0f}.")
            else:
                notes.append(f"Ni opción simple ni spread caben en ${float(capital):,.0f} — sube capital, usa menos DTE, o un strike más OTM.")
        plans.append({
            "label": tg.get("label"), "horizon_days": tg.get("horizon_days"),
            "direction": direction, "opt_type": opt.upper(), "strike": K,
            "long_moneyness": long_mny,
            "inst_strike": (inst_hz["strike"] if inst_hz else anchor_K),
            "inst_premium": (inst_hz["premium"] if inst_hz else None),
            "inst_trades": (inst_hz["trades"] if inst_hz else None),
            "inst_exp": (inst_hz["exp_top"] if inst_hz else None),
            "anchored": bool(anchor_K is not None),
            "expiry": tg.get("expiry"), "dte": dte,
            "iv_pct": round(iv * 100, 1), "iv_source": tg.get("vol_source"),
            "entry_price": round(entry, 2), "entry_cost_contract": round(entry_c, 0),
            "entry_basis": pricing_basis, "entry_theo": round(entry_theo, 2),
            "entry_ask": (round(liq_ask, 2) if liq_ask else None),
            "entry_spread_pct": liq_spread, "strike_oi": liq_oi, "strike_vol": liq_vol,
            "liquidity_ok": (None if not pricing_basis.startswith("mid real")
                             else bool((liq_oi or 0) >= 50 and (liq_spread is None or liq_spread <= 20))),
            "breakeven": round(breakeven, 2), "target_underlying": round(level, 2),
            "exit_value_fast": round(val_fast, 0), "exit_value_expiry": round(intr, 0),
            "reward_fast_contract": round(reward_fast_c, 0), "reward_expiry_contract": round(reward_intr_c, 0),
            "rr_fast": round(rr_fast, 2) if rr_fast is not None else None,
            "rr_expiry": round(rr_intr, 2) if rr_intr is not None else None,
            "stop_pct": round(stop_frac * 100, 0), "stop_price": round(stop_price, 2), "stop_band": "−20% a −30%",
            "planned_risk_contract": round(planned_risk_c, 0), "max_loss_contract": round(entry_c, 0),
            "contracts": contracts, "total_cost": round(entry_c * contracts, 0),
            "n_by_risk": n_by_risk, "n_by_cap": n_by_cap,
            "total_risk": round(planned_risk_c * contracts, 0),
            "total_reward_fast": round(reward_fast_c * contracts, 0),
            "confidence": tg.get("confidence"), "calibrated_pct": tg.get("calibrated_pct"),
            "earnings_soon": tg.get("earnings_soon"), "tipo_a_active": tipo_a, "notes": notes,
            "spread": spread, "spread_otm": spread_otm, "recommended": recommended,
            "iv_vs_realized": _iv_regime(iv)[0], "iv_structure_hint": _iv_regime(iv)[1],
            "realized_vol_pct": round(realized_ann * 100, 1) if realized_ann else None,
        })
    if not plans:
        return {"ok": False, "error": "No se pudo estructurar ninguna operación (sin IV/targets válidos)."}
    return _json_safe({
        "ok": True, "ticker": ticker, "spot": round(spot, 2),
        "capital": float(capital), "risk_pct": float(risk_pct), "stop_pct": round(stop_frac * 100, 0),
        "alloc_pct": (round(float(alloc_pct), 1) if alloc_pct is not None else None),
        "sizing_basis": ("kelly_alloc" if alloc_pct is not None else "risk_budget"),
        "bias": t.get("bias"), "conviction": conv, "tipo_a_active": tipo_a,
        "ai_concentration": _ai_concentration(ticker),
        "inst_anchor": inst_overall,
        "flow_exception": ("Flujo Tipo A ($5M+) activo y alineado con el sesgo: tu regla permite mantener pese al "
                           "stop −20/−30% mientras el flujo siga vivo."
                           if tipo_a else "Sin flujo Tipo A ($5M+) alineado: respeta el stop −20/−30% sin excepción."),
        "plans": plans,
        "disclaimer": ("Estructura estimada con Black-Scholes (IV del horizonte, r=4.3%). El strike LARGO se ancla al "
                       "strike institucional con más millones direccionales (ventana 90d, $1M+) y se ajusta a tu regla: "
                       "ATM o ITM, nunca OTM. El valor en el target depende de CUÁNDO llegue (theta): 'rápido' = a mitad "
                       "del horizonte con valor temporal; 'al vencimiento' = solo intrínseco. No es consejo de inversión."),
        "generated_at": datetime.now().strftime('%m/%d/%Y, %I:%M:%S %p')})


@app.get("/api/confluence")
def confluence_endpoint(ticker: str):
    """Confirmation/divergence engine: do the 3 Quant Data pillars agree?
    Convicción (tape ΔOI) + GEX/posicionamiento + dark pool → veredicto + badge + votos."""
    ticker = ticker.upper().strip()
    if not _quantdata_ready():
        return {"ok": False, "error": _quantdata_reason()}
    try:
        gex = get_gex_cached(ticker)
        spot = gex.get("spot") if isinstance(gex, dict) else None
        fl = quantdata_flow(ticker)
        oic = quantdata_oi_change(ticker)
        oimap = oic.get("map") if isinstance(oic, dict) else None
        conv = _qd_conviction(fl, oi_change_map=oimap) if fl else None
        dp = quantdata_darkpool(ticker)
        dpf = quantdata_dark_prints(ticker)                      # buy/sell proxy (directional)
        confl = _qd_confluence(conv, gex, dp, spot, dp_flow=dpf)
        return _json_safe({"ok": True, "ticker": ticker, "spot": spot,
                           "confluence": confl, "conviction": conv, "dark_flow": dpf})
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _income_flow_sells(ticker, dte_max):
    """Ventas de prima institucionales más GRANDES del chain (lado BID/BELOW_BID), agrupadas por
    (exp, cp, strike) sumando premium. Marcan dónde el dinero grande VENDE prima = dónde apuesta a que
    el precio NO llega. Se usan para alinear los cortos de nuestras estructuras. {} si no hay Quant Data."""
    try:
        if not _quantdata_ready():
            return {}
        flow = quantdata_flow_window(ticker, days=max(int(dte_max), 30), min_premium=250_000, max_rows=400) or []
    except Exception:
        return {}
    sells = {}
    for t in flow:
        if "BID" not in str(t.get("side") or "").upper():          # solo ventas (BID / BELOW_BID)
            continue
        cp = str(t.get("cp") or "").upper()
        K = _safe_num(t.get("strike"))
        exp = str(t.get("exp") or "")[:10]
        if K <= 0 or not exp:
            continue
        key = (exp, "CALL" if cp.startswith("C") else "PUT", round(K, 2))
        sells[key] = sells.get(key, 0.0) + abs(_safe_num(t.get("premium")))
    return sells


def build_income_strategies(ticker, dte_min=7, dte_max=30, capital=500.0, risk_pct=50.0):
    """Estructuras de VENTA DE PRIMA (income) sobre expiraciones dte_min–dte_max (por defecto 7–30 DTE):
    Iron Condor, Put Credit Spread (bull put), Call Credit Spread (bear call) y Cash-Secured Put.
    Los cortos se colocan ~1σ (delta≈0.16) FUERA del movimiento esperado (IV × √T), y se alinean con
    los OI walls y con las VENTAS institucionales más grandes del chain. Para cada estructura: crédito,
    máx ganancia/pérdida, breakevens, POP (prob. de ganar, riesgo-neutral desde IV) y retorno sobre riesgo.
    Rankea por POP × RoR. Escanea varias expiraciones de la ventana y elige la mejor."""
    RF = 0.043
    try:
        tk = yf.Ticker(ticker)
        h = tk.history(period="3mo")
        spot = _safe_num(h["Close"].iloc[-1], 0.0)
        exps_all = list(tk.options or [])
    except Exception:
        return {"ok": False, "error": "Sin datos de mercado para el ticker."}
    if spot <= 0 or not exps_all:
        return {"ok": False, "error": "Sin cadena de opciones disponible."}
    now = datetime.now()
    _dte = lambda e: ((datetime.strptime(e, "%Y-%m-%d") - now).days if e else None)
    win = [(e, _dte(e)) for e in exps_all]
    win = [(e, d) for e, d in win if d is not None and dte_min <= d <= dte_max]
    if not win:                                    # sin expiraciones en la ventana → la más cercana al centro
        fut = [(e, _dte(e)) for e in exps_all if (_dte(e) or 0) >= 1]
        fut.sort(key=lambda x: abs((x[1] or 0) - (dte_min + dte_max) // 2))
        win = fut[:1]
    if not win:
        return {"ok": False, "error": "Sin expiraciones utilizables."}
    win.sort(key=lambda x: x[1])
    picks = [win[0], win[len(win) // 2], win[-1]] if len(win) > 3 else win   # muestrea corta/media/larga
    seen = set(); picks = [p for p in picks if not (p[0] in seen or seen.add(p[0]))]
    sells = _income_flow_sells(ticker, dte_max)
    risk_budget = max(0.0, _safe_num(capital) * _safe_num(risk_pct) / 100.0)

    def _p_below(K, T, iv):                         # prob. riesgo-neutral de que S_T < K (lognormal con IV)
        if iv <= 0 or T <= 0 or K <= 0 or spot <= 0:
            return None
        d2 = (math.log(spot / K) + (RF - 0.5 * iv * iv) * T) / (iv * math.sqrt(T))
        return _norm_cdf(-d2)
    _p_above = lambda K, T, iv: (None if _p_below(K, T, iv) is None else 1.0 - _p_below(K, T, iv))

    all_structs, per_exp = [], []
    for exp, dte in picks:
        try:
            ch = tk.option_chain(exp)
        except Exception:
            continue
        T = max(int(dte), 1) / 365.0
        # IV ATM + walls + filas por strike desde UNA sola bajada de cadena
        ivs = []
        for df in (ch.calls, ch.puts):
            if df is None or df.empty:
                continue
            d2 = df.dropna(subset=["impliedVolatility"])
            if d2.empty:
                continue
            idx = (d2["strike"] - spot).abs().idxmin()
            v = float(d2.loc[idx, "impliedVolatility"])
            if 0.01 < v < 5.0:
                ivs.append(v)
        iv = (sum(ivs) / len(ivs)) if ivs else 0.0
        if iv <= 0:
            continue
        em = _expected_move(spot, iv, dte)
        call_oi, put_oi = {}, {}
        calls, puts = {}, {}
        for df, oid, rowd in ((ch.calls, call_oi, calls), (ch.puts, put_oi, puts)):
            if df is None or df.empty:
                continue
            for _, r in df.iterrows():
                K = _safe_num(r.get("strike"))
                if K <= 0:
                    continue
                rowd[round(K, 2)] = r
                oi = _safe_num(r.get("openInterest"))
                if oi > 0:
                    oid[round(K, 2)] = oid.get(round(K, 2), 0.0) + oi
        call_ks = sorted(calls.keys()); put_ks = sorted(puts.keys())
        if len(call_ks) < 3 or len(put_ks) < 3:
            continue
        _ca = {k: v for k, v in call_oi.items() if k >= spot}
        _pb = {k: v for k, v in put_oi.items() if k <= spot}
        call_wall = max(_ca, key=_ca.get) if _ca else None
        put_wall = max(_pb, key=_pb.get) if _pb else None
        gap = 1.0
        if len(call_ks) > 1:
            gaps = [call_ks[i + 1] - call_ks[i] for i in range(len(call_ks) - 1)]
            gap = sorted(gaps)[len(gaps) // 2] if gaps else 1.0

        def _px(rowmap, K, opt):
            r = rowmap.get(round(K, 2))
            if r is None:
                return _bs_price(spot, K, T, iv, RF, opt)
            bid = _safe_num(r.get("bid")); ask = _safe_num(r.get("ask")); last = _safe_num(r.get("lastPrice"))
            if bid > 0 and ask > 0:
                return (bid + ask) / 2.0
            if last > 0:
                return last
            return _bs_price(spot, K, T, iv, RF, opt)
        _dlt = lambda K, opt: _bs_greeks(spot, K, T, iv, RF, opt)["delta"]
        _near = lambda ks, tgt: (min(ks, key=lambda k: abs(k - tgt)) if ks else None)

        def _by_delta(ks, opt, dtarget=0.16):       # strike cuyo |delta| ≈ objetivo (≈ borde 1σ)
            best, bd = None, 1e9
            for k in ks:
                d = abs(abs(_dlt(k, opt)) - dtarget)
                if d < bd:
                    bd, best = d, k
            return best

        put_below = [k for k in put_ks if k < spot]
        call_above = [k for k in call_ks if k > spot]
        short_put = _by_delta(put_below, "put") or _near(put_ks, spot - em)
        short_call = _by_delta(call_above, "call") or _near(call_ks, spot + em)
        # snap hacia el wall si está a ≤1 gap (alinéate con el OI institucional)
        if put_wall and short_put and abs(put_wall - short_put) <= gap * 1.5 and put_wall <= spot:
            short_put = _near(put_ks, put_wall)
        if call_wall and short_call and abs(call_wall - short_call) <= gap * 1.5 and call_wall >= spot:
            short_call = _near(call_ks, call_wall)
        wt = max(gap, round(0.4 * em / gap) * gap) if em else gap * 2   # ancho de ala
        long_put = _near([k for k in put_ks if k < (short_put or spot)], (short_put or spot) - wt) if short_put else None
        long_call = _near([k for k in call_ks if k > (short_call or spot)], (short_call or spot) + wt) if short_call else None

        _sell_at = lambda cp, K: max((v for (e2, c2, k2), v in sells.items()
                                      if e2 == exp and c2 == cp and abs(k2 - K) <= gap * 0.6), default=0.0)

        def _mk(kind, direction, legs, credit, maxloss, be_lo, be_hi, pop, note, collateral=None):
            credit = round(max(0.0, credit), 2); maxloss = round(max(0.01, maxloss), 2)
            ror = credit / maxloss if maxloss > 0 else None
            score = round((pop * 100.0) * (ror or 0), 1) if pop is not None else None
            if collateral:
                contracts = int(_safe_num(capital) // collateral) if collateral > 0 else 0
            else:
                contracts = int(risk_budget // (maxloss * 100.0)) if maxloss > 0 else 0
            return {"kind": kind, "exp": exp, "dte": int(dte), "direction": direction, "iv_pct": round(iv * 100, 1),
                    "legs": legs, "credit": credit, "credit_usd": round(credit * 100, 0),
                    "max_profit_usd": round(credit * 100, 0), "max_loss": maxloss, "max_loss_usd": round(maxloss * 100, 0),
                    "breakeven_low": (round(be_lo, 2) if be_lo else None), "breakeven_high": (round(be_hi, 2) if be_hi else None),
                    "pop_pct": (round(pop * 100, 1) if pop is not None else None),
                    "ror_pct": (round(ror * 100, 1) if ror else None), "score": score,
                    "contracts": max(0, contracts), "collateral_usd": (round(collateral, 0) if collateral else None),
                    "note": note}

        def _leg(action, opt, K):
            return {"action": action, "type": opt, "strike": round(K, 2),
                    "price": round(_px(puts if opt == "put" else calls, K, opt), 2),
                    "delta": round(_dlt(K, opt), 3), "inst_sell_usd": round(_sell_at(opt.upper(), K), 0)}

        structs = []
        # ── Iron Condor (neutral, riesgo definido) ──
        if short_put and short_call and long_put and long_call and short_put > long_put and long_call > short_call:
            cr = (_px(puts, short_put, "put") - _px(puts, long_put, "put")
                  + _px(calls, short_call, "call") - _px(calls, long_call, "call"))
            width = max(short_put - long_put, long_call - short_call)
            ml = width - cr
            be_lo, be_hi = short_put - cr, short_call + cr
            pa, pb2 = _p_below(be_hi, T, iv), _p_below(be_lo, T, iv)
            pop = (pa - pb2) if (pa is not None and pb2 is not None) else None
            structs.append(_mk("Iron Condor", "neutral",
                               [_leg("BUY", "put", long_put), _leg("SELL", "put", short_put),
                                _leg("SELL", "call", short_call), _leg("BUY", "call", long_call)],
                               cr, ml, be_lo, be_hi, pop,
                               "Ganas si el precio se queda ENTRE los cortos al vencimiento. Neutral, riesgo definido por las alas."))
        # ── Put Credit Spread / Bull Put (alcista-neutral) ──
        if short_put and long_put and short_put > long_put:
            cr = _px(puts, short_put, "put") - _px(puts, long_put, "put")
            ml = (short_put - long_put) - cr
            be_lo = short_put - cr
            pop = _p_above(be_lo, T, iv)
            structs.append(_mk("Put Credit Spread", "alcista-neutral",
                               [_leg("BUY", "put", long_put), _leg("SELL", "put", short_put)],
                               cr, ml, be_lo, None, pop,
                               "Vendes soporte: ganas si el precio se mantiene ARRIBA del breakeven. Sesgo alcista-neutral."))
        # ── Call Credit Spread / Bear Call (bajista-neutral) ──
        if short_call and long_call and long_call > short_call:
            cr = _px(calls, short_call, "call") - _px(calls, long_call, "call")
            ml = (long_call - short_call) - cr
            be_hi = short_call + cr
            pop = _p_below(be_hi, T, iv)
            structs.append(_mk("Call Credit Spread", "bajista-neutral",
                               [_leg("SELL", "call", short_call), _leg("BUY", "call", long_call)],
                               cr, ml, None, be_hi, pop,
                               "Vendes resistencia: ganas si el precio se mantiene DEBAJO del breakeven. Sesgo bajista-neutral."))
        # ── Cash-Secured Put (alcista-neutral / quiero las acciones) ──
        if short_put:
            cr = _px(puts, short_put, "put")
            ml = short_put - cr                      # pérdida si el subyacente → 0 (menos el crédito)
            be_lo = short_put - cr
            pop = _p_above(be_lo, T, iv)
            structs.append(_mk("Cash-Secured Put", "alcista-neutral",
                               [_leg("SELL", "put", short_put)],
                               cr, ml, be_lo, None, pop,
                               "Cobras prima por comprometerte a comprar en el corto. Si baja, te asignan a un precio menor; si no, te quedas la prima.",
                               collateral=short_put * 100.0))

        structs = [s for s in structs if s["credit"] > 0]
        for s in structs:
            all_structs.append(s)
        ic = next((s for s in structs if s["kind"] == "Iron Condor"), None)
        per_exp.append({"exp": exp, "dte": int(dte), "iv_pct": round(iv * 100, 1),
                        "expected_move": round(em, 2), "em_low": round(spot - em, 2), "em_high": round(spot + em, 2),
                        "call_wall": call_wall, "put_wall": put_wall,
                        "strategies": sorted(structs, key=lambda s: (s["score"] or 0), reverse=True),
                        "_ic_score": (ic["score"] if ic else -1)})

    if not per_exp:
        return {"ok": False, "error": "No se pudieron construir estructuras (cadena/IV insuficiente en 7–30 DTE)."}
    per_exp.sort(key=lambda x: x["_ic_score"], reverse=True)
    primary = per_exp[0]
    ranked = sorted(all_structs, key=lambda s: (s["score"] or 0), reverse=True)[:8]
    big_sells = sorted(({"exp": e, "cp": c, "strike": k, "premium_usd": round(v, 0)}
                        for (e, c, k), v in sells.items()), key=lambda x: x["premium_usd"], reverse=True)[:8]
    return {"ok": True, "ticker": ticker.upper(), "spot": round(spot, 2),
            "dte_window": [int(dte_min), int(dte_max)], "capital": _safe_num(capital), "risk_pct": _safe_num(risk_pct),
            "primary": primary, "expiries": [{k: v for k, v in pe.items() if k != "_ic_score"} for pe in per_exp],
            "ranked": ranked, "big_sells": big_sells,
            "note": "Cortos ~1σ (delta≈0.16) fuera del movimiento esperado, alineados con OI walls y con las ventas "
                    "institucionales más grandes. POP es riesgo-neutral (desde IV); en la práctica suele salir algo mejor. "
                    "Crédito con mids del chain (fallback Black-Scholes). Riesgo definido salvo el Cash-Secured Put."}


@app.get("/api/income-strategies")
def income_strategies_endpoint(ticker: str, dte_min: int = 7, dte_max: int = 30,
                               capital: float = 500.0, risk_pct: float = 50.0):
    """Estructuras de venta de prima (Iron Condor, credit spreads, CSP) en la ventana 7–30 DTE,
    con crédito, POP, breakevens y retorno sobre riesgo. Motor: build_income_strategies."""
    ticker = (ticker or "").upper().strip()
    if not ticker:
        return {"ok": False, "error": "Ticker requerido."}
    try:
        return _json_safe(build_income_strategies(ticker, dte_min=dte_min, dte_max=dte_max,
                                                  capital=capital, risk_pct=risk_pct))
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/net-flow")
def net_flow_endpoint(ticker: str, window: str = "today", expiration: str = ""):
    """Net call vs put premium over time (net-flow). window: today/7d/30d/90d.
    expiration (YYYY-MM-DD) restringe el flujo a los contratos de esa expiración (0DTE = la del día)."""
    ticker = ticker.upper().strip()
    if window not in ("today", "7d", "30d", "90d"):
        window = "today"
    if not _quantdata_ready():
        return {"ok": False, "error": _quantdata_reason()}
    try:
        exp = (expiration or "").strip()
        nf = quantdata_net_flow(ticker, window, expiration=(exp or None))
        if not nf:
            return {"ok": False, "error": "Sin datos de net-flow para esta ventana."}
        exps = []
        try:
            exps = (quantdata_exposure(ticker, "GAMMA") or {}).get("expirations") or []
        except Exception:
            exps = []
        prints = []
        try:
            _oic = quantdata_oi_change(ticker)          # ΔOI por contrato para confirmar apertura de los prints
            _oimap = (_oic or {}).get("map") if isinstance(_oic, dict) else None
            prints = quantdata_big_prints(ticker, window, expiration=(exp or None), oi_map=_oimap)
        except Exception:
            prints = []
        return _json_safe({"ok": True, "ticker": ticker, "expirations": exps, "prints": prints, **nf})
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/gex-strike")
def gex_strike_endpoint(ticker: str, exp: str = "", greek: str = "GAMMA"):
    """Quant Data exposure by strike for GAMMA / VANNA / CHARM / DELTA, optionally for one expiration.
    Returns by_strike, spot, call/put walls (gamma) and the list of available expirations."""
    ticker = ticker.upper().strip()
    greek = (greek or "GAMMA").upper()
    if greek not in ("GAMMA", "VANNA", "CHARM", "DELTA"):
        greek = "GAMMA"
    if not _quantdata_ready():
        return {"ok": False, "error": _quantdata_reason()}
    try:
        expf = exp if (exp and exp.lower() not in ("all", "todas", "")) else None
        ex = quantdata_exposure(ticker, greek, expiration=expf)
        if not ex or not ex.get("by_strike"):
            return {"ok": False, "error": f"Sin datos de exposición {greek}."}
        spot = ex.get("stock_price")
        if not spot:
            g = get_gex_cached(ticker)
            spot = g.get("spot") if isinstance(g, dict) else None
        # walls/gamma-flip solo tienen sentido en GAMMA
        walls = _qd_gex_walls(ex, _safe_num(spot)) if (spot and greek == "GAMMA") else None
        # #5 — max pain REAL (OI completo de la cadena) para el vencimiento seleccionado
        max_pain = None
        if greek == "GAMMA" and expf and spot:
            try:
                max_pain = _chain_metrics(yf.Ticker(ticker), expf, _safe_num(spot)).get("max_pain")
            except Exception:
                max_pain = None
        if walls is not None:
            walls["max_pain"] = max_pain
        exps = ex.get("expirations") or []
        if expf and not exps:
            base = quantdata_exposure(ticker, greek)
            exps = base.get("expirations") if base else []
        return _json_safe({"ok": True, "ticker": ticker, "greek": greek, "exp": expf or "all", "spot": spot,
                           "by_strike": ex["by_strike"], "walls": walls, "max_pain": max_pain,
                           "expirations": exps})
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ════════════════════════ HISTORICAL COLLECTOR + BACKTEST ════════════════════════
def _collect_signal_snapshot(ticker):
    """Snapshot today's full signal set for a ticker into signal_snapshots (idempotente por día)."""
    ticker = ticker.upper().strip()
    gex = get_gex_cached(ticker)
    spot = _safe_num(gex.get("spot")) if isinstance(gex, dict) else 0.0
    if spot <= 0:
        return {"ok": False, "error": "Sin spot/GEX para capturar el snapshot."}
    qd = _quantdata_ready()
    fl = quantdata_flow(ticker) if qd else None
    oic = quantdata_oi_change(ticker) if qd else None
    oimap = oic.get("map") if isinstance(oic, dict) else None
    conv = _qd_conviction(fl, oi_change_map=oimap) if fl else None
    npm = quantdata_net_premium(ticker) if qd else None
    dp = quantdata_darkpool(ticker) if qd else None
    dpf = quantdata_dark_prints(ticker) if qd else None
    confl = _qd_confluence(conv, gex, dp, spot, dp_flow=dpf)
    targets = []
    try:
        wallsfn = (lambda e, s: _qd_exposure_walls(ticker, e, s)) if qd else None
        t = compute_horizon_targets(ticker, net_premium=npm, flow=fl, ai_12m=0.0,
                                    qd_walls_fn=wallsfn, conviction=conv, calibrate=False)
        for tg in ((t.get("targets") if isinstance(t, dict) else []) or []):
            lvl = _safe_num(tg.get("level")); hz = tg.get("horizon_days")
            if lvl > 0 and hz:
                targets.append({"hz": int(hz), "level": round(lvl, 2), "dir": tg.get("direction")})
    except Exception:
        pass
    row = {"ticker": ticker, "snap_date": datetime.now().strftime("%Y-%m-%d"), "spot": round(spot, 2),
           "confl_verdict": confl.get("verdict"), "confl_direction": confl.get("direction"),
           "confl_score": confl.get("score"),
           "conv_bias": (conv or {}).get("bias"), "conv_strength": (conv or {}).get("strength_pct"),
           "net_premium": (_safe_num(npm.get("net_premium")) if isinstance(npm, dict) else None),
           "dark_bias": (dpf or {}).get("bias"),
           "call_wall": _safe_num(gex.get("call_wall")) or None,
           "put_wall": _safe_num(gex.get("put_wall")) or None,
           "gamma_flip": _safe_num(gex.get("gamma_flip")) or None,
           "targets_json": json.dumps(targets), "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    try:
        _store_signal_snapshot(row)
    except Exception as e:
        return {"ok": False, "error": f"DB: {e}"}
    return {"ok": True, "snapshot": row}


def _store_signal_snapshot(row):
    """INSERT OR REPLACE one snapshot row (shared by the live collector and the historical backfill)."""
    conn = _db()
    conn.execute("""INSERT OR REPLACE INTO signal_snapshots
        (ticker,snap_date,spot,confl_verdict,confl_direction,confl_score,conv_bias,conv_strength,
         net_premium,dark_bias,call_wall,put_wall,gamma_flip,targets_json,created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (row["ticker"], row["snap_date"], row["spot"], row.get("confl_verdict"), row.get("confl_direction"),
         row.get("confl_score"), row.get("conv_bias"), row.get("conv_strength"), row.get("net_premium"),
         row.get("dark_bias"), row.get("call_wall"), row.get("put_wall"), row.get("gamma_flip"),
         row.get("targets_json") or "[]", row.get("created_at")))
    conn.commit(); conn.close()


def _backtest_eval(snaps, closes, highs, lows, dates, fwd_window=10):
    """Pure backtest evaluator (no I/O) so it can be unit-tested.
    - Confluence DIRECTION accuracy over `fwd_window` trading days forward.
    - Target HIT-RATE per horizon (price touches the level within horizon_days calendar days)."""
    def _fwd(d0, n):
        return [d for d in dates if d > d0][:n]
    dir_total = dir_correct = 0
    by_verdict = {}
    for s in snaps:
        direction = s.get("confl_direction")
        if direction not in ("alcista", "bajista"):
            continue
        fwd = _fwd(s["snap_date"], fwd_window)
        if len(fwd) < fwd_window:
            continue
        p0 = closes.get(s["snap_date"]) or _safe_num(s.get("spot"))
        p1 = closes.get(fwd[-1])
        if not p0 or not p1:
            continue
        ret = (p1 - p0) / p0
        ok = (ret > 0 and direction == "alcista") or (ret < 0 and direction == "bajista")
        dir_total += 1; dir_correct += 1 if ok else 0
        bv = by_verdict.setdefault(s.get("confl_verdict") or "?", {"total": 0, "correct": 0})
        bv["total"] += 1; bv["correct"] += 1 if ok else 0
    hz_stats = {}
    last_date = dates[-1] if dates else None
    for s in snaps:
        try:
            tgs = json.loads(s.get("targets_json") or "[]")
        except Exception:
            tgs = []
        for tg in tgs:
            hz = int(_safe_num(tg.get("hz")) or 0); lvl = _safe_num(tg.get("level"))
            if hz <= 0 or lvl <= 0:
                continue
            try:
                cutoff = (datetime.strptime(s["snap_date"], "%Y-%m-%d") + timedelta(days=hz)).strftime("%Y-%m-%d")
            except Exception:
                continue
            if not last_date or cutoff > last_date:        # horizon not elapsed yet → skip
                continue
            window = [d for d in dates if s["snap_date"] < d <= cutoff]
            if not window:
                continue
            p0 = closes.get(s["snap_date"]) or _safe_num(s.get("spot"))
            if not p0:
                continue
            above = lvl >= p0
            hit = any((above and highs.get(d, 0) >= lvl) or ((not above) and lows.get(d, 1e18) <= lvl)
                      for d in window)
            st = hz_stats.setdefault(hz, {"total": 0, "hit": 0})
            st["total"] += 1; st["hit"] += 1 if hit else 0
    dir_acc = round(dir_correct / dir_total * 100, 1) if dir_total else None
    verdict_acc = {v: {"total": x["total"], "accuracy_pct": round(x["correct"] / x["total"] * 100, 1)}
                   for v, x in by_verdict.items() if x["total"]}
    hz_out = {str(h): {"total": st["total"], "hit_rate_pct": round(st["hit"] / st["total"] * 100, 1)}
              for h, st in sorted(hz_stats.items()) if st["total"]}
    return {"confluence_direction": {"evaluated": dir_total, "fwd_days": fwd_window,
                                     "accuracy_pct": dir_acc, "by_verdict": verdict_acc},
            "target_hit_rate": hz_out}


def _backtest_signals(ticker):
    """Read stored snapshots + realized prices (yfinance) and evaluate. Honest: only snapshots
    whose horizon already elapsed count; starts empty and fills as you collect daily."""
    ticker = ticker.upper().strip()
    try:
        conn = _db()
        rows = conn.execute("SELECT * FROM signal_snapshots WHERE ticker=? ORDER BY snap_date", (ticker,)).fetchall()
        conn.close()
        snaps = [dict(r) for r in rows]
    except Exception as e:
        return {"ok": False, "error": f"DB: {e}"}
    if not snaps:
        return {"ok": True, "ticker": ticker, "n_snapshots": 0,
                "message": "Aún no hay snapshots para este ticker. Captura señales unos días y vuelve."}
    try:
        hist = yf.Ticker(ticker).history(period="1y")
        closes = {d.strftime("%Y-%m-%d"): float(c) for d, c in hist["Close"].items()}
        highs = {d.strftime("%Y-%m-%d"): float(c) for d, c in hist["High"].items()}
        lows = {d.strftime("%Y-%m-%d"): float(c) for d, c in hist["Low"].items()}
        dates = sorted(closes.keys())
    except Exception as e:
        return {"ok": False, "error": f"No pude bajar precios realizados: {e}"}
    ev = _backtest_eval(snaps, closes, highs, lows, dates)
    return {"ok": True, "ticker": ticker, "n_snapshots": len(snaps),
            "date_range": [snaps[0]["snap_date"], snaps[-1]["snap_date"]],
            "note": "Solo se evalúan snapshots cuyo horizonte ya transcurrió.", **ev}


@app.get("/api/collect-signals")
def collect_signals_endpoint(ticker: str):
    """Capture today's signal snapshot for forward backtesting."""
    return _json_safe(_collect_signal_snapshot(ticker))


@app.get("/api/backtest")
def backtest_endpoint(ticker: str):
    """Backtest stored snapshots: confluence direction accuracy + target hit-rate per horizon."""
    return _json_safe(_backtest_signals(ticker))


@app.get("/api/signal-history")
def signal_history_endpoint(ticker: str):
    """List stored snapshots for a ticker (status panel)."""
    ticker = ticker.upper().strip()
    try:
        conn = _db()
        rows = conn.execute(
            "SELECT snap_date,spot,confl_verdict,confl_direction,confl_score,conv_bias FROM signal_snapshots "
            "WHERE ticker=? ORDER BY snap_date DESC LIMIT 60", (ticker,)).fetchall()
        conn.close()
        return _json_safe({"ok": True, "ticker": ticker, "count": len(rows), "rows": [dict(r) for r in rows]})
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ──────────────── HISTORICAL BACKFILL (reconstruct past confluence via Quant Data sessionDate) ────────────────
def _reconstruct_confluence_snapshot(ticker, date, spot):
    """Rebuild the confluence as it WOULD have looked on `date` using Quant Data history
    (sessionDate). Direction signals only — target levels need the live options chain, so the
    historical row stores no targets (target hit-rate accrues forward via the live collector)."""
    spot = _safe_num(spot)
    if spot <= 0:
        return None
    fl = quantdata_flow(ticker, session_date=date)
    oic = quantdata_oi_change(ticker, session_date=date)
    oimap = oic.get("map") if isinstance(oic, dict) else None
    conv = _qd_conviction(fl, oi_change_map=oimap) if fl else None
    ex = quantdata_exposure(ticker, "GAMMA", session_date=date)
    walls = _qd_gex_walls(ex, spot) if ex else None
    # Antes se tiraba el gamma_flip que _qd_gex_walls ya calcula y net_gex quedaba en None, dejando
    # el voto GEX en "neutral" salvo ruptura de wall. Ahora los reconstruimos desde la MISMA
    # exposición histórica (sessionDate), igual que en vivo → el histórico se vuelve un test justo.
    _flip_hist = (walls or {}).get("gamma_flip")
    _net_gex_hist = None
    try:
        _rows = (ex.get("by_strike") if isinstance(ex, dict) else None) or []
        if _rows:
            _net_gex_hist = round(sum(_safe_num(r.get("net")) for r in _rows if r.get("strike")), 0)
    except Exception:
        _net_gex_hist = None
    gexd = ({"ok": True, "spot": spot, "call_wall": (walls or {}).get("call_wall"),
             "put_wall": (walls or {}).get("put_wall"), "gamma_flip": _flip_hist, "net_gex": _net_gex_hist}
            if walls else None)
    dpf = quantdata_dark_prints(ticker, session_date=date)
    npm = quantdata_net_premium(ticker, session_date=date)
    confl = _qd_confluence(conv, gexd, None, spot, dp_flow=dpf)
    return {"ticker": ticker.upper(), "snap_date": str(date)[:10], "spot": round(spot, 2),
            "confl_verdict": confl.get("verdict"), "confl_direction": confl.get("direction"),
            "confl_score": confl.get("score"),
            "conv_bias": (conv or {}).get("bias"), "conv_strength": (conv or {}).get("strength_pct"),
            "net_premium": (_safe_num(npm.get("net_premium")) if isinstance(npm, dict) else None),
            "dark_bias": (dpf or {}).get("bias"),
            "call_wall": (walls or {}).get("call_wall"), "put_wall": (walls or {}).get("put_wall"),
            "gamma_flip": _flip_hist, "targets_json": "[]",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " (backfill)"}

_BACKFILL_STATE = {}

def _run_backfill(ticker, sample_every=3, lookback_days=365, throttle=0.4):
    st = _BACKFILL_STATE[ticker]
    try:
        hist = yf.Ticker(ticker).history(period="1y")
        all_dates = [d.strftime("%Y-%m-%d") for d in hist.index]
        closes = {d.strftime("%Y-%m-%d"): float(c) for d, c in hist["Close"].items()}
        if not all_dates:
            st.update(running=False, error="Sin precios históricos."); return
        sampled = all_dates[::max(int(sample_every), 1)]
        # leave a forward tail so the 10-day direction window can be evaluated
        cutoff_idx = len(all_dates) - 11
        sampled = [d for d in sampled if all_dates.index(d) <= cutoff_idx]
        st["total"] = len(sampled)
        for d in sampled:
            if st.get("cancel"):
                break
            try:
                snap = _reconstruct_confluence_snapshot(ticker, d, closes.get(d))
                if snap:
                    _store_signal_snapshot(snap); st["stored"] += 1
            except Exception:
                st["errors"] = st.get("errors", 0) + 1
            st["done"] += 1
            time.sleep(throttle)
        st.update(running=False, finished=True)
    except Exception as e:
        st.update(running=False, error=str(e))


@app.get("/api/backfill/start")
def backfill_start_endpoint(ticker: str, sample_every: int = 3):
    """Kick off a background historical backfill of confluence snapshots (Quant Data sessionDate)."""
    ticker = ticker.upper().strip()
    if not _quantdata_ready():
        return {"ok": False, "error": _quantdata_reason()}
    cur = _BACKFILL_STATE.get(ticker)
    if cur and cur.get("running"):
        return {"ok": True, "already_running": True, **{k: cur.get(k) for k in ("done", "total", "stored")}}
    _BACKFILL_STATE[ticker] = {"running": True, "done": 0, "total": 0, "stored": 0, "errors": 0,
                               "finished": False, "cancel": False, "started": datetime.now().strftime("%H:%M:%S")}
    threading.Thread(target=_run_backfill, args=(ticker, max(int(sample_every), 1)), daemon=True).start()
    return {"ok": True, "started": True, "ticker": ticker,
            "note": "Reconstruyendo histórico en segundo plano. Consulta el progreso."}


@app.get("/api/backfill/status")
def backfill_status_endpoint(ticker: str):
    ticker = ticker.upper().strip()
    st = _BACKFILL_STATE.get(ticker)
    if not st:
        return {"ok": True, "running": False, "total": 0, "done": 0, "stored": 0}
    return _json_safe({"ok": True, **st})


_BACKTEST_CACHE = {}

def _backtest_cached(ticker, ttl=1800):
    ent = _BACKTEST_CACHE.get(ticker.upper())
    if ent and time.time() - ent[0] < ttl:
        return ent[1]
    bt = _backtest_signals(ticker)
    _BACKTEST_CACHE[ticker.upper()] = (time.time(), bt)
    return bt

def _calibration_prompt_block(ticker):
    """Feed the agent its OWN historical track record for this ticker so it calibrates confidence
    to empirical accuracy, not theory. This is the feedback loop that makes it improve over time."""
    try:
        bt = _backtest_cached(ticker)
    except Exception:
        return ""
    if not bt or not bt.get("ok") or not bt.get("n_snapshots"):
        return ""
    cd = bt.get("confluence_direction") or {}
    n_dir = cd.get("evaluated") or 0
    if not n_dir:
        return ""
    byv = cd.get("by_verdict") or {}
    parts = []
    for v, x in byv.items():
        tag = "" if x["total"] >= _BT_MIN_DIR_N else " ⚠n-baja"
        parts.append(f"{v} {x['accuracy_pct']}% (n={x['total']}{tag})")
    hr = bt.get("target_hit_rate") or {}
    hrp = [f"{h}d {x['hit_rate_pct']}% (n={x['total']})" for h, x in hr.items()]
    dir_lo = None
    if cd.get("accuracy_pct") is not None:
        dir_lo = round(_wilson_lower(round(cd["accuracy_pct"] / 100.0 * n_dir), n_dir) * 100, 0)
    rng = bt.get("date_range") or ["", ""]
    reliable = n_dir >= _BT_MIN_DIR_N
    return (
        f"\nCALIBRACIÓN HISTÓRICA (backtest propio de {ticker.upper()}, {bt['n_snapshots']} snapshots "
        f"{rng[0]}→{rng[1]}): acierto direccional de la confluencia a {cd.get('fwd_days')} días = "
        f"{cd.get('accuracy_pct')}% sobre {n_dir} señales"
        + (f" (piso de confianza {dir_lo:.0f}% al 68%)" if dir_lo is not None else "")
        + f". Por veredicto: {', '.join(parts) if parts else 'sin desglose'}."
        + (f" Hit-rate de targets: {', '.join(hrp)}." if hrp else "")
        + (" USO: CALIBRA tu confianza a estos números reales de ESTE ticker, pero trata el PISO (no el % "
           "crudo) como tu probabilidad real, y NO subas convicción con veredictos marcados ⚠n-baja (ruido)."
           if reliable else
           f" ⚠ MUESTRA INSUFICIENTE (n={n_dir} < {_BT_MIN_DIR_N}): el track record todavía NO es "
           "estadísticamente fiable. NO ajustes tu convicción por estos números aún; trátalos como referencia "
           "débil y apóyate en fundamentales/flujo. Captura más snapshots para que la calibración sea confiable."))


def _horizon_targets_prompt_block(ticker, conviction=None, net_premium=None):
    """#4 — feed the SAME gamma/flow target engine that Proyecciones uses into the agent prompt,
    so its narrative and Proyecciones agree (magnet, conflicts, earnings, calibrated confidence)."""
    try:
        wallsfn = (lambda e, s: _qd_exposure_walls(ticker, e, s)) if _quantdata_ready() else None
        t = compute_horizon_targets(ticker, net_premium=net_premium, flow=None, ai_12m=0.0,
                                    qd_walls_fn=wallsfn, conviction=conviction, calibrate=True)
    except Exception:
        return ""
    tgs = (t.get("targets") if isinstance(t, dict) else None) or []
    rows = [x for x in tgs if x.get("horizon_days", 0) < 365 and x.get("level")]
    if not rows:
        return ""
    parts = []
    for x in rows:
        arrow = "↑" if x["direction"] == "up" else "↓"
        tag = []
        if x.get("conflict"):
            tag.append("CONFLICTO flujo-vs-gamma")
        if x.get("earnings_soon"):
            tag.append("earnings en el rango")
        if x.get("capped"):
            tag.append("acotado al mov. esperado")
        extra = (" [" + "; ".join(tag) + "]") if tag else ""
        parts.append(f"{x['label']} {arrow}${x['level']} (conf {x['confidence']}, base {x.get('basis')}{extra})")
    return ("\nTARGETS DE GAMMA/FLUJO (MISMO motor que Proyecciones — niveles-imán de dealers + flujo "
            f"institucional, corto plazo; spot ${t.get('spot')}): " + " · ".join(parts) +
            ". USO: son niveles-imán (gamma) y de flujo, NO el target fundamental. Concílialos con tus "
            "targets de σ/DCF: si coinciden, refuerza la convicción; si un horizonte marca CONFLICTO "
            "flujo-vs-gamma o earnings en el rango, explícalo en tesis_riesgos. No los cambies de número.")


def _ledger_current_oi(ticker, trades, max_expiries=15):
    """Enriquece los trades mostrados con el OI ACTUAL del contrato y su prima vigente. Baja la cadena
    yfinance una vez por cada vencimiento único NO vencido. Agrega oi_now, price_now y
    premium_oi_now = oi_now × price_now × 100. Deja None si no hay (p.ej. contrato ya vencido)."""
    today = datetime.now().date()
    exps = []
    for t in trades:
        e = t.get("exp")
        if not e or e in exps:
            continue
        try:
            if datetime.strptime(e, "%Y-%m-%d").date() < today:
                continue   # contrato vencido → no hay OI actual
        except Exception:
            continue
        exps.append(e)
    exps = exps[:max_expiries]
    if not exps:
        return
    tk = yf.Ticker(ticker)
    cmap = {}
    for e in exps:
        try:
            ch = tk.option_chain(e)
        except Exception:
            continue
        for df, cp in ((getattr(ch, "calls", None), "CALL"), (getattr(ch, "puts", None), "PUT")):
            if df is None or df.empty:
                continue
            for _, r in df.iterrows():
                K = _safe_num(r.get("strike"))
                if K <= 0:
                    continue
                bid = _safe_num(r.get("bid")); ask = _safe_num(r.get("ask")); last = _safe_num(r.get("lastPrice"))
                price = round((bid + ask) / 2, 2) if (bid > 0 and ask > 0) else (last if last > 0 else None)
                cmap[(e, cp, round(K, 2))] = {"oi": int(_safe_num(r.get("openInterest"))), "price": price}
    for t in trades:
        info = cmap.get((t.get("exp"), t.get("cp"), round(_safe_num(t.get("strike")), 2)))
        if info:
            t["oi_now"] = info["oi"] or None
            t["price_now"] = info["price"]
            t["premium_oi_now"] = (round(info["oi"] * info["price"] * 100, 0)
                                   if (info["oi"] and info["price"]) else None)
        else:
            t["oi_now"] = t["price_now"] = t["premium_oi_now"] = None


@app.get("/api/options-ledger")
def options_ledger_endpoint(ticker: str, limit: int = 60, days: int = 120,
                            min_premium: float = 5_000_000, with_oi: bool = True,
                            only_active: bool = True):
    """Libro de transacciones institucionales de los ÚLTIMOS `days` días (default 120), solo trades de
    `min_premium`+ (default $5M) y solo lado direccional (ASK/ABOVE_ASK = compra · BID/BELOW_BID = venta).
    Con only_active=True (default) OCULTA contratos cuya fecha de vencimiento ya pasó: solo muestra flujo de
    contratos vigentes. Por trade: contrato, DTE (entero), nº de contratos, precio (optionPrice), total
    invertido. Más un resumen comprado-vs-vendido. Todo filtrado server-side por Quant Data (timeRange+premiumRange+sides)."""
    ticker = ticker.upper().strip()
    if not _quantdata_ready():
        return {"ok": False, "error": _quantdata_reason()}
    _today = datetime.utcnow().strftime("%Y-%m-%d")
    rows = quantdata_flow_window(ticker, days=int(days), min_premium=float(min_premium))
    if not rows:
        # fallback: sesión más reciente, filtrada del lado del cliente
        base = quantdata_flow(ticker, limit=100) or []
        rows = [r for r in base
                if _safe_num(r.get("premium")) >= float(min_premium)
                and (r.get("side") or "").upper() in ("ASK", "ABOVE_ASK", "BID", "BELOW_BID")]
        for r in rows:
            r["tradeTime"] = None
            r["price"] = (round(_safe_num(r.get("premium")) / (int(_safe_num(r.get("size"))) * 100), 2)
                          if int(_safe_num(r.get("size"))) > 0 else None)
    if not rows:
        return {"ok": True, "ticker": ticker, "trades": [], "summary": None,
                "note": f"Sin trades de ${float(min_premium):,.0f}+ direccionales en {int(days)} días."}

    def lean(side):
        s = (side or "").upper()
        if s in ("ASK", "ABOVE_ASK"):
            return "compra"
        if s in ("BID", "BELOW_BID"):
            return "venta"
        return "neutral"

    trades, summ = [], {"call_buy": 0.0, "call_sell": 0.0, "put_buy": 0.0, "put_sell": 0.0}
    n_expired = 0
    for r in rows:
        _exp = (r.get("exp") or "")[:10]
        if only_active and _exp and _exp < _today:      # contrato ya vencido → no mostrar su flujo
            n_expired += 1
            continue
        prem = _safe_num(r.get("premium"))
        size = int(_safe_num(r.get("size")))
        price = r.get("price")
        if price is None and size > 0 and prem:
            price = round(prem / (size * 100), 2)
        bs = lean(r.get("side"))
        cp = (r.get("cp") or "").upper()
        if prem:
            if cp == "CALL" and bs == "compra":
                summ["call_buy"] += prem
            elif cp == "CALL" and bs == "venta":
                summ["call_sell"] += prem
            elif cp == "PUT" and bs == "compra":
                summ["put_buy"] += prem
            elif cp == "PUT" and bs == "venta":
                summ["put_sell"] += prem
        # fecha legible del trade (la ventana abarca 120 días)
        tt = r.get("tradeTime")
        when = None
        if tt:
            try:
                when = datetime.fromtimestamp(int(tt) / 1000).strftime("%m-%d %H:%M")
            except Exception:
                when = None
        oi = int(_safe_num(r.get("oi")))
        premium_oi = round(oi * price * 100, 0) if (oi > 0 and price) else None   # prima total comprometida en el OI actual
        trades.append({"when": when, "_ts": (int(tt) if tt else 0),
                       "cp": cp, "strike": r.get("strike"), "exp": r.get("exp"),
                       "dte": (int(round(_safe_num(r.get("dte")))) if r.get("dte") is not None else None),
                       "size": size, "price": price, "side": r.get("side"), "buy_sell": bs,
                       "premium": round(prem, 0) if prem else None,
                       "oi": oi or None, "premium_oi": premium_oi, "kind": r.get("kind"),
                       "opening": r.get("opening"), "unusual": r.get("unusual"),
                       "golden": r.get("golden"), "delta": r.get("delta")})
    trades.sort(key=lambda t: (t.get("_ts") or 0), reverse=True)   # más reciente → más viejo
    bull = summ["call_buy"] + summ["put_sell"]
    bear = summ["put_buy"] + summ["call_sell"]
    bias = "alcista" if bull > bear else ("bajista" if bear > bull else "neutral")
    summ = {k: round(v, 0) for k, v in summ.items()}
    summ.update({"net_call": round(summ["call_buy"] - summ["call_sell"], 0),
                 "net_put": round(summ["put_buy"] - summ["put_sell"], 0),
                 "bull_notional": round(bull, 0), "bear_notional": round(bear, 0), "bias": bias})
    shown = trades[:int(limit)]
    if with_oi:
        try:
            _ledger_current_oi(ticker, shown)
        except Exception:
            pass
    return _json_safe({"ok": True, "ticker": ticker, "n": len(trades), "days": int(days),
                       "min_premium": float(min_premium), "only_active": only_active,
                       "n_expired_hidden": n_expired, "trades": shown, "summary": summ})


@app.get("/api/self-test")
def self_test_endpoint(ticker: str):
    """Auto-diagnóstico EN VIVO: corre las rutas de datos clave para un ticker y marca en verde/ámbar/rojo
    lo que se vea raro (sin spot, IV NaN, target con movimiento absurdo, walls invertidos, max pain fuera
    de rango, libro vacío). Pensado para validar contra datos reales antes de operar."""
    ticker = ticker.upper().strip()
    checks = []

    def add(name, status, detail, value=None):
        checks.append({"name": name, "status": status, "detail": detail, "value": _json_safe(value)})

    spot = None
    try:
        gx = get_gex_cached(ticker)
        spot = _safe_num(gx.get("spot")) if isinstance(gx, dict) else None
        if spot and spot > 0:
            _gsrc = (gx.get("source") or "") if isinstance(gx, dict) else ""
            _yfbk = "yfinance" in _gsrc.lower()
            add("Spot / GEX", "warn" if _yfbk else "ok",
                f"spot ${spot:.2f}" + (" · GEX por respaldo yfinance (Quant Data no respondió)" if _yfbk
                                       else " · GEX vía Quant Data"), spot)
            gf = _safe_num(gx.get("gamma_flip")) if isinstance(gx, dict) else None
            if gf and (gf / spot > 1.5 or gf / spot < 0.5):
                add("Gamma flip (computado)", "warn", f"flip ${gf} muy lejos del spot", gf)
        else:
            add("Spot / GEX", "fail", "Sin spot/GEX (yfinance no respondió)")
    except Exception as e:
        add("Spot / GEX", "fail", f"error: {e}")

    qd = _quantdata_ready()
    add("Quant Data", "ok" if qd else "warn", "conectado" if qd else _quantdata_reason())

    if qd:
        try:
            npm = quantdata_net_premium(ticker)
            if npm:
                add("Premium neto (signo)", "ok",
                    f"call ${npm['net_call_premium']:,.0f} − put ${npm['net_put_premium']:,.0f} → {npm['bias']}",
                    npm.get("net_premium"))
            else:
                add("Premium neto", "warn", "sin datos esta sesión")
        except Exception as e:
            add("Premium neto", "fail", f"error: {e}")
        try:
            fl = quantdata_flow(ticker)
            oic = quantdata_oi_change(ticker)
            oimap = oic.get("map") if isinstance(oic, dict) else None
            conv = _qd_conviction(fl, oi_change_map=oimap) if fl else None
            if conv and conv.get("qualifying"):
                add("Convicción (ΔOI)", "ok",
                    f"{conv['bias']} · {conv['strength_pct']}% · {conv['qualifying']} trades", conv.get("strength_pct"))
            else:
                add("Convicción (ΔOI)", "warn", "0 trades calificados (normal en días tranquilos)")
        except Exception as e:
            add("Convicción", "fail", f"error: {e}")
        try:
            ex = quantdata_exposure(ticker, "GAMMA")
            w = _qd_gex_walls(ex, spot) if (ex and spot) else None
            if w:
                cw, pw, gf = w.get("call_wall"), w.get("put_wall"), w.get("gamma_flip")
                inv = bool((cw and cw < spot) or (pw and pw > spot))
                add("Walls de gamma", "warn" if inv else "ok",
                    f"call ${cw} · put ${pw} · flip ${gf}" + (" — INVERTIDOS" if inv else ""), w)
            else:
                add("Walls de gamma", "warn", "sin exposición GAMMA")
        except Exception as e:
            add("Walls de gamma", "fail", f"error: {e}")

    try:
        wallsfn = (lambda e, s: _qd_exposure_walls(ticker, e, s)) if qd else None
        npm2 = quantdata_net_premium(ticker) if qd else None
        conv2 = None
        if qd:
            _fl = quantdata_flow(ticker)
            _oic = quantdata_oi_change(ticker)
            _om = _oic.get("map") if isinstance(_oic, dict) else None
            conv2 = _qd_conviction(_fl, oi_change_map=_om) if _fl else None
        t = compute_horizon_targets(ticker, net_premium=npm2, flow=None, ai_12m=0.0,
                                    qd_walls_fn=wallsfn, conviction=conv2, calibrate=False)
        tgs = [x for x in ((t.get("targets") if isinstance(t, dict) else []) or [])
               if x.get("horizon_days", 0) < 365]
        if not tgs:
            add("Targets", "warn", "sin targets (¿sin cadena de opciones?)")
        else:
            problems = []
            no_iv = sum(1 for x in tgs if x.get("vol_source") != "IV")
            no_mp = sum(1 for x in tgs if not x.get("max_pain"))
            for x in tgs:
                lv = _safe_num(x.get("level"))
                if not lv:
                    problems.append(f"{x['label']} sin nivel"); continue
                mv = abs(lv / spot - 1) if spot else 0
                if x["horizon_days"] <= 30 and mv > 0.5:
                    problems.append(f"{x['label']} {mv*100:.0f}% (grande)")
            det = ("; ".join(problems) if problems else f"{len(tgs)} targets en rango")
            if no_iv:
                det += f" · {no_iv} sin IV (vol histórica)"
            if no_mp:
                det += f" · {no_mp} sin max pain"
            add("Targets", "ok" if not problems else "warn", det,
                [{"l": x["label"], "lvl": x.get("level"), "dir": x.get("direction"),
                  "src": x.get("vol_source")} for x in tgs])
    except Exception as e:
        add("Targets", "fail", f"error: {e}")

    if qd:
        try:
            lg = quantdata_flow_window(ticker, days=120, min_premium=1_000_000)
            add("Libro de flujo (120d, $1M+)", "ok" if lg else "warn",
                f"{len(lg)} trades" if lg else "0 trades — revisa timeRange o permisos del plan", len(lg))
        except Exception as e:
            add("Libro de flujo", "fail", f"error: {e}")

    summ = {"ok": sum(1 for c in checks if c["status"] == "ok"),
            "warn": sum(1 for c in checks if c["status"] == "warn"),
            "fail": sum(1 for c in checks if c["status"] == "fail")}
    return _json_safe({"ok": True, "ticker": ticker, "spot": spot, "summary": summ, "checks": checks})


def _integrity_checks(g, fair_value=None, targets=None):
    """Cedazo de integridad sobre los números YA computados (no re-pega a las APIs): valida que el gamma
    flip esté cerca del dinero, que las walls encierren el spot, que el max pain y el fair value caigan en
    banda razonable, que los P/C tengan sentido y que la fuente no sea un respaldo silencioso. Devuelve una
    lista verde/ámbar/rojo + resumen para pintar una tira de confianza en el reporte y en Proyecciones.
    Convierte 'número silenciosamente malo' (como el flip $3.11) en 'bandera visible'."""
    out = []
    def add(name, status, detail, value=None):
        out.append({"name": name, "status": status, "detail": detail, "value": _json_safe(value)})
    if not isinstance(g, dict):
        return {"checks": [], "ok": 0, "warn": 0, "fail": 1, "status": "fail"}
    spot = _safe_num(g.get("spot"))
    src = (g.get("source") or "")
    # Fuente de datos
    if "yfinance" in src.lower() or "respaldo" in src.lower():
        add("Fuente de datos", "warn", f"GEX por respaldo ({src}) — Quant Data no respondió esta vez")
    else:
        add("Fuente de datos", "ok", src or "Quant Data")
    # Spot
    if spot and spot > 0:
        add("Spot", "ok", f"${spot:,.2f}", spot)
    else:
        add("Spot", "fail", "sin precio de referencia")
        return {"checks": out, "ok": 0, "warn": 0, "fail": 1, "status": "fail"}
    # Gamma flip cerca del dinero
    gf = _safe_num(g.get("gamma_flip"))
    if g.get("gamma_flip") is None:
        add("Gamma flip", "warn", "sin cruce en rango (estructura sin gamma flip)")
    else:
        r = gf / spot
        if 0.85 <= r <= 1.15:
            add("Gamma flip", "ok", f"${gf:,.2f} ({(r-1)*100:+.1f}% del spot)", gf)
        elif 0.75 <= r <= 1.25:
            add("Gamma flip", "warn", f"${gf:,.2f} algo lejos del spot ({(r-1)*100:+.1f}%)", gf)
        else:
            add("Gamma flip", "fail", f"${gf:,.2f} IMPOSIBLE para spot ${spot:,.2f}", gf)
    # Walls encierran el spot
    cw, pw = _safe_num(g.get("call_wall")), _safe_num(g.get("put_wall"))
    if cw and pw:
        if cw >= spot >= pw:
            add("Walls (call/put)", "ok", f"call ${cw:,.0f} encima · put ${pw:,.0f} debajo")
        elif cw < pw:
            add("Walls (call/put)", "fail", f"INVERTIDAS: call ${cw:,.0f} < put ${pw:,.0f}")
        else:
            add("Walls (call/put)", "warn", f"no encierran el spot (call ${cw:,.0f} · put ${pw:,.0f})")
    else:
        add("Walls (call/put)", "warn", "incompletas esta sesión")
    # Max pain en banda
    mp = _safe_num(g.get("max_pain"))
    if mp:
        rm = mp / spot
        add("Max pain", "ok" if 0.7 <= rm <= 1.3 else "warn",
            f"${mp:,.0f} ({(rm-1)*100:+.1f}% del spot)" + ("" if 0.7 <= rm <= 1.3 else " — fuera de banda"), mp)
    # Net GEX (signo/régimen)
    ng = g.get("net_gex")
    if ng is not None:
        add("Net GEX", "ok", f"{_safe_num(ng):,.0f} · {'GEX+ (anclado)' if _safe_num(ng) >= 0 else 'GEX− (amplificado)'}")
    # P/C en rango plausible
    for lbl, key in (("P/C volumen", "pcr_vol"), ("P/C prima", "pcr_premium")):
        v = _safe_num(g.get(key))
        if g.get(key) is not None:
            add(lbl, "ok" if 0.1 <= v <= 10 else "warn",
                f"{v:.2f}" + ("" if 0.1 <= v <= 10 else " — valor atípico"), v)
    # Fair value en banda del precio
    fv = _safe_num(fair_value)
    if fair_value is not None and fv > 0:
        rf = fv / spot
        add("Fair value 12m", "ok" if 0.5 <= rf <= 2.0 else "warn",
            f"${fv:,.2f} ({(rf-1)*100:+.1f}% vs spot)" + ("" if 0.5 <= rf <= 2.0 else " — fuera de banda razonable"), fv)
    # Targets en banda
    if targets:
        try:
            bad = [t for t in targets if (_safe_num(t) > 0 and not (0.5 * spot <= _safe_num(t) <= 2.0 * spot))]
            add("Targets", "ok" if not bad else "warn",
                f"{len(targets)} niveles, todos en banda" if not bad else f"{len(bad)} target(s) fuera de banda 0.5×–2×")
        except Exception:
            pass
    n_fail = sum(1 for c in out if c["status"] == "fail")
    n_warn = sum(1 for c in out if c["status"] == "warn")
    n_ok = sum(1 for c in out if c["status"] == "ok")
    status = "fail" if n_fail else ("warn" if n_warn else "ok")
    return {"checks": out, "ok": n_ok, "warn": n_warn, "fail": n_fail, "status": status}


@app.get("/api/options-gex")
def options_gex(ticker: str, refresh: bool = False):
    """GEX + key levels for one ticker. When a Quant Data API key is configured, augments
    tape_flow / pro-exposure y el dark pool COMPLETO (todos los niveles) de HOY y de los últimos
    30 días. refresh=1 salta el cache de GEX para recalcular en vivo (botón Refrescar / auto-refresh)."""
    g = get_gex_cached(ticker, force=bool(refresh))
    if not g:
        return {"ok": False, "error": "Cadena de opciones no disponible para este ticker."}
    try:
        g = dict(g)
        g["integrity"] = _integrity_checks(g)
    except Exception as _e:
        print(f"[integrity] skip: {_e}")
    if _quantdata_ready():
        try:
            g = dict(g)
            g["tape_flow"] = quantdata_net_premium(ticker)
            g["dark_pool"] = quantdata_darkpool(ticker, limit=500, lookback_days=30)   # todos, 30 días
            g["dark_pool_today"] = quantdata_darkpool(ticker, limit=500, lookback_days=0)  # todos, hoy
            g["exposure_pro"] = quantdata_exposure(ticker)
        except Exception as e:
            print(f"[QuantData] augment skip: {e}")
    return _json_safe(g)


# ── QUANT DATA PROVIDER (options flow + exposure + dark pool) ──────────────────
# Modular institutional-data source chosen over Unusual Whales. Fills the
# dark_pool / tape_flow slots of the GEX engine and feeds the agent's 25% flow
# signal. Set QUANTDATA_API_KEY to activate; everything degrades to None when
# absent, so the platform runs identically with or without it.
QUANTDATA_API_KEY = os.environ.get("QUANTDATA_API_KEY", "")   # <-- pega tu API key de Quant Data
QUANTDATA_BASE    = os.environ.get("QUANTDATA_BASE", "https://api.quantdata.us/v1")
# Endpoint paths centralized. All confirmed from Quant Data's API reference
# (quantdata.us/api). Base = https://api.quantdata.us/v1, all POST with body
# {"sessionDate": "YYYY-MM-DD", "filter": {"ticker": "..."}}.
QUANTDATA_ENDPOINTS = {
    "net_premium": os.environ.get("QD_EP_NETPREMIUM", "/options/tool/net-drift"),
    "flow":        os.environ.get("QD_EP_FLOW",       "/options/tool/order-flow/consolidated"),
    "exposure":    os.environ.get("QD_EP_EXPOSURE",   "/options/tool/exposure-by-strike"),
    "darkpool":    os.environ.get("QD_EP_DARKPOOL",   "/equities/tool/dark-pool-levels"),
    "oi_change":   os.environ.get("QD_EP_OICHANGE",   "/options/tool/open-interest-change"),
    "equity_prints": os.environ.get("QD_EP_PRINTS",   "/equities/tool/equity-prints"),
    "net_flow":    os.environ.get("QD_EP_NETFLOW",    "/options/tool/net-flow"),
    "max_pain":    os.environ.get("QD_EP_MAXPAIN",    "/options/tool/max-pain"),
}

def _quantdata_ready():
    return bool(QUANTDATA_API_KEY)

def _quantdata_reason():
    if not QUANTDATA_API_KEY:
        return "QUANTDATA_API_KEY no configurada. Pega tu API key de Quant Data para activar flow + dark pool."
    return None

def _quantdata_request(path, payload=None, method="POST", timeout=12):
    """Bearer-auth call to the Quant Data API. Returns parsed JSON, or a dict with
    '_error' on failure. Never raises — keeps the agent resilient if the feed is down."""
    if not QUANTDATA_API_KEY:
        return None
    url = QUANTDATA_BASE.rstrip("/") + path
    headers = {"Authorization": f"Bearer {QUANTDATA_API_KEY}", "Content-Type": "application/json"}
    try:
        if method.upper() == "POST":
            r = requests.post(url, json=(payload or {}), headers=headers, timeout=timeout)
        else:
            r = requests.get(url, params=(payload or {}), headers=headers, timeout=timeout)
        if r.status_code != 200:
            return {"_error": f"HTTP {r.status_code}", "_body": (r.text or "")[:300]}
        return r.json()
    except Exception as e:
        return {"_error": str(e)}

def _qd_session_date():
    return datetime.now().strftime("%Y-%m-%d")

# NOTE: sessionDate is intentionally OMITTED on the time-series tools. Per the Quant
# Data docs, when omitted the API uses "the most recent completed trading session" —
# which is exactly what we want and is robust on weekends, holidays (e.g. Juneteenth),
# and before today's close. Forcing today's date returns empty data on non-session days.
def quantdata_net_premium(ticker, session_date=None):
    """Net call/put premium (endpoint /options/tool/net-drift).
    Response: {"data": {ts: {netCallPremium, netPutPremium, stockPrice}}}.
    session_date (YYYY-MM-DD) pulls a historical session for backtesting."""
    _p = {"filter": {"ticker": ticker.upper()}}
    if session_date:
        _p["sessionDate"] = str(session_date)[:10]
    d = _quantdata_request(QUANTDATA_ENDPOINTS["net_premium"], _p)
    if not d or not isinstance(d, dict) or "_error" in d:
        return None
    data = d.get("data") or {}
    if not isinstance(data, dict) or not data:
        return None
    try:
        last_ts = max(data.keys(), key=lambda k: int(k))
        row = data[last_ts] or {}
        ncp = _safe_num(row.get("netCallPremium")); npp = _safe_num(row.get("netPutPremium"))
        # Convención Quant Data: netPutPremium > 0 = COMPRA de puts (bajista); < 0 = venta (alcista).
        # Bullishness = call premium MENOS put premium (no sumar: el signo de puts ya codifica bajista).
        net = ncp - npp
        return {"net_call_premium": round(ncp, 0), "net_put_premium": round(npp, 0),
                "net_premium": round(net, 0), "stock_price": _safe_num(row.get("stockPrice")) or None,
                "bias": "alcista" if net > 0 else ("bajista" if net < 0 else "neutral")}
    except Exception:
        return None

def quantdata_darkpool(ticker, limit=15, lookback_days=30):
    """Off-exchange print activity aggregated BY PRICE LEVEL via /equities/tool/dark-pool-levels.
    Request uses sessionDateRange (startDate required; endDate defaults to tomorrow NY).
    Default lookback = 30 days (accumulation/distribution zones over the last month).
    Response: {"latestStockPrice":x, "data": {"215.00": {notionalValue, size, tradeCount}}}."""
    start = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    d = _quantdata_request(QUANTDATA_ENDPOINTS["darkpool"],
                           {"sessionDateRange": {"startDate": start}, "filter": {"ticker": ticker.upper()}})
    if not d or not isinstance(d, dict) or "_error" in d:
        return None
    data = d.get("data") or {}
    if not isinstance(data, dict) or not data:
        return None
    out = []
    for lvl, cell in data.items():
        if not isinstance(cell, dict):
            continue
        try:
            price = float(lvl)
        except Exception:
            price = _safe_num(cell.get("price"))
        out.append({
            "price": price or None,
            "value": _safe_num(cell.get("notionalValue")) or None,
            "size": int(_safe_num(cell.get("size"))),
            "trade_count": int(_safe_num(cell.get("tradeCount"))),
        })
    out = [x for x in out if x["price"] and x["value"]]
    out.sort(key=lambda x: x["value"], reverse=True)
    return out[:limit] or None

def quantdata_exposure(ticker, greek="GAMMA", representation="PER_ONE_PERCENT_MOVE", expiration=None, session_date=None):
    """Per-strike dealer exposure via /options/tool/exposure-by-strike. greekMode +
    representationMode are REQUIRED by the API (GAMMA/DELTA/VANNA/CHARM). Aggregates the
    exposureMap (expiration -> strike -> {callExposure,putExposure}) into net per strike.
    If `expiration` (YYYY-MM-DD) is given, restricts exposure to that expiry.
    session_date (YYYY-MM-DD) pulls a historical session for backtesting."""
    _filter = {"ticker": ticker.upper()}
    if expiration:
        _filter["expirationDate"] = str(expiration)[:10]
    _payload = {"greekMode": greek, "representationMode": representation, "filter": _filter}
    if session_date:
        _payload["sessionDate"] = str(session_date)[:10]
    d = _quantdata_request(QUANTDATA_ENDPOINTS["exposure"], _payload)
    if not d or not isinstance(d, dict) or "_error" in d:
        return None
    data = d.get("data") or {}
    if not isinstance(data, dict) or not data:
        return None
    tk = data.get(ticker.upper()) or next(iter(data.values()), {})
    if not isinstance(tk, dict):
        return None
    emap = tk.get("exposureMap") or {}
    by_strike = {}
    for _exp, strikes in (emap.items() if isinstance(emap, dict) else []):
        if not isinstance(strikes, dict):
            continue
        for k, cell in strikes.items():
            if not isinstance(cell, dict):
                continue
            K = _safe_num(k)
            if K <= 0:
                continue
            ce = _safe_num(cell.get("callExposure")); pe = _safe_num(cell.get("putExposure"))
            agg = by_strike.setdefault(K, {"call": 0.0, "put": 0.0})
            agg["call"] += ce; agg["put"] += pe
    rows = [{"strike": K, "call": round(v["call"], 0), "put": round(v["put"], 0),
             "net": round(v["call"] + v["put"], 0)} for K, v in sorted(by_strike.items())]
    if not rows:
        return None
    exps_avail = sorted({str(e)[:10] for e in emap.keys()}) if isinstance(emap, dict) else []
    return {"greek": greek, "representation": representation,
            "stock_price": _safe_num(tk.get("stockPrice")) or None,
            "by_strike": rows, "expirations": exps_avail}

_QD_MAXPAIN_CACHE = {}

def _extract_max_pain(d, ticker, expiration=None):
    """Extrae el strike de Max Pain de la respuesta QD sin asumir un único formato (defensivo)."""
    if not d or not isinstance(d, dict) or "_error" in d:
        return None

    def _mp_from(obj):
        if not isinstance(obj, dict):
            return None
        for k in ("maxPain", "max_pain", "maxPainStrike", "maxPainPrice", "maxpain"):
            if obj.get(k) is not None:
                v = _safe_num(obj.get(k))
                return v if v > 0 else None
        return None

    data = d.get("data", d)
    v = _mp_from(data)                                   # caso 1: maxPain directo
    if v:
        return v
    if isinstance(data, dict):
        v = _mp_from(data.get(ticker.upper()))           # caso 2: keyed por ticker
        if v:
            return v
        try:                                             # caso 3: keyed por timestamp → último bucket
            num_keys = [k for k in data.keys() if str(k).isdigit()]
            if num_keys:
                last = data[max(num_keys, key=lambda k: int(k))]
                v = _mp_from(last) if isinstance(last, dict) else (_safe_num(last) or None)
                if v and v > 0:
                    return v
        except Exception:
            pass
        exp_keys = [k for k in data.keys() if isinstance(k, str) and re.match(r"\d{4}-\d{2}-\d{2}", k)]
        if exp_keys:                                     # caso 4: keyed por expiración
            pick = str(expiration)[:10] if (expiration and str(expiration)[:10] in exp_keys) else sorted(exp_keys)[0]
            cell = data.get(pick)
            v = _mp_from(cell) if isinstance(cell, dict) else (_safe_num(cell) or None)
            if v and v > 0:
                return v
        first = next(iter(data.values()), None)          # caso 5: primer valor
        v = _mp_from(first) if isinstance(first, dict) else (_safe_num(first) or None)
        if v and v > 0:
            return v
    return None


def quantdata_max_pain(ticker, expiration=None, session_date=None, ttl=300):
    """Max Pain NATIVO de Quant Data (/options/tool/max-pain), computado server-side sobre el OI
    completo de 18 exchanges. Devuelve el strike (float) o None. Si el endpoint/schema difieren,
    degrada a None y el caller cae a yfinance (sin romper nada)."""
    key = f"{ticker.upper()}|{expiration or ''}|{session_date or ''}"
    nowt = time.time()
    ent = _QD_MAXPAIN_CACHE.get(key)
    if ent and nowt - ent[0] < ttl:
        return ent[1]
    val = None
    try:
        _filter = {"ticker": ticker.upper()}
        if expiration:
            _filter["expirationDate"] = str(expiration)[:10]
        payload = {"filter": _filter}
        if session_date:
            payload["sessionDate"] = str(session_date)[:10]
        val = _extract_max_pain(_quantdata_request(QUANTDATA_ENDPOINTS["max_pain"], payload), ticker, expiration)
    except Exception:
        val = None
    _QD_MAXPAIN_CACHE[key] = (nowt, val)
    return val


def _max_pain_best(ticker, expiration=None, call_oi=None, put_oi=None):
    """Prefiere el Max Pain NATIVO de Quant Data (OI completo, server-side); si no hay, cae al cálculo
    sobre el OI de la cadena yfinance. Devuelve (valor|None, fuente)."""
    if _quantdata_ready():
        qd = quantdata_max_pain(ticker, expiration)
        if qd:
            return qd, "quantdata"
    if call_oi and put_oi:
        mp = _max_pain(call_oi, put_oi)
        if mp:
            return mp, "yfinance"
    return None, None


def _max_pain_per_expiry_best(ticker, expiration, call_oi, put_oi):
    """#7 — Max Pain POR VENCIMIENTO. Prefiere el nativo de Quant Data (OI completo de 18 exchanges,
    server-side) PERO solo si QD realmente honra el filtro `expirationDate`. Lo AUTODETECTA: si el
    max-pain QD del vencimiento concreto difiere del agregado QD (None), entonces QD está respetando
    el filtro → se usa. Si es idéntico (lo ignora) o nulo, cae al cálculo por-vencimiento de yfinance,
    que SÍ varía por expiración. Así nunca degradamos: o mejora con QD real, o queda igual que antes.
    Kill-switch: env QD_MAXPAIN_PER_EXPIRY=0. Devuelve (valor|None, fuente)."""
    yf_mp = _max_pain(call_oi, put_oi) if (call_oi and put_oi) else None
    try:
        if os.environ.get("QD_MAXPAIN_PER_EXPIRY", "1") != "0" and expiration and _quantdata_ready():
            qd_exp = quantdata_max_pain(ticker, expiration)
            if qd_exp:
                qd_agg = quantdata_max_pain(ticker, None)          # cacheado; 1 sola vez por ticker
                if qd_agg is None or abs(float(qd_exp) - float(qd_agg)) > 1e-6:
                    return qd_exp, "quantdata"                     # QD honró expirationDate
    except Exception:
        pass
    return (yf_mp, "yfinance") if yf_mp else (None, None)


def quantdata_flow(ticker, limit=20, session_date=None):
    """Per-trade options flow via /options/tool/order-flow/consolidated (blocks, sweeps, splits).
    Field names match the real schema; delta comes from each row's nested `greeks`.
    session_date (YYYY-MM-DD) pulls a historical session for backtesting."""
    _p = {"filter": {"ticker": ticker.upper()}, "size": max(limit, 50)}
    if session_date:
        _p["sessionDate"] = str(session_date)[:10]
    d = _quantdata_request(QUANTDATA_ENDPOINTS["flow"], _p)
    if not d or not isinstance(d, dict) or "_error" in d:
        return None
    rows = d.get("data") or []
    if isinstance(rows, dict):
        rows = list(rows.values())
    out = []
    for r in (rows or []):
        if not isinstance(r, dict):
            continue
        g = r.get("greeks") or {}
        dlt = g.get("delta")
        vol = _safe_num(r.get("volume"))
        oi_raw = r.get("openInterest", r.get("open_interest", r.get("oi")))
        oi = _safe_num(oi_raw) if oi_raw is not None else None
        unusual = bool(r.get("isUnusual"))
        # "Adds to open interest" = an OPENING trade. Direct proof: the day's volume exceeds
        # the prior open interest (you can't close more contracts than exist → must be opening).
        # Falls back to isUnusual (Quant Data's vol>OI flag) when OI isn't in the payload.
        if oi is not None and vol > 0:
            opening = vol > oi
        else:
            opening = unusual
        out.append({
            "time": r.get("tradeTime"),
            "kind": r.get("tradeConsolidationType") or r.get("tradeType"),   # SWEEP / BLOCK / SPLIT
            "side": r.get("tradeSideCode"),                                   # ABOVE_ASK / BELOW_BID ...
            "cp": r.get("contractType"),                                      # CALL / PUT
            "exp": r.get("expirationDate"),
            "dte": _safe_num(r.get("dte")) or None,
            "strike": _safe_num(r.get("strikePrice")) or None,
            "size": int(_safe_num(r.get("size") or r.get("volume"))),
            "volume": int(vol),
            "open_interest": (int(oi) if oi is not None else None),
            "vol_oi": (round(vol / oi, 2) if (oi and oi > 0) else None),
            "opening": opening,                                               # adds to OI
            "premium": _safe_num(r.get("premium")) or None,
            "delta": (_safe_num(dlt) if dlt is not None else None),
            "spot": _safe_num(r.get("stockPrice")) or None,
            "unusual": unusual,
            "golden": bool(r.get("isGoldenSweep")),
        })
        if len(out) >= limit:
            break
    return out or None


def quantdata_flow_window(ticker, days=120, min_premium=1_000_000, max_rows=300):
    """Order-flow over a multi-day window, FILTERED SERVER-SIDE (one paginated query, no per-day loop):
    timeRange (last `days`), premiumRange ≥ min_premium, and tradeSideCodes restricted to the four
    directional sides (ASK/ABOVE_ASK/BID/BELOW_BID — excludes MID). Uses optionPrice + isOpeningPosition
    straight from the schema. Sorted by tradeTime DESC (most recent first); cursor-paginated up to max_rows."""
    if not _quantdata_ready():
        return []
    end = datetime.utcnow()
    start = end - timedelta(days=int(days))
    iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")
    base = {"filter": {"ticker": ticker.upper(),
                       "premiumRange": {"min": float(min_premium)},
                       "tradeSideCodes": ["ASK", "ABOVE_ASK", "BID", "BELOW_BID"]},
            "timeRange": {"startTime": iso(start), "endTime": iso(end)},
            "size": 100, "sort": {"field": "tradeTime", "direction": "DESCENDING"}}
    out, after = [], None
    for _ in range(max(1, max_rows // 100 + 1)):
        body = dict(base)
        if after:
            body["searchAfter"] = after
        d = _quantdata_request(QUANTDATA_ENDPOINTS["flow"], body)
        if not d or not isinstance(d, dict) or "_error" in d:
            break
        rows = d.get("data") or []
        for r in rows:
            g = r.get("greeks") or {}
            dlt = g.get("delta") if isinstance(g, dict) else None
            out.append({
                "tradeTime": r.get("tradeTime"),
                "kind": r.get("tradeConsolidationType") or r.get("tradeType"),
                "side": r.get("tradeSideCode"),
                "cp": r.get("contractType"),
                "exp": r.get("expirationDate"),
                "dte": _safe_num(r.get("dte")) or None,
                "strike": _safe_num(r.get("strikePrice")) or None,
                "size": int(_safe_num(r.get("size") or r.get("volume"))),
                "oi": int(_safe_num(r.get("openInterest"))),
                "price": _safe_num(r.get("optionPrice")) or None,
                "premium": _safe_num(r.get("premium")) or None,
                "opening": (bool(r.get("isOpeningPosition")) if r.get("isOpeningPosition") is not None
                            else bool(r.get("isVolumeGreaterThanOpenInterest"))),
                "unusual": bool(r.get("isUnusual")),
                "golden": bool(r.get("isGoldenSweep")),
                "delta": (_safe_num(dlt) if dlt is not None else None),
                "spot": _safe_num(r.get("stockPrice")) or None,
            })
        after = d.get("nextSearchAfter")
        if not after or len(out) >= max_rows:
            break
    return out[:max_rows]


def quantdata_oi_change(ticker, limit=100, session_date=None):
    """Per-contract daily open-interest delta via /options/tool/open-interest-change.
    THIS is the real 'added to OI' signal Kevin wants: it compares the open interest the day
    AFTER the trades (currentOpenInterest) vs the day OF the trades (previousOpenInterest) and
    returns the signed changeInOpenInterest. Solves multi-day accumulation: even with 70k already
    open, adding 30k more shows changeInOpenInterest = +30k (vol>OI would have missed it).
    Returns {'map': {key->{change,current,previous,pct}}, 'builds': [top OI builds]} or None.
    key = 'CALL|220.0|2026-05-16' (contractType|strike|expiration)."""
    payload = {
        "filter": {"tickers": [ticker.upper()],
                   "changeInOpenInterestRange": {"min": 1, "max": None}},  # positive deltas = additions
        "size": min(max(int(limit), 1), 100),
        "sort": {"field": "changeInOpenInterest", "direction": "DESCENDING"},
    }
    if session_date:
        payload["sessionDate"] = str(session_date)[:10]
    d = _quantdata_request(QUANTDATA_ENDPOINTS["oi_change"], payload)
    if not d or not isinstance(d, dict) or "_error" in d:
        return None
    rows = d.get("data") or []
    if isinstance(rows, dict):
        rows = list(rows.values())
    omap, builds = {}, []
    for r in (rows or []):
        if not isinstance(r, dict):
            continue
        cp = str(r.get("contractType") or "").upper()
        K = _safe_num(r.get("strikePrice"))
        exp = str(r.get("expirationDate") or "")[:10]
        chg = int(_safe_num(r.get("changeInOpenInterest")))
        cur = int(_safe_num(r.get("currentOpenInterest")))
        prev = int(_safe_num(r.get("previousOpenInterest")))
        pct = _safe_num(r.get("percentChangeInOpenInterest"))
        if not cp or not exp:
            continue
        key = f"{cp}|{round(K, 2)}|{exp}"
        omap[key] = {"change": chg, "current": cur, "previous": prev, "pct": pct}
        builds.append({"cp": cp, "strike": K, "exp": exp, "change": chg,
                       "current": cur, "pct": round(pct * 100, 1) if pct else None})
    if not omap:
        return None
    builds.sort(key=lambda b: b["change"], reverse=True)
    return {"map": omap, "builds": builds[:15]}


def quantdata_dark_prints(ticker, limit=100, session_date=None):
    """Dark-pool BUY/SELL proxy via /equities/tool/equity-prints (printType DARK_POOL).
    tradeSide ASK/ABOVE_ASK = comprador (levanta la oferta, alcista); BID/BELOW_BID = vendedor
    (golpea el bid, bajista); MID_MARKET = neutral. Agrega notional por lado → el dark pool deja de
    ser solo posicional y pasa a ser DIRECCIONAL. Honesto: muchos prints dark imprimen al mid.
    session_date (YYYY-MM-DD) pulls a historical session for backtesting."""
    payload = {"filter": {"ticker": ticker.upper(), "equityPrintTypes": ["DARK_POOL"]},
               "size": min(max(int(limit), 1), 100),
               "sort": {"field": "notionalValue", "direction": "DESCENDING"}}
    if session_date:
        payload["sessionDate"] = str(session_date)[:10]
    d = _quantdata_request(QUANTDATA_ENDPOINTS["equity_prints"], payload)
    if not d or not isinstance(d, dict) or "_error" in d:
        return None
    rows = d.get("data") or []
    if isinstance(rows, dict):
        rows = list(rows.values())
    buy = sell = mid = 0.0
    prints = []
    for r in (rows or []):
        if not isinstance(r, dict):
            continue
        notl = _safe_num(r.get("notionalValue"))
        if notl <= 0:
            continue
        side = str(r.get("tradeSide") or "").upper()
        if side in ("ASK", "ABOVE_ASK"):
            buy += notl; lab = "compra"
        elif side in ("BID", "BELOW_BID"):
            sell += notl; lab = "venta"
        else:
            mid += notl; lab = "mid"
        if len(prints) < 12:
            prints.append({"price": _safe_num(r.get("price")) or None,
                           "size": int(_safe_num(r.get("size"))), "notional": round(notl, 0),
                           "side": side, "lab": lab, "time": r.get("tradeTime")})
    tot = buy + sell + mid
    if tot <= 0:
        return None
    directional = buy + sell
    lean = round((buy - sell) / directional * 100, 0) if directional > 0 else None
    bias = "alcista" if buy > sell * 1.15 else ("bajista" if sell > buy * 1.15 else "neutral")
    return {"buy_notional": round(buy, 0), "sell_notional": round(sell, 0), "mid_notional": round(mid, 0),
            "total_notional": round(tot, 0), "bias": bias, "lean_pct": lean, "prints": prints}


def quantdata_net_flow(ticker, window="today", expiration=None):
    """Net call vs put premium OVER TIME via /options/tool/net-flow (dataMode NET_PREMIUM, en centavos).
    window: 'today' (última sesión, buckets intradía) o '7d'/'30d'/'90d' (timeRange multi-día).
    expiration: 'YYYY-MM-DD' restringe el flujo a los contratos de esa expiración (0DTE = la del día).
    Devuelve serie + neto acumulado + tendencia (acelerando/desvaneciéndose/revirtiendo)."""
    _filter = {"ticker": ticker.upper()}
    if expiration and str(expiration).strip().lower() not in ("", "all", "todas"):
        _filter["expirationDate"] = str(expiration)[:10]
    payload = {"dataMode": "NET_PREMIUM", "filter": _filter}
    wd = {"7d": 7, "30d": 30, "90d": 90}.get(window)
    if wd:
        end = datetime.utcnow()
        start = end - timedelta(days=wd)
        payload["timeRange"] = {"startTime": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                "endTime": end.strftime("%Y-%m-%dT%H:%M:%SZ")}
    d = _quantdata_request(QUANTDATA_ENDPOINTS["net_flow"], payload)
    if not d or not isinstance(d, dict) or "_error" in d:
        return None
    data = d.get("data") or {}
    if not isinstance(data, dict) or not data:
        return None
    series = []
    for ts, cell in data.items():
        if not isinstance(cell, dict):
            continue
        try:
            t = int(ts)
        except Exception:
            continue
        call = _safe_num(cell.get("callSum")) / 100.0      # cents → dollars
        put = _safe_num(cell.get("putSum")) / 100.0
        series.append({"t": t, "call": round(call, 0), "put": round(put, 0),
                       "net": round(call - put, 0), "spot": _safe_num(cell.get("stockPrice")) or None})
    if not series:
        return None
    series.sort(key=lambda x: x["t"])
    call_total = sum(s["call"] for s in series)
    put_total = sum(s["put"] for s in series)
    cum_net = round(call_total - put_total, 0)
    n = len(series)

    def _dir(x):
        return "alcista" if x > 0 else ("bajista" if x < 0 else "neutral")
    if n < 3:
        trend = "datos insuficientes"
    else:
        k = max(n // 3, 1)
        first = sum(s["net"] for s in series[:k]) / k
        last = sum(s["net"] for s in series[-k:]) / k
        if first != 0 and last * first < 0:
            trend = f"revirtiendo a {_dir(last)}"
        elif abs(last) > abs(first) * 1.15:
            trend = f"acelerando ({_dir(last)})"
        elif abs(last) < abs(first) * 0.6:
            trend = "desvaneciéndose"
        else:
            trend = f"estable ({_dir(last)})"
    return {"window": window, "expiration": (expiration or "all"), "series": series, "n": n, "cum_net": cum_net,
            "call_total": round(call_total, 0), "put_total": round(put_total, 0),
            "bias": _dir(cum_net), "trend": trend}


def quantdata_big_prints(ticker, window="today", expiration=None, a_min=5_000_000, b_min=1_000_000, oi_map=None):
    """Prints institucionales grandes para MARCAR sobre la línea de Net Drift, alineados a la ventana.
    Tipo A = transacción ÚNICA ≥ $5M (convicción de un solo golpe). Tipo B = ≥2 transacciones ≥ $1M en el
    MISMO contrato (cp/strike/exp) → acumulación repetida. Devuelve [{t(ms), premium, cp, side, strike, exp,
    type, golden, spot, oi_confirm, oi_change, b_count, b_span_min, b_density}] ordenado por tiempo.
    oi_map (de quantdata_oi_change) confirma si el print ABRIÓ posición (ΔOI>0) o probablemente la cerró (ΔOI<0).
    Tipo B trae densidad temporal: 3 golpes en 20min (density 'alta') pesan mucho más que 3 en 3 días ('baja')."""
    days = {"today": 1, "7d": 7, "30d": 30, "90d": 90}.get(window, 1)
    flow = quantdata_flow_window(ticker, days=days, min_premium=b_min, max_rows=400)
    if not flow:
        return []
    exp_f = str(expiration)[:10] if (expiration and str(expiration).strip().lower() not in ("", "all", "todas")) else None

    def _to_ms(ts):
        if ts is None:
            return None
        try:
            v = float(ts)
            return int(v) if v > 1e11 else int(v * 1000)   # ≥1e11 ya viene en ms; si no, seg→ms
        except Exception:
            pass
        s = str(ts).replace("Z", "").replace("T", " ")[:19]
        import calendar
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                return int(calendar.timegm(time.strptime(s, fmt)) * 1000)   # ISO tratado como UTC (igual que los buckets QD)
            except Exception:
                continue
        return None

    # 1er paso: por contrato, junta los timestamps de trades ≥ b_min → define Tipo B + su densidad temporal
    times = {}
    for t in flow:
        if exp_f and str(t.get("exp") or "")[:10] != exp_f:
            continue
        if abs(_safe_num(t.get("premium"))) >= b_min:
            ms0 = _to_ms(t.get("tradeTime"))
            if ms0 is None:
                continue
            times.setdefault((t.get("cp"), t.get("strike"), t.get("exp")), []).append(ms0)
    bmeta = {}
    for key, ts in times.items():
        if len(ts) >= 2:
            span_min = (max(ts) - min(ts)) / 60000.0
            dens = "alta" if span_min <= 30 else ("media" if span_min <= 1440 else "baja")
            bmeta[key] = {"count": len(ts), "span_min": round(span_min, 1), "density": dens}

    out = []
    for t in flow:
        if exp_f and str(t.get("exp") or "")[:10] != exp_f:
            continue
        prem = abs(_safe_num(t.get("premium")))
        key = (t.get("cp"), t.get("strike"), t.get("exp"))
        typ = "A" if prem >= a_min else ("B" if (prem >= b_min and key in bmeta) else None)
        if not typ:
            continue
        ms = _to_ms(t.get("tradeTime"))
        if ms is None:
            continue
        rec = {"t": ms, "premium": round(prem, 0), "cp": t.get("cp"), "side": t.get("side"),
               "strike": t.get("strike"), "exp": t.get("exp"), "type": typ,
               "golden": bool(t.get("golden")), "spot": t.get("spot")}
        # Confirmación de OI: ¿el print ABRIÓ posición (ΔOI>0) o probablemente la cerró (ΔOI<0)?
        if isinstance(oi_map, dict) and oi_map:
            _cp = str(t.get("cp") or "").upper()
            _cpf = "CALL" if _cp.startswith("C") else ("PUT" if _cp.startswith("P") else _cp)
            oi = oi_map.get(f"{_cpf}|{round(_safe_num(t.get('strike')), 2)}|{str(t.get('exp') or '')[:10]}")
            if oi is not None:
                ch = int(_safe_num(oi.get("change")))
                rec["oi_change"] = ch
                rec["oi_confirm"] = True if ch > 0 else (False if ch < 0 else None)
            else:
                rec["oi_confirm"] = None
        # Densidad temporal del Tipo B
        if typ == "B" and key in bmeta:
            bm = bmeta[key]
            rec["b_count"] = bm["count"]
            rec["b_span_min"] = bm["span_min"]
            rec["b_density"] = bm["density"]
        out.append(rec)
    out.sort(key=lambda x: x["t"])
    return out[:60]


@app.get("/api/quantdata/status")
def quantdata_status():
    """Configured? + a live probe that SURFACES the real HTTP error so failures are diagnosable."""
    if not _quantdata_ready():
        return {"ok": False, "configured": False, "error": _quantdata_reason()}
    probe = _quantdata_request(QUANTDATA_ENDPOINTS["net_premium"], {"filter": {"ticker": "AAPL"}})
    err = probe.get("_error") if isinstance(probe, dict) else None
    body = probe.get("_body") if isinstance(probe, dict) else None
    has_data = bool(isinstance(probe, dict) and probe.get("data"))
    if has_data:
        hint = "Conexión OK · datos recibidos de la última sesión cerrada."
    elif err and "401" in str(err):
        hint = "HTTP 401: API key inválida o no autorizada. Verifica que copiaste bien la key."
    elif err and ("403" in str(err) or "402" in str(err)):
        hint = "HTTP 403/402: la key es válida pero el PLAN API no está activo. Activa el plan API ($149.99/mo) en quantdata.us/pricing?planType=API."
    elif err and "404" in str(err):
        hint = "HTTP 404: ruta incorrecta. Revisa QUANTDATA_ENDPOINTS."
    elif err and ("422" in str(err) or "400" in str(err)):
        hint = "HTTP 422/400: request o sin datos para esa sesión. Hoy (Juneteenth) el mercado de EE.UU. está cerrado; al omitir sessionDate debería usar la última sesión cerrada."
    elif err and "429" in str(err):
        hint = "HTTP 429: límite de tasa (240 req/min). Espera y reintenta."
    else:
        hint = "Sin error HTTP pero sin datos: probablemente fin de semana/feriado sin sesión reciente, o respuesta vacía."
    return {"ok": True, "configured": True, "base": QUANTDATA_BASE,
            "endpoints": QUANTDATA_ENDPOINTS, "live_ping": has_data,
            "error": err, "error_body": body, "hint": hint}


_DH_CACHE = {"ts": 0.0, "data": None}


@app.get("/api/data-health")
def data_health():
    """#5 — Salud de fuentes de datos para el strip persistente: qué está configurado y, en la fuente crítica
    (Quant Data), si está VIVA ahora mismo. Cacheado 90s para no martillar la API. Nunca tradees sobre datos
    stale: si QD sale en rojo, los walls/GEX que ves pueden ser de otra sesión."""
    now = time.time()
    if _DH_CACHE["data"] and now - _DH_CACHE["ts"] < 90:
        return _DH_CACHE["data"]
    try:
        qd = quantdata_status()
    except Exception as e:
        qd = {"configured": bool(QUANTDATA_API_KEY), "live_ping": False, "hint": str(e)[:140]}
    sources = [
        {"key": "quantdata", "label": "Quant Data", "role": "GEX · primaria", "critical": True,
         "configured": bool(qd.get("configured")), "live": (True if qd.get("live_ping") else (None if not qd.get("configured") else False)),
         "note": qd.get("hint") or qd.get("error")},
        {"key": "yfinance", "label": "yfinance", "role": "precio · airbag", "critical": False,
         "configured": True, "live": None, "note": "Sin key · respaldo de precio/cadena"},
        {"key": "gemini", "label": "Gemini", "role": "tesis · IA", "critical": True,
         "configured": bool(API_KEY), "live": None, "note": None if API_KEY else "Falta GEMINI_API_KEY"},
        {"key": "grok", "label": "Grok / xAI", "role": "sentimiento · X", "critical": False,
         "configured": bool(XAI_API_KEY), "live": None, "note": None if XAI_API_KEY else "Falta XAI_API_KEY"},
        {"key": "finnhub", "label": "Finnhub", "role": "insiders · earnings", "critical": False,
         "configured": bool(FINNHUB_API_KEY), "live": None, "note": None if FINNHUB_API_KEY else "Falta FINNHUB_API_KEY"},
        {"key": "openai", "label": "OpenAI", "role": "desempate (opc.)", "critical": False,
         "configured": bool(OPENAI_API_KEY), "live": None, "note": None if OPENAI_API_KEY else "Opcional · sin configurar"},
        {"key": "plaid", "label": "Plaid/SnapTrade", "role": "portafolio", "critical": False,
         "configured": bool(PLAID_CLIENT_ID and PLAID_SECRET), "live": None,
         "note": None if (PLAID_CLIENT_ID and PLAID_SECRET) else "Portafolio por SnapTrade snapshot"},
    ]
    n_crit_down = sum(1 for s in sources if s["critical"] and (not s["configured"] or s["live"] is False))
    out = {"ok": True, "sources": sources, "checked_at": int(now),
           "status": ("down" if n_crit_down else "ok"),
           "qd_live": bool(qd.get("live_ping"))}
    _DH_CACHE.update(ts=now, data=out)
    return out


@app.get("/api/quantdata/flow")
def quantdata_flow_endpoint(ticker: str):
    """Quant Data flow + dark pool + net premium for one ticker (for the agent / UI)."""
    if not _quantdata_ready():
        return {"ok": False, "configured": False, "error": _quantdata_reason()}
    return _json_safe({"ok": True, "ticker": ticker.upper(),
                       "net_premium": quantdata_net_premium(ticker),
                       "dark_pool": quantdata_darkpool(ticker),
                       "flow": quantdata_flow(ticker)})


# ── FINNHUB DATA PROVIDER ─────────────────────────────────────────────────────
# Best "basic" tier: 60 calls/min free — delayed (~15-min) US quotes, fundamentals,
# company news, insider sentiment, congressional trading. Paste your free key below
# (or set the FINNHUB_API_KEY env var). Everything degrades gracefully if unset.
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "")  # <-- pega tu API key gratis de finnhub.io


def finnhub_get(path, params=None):
    if not FINNHUB_API_KEY:
        return None
    try:
        p = dict(params or {}); p["token"] = FINNHUB_API_KEY
        r = requests.get(f"https://finnhub.io/api/v1{path}", params=p, timeout=12)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def finnhub_quote(symbol):
    """Delayed (~15-min) quote. Returns normalized dict or None."""
    d = finnhub_get("/quote", {"symbol": symbol})
    if not d or not d.get("c"):
        return None
    c = float(d.get("c")); pc = float(d.get("pc") or 0)
    chg = (c - pc) if pc else 0.0
    return {
        "price": round(c, 2),
        "change": round(chg, 2),
        "change_pct": round(chg / pc * 100, 2) if pc else None,
        "high": d.get("h"), "low": d.get("l"), "open": d.get("o"), "prev_close": round(pc, 2),
        "ts": d.get("t"),
    }


def _live_spot(ticker):
    """#5 — Spot con respaldo: yfinance fast_info → Finnhub. Evita que un fallo de yfinance
    tumbe watchlist/analyze. Devuelve (precio|None, fuente)."""
    tk = str(ticker).upper().strip()
    try:
        fi = yf.Ticker(tk).fast_info
        p = getattr(fi, "last_price", None) or getattr(fi, "regular_market_price", None)
        if p and float(p) > 0:
            return round(float(p), 2), "yfinance"
    except Exception:
        pass
    try:
        q = finnhub_quote(tk)
        if q and q.get("price"):
            return round(float(q["price"]), 2), "finnhub"
    except Exception:
        pass
    return None, None


_SPOT_RESOLVE_CACHE = {}
def _resolve_spot(ticker, ttl=20):
    """Fuente ÚNICA de precio para todo el sistema: yfinance/Finnhub → Stooq, con etiqueta de FUENTE y HORA.
    Devuelve {price, source, as_of}. Cache corto para que todos los paneles vean el MISMO spot y no haya
    discrepancias sutiles (GEX a un precio, gráfica a otro). Degradación limpia si una fuente cae."""
    tk = str(ticker).upper().strip()
    now = time.time()
    hit = _SPOT_RESOLVE_CACHE.get(tk)
    if hit and now - hit[0] < ttl:
        return hit[1]
    price, source = _live_spot(tk)                 # yfinance fast_info → Finnhub
    if not price:
        try:
            s = _cached_price_series(tk, period="5d") or []
            if s:
                price, source = round(float(s[-1][1]), 2), "stooq (respaldo)"
        except Exception:
            pass
    out = {"price": price, "source": source,
           "as_of": (datetime.now().strftime('%m/%d/%Y, %I:%M:%S %p') if price else None)}
    _SPOT_RESOLVE_CACHE[tk] = (now, out)
    return out


def finnhub_metrics(symbol):
    d = finnhub_get("/stock/metric", {"symbol": symbol, "metric": "all"})
    if not d or "metric" not in d:
        return None
    m = d["metric"] or {}
    keys = {
        "peTTM": "P/E", "psTTM": "P/S", "pbAnnual": "P/B",
        "52WeekHigh": "52WHigh", "52WeekLow": "52WLow", "beta": "Beta",
        "grossMarginTTM": "GrossMargin%", "netProfitMarginTTM": "NetMargin%",
        "roeTTM": "ROE%", "revenueGrowthTTMYoy": "RevGrowthYoY%",
    }
    out = {}
    for k, lbl in keys.items():
        v = m.get(k)
        if isinstance(v, (int, float)):
            out[lbl] = round(float(v), 2)
    return out or None


def finnhub_company_news(symbol, days=7, limit=5):
    to = datetime.now().strftime("%Y-%m-%d")
    frm = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    d = finnhub_get("/company-news", {"symbol": symbol, "from": frm, "to": to})
    if not isinstance(d, list):
        return []
    out = []
    for it in d[:limit]:
        hl = it.get("headline", "")
        if hl:
            out.append({"headline": hl, "source": it.get("source", ""),
                        "summary": (it.get("summary", "") or "")[:200]})
    return out


def finnhub_insider_sentiment(symbol):
    frm = (datetime.now() - timedelta(days=120)).strftime("%Y-%m-%d")
    to = datetime.now().strftime("%Y-%m-%d")
    d = finnhub_get("/stock/insider-sentiment", {"symbol": symbol, "from": frm, "to": to})
    if not d or not d.get("data"):
        return None
    rows = d["data"]
    if not rows:
        return None
    mspr = sum(float(r.get("mspr", 0) or 0) for r in rows) / len(rows)
    net = sum(int(r.get("change", 0) or 0) for r in rows)
    return {"avg_mspr": round(mspr, 1), "net_shares": net, "months": len(rows)}


def finnhub_congressional(symbol):
    frm = (datetime.now() - timedelta(days=180)).strftime("%Y-%m-%d")
    to = datetime.now().strftime("%Y-%m-%d")
    d = finnhub_get("/stock/congressional-trading", {"symbol": symbol, "from": frm, "to": to})
    if not d or not d.get("data"):
        return None
    rows = d["data"]
    buys = sum(1 for r in rows if "purchase" in (r.get("transactionType", "") or "").lower())
    sells = sum(1 for r in rows if "sale" in (r.get("transactionType", "") or "").lower())
    return {"transactions": len(rows), "recent_buys": buys, "recent_sells": sells}


def format_finnhub_context(symbol):
    """Compact text block (fundamentals + news + insider + congress) for the AI prompt."""
    if not FINNHUB_API_KEY:
        return ""
    parts = []
    q = finnhub_quote(symbol)
    if q:
        sign = "+" if (q["change"] or 0) >= 0 else ""
        parts.append(f"Quote 15-min ${q['price']} ({sign}{q['change']}, {q['change_pct']}%)")
    mt = finnhub_metrics(symbol)
    if mt:
        parts.append("Fundamentales: " + ", ".join(f"{k} {v}" for k, v in list(mt.items())[:8]))
    ins = finnhub_insider_sentiment(symbol)
    if ins and ins.get("avg_mspr") is not None:
        bias = "compra" if ins["avg_mspr"] > 0 else "venta"
        parts.append(f"Insider sentiment MSPR {ins['avg_mspr']} (neto {ins['net_shares']:+} acc, sesgo {bias})")
    cong = finnhub_congressional(symbol)
    if cong:
        parts.append(f"Congreso 6m: {cong['transactions']} trades ({cong['recent_buys']} compras / {cong['recent_sells']} ventas)")
    news = finnhub_company_news(symbol, days=7, limit=4)
    if news:
        parts.append("News 7d: " + " | ".join(n["headline"][:80] for n in news))
    return " || ".join(parts) if parts else ""


@app.get("/api/finnhub-quote")
def get_finnhub_quote(symbol: str):
    """Delayed (~15-min) quote from Finnhub. Returns {ok, ...} or graceful error."""
    symbol = symbol.upper().strip()
    if not FINNHUB_API_KEY:
        return {"ok": False, "error": "FINNHUB_API_KEY no configurada."}
    q = finnhub_quote(symbol)
    if not q:
        return {"ok": False, "error": f"Sin quote para {symbol}."}
    return {"ok": True, "symbol": symbol, **q,
            "generated_at": datetime.now().strftime('%m/%d/%Y, %I:%M:%S %p')}


def _reconcile_targets(inst, ht, spot):
    """#5 — reconcilia los DOS motores de targets: σ/DCF (agente) vs gamma/flujo (Proyecciones).
    Por horizonte compara la base del agente con el nivel de gamma/flujo, marca divergencia y consenso."""
    if not ht or not ht.get("targets"):
        return None
    gmap = {int(x.get("horizon_days")): x for x in ht["targets"] if x.get("level") and x.get("horizon_days")}
    pairs = [("7d", 7), ("30d", 30), ("3m", 90), ("6m", 120), ("12m", 365)]
    out = []
    for ik, hd in pairs:
        a = inst.get(ik) or {}
        base = _safe_num(a.get("base"))
        g = gmap.get(hd)
        if base <= 0 or not g:
            continue
        glvl = _safe_num(g.get("level"))
        if glvl <= 0:
            continue
        div = (glvl - base) / base * 100.0
        out.append({"horizon": ik, "agent_base": round(base, 2), "gamma_level": round(glvl, 2),
                    "gamma_dir": g.get("direction"), "gamma_conf": g.get("confidence"),
                    "divergence_pct": round(div, 1), "agree": bool(abs(div) <= 3.0),
                    "consensus": round((base + glvl) / 2, 2)})
    return out or None


def _key_signals_summary(conv, confl, regime, net_premium):
    """#6 — punchline de las señales de MAYOR peso al INICIO del prompt, para que no se diluyan en el
    contexto largo. El LLM ve primero lo que más importa."""
    lines = []
    if conv and conv.get("strength_pct") is not None:
        lines.append(f"• Convicción institucional COMPUTADA (ΔOI real): sesgo {conv.get('bias')} · "
                     f"dominancia {conv.get('strength_pct'):.0f}% · {conv.get('qualifying')} confirmaciones "
                     f"(${conv.get('bull_premium',0):,.0f} calls vs ${conv.get('bear_premium',0):,.0f} puts). "
                     "ESTA es tu señal de mayor peso (25%) — ánclala, no la adivines.")
    else:
        lines.append("• Convicción institucional: sin flujo calificado hoy → puntúa el flujo con cautela.")
    if confl and confl.get("verdict"):
        lines.append(f"• Confluencia (convicción + GEX + dark pool): {confl.get('verdict')}"
                     + (f" ({confl.get('agree_count')}/3 de acuerdo)" if confl.get('agree_count') is not None else "") + ".")
    if net_premium and net_premium.get("bias"):
        lines.append(f"• Premium neto de opciones: {net_premium.get('bias')} (${net_premium.get('net_premium',0):,.0f}).")
    if regime and regime.get("label"):
        lines.append(f"• Régimen de mercado: {regime['label']}"
                     + (f" · VIX {regime.get('vix')}" if regime.get('vix') is not None else "") + ".")
    return "SEÑALES CLAVE (PRIORIZA ESTAS — son el núcleo de la decisión):\n" + "\n".join(lines) if lines else ""


def _agent_coherence_checks(aj, spot):
    """#8 — gate de coherencia sobre la SALIDA del LLM (igual que self-test, pero para la lógica del
    reporte): marca contradicciones internas para que no operes sobre un análisis inconsistente."""
    flags = []
    rec = (aj.get("recommendation") or "").upper()
    cw = _safe_num(aj.get("conviction_weighted"))
    up = aj.get("upside_pct")
    up = _safe_num(up) if up is not None else None
    buyish = any(w in rec for w in ("BUY", "COMPR", "ACUMUL"))
    sellish = any(w in rec for w in ("SELL", "VEND", "REDUC"))
    if buyish and cw and cw < 45:
        flags.append({"check": "Recomendación vs convicción", "status": "warn",
                      "detail": f"{rec} con convicción ponderada baja ({cw:.0f}/100)"})
    if sellish and cw and cw > 60:
        flags.append({"check": "Recomendación vs convicción", "status": "warn",
                      "detail": f"{rec} con convicción alta ({cw:.0f}/100)"})
    if buyish and up is not None and up < 0:
        flags.append({"check": "Recomendación vs fair value", "status": "warn",
                      "detail": f"{rec} pero el fair value implica downside ({up:+.1f}%)"})
    if sellish and up is not None and up > 5:
        flags.append({"check": "Recomendación vs fair value", "status": "warn",
                      "detail": f"{rec} pero el fair value implica upside ({up:+.1f}%)"})
    fa = aj.get("flow_anchor") or {}
    if buyish and fa.get("bias") == "bajista":
        flags.append({"check": "Recomendación vs flujo", "status": "warn",
                      "detail": "Compra con flujo institucional COMPUTADO bajista"})
    if sellish and fa.get("bias") == "alcista":
        flags.append({"check": "Recomendación vs flujo", "status": "warn",
                      "detail": "Venta con flujo institucional COMPUTADO alcista"})
    ss = aj.get("signal_scores") or {}
    scores = [_safe_num((v or {}).get("score")) for v in ss.values() if isinstance(v, dict)]
    if buyish and scores and max(scores) < 45:
        flags.append({"check": "Señales vs recomendación", "status": "warn",
                      "detail": "Compra pero ninguna señal supera 45/100"})
    return flags


# ═════════════════════════════════════════════════════════════════════════════
# FRAMEWORK WBJ — "Warren Buffett Jr" / Ruta 2030 Wall Street Agent System v2.0.0
# 6 especialistas independientes, 100 puntos, evidencia obligatoria.
# Regla innegociable: sin evidencia no hay número; sin número no hay score;
# sin fórmula no hay conclusión. Sin datos → NOT_SCORABLE (nunca 5/10 de relleno).
# Score y confianza son cosas separadas. Fuente: /Cerebro (base de conocimiento).
# ═════════════════════════════════════════════════════════════════════════════

# Cada dimensión ya está expresada en PUNTOS DE CATEGORÍA (suman el máximo de la
# categoría), así: category_points = Σ dim_max * score/10 sobre dims con evidencia.
WBJ_CATEGORIES = {
    "business":  {"max": 20, "label": "Business", "dims": [
        ("moat_pricing_power",            "Moat y pricing power", 5),
        ("competitive_position",          "Posición competitiva", 4),
        ("management_capital_allocation", "Management y asignación de capital", 4),
        ("business_durability",           "Durabilidad del negocio", 4),
        ("customer_economics",            "Economía del cliente", 3)]},
    "financial": {"max": 15, "label": "Financial", "dims": [
        ("revenue_quality_growth",           "Calidad y crecimiento de ingresos", 3),
        ("eps_fcf",                          "EPS y free cash flow", 3),
        ("margins",                          "Márgenes", 3),
        ("balance_liquidity",                "Balance y liquidez", 3),
        ("cash_conversion_capital_efficiency","Conversión de caja y eficiencia de capital", 3)]},
    "market":    {"max": 20, "label": "Market & Growth", "dims": [
        ("tam_tailwind",       "TAM y viento de cola de la industria", 5),
        ("revisions",          "Revisiones de earnings/ingresos", 4),
        ("catalysts",          "Catalizadores de producto/negocio", 4),
        ("growth_runway",      "Pista de crecimiento y captura de share", 4),
        ("operating_leverage", "Apalancamiento operativo y confirmación de mercado", 3)]},
    "technical": {"max": 20, "label": "Technical & Momentum", "dims": [
        ("primary_trend",       "Tendencia primaria de precio", 4),
        ("relative_strength",   "Fuerza relativa", 4),
        ("volume_demand",       "Volumen y demanda institucional", 3),
        ("earnings_gap",        "Comportamiento en gaps de earnings", 3),
        ("breakout_base",       "Calidad de base y breakout", 3),
        ("breadth_volatility",  "Amplitud sectorial y calidad de volatilidad", 3)]},
    "risk":      {"max": 15, "label": "Risk & Resilience", "dims": [
        ("financing_balance_sheet",   "Riesgo de financiamiento y balance", 3),
        ("concentration",             "Riesgo de competencia y concentración", 3),
        ("execution_earnings_quality","Riesgo de ejecución y calidad de earnings", 3),
        ("regulatory_legal_macro",    "Riesgo regulatorio, legal y macro", 2),
        ("valuation_compression",     "Riesgo de compresión de múltiplo", 2),
        ("volatility_drawdown",       "Riesgo de volatilidad y drawdown", 2)]},
    "valuation": {"max": 10, "label": "Valuation", "dims": [
        ("growth_adjusted_multiples","Múltiplos ajustados por crecimiento", 3),
        ("historical_peer",          "Comparación histórica y con pares", 2),
        ("cashflow_earnings_yield",  "Yield de caja y earnings", 2),
        ("fair_value_scenarios",     "Valor justo por escenarios", 2),
        ("margin_of_safety",         "Margen de seguridad", 1)]},
}
WBJ_ORDER = ["business", "financial", "market", "technical", "risk", "valuation"]


class WBJDim(BaseModel):
    score: float = Field(..., description="Score económico 0-10 (10 = máximamente favorable). Usa -1 SOLO si NOT_SCORABLE: no hay evidencia/número para puntuar (jamás rellenes con 5).")
    confidence: int = Field(..., description="Confianza 0-100 en la EVIDENCIA de esta dimensión (cobertura, calidad de fuente, frescura). Separada del score: score alto con evidencia vieja/escasa lleva confianza baja.")
    nota: str = Field(..., description="Una línea citando el dato/número concreto que sustenta el score (p.ej. 'ROIC 18% vs WACC 9% en 5/5 años'). Sin número → declara NOT_SCORABLE.")

class WBJBusiness(BaseModel):
    moat_pricing_power: WBJDim; competitive_position: WBJDim
    management_capital_allocation: WBJDim; business_durability: WBJDim; customer_economics: WBJDim
class WBJFinancial(BaseModel):
    revenue_quality_growth: WBJDim; eps_fcf: WBJDim; margins: WBJDim
    balance_liquidity: WBJDim; cash_conversion_capital_efficiency: WBJDim
class WBJMarket(BaseModel):
    tam_tailwind: WBJDim; revisions: WBJDim; catalysts: WBJDim
    growth_runway: WBJDim; operating_leverage: WBJDim
class WBJTechnical(BaseModel):
    primary_trend: WBJDim; relative_strength: WBJDim; volume_demand: WBJDim
    earnings_gap: WBJDim; breakout_base: WBJDim; breadth_volatility: WBJDim
class WBJRisk(BaseModel):
    financing_balance_sheet: WBJDim; concentration: WBJDim; execution_earnings_quality: WBJDim
    regulatory_legal_macro: WBJDim; valuation_compression: WBJDim; volatility_drawdown: WBJDim
class WBJValuation(BaseModel):
    growth_adjusted_multiples: WBJDim; historical_peer: WBJDim; cashflow_earnings_yield: WBJDim
    fair_value_scenarios: WBJDim; margin_of_safety: WBJDim

class WBJScorecard(BaseModel):
    business: WBJBusiness; financial: WBJFinancial; market: WBJMarket
    technical: WBJTechnical; risk: WBJRisk; valuation: WBJValuation

class WBJScenario(BaseModel):
    scenario: str = Field(..., description="'Bear', 'Base' o 'Bull'.")
    value: float = Field(..., description="Valor intrínseco por acción del escenario (número, no un rango).")
    assumptions: str = Field(..., description="Supuestos declarados: crecimiento y margen asumidos, método (DCF/múltiplo). Sin supuestos, el número no significa nada.")

class WBJLevel(BaseModel):
    tipo: str = Field(..., description="Clase de nivel (p.ej. 'Soporte confirmado', 'Resistencia', 'SMA200', 'Bull intrínseco', 'Reverse-DCF implícito').")
    lente: str = Field(..., description="'Técnico' o 'Valuación'. Nunca se promedian entre sí.")
    valor: float = Field(..., description="Precio del nivel/zona.")
    nota: str = Field(..., description="Confirmación/invalidación o supuesto. Lenguaje de referencia, nunca 'target garantizado'.")

class WBJReport(BaseModel):
    scorecard: WBJScorecard = Field(..., description="Los 6 especialistas puntuados de forma independiente. Cada dimensión 0-10 (o -1 NOT_SCORABLE) + confianza + nota con número.")
    # Resumen ejecutivo (las 7 frases obligatorias del FINAL_REPORT_SCHEMA)
    what_company_does: str = Field(..., description="Qué hace económicamente la compañía, en simple.")
    value_creation_durability: str = Field(..., description="Por qué la creación de valor es o no durable (ROIC vs WACC, moat).")
    growth_funding: str = Field(..., description="Qué está financiando el crecimiento (FCF propio vs capital externo/dilución/deuda).")
    market_validation: str = Field(..., description="Si el mercado valida hoy la tesis (revisiones, técnicos, breadth).")
    price_implied_assumptions: str = Field(..., description="Qué supuestos parece exigir el precio actual (reverse-DCF: crecimiento/margen implícitos).")
    nearest_key_levels: str = Field(..., description="Los soportes/resistencias y referencias de valor intrínseco más cercanos y relevantes.")
    top_invalidation_risk: str = Field(..., description="El único riesgo de invalidación más importante de la tesis.")
    executive_summary: str = Field(..., description="Síntesis ejecutiva de una página, en prosa. La narrativa sigue a los números: cada adjetivo material apunta a una métrica, gate o regla.")
    company_summary_simple: str = Field(..., description="Qué hace la empresa y cómo gana dinero, para cualquier inversor.")
    valuation_scenarios: list[WBJScenario] = Field(..., description="Bear/Base/Bull de valor intrínseco con supuestos declarados. Nunca un único punto.")
    reverse_dcf: str = Field(..., description="Qué crecimiento/retornos implica el precio actual (reverse DCF) y si son plausibles frente a la capacidad evidenciada.")
    thesis_killers: list[str] = Field(..., description="Exactamente 3 'thesis killers': los eventos concretos que romperían la tesis.")
    monitoring_triggers: list[str] = Field(..., description="Disparadores concretos y verificables que obligarían a recalcular (earnings, nivel técnico, revisión de estimados, financiamiento).")
    important_levels: list[WBJLevel] = Field(..., description="Niveles de valuación (intrínsecos) y zonas técnicas por separado. Marca lente Técnico/Valuación en cada uno.")
    revisit_when: str = Field(..., description="Si la clasificación es 'evitar', fecha o evento concreto para revisitar el análisis. Si no aplica, ''.")
    fair_value: float = Field(..., description="Valor justo base (número). Debe ser el escenario Base de valuation_scenarios.")
    fair_value_low: float = Field(..., description="Extremo bajo del rango (escenario Bear de valor intrínseco).")
    fair_value_high: float = Field(..., description="Extremo alto del rango (escenario Bull de valor intrínseco).")
    probabilities: TradeProbabilities = Field(..., description="Probabilidades calibradas ancladas en base-rates (no optimismo). Se usan para dimensionar la posición con Kelly fraccional.")


def _wbj_num(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _wbj_compute(scorecard: dict) -> dict:
    """Recalcula puntos por categoría desde los scores 0-10 de las dimensiones.
    NOT_SCORABLE (score < 0) NO puntúa y baja la cobertura (nunca aporta 5/10).
    Devuelve por categoría: points, max, score10, coverage, confidence (0-100)."""
    out = {"categories": {}, "raw_total": 0.0, "total_confidence": 0.0,
           "coverage_min": 1.0, "incomplete": []}
    for cat_key in WBJ_ORDER:
        spec = WBJ_CATEGORIES[cat_key]
        cat_obj = scorecard.get(cat_key, {}) or {}
        cat_max = float(spec["max"])
        points = 0.0
        scorable_max = 0.0
        conf_num = 0.0
        for dim_key, _label, dim_max in spec["dims"]:
            dim = cat_obj.get(dim_key, {}) if isinstance(cat_obj.get(dim_key), dict) else {}
            sc = _wbj_num(dim.get("score"), -1.0)
            if sc is None or sc < 0:                      # NOT_SCORABLE → no puntúa
                continue
            sc = max(0.0, min(10.0, sc))
            points += dim_max * (sc / 10.0)
            scorable_max += dim_max
            conf_num += dim_max * max(0.0, min(100.0, _wbj_num(dim.get("confidence"), 0.0)))
        coverage = (scorable_max / cat_max) if cat_max > 0 else 0.0
        cat_conf = (conf_num / scorable_max) if scorable_max > 0 else 0.0
        score10 = (10.0 * points / cat_max) if cat_max > 0 else 0.0
        out["categories"][cat_key] = {
            "label": spec["label"], "points": round(points, 2), "max": cat_max,
            "score10": round(score10, 1), "coverage": round(coverage, 2),
            "confidence": round(cat_conf, 0)}
        out["raw_total"] += points
        out["total_confidence"] += cat_max * cat_conf
        out["coverage_min"] = min(out["coverage_min"], coverage)
        if coverage < 0.70:
            out["incomplete"].append(cat_key)
    out["raw_total"] = round(out["raw_total"], 1)
    out["total_confidence"] = round(out["total_confidence"] / 100.0, 0)   # Σ(max*conf)/100
    return out


def _wbj_band(raw: float) -> str:
    if raw >= 90:   return "Elite raw score"
    if raw >= 80:   return "Strong raw score"
    if raw >= 70:   return "Conditional raw score"
    if raw >= 60:   return "Mixed / wait"
    if raw >= 50:   return "Weak"
    return "Avoid on raw score"


def _wbj_gates(comp: dict) -> dict:
    """Aplica overrides obligatorios y gates de perfil (Cerebro/00_main_agent/SCORING_AND_GATES).
    Devuelve el perfil final + gates pasados/fallidos + overrides activados."""
    c = comp["categories"]
    def P(k):  return c.get(k, {}).get("points", 0.0)
    def CV(k): return c.get(k, {}).get("coverage", 0.0)
    def CF(k): return c.get(k, {}).get("confidence", 0.0)
    raw = comp["raw_total"]; tconf = comp["total_confidence"]
    biz, fin, mkt, tec, rsk, val = P("business"), P("financial"), P("market"), P("technical"), P("risk"), P("valuation")
    tech_conf = CF("technical")

    def F(k):  return c.get(k, {}).get("mandatory_flags", []) or []
    biz_flags, fin_flags = F("business"), F("financial")
    # Override 2 de Victor (aggregate/overrides.py): ROIC<WACC (business VALUE_DESTRUCTION
    # y/o financial OVERRIDE_2_ROIC_BELOW_WACC) → NO_ELITE_QUALITY (no Quality Opportunity).
    value_destruction = ("VALUE_DESTRUCTION" in biz_flags) or ("OVERRIDE_2_ROIC_BELOW_WACC" in fin_flags)

    overrides = []
    if rsk <= 4:
        overrides.append("Risk override: Risk ≤4/15 limita el perfil a Speculative.")
    if val <= 4 and tec <= 8:
        overrides.append("Premium breakdown override: Valuation ≤4/10 y Technical ≤8/20 → Wait/Avoid.")
    if value_destruction:
        overrides.append("Override 2 (ROIC<WACC / VALUE_DESTRUCTION): no puede clasificar como "
                         "Quality Opportunity ni Elite (destrucción de valor).")
    if "CONCENTRATION_RED_FLAG" in biz_flags:
        overrides.append("Concentration red flag: un cliente concentra demasiado ingreso "
                         "(Business ya capó la dimensión Durability).")
    if "DILUTION_RED_FLAG" in biz_flags:
        overrides.append("Dilution red flag: dilución material de acciones en circulación.")
    core_incomplete = [k for k in WBJ_ORDER if CV(k) < 0.70]
    if core_incomplete:
        overrides.append("Coverage override: categoría(s) con cobertura <70% no pueden pasar un gate de perfil: "
                         + ", ".join(WBJ_CATEGORIES[k]["label"] for k in core_incomplete) + ".")

    # Gates de perfil (todas las condiciones deben cumplirse) — se registran numéricamente
    gates = {}
    gates["Momentum Candidate"] = [
        ("Raw ≥ 78", raw >= 78), ("Technical ≥ 17/20", tec >= 17),
        ("Market ≥ 16/20", mkt >= 16), ("Business+Financial ≥ 28/35", (biz + fin) >= 28),
        ("Risk ≥ 8/15", rsk >= 8), ("Confianza técnica ≥ 70", tech_conf >= 70)]
    gates["Quality Opportunity"] = [
        ("Raw ≥ 80", raw >= 80), ("Business ≥ 16/20", biz >= 16), ("Financial ≥ 11/15", fin >= 11),
        ("Risk ≥ 10/15", rsk >= 10), ("Valuation ≥ 5/10", val >= 5), ("Technical ≥ 12/20", tec >= 12)]
    gates["Value Opportunity"] = [
        ("Raw ≥ 75", raw >= 75), ("Valuation ≥ 8/10", val >= 8), ("Business ≥ 13/20", biz >= 13),
        ("Risk ≥ 10/15", rsk >= 10), ("Technical ≥ 9/20", tec >= 9)]

    gate_eligible = not core_incomplete            # coverage override bloquea gates mayores
    passed, failed = [], []
    profile = None
    for name in ("Momentum Candidate", "Quality Opportunity", "Value Opportunity"):
        conds = gates[name]
        ok = all(v for _, v in conds)
        # Override 2: la destrucción de valor bloquea específicamente Quality Opportunity.
        _blocked = (name == "Quality Opportunity" and value_destruction)
        (passed if (ok and not _blocked) else failed).append({"gate": name, "conditions": (
            [{"cond": lbl, "pass": bool(v)} for lbl, v in conds]
            + ([{"cond": "Sin destrucción de valor (Override 2)", "pass": False}] if _blocked else []))})
        if ok and not _blocked and gate_eligible and profile is None:
            profile = name

    # Speculative / Avoid / Conditional
    spec_reasons = []
    if rsk <= 4: spec_reasons.append("Risk ≤4/15")
    if tconf < 60: spec_reasons.append("confianza total <60")
    if core_incomplete: spec_reasons.append("categoría crítica incompleta")

    if raw < 50 or (val <= 4 and tec <= 8):
        profile = "Avoid / Wait"
    elif profile is None and spec_reasons:
        profile = "Speculative"
    elif profile is None and raw >= 60:
        profile = "Conditional / Watch"
    elif profile is None:
        profile = "Avoid / Wait"

    # Override de riesgo: cap a Speculative si Risk ≤4 (nunca perfil de calidad)
    if rsk <= 4 and profile in ("Momentum Candidate", "Quality Opportunity", "Value Opportunity"):
        profile = "Speculative"

    # Clasificación de research + recomendación de compatibilidad (persistencia/histórico)
    if profile in ("Momentum Candidate", "Quality Opportunity", "Value Opportunity"):
        classification, rec = "Favorable a invertir", "BUY"
    elif profile == "Conditional / Watch":
        classification, rec = "Condicional — esperar confirmación", "HOLD"
    elif profile == "Speculative":
        classification, rec = "Especulativa — solo tamaño de riesgo", "SPECULATIVE"
    else:
        classification, rec = "Evitar / esperar", "AVOID"

    return {"profile": profile, "band": _wbj_band(raw), "classification": classification,
            "recommendation": rec, "passed_gates": passed, "failed_gates": failed,
            "overrides": overrides, "spec_reasons": spec_reasons,
            "gate_eligible": gate_eligible}


# Metodología WBJ resumida que se inyecta en el prompt (fuente: /Cerebro).
_WBJ_METHODOLOGY = """
FRAMEWORK DE ANÁLISIS WBJ (Ruta 2030 Wall Street Agent System v2.0.0 — base de conocimiento en /Cerebro).
Actúas como 6 especialistas INDEPENDIENTES; ninguno ve el score del otro. Rellena 'scorecard' puntuando
cada dimensión de 0 a 10 (10 = máximamente favorable) con su 'confidence' (0-100, calidad de la EVIDENCIA)
y una 'nota' que CITE EL NÚMERO concreto.

REGLA INNEGOCIABLE: sin evidencia no hay número; sin número no hay score. Si NO tienes datos suficientes
para una dimensión, pon score = -1 (NOT_SCORABLE) y explica por qué en la nota. NUNCA rellenes con 5/10.
Score y confianza son SEPARADOS: un score alto con evidencia vieja/escasa debe llevar confianza baja.

Máximos por categoría y dimensiones (cada dimensión ya está en puntos de categoría):
1) BUSINESS (20): Moat y pricing power (5) · Posición competitiva (4) · Management y asignación de capital (4)
   · Durabilidad del negocio (4) · Economía del cliente (3). No declares moat por marca; exige efectos medibles
   (ROIC-WACC persistente, estabilidad de márgenes, retención/pricing). No uses el precio de la acción aquí.
2) FINANCIAL (15): Calidad y crecimiento de ingresos (3) · EPS y FCF (3) · Márgenes (3) · Balance y liquidez (3)
   · Conversión de caja y eficiencia de capital (3). Usa números reportados; guidance es solo contexto.
   Interest coverage <1.5x = alerta de solvencia. ROIC<WACC impide veredicto 'excelente'.
3) MARKET & GROWTH (20): TAM y viento de cola (5) · Revisiones de earnings/ingresos (4) · Catalizadores (4)
   · Pista de crecimiento y captura de share (4) · Apalancamiento operativo y confirmación (3). No confundas TAM
   con ingresos; catalizador solo narrativo se limita a 3.
4) TECHNICAL & MOMENTUM (20): Tendencia primaria (4) · Fuerza relativa (4) · Volumen y demanda institucional (3)
   · Comportamiento en gaps de earnings (3) · Base y breakout (3) · Amplitud y volatilidad (3). Un chart fuerte
   NO compensa un negocio o solvencia débiles.
5) RISK & RESILIENCE (15): Financiamiento/balance (3) · Competencia/concentración (3) · Ejecución/calidad de
   earnings (3) · Regulatorio/legal/macro (2) · Compresión de múltiplo (2) · Volatilidad/drawdown (2).
   MÁS PUNTOS = MENOR RIESGO. No infieras bajo riesgo por un precio alto. No ocultes coverage <1.5x.
6) VALUATION (10): Múltiplos ajustados por crecimiento (3) · Histórico y pares (2) · Yield de caja/earnings (2)
   · Valor justo por escenarios (2) · Margen de seguridad (1). Nunca un único punto: da Bear/Base/Bull con
   supuestos. Terminal growth < tasa de descuento. Un múltiplo bajo NO es barato sin controlar calidad/riesgo.

Además: escribe las 7 frases del resumen ejecutivo, escenarios de valuación (Bear/Base/Bull con supuestos),
reverse DCF (qué exige el precio hoy), exactamente 3 thesis killers, disparadores de monitoreo, niveles
importantes (marca lente Técnico/Valuación; NUNCA promedies un nivel técnico con un valor intrínseco), y las
probabilidades calibradas. El fair_value debe ser el escenario Base. Lenguaje de referencia: 'zona',
'confirmación', 'invalidación', 'escenario' — nunca 'target garantizado' ni órdenes de compra/venta.
"""


def _wbj_analyze_structured(prompt, temp=0.2):
    """Genera el reporte WBJ estructurado con respaldo de proveedor: Gemini (schema) → OpenAI → Grok."""
    last = None
    for attempt in range(2):
        try:
            r = client_gemini.models.generate_content(
                model="gemini-2.5-flash", contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json", response_schema=WBJReport, temperature=temp))
            return json.loads(r.text), "gemini"
        except Exception as e:
            last = e
            if _is_quota_error(e) and attempt == 0:
                time.sleep(_retry_delay_secs(e))
                continue
            break
    try:
        keys = list(getattr(WBJReport, "model_fields", None) or getattr(WBJReport, "__fields__", {}) or [])
    except Exception:
        keys = []
    for fn, src in ((_openai_json, "openai (ChatGPT)"), (_grok_json, "grok")):
        try:
            return fn(prompt, keys, temp), src
        except Exception as e2:
            last = e2
    raise last if last else RuntimeError("Generación WBJ falló en todos los proveedores")


# ─────────────────────────────────────────────────────────────────────────────
# CAPAS ADITIVAS WBJ — NO alteran ningún número. Calculan datos deterministas
# (con la matemática de Victor) y el LLM SOLO los explica en un 2º pase.
# ─────────────────────────────────────────────────────────────────────────────

def _load_investor_profile():
    """Lee el perfil del inversionista (el mío). Prioriza 'Mi Perfil.md'.
    Devuelve (nombre, texto) o (None, '') si no existe. Solo contexto para la
    explicación; nunca cambia el scoring."""
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Perfil Inversionista")
    for fn in ("Kevin.md", "Mi Perfil.md", "MiPerfil.md", "Perfil.md"):
        p = os.path.join(base, fn)
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return fn.replace(".md", ""), f.read().strip()
            except Exception:
                pass
    return None, ""


def _industry_adapter_hint(info):
    """Mapea sector/industria (yfinance) a un adaptador del Cerebro
    (shared/INDUSTRY_ADAPTERS.md). Solo es una PISTA para que la explicación use
    el lente correcto; no cambia la agregación ni los pesos."""
    sec = (info.get("sector") or "").lower()
    ind = (info.get("industry") or "").lower()
    blob = f"{sec} {ind}"
    if any(k in blob for k in ("bank", "banco")):                 return "Banco"
    if any(k in blob for k in ("insurance", "seguro")):           return "Aseguradora"
    if "reit" in blob or "real estate" in blob:                   return "REIT / inmobiliaria"
    if any(k in blob for k in ("software", "saas", "internet")):  return "Software / SaaS"
    if any(k in blob for k in ("biotech", "pharma", "drug")):     return "Biotech / farma"
    if any(k in blob for k in ("oil", "gas", "mining", "metal", "energy")): return "Commodities / energía"
    if any(k in blob for k in ("auto", "industrial", "materials", "semiconductor")): return "Cíclica"
    return "Empresa estándar (industrial/servicios)"


def _wbj_coherence(comp, gates):
    """Flags de contradicción (Cerebro/00_main_agent/CONTRADICTION_RESOLUTION.md).
    SON INFORMATIVOS: no cambian ni un punto. Solo señalan tensiones para explicar."""
    c = comp["categories"]
    def P(k): return c.get(k, {}).get("points", 0.0)
    flags = []
    raw, tconf = comp["raw_total"], comp["total_confidence"]
    prof = gates["profile"]
    if P("business") >= 16 and P("technical") <= 8:
        flags.append({"tipo": "Negocio fuerte, técnico débil",
                      "detalle": "La calidad puede estar intacta pero el timing no está confirmado (esperar confirmación)."})
    if P("business") <= 8 and P("technical") >= 15:
        flags.append({"tipo": "Negocio débil, técnico fuerte",
                      "detalle": "Liderazgo de precio sin economía durable — momentum especulativo."})
    if P("valuation") >= 8 and P("technical") <= 8:
        flags.append({"tipo": "Valuación atractiva, técnico débil",
                      "detalle": "Posible value trap: barata pero sin confirmación del mercado."})
    if raw >= 75 and tconf < 60:
        flags.append({"tipo": "Score alto, confianza baja",
                      "detalle": "El agregado luce fuerte pero la evidencia es escasa/vieja — trata el número con cautela."})
    if prof in ("Momentum Candidate", "Quality Opportunity", "Value Opportunity") and P("risk") <= 6:
        flags.append({"tipo": "Perfil favorable con riesgo elevado",
                      "detalle": "El agregado puede esconder riesgo de supervivencia; revisa el override de riesgo."})
    if comp.get("incomplete"):
        labs = ", ".join(WBJ_CATEGORIES[k]["label"] for k in comp["incomplete"])
        flags.append({"tipo": "Cobertura incompleta",
                      "detalle": f"Categoría(s) con <70% de evidencia: {labs}. No pueden pasar un gate de perfil."})
    return flags


def _wbj_confluence_zones(levels, atr, price):
    """Zonas de confluencia (Cerebro/00_main_agent/PRICE_LEVEL_SYNTHESIS.md):
    existe confluencia cuando un nivel TÉCNICO y uno de VALUACIÓN se solapan
    dentro de max(0.50*ATR14, 0.75% del precio). NUNCA promedia — solo reporta
    el solape. Fórmula exacta de Victor; no cambia scores."""
    try:
        atr_f = float(atr) if atr not in (None, "N/A", "") else None
    except (TypeError, ValueError):
        atr_f = None
    tol = max((0.5 * atr_f) if atr_f else 0.0, 0.0075 * float(price)) if price else 0.0
    if tol <= 0:
        return []
    tech = [l for l in levels if "técn" in (l.get("lente", "").lower()) or "tecn" in (l.get("lente", "").lower())]
    val = [l for l in levels if "valu" in (l.get("lente", "").lower())]
    zones = []
    for t in tech:
        for v in val:
            try:
                tv, vv = float(t["valor"]), float(v["valor"])
            except (TypeError, ValueError, KeyError):
                continue
            if abs(tv - vv) <= tol:
                mid = round((tv + vv) / 2, 2)
                zones.append({
                    "zona": mid, "tolerancia": round(tol, 2),
                    "tecnico": {"tipo": t.get("tipo"), "valor": tv},
                    "valuacion": {"tipo": v.get("tipo"), "valor": vv},
                    "dist_pct": round((mid - float(price)) / float(price) * 100, 1) if price else None})
    zones.sort(key=lambda z: abs(z.get("dist_pct") or 999))
    return zones


def _wbj_read_thesis_md(ticker):
    """Lee Memoria/tesis/<TICKER>.md (tesis previa) para el prompt. '' si no hay."""
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Memoria", "tesis", f"{ticker.upper()}.md")
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            return ""
    return ""


def _wbj_write_thesis_md(ticker, price, profile, raw, fair_value, targets, thesis, invalidation):
    """Escribe/actualiza Memoria/tesis/<TICKER>.md (protocolo de memoria del CLAUDE.md).
    Corrige encima; no borra la tesis vieja (la apila como historial). Best-effort."""
    try:
        base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Memoria", "tesis")
        os.makedirs(base, exist_ok=True)
        p = os.path.join(base, f"{ticker.upper()}.md")
        fecha = datetime.now().strftime("%Y-%m-%d %H:%M")
        t12 = (targets or {}).get("12m", {}) or {}
        prev = ""
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                prev = f.read()
        entry = (f"## {fecha} — perfil {profile} · raw {raw}/100\n"
                 f"- Precio al análisis: ${price} · Fair value (base): ${fair_value}\n"
                 f"- Targets 12M: Bull ${t12.get('bull')} / Base ${t12.get('base')} / Bear ${t12.get('bear')}\n"
                 f"- Tesis: {(thesis or '').strip()[:600]}\n"
                 f"- Invalidación: {invalidation}\n\n")
        with open(p, "w", encoding="utf-8") as f:
            f.write(f"# Tesis — {ticker.upper()}\n\n{entry}{prev}")
        # índice
        idx = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Memoria", "MEMORIA.md")
        line = f"- {ticker.upper()} · {fecha} · {profile} · raw {raw}/100 · FV ${fair_value}\n"
        with open(idx, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        print(f"[Memoria] no se pudo escribir tesis {ticker}: {e}")


def _wbj_write_prediccion(ticker, report_id, price, fair_value, profile, raw, targets, recommendation):
    """Guarda Reportes/<TICKER>/<fecha>/prediccion.json (para el track record).
    Nunca se edita luego. Best-effort."""
    try:
        fecha = datetime.now().strftime("%Y-%m-%d")
        base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Reportes", ticker.upper(), fecha)
        os.makedirs(base, exist_ok=True)
        payload = {"report_id": report_id, "ticker": ticker.upper(), "fecha": fecha,
                   "price_at_analysis": price, "fair_value": fair_value, "profile": profile,
                   "raw_total": raw, "recommendation": recommendation,
                   "targets_12m": (targets or {}).get("12m", {}), "framework": "WBJ v2.0.0"}
        with open(os.path.join(base, "prediccion.json"), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[Predicción] no se pudo escribir {ticker}: {e}")


class WBJExplanation(BaseModel):
    resumen_simple: str = Field(..., description="En 3-5 frases y en español MUY simple: qué es esta empresa como inversión y qué dice el veredicto. Para alguien sin conocimientos financieros.")
    por_categoria: str = Field(..., description="Explica en palabras qué significa el puntaje de CADA una de las 6 categorías (business, financial, market, technical, risk, valuation) y por qué está alto o bajo, citando las notas. Detallado pero simple.")
    gates_y_perfil: str = Field(..., description="Explica qué significa el perfil asignado (Momentum/Quality/Value/Conditional/Speculative/Avoid), qué gates pasaron o fallaron y qué implican, en palabras llanas.")
    overrides_y_coherencia: str = Field(..., description="Explica qué significan los overrides activados y los flags de coherencia (contradicciones) listados, y qué debería vigilar el inversionista.")
    niveles_y_confluencia: str = Field(..., description="Explica los niveles importantes (técnicos vs valuación) y las zonas de confluencia detectadas: qué son, por qué importan y cómo leerlas. Recuerda que no se promedian.")
    ajuste_a_mi_perfil: str = Field(..., description="Explica cómo encaja (o no) esta inversión con MI perfil (horizonte 1-3a + opciones corto plazo + ingresos, agresivo/especulativo, acciones/ETF/opciones, ~$1,000), incluido el riesgo de sizing con capital pequeño.")
    calibracion: str = Field(..., description="Explica en palabras qué dice mi track record/calibración histórica (si hay) y cómo tomar la confianza del veredicto. Si no hay historial, dilo.")
    conclusion: str = Field(..., description="La conclusión final en 1-2 frases, honesta y sin promesas de retorno.")


def _wbj_explain(context_text, temp=0.3):
    """2º PASE: el LLM SOLO explica el paquete ya calculado en palabras simples.
    Recibe los números FINALES (matemática de Victor) y NO los cambia. Respaldo de
    proveedor igual que el análisis. Devuelve (dict, fuente) o (None, None) si falla."""
    prompt = (
        "Eres un divulgador financiero. Abajo tienes un análisis WBJ YA CALCULADO con la "
        "metodología de Victor (los números son FINALES y correctos). Tu ÚNICO trabajo es "
        "EXPLICARLO en español simple, claro y detallado para el inversionista de 'MI PERFIL'. "
        "NO recalcules, NO cambies, NO reduzcas ni 'corrijas' ningún número, score, gate ni nivel. "
        "Si algo no tiene datos (NOT_SCORABLE), explícalo con honestidad. No prometas retornos ni "
        "des órdenes de compra/venta.\n\n" + context_text)
    last = None
    for attempt in range(2):
        try:
            r = client_gemini.models.generate_content(
                model="gemini-2.5-flash", contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json", response_schema=WBJExplanation, temperature=temp))
            return json.loads(r.text), "gemini"
        except Exception as e:
            last = e
            if _is_quota_error(e) and attempt == 0:
                time.sleep(_retry_delay_secs(e)); continue
            break
    try:
        keys = list(getattr(WBJExplanation, "model_fields", None) or getattr(WBJExplanation, "__fields__", {}) or [])
    except Exception:
        keys = []
    for fn, src in ((_openai_json, "openai (ChatGPT)"), (_grok_json, "grok")):
        try:
            return fn(prompt, keys, temp), src
        except Exception as e2:
            last = e2
    return None, None


# ── ENGINE DETERMINISTA DE VICTOR (sin LLM) para los scores de las 6 categorías ──
_WBJ_ENGINE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "engine")


def _compute_earnings_gaps(cal, dates, opens, closes):
    """TECH-GAP-020 / TECH-GHOLD-021: gap de earnings y hold a 5 sesiones, mapeando
    las fechas de release de FMP a las sesiones OHLCV. 'amc' = gap en la sesión
    siguiente; 'bmo' = gap en la sesión del día. Best-effort."""
    if not cal or not dates:
        return []
    out = []
    for ev in cal:
        d = ev.get("date"); t = (ev.get("time") or "").lower()
        if not d:
            continue
        pos = next((i for i, ds in enumerate(dates) if ds >= d), None)
        if pos is None:
            continue
        if t == "amc":
            base = pos if (pos < len(dates) and dates[pos] == d) else pos - 1
            if base < 0 or base + 1 >= len(dates):
                continue
            prior_close = closes[base]; gap_open = opens[base + 1]; k = base + 1
        else:  # bmo / desconocido
            if pos - 1 < 0:
                continue
            prior_close = closes[pos - 1]; gap_open = opens[pos]; k = pos
        if not prior_close or prior_close <= 0 or (gap_open - prior_close) == 0:
            continue
        gap = (gap_open - prior_close) / prior_close
        hold5 = None
        if k + 5 < len(closes):
            hold5 = (closes[k + 5] - prior_close) / (gap_open - prior_close)
        out.append({"gap": round(gap, 4), "hold5": round(hold5, 3) if hold5 is not None else None})
    return out


def _fmp_earnings_surprise(cal):
    """MKT-SURP-014: sorpresa media de EPS de los últimos ~4 trimestres reportados."""
    sur = []
    for ev in (cal or []):
        a = ev.get("eps"); e = ev.get("epsEstimated")
        if a is not None and e not in (None, 0):
            sur.append((a - e) / abs(e))
        if len(sur) >= 4:
            break
    return (sum(sur) / len(sur)) if sur else None


def _fmp_forward_estimates(rows):
    """De FMP analyst_estimates: crecimiento forward de EPS + dispersión + # analistas."""
    rows = [r for r in (rows or []) if r.get("estimatedEpsAvg") is not None]
    rows = sorted(rows, key=lambda r: r.get("date", ""))
    eps_growth = dispersion = analysts = None
    if len(rows) >= 2 and rows[0]["estimatedEpsAvg"] and rows[0]["estimatedEpsAvg"] > 0:
        eps_growth = rows[1]["estimatedEpsAvg"] / rows[0]["estimatedEpsAvg"] - 1.0
    if rows:
        r0 = rows[0]
        lo, hi, av = r0.get("estimatedRevenueLow"), r0.get("estimatedRevenueHigh"), r0.get("estimatedRevenueAvg")
        if lo and hi and av:
            dispersion = (hi - lo) / abs(av)
        analysts = r0.get("numberAnalystEstimatedRevenue")
    return eps_growth, dispersion, analysts


def _wbj_extract_business_qual(ticker, cik, settings):
    """Extrae del 10-K real (SEC EDGAR) los inputs CUALITATIVOS que el especialista
    Business de Victor lee por `overlay` y que NO están en FMP ni son slots del judge:
    recurring_revenue, largest_customer_share, customer_shares, retention (NRR/GRR),
    churn, customer_economics (arpu/ltv/cac/payback) y guidance_history.

    Fiel al sub-agente `business-analysis` de Victor: Claude LEE el filing y devuelve
    SOLO lo que la empresa divulga explícitamente; si no está, null → la métrica queda
    N/S ('sin evidencia, no hay número'). Nunca inventa. Cualquier fallo → {} (el
    análisis sigue igual). Devuelve el dict de overlay ya en la forma que espera Victor."""
    key = getattr(settings, "anthropic_api_key", None)
    if not key or not cik:
        return {}
    try:
        import httpx, json as _json, re as _re
        from wbj.providers.edgar import EDGAR_USER_AGENT
        _hdr = {"User-Agent": EDGAR_USER_AGENT}
        # 1) localizar el último 10-K (accession + documento primario) en submissions
        _sub = httpx.get(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json",
                         headers=_hdr, timeout=20.0)
        _sub.raise_for_status()
        _rec = (_sub.json().get("filings", {}) or {}).get("recent", {}) or {}
        _forms = _rec.get("form", []); _accs = _rec.get("accessionNumber", []); _docs = _rec.get("primaryDocument", [])
        _idx = next((i for i, f in enumerate(_forms) if f == "10-K"), None)
        if _idx is None:
            return {}
        _acc = _accs[_idx].replace("-", ""); _doc = _docs[_idx]
        # 2) bajar el documento y limpiarlo a texto plano
        _u = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{_acc}/{_doc}"
        _r = httpx.get(_u, headers=_hdr, timeout=30.0); _r.raise_for_status()
        _txt = _re.sub(r"<[^>]+>", " ", _r.text)
        _txt = _re.sub(r"&#\d+;|&[a-z]+;", " ", _txt)
        _txt = _re.sub(r"\s+", " ", _txt).strip()[:180000]   # cap de contexto
        # 3) extracción con Claude — SOLO lo divulgado, si no null
        import anthropic
        _client = anthropic.Anthropic(api_key=key)
        _sys = ("Eres un analista que extrae SOLO datos DIVULGADOS explícitamente en un 10-K. "
                "Si un dato no aparece divulgado, devuelve null. NUNCA estimes ni inventes. "
                "Muchas empresas (no-suscripción) no reportan NRR/churn/LTV/CAC: en ese caso null. "
                "Responde ÚNICAMENTE con un objeto JSON válido, sin texto alrededor.")
        _schema = (
            '{"recurring_revenue_usd": number|null,  // ingreso recurrente/suscripción anual en USD absolutos\n'
            ' "largest_customer_share": number|null, // 0-1; fracción de ingresos del mayor cliente (o "no >10%" -> 0.10)\n'
            ' "customer_shares": [number]|null,      // 0-1 por cliente si divulga varios\n'
            ' "segment_shares": [number]|null,       // 0-1 fracción de ingresos por segmento de negocio\n'
            ' // Cohorte de retención de ingresos (ARR bridge), en USD del MISMO periodo:\n'
            ' "retention_begin": number|null,        // ARR/ingreso recurrente al inicio\n'
            ' "retention_expansion": number|null,    // expansión/upsell del periodo\n'
            ' "retention_contraction": number|null,  // contracción/downgrade del periodo\n'
            ' "retention_churn": number|null,        // ingreso perdido por bajas del periodo\n'
            ' // Churn de logos (CONTEO de clientes):\n'
            ' "customers_lost": number|null,         // clientes perdidos en el periodo\n'
            ' "customers_begin": number|null,        // clientes al inicio del periodo\n'
            ' // Unit economics (si los divulga):\n'
            ' "arpu": number|null,                   // ingreso anual promedio por cliente (USD)\n'
            ' "monthly_arpu": number|null,           // ingreso MENSUAL promedio por cliente (USD)\n'
            ' "gross_margin": number|null,           // margen bruto por cliente 0-1\n'
            ' "customer_life_years": number|null,    // vida media del cliente en años\n'
            ' "cac_spend": number|null,              // gasto total de adquisición (S&M) USD\n'
            ' "new_customers": number|null,          // clientes nuevos adquiridos en el periodo\n'
            ' "guidance_history": [{"actual": number, "guidance_midpoint": number}]|null}'
        )
        _msg = _client.messages.create(
            model=getattr(settings, "judge_model", "claude-opus-4-8"),
            max_tokens=1024, system=_sys,
            messages=[{"role": "user", "content":
                       f"Empresa {ticker}. Del siguiente 10-K, extrae este JSON (null si no está divulgado):\n"
                       f"{_schema}\n\n=== 10-K ===\n{_txt}"}],
        )
        _raw = "".join(getattr(b, "text", "") for b in _msg.content)
        _m = _re.search(r"\{.*\}", _raw, _re.DOTALL)
        if not _m:
            return {}
        _d = _json.loads(_m.group(0))
    except Exception as _e:
        print(f"[engine] extracción cualitativa del 10-K omitida: {str(_e)[:140]}")
        return {}
    # 4) mapear a la forma EXACTA que Victor espera por overlay (solo lo no-null)
    _ov = {}
    def _num(x):
        try:
            return float(x) if x is not None else None
        except (TypeError, ValueError):
            return None
    _rr = _num(_d.get("recurring_revenue_usd"))
    if _rr is not None and _rr > 0:
        _ov["recurring_revenue"] = _rr
    _lcs = _num(_d.get("largest_customer_share"))
    if _lcs is not None and 0.0 <= _lcs <= 1.0:
        _ov["largest_customer_share"] = _lcs
    _cs = _d.get("customer_shares")
    if isinstance(_cs, list):
        _csv = [c for c in (_num(x) for x in _cs) if c is not None and 0.0 <= c <= 1.0]
        if _csv:
            _ov["customer_shares"] = _csv
    _ss = _d.get("segment_shares")
    if isinstance(_ss, list):
        _ssv = [s for s in (_num(x) for x in _ss) if s is not None and 0.0 <= s <= 1.0]
        if _ssv:
            _ov["segment_shares"] = _ssv
    # retention: Victor exige la cohorte cruda {begin, expansion, contraction, churn}
    # (NO nrr/grr; él los calcula). Solo se pasa si los 4 componentes están divulgados.
    _rb, _rx = _num(_d.get("retention_begin")), _num(_d.get("retention_expansion"))
    _rcn, _rch = _num(_d.get("retention_contraction")), _num(_d.get("retention_churn"))
    if None not in (_rb, _rx, _rcn, _rch) and _rb > 0:
        _ov["retention"] = {"begin": _rb, "expansion": _rx, "contraction": _rcn, "churn": _rch}
    # churn de logos: Victor exige {lost, begin_customers} (conteo de clientes)
    _cl, _cb = _num(_d.get("customers_lost")), _num(_d.get("customers_begin"))
    if None not in (_cl, _cb) and _cb > 0:
        _ov["churn"] = {"lost": _cl, "begin_customers": _cb}
    # customer_economics: claves EXACTAS que Victor consume para LTV/CAC/payback
    _ce = {}
    for _src, _dst in (("arpu", "arpu"), ("monthly_arpu", "monthly_arpu"), ("gross_margin", "gross_margin"),
                       ("customer_life_years", "customer_life_years"), ("cac_spend", "cac_spend"),
                       ("new_customers", "new_customers")):
        _val = _num(_d.get(_src))
        if _val is not None:
            _ce[_dst] = _val
    if _ce:
        _ov["customer_economics"] = _ce
    _gh = _d.get("guidance_history")
    if isinstance(_gh, list):
        _ghv = [{"actual": _num(g.get("actual")), "guidance_midpoint": _num(g.get("guidance_midpoint"))}
                for g in _gh if isinstance(g, dict) and _num(g.get("actual")) is not None
                and _num(g.get("guidance_midpoint")) not in (None, 0)]
        if _ghv:
            _ov["guidance_history"] = _ghv
    if _ov:
        print(f"[engine] {ticker}: 10-K → inputs cualitativos divulgados: {sorted(_ov.keys())}")
    return _ov


def _engine_scorecard(ticker, info, price):
    """Scorecard de 6 categorías con el ENGINE REAL de Victor (código determinista,
    sin LLM), EXACTAMENTE como él lo tiene:

      build_packet(ticker, Providers(fmp,edgar,finnhub,fred), now) → Packet
        → los 6 especialistas reales de Victor (wbj/specialists/*.run(packet))
        → aggregate (raw_total, confianza) + targets.price_targets

    Si el paquete completo (FMP) no está disponible, cae al camino RÁPIDO de Victor
    (wbj.quick.quick_scorecard sobre el packet EDGAR: Business/Financial/Risk reales,
    las demás N/S — 'sin evidencia no hay número'). Devuelve el dict del engine o
    None si nada pudo calcularse (→ fallback LLM)."""
    import sys
    if _WBJ_ENGINE_PATH not in sys.path:
        sys.path.insert(0, _WBJ_ENGINE_PATH)
    try:
        from datetime import datetime, timezone
        from wbj.config import load_settings
        from wbj.providers.cache import Cache
        from wbj.providers.edgar import EdgarProvider
        from wbj.providers.fmp import FMPProvider
        from wbj.providers.finnhub import FinnhubProvider
        from wbj.providers.fred import FredProvider
        from wbj.packet.builder import Providers, build_packet
        import wbj.specialists.business as _biz
        import wbj.specialists.financial as _fin
        import wbj.specialists.market as _mkt
        import wbj.specialists.risk as _rsk
        import wbj.specialists.technical as _tec
        import wbj.specialists.valuation as _val
        from wbj.cli import _build_packet
        from wbj.targets import price_targets
    except Exception as e:
        print(f"[engine] no disponible (deps/import): {str(e)[:160]}")
        return None

    # settings + inyección de claves desde el entorno (Render) si el .env no las tomó
    try:
        settings = load_settings()
    except Exception as e:
        print(f"[engine] load_settings falló: {str(e)[:120]}"); return None
    for _env, _attr in (("FMP_API_KEY", "fmp_api_key"), ("FINNHUB_API_KEY", "finnhub_api_key"),
                        ("FRED_API_KEY", "fred_api_key")):
        _v = os.environ.get(_env)
        if _v and not getattr(settings, _attr, None):
            try: setattr(settings, _attr, _v)
            except Exception: pass
    cache = Cache(settings.cache_dir)

    _LABEL = {k: WBJ_CATEGORIES[k]["label"] for k in WBJ_ORDER}
    _MODS = [("business", _biz), ("financial", _fin), ("market", _mkt),
             ("technical", _tec), ("risk", _rsk), ("valuation", _val)]

    categories = {}; raw_total = 0.0; conf_num = 0.0; conf_den = 0.0; incomplete = []
    used_specialists = False

    # ── CAMINO PRINCIPAL: los 6 especialistas REALES de Victor sobre el Packet completo ──
    try:
        prov = Providers(fmp=FMPProvider(settings, cache), edgar=EdgarProvider(settings, cache),
                         finnhub=FinnhubProvider(settings, cache), fred=FredProvider(settings, cache))
        pk = build_packet(ticker, prov, datetime.now(timezone.utc))

        # ── ADAPTADOR DE INDUSTRIA: el builder de Victor fija 'default_nonfinancial' para TODO.
        #    Para bancos/aseguradoras/REITs las fórmulas de ROIC/moat no aplican; si se deja el
        #    default, Business puntúa con confianza ALTA (model_fit 90) y SIN la advertencia de
        #    Victor. Fijamos el adaptador según el sector real (dato, no lógica): así se dispara
        #    su propia advertencia (business.py L975) y baja la confianza a 40 (L1215). ──
        try:
            _sector = (info.get("sector") or "").strip().lower()
            _adapter = None
            if _sector == "financial services":
                _adapter = "financials"
            elif _sector == "real estate":
                _adapter = "reits"
            if _adapter:
                pk = pk.model_copy(update={
                    "analysis": pk.analysis.model_copy(update={"industry_adapter": _adapter})})
                print(f"[engine] {ticker}: sector '{info.get('sector')}' → industry_adapter='{_adapter}' "
                      f"(Business marcará la advertencia y bajará la confianza, como define Victor)")
        except Exception as _ae:
            print(f"[engine] ajuste de adaptador de industria omitido: {str(_ae)[:120]}")

        # ── HANDOFF de Victor: Valuation computa el WACC (VAL-WACC-007) y Business/Financial
        #    lo CONSUMEN vía overlay["wacc"]. Sin este traspaso, todo ROIC/spread/EVA/moat de
        #    Business degrada a MISSING (business ≈ 0). Es el mecanismo que su metodología define
        #    ("mirroring financial.py's overlay['wacc'] precedent" + HANDOFF_CONTRACT). ──
        _overlay = {}
        # respaldo de beta desde yfinance si el packet no lo trae (Ke = RF + β·ERP)
        try:
            if info.get("beta") is not None:
                _overlay["beta"] = float(info["beta"])
        except Exception:
            pass
        _vo = None
        try:
            _vo = _val.run(pk, overlay=(_overlay or None))   # valuation primero → su WACC
            _w = getattr(getattr(_vo, "wacc", None), "value", None)
            if _w is not None and float(_w) > 0:
                _overlay["wacc"] = float(_w)
        except Exception as _ve:
            print(f"[engine] handoff de WACC no disponible: {str(_ve)[:120]}")
        if _overlay.get("wacc"):
            print(f"[engine] {ticker}: WACC del handoff = {_overlay['wacc']:.4f} → Business/Financial")

        # ── peer_roic para BUSINESS (dimensión Competitive, BUS): ROIC de los pares con
        #    la fórmula EXACTA de Victor (valuation_engine.nopat / invested_capital) sobre
        #    los estados FMP de cada par. Sin esto, media dimensión Competitive queda N/S. ──
        try:
            from wbj.engines import valuation_engine as _ve_roic
            _peers_raw = prov.fmp.peers(ticker) or []
            _plist = (_peers_raw[0].get("peersList") if isinstance(_peers_raw, list) and _peers_raw
                      and isinstance(_peers_raw[0], dict) else _peers_raw) or []
            # Usa las funciones EXACTAS de Victor para que el ROIC de los pares sea
            # IDÉNTICO al que él computa para la empresa (misma tasa efectiva _tax_rate,
            # mismo capital invertido PROMEDIO inicio+fin, misma fórmula roic). Si no,
            # el percentil peer_score compararía cosas distintas.
            from wbj.specialists.business import _tax_rate as _biz_tax_rate, average_invested_capital as _biz_avg_ic
            def _fmp_num(_row, _k):
                _v = _row.get(_k) if isinstance(_row, dict) else None
                try:
                    return float(_v) if _v is not None else None
                except (TypeError, ValueError):
                    return None
            _proics = []
            # Victor's peer_score exige ≥8 pares válidos (SCORING_ENGINE.md); con menos
            # devuelve N/S. Por eso pedimos hasta 15 para asegurar ≥8 tras posibles fallos.
            for _pt in list(_plist)[:15]:
                try:
                    if not _pt or str(_pt).upper() == ticker.upper():
                        continue                      # nunca comparar la empresa contra sí misma
                    _inc = prov.fmp.income_annual(_pt, limit=1) or []
                    _bal = prov.fmp.balance_annual(_pt, limit=2) or []   # año actual + previo (IC promedio)
                    _ir = _inc[0] if isinstance(_inc, list) and _inc else None
                    _cur = _bal[0] if isinstance(_bal, list) and len(_bal) >= 1 else None
                    _prev = _bal[1] if isinstance(_bal, list) and len(_bal) >= 2 else None
                    if not isinstance(_ir, dict) or not isinstance(_cur, dict) or not isinstance(_prev, dict):
                        continue
                    _ebit = _fmp_num(_ir, "operatingIncome")
                    _dc = _fmp_num(_cur, "totalDebt"); _ec = _fmp_num(_cur, "totalStockholdersEquity"); _cc = _fmp_num(_cur, "cashAndCashEquivalents")
                    _dp = _fmp_num(_prev, "totalDebt"); _ep = _fmp_num(_prev, "totalStockholdersEquity"); _cp = _fmp_num(_prev, "cashAndCashEquivalents")
                    if None in (_ebit, _dc, _ec, _dp, _ep):
                        continue
                    # tasa efectiva con el helper de Victor (income_before_tax/income_tax_expense, fallback 0.21)
                    _row_can = {"income_before_tax": _fmp_num(_ir, "incomeBeforeTax"),
                                "income_tax_expense": _fmp_num(_ir, "incomeTaxExpense")}
                    _trate = _biz_tax_rate(_row_can, 0.21)
                    _np = _ve_roic.nopat(_ebit, _trate).value
                    _avg_ic = _biz_avg_ic(_dp, _ep, (_cp or 0.0), _dc, _ec, (_cc or 0.0))   # promedio inicio+fin
                    if _avg_ic.is_null:
                        continue
                    _roic_v = _ve_roic.roic(_np, _avg_ic.value)
                    if _roic_v.is_valid:
                        _proics.append(_roic_v.value)
                except Exception:
                    continue
            if len(_proics) >= 8:                            # umbral de Victor: ≥8 pares o N/S
                _overlay["peer_roic"] = _proics
                print(f"[engine] {ticker}: peer_roic de {len(_proics)} pares → Business (Competitive)")
            elif _proics:
                print(f"[engine] {ticker}: solo {len(_proics)} pares con ROIC (<8) → "
                      f"Competitive cae a reglas absolutas (peer_score N/S, como define Victor)")
        except Exception as _pe:
            print(f"[engine] peer_roic omitido: {str(_pe)[:120]}")

        # ── Inputs CUALITATIVOS del 10-K (fiel al sub-agente business de Victor): una sola
        #    extracción con Claude que devuelve TODO lo divulgado. Se consumen por dimensión.
        #    DURABILITY: recurring_revenue + largest_customer_share (+ customer_shares).
        #    MANAGEMENT: guidance_history (precisión de guía = actual vs punto medio guiado).
        #    CUSTOMER ECONOMICS: retention (cohorte NRR/GRR) + churn (logos) + customer_economics
        #    (LTV/CAC/payback) — solo empresas de suscripción los divulgan; si no, N/S correcto. ──
        _qual = {}
        try:
            _cik_biz = prov.edgar.cik_for(ticker)
            _qual = _wbj_extract_business_qual(ticker, _cik_biz, settings) or {}
        except Exception as _qe:
            print(f"[engine] extracción cualitativa omitida: {str(_qe)[:120]}")
        for _qk in ("recurring_revenue", "largest_customer_share", "customer_shares", "segment_shares",
                    "guidance_history", "retention", "churn", "customer_economics"):
            if _qual.get(_qk) is not None:
                _overlay[_qk] = _qual[_qk]

        # ── Fase A: correr los 6 especialistas (con overlay wacc/peer_roic) y recoger sus outputs ──
        _outputs = []                       # [(key, output)] en orden, para el judge y el merge
        for key, mod in _MODS:
            try:
                out = _vo if (key == "valuation" and _vo is not None) else mod.run(pk, overlay=(_overlay or None))
                _outputs.append((key, out))
            except Exception as _es:
                categories[key] = {"key": key, "label": _LABEL[key], "max": WBJ_CATEGORIES[key]["max"],
                    "score10": None, "points": None, "coverage": 0.0, "status": "not_scorable",
                    "confidence": None, "reason": f"no se pudo analizar ({type(_es).__name__})"}
                incomplete.append(key)

        # ── Fase B: JUDGE de Victor — Claude responde lo CUALITATIVO que el código no puede
        #    puntuar (moat, catalizadores, concentración, thesis-killers, tier de TAM) y lo
        #    FUSIONA a los outputs (sube awarded_points/coverage de Business/Market/Risk).
        #    Necesita ANTHROPIC_API_KEY; si falla o no hay key, el análisis determinista sigue. ──
        if getattr(settings, "anthropic_api_key", None) and _outputs:
            try:
                from wbj.overlay.merge import collect_requests, merge_overlay
                from wbj.judge import answer_judgments
                _outs = [o for _, o in _outputs]
                _reqs = collect_requests(_outs)
                _judgments = answer_judgments(pk, _reqs, settings)
                if _judgments:
                    _merged = merge_overlay(_outs, _judgments)
                    _outputs = [(_outputs[i][0], _merged[i]) for i in range(len(_outputs))]
                    print(f"[engine] {ticker}: judge respondió {len(_judgments)}/{len(_reqs)} preguntas "
                          f"cualitativas → coberturas de Business/Market/Risk actualizadas")
                else:
                    print(f"[engine] {ticker}: judge sin respuestas (sin SDK/key válida o sin preguntas)")
            except Exception as _je:
                print(f"[engine] judge omitido (sigue el análisis determinista): {str(_je)[:140]}")

        # ── Fase C: construir el scorecard desde los outputs (ya fusionados si corrió el judge) ──
        for key, out in _outputs:
            cat = out.category
            cov = out.coverage if out.coverage is not None else 0.0
            s10 = round(cat.score_10, 1) if cat.score_10 is not None else None
            categories[key] = {
                "key": key, "label": _LABEL[key], "max": cat.max_points,
                "score10": s10,
                "points": round(cat.awarded_points, 2) if cat.awarded_points is not None else None,
                "coverage": round(cov, 2), "status": "scored" if s10 is not None else "not_scorable",
                "confidence": round(cat.confidence) if cat.confidence is not None else None,
                # banderas obligatorias que emite el especialista (p.ej. VALUE_DESTRUCTION,
                # CONCENTRATION_RED_FLAG, DILUTION_RED_FLAG) — insumo de los overrides del principal
                "mandatory_flags": list(getattr(out, "mandatory_flags", []) or []),
                "reason": None if s10 is not None else "cobertura insuficiente (sin evidencia, no hay número)"}
            if s10 is not None:
                raw_total += cat.awarded_points or 0.0
            else:
                incomplete.append(key)
            # confianza total (Victor, core.confidence.total_confidence): Σ(max_i × conf_i)/100,
            # acumulando TODA categoría con confianza (no solo las puntuadas) — así una categoría
            # N/S penaliza la confianza total en vez de ignorarse.
            if cat.confidence is not None:
                conf_num += cat.max_points * cat.confidence
        used_specialists = any(c["status"] == "scored" for c in categories.values())
        if used_specialists:
            print(f"[engine] {ticker}: 6 especialistas reales de Victor OK "
                  f"({sum(1 for c in categories.values() if c['status']=='scored')}/6 con datos)")
    except Exception as e:
        print(f"[engine] pipeline de especialistas no disponible: {str(e)[:160]}")

    # ── packet EDGAR (dict) para targets + ventas anuales (y fallback rápido) ──
    dict_packet = None
    try:
        dict_packet = _build_packet(ticker)
    except Exception as e:
        print(f"[engine] packet EDGAR (dict) falló: {str(e)[:140]}")

    # ── FALLBACK: camino RÁPIDO de Victor (EDGAR) si los especialistas no dieron nada ──
    if not used_specialists:
        if not dict_packet:
            return None
        try:
            from wbj.quick import quick_scorecard
            qs = quick_scorecard(dict_packet)
            categories = {}; raw_total = 0.0; conf_num = 0.0; conf_den = 0.0; incomplete = []
            for row in qs.get("categories", []):
                k = row.get("key")
                if k not in WBJ_ORDER:
                    continue
                categories[k] = {
                    "key": k, "label": _LABEL.get(k, row.get("label")), "max": row.get("max_points"),
                    "score10": row.get("score10"), "points": row.get("points"),
                    "coverage": row.get("coverage", 0.0), "confidence": None,
                    "status": row.get("status"), "reason": row.get("reason")}
                if row.get("status") == "scored":
                    raw_total += row.get("points") or 0.0
                else:
                    incomplete.append(k)
            # rellena cualquier categoría faltante como N/S (contrato de 6)
            for k in WBJ_ORDER:
                if k not in categories:
                    categories[k] = {"key": k, "label": _LABEL[k], "max": WBJ_CATEGORIES[k]["max"],
                        "score10": None, "points": None, "coverage": 0.0, "status": "not_scorable",
                        "confidence": None, "reason": "motor pendiente sin FMP (N/S)"}
                    incomplete.append(k)
            print(f"[engine] {ticker}: camino RÁPIDO de Victor (EDGAR) — "
                  f"{sum(1 for c in categories.values() if c['status']=='scored')}/6 con datos")
        except Exception as e:
            print(f"[engine] quick_scorecard falló: {str(e)[:160]}")
            return None

    if not categories:
        return None

    # Victor: Σ(category_max_points × confidence) / 100 (÷ el 100 fijo, NO por la suma de
    # máximos puntuados). Una categoría N/S baja la confianza total, como en su metodología.
    total_confidence = round(conf_num / 100.0) if conf_num > 0 else 50
    sc = {"categories": categories, "raw_total": round(raw_total, 1),
          "total_confidence": total_confidence, "incomplete": sorted(set(incomplete))}

    # ── TARGETS + FAIR VALUE de Victor (su targets.py) — deterministas, no del LLM ──
    if dict_packet:
        try:
            pt = price_targets(dict_packet, price)
            if isinstance(pt, dict) and pt.get("status") == "ok":
                sm = {s["key"]: s.get("target") for s in pt.get("scenarios", [])}
                sc["victor_targets_12m"] = {"bull": sm.get("bull"), "base": sm.get("base"), "bear": sm.get("bear")}
                sc["victor_fair_value"] = sm.get("base")      # el target "Medio" ES el fair value de Victor
                sc["victor_targets_detail"] = pt
        except Exception as e:
            print(f"[engine] targets de Victor omitidos: {str(e)[:140]}")
    # ── VENTAS ANUALES + CRECIMIENTO (desde el packet EDGAR de Victor) para las gráficas ──
    try:
        _a = (dict_packet or {}).get("annual", {}) or {}
        def _ser(key):
            return [(str(r.get("end", ""))[:4], r.get("val")) for r in _a.get(key, []) if r.get("val") is not None][-6:]
        rev = _ser("revenue"); ni = _ser("net_income"); op = _ser("operating_income"); gp = _ser("gross_profit")
        years = [y for y, _ in rev]
        revenue = [v for _, v in rev]
        ni_by = dict(ni); op_by = dict(op); gp_by = dict(gp)
        rev_growth = [None] + [round((revenue[i] / revenue[i - 1] - 1) * 100, 1) if revenue[i - 1] else None
                               for i in range(1, len(revenue))]
        cagr = None
        if len(revenue) >= 3 and revenue[0] > 0 and revenue[-1] > 0:
            cagr = round(((revenue[-1] / revenue[0]) ** (1 / (len(revenue) - 1)) - 1) * 100, 1)
        net_margin = [round(ni_by[y] / v * 100, 1) if (y in ni_by and v) else None for y, v in rev]
        op_margin = [round(op_by[y] / v * 100, 1) if (y in op_by and v) else None for y, v in rev]
        gross_margin = [round(gp_by[y] / v * 100, 1) if (y in gp_by and v) else None for y, v in rev]
        sc["financials_annual"] = {
            "years": years, "revenue": revenue,
            "net_income": [ni_by.get(y) for y in years],
            "revenue_growth_yoy": rev_growth, "revenue_cagr": cagr,
            "net_margin": net_margin, "operating_margin": op_margin, "gross_margin": gross_margin}
    except Exception as e:
        print(f"[engine] financials anuales omitidos: {str(e)[:140]}")
    return sc


@app.get("/api/analyze")
def analyze_ticker(ticker: str):
    ticker = ticker.upper().strip()
    try:
        stock = yf.Ticker(ticker)
        try:
            info = stock.info or {}
        except Exception:
            info = {}
        hist  = _resilient_history(stock, ticker, "6mo")   # yfinance → respaldo Stooq (no se cae)
        if hist is None or hist.empty:
            raise HTTPException(status_code=404, detail="Sin datos")

        hist_1m = _resilient_history(stock, ticker, "1mo")
        if hist_1m is None or hist_1m.empty:
            hist_1m = hist.tail(22)                          # último mes aprox. desde la historia ya obtenida
        precios_hist = [round(float(x), 2) for x in hist_1m['Close'].tolist()]
        fechas_hist  = [x.strftime("%b %d") for x in hist_1m.index.tolist()]
        precio_actual = precios_hist[-1] if precios_hist else round(float(hist['Close'].iloc[-1]), 2)

        # ── OHLC + Volume for candlestick chart (TradingView format) ─────────
        ohlc_hist   = []
        volumen_hist = []
        for idx, row in hist_1m.iterrows():
            t = idx.strftime("%Y-%m-%d")
            ohlc_hist.append({
                "time":  t,
                "open":  round(float(row['Open']), 2),
                "high":  round(float(row['High']), 2),
                "low":   round(float(row['Low']), 2),
                "close": round(float(row['Close']), 2),
            })
            volumen_hist.append({
                "time":  t,
                "value": int(row['Volume']) if not math.isnan(row['Volume']) else 0,
                "up":    bool(row['Close'] >= row['Open']),
            })

        # ── Earnings date / EPS estimate ─────────────────────────────────────
        earnings_info = fetch_earnings_info(stock, info)
        earnings_hist = fetch_earnings_history(stock)          # #2 — sorpresas + reacción post-earnings
        insiders_snapshot = fetch_insiders_data(stock, ticker)
        insiders_context  = format_insiders_context(insiders_snapshot)
        finnhub_context   = format_finnhub_context(ticker)
        portfolio_fit     = compute_ticker_vs_portfolio(ticker, get_portfolio_snapshot())

        logo_url = obtener_logo(ticker, info.get("website", ""))

        # ── INSTITUTIONAL TARGETS ───────────────────────────────────────────
        institutional = calculate_institutional_targets(ticker, info, hist)
        targets = institutional["targets"]
        methodology = institutional["methodology"]

        # ── NOTICIAS ────────────────────────────────────────────────────────
        raw_news = stock.news if stock.news else []
        noticias_formateadas = []
        for item in raw_news[:6]:
            pub_time = datetime.fromtimestamp(item.get("providerPublishTime", 0)).strftime('%Y-%m-%d %I:%M %p')
            noticias_formateadas.append({
                "title": item.get("title", "No Title"),
                "publisher": item.get("publisher", "Yahoo Finance"),
                "publish_time": pub_time,
                "summary": item.get("summary", "No description available.")
            })

        titulares_contexto, news_catalysts = _news_catalyst_context(noticias_formateadas)   # #3
        sec_8k = fetch_recent_8k(ticker)                                                    # #3 — 8-K reales (SEC EDGAR)
        _8k_block, _8k_tags = _8k_catalyst_block(sec_8k)
        if _8k_tags:
            news_catalysts = sorted(set((news_catalysts or []) + _8k_tags))
        analisis_timestamp = datetime.now().strftime('%m/%d/%Y, %I:%M:%S %p')
        report_id = datetime.now().strftime('%Y%m%d_%H%M%S') + f"_{ticker}"

        # ── LONG-TERM MEMORY: recall the agent's prior report on this ticker ──
        prior_report = get_prior_report(ticker)
        memory_block = build_memory_block(prior_report, precio_actual)
        # #3 — memoria PROFUNDA (últimas N llamadas con su resultado) + posiciones de opciones abiertas
        _deep_mem_block, _deep_mem_meta = _deep_memory_block(ticker, precio_actual)
        _opt_block, _open_opts = _open_options_block(ticker)

        # ── #3 CALIBRACIÓN: historial de aciertos del agente para el prompt ───
        calib_stats = get_calibration_cached()
        calibration_block = ""
        if calib_stats and calib_stats.get("n"):
            _rl = ", ".join(f"{k} {v['hit_rate']}% (n={v['n']})"
                            for k, v in calib_stats["by_recommendation"].items()
                            if v.get("hit_rate") is not None)
            _err = calib_stats.get("avg_upside_error_pct")
            _en = f" Error medio de upside a 180d: {_err:+.1f}% (negativo = histórico sobreoptimista)." if _err is not None else ""
            calibration_block = (
                f"\nTU HISTORIAL DE CALIBRACIÓN (acierto direccional realizado, no opinión): "
                f"global {calib_stats['overall_hit_rate']}% en {calib_stats['n']} evaluaciones; "
                f"por recomendación: {_rl}.{_en} Usa esto para NO sobreestimar tu convicción: "
                f"si tus BUY históricamente aciertan ~X%, no presentes 90% sin evidencia excepcional. Calibra con humildad.")

        # ── #4 RÉGIMEN DE MERCADO: que el LLM razone consciente del entorno ───
        _regime_now = get_regime_cached()
        regime_block = ""
        if _regime_now and _regime_now.get("label"):
            regime_block = (
                f"\nRÉGIMEN DE MERCADO ACTUAL (clasificado por el motor Vertex): {_regime_now['label']}"
                f"{(' · VIX ' + str(_regime_now.get('vix'))) if _regime_now.get('vix') is not None else ''}"
                f"{(' · SPY vs 200d ' + str(_regime_now.get('spy_vs_200d_pct')) + '%') if _regime_now.get('spy_vs_200d_pct') is not None else ''}. "
                f"Ajusta tu lectura al régimen: en estrés/bajista prioriza riesgo, macro y fundamentales y sé escéptico con el momentum; "
                f"en tendencia alcista/calma el flujo institucional y los técnicos pesan más. Refleja esto en tus signal_scores y en la tesis.")

        # ── INTELIGENCIA DE OPCIONES: GEX / walls / posicionamiento (gratis, calculado) ──
        try:
            _gex_now = get_gex_cached(ticker)
        except Exception:
            _gex_now = None
        gex_block = ""
        if _gex_now and _gex_now.get("ok"):
            gex_block = (
                f"\nINTELIGENCIA DE OPCIONES (GEX y niveles clave, calculados de la cadena para {ticker}): "
                f"{format_options_intel(_gex_now)}. "
                f"Usa estos niveles para tus targets y tu especulación de precio: los walls actúan como imán / soporte / "
                f"resistencia, y el gamma flip define el régimen — sobre el flip (GEX+) el precio tiende a anclarse y "
                f"mean-revertir, bajo el flip (GEX-) los movimientos se amplifican y tienden a extender. "
                f"Si el spot está entre el put wall y el call wall, favorece rango; una ruptura confirmada de un wall suele "
                f"soltar el siguiente tramo direccional. La actividad inusual (sweeps golden/unusual de Quant Data) "
                f"señala posicionamiento institucional fresco. "
                f"Integra todo esto explícitamente en tu tesis, tu plan de compra/venta, tus targets y tus proyecciones.")

        # ── FLUJO INSTITUCIONAL (Quant Data) → alimenta tu señal de flujo (25%) ──
        _qd_conv = None
        _qd_np = None
        _qd_fl = None
        _qd_confl = None
        if _quantdata_ready():
            _qd_np = None
            try:
                _qd_np = quantdata_net_premium(ticker)
                if _qd_np:
                    gex_block += (
                        f"\nFLUJO INSTITUCIONAL DE OPCIONES (Quant Data, exchange-licensed): "
                        f"premium neto de calls ${_qd_np.get('net_call_premium', 0):,.0f}, "
                        f"premium neto de puts ${_qd_np.get('net_put_premium', 0):,.0f}, "
                        f"premium neto total ${_qd_np.get('net_premium', 0):,.0f} → sesgo {_qd_np.get('bias', 'neutral')}. "
                        f"Esta es tu señal de flujo institucional (peso 25%): un premium neto fuertemente positivo confirma "
                        f"acumulación alcista; negativo, distribución/cobertura bajista. Pondéralo en tus signal_scores.")
            except Exception as _eqd:
                print(f"[QuantData] prompt flow skip: {_eqd}")
            # ── CONVICCIÓN confirmada por ΔOI → el bloque más fuerte de tu señal de flujo ──
            _qd_conv = None
            try:
                _qd_fl = quantdata_flow(ticker)
                _qd_oic = quantdata_oi_change(ticker)
                _qd_oimap = _qd_oic.get("map") if isinstance(_qd_oic, dict) else None
                _qd_conv = _qd_conviction(_qd_fl, oi_change_map=_qd_oimap) if _qd_fl else None
                _conv_block = _qd_conviction_prompt_block(_qd_conv)
                if _conv_block:
                    gex_block += _conv_block
            except Exception as _eqc:
                print(f"[QuantData] prompt conviction skip: {_eqc}")
            # ── DARK POOL (Quant Data): niveles + proxy compra/venta de los prints ──
            _qd_dp = None; _qd_dpf = None
            try:
                _qd_dp = quantdata_darkpool(ticker)
                _qd_dpf = quantdata_dark_prints(ticker)
                _qd_spot = _gex_now.get("spot") if isinstance(_gex_now, dict) else None
                _dp_block = _qd_darkpool_prompt_block(_qd_dp, _qd_spot, dp_flow=_qd_dpf)
                if _dp_block:
                    gex_block += _dp_block
            except Exception as _edp:
                print(f"[QuantData] prompt darkpool skip: {_edp}")
            # ── NET DRIFT EN EL TIEMPO (net-flow, 7d) → tendencia del premium ──
            try:
                _qd_nf = quantdata_net_flow(ticker, "7d")
                _nf_block = _qd_netflow_prompt_block(_qd_nf)
                if _nf_block:
                    gex_block += _nf_block
            except Exception as _enf:
                print(f"[QuantData] prompt netflow skip: {_enf}")
            # ── CONFLUENCIA: ¿coinciden convicción + GEX + dark pool? ──
            try:
                _qd_spot = _gex_now.get("spot") if isinstance(_gex_now, dict) else None
                _qd_confl = _qd_confluence(_qd_conv, _gex_now, _qd_dp, _qd_spot, dp_flow=_qd_dpf)
                _confl_block = _qd_confluence_prompt_block(_qd_confl)
                if _confl_block:
                    gex_block += _confl_block
            except Exception as _ecf:
                print(f"[QuantData] prompt confluence skip: {_ecf}")
            # ── CALIBRACIÓN: track-record histórico propio del ticker (feedback loop) ──
            try:
                _cal_block = _calibration_prompt_block(ticker)
                if _cal_block:
                    gex_block += _cal_block
            except Exception as _ecal:
                print(f"[QuantData] prompt calibration skip: {_ecal}")
            # ── #4 — TARGETS DE GAMMA/FLUJO (mismo motor que Proyecciones) ──
            try:
                _ht_block = _horizon_targets_prompt_block(ticker, conviction=_qd_conv, net_premium=_qd_np)
                if _ht_block:
                    gex_block += _ht_block
            except Exception as _eht:
                print(f"[QuantData] prompt horizon-targets skip: {_eht}")

        # ── PROMPT GEMINI (AI narrative sobre targets ya calculados) ─────────
        _key_sig = _key_signals_summary(_qd_conv, _qd_confl, _regime_now, _qd_np)
        _earn_block = _earnings_depth_block(earnings_hist, earnings_info)
        prompt = f"""
Analiza en profundidad la compañía {ticker} ({info.get('longName', ticker)}) con un enfoque institucional para Vertex Holding Group.

{_key_sig}

DATOS DE MERCADO (ya calculados por el motor cuantitativo de Vertex):
- Precio Spot: ${precio_actual} {info.get('currency', 'USD')}
- P/E actual: {info.get('trailingPE', 'N/A')} | Forward P/E: {info.get('forwardPE', 'N/A')}
- Market Cap: {info.get('marketCap', 'N/A')}
- Revenue Growth YoY: {info.get('revenueGrowth', 'N/A')}
- Gross Margins: {info.get('grossMargins', 'N/A')} | EBITDA Margins: {info.get('ebitdaMargins', 'N/A')}
- Free Cash Flow: {info.get('freeCashflow', 'N/A')}
- Beta: {info.get('beta', 'N/A')}
- Analyst Mean Target (Wall Street): ${methodology.get('analyst_mean', 'N/A')}
- DCF Fair Value (Vertex Engine): ${methodology.get('dcf_fair_value', 'N/A')}
- Annual Volatility: {methodology.get('annual_volatility_pct', 'N/A')}% | ATR-14: ${methodology.get('atr_14', 'N/A')}
- Noticias recientes: {titulares_contexto}
- Proximo Earnings: {('en ' + str(earnings_info['days_until']) + ' dias (' + earnings_info['label'] + ')') if earnings_info.get('days_until') is not None else 'N/A'}{(' | EPS estimado: $' + str(earnings_info['eps_estimate'])) if earnings_info.get('eps_estimate') else ''}
- Actividad de Insiders y Flujo Institucional (SEC/13F): {insiders_context if insiders_context else 'N/A'}
- Contexto Finnhub (15-min | fundamentales/news/sentiment/insiders/congreso): {finnhub_context if finnhub_context else 'N/A'}
- Ajuste con TU portafolio actual (consciencia de cartera): {format_portfolio_fit(portfolio_fit)}
- Riesgo de factores y contribucion marginal al riesgo (estilo BlackRock/Aladdin): {format_factor_risk(portfolio_fit)}
{memory_block}{_deep_mem_block}{_opt_block}{calibration_block}{regime_block}{gex_block}{_earn_block}{_8k_block}

PRICE TARGETS YA CALCULADOS (usa estos en tu narrativa; NO los modifiques):
- 7D:  Bull ${targets['7d']['bull']} | Base ${targets['7d']['base']} | Bear ${targets['7d']['bear']}
- 30D: Bull ${targets['30d']['bull']} | Base ${targets['30d']['base']} | Bear ${targets['30d']['bear']}
- 3M:  Bull ${targets['3m']['bull']} | Base ${targets['3m']['base']} | Bear ${targets['3m']['bear']}
- 6M:  Bull ${targets['6m']['bull']} | Base ${targets['6m']['base']} | Bear ${targets['6m']['bear']}
- 12M: Bull ${targets['12m']['bull']} | Base ${targets['12m']['base']} | Bear ${targets['12m']['bear']}

INSTRUCCIÓN CRÍTICA:
Basa tu recomendación final, el Fair Value y tu tesis estrictamente en los **targets a futuro de 1 año** calculados y los **targets de Wall Street (Analyst Mean Target)**. NO bases tu recomendación ni tu Fair Value en el valor intrínseco actual histórico o descontado. Tu decisión e indicador de valor justo deben responder puramente a la proyección futura a 1 año.

FRAMEWORK DE CONVICCIÓN VERTEX (rellena 'signal_scores' con honestidad y especificidad):
Puntúa de 0 a 100 cada señal (100 = máximamente favorable/alcista). Estas se ponderan así para la convicción final de la firma:
- Flujo institucional de opciones — 25% (LA señal de mayor peso, tu edge): busca barridos/bloques, Tipo A ($5M+ en una transacción) y Tipo B (múltiples $1M+ en mismo contrato/strike/exp); delta de convicción institucional 0.60-0.90. Usa el contexto de insiders/13F y Finnhub disponible.
- Fundamentales — 20% | Earnings — 20% | Técnicos — 15% | News/SEC — 10% | Macro — 5% | Riesgo — 5% (100 = riesgo bien controlado).
Cada señal lleva una 'nota' de una línea citando el dato concreto. No infles puntuaciones sin evidencia; si no hay datos de flujo, dilo en la nota y puntúa con cautela.

PROBABILIDADES CALIBRADAS (rellena 'probabilities'):
Da probabilidades 0-100 ANCLADAS EN BASE-RATES, no en optimismo. Pregúntate: ¿con qué frecuencia históricamente una acción con esta volatilidad/perfil logra este movimiento? Evita sobreconfianza: si dices 90%, debe haber evidencia fuerte. En 'rationale' ancla explícitamente en frecuencias base. Estas probabilidades se usan para dimensionar la posición con Kelly fraccional, así que la calibración importa más que el optimismo.

Genera el reporte financiero estructurado con análisis narrativo, fundamentales, tesis y riesgos.
En 'calculos_y_crecimiento_ai' explica la metodología enfocada en cómo el promedio de los targets a 12 meses y Wall Street justifican los valores.
"""

        analisis_json, _analysis_src = _analyze_structured(prompt, temp=0.2)

        # ── OVERRIDE MATEMÁTICO: Fair Value = Promedio de mis targets 12M y Wall Street Mean Target ──
        avg_my_12m = (targets['12m']['bull'] + targets['12m']['base'] + targets['12m']['bear']) / 3
        wall_street_mean = methodology.get('analyst_mean', precio_actual)
        
        combined_fair_value = (avg_my_12m + float(wall_street_mean)) / 2
        
        analisis_json['fair_value'] = round(combined_fair_value, 2)
        analisis_json['upside_pct'] = round(((combined_fair_value - precio_actual) / precio_actual) * 100, 2)

        # ── MOTOR DE CONVICCIÓN PONDERADO (framework Vertex 25/20/20/15/10/5/5) ──
        # #4: los pesos base se ajustan al régimen de mercado actual (vol/tendencia/amplitud/tasas).
        SIGNAL_WEIGHTS = {"flujo_institucional_opciones": 0.25, "fundamentales": 0.20,
                          "earnings": 0.20, "tecnicos": 0.15, "news_sec": 0.10,
                          "macro": 0.05, "riesgo": 0.05}
        regime = get_regime_cached()
        adj_weights, regime_notes = regime_signal_weights(SIGNAL_WEIGHTS, regime)
        # ── #2 (loop de aprendizaje): inclina los pesos por el IC realizado de cada señal ──
        _ic_data = get_signal_ic_cached(horizon_days=30)
        adj_weights, _ic_tilt = _apply_ic_tilt(adj_weights, _ic_data)
        analisis_json["adaptive_weights"] = {
            "by_signal_pct": {d: round(w * 100, 1) for d, w in adj_weights.items()},
            "ic_tilt": _ic_tilt}
        ss = analisis_json.get("signal_scores", {}) or {}
        # ── #1 — ANCLA la señal de flujo (25%) al cómputo de convicción, no a la estimación del LLM ──
        _fa = _flow_anchor_score(_qd_conv)
        if _fa is not None:
            _fl = ss.get("flujo_institucional_opciones", {}) if isinstance(ss.get("flujo_institucional_opciones"), dict) else {}
            try:
                _llm_fl = float(_fl.get("score", _fa) or _fa)
            except (TypeError, ValueError):
                _llm_fl = _fa
            _anchored = max(_fa - 12.0, min(_fa + 12.0, _llm_fl))   # el LLM solo ajusta ±12 con justificación
            _fl["score"] = round(_anchored, 0)
            _fl["nota"] = ((_fl.get("nota", "") or "") +
                           f" [anclado al cómputo de convicción {_fa:.0f} · sesgo {_qd_conv.get('bias')} · n={_qd_conv.get('qualifying')}]").strip()
            ss["flujo_institucional_opciones"] = _fl
            analisis_json["signal_scores"] = ss
            analisis_json["flow_anchor"] = {"computed": round(_fa, 0), "llm": round(_llm_fl, 0),
                                            "used": round(_anchored, 0), "bias": _qd_conv.get("bias"),
                                            "qualifying": _qd_conv.get("qualifying")}
        breakdown = []
        composite = 0.0
        for k, w in adj_weights.items():
            dim = ss.get(k, {}) if isinstance(ss.get(k), dict) else {}
            try:
                sc = float(dim.get("score", 0) or 0)
            except (TypeError, ValueError):
                sc = 0.0
            sc = max(0.0, min(100.0, sc))
            contrib = w * sc
            composite += contrib
            breakdown.append({"signal": k, "score": round(sc, 0), "weight_pct": round(w * 100, 1),
                              "base_weight_pct": int(SIGNAL_WEIGHTS[k] * 100),
                              "contribution": round(contrib, 1), "nota": dim.get("nota", "")})
        breakdown.sort(key=lambda x: x["contribution"], reverse=True)
        analisis_json["conviction_weighted"] = round(composite, 1)
        analisis_json["conviction_breakdown"] = breakdown
        analisis_json["regime"] = regime
        analisis_json["regime_adjustments"] = regime_notes

        # ── #5: la CONFLUENCIA formal (convicción+GEX+dark pool) ajusta la CONVICCIÓN, no solo el prompt ──
        if _qd_confl:
            _cv_adj, _cv_info, _cv_flag = _confluence_conviction_adj(
                composite, _qd_confl, analisis_json.get("recommendation"))
            if _cv_info:
                composite = _cv_adj
                analisis_json["conviction_weighted"] = round(composite, 1)
                analisis_json["confluence_adjustment"] = _cv_info
                if _cv_flag:
                    _cfl = analisis_json.get("coherence_flags") or []
                    _cfl.append(_cv_flag)
                    analisis_json["coherence_flags"] = _cfl

        # ── #3 CONVICCIÓN CALIBRADA (shrinkage empírico-bayesiano hacia el hit-rate real) ──
        cal_val, cal_info = calibrate_conviction(
            analisis_json["conviction_weighted"], analisis_json.get("recommendation"), calib_stats)
        analisis_json["calibrated_conviction"] = cal_val
        analisis_json["calibration"] = cal_info

        # ── #2 PROBABILIDADES CALIBRADAS + SIZING (Kelly fraccional, acotado por guardrails) ──
        # ── #5 PLAN DE RIESGO (stops según reglas Vertex) ──
        probs = analisis_json.get("probabilities", {}) or {}
        def _pi(k, d=0):
            try:
                return max(0, min(100, int(probs.get(k, d) or d)))
            except (TypeError, ValueError):
                return d
        p_pos = _pi("p_positive_12m", 50)
        # ── #4/#1 — ANCLA la probabilidad al base-rate empírico ANTES del Kelly (el sizing es muy sensible a p).
        # Kelly ADAPTATIVO: usa el edge realizado MÁS ESPECÍFICO con muestra suficiente
        # (ticker → tipo de setup → recomendación → global), con shrinkage por n. Dimensiona por TU edge medido.
        _rec = analisis_json.get("recommendation")
        _cur_ss = analisis_json.get("signal_scores") or {}
        _cur_setup, _bv = None, None
        for _k, _w in _SETUP_W.items():
            _d = _cur_ss.get(_k)
            if not isinstance(_d, dict):
                continue
            try:
                _sc = float(_d.get("score", 0) or 0)
            except (TypeError, ValueError):
                _sc = 0.0
            if _bv is None or _sc * _w > _bv:
                _bv, _cur_setup = _sc * _w, _k
        _cur_setup_lbl = _SETUP_LBL.get(_cur_setup, "n/d") if _cur_setup else "n/d"
        _cand = []
        if calib_stats:
            _bt = (calib_stats.get("by_ticker") or {}).get(ticker)
            if _bt and _bt.get("hit_rate") is not None and _bt.get("n"):
                _cand.append(("ticker " + ticker, float(_bt["hit_rate"]), int(_bt["n"])))
            _bsu = (calib_stats.get("by_setup") or {}).get(_cur_setup_lbl)
            if _bsu and _bsu.get("hit_rate") is not None and _bsu.get("n"):
                _cand.append(("setup " + _cur_setup_lbl, float(_bsu["hit_rate"]), int(_bsu["n"])))
            _brd = (calib_stats.get("by_recommendation") or {}).get(_rec) or {}
            if _brd.get("hit_rate") is not None and _brd.get("n"):
                _cand.append(("recomendación " + str(_rec), float(_brd["hit_rate"]), int(_brd["n"])))
        _br = None                                  # el más específico con n≥5; si no, el más específico con n>0
        for _c in _cand:
            if _c[2] >= 5:
                _br = _c; break
        if _br is None and _cand:
            _br = _cand[0]
        if _br is None and calib_stats and calib_stats.get("overall_hit_rate") is not None and calib_stats.get("n"):
            _br = ("global", float(calib_stats["overall_hit_rate"]), int(calib_stats["n"]))
        prob_anchor = None
        if _br is not None and _br[2] > 0:
            _scope, _brp, _brn = _br
            _Kp = 10.0
            _wb = _brn / (_brn + _Kp)                       # n alto → confía en el base-rate; n bajo → en el LLM
            _p_used = _wb * _brp + (1 - _wb) * p_pos
            prob_anchor = {"llm_p": p_pos, "base_rate": round(_brp, 1), "n": _brn,
                           "weight_base": round(_wb, 2), "p_used": round(_p_used, 1), "scope": _scope}
            p_pos = int(round(max(1, min(99, _p_used))))
        analisis_json["prob_anchoring"] = prob_anchor
        bull12 = float(targets['12m']['bull']); bear12 = float(targets['12m']['bear'])
        reward = max(0.0, (bull12 - precio_actual) / precio_actual)
        risk_dn = max(1e-6, (precio_actual - bear12) / precio_actual)
        rr = reward / risk_dn if risk_dn > 0 else 0.0
        p = p_pos / 100.0; q = 1.0 - p
        kelly_full = max(0.0, (rr * p - q) / rr) if rr > 0 else 0.0   # f* = p - q/b
        kelly_half = kelly_full * 0.5                                  # half-Kelly por seguridad
        held_w = 0.0
        if portfolio_fit and portfolio_fit.get("already_held"):
            try:
                held_w = float(portfolio_fit.get("current_weight_pct", 0) or 0)
            except (TypeError, ValueError):
                held_w = 0.0
        room = max(0.0, 25.0 - held_w)                                # tope de concentración 25%
        suggested = max(0.0, min(kelly_half * 100.0, room))
        cap_reason = ""
        if kelly_half * 100.0 > room:
            cap_reason = (f"Limitado por tope de concentración 25% (ya tienes {held_w:.1f}% en {ticker})."
                          if held_w > 0 else "Limitado por tope de concentración 25%.")

        # #2 — haircut por concentración de factor: si la idea apila sobre el factor ya
        # dominante del book (o es muy redundante), recorta el tamaño sugerido 30%.
        factor_haircut = 1.0
        _rc = (portfolio_fit or {}).get("risk_contribution") or {}
        _fc = (portfolio_fit or {}).get("factors") or {}
        if _fc.get("concentrates_dominant") or _rc.get("verdict") == "concentra":
            factor_haircut = 0.70
            if suggested > 0:
                suggested = suggested * factor_haircut
                _lab = _fc.get("factor_labels", {})
                _dom = _lab.get(_fc.get("book_dominant"), _fc.get("book_dominant"))
                cap_reason = (cap_reason + f" Haircut −30% por concentración de factor"
                              + (f" ({_dom})." if _dom else ".")).strip()

        # #7 — recalcula el MCR/Δvol/VaR al PESO REALMENTE RECOMENDADO (no al 5% fijo)
        if portfolio_fit and suggested and suggested > 0:
            try:
                portfolio_fit["risk_contribution"] = _recompute_risk_contribution(portfolio_fit, suggested / 100.0)
            except Exception:
                pass

        ss_fund = float((ss.get("fundamentales", {}) or {}).get("score", 0) or 0)
        ss_flow = float((ss.get("flujo_institucional_opciones", {}) or {}).get("score", 0) or 0)
        is_a_grade = (ss_fund >= 70 and analisis_json["conviction_weighted"] >= 65)
        atr = methodology.get("atr_14")
        try:
            atr_f = float(atr) if atr not in (None, "N/A", "") else None
        except (TypeError, ValueError):
            atr_f = None
        if is_a_grade:
            equity_stop = None
            equity_stop_note = f"Equity A-grade: sin stop fijo. Gestiona por tesis; quiebre de tesis ≈ ${round(bear12, 2)}."
        elif atr_f:
            equity_stop = round(precio_actual - 2 * atr_f, 2)
            equity_stop_note = f"No A-grade: stop sugerido en ${equity_stop} (2×ATR-14 bajo el spot)."
        else:
            equity_stop = round(precio_actual * 0.85, 2)
            equity_stop_note = f"No A-grade: stop sugerido en ${equity_stop} (-15% del spot)."
        flow_override = ss_flow >= 80

        analisis_json["trade_plan"] = {
            "probabilities": {
                "p_positive_12m": p_pos, "p_touch_bull_12m": _pi("p_touch_bull_12m"),
                "p_touch_bear_12m": _pi("p_touch_bear_12m"), "p_up_10pct_3m": _pi("p_up_10pct_3m"),
                "rationale": probs.get("rationale", "")},
            "reward_pct": round(reward * 100, 1), "risk_pct": round(risk_dn * 100, 1),
            "reward_risk": round(rr, 2),
            "kelly_full_pct": round(kelly_full * 100, 1), "kelly_half_pct": round(kelly_half * 100, 1),
            "suggested_pct": round(suggested, 1), "cap_reason": cap_reason,
            "factor_haircut_pct": round((1 - factor_haircut) * 100, 0),
            "already_held_pct": round(held_w, 1),
            "risk_plan": {
                "is_a_grade": is_a_grade, "equity_stop": equity_stop,
                "equity_stop_note": equity_stop_note, "thesis_break_level": round(bear12, 2),
                "options_stop_rule": ("Opciones: stop −20% a −30% de la prima pagada."
                                      + (" ⚠️ Override: flujo Tipo A ($5M+) fuerte — puede justificar mantener pese al stop." if flow_override else "")),
                "flow_override": flow_override,
            },
        }

        # ── Batch R · targets en R (1R = entry→bear) + lista FALSABLE de invalidación de tesis ──
        _R_unit = max(1e-9, abs(precio_actual - bear12))
        _base12 = float(targets['12m'].get('base', precio_actual))
        analisis_json["trade_plan"]["targets_r"] = {
            "entry": round(precio_actual, 2), "stop": round(bear12, 2), "r_unit": round(_R_unit, 2),
            "bull_r": round((bull12 - precio_actual) / _R_unit, 2),
            "base_r": round((_base12 - precio_actual) / _R_unit, 2), "bear_r": -1.0,
        }
        # "Qué me haría cambiar de opinión": checkpoints concretos y verificables construidos desde las señales
        # reales del análisis (precio, walls, gamma flip, flujo, earnings). No depende del LLM → siempre presente.
        _inval = []
        _is_bull = (_rec or "").upper() == "BUY"
        _inval.append({"factor": "Precio", "kind": "price",
                       "trigger": f"Cierre {'bajo' if _is_bull else 'sobre'} ${round(bear12, 2)} (quiebre de tesis = −1R)"})
        try:
            _gw = _gex_now or {}
            _pw, _cw, _fl = _gw.get("put_wall"), _gw.get("call_wall"), _gw.get("gamma_flip")
            if _is_bull and _pw:
                _inval.append({"factor": "Put wall", "kind": "level",
                               "trigger": f"Pérdida del put wall ${_pw} (el soporte gamma cede)"})
            if (not _is_bull) and _cw:
                _inval.append({"factor": "Call wall", "kind": "level",
                               "trigger": f"Ruptura del call wall ${_cw} (la resistencia gamma cede)"})
            if _fl:
                _inval.append({"factor": "Gamma flip", "kind": "regime",
                               "trigger": f"{'Pérdida' if _is_bull else 'Recuperación'} del flip ${_fl} → dealers en régimen {'vendedor' if _is_bull else 'comprador'}"})
        except Exception:
            pass
        if (_qd_conv or {}).get("bias") or (_qd_np or {}).get("bias"):
            _inval.append({"factor": "Flujo institucional", "kind": "flow",
                           "trigger": f"El flujo/premium neto se voltea {'bajista' if _is_bull else 'alcista'} de forma sostenida"})
        _ed = (earnings_info or {}).get("days_until")
        if isinstance(_ed, (int, float)) and 0 <= _ed <= 45:
            _inval.append({"factor": "Earnings", "kind": "catalyst",
                           "trigger": f"Sorpresa o guía {'a la baja' if _is_bull else 'al alza'} en earnings (en {int(_ed)}d)"})
        analisis_json["trade_plan"]["thesis_invalidation"] = _inval

        # ── #5 — RECONCILIACIÓN de los dos motores de targets (σ/DCF vs gamma/flujo) ──
        try:
            _ht_data = get_horizon_targets_cached(ticker, net_premium=_qd_np, flow=_qd_fl, ai_12m=None)
            analisis_json["target_reconciliation"] = _reconcile_targets(targets, _ht_data, precio_actual)
        except Exception:
            analisis_json["target_reconciliation"] = None

        # ── #8 — GATE DE COHERENCIA sobre la salida del LLM (contradicciones internas) ──
        try:
            _cf = _agent_coherence_checks(analisis_json, precio_actual)
            analisis_json["coherence_flags"] = _cf
            analisis_json["coherence_ok"] = (len(_cf) == 0)
        except Exception:
            analisis_json["coherence_flags"] = []
            analisis_json["coherence_ok"] = True
        analisis_json["model_source"] = _analysis_src
        analisis_json["earnings_history"] = earnings_hist          # #2
        # ── INTEGRIDAD: cedazo visible sobre los números del reporte (flip/walls/FV/targets/fuente) ──
        try:
            _tgt_lvls = [_safe_num(t.get("level")) for t in (targets or []) if isinstance(t, dict) and t.get("level")]
            analisis_json["integrity"] = _integrity_checks(
                _gex_now if isinstance(_gex_now, dict) else {},
                fair_value=analisis_json.get("fair_value"), targets=_tgt_lvls)
        except Exception:
            analisis_json["integrity"] = None
        try:
            analisis_json["ai_concentration"] = _ai_concentration(ticker)
        except Exception:
            analisis_json["ai_concentration"] = None
        analisis_json["news_catalysts"] = news_catalysts            # #3
        analisis_json["sec_8k"] = sec_8k                            # #3 — 8-K reales
        # ── #9 — AUTO-DEBATE: marca para disparar el debate adversarial en convicción alta ──
        _cc = _safe_num(analisis_json.get("calibrated_conviction"))
        analisis_json["should_debate"] = bool(_cc >= 75)
        analisis_json["debate_reason"] = ("Convicción calibrada alta (≥75) — conviene estresarla con el "
                                          "debate adversarial Toro/Oso/Árbitro antes de dimensionar."
                                          if _cc >= 75 else None)

        # ── OVERLAY WBJ: sobrescribe los NÚMEROS con los de Victor (engine determinista) ──
        try:
            _eng = _engine_scorecard(ticker, info, precio_actual)
        except Exception as _eov:
            print(f"[analyze] overlay Victor omitido: {_eov}"); _eng = None
        if _eng and _eng.get("categories"):
            _comp = {"categories": _eng["categories"], "raw_total": _eng["raw_total"],
                     "total_confidence": _eng["total_confidence"], "incomplete": _eng.get("incomplete", [])}
            _gates = _wbj_gates(_comp)
            _vt = _eng.get("victor_targets_12m") or {}
            if all(_vt.get(k) is not None for k in ("bull", "base", "bear")):
                targets["12m"] = {k: round(float(_vt[k]), 2) for k in ("bull", "base", "bear")}
                analisis_json["target_bull_12m"] = targets["12m"]["bull"]
                analisis_json["target_base_12m"] = targets["12m"]["base"]
                analisis_json["target_bear_12m"] = targets["12m"]["bear"]
            _vfv = _eng.get("victor_fair_value")
            if _vfv:
                analisis_json["fair_value"] = round(float(_vfv), 2)
                analisis_json["upside_pct"] = round(((float(_vfv) - precio_actual) / precio_actual) * 100, 2) if precio_actual else 0.0
            analisis_json["recommendation"] = _gates["recommendation"]
            analisis_json["conviccion_score"] = int(round(_eng["raw_total"]))
            analisis_json["wbj"] = {"framework": "Ruta 2030 Wall Street Agent System v2.0.0",
                "categories": _eng["categories"], "raw_total": _eng["raw_total"],
                "total_confidence": _eng["total_confidence"], "band": _gates["band"],
                "profile": _gates["profile"], "classification": _gates["classification"],
                "passed_gates": _gates["passed_gates"], "failed_gates": _gates["failed_gates"],
                "overrides": _gates["overrides"], "scores_source": "engine determinista (metodología de Victor)"}
            analisis_json["victor_targets_detail"] = _eng.get("victor_targets_detail")
            analisis_json["financials_annual"] = _eng.get("financials_annual")
            analisis_json["scores_source"] = "victor"

        # ── MEMORY: compare with prior report + persist this one ─────────────
        memory_comparison = compute_memory_comparison(
            prior_report, precio_actual, analisis_json['fair_value'],
            analisis_json.get('recommendation'), analisis_json.get('conviccion_score'))
        save_report(report_id, ticker, precio_actual, analisis_json['fair_value'],
                    analisis_json['upside_pct'], analisis_json.get('recommendation'),
                    analisis_json.get('conviccion_score'), targets,
                    analisis_json.get('tesis_inversion_completa'),
                    signal_scores=analisis_json.get('signal_scores'))

        _analyze_resp = {
            "report_id": report_id,
            "ticker": ticker,
            "nombre_completo": info.get("longName", ticker),
            "precio_actual": precio_actual,
            "precio_fuente": (_resolve_spot(ticker).get("source") or "yfinance"),
            "precio_as_of": (_resolve_spot(ticker).get("as_of")),
            "pe_ratio": info.get("trailingPE") or "N/A",
            "logo_url": logo_url,
            "fecha_analisis": analisis_timestamp,
            "historial_precios": precios_hist,
            "historial_fechas": fechas_hist,
            "historial_ohlc": ohlc_hist,
            "historial_volumen": volumen_hist,
            "earnings_info": earnings_info,
            "insiders_snapshot": insiders_snapshot,
            "memory_comparison": memory_comparison,
            "deep_memory": _deep_mem_meta,
            "open_options": _open_opts,
            "portfolio_fit": portfolio_fit,
            "noticias_reales": noticias_formateadas,
            "targets": targets,
            "methodology": methodology,
            "analisis": analisis_json
        }
        try:
            save_report_payload(report_id, _analyze_resp)   # #4 — archivo durable en el servidor
        except Exception:
            pass
        return _analyze_resp
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



# ─────────────────────────────────────────────────────────────────────────────
# #4 DEBATE ADVERSARIAL — Toro + Oso + Árbitro (gemini-2.5-pro)
# ─────────────────────────────────────────────────────────────────────────────
def _is_quota_error(e):
    s = str(e)
    return ("RESOURCE_EXHAUSTED" in s or "429" in s or "quota" in s.lower()
            or "exhausted" in s.lower())


def _retry_delay_secs(e, default=8.0, cap=20.0):
    m = re.search(r"retry in (\d+(?:\.\d+)?)\s*s", str(e)) or re.search(r"retryDelay'?:?\s*'?(\d+)", str(e))
    try:
        return min(float(m.group(1)), cap) if m else default
    except Exception:
        return default


def _openai_json(prompt, keys, temp=0.3, model="gpt-4o"):
    """Fallback de proveedor (ChatGPT): pide JSON estricto a OpenAI con response_format json_object."""
    if not OPENAI_API_KEY:
        raise RuntimeError("OpenAI no configurado (OPENAI_API_KEY vacío).")
    sysmsg = ("Devuelve EXCLUSIVAMENTE un objeto JSON válido (sin markdown, sin ```), "
              "con exactamente estas claves (string salvo las numéricas obvias): " + ", ".join(keys) + ".")
    resp = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
        json={"model": model, "temperature": temp, "response_format": {"type": "json_object"},
              "messages": [{"role": "system", "content": sysmsg},
                           {"role": "user", "content": prompt}]},
        timeout=90)
    if resp.status_code != 200:
        raise RuntimeError(f"OpenAI error {resp.status_code}: {resp.text[:200]}")
    txt = resp.json()["choices"][0]["message"]["content"].strip()
    txt = re.sub(r"^```(?:json)?\s*|\s*```$", "", txt, flags=re.S).strip()
    return json.loads(txt)


def _grok_json(prompt, keys, temp=0.4):
    """Fallback when Gemini is out of quota: ask Grok (x.ai) for strict JSON."""
    if not XAI_API_KEY:
        raise RuntimeError("Grok no configurado (XAI_API_KEY vacío).")
    sysmsg = ("Devuelve EXCLUSIVAMENTE un objeto JSON válido (sin markdown, sin ```), "
              "con exactamente estas claves (todas string salvo las numéricas obvias): "
              + ", ".join(keys) + ".")
    resp = requests.post(
        "https://api.x.ai/v1/chat/completions",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {XAI_API_KEY}"},
        json={"model": "grok-3", "temperature": temp,
              "messages": [{"role": "system", "content": sysmsg},
                           {"role": "user", "content": prompt}]},
        timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(f"Grok error {resp.status_code}: {resp.text[:200]}")
    txt = resp.json()["choices"][0]["message"]["content"].strip()
    txt = re.sub(r"^```(?:json)?\s*|\s*```$", "", txt, flags=re.S).strip()
    return json.loads(txt)


def _debate_gen(prompt, schema, temp, keys, model="gemini-2.5-flash", force_grok=False):
    """Gemini structured generation with transient-429 retry; on quota exhaustion,
    fall back to Grok JSON so the debate still completes. If force_grok=True, skip
    Gemini entirely (lets the user bypass Gemini's free-tier 20/day limit)."""
    if force_grok:
        return _grok_json(prompt, keys, temp), "grok"
    last = None
    for attempt in range(2):
        try:
            r = client_gemini.models.generate_content(
                model=model, contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json", response_schema=schema, temperature=temp))
            return json.loads(r.text), "gemini"
        except Exception as e:
            last = e
            if _is_quota_error(e):
                if attempt == 0:
                    time.sleep(_retry_delay_secs(e))
                    continue
                # cuota de Gemini agotada → ChatGPT (OpenAI), y si falla → Grok
                try:
                    return _openai_json(prompt, keys, temp), "openai"
                except Exception:
                    return _grok_json(prompt, keys, temp), "grok"
            raise
    if last:
        raise last


# ─────────────────────────────────────────────────────────────────────────────
def _analyze_structured(prompt, temp=0.2):
    """#7 — genera el reporte estructurado con RESPALDO DE PROVEEDOR: Gemini (schema) → ChatGPT
    (json_object) → Grok. Devuelve (dict, fuente). El downstream usa .get() con defaults, así que un
    JSON parcial del respaldo degrada con gracia en vez de tumbar el endpoint cuando se agota la cuota."""
    last = None
    for attempt in range(2):
        try:
            r = client_gemini.models.generate_content(
                model="gemini-2.5-flash", contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json", response_schema=VertexDeepAnalysis, temperature=temp))
            return json.loads(r.text), "gemini"
        except Exception as e:
            last = e
            if _is_quota_error(e) and attempt == 0:
                time.sleep(_retry_delay_secs(e))
                continue
            break
    try:
        keys = list(getattr(VertexDeepAnalysis, "model_fields", None)
                    or getattr(VertexDeepAnalysis, "__fields__", {}) or [])
    except Exception:
        keys = []
    for fn, src in ((_openai_json, "openai (ChatGPT)"), (_grok_json, "grok")):
        try:
            return fn(prompt, keys, temp), src
        except Exception as e2:
            last = e2
    raise last if last else RuntimeError("Generación estructurada falló en todos los proveedores")


@app.get("/api/analyze-debate")
def analyze_debate(ticker: str, engine: str = "auto"):
    """Cascada adversarial: un agente TORO y un agente OSO construyen el mejor caso
    de cada lado, y un ÁRBITRO (gemini-2.5-pro) reconcilia sin sesgo de confirmación.
    engine='auto' (Gemini con respaldo Grok) o 'grok' (forzar Grok, evita el límite
    free-tier de Gemini). Aditivo y separado de /api/analyze."""
    ticker = ticker.upper().strip()
    force_grok = (engine or "").lower() == "grok"
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        hist = stock.history(period="6mo")
        if hist.empty:
            raise HTTPException(status_code=404, detail="Sin datos")
        precio_actual = round(float(hist['Close'].iloc[-1]), 2)
        inst = calculate_institutional_targets(ticker, info, hist)
        targets = inst["targets"]; meth = inst["methodology"]
        finnhub_ctx = format_finnhub_context(ticker)
        try:
            ins_ctx = format_insiders_context(fetch_insiders_data(stock, ticker))
        except Exception:
            ins_ctx = ""
        raw_news = stock.news if stock.news else []
        headlines = " | ".join([n.get("title", "") for n in raw_news[:5]])

        context = f"""ACCIÓN: {ticker} ({info.get('longName', ticker)})
Precio spot: ${precio_actual} | P/E: {info.get('trailingPE', 'N/A')} | Fwd P/E: {info.get('forwardPE', 'N/A')}
Market Cap: {info.get('marketCap', 'N/A')} | Rev growth YoY: {info.get('revenueGrowth', 'N/A')}
Márgenes: gross {info.get('grossMargins', 'N/A')} / EBITDA {info.get('ebitdaMargins', 'N/A')} | FCF: {info.get('freeCashflow', 'N/A')} | Beta: {info.get('beta', 'N/A')}
Target 12M (Vertex): Bull ${targets['12m']['bull']} / Base ${targets['12m']['base']} / Bear ${targets['12m']['bear']} | Analyst mean: ${meth.get('analyst_mean', 'N/A')} | DCF: ${meth.get('dcf_fair_value', 'N/A')}
Vol anual: {meth.get('annual_volatility_pct', 'N/A')}% | ATR-14: ${meth.get('atr_14', 'N/A')}
Insiders/13F: {ins_ctx or 'N/A'}
Finnhub (fundamentales/news/sentiment/insiders/congreso): {finnhub_ctx or 'N/A'}
Noticias: {headlines or 'N/A'}"""

        BULL_KEYS = ["thesis", "catalysts", "why_underappreciated", "strongest_point"]
        BEAR_KEYS = ["thesis", "risks", "what_breaks_it", "strongest_point"]
        VERDICT_KEYS = ["winner", "lean", "confidence", "key_disagreement",
                        "what_would_flip", "synthesis", "p_bull_correct"]
        used = []

        def gem(prompt, schema, temp, keys, model="gemini-2.5-flash"):
            out, src = _debate_gen(prompt, schema, temp, keys, model=model, force_grok=force_grok)
            used.append(src)
            return out

        bull = gem(f"""Eres el analista TORO más brillante y agresivo de Vertex Holding Group. Construye el caso ALCISTA más fuerte y convincente posible para {ticker} — el steelman del comprador. Usa los datos, sé específico. NO seas equilibrado: tu único rol es el mejor caso alcista.

{context}

Entrega la tesis alcista más fuerte, 3-5 catalizadores concretos, por qué el mercado lo subestima ahora, y tu punto más fuerte e irrefutable.""", BullCase, 0.45, BULL_KEYS)

        bear = gem(f"""Eres el analista OSO más implacable y escéptico de Vertex Holding Group. DESTRUYE la tesis alcista de {ticker} con el caso BAJISTA más fuerte posible — el steelman del vendedor. Usa los datos, sé específico. NO seas equilibrado: tu único rol es el mejor caso bajista.

{context}

El TORO argumentó: "{bull.get('thesis', '')}" (su punto más fuerte: "{bull.get('strongest_point', '')}").
Rebátelo. Entrega la tesis bajista más fuerte, 3-5 riesgos concretos, el escenario que rompe la tesis y cuánto downside implica, y tu punto más fuerte e irrefutable.""", BearCase, 0.45, BEAR_KEYS)

        verdict_json = gem(f"""Eres el CIO árbitro de Vertex Holding Group. Dos analistas debatieron {ticker}. Reconcilia SIN sesgo de confirmación, con disciplina y honestidad calibrada.

{context}

CASO TORO:
- Tesis: {bull.get('thesis', '')}
- Catalizadores: {bull.get('catalysts', '')}
- Punto más fuerte: {bull.get('strongest_point', '')}

CASO OSO:
- Tesis: {bear.get('thesis', '')}
- Riesgos: {bear.get('risks', '')}
- Qué lo rompe: {bear.get('what_breaks_it', '')}
- Punto más fuerte: {bear.get('strongest_point', '')}

Determina quién tiene el caso más fuerte (TORO/OSO/EMPATE), el punto central de desacuerdo, qué evidencia específica y observable cambiaría tu recomendación, una síntesis equilibrada, la recomendación reconciliada (BUY/HOLD/SELL/AVOID), la confianza calibrada 0-100, y la probabilidad 0-100 de que el toro tenga razón a 12 meses.""",
            DebateVerdict, 0.2, VERDICT_KEYS, model="gemini-2.5-pro")

        return {"ticker": ticker, "nombre_completo": info.get("longName", ticker),
                "precio_actual": precio_actual, "target_12m": targets['12m'],
                "analyst_mean": meth.get('analyst_mean'), "bull": bull, "bear": bear,
                "verdict": verdict_json,
                "engine": ("grok-3 (forzado)" if force_grok else "grok-3 (respaldo)" if "grok" in used else "gemini"),
                "generated_at": datetime.now().strftime('%m/%d/%Y, %I:%M:%S %p')}
    except HTTPException:
        raise
    except Exception as e:
        if _is_quota_error(e):
            raise HTTPException(status_code=429, detail=(
                "Cuota de Gemini agotada (free tier: ~20 req/día en gemini-2.5-flash) y el "
                "respaldo Grok no estuvo disponible. Reintenta en ~20s, o activa facturación en "
                "Google AI Studio / usa otra API key. Configura XAI_API_KEY para tener respaldo automático."))
        raise HTTPException(status_code=500, detail=f"Debate error: {str(e)}")


# ─────────────────────────────────────────────────────────────────────────────
# INSIDERS + 13F (SEC EDGAR) + WATCHLIST
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/insiders")
def get_insiders(ticker: str):
    """Insider transactions (Form 4) + institutional holders (13F) + EDGAR links,
    enriched with a short AI interpretation of what the smart money is doing."""
    ticker_clean = ticker.upper().strip()
    try:
        stock = yf.Ticker(ticker_clean)
        try:
            info = stock.info
        except Exception:
            info = {}
        company_name = info.get("longName", ticker_clean)
        ins = fetch_insiders_data(stock, ticker_clean)

        # ── AI interpretation (Gemini) ───────────────────────────────────────
        ai_text = ""
        try:
            ctx = format_insiders_context(ins)
            if ctx:
                prompt = (
                    f"Eres analista institucional de Vertex AI. Interpreta la actividad de insiders y fondos "
                    f"institucionales (13F) de {company_name} ({ticker_clean}).\n\nDATOS:\n{ctx}\n\n"
                    "Responde en espanol, conciso y accionable, en 3 secciones:\n"
                    "**1. QUE ESTAN HACIENDO LOS INSIDERS** — compras/ventas netas y que señala.\n"
                    "**2. FLUJO INSTITUCIONAL (13F)** — quien acumula o reduce y que implica.\n"
                    "**3. VEREDICTO SMART MONEY** — el dinero inteligente esta entrando o saliendo, y que deberia hacer el inversor."
                )
                resp = client_gemini.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=prompt,
                    config=types.GenerateContentConfig(temperature=0.3, max_output_tokens=1500)
                )
                ai_text = resp.text
        except Exception as ex:
            ai_text = f"[AI no disponible: {str(ex)}]"

        return {
            "ticker": ticker_clean,
            "company_name": company_name,
            "transactions": ins.get("transactions", []),
            "summary": ins.get("summary", {}),
            "institutional": ins.get("institutional", []),
            "major_holders": ins.get("major_holders", {}),
            "edgar": ins.get("edgar", {}),
            "ai_analysis": ai_text,
            "generated_at": datetime.now().strftime('%m/%d/%Y, %I:%M:%S %p'),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/watchlist-radar")
def get_watchlist_radar(ticker: str):
    """#4 — Fila de watchlist como RADAR DE SEÑALES (no solo quote): convicción institucional QD,
    premium neto, muros GEX / gamma flip / max pain, y ALERTAS accionables (earnings cercano,
    flujo Tipo A ≥$5M, precio pegado a un muro)."""
    tk = ticker.upper().strip()
    out = {"ticker": tk, "alerts": [], "signals": {},
           "generated_at": datetime.now().strftime('%I:%M:%S %p')}
    spot, spot_src = _live_spot(tk)
    out["price"] = spot
    out["price_source"] = spot_src
    # Earnings + alerta
    try:
        stock = yf.Ticker(tk)
        try:
            info = stock.info
        except Exception:
            info = {}
        earn = fetch_earnings_info(stock, info)
        out["next_earnings_label"] = earn.get("label")
        d = earn.get("days_until")
        out["next_earnings_days"] = d
        if isinstance(d, (int, float)) and 0 <= d <= 7:
            out["alerts"].append({"type": "earnings", "level": "warn", "msg": f"Earnings en {int(d)}d"})
    except Exception:
        pass
    # Convicción institucional + Tipo A (transacción única ≥ $5M)
    try:
        fl = quantdata_flow(tk)
        conv = _qd_conviction(fl) if fl else None
        if conv:
            out["signals"]["conviction"] = {"bias": conv.get("bias"), "strength_pct": conv.get("strength_pct"),
                                            "qualifying": conv.get("qualifying")}
            st = conv.get("strong_trades") or []
            if st and _safe_num(st[0].get("premium")) >= 5_000_000:
                top = st[0]
                side = "alcista" if top.get("side") == "CALL" else "bajista"
                out["alerts"].append({"type": "flow", "level": "hot",
                                      "msg": f"Flujo Tipo A {side} (${_safe_num(top['premium'])/1e6:.1f}M)"})
    except Exception:
        pass
    # Premium neto
    try:
        npm = quantdata_net_premium(tk)
        if isinstance(npm, dict):
            out["signals"]["net_premium"] = _safe_num(npm.get("net_premium")) or None
    except Exception:
        pass
    # Muros GEX + max pain + alerta de proximidad
    try:
        ex = quantdata_exposure(tk, "GAMMA")
        walls = _qd_gex_walls(ex, spot) if (ex and spot) else None
        if walls:
            out["signals"]["walls"] = {"call_wall": walls.get("call_wall"), "put_wall": walls.get("put_wall"),
                                       "gamma_flip": walls.get("gamma_flip")}
            for nm, w in (("call wall", walls.get("call_wall")), ("put wall", walls.get("put_wall")),
                          ("gamma flip", walls.get("gamma_flip"))):
                if w and spot and abs(spot - w) / spot <= 0.01:
                    out["alerts"].append({"type": "level", "level": "info", "msg": f"Precio pegado a {nm} (${w})"})
        mp = quantdata_max_pain(tk)
        if mp:
            out["signals"]["max_pain"] = mp
    except Exception:
        pass
    return _json_safe(out)


@app.get("/api/alerts/scan")
def alerts_scan(tickers: str = "", max_tickers: int = 12):
    """#4 — Escaneo agregado de alertas accionables sobre TODA la watchlist en una sola llamada.
    Reúne las alertas de watchlist-radar (earnings cercano, flujo Tipo A ≥$5M, precio pegado a un muro/flip)
    de cada ticker y les pone un `id` estable (ticker:tipo:msg) para que el frontend deduplique y solo
    notifique las NUEVAS. El frontend lo pollea cada pocos minutos y dispara toast + campana."""
    tks = [t.strip().upper() for t in (tickers or "").split(",") if t.strip()][:max(1, min(max_tickers, 25))]
    now_ms = int(time.time() * 1000)
    alerts = []
    for tk in tks:
        try:
            rad = get_watchlist_radar(tk)
        except Exception:
            continue
        for a in (rad.get("alerts") or []):
            typ = a.get("type", "alert")
            msg = a.get("msg", "")
            alerts.append({
                "id": f"{tk}:{typ}:{msg}",
                "ticker": tk, "type": typ, "level": a.get("level", "info"),
                "msg": msg, "ts": now_ms,
            })
    # ordena por severidad (warn > hot > info) para que lo importante salga primero
    _sev = {"warn": 0, "hot": 1, "info": 2}
    alerts.sort(key=lambda x: _sev.get(x["level"], 3))
    return {"ok": True, "checked_at": now_ms, "n": len(alerts), "alerts": alerts}


@app.get("/api/watchlist-quote")
def get_watchlist_quote(ticker: str):
    """Lightweight one-shot quote for the Watchlist: price, day change, next earnings,
    Wall Street target, and an insider signal — everything a watchlist row needs."""
    ticker_clean = ticker.upper().strip()
    try:
        stock = yf.Ticker(ticker_clean)
        try:
            info = stock.info
        except Exception:
            info = {}

        # Live price + previous close via fast_info
        live_price = None
        prev_close = None
        try:
            fi = stock.fast_info
            live_price = getattr(fi, "last_price", None) or getattr(fi, "regular_market_price", None)
            prev_close = getattr(fi, "previous_close", None) or getattr(fi, "regular_market_previous_close", None)
        except Exception:
            pass
        if not live_price:
            live_price = info.get("currentPrice") or info.get("regularMarketPrice")
        if not prev_close:
            prev_close = info.get("previousClose")

        change_pct = None
        if live_price and prev_close and prev_close > 0:
            change_pct = round(((live_price - prev_close) / prev_close) * 100, 2)

        earnings = fetch_earnings_info(stock, info)
        logo_url = obtener_logo(ticker_clean, info.get("website", ""))

        return {
            "ticker": ticker_clean,
            "name": info.get("longName", ticker_clean),
            "logo_url": logo_url,
            "price": round(float(live_price), 2) if live_price else None,
            "prev_close": round(float(prev_close), 2) if prev_close else None,
            "change_pct": change_pct,
            "target_mean": info.get("targetMeanPrice"),
            "target_upside_pct": round(((info.get("targetMeanPrice") - live_price) / live_price) * 100, 2)
                                  if (info.get("targetMeanPrice") and live_price) else None,
            "next_earnings_label": earnings.get("label"),
            "next_earnings_days": earnings.get("days_until"),
            "market_cap": info.get("marketCap"),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# MULTI-MODEL CASCADE ENDPOINT — Gemini (analyst) vs Grok (sentiment) + arbiter
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/api/cascade")
def get_cascade(ticker: str):
    """Runs a fast analyst view (Gemini) and a fast sentiment view (Grok). If they
    conflict, an independent 3rd model arbitrates and issues the final verdict."""
    ticker_clean = ticker.upper().strip()
    try:
        stock = yf.Ticker(ticker_clean)
        try: info = stock.info
        except Exception: info = {}
        company = info.get("longName", ticker_clean)
        price   = info.get("currentPrice") or info.get("regularMarketPrice") or "N/A"
        ctx = (f"{company} ({ticker_clean}) | Precio ${price} | P/E {info.get('trailingPE','N/A')} | "
               f"Fwd P/E {info.get('forwardPE','N/A')} | Target WS ${info.get('targetMeanPrice','N/A')} | "
               f"Rev growth {info.get('revenueGrowth','N/A')} | Margenes {info.get('grossMargins','N/A')}")

        # ── Model 1: Gemini analyst (fundamental/valuation) ──────────────────
        gemini_view, gemini_verdict = "", None
        try:
            gp = (f"Eres analista cuantitativo institucional. Da tu recomendacion para {ctx}. "
                  "En 3-4 frases justifica con valoracion y fundamentales. "
                  "Termina OBLIGATORIamente con una linea: 'VEREDICTO: BUY' o 'VEREDICTO: HOLD' o 'VEREDICTO: SELL'. Responde en espanol.")
            gr = client_gemini.models.generate_content(
                model="gemini-2.5-flash", contents=gp,
                config=types.GenerateContentConfig(temperature=0.2, max_output_tokens=500))
            gemini_view = (gr.text or "").strip()
            gemini_verdict = _extract_verdict(gemini_view)
        except Exception as e:
            gemini_view = f"[Gemini no disponible: {e}]"

        # ── Model 2: Grok sentiment (market psychology) ──────────────────────
        grok_view, grok_verdict = "", None
        try:
            sp = (f"Eres experto en sentimiento de mercado con acceso en tiempo real a X/Twitter y Reddit. "
                  f"Cual es el sentimiento actual sobre {ticker_clean} ({company})? En 3-4 frases. "
                  "Termina OBLIGATORIamente con: 'VEREDICTO: BUY' (sentimiento alcista), 'VEREDICTO: HOLD' (neutral) o 'VEREDICTO: SELL' (bajista). Responde en espanol.")
            r = requests.post("https://api.x.ai/v1/chat/completions",
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {XAI_API_KEY}"},
                json={"model": "grok-3", "messages": [{"role": "user", "content": sp}],
                      "max_tokens": 500, "temperature": 0.3},
                timeout=90)
            if r.status_code == 200:
                grok_view = r.json()["choices"][0]["message"]["content"].strip()
                grok_verdict = _extract_verdict(grok_view)
            else:
                grok_view = f"[Grok error {r.status_code}]"
        except Exception as e:
            grok_view = f"[Grok no disponible: {e}]"

        # ── Conflict detection + arbiter ─────────────────────────────────────
        def _bucket(v):
            if v in ("BUY",): return "bull"
            if v in ("SELL", "AVOID"): return "bear"
            if v in ("HOLD",): return "neutral"
            return None
        gb, sb = _bucket(gemini_verdict), _bucket(grok_verdict)
        conflict = bool(gb and sb and {gb, sb} == {"bull", "bear"})

        arbiter_text, arbiter_model, final_verdict = "", None, None
        if conflict:
            ap = (f"Dos modelos de IA analizaron {company} ({ticker_clean}) y LLEGARON A CONCLUSIONES OPUESTAS.\n\n"
                  f"MODELO 1 — Analista cuantitativo (Gemini):\n{gemini_view}\n\n"
                  f"MODELO 2 — Sentimiento de mercado (Grok):\n{grok_view}\n\n"
                  "Como ARBITRO experto e independiente, resuelve el conflicto: explica que modelo tiene el argumento "
                  "mas solido y por que, pondera fundamentales vs sentimiento, y emite el VEREDICTO FINAL definitivo. "
                  "Termina con: 'VEREDICTO: BUY' o 'VEREDICTO: HOLD' o 'VEREDICTO: SELL'. Responde en espanol.")
            arbiter_text, arbiter_model = _arbiter_call(ap)
            final_verdict = _extract_verdict(arbiter_text)
        else:
            final_verdict = gemini_verdict or grok_verdict

        return {
            "ticker": ticker_clean, "company_name": company, "price": price,
            "gemini_view": gemini_view, "gemini_verdict": gemini_verdict,
            "grok_view": grok_view, "grok_verdict": grok_verdict,
            "conflict": conflict, "arbiter_used": conflict,
            "arbiter_model": arbiter_model, "arbiter_text": arbiter_text,
            "final_verdict": final_verdict,
            "generated_at": datetime.now().strftime('%m/%d/%Y, %I:%M:%S %p'),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# ACCURACY TRACKER — scores past reports against actual price action
# ─────────────────────────────────────────────────────────────────────────────
_CALIB_CACHE = {"ts": 0.0, "data": None}


def _dir_hit(rec, ret, flat=5.0):
    """Acierto direccional crudo (se usa para todos los buckets)."""
    if rec == "BUY":
        return ret > 0
    if rec in ("SELL", "AVOID"):
        return ret < 0
    return abs(ret) < flat

_SETUP_LBL = {"flujo_institucional_opciones": "Flujo", "fundamentales": "Fundamental", "earnings": "Earnings",
              "tecnicos": "Técnico", "news_sec": "News/SEC", "macro": "Macro", "riesgo": "Riesgo"}
_SETUP_W = {"flujo_institucional_opciones": 0.25, "fundamentales": 0.20, "earnings": 0.20,
            "tecnicos": 0.15, "news_sec": 0.10, "macro": 0.05, "riesgo": 0.05}


def _report_setup(r):
    """Tipo de setup dominante de un reporte = la dimensión de mayor contribución (score×peso) en signal_scores.
    Dice qué CLASE de señal manejó la llamada (Flujo / Fundamental / Earnings / Técnico / News·SEC / ...)."""
    try:
        ss = json.loads(r.get("signal_scores") or "{}")
    except Exception:
        return "n/d"
    best, bv = None, None
    for k, w in _SETUP_W.items():
        d = ss.get(k)
        if not isinstance(d, dict):
            continue
        try:
            sc = float(d.get("score", 0) or 0)
        except Exception:
            sc = 0.0
        if bv is None or sc * w > bv:
            bv, best = sc * w, k
    return _SETUP_LBL.get(best, "n/d") if best else "n/d"


def compute_calibration_stats():
    """#2 — Tasa de acierto realizada de las llamadas pasadas. Mejoras sobre la versión previa:
    (a) `by_recommendation` se puntúa UNA vez por reporte en su horizonte más largo disponible →
        muestras INDEPENDIENTES (antes mezclaba 30/90/180 del mismo reporte e inflaba n ~3x);
    (b) hit-rate RELATIVO a SPY (alpha) además del absoluto — en mercado alcista el absoluto engaña;
    (c) granularidad `by_recommendation_horizon` por 30/90/180d.
    Mantiene la forma {hit_rate,n} que consumen calibrate_conviction y el anclaje #4."""
    try:
        conn = _db()
        rows = [dict(r) for r in conn.execute("SELECT * FROM reports ORDER BY created_ts DESC").fetchall()]
        conn.close()
    except Exception:
        return None
    if not rows:
        return None
    spy = _cached_price_series("SPY")
    now = datetime.now().timestamp()

    dedup_hits, dedup_alpha = [], []          # un voto por reporte (horizonte más largo)
    by_rec, by_rec_alpha = {}, {}             # idem, por recomendación
    by_tk, by_setup = {}, {}                   # hit-rate realizado por TICKER y por TIPO DE SETUP (alimentan el Kelly adaptativo)
    hz_hits = {"30": {}, "90": {}, "180": {}}  # por horizonte y recomendación (granular)
    err_list = []
    for r in rows:
        base_p = r.get("price_at_analysis")
        if not base_p:
            continue
        rec = (r.get("recommendation") or "").upper()
        created = r.get("created_ts", 0)
        pred_up = r.get("upside_pct")
        series = _cached_price_series(r["ticker"])
        base_spy = _price_at(spy, created)
        longest = None                         # (ret, alpha, hit, alpha_hit) del mayor horizonte maduro
        for days in (30, 90, 180):
            hts = created + days * 86400
            if hts > now:
                continue
            p = _price_at(series, hts)
            if not p:
                continue
            ret = (p - base_p) / base_p * 100
            hit = _dir_hit(rec, ret)
            hz_hits[str(days)].setdefault(rec, []).append(hit)
            # alpha vs SPY: ¿batió (BUY) / evitó mejor que (SELL) al mercado?
            alpha_hit = None
            if base_spy and base_spy > 0:
                p_spy = _price_at(spy, hts)
                if p_spy:
                    spy_ret = (p_spy - base_spy) / base_spy * 100
                    alpha = ret - spy_ret
                    if rec == "BUY":
                        alpha_hit = alpha > 0
                    elif rec in ("SELL", "AVOID"):
                        alpha_hit = alpha < 0
                    else:
                        alpha_hit = abs(alpha) < 5
            longest = (ret, hit, alpha_hit)
            if days == 180 and pred_up is not None:
                try:
                    err_list.append(ret - float(pred_up))
                except (TypeError, ValueError):
                    pass
        if longest is not None:
            ret, hit, alpha_hit = longest
            dedup_hits.append(hit)
            by_rec.setdefault(rec, []).append(hit)
            by_tk.setdefault(r["ticker"], []).append(hit)
            by_setup.setdefault(_report_setup(r), []).append(hit)
            if alpha_hit is not None:
                dedup_alpha.append(alpha_hit)
                by_rec_alpha.setdefault(rec, []).append(alpha_hit)

    if not dedup_hits:
        return None

    def rate(lst):
        return round(100 * sum(lst) / len(lst), 1) if lst else None
    rec_rates = {k: {"hit_rate": rate(v), "n": len(v)} for k, v in by_rec.items()}
    tk_rates = {k: {"hit_rate": rate(v), "n": len(v)} for k, v in by_tk.items()}
    setup_rates = {k: {"hit_rate": rate(v), "n": len(v)} for k, v in by_setup.items()}
    rec_alpha = {k: {"hit_rate": rate(v), "n": len(v)} for k, v in by_rec_alpha.items()}
    hz_rates = {hz: {k: {"hit_rate": rate(v), "n": len(v)} for k, v in d.items()}
                for hz, d in hz_hits.items()}
    avg_err = round(sum(err_list) / len(err_list), 1) if err_list else None
    return {"overall_hit_rate": rate(dedup_hits), "n": len(dedup_hits),
            "by_recommendation": rec_rates, "avg_upside_error_pct": avg_err,
            "by_ticker": tk_rates, "by_setup": setup_rates,
            "alpha_hit_rate": rate(dedup_alpha), "alpha_n": len(dedup_alpha),
            "by_recommendation_alpha": rec_alpha, "by_recommendation_horizon": hz_rates,
            "n_reports": len(rows)}


def get_calibration_cached(ttl=600):
    """Cached calibration stats (TTL seconds) to avoid recomputing yfinance history on every analysis."""
    now = datetime.now().timestamp()
    if _CALIB_CACHE["data"] is not None and (now - _CALIB_CACHE["ts"]) < ttl:
        return _CALIB_CACHE["data"]
    data = compute_calibration_stats()
    _CALIB_CACHE["ts"] = now
    _CALIB_CACHE["data"] = data
    return data


def calibrate_conviction(raw_conviction, recommendation, calib, K=10):
    """Empirical-Bayes shrinkage of model conviction toward the realized hit-rate
    for that recommendation type. Returns (calibrated_value, info_dict_or_None)."""
    if not calib or not calib.get("by_recommendation"):
        return raw_conviction, None
    rec = (recommendation or "").upper()
    rr = calib["by_recommendation"].get(rec)
    if not rr or rr.get("hit_rate") is None or not rr.get("n"):
        return raw_conviction, None
    emp = float(rr["hit_rate"]); n = int(rr["n"])
    w_emp = n / (n + K)                       # more history -> trust empirical more
    calibrated = round(raw_conviction * (1 - w_emp) + emp * w_emp, 1)
    return calibrated, {
        "raw_conviction": raw_conviction, "calibrated_conviction": calibrated,
        "empirical_hit_rate": emp, "sample_n": n, "shrinkage_w": round(w_emp, 2),
        "recommendation": rec, "overall_hit_rate": calib.get("overall_hit_rate"),
        "avg_upside_error_pct": calib.get("avg_upside_error_pct")}


# ── #3 INFORMATION COEFFICIENT — ¿cada señal realmente predice? ───────────────
_IC_CACHE = {"ts": 0.0, "data": None, "horizon": None}
_IC_DIMS = ["flujo_institucional_opciones", "fundamentales", "earnings",
            "tecnicos", "news_sec", "macro", "riesgo"]
_IC_LABELS = {"flujo_institucional_opciones": "Flujo opciones", "fundamentales": "Fundamentales",
              "earnings": "Earnings", "tecnicos": "Técnicos", "news_sec": "News/SEC",
              "macro": "Macro", "riesgo": "Riesgo"}
_IC_STATIC_W = {"flujo_institucional_opciones": 0.25, "fundamentales": 0.20, "earnings": 0.20,
                "tecnicos": 0.15, "news_sec": 0.10, "macro": 0.05, "riesgo": 0.05}


def _rankdata(a):
    """Average ranks (1-based), ties averaged — for Spearman without scipy."""
    a = np.asarray(a, dtype=float)
    order = a.argsort()
    ranks = np.empty(len(a), dtype=float)
    ranks[order] = np.arange(1, len(a) + 1)
    sa = a[order]
    i = 0
    while i < len(a):
        j = i
        while j + 1 < len(a) and sa[j + 1] == sa[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = (i + 1 + j + 1) / 2.0
        i = j + 1
    return ranks


def _spearman(x, y):
    if len(x) < 3:
        return None
    rx, ry = _rankdata(x), _rankdata(y)
    if np.std(rx) == 0 or np.std(ry) == 0:
        return None
    return float(np.corrcoef(rx, ry)[0, 1])


def compute_signal_ic(horizon_days=30, min_samples=6):
    """Information Coefficient per signal: rank-correlation between each signal's score
    at analysis time and the realized forward return over `horizon_days`. Validates
    whether a signal actually predicts — and suggests IC-implied weights vs the static ones."""
    try:
        conn = _db()
        rows = [dict(r) for r in conn.execute(
            "SELECT ticker,created_ts,price_at_analysis,signal_scores FROM reports "
            "WHERE signal_scores IS NOT NULL ORDER BY created_ts DESC").fetchall()]
        conn.close()
    except Exception:
        return None
    base = {"ok": True, "horizon_days": horizon_days, "min_samples": min_samples,
            "n_reports_with_scores": len(rows), "n_matured": 0, "signals": [],
            "ic_weights_available": False}
    if not rows:
        base["note"] = ("Aún no hay reportes con puntuaciones por señal. El IC se llena a medida "
                        "que analizas tickers y los reportes maduran (necesitan ≥" + str(horizon_days) + "d).")
        return base

    tickers = list({r["ticker"] for r in rows})
    series_map = {tk: _cached_price_series(tk) for tk in tickers}   # #5 caché compartido

    price_at = _price_at

    now = datetime.now().timestamp()
    pairs = {d: {"s": [], "r": []} for d in _IC_DIMS}
    n_matured = 0
    for r in rows:
        base_p = r.get("price_at_analysis")
        created = r.get("created_ts", 0)
        if not base_p:
            continue
        hts = created + horizon_days * 86400
        if hts > now:
            continue
        p = price_at(series_map.get(r["ticker"], []), hts)
        if not p:
            continue
        fwd = (p - base_p) / base_p * 100.0
        try:
            ss = json.loads(r["signal_scores"]) if r.get("signal_scores") else {}
        except Exception:
            ss = {}
        if not ss:
            continue
        used = False
        for d in _IC_DIMS:
            node = ss.get(d)
            score = node.get("score") if isinstance(node, dict) else node if isinstance(node, (int, float)) else None
            if score is None:
                continue
            try:
                score = float(score)
            except (TypeError, ValueError):
                continue
            pairs[d]["s"].append(score)
            pairs[d]["r"].append(fwd)
            used = True
        if used:
            n_matured += 1

    sigs = []
    ic_pos = {}
    for d in _IC_DIMS:
        s, rr = pairs[d]["s"], pairs[d]["r"]
        n = len(s)
        ic = _spearman(s, rr) if n >= min_samples else None
        tstat = round(ic * math.sqrt(n - 1), 2) if (ic is not None and n > 1) else None
        verdict = None
        if ic is not None:
            if abs(tstat or 0) < 1.5:
                verdict = "ruido"
            elif ic > 0:
                verdict = "predictivo"
            else:
                verdict = "contrario"
        sigs.append({"signal": d, "label": _IC_LABELS[d],
                     "ic": round(ic, 3) if ic is not None else None, "n": n,
                     "t_stat": tstat, "verdict": verdict,
                     "static_weight_pct": round(_IC_STATIC_W[d] * 100)})
        ic_pos[d] = max(0.0, ic) if (ic is not None and (tstat or 0) >= 1.5) else 0.0

    tot = sum(ic_pos.values())
    for sg in sigs:
        sg["ic_weight_pct"] = round(ic_pos[sg["signal"]] / tot * 100) if tot > 0 else None
    base["n_matured"] = n_matured
    base["signals"] = sigs
    base["ic_weights_available"] = tot > 0
    base["note"] = ("IC = correlación de rango (Spearman) entre la puntuación de la señal y el "
                    "retorno realizado a " + str(horizon_days) + "d. |t|≥1.5 ≈ señal con poder predictivo; "
                    "<1.5 = indistinguible de ruido con esta muestra. Los pesos por IC solo usan señales "
                    "significativas y positivas.")
    return base


def get_signal_ic_cached(horizon_days=30, ttl=900):
    now = datetime.now().timestamp()
    if (_IC_CACHE["data"] is not None and _IC_CACHE["horizon"] == horizon_days
            and (now - _IC_CACHE["ts"]) < ttl):
        return _IC_CACHE["data"]
    data = compute_signal_ic(horizon_days=horizon_days)
    _IC_CACHE["ts"] = now
    _IC_CACHE["data"] = data
    _IC_CACHE["horizon"] = horizon_days
    return data


def _apply_ic_tilt(weights, ic_data, k=1.5, cap=0.40, n0=20.0):
    """CIERRA EL LOOP DE APRENDIZAJE: inclina los pesos (ya ajustados por régimen) hacia las señales con
    IC significativo y positivo ('predictivo') y los reduce para las 'contrarias', con SHRINKAGE por nº de
    muestras (lam = n/(n+n0)) y TOPE de ±cap por señal, luego renormaliza a 1. Las señales 'ruido' o con
    pocas muestras quedan en su peso de régimen (no se mueven). Toggle: VERTEX_ADAPTIVE_WEIGHTS=0 lo apaga.
    Devuelve (pesos_inclinados, info)."""
    if os.environ.get("VERTEX_ADAPTIVE_WEIGHTS", "1") == "0":
        return weights, {"applied": False, "reason": "desactivado (VERTEX_ADAPTIVE_WEIGHTS=0)"}
    if not isinstance(ic_data, dict) or not ic_data.get("ic_weights_available"):
        return weights, {"applied": False, "reason": "sin IC significativo aún (las señales todavía maduran)"}
    by = {s["signal"]: s for s in ic_data.get("signals", [])}
    min_n = ic_data.get("min_samples", 6)
    tilts, out = {}, {}
    for d, w in weights.items():
        s = by.get(d)
        tilt = 0.0
        if s and s.get("ic") is not None and s.get("n", 0) >= min_n and abs(s.get("t_stat") or 0) >= 1.5:
            lam = s["n"] / (s["n"] + n0)                       # shrinkage por muestra
            tilt = max(-cap, min(cap, k * float(s["ic"]) * lam))
        out[d] = w * (1.0 + tilt)
        if abs(tilt) > 1e-9:
            tilts[d] = {"tilt_pct": round(tilt * 100, 1), "ic": s.get("ic"), "n": s.get("n"),
                        "verdict": s.get("verdict")}
    tot = sum(out.values()) or 1.0
    out = {d: v / tot for d, v in out.items()}
    return out, {"applied": bool(tilts), "tilts": tilts, "horizon_days": ic_data.get("horizon_days"),
                 "n_matured": ic_data.get("n_matured"),
                 "note": "Pesos inclinados hacia señales con IC significativo (|t|≥1.5), shrinkage por muestra, tope ±40%."}


@app.get("/api/signal-ic")
def get_signal_ic(horizon_days: int = 30):
    """Per-signal Information Coefficient on the user's own track record (#3)."""
    hd = horizon_days if horizon_days in (30, 90, 180) else 30
    data = get_signal_ic_cached(horizon_days=hd)
    if data is None:
        return {"ok": False, "error": "No se pudo computar el IC."}
    return data


# ── #4 RÉGIMEN DE MERCADO — pesos de señales condicionados al régimen ─────────
_REGIME_CACHE = {"ts": 0.0, "data": None}


def compute_regime():
    """Classify the current market regime from liquid signals: volatility (^VIX),
    trend (SPY vs 50/200d SMA + slope), breadth (RSP vs SPY 20d), rates (TLT 20d)."""
    out = {"vol": "normal", "trend": "lateral", "breadth": "neutral", "rates": "estable",
           "vix": None, "spy_vs_200d_pct": None, "detail": {}}
    try:
        vix = yf.Ticker("^VIX").history(period="1mo")
        if not vix.empty:
            v = float(vix["Close"].iloc[-1]); out["vix"] = round(v, 1)
            out["vol"] = "calmo" if v < 15 else "estrés" if v > 25 else "normal"
    except Exception:
        pass
    try:
        spy = yf.Ticker("SPY").history(period="1y")["Close"].dropna()
        if len(spy) > 210:
            last = float(spy.iloc[-1])
            s50 = float(spy.tail(50).mean()); s200 = float(spy.tail(200).mean())
            sma200 = spy.rolling(200).mean()
            slope = float(sma200.iloc[-1] - sma200.iloc[-21])
            out["spy_vs_200d_pct"] = round((last / s200 - 1) * 100, 1)
            if last > s50 and s50 > s200 and slope > 0:
                out["trend"] = "alcista"
            elif last < s200 and slope < 0:
                out["trend"] = "bajista"
            else:
                out["trend"] = "lateral"
            out["detail"]["spy_20d_pct"] = round(float(spy.iloc[-1] / spy.iloc[-21] - 1) * 100, 1)
    except Exception:
        pass
    try:
        rsp = yf.Ticker("RSP").history(period="3mo")["Close"].dropna()
        spy3 = yf.Ticker("SPY").history(period="3mo")["Close"].dropna()
        if len(rsp) > 21 and len(spy3) > 21:
            diff = float(rsp.iloc[-1] / rsp.iloc[-21] - 1) * 100 - float(spy3.iloc[-1] / spy3.iloc[-21] - 1) * 100
            out["breadth"] = "estrecho" if diff < -2 else "amplio" if diff > 2 else "neutral"
            out["detail"]["rsp_minus_spy_20d_pct"] = round(diff, 1)
    except Exception:
        pass
    try:
        tlt = yf.Ticker("TLT").history(period="2mo")["Close"].dropna()
        if len(tlt) > 21:
            r = float(tlt.iloc[-1] / tlt.iloc[-21] - 1) * 100   # TLT down => rates up
            out["detail"]["tlt_20d_pct"] = round(r, 1)
            out["rates"] = "subiendo" if r < -2 else "bajando" if r > 2 else "estable"
    except Exception:
        pass
    out["label"] = (f"Tendencia {out['trend']} · vol {out['vol']} · "
                    f"amplitud {out['breadth']} · tasas {out['rates']}")
    return out


def get_regime_cached(ttl=2700):
    now = datetime.now().timestamp()
    if _REGIME_CACHE["data"] is not None and (now - _REGIME_CACHE["ts"]) < ttl:
        return _REGIME_CACHE["data"]
    data = compute_regime()
    _REGIME_CACHE["ts"] = now
    _REGIME_CACHE["data"] = data
    return data


def regime_signal_weights(base, regime):
    """Tilt the static framework weights by the active regime, then floor+renormalize.
    Returns (adjusted_weights dict summing to 1, list of human-readable adjustment notes)."""
    if not regime:
        return dict(base), []
    w = dict(base)
    notes = []

    def bump(k, d):
        w[k] = w.get(k, 0.0) + d

    vol, trend = regime.get("vol"), regime.get("trend")
    breadth, rates = regime.get("breadth"), regime.get("rates")

    if vol == "estrés":
        bump("riesgo", +0.06); bump("macro", +0.04); bump("fundamentales", +0.03)
        bump("tecnicos", -0.05); bump("flujo_institucional_opciones", -0.04); bump("earnings", -0.04)
        notes.append("Vol en estrés → ↑ riesgo y macro, ↓ técnicos y flujo")
    elif vol == "calmo":
        bump("flujo_institucional_opciones", +0.03); bump("tecnicos", +0.02)
        bump("riesgo", -0.03); bump("macro", -0.02)
        notes.append("Vol calma → ↑ flujo y técnicos")

    if trend == "alcista":
        bump("flujo_institucional_opciones", +0.05); bump("tecnicos", +0.04)
        bump("riesgo", -0.03); bump("macro", -0.03); bump("news_sec", -0.03)
        notes.append("Tendencia alcista → ↑ flujo y técnicos (momentum)")
    elif trend == "bajista":
        bump("riesgo", +0.05); bump("fundamentales", +0.04); bump("macro", +0.03)
        bump("tecnicos", -0.06); bump("flujo_institucional_opciones", -0.03); bump("earnings", -0.03)
        notes.append("Tendencia bajista → ↑ riesgo y fundamentales, ↓ técnicos")
    elif trend == "lateral":
        bump("fundamentales", +0.04); bump("earnings", +0.03)
        bump("tecnicos", -0.05); bump("flujo_institucional_opciones", -0.02)
        notes.append("Mercado lateral → ↑ fundamentales y earnings, ↓ técnicos")

    if breadth == "estrecho":
        bump("riesgo", +0.03); bump("flujo_institucional_opciones", +0.02)
        bump("macro", -0.02); bump("tecnicos", -0.03)
        notes.append("Amplitud estrecha → ↑ riesgo (fragilidad de mega-caps)")

    if rates == "subiendo":
        bump("macro", +0.04); bump("fundamentales", +0.03); bump("tecnicos", -0.03)
        bump("flujo_institucional_opciones", -0.02); bump("earnings", -0.02)
        notes.append("Tasas subiendo → ↑ macro y fundamentales (sensibilidad a duración)")
    elif rates == "bajando":
        bump("flujo_institucional_opciones", +0.02); bump("tecnicos", +0.02)
        bump("macro", -0.02); bump("riesgo", -0.02)
        notes.append("Tasas bajando → ↑ flujo y técnicos")

    for k in w:
        w[k] = max(0.02, w[k])
    tot = sum(w.values()) or 1.0
    return {k: v / tot for k, v in w.items()}, notes


@app.get("/api/regime")
def get_regime():
    """Current market regime + the regime-adjusted signal weights (#4)."""
    regime = get_regime_cached()
    base = {"flujo_institucional_opciones": 0.25, "fundamentales": 0.20, "earnings": 0.20,
            "tecnicos": 0.15, "news_sec": 0.10, "macro": 0.05, "riesgo": 0.05}
    adj, notes = regime_signal_weights(base, regime)
    labels = _IC_LABELS
    weights = [{"signal": k, "label": labels.get(k, k),
                "base_weight_pct": round(base[k] * 100, 1),
                "weight_pct": round(adj[k] * 100, 1),
                "delta_pct": round((adj[k] - base[k]) * 100, 1)} for k in base]
    weights.sort(key=lambda x: x["weight_pct"], reverse=True)
    return {"ok": True, "regime": regime, "adjustments": notes, "weights": weights}


@app.get("/api/reports/list")
def reports_list(limit: int = 60):
    """#4 — Lista de reportes DURABLES desde el servidor (payload completo) para hidratar el archivo
    multi-dispositivo. Cae en silencio si no hay payloads (DBs viejas sin la columna llena)."""
    try:
        conn = _db()
        rows = conn.execute(
            "SELECT payload FROM reports WHERE payload IS NOT NULL ORDER BY created_ts DESC LIMIT ?",
            (int(max(1, min(limit, 200))),)).fetchall()
        conn.close()
        out = []
        for r in rows:
            try:
                out.append(json.loads(r["payload"]))
            except Exception:
                continue
        return {"ok": True, "reports": out}
    except Exception as e:
        return {"ok": False, "error": str(e), "reports": []}


@app.get("/api/report-delete")
def report_delete(report_id: str):
    """#4 — borra un reporte del archivo del servidor (sincroniza el borrado entre dispositivos)."""
    try:
        conn = _db()
        conn.execute("DELETE FROM reports WHERE report_id=?", (report_id,))
        conn.commit(); conn.close()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/calibration")
def get_calibration(horizon: int = 90):
    """#4 — Diagrama de fiabilidad (reliability diagram): ¿tu convicción PREDICHA acierta a esa frecuencia?
    Agrupa los reportes por convicción y compara la confianza media de cada bucket con el hit-rate REAL a
    `horizon` días. Cierra el lazo de calibración — el gráfico que demuestra si tu '70%' de verdad acierta 70%."""
    try:
        conn = _db()
        rows = [dict(r) for r in conn.execute(
            "SELECT ticker,created_ts,price_at_analysis,recommendation,conviction FROM reports "
            "WHERE conviction IS NOT NULL AND price_at_analysis IS NOT NULL ORDER BY created_ts ASC").fetchall()]
        conn.close()
        now = datetime.now().timestamp()
        edges = [(0, 50), (50, 60), (60, 70), (70, 80), (80, 90), (90, 101)]
        buckets = {f"{lo}-{min(hi,100)}": {"preds": [], "hits": []} for (lo, hi) in edges}
        scored = 0
        for r in rows:
            conv = _safe_num(r.get("conviction"))
            base_p = _safe_num(r.get("price_at_analysis"))
            rec = (r.get("recommendation") or "").upper()
            if conv <= 0 or base_p <= 0 or rec not in ("BUY", "SELL", "AVOID"):
                continue
            hts = _safe_num(r.get("created_ts")) + horizon * 86400
            if hts > now:
                continue
            p = _price_at(_cached_price_series(r["ticker"]), hts)
            if not p:
                continue
            ret = (p - base_p) / base_p * 100
            hit = 1 if _dir_hit(rec, ret) else 0
            for (lo, hi) in edges:
                if lo <= conv < hi:
                    key = f"{lo}-{min(hi,100)}"
                    buckets[key]["preds"].append(conv)
                    buckets[key]["hits"].append(hit)
                    scored += 1
                    break
        out, gap_acc = [], 0.0
        for key, b in buckets.items():
            n = len(b["hits"])
            if n == 0:
                out.append({"bucket": key, "n": 0, "predicted": None, "realized": None, "gap": None})
                continue
            pred = round(sum(b["preds"]) / n, 1)
            real = round(100 * sum(b["hits"]) / n, 1)
            out.append({"bucket": key, "n": n, "predicted": pred, "realized": real, "gap": round(real - pred, 1)})
            gap_acc += abs(real - pred) * n
        cal_err = round(gap_acc / scored, 1) if scored else None
        return {"ok": True, "horizon": horizon, "scored": scored, "buckets": out,
                "calibration_error": cal_err,
                "note": ("Calibración perfecta = puntos sobre la diagonal (confianza = acierto real). Por encima de la "
                         "diagonal = pesimista (aciertas MÁS de lo que dices); por debajo = sobreconfiado. Necesita "
                         "reportes con ≥" + str(horizon) + "d de maduración para puntuar.")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _r_outcome(r, series, now):
    """R-multiple del SUBYACENTE para una recomendación, usando el bracket de escenarios del agente:
    bull = reward / bear = stop para un BUY (invertido para SELL/AVOID). 1R = distancia entrada→stop.
    Recorre el path de cierres y resuelve QUÉ nivel se tocó PRIMERO (target → +reward_R · stop → −1R);
    si ninguno, marca a mercado en R al horizonte más largo maduro. Normaliza el retorno por el riesgo
    que el propio agente declaró (la pata bajista), así un +5% con −25% de riesgo (0.2R) ≠ con −5% (1R).
    No necesita tu contrato real: mide la CALIDAD DE LA SEÑAL del agente en unidades de riesgo."""
    entry = _safe_num(r.get("price_at_analysis"))
    bull = _safe_num(r.get("target_bull")); bear = _safe_num(r.get("target_bear"))
    rec = (r.get("recommendation") or "").upper()
    created = _safe_num(r.get("created_ts"))
    if entry <= 0 or not series:
        return None
    matured_hts = None
    for days in (30, 90, 180):
        if created + days * 86400 <= now:
            matured_hts = created + days * 86400
    if matured_hts is None:
        return None
    if not (bull and bear and bull > entry > bear):   # bracket sano requerido
        return None
    if rec == "BUY":
        target, stop, up = bull, bear, True
    elif rec in ("SELL", "AVOID"):
        target, stop, up = bear, bull, False           # gana si baja al bear; stop si sube al bull
    else:
        return None
    one_r = abs(entry - stop)
    if one_r <= 0:
        return None
    t_hit = s_hit = None
    for (ts, px) in sorted(series, key=lambda x: x[0]):
        if ts < created or ts > matured_hts:
            continue
        if up:
            if px >= target and t_hit is None: t_hit = ts
            if px <= stop and s_hit is None: s_hit = ts
        else:
            if px <= target and t_hit is None: t_hit = ts
            if px >= stop and s_hit is None: s_hit = ts
        if t_hit and s_hit:
            break
    reward_r = abs(target - entry) / one_r
    if t_hit and (s_hit is None or t_hit <= s_hit):
        return round(reward_r, 2)        # target primero → gana sus R
    if s_hit and (t_hit is None or s_hit < t_hit):
        return -1.0                       # stop primero → −1R
    px_end = _price_at(series, matured_hts)   # ninguno → marca a mercado en R
    if not px_end:
        return None
    move = (px_end - entry) if up else (entry - px_end)
    return round(move / one_r, 2)


@app.get("/api/track-record")
def get_track_record():
    """#2 — Track record realista: hit-rate por horizonte (30/90/180d) + hit-rate RELATIVO a SPY
    (alpha) + scoring por magnitud + CURVA DE EQUITY simulada (siguiendo cada call) con drawdown."""
    try:
        conn = _db()
        rows = [dict(r) for r in conn.execute("SELECT * FROM reports ORDER BY created_ts ASC").fetchall()]
        conn.close()
        if not rows:
            return {"total_reports": 0, "scored_reports": 0, "hit_rate_30": None, "hit_rate_90": None,
                    "hit_rate_180": None, "n_30": 0, "n_90": 0, "n_180": 0, "detail": [],
                    "alpha_30": None, "alpha_90": None, "alpha_180": None, "equity_curve": None}

        spy = _cached_price_series("SPY")
        now = datetime.now().timestamp()
        buckets = {"30": [], "90": [], "180": []}
        alpha_buckets = {"30": [], "90": [], "180": []}
        detail = []
        equity_pts = []          # curva de equity siguiendo la dirección de cada call (90d)
        equity = 100.0
        peak = 100.0
        max_dd = 0.0
        equity_r_pts = []        # Batch R · curva de R acumulada (suma de R-múltiplos por call maduro)
        cum_r = 0.0
        wins = losses = 0
        tr_recs = []   # una fila por call madura (90d) → desgloses por ticker / régimen / convicción + expectancy
        for r in rows:
            base_p = r.get("price_at_analysis")
            if not base_p:
                continue
            rec = (r.get("recommendation") or "").upper()
            created = r.get("created_ts", 0)
            series = _cached_price_series(r["ticker"])
            base_spy = _price_at(spy, created)
            row_eval = {"ticker": r["ticker"], "date": r.get("created_at"), "recommendation": rec,
                        "price_at_analysis": base_p, "fair_value": r.get("fair_value"), "horizons": {}}
            ret90 = None
            for label, days in (("30", 30), ("90", 90), ("180", 180)):
                hts = created + days * 86400
                if hts > now:
                    continue
                p = _price_at(series, hts)
                if not p:
                    continue
                ret = (p - base_p) / base_p * 100
                hit = _dir_hit(rec, ret)
                buckets[label].append(hit)
                alpha = None
                if base_spy and base_spy > 0:
                    p_spy = _price_at(spy, hts)
                    if p_spy:
                        alpha = round(ret - (p_spy - base_spy) / base_spy * 100, 1)
                        a_hit = (alpha > 0) if rec == "BUY" else (alpha < 0) if rec in ("SELL", "AVOID") else (abs(alpha) < 5)
                        alpha_buckets[label].append(a_hit)
                # magnitud: clasifica el resultado (no solo signo)
                mag = ("ganancia fuerte" if ret >= 8 else "ganancia" if ret > 1 else
                       "plano" if abs(ret) <= 1 else "pérdida" if ret > -8 else "pérdida fuerte")
                row_eval["horizons"][label] = {"price": round(p, 2), "return_pct": round(ret, 1),
                                               "hit": hit, "alpha_pct": alpha, "magnitud": mag}
                if label == "90":
                    ret90 = ret
            if row_eval["horizons"]:
                detail.append(row_eval)
            # curva de equity: posición direccional a 90d (BUY=+ret, SELL=-ret, HOLD ignora)
            if ret90 is not None and rec in ("BUY", "SELL", "AVOID"):
                pnl = ret90 if rec == "BUY" else -ret90
                equity *= (1.0 + pnl / 100.0)
                peak = max(peak, equity)
                max_dd = min(max_dd, (equity - peak) / peak * 100.0)
                equity_pts.append({"date": r.get("created_at"), "ticker": r["ticker"],
                                   "equity": round(equity, 1), "pnl_pct": round(pnl, 1)})
                if pnl > 0: wins += 1
                else: losses += 1
                # desglose: régimen GEX en el momento (del payload) + tier de convicción
                regime = "?"
                try:
                    _pl = json.loads(r.get("payload") or "{}")
                    _ng = (_pl.get("gex") or {}).get("net_gex")
                    if _ng is not None:
                        regime = "GEX+" if float(_ng) >= 0 else "GEX−"
                except Exception:
                    pass
                _cv = r.get("conviction")
                conv_tier = "n/d" if _cv is None else ("alta" if _cv >= 70 else "media" if _cv >= 45 else "baja")
                setup = _report_setup(r)   # #2 — tipo de setup = dimensión dominante (score×peso) de signal_scores
                _rval = _r_outcome(r, series, now)
                tr_recs.append({"ticker": r["ticker"], "regime": regime, "conv": conv_tier, "setup": setup,
                                "dret": pnl, "r": _rval})
                if _rval is not None:
                    cum_r += _rval
                    equity_r_pts.append({"date": r.get("created_at"), "ticker": r["ticker"],
                                         "cum_r": round(cum_r, 2), "r": round(_rval, 2)})

        def rate(lst): return round(100 * sum(lst) / len(lst), 1) if lst else None

        # ── Desglose por ticker / régimen / convicción, con EXPECTANCY (retorno direccional medio por call) ──
        def _agg(items):
            n = len(items)
            if not n:
                return None
            drets = [x["dret"] for x in items]
            w = [d for d in drets if d > 0]; l = [d for d in drets if d <= 0]
            rr = [x["r"] for x in items if x.get("r") is not None]
            rw = [v for v in rr if v > 0]; rl = [v for v in rr if v <= 0]
            return {"n": n, "hit_rate": round(100 * len(w) / n, 1),
                    "expectancy": round(sum(drets) / n, 2),
                    "avg_win": round(sum(w) / len(w), 2) if w else None,
                    "avg_loss": round(sum(l) / len(l), 2) if l else None,
                    "best": round(max(drets), 2), "worst": round(min(drets), 2),
                    "n_r": len(rr),
                    "expectancy_r": round(sum(rr) / len(rr), 2) if rr else None,
                    "win_rate_r": round(100 * len(rw) / len(rr), 1) if rr else None,
                    "avg_win_r": round(sum(rw) / len(rw), 2) if rw else None,
                    "avg_loss_r": round(sum(rl) / len(rl), 2) if rl else None}

        def _grp(key):
            g = {}
            for x in tr_recs:
                g.setdefault(x[key], []).append(x)
            return {k: _agg(v) for k, v in sorted(g.items(), key=lambda kv: -len(kv[1]))}

        breakdown = {"by_ticker": _grp("ticker"), "by_regime": _grp("regime"),
                     "by_conviction": _grp("conv"), "by_setup": _grp("setup"), "overall": _agg(tr_recs),
                     "basis": ("Retorno direccional a 90d por call (a favor de la recomendación). Expectancy = promedio "
                               "por call; >0 = tus calls ganan plata en promedio. 'R' = R-multiple del SUBYACENTE: el "
                               "agente define el bracket (escenario alcista = reward, bajista = stop), 1R = riesgo declarado; "
                               "expectancy R >0 = la señal del agente gana en unidades de riesgo (normaliza el % por el riesgo "
                               "que el propio agente marcó). Régimen = GEX± en el momento del análisis. Sincero: mide la señal "
                               "del agente sobre el subyacente, NO el R real de tu opción (eso requeriría tu CSV de broker).")}
        eq = None
        if equity_pts:
            eq = {"points": equity_pts[-120:], "final": round(equity, 1),
                  "total_return_pct": round(equity - 100.0, 1), "max_drawdown_pct": round(max_dd, 1),
                  "n_trades": wins + losses, "win_rate": rate([True] * wins + [False] * losses),
                  "note": "Equity simulada siguiendo la dirección de cada call a 90d (no incluye apalancamiento de opciones)."}
            if equity_r_pts:                                  # Batch R · curva de R acumulada del subyacente
                _rv = [p["r"] for p in equity_r_pts]
                eq["points_r"] = equity_r_pts[-120:]
                eq["total_r"] = round(cum_r, 2)
                eq["expectancy_r"] = round(cum_r / len(_rv), 2)
                eq["n_r"] = len(_rv)
                eq["r_note"] = "R acumulada del subyacente: cada call suma su R (objetivo cumplido = +reward·R, stop = −1R), usando el bracket del escenario."
        return {
            "total_reports": len(rows), "scored_reports": len(detail),
            "hit_rate_30": rate(buckets["30"]),   "n_30": len(buckets["30"]),
            "hit_rate_90": rate(buckets["90"]),   "n_90": len(buckets["90"]),
            "hit_rate_180": rate(buckets["180"]), "n_180": len(buckets["180"]),
            "alpha_30": rate(alpha_buckets["30"]), "alpha_90": rate(alpha_buckets["90"]),
            "alpha_180": rate(alpha_buckets["180"]),
            "alpha_note": "Alpha = % de calls que batieron a SPY en ese horizonte (lo que importa de verdad).",
            "equity_curve": eq,
            "breakdown": breakdown,
            "detail": detail[-60:][::-1],
            "generated_at": datetime.now().strftime('%m/%d/%Y, %I:%M:%S %p'),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/portfolio-edge")
def portfolio_edge():
    """Batch Confirmación · cruza tu LIBRO (snapshot de equity) con tu EDGE MEDIDO por ticker (track-record).
    Responde la pregunta clave: ¿tu capital está donde tienes ventaja, o sobreponderado donde la expectancy
    es negativa o aún desconocida? Marca holdings con expectancy negativa y peso alto, y dónde está tu mejor edge."""
    holdings = get_portfolio_snapshot() or []
    total = sum(float(h.get("value") or 0) for h in holdings) or 0.0
    try:
        tr = get_track_record()
    except Exception:
        tr = {}
    by_tk = ((tr or {}).get("breakdown") or {}).get("by_ticker") or {}
    rows = []
    for h in holdings:
        tk = (h.get("ticker") or "").upper()
        if not tk:
            continue
        val = float(h.get("value") or 0)
        w = round(100 * val / total, 1) if total else 0.0
        ed = by_tk.get(tk) or {}
        e = ed.get("expectancy_r") if ed.get("expectancy_r") is not None else ed.get("expectancy")
        if not ed.get("n"):
            flag = "unknown"
        elif e is None:
            flag = "flat"
        elif e < 0:
            flag = "neg"
        elif e > 0:
            flag = "edge"
        else:
            flag = "flat"
        rows.append({"ticker": tk, "value": round(val, 2), "weight_pct": w, "flag": flag,
                     "n": ed.get("n", 0), "hit_rate": ed.get("hit_rate"),
                     "expectancy": ed.get("expectancy"), "expectancy_r": ed.get("expectancy_r"),
                     "n_r": ed.get("n_r", 0)})
    insights = []
    for r in rows:
        if r["flag"] == "neg" and r["weight_pct"] >= 10:
            _e = (f"{r['expectancy_r']:+.2f}R" if r["expectancy_r"] is not None else f"{r['expectancy']:+.1f}%")
            insights.append(f"{r['ticker']}: {r['weight_pct']}% del libro con expectancy {_e} (negativa) — sobreponderado donde no tienes ventaja.")
        elif r["flag"] == "unknown" and r["weight_pct"] >= 15:
            insights.append(f"{r['ticker']}: {r['weight_pct']}% del libro sin track-record medible todavía — riesgo a ciegas.")
    best = sorted([(k, v) for k, v in by_tk.items() if v.get("n") and v.get("expectancy_r") is not None],
                  key=lambda kv: kv[1]["expectancy_r"], reverse=True)[:3]
    best_edge = [{"ticker": k, "expectancy_r": v["expectancy_r"], "n": v["n"], "hit_rate": v.get("hit_rate")} for k, v in best]
    for be in best_edge:
        held = next((r for r in rows if r["ticker"] == be["ticker"]), None)
        if be["expectancy_r"] and be["expectancy_r"] > 0 and (not held or held["weight_pct"] < 5):
            insights.append(f"Tu mejor edge está en {be['ticker']} ({be['expectancy_r']:+.2f}R, n={be['n']}) pero pesa "
                            f"{held['weight_pct'] if held else 0}% del libro — infraponderado donde ganas.")
    rows.sort(key=lambda x: x["weight_pct"], reverse=True)
    return {"ok": True, "total_value": round(total, 2), "holdings": rows, "best_edge": best_edge,
            "insights": insights[:5], "n_holdings": len(rows),
            "note": "Cruce de tu libro (último snapshot) con tu expectancy medida por ticker. Donde no hay muestra suficiente, la celda queda 'sin datos'."}


# ─────────────────────────────────────────────────────────────────────────────
# SENTIMENT ENGINE — xAI Grok
# ─────────────────────────────────────────────────────────────────────────────
XAI_API_KEY = os.environ.get("XAI_API_KEY", "")

@app.get("/api/sentiment")
def get_sentiment(ticker: str):
    ticker_clean = ticker.upper().strip()

    company_name = ticker_clean
    ctx = f"Accion: {ticker_clean}"
    try:
        stock = yf.Ticker(ticker_clean)
        info  = stock.info
        company_name = info.get("longName", ticker_clean)
        sector   = info.get("sector",   "N/A")
        industry = info.get("industry", "N/A")
        price    = info.get("currentPrice") or info.get("regularMarketPrice") or "N/A"
        w52      = info.get("52WeekChange")
        mktcap   = info.get("marketCap")
        pe       = info.get("trailingPE") or "N/A"
        ctx = (
            f"{company_name} ({ticker_clean}) | Sector: {sector} | Industry: {industry} | "
            f"Precio actual: ${price} | Market Cap: {'$'+str(round(mktcap/1e9,1))+'B' if mktcap else 'N/A'} | "
            f"Cambio 52W: {str(round(w52*100,1))+'%' if w52 else 'N/A'} | P/E: {pe}"
        )
    except Exception:
        pass

    # ── Fetch REAL Reddit posts to feed the AI as literal context ───────────
    reddit_posts   = fetch_reddit_posts(ticker_clean, limit=8)
    reddit_context = format_reddit_context(reddit_posts)

    # Date window: only consider posts/news from the last 3 months
    three_months_ago = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
    today_str = datetime.now().strftime("%Y-%m-%d")

    # Human-readable (Spanish) date range for display in the report/UI
    _meses_es = ["enero","febrero","marzo","abril","mayo","junio","julio",
                  "agosto","septiembre","octubre","noviembre","diciembre"]
    _start_dt = datetime.now() - timedelta(days=90)
    _end_dt   = datetime.now()
    date_range_label = (
        f"{_start_dt.day} de {_meses_es[_start_dt.month-1]} de {_start_dt.year} "
        f"— {_end_dt.day} de {_meses_es[_end_dt.month-1]} de {_end_dt.year}"
    )

    system_msg = (
        "Eres el maximo experto mundial en psicologia del inversor, finanzas conductuales, analisis "
        "de sentimiento de mercados financieros y especulacion de tesis de inversion basada en el "
        "sentir colectivo. Tienes acceso en tiempo real a X (Twitter), Reddit, StockTwits, noticias "
        "financieras y la web. Tu especialidad es descifrar el estado emocional colectivo de los "
        "inversores: miedo, codicia, euforia, panico, FOMO, efecto manada, aversion a la perdida y "
        "sesgos cognitivos. Analizas por que la gente compra o vende, que narrativas los mueven "
        "emocionalmente, y como la psicologia de masas impacta el precio de las acciones. "
        "REGLA CRITICA: SOLO analizas publicaciones, posts, tweets, noticias y discusiones de los "
        "ULTIMOS 3 MESES (90 dias). Ignora completamente cualquier contenido, evento o narrativa "
        "anterior a ese periodo, incluso si es relevante historicamente — el sentimiento de mercado "
        "cambia rapido y solo lo reciente importa para esta lectura. "
        "Ademas de describir el sentimiento, ESPECULAS y construyes una TESIS DE INVERSION propia "
        "basada en lo que la gente esta diciendo: si el consenso emocional de la comunidad tiene "
        "fundamento, hacia donde podria ir el precio si esa narrativa se cumple, y que escenario es "
        "mas probable. SIEMPRE das datos especificos, porcentajes estimados, ejemplos reales y citas "
        "de lo que dice la gente con fecha aproximada. Eres directo, detallado, exhaustivo y objetivo. "
        "Respondes SIEMPRE en espanol."
    )

    reddit_block = reddit_context if reddit_context else "(No se pudieron extraer posts de Reddit en este momento; usa tu conocimiento general de la comunidad pero respeta la ventana de 90 dias.)"
    user_msg = f"""Analiza el SENTIMIENTO PSICOLOGICO COMPLETO y ACTUAL de los inversores sobre {ticker_clean} ({company_name}).

Datos del activo: {ctx}

VENTANA DE TIEMPO OBLIGATORIA: Analiza UNICAMENTE publicaciones, posts, tweets, discusiones y noticias publicadas entre {three_months_ago} y {today_str} (ultimos 90 dias / 3 meses). Descarta cualquier informacion, narrativa o evento anterior a {three_months_ago}, sin importar que tan relevante haya sido en su momento. Si citas algo, intenta dar la fecha aproximada (semana o mes) dentro de esta ventana.

POSTS REALES DE REDDIT (extraidos en vivo de r/wallstreetbets, r/stocks, r/investing, r/options, r/StockMarket — ordenados por upvotes para ponderar credibilidad). USA ESTOS POSTS REALES como evidencia principal de lo que la comunidad esta diciendo AHORA. Cita y analiza estos posts especificos cuando sea relevante:
{reddit_block}

Responde con EXACTAMENTE estas 7 secciones. Cada seccion debe ser EXHAUSTIVA, MUY DETALLADA, con datos reales, ejemplos especificos, citas con fecha aproximada y cifras concretas. No seas breve — desarrolla cada punto a fondo:

**1. ESTADO EMOCIONAL COLECTIVO (ULTIMOS 3 MESES)**
Cual es la emocion dominante ahora mismo entre los inversores de {ticker_clean}, considerando solo los ultimos 90 dias?
Clasifica: Euforia / Optimismo / Esperanza / Neutral / Ansiedad / Miedo / Panico
Da el porcentaje estimado de Bulls vs Bears en la comunidad en este momento, y como ha evolucionado ese porcentaje semana a semana dentro de los ultimos 3 meses (iba mejorando? empeorando? estable?).
Explica detalladamente que eventos de los ultimos 3 meses generaron ese estado emocional especifico, con fechas aproximadas.

**2. QUE DICE Y PIENSA LA GENTE (ULTIMOS 3 MESES)**
Que narrativa o "story" ha estado circulando sobre {ticker_clean} en X/Twitter, Reddit r/wallstreetbets, r/investing y StockTwits durante los ultimos 90 dias?
Que argumentos concretos usa la gente para justificar comprar o vender, citando ejemplos de discusiones recientes?
Que frases, opiniones o argumentos se repiten mas en la comunidad reciente? Da multiples ejemplos con fecha aproximada.
Ha cambiado la narrativa durante estos 3 meses? Como era al inicio del periodo vs ahora?

**3. PSICOLOGIA Y SESGOS COGNITIVOS (ULTIMOS 3 MESES)**
Que sesgos psicologicos han dominado a los inversores de {ticker_clean} en los ultimos 90 dias?
(FOMO, Efecto Manada, Sesgo de Confirmacion, Anclaje de Precio, Aversion a la Perdida, Overconfidence, etc.)
Por que la gente ha tomado las decisiones emocionales que ha tomado con esta accion recientemente?
Ha habido euforia irracional, pesimismo exagerado o capitulacion de inversores en este periodo? Da ejemplos concretos con fechas.

**4. CATALIZADORES EMOCIONALES (ULTIMOS 3 MESES)**
Que noticias, earnings, anuncios, movimientos de precio o eventos de los ultimos 90 dias han generado las reacciones emocionales mas fuertes? Lista cada catalizador con fecha aproximada y la reaccion que provoco.
Que genera mas MIEDO y que genera mas OPTIMISMO en la comunidad sobre {ticker_clean} ahora mismo, basado en lo discutido recientemente?
Hay algun rumor, tweet viral o narrativa emergente de las ultimas semanas que este moviendo el sentimiento?

**5. SENTIMIENTO vs FUNDAMENTALES (ULTIMOS 3 MESES)**
El sentimiento de los ultimos 90 dias esta alineado con los fundamentales reales de {company_name} o hay una desconexion peligrosa?
Esta el mercado siendo mas emocional que racional con esta accion en este periodo reciente?
Hay una oportunidad de contrarian investing basada en el sentimiento actual?
Que dice el Fear & Greed Index reciente sobre el mercado en general y como aplica a {ticker_clean}?

**6. TESIS DE INVERSION ESPECULATIVA — BASADA EN EL SENTIR DE LA GENTE**
Basandote en TODO lo que la comunidad ha estado diciendo en los ultimos 3 meses, construye tu propia tesis de inversion especulativa:
- Si el consenso emocional/narrativo de la comunidad tiene fundamento real, hacia donde podria ir el precio de {ticker_clean} en los proximos 3-6 meses?
- Cual es el escenario MAS PROBABLE segun el sentir colectivo reciente (alcista, bajista o lateral) y por que?
- Que tendria que pasar para que la narrativa dominante de la comunidad se confirme o se rompa?
- Que esta viendo la gente que el mercado/Wall Street aun no ha valorado completamente (si aplica)?
- Tu propia especulacion: dado el sentimiento reciente, esto es una señal de alerta (euforia excesiva = riesgo) o una oportunidad temprana (acumulacion silenciosa, cambio de narrativa)?

**7. VEREDICTO PSICOLOGICO DEL EXPERTO**
Como el maximo experto en psicologia del inversor: cual es tu evaluacion final del estado mental colectivo sobre {ticker_clean}, basado en los ultimos 3 meses?
El momento psicologico del mercado favorece o perjudica una inversion ahora mismo?
Que le recomendarias al inversor inteligente basandote en el sentimiento reciente, la psicologia de masas y tu tesis especulativa de la seccion anterior?
Cual es la trampa psicologica mas grande que ves en como la gente percibe a {ticker_clean} en este momento?
"""

    grok_ok    = False
    grok_text  = ""
    grok_error = ""

    try:
        resp = requests.post(
            "https://api.x.ai/v1/chat/completions",
            headers={
                "Content-Type":  "application/json",
                "Authorization": f"Bearer {XAI_API_KEY}"
            },
            json={
                "model":       "grok-3",
                "messages": [
                    {"role": "system", "content": system_msg},
                    {"role": "user",   "content": user_msg}
                ],
                "max_tokens":  4000,
                "temperature": 0.4
            },
            timeout=120
        )
        if resp.status_code == 200:
            grok_text = resp.json()["choices"][0]["message"]["content"].strip()
            grok_ok   = True
        else:
            grok_error = f"Grok API error {resp.status_code}: {resp.text[:600]}"
            print(f"[GROK DEBUG] {resp.status_code}: {resp.text[:600]}")
    except requests.exceptions.Timeout:
        grok_error = "Timeout: Grok tardo mas de 90 segundos. Intenta de nuevo."
    except Exception as ex:
        grok_error = f"Error de conexion con Grok: {str(ex)}"


    score, label, color = 50, "NEUTRAL", "amber"
    if grok_ok and grok_text:
        txt = grok_text.lower()
        bulls = ["bullish","alcist","optimism","euforia","compra","subir","sube","rally","positiv",
                 "esperanza","confianza","buy","strong","crecimiento","potencial"]
        bears = ["bearish","bajist","pesimism","panico","miedo","venta","bajar","baja","negativ",
                 "ansiedad","temor","sell","riesgo","caida","preocupacion"]
        bc = sum(txt.count(w) for w in bulls)
        nc = sum(txt.count(w) for w in bears)
        tot = bc + nc
        if tot > 0:
            p = (bc / tot) * 100
            score = round(p)
            if   p >= 70: label, color = "MUY BULLISH",  "emerald"
            elif p >= 55: label, color = "BULLISH",       "emerald"
            elif p >= 45: label, color = "NEUTRAL",       "amber"
            elif p >= 30: label, color = "BEARISH",       "red"
            else:         label, color = "MUY BEARISH",   "red"

    return {
        "ticker":        ticker_clean,
        "company_name":  company_name,
        "overall_score": score,
        "overall_label": label,
        "overall_color": color,
        "grok_ok":       grok_ok,
        "grok_text":     grok_text,
        "grok_error":    grok_error,
        "analysis_date": today_str,
        "window_start":  three_months_ago,
        "window_end":    today_str,
        "date_range_label": date_range_label,
        "reddit_posts_count": len(reddit_posts),
        "reddit_posts": reddit_posts,
        "generated_at":  datetime.now().strftime('%m/%d/%Y, %I:%M:%S %p')
    }

# ─────────────────────────────────────────────────────────────────────────────
# PORTFOLIO ENGINE — Plaid + AI Analysis
# Plaid keys: export PLAID_CLIENT_ID="..." PLAID_SECRET="..." PLAID_ENV="sandbox|development|production"
# ─────────────────────────────────────────────────────────────────────────────
PLAID_CLIENT_ID = os.environ.get("PLAID_CLIENT_ID", "")
PLAID_SECRET    = os.environ.get("PLAID_SECRET",    "")
PLAID_ENV       = os.environ.get("PLAID_ENV",       "production")  # sandbox | production

def plaid_base_url():
    env = PLAID_ENV.lower()
    if env == "production":  return "https://production.plaid.com"
    return "https://sandbox.plaid.com"

def plaid_headers():
    return {"Content-Type": "application/json", "PLAID-CLIENT-ID": PLAID_CLIENT_ID, "PLAID-SECRET": PLAID_SECRET}


@app.post("/api/plaid/link-token")
def create_link_token(body: dict = None):
    """Step 1 — Create a Plaid Link token to open the Plaid UI in the browser."""
    try:
        payload = {
            "client_name":    "Vertex Fund OS",
            "country_codes":  ["US"],
            "language":       "en",
            "products":       ["investments"],
            "user":           {"client_user_id": "vertex_user_001"}
        }
        resp = requests.post(f"{plaid_base_url()}/link/token/create", headers=plaid_headers(), json=payload, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Plaid link token error: {str(e)}")


@app.post("/api/plaid/exchange-token")
def exchange_public_token(body: dict):
    """Step 2 — Exchange public_token from Plaid Link for an access_token."""
    public_token = body.get("public_token", "")
    try:
        resp = requests.post(f"{plaid_base_url()}/item/public_token/exchange",
            headers=plaid_headers(), json={"public_token": public_token}, timeout=15)
        resp.raise_for_status()
        return resp.json()   # contains access_token — store this in the frontend
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Plaid exchange error: {str(e)}")



@app.get("/api/accounts")
def get_accounts(access_token: str):
    """Return list of investment accounts so the user can choose which one to analyze."""
    try:
        resp = requests.post(f"{plaid_base_url()}/investments/holdings/get",
            headers=plaid_headers(), json={"access_token": access_token}, timeout=20)
        resp.raise_for_status()
        data     = resp.json()
        accounts = data.get("accounts", [])
        holdings = data.get("holdings", [])

        hcount = {}
        for h in holdings:
            aid = h.get("account_id", "")
            hcount[aid] = hcount.get(aid, 0) + 1

        result = []
        for a in accounts:
            aid = a.get("account_id", "")
            bal = a.get("balances", {})
            result.append({
                "account_id":    aid,
                "name":          a.get("name", "Account"),
                "official_name": a.get("official_name", ""),
                "type":          a.get("type", ""),
                "subtype":       a.get("subtype", ""),
                "current":       bal.get("current") or 0,
                "iso_currency":  bal.get("iso_currency_code", "USD"),
                "holdings_count": hcount.get(aid, 0),
            })
        return {"accounts": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Accounts error: {str(e)}")

@app.get("/api/portfolio")
def get_portfolio(access_token: str, account_id: str = ""):
    """
    Fetch full investment holdings + transactions from Plaid and enrich with:
    - Live prices via yfinance
    - P&L per position (day, week, month, 3M, 6M, 1Y, all-time)
    - Options contracts details
    - AI analysis via Gemini on the full portfolio
    """
    try:
        # ── Fetch holdings ─────────────────────────────────────────────────
        holdings_resp = requests.post(f"{plaid_base_url()}/investments/holdings/get",
            headers=plaid_headers(), json={"access_token": access_token}, timeout=20)
        holdings_resp.raise_for_status()
        plaid_data = holdings_resp.json()

        holdings_all = plaid_data.get("holdings", [])
        securities   = plaid_data.get("securities", [])
        accounts     = plaid_data.get("accounts", [])

        # ── Filter by selected account if provided ─────────────────────
        holdings = [h for h in holdings_all if h.get("account_id") == account_id] if account_id else holdings_all

        # ── Map securities by security_id ──────────────────────────────────
        sec_map = {s["security_id"]: s for s in securities}

        # ── Filter accounts by account_id if provided ──────────────────────
        acct_filter = [a for a in accounts if a.get("account_id") == account_id] if account_id else accounts

        # ── Extract cash from ALL account types (Robinhood reports cash
        #    inside brokerage accounts, not as a separate depository) ──────
        cash_balance = 0.0
        cash_positions = []   # Cash shown as a line in the portfolio
        for a in acct_filter:
            bal     = a.get("balances", {})
            subtype = (a.get("subtype") or "").lower()
            atype   = (a.get("type") or "").lower()
            acct_name = a.get("name", "Cash")

            # Robinhood cash: Plaid returns it as available balance in the
            # brokerage account, separate from the securities value.
            # We also catch classic cash/depository accounts.
            current_bal   = safe_float(bal.get("current"))
            available_bal = safe_float(bal.get("available"))

            is_cash_acct = subtype in ("cash", "checking", "savings", "money market")
            is_brokerage  = subtype in ("brokerage",) or atype in ("investment",)

            if is_cash_acct:
                cash_val = current_bal
            elif is_brokerage:
                # For brokerage accounts: available - securities value gives
                # the uninvested cash (what Robinhood calls "Buying Power").
                # Plaid also sometimes puts a separate cash holding with
                # ticker "CUR:USD" — we handle that in the holdings loop.
                # Here we just track the account-level available balance.
                cash_val = available_bal if available_bal > 0 else 0.0
            else:
                cash_val = 0.0

            if cash_val > 0:
                cash_balance += cash_val
                cash_positions.append({
                    "account_id":   a.get("account_id",""),
                    "account_name": acct_name,
                    "cash_value":   safe_round(cash_val),
                    "subtype":      subtype,
                })

        total_cost_basis = safe_float(sum(safe_float(h.get("cost_basis", 0)) for h in holdings))

        # ── nan-safe helpers ───────────────────────────────────────────────
        def safe_float(v, default=0.0):
            try:
                f = float(v)
                if math.isnan(f) or math.isinf(f):
                    return default
                return f
            except (TypeError, ValueError):
                return default

        def safe_round(v, decimals=2, default=0.0):
            return round(safe_float(v, default), decimals)

        stocks  = []
        options = []
        other   = []

        for h in holdings:
            sec      = sec_map.get(h.get("security_id"), {})
            ticker   = (sec.get("ticker_symbol") or "").strip()
            name     = sec.get("name", ticker)
            sec_type = (sec.get("type") or "").lower()

            # Skip cash currency holdings (CUR:USD etc.) — already in cash_balance
            if sec_type == "cash" or ticker.startswith("CUR:") or name.upper() in ("USD", "CASH"):
                cash_from_holding = safe_float(h.get("institution_value", 0))
                if cash_from_holding > 0:
                    # Add to cash if not already counted via account balance
                    cash_balance  # already set above; don't double-count
                continue

            qty        = safe_float(h.get("quantity", 0))
            cost       = safe_float(h.get("cost_basis", 0))       # Real cost — from Robinhood via Plaid
            inst_val   = safe_float(h.get("institution_value", 0)) # Real current value — from Robinhood via Plaid
            inst_price = safe_float(h.get("institution_price", 0)) # Real price — from Robinhood via Plaid

            is_option = (
                sec_type in ("derivative",)
                or (ticker and len(ticker) > 10)
                or sec.get("option_contract") is not None
            )

            # ── Live price from yfinance (for enrichment only) ─────────────
            live_price    = inst_price   # default: Robinhood's own price
            day_chg_pct   = 0.0
            week_chg_pct  = 0.0
            month_chg_pct = 0.0
            m3_chg_pct    = 0.0
            m6_chg_pct    = 0.0
            y1_chg_pct    = 0.0

            if ticker and not is_option and 1 <= len(ticker) <= 6:
                try:
                    stk = yf.Ticker(ticker)
                    fi  = stk.fast_info

                    raw_px = (
                        getattr(fi, 'last_price', None)
                        or getattr(fi, 'regular_market_price', None)
                    )
                    yf_price = safe_float(raw_px)
                    if yf_price > 0:
                        live_price = yf_price

                    prev_close = safe_float(
                        getattr(fi, 'previous_close', None)
                        or getattr(fi, 'regular_market_previous_close', None)
                    )
                    if prev_close > 0 and live_price > 0:
                        day_chg_pct = safe_round(((live_price - prev_close) / prev_close) * 100)
                    else:
                        try:
                            h2d = stk.history(period="2d")
                            if not h2d.empty and len(h2d) >= 2:
                                c1 = safe_float(h2d['Close'].iloc[-1])
                                c0 = safe_float(h2d['Close'].iloc[-2])
                                day_chg_pct = safe_round(((c1 - c0) / c0) * 100) if c0 > 0 else 0.0
                        except Exception:
                            pass

                    hist_1y = stk.history(period="1y")
                    if not hist_1y.empty and len(hist_1y) >= 2:
                        closes = [safe_float(x) for x in hist_1y['Close'].values if safe_float(x) > 0]
                        if len(closes) >= 2:
                            c_now  = live_price if live_price > 0 else closes[-1]
                            c_5d   = closes[-6]   if len(closes) >= 6   else closes[0]
                            c_30d  = closes[-22]  if len(closes) >= 22  else closes[0]
                            c_90d  = closes[-63]  if len(closes) >= 63  else closes[0]
                            c_180d = closes[-126] if len(closes) >= 126 else closes[0]
                            c_1y   = closes[0]
                            def pct(a, b):
                                a, b = safe_float(a), safe_float(b)
                                return safe_round(((a - b) / b) * 100) if b > 0 else 0.0
                            week_chg_pct  = pct(c_now, c_5d)
                            month_chg_pct = pct(c_now, c_30d)
                            m3_chg_pct    = pct(c_now, c_90d)
                            m6_chg_pct    = pct(c_now, c_180d)
                            y1_chg_pct    = pct(c_now, c_1y)
                except Exception:
                    pass

            # ── P&L: use Plaid's institution_value vs cost_basis ──────────
            # This matches EXACTLY what Robinhood shows — their own numbers.
            # live_price * qty used only as fallback if institution_value = 0.
            current_val = inst_val if inst_val > 0 else safe_round(live_price * qty)
            total_pnl   = safe_round(current_val - cost)
            pnl_pct     = safe_round((total_pnl / cost) * 100) if cost > 0 else 0.0

            h_acct_id   = h.get("account_id", "")
            h_acct_name = next((a.get("name","") for a in accounts if a.get("account_id") == h_acct_id), "")

            position = {
                "ticker":        ticker,
                "name":          name,
                "type":          sec_type,
                "account_id":    h_acct_id,
                "account_name":  h_acct_name,
                "qty":           safe_round(qty, 4),
                "cost_basis":    safe_round(cost),       # Real: what you paid
                "inst_price":    safe_round(inst_price, 4),  # Real: Robinhood price
                "live_price":    safe_round(live_price, 4),  # Enriched: yfinance
                "current_val":   safe_round(current_val),    # Real: Robinhood value
                "total_pnl":     total_pnl,                  # Real: matches Robinhood
                "pnl_pct":       pnl_pct,                    # Real: matches Robinhood
                "alltime_pnl_pct": pnl_pct,
                "day_chg_pct":   safe_round(day_chg_pct),
                "week_chg_pct":  safe_round(week_chg_pct),
                "month_chg_pct": safe_round(month_chg_pct),
                "m3_chg_pct":    safe_round(m3_chg_pct),
                "m6_chg_pct":    safe_round(m6_chg_pct),
                "y1_chg_pct":    safe_round(y1_chg_pct),
            }

            if is_option:
                options.append(position)
            elif sec_type in ("equity", "etf", "mutual fund", "fixed income", ""):
                stocks.append(position)
            else:
                other.append(position)

        # ── Portfolio-level totals — cash is part of the portfolio ────────
        securities_value = safe_float(sum(safe_float(p["current_val"]) for p in stocks + options + other))
        live_total       = safe_round(securities_value + safe_float(cash_balance))  # Total including cash
        total_pnl_dollar = safe_round(live_total - total_cost_basis - safe_float(cash_balance))
        # P&L only on invested positions (not cash) — matches Robinhood's display
        invested_cost    = safe_float(total_cost_basis)
        total_pnl_pct    = safe_round((total_pnl_dollar / invested_cost) * 100) if invested_cost > 0 else 0.0

        # Weighted portfolio period changes — nan-safe, securities only
        def weighted_chg(field):
            total_val = sum(safe_float(p["current_val"]) for p in stocks if safe_float(p["current_val"]) > 0)
            if not total_val:
                return 0.0
            weighted_sum = sum(
                safe_float(p.get(field, 0)) * safe_float(p["current_val"])
                for p in stocks
            )
            return safe_round(safe_float(weighted_sum / total_val))

        portfolio_periods = {
            "1d":  weighted_chg("day_chg_pct"),
            "1w":  weighted_chg("week_chg_pct"),
            "1mo": weighted_chg("month_chg_pct"),
            "3mo": weighted_chg("m3_chg_pct"),
            "6mo": weighted_chg("m6_chg_pct"),
            "1y":  weighted_chg("y1_chg_pct"),
            "all": safe_round(total_pnl_pct),
        }

        # ── Persist equity-book snapshot so the per-stock agent is portfolio-aware ──
        try:
            _snap = [{"ticker": p["ticker"], "name": p.get("name", p["ticker"]),
                      "value": safe_float(p["current_val"])}
                     for p in stocks
                     if p.get("ticker") and 1 <= len(p["ticker"]) <= 6 and safe_float(p["current_val"]) > 0]
            save_portfolio_snapshot(_snap, account_id or "ALL")
        except Exception as _se:
            print(f"[DB] snapshot skip: {_se}")

        # ── AI Portfolio Analysis via Gemini ──────────────────────────────
        acct_label = next((a.get("name","") for a in accounts if a.get("account_id") == account_id), "Todas las cuentas") if account_id else "Todas las cuentas"

        # ─────────────────────────────────────────────────────────────────────
        # STEP A: Enrich each held stock with REAL yfinance fundamentals + news
        # ─────────────────────────────────────────────────────────────────────
        def fetch_yf_fundamentals(ticker: str) -> dict:
            """Pull live price, analyst targets, fundamentals, and news from yfinance."""
            out = {}
            if not ticker or len(ticker) > 6:
                return out
            try:
                stk  = yf.Ticker(ticker)
                info = stk.info or {}
                fi   = stk.fast_info

                live_px = (
                    getattr(fi, 'last_price', None)
                    or getattr(fi, 'regular_market_price', None)
                    or info.get('regularMarketPrice')
                    or info.get('currentPrice')
                )
                prev_cl = (
                    getattr(fi, 'previous_close', None)
                    or info.get('previousClose')
                )
                day_chg = round(((float(live_px) - float(prev_cl)) / float(prev_cl)) * 100, 2) if live_px and prev_cl and float(prev_cl) > 0 else None

                out = {
                    "live_price":        round(float(live_px), 2) if live_px else None,
                    "prev_close":        round(float(prev_cl), 2) if prev_cl else None,
                    "day_chg_pct":       day_chg,
                    "market_cap":        info.get("marketCap"),
                    "forward_pe":        info.get("forwardPE"),
                    "trailing_pe":       info.get("trailingPE"),
                    "revenue_growth":    info.get("revenueGrowth"),       # YoY %
                    "earnings_growth":   info.get("earningsGrowth"),
                    "gross_margins":     info.get("grossMargins"),
                    "operating_margins": info.get("operatingMargins"),
                    "total_revenue":     info.get("totalRevenue"),
                    "free_cashflow":     info.get("freeCashflow"),
                    "total_debt":        info.get("totalDebt"),
                    "cash":              info.get("totalCash"),
                    "52w_high":          info.get("fiftyTwoWeekHigh"),
                    "52w_low":           info.get("fiftyTwoWeekLow"),
                    "analyst_target_mean":   info.get("targetMeanPrice"),
                    "analyst_target_high":   info.get("targetHighPrice"),
                    "analyst_target_low":    info.get("targetLowPrice"),
                    "analyst_recommendation": info.get("recommendationKey"),
                    "num_analyst_opinions":  info.get("numberOfAnalystOpinions"),
                    "short_name":        info.get("shortName") or info.get("longName"),
                    "sector":            info.get("sector"),
                    "industry":          info.get("industry"),
                    "business_summary":  (info.get("longBusinessSummary") or "")[:400],
                }
                # Live news headlines
                try:
                    raw_news = stk.news or []
                    out["news"] = [n.get("title","") for n in raw_news[:5] if n.get("title")]
                except Exception:
                    out["news"] = []
            except Exception:
                pass
            return out

        def fmt_billions(v):
            if v is None: return "N/A"
            v = float(v)
            if abs(v) >= 1e12: return f"${v/1e12:.2f}T"
            if abs(v) >= 1e9:  return f"${v/1e9:.2f}B"
            if abs(v) >= 1e6:  return f"${v/1e6:.2f}M"
            return f"${v:.0f}"

        def fmt_pct(v):
            if v is None: return "N/A"
            return f"{round(float(v)*100, 1)}%"

        # Enrich held stocks
        held_details = []
        for p in stocks[:25]:
            tk = p.get("ticker","")
            if not tk or len(tk) > 6:
                continue
            f = fetch_yf_fundamentals(tk)
            # Use yfinance live price if available, otherwise Plaid price
            live_px_final = f.get("live_price") or p["live_price"]
            cur_val_final = round(float(live_px_final) * float(p["qty"]), 2) if live_px_final else p["current_val"]
            pnl_dollar    = round(cur_val_final - p["cost_basis"], 2) if p["cost_basis"] else 0
            pnl_pct_final = round((pnl_dollar / p["cost_basis"]) * 100, 2) if p["cost_basis"] else 0

            # Analyst upside from live price
            at_mean = f.get("analyst_target_mean")
            upside  = round(((float(at_mean) - float(live_px_final)) / float(live_px_final)) * 100, 1) if at_mean and live_px_final else None

            news_str = " | ".join(f.get("news", [])[:4]) or "No hay noticias recientes"

            block = f"""  [{tk}] {f.get('short_name') or p['name']}
    Sector: {f.get('sector','N/A')} | Industria: {f.get('industry','N/A')}
    Precio LIVE (Yahoo Finance): ${live_px_final} | Cambio hoy: {f.get('day_chg_pct','N/A')}%
    52W High: ${f.get('52w_high','N/A')} | 52W Low: ${f.get('52w_low','N/A')}
    Qty en portafolio: {p['qty']} acciones | Costo promedio pagado: ${p['cost_basis']} (dato real Robinhood/Plaid)
    Valor actual: ${cur_val_final} | P&L real: ${pnl_dollar} ({pnl_pct_final}%)
    Rendimientos: 1D={p['day_chg_pct']}% | 1M={p['month_chg_pct']}% | 3M={p['m3_chg_pct']}% | 6M={p['m6_chg_pct']}% | 1Y={p['y1_chg_pct']}%
    Market Cap: {fmt_billions(f.get('market_cap'))} | Revenue: {fmt_billions(f.get('total_revenue'))} | Revenue Growth YoY: {fmt_pct(f.get('revenue_growth'))}
    Gross Margin: {fmt_pct(f.get('gross_margins'))} | Op Margin: {fmt_pct(f.get('operating_margins'))} | Free Cash Flow: {fmt_billions(f.get('free_cashflow'))}
    Forward P/E: {f.get('forward_pe','N/A')} | Trailing P/E: {f.get('trailing_pe','N/A')}
    Deuda: {fmt_billions(f.get('total_debt'))} | Cash: {fmt_billions(f.get('cash'))}
    Target Wall Street (media): ${at_mean or 'N/A'} | Upside desde precio actual: {upside or 'N/A'}% | Rating: {f.get('analyst_recommendation','N/A')} ({f.get('num_analyst_opinions','N/A')} analistas)
    Target High: ${f.get('analyst_target_high','N/A')} | Target Low: ${f.get('analyst_target_low','N/A')}
    Negocio (resumen): {f.get('business_summary','N/A')}
    Noticias recientes: {news_str}"""
            held_details.append(block)

        # ─────────────────────────────────────────────────────────────────────
        # STEP B: Fetch REAL live data for the growth watchlist from Yahoo Finance
        # These are curated tickers the AI will analyze and recommend from.
        # Data is 100% real — no simulation, no fake numbers.
        # ─────────────────────────────────────────────────────────────────────
        GROWTH_WATCHLIST = [
            # AI Infrastructure & Software
            "PLTR","APP","CRWD","NET","SNOW","DDOG","MDB","GTLB","HUBS",
            # Quantum Computing
            "IONQ","RGTI","QUBT","QMCO",
            # Semiconductors (non-Mag7)
            "AMD","AVGO","AMAT","ARM","MRVL","SMCI","TSM","ASML",
            # Robotics / Autonomous
            "RDDT","PATH","ACHR",
            # Space Economy
            "RKLB","LUNR","ASTS",
            # Biotech / Longevity
            "RXRX","SANA","CRSP","BEAM","NTLA",
            # Nuclear / Clean Energy
            "OKLO","NNE","CEG","BWXT",
            # Fintech Disruptivo
            "HOOD","AFRM","SOFI","NU",
            # Stable 30% (Mag7 + leaders)
            "NVDA","MSFT","GOOGL","META","AAPL","AMZN","TSLA","NFLX",
        ]

        watchlist_data = {}
        for tk in GROWTH_WATCHLIST:
            watchlist_data[tk] = fetch_yf_fundamentals(tk)

        def build_watchlist_line(tk: str) -> str:
            f = watchlist_data.get(tk, {})
            live_px = f.get("live_price")
            at_mean = f.get("analyst_target_mean")
            upside  = round(((float(at_mean) - float(live_px)) / float(live_px)) * 100, 1) if at_mean and live_px and float(live_px) > 0 else None
            news_str = " | ".join(f.get("news", [])[:3]) or "—"
            return (
                f"  [{tk}] {f.get('short_name','') or tk} | Sector: {f.get('sector','N/A')}\n"
                f"    Precio LIVE: ${live_px or 'N/A'} | 1D: {f.get('day_chg_pct','N/A')}% | 52W High: ${f.get('52w_high','N/A')} | 52W Low: ${f.get('52w_low','N/A')}\n"
                f"    Market Cap: {fmt_billions(f.get('market_cap'))} | Revenue: {fmt_billions(f.get('total_revenue'))} | Rev Growth YoY: {fmt_pct(f.get('revenue_growth'))}\n"
                f"    Gross Margin: {fmt_pct(f.get('gross_margins'))} | Free Cash Flow: {fmt_billions(f.get('free_cashflow'))}\n"
                f"    Wall St Target (media): ${at_mean or 'N/A'} | Upside: {upside or 'N/A'}% | Rating: {f.get('analyst_recommendation','N/A')} ({f.get('num_analyst_opinions','N/A')} analistas)\n"
                f"    Negocio: {f.get('business_summary','')[:250]}\n"
                f"    Noticias: {news_str}"
            )

        growth_tickers  = [t for t in GROWTH_WATCHLIST if t not in ("NVDA","MSFT","GOOGL","META","AAPL","AMZN","TSLA","NFLX")]
        stable_tickers  = ["NVDA","MSFT","GOOGL","META","AAPL","AMZN","TSLA","NFLX"]
        growth_data_str = "\n".join(build_watchlist_line(t) for t in growth_tickers)
        stable_data_str = "\n".join(build_watchlist_line(t) for t in stable_tickers)

        options_summary = "\n".join([
            f"  {p['ticker']}: {p['qty']} contratos | Cost: ${p['cost_basis']} | Valor actual: ${p['current_val']} | P&L: ${p['total_pnl']} ({p['pnl_pct']}%)"
            for p in options[:15]
        ])

        ai_prompt = f"""Eres el CIO mas agresivo y visionario de Vertex Holding Group. Tienes acceso a busqueda web en tiempo real para enriquecer el analisis con noticias actuales y perspectivas de mercado.

TODOS LOS DATOS A CONTINUACION SON REALES — extraidos directamente de Yahoo Finance (yfinance) y Robinhood via Plaid Production. NINGUN dato es simulado o inventado.

══════════════════════════════════════════════
CUENTA: {acct_label}
PORTAFOLIO TOTAL: ${round(live_total,2)} | COSTO TOTAL REAL: ${round(total_cost_basis,2)} | P&L TOTAL: ${total_pnl_dollar} ({total_pnl_pct}%) | CASH: ${round(cash_balance,2)}
RENDIMIENTO PONDERADO: 1D={portfolio_periods['1d']}% | 1W={portfolio_periods['1w']}% | 1M={portfolio_periods['1mo']}% | 3M={portfolio_periods['3mo']}% | 6M={portfolio_periods['6mo']}% | 1Y={portfolio_periods['1y']}%
══════════════════════════════════════════════

POSICIONES ACTUALES CON DATOS REALES DE YAHOO FINANCE + ROBINHOOD/PLAID:
{chr(10).join(held_details) if held_details else 'Sin posiciones en acciones'}

OPCIONES ACTUALES:
{options_summary if options_summary else 'Ninguna'}

══════════════════════════════════════════════
UNIVERSE DE OPORTUNIDADES — DATOS REALES YAHOO FINANCE (usa estos numeros exactos en tus recomendaciones):

--- GROWTH EXPONENCIAL (para el 70% del portafolio) ---
{growth_data_str}

--- ESTABLES MAGNIFICAS 7 + LIDERES (para el 30% del portafolio) ---
{stable_data_str}
══════════════════════════════════════════════

FILOSOFIA DE INVERSION:
- 30% ESTABLES: Mag 7 (NVDA, AAPL, MSFT, AMZN, GOOGL, META, TSLA) + Netflix. Crecimiento solido, moat enorme, liderazgo consolidado. Siguen creciendo extraordinariamente.
- 70% GROWTH EXPONENCIAL: Companias fuera de Mag 7 que pueden multiplicarse 5x-10x+ en 5 años. Sectores: AI pura, computacion cuantica, robotica, espacio, biotech/longevity, energia nuclear, semiconductores next-gen, fintech. Prioriza las que tienen: revenue creciendo +40% YoY, targets de analistas muy superiores al precio actual, y gran TAM.

INSTRUCCION CRITICA: Para cada recomendacion DEBES usar los precios reales de Yahoo Finance que se te dieron arriba. Para cada accion que recomiendas: explica el negocio en terminos simples (que hace, como gana dinero), la tesis de inversion completa basada en los datos reales, y por que es una oportunidad HOY basado en sus numeros reales.

Responde con EXACTAMENTE estas 11 secciones. Usa los datos reales. Se especifico y agresivo:

**1. DIAGNOSTICO DEL PORTAFOLIO**
Analiza el portafolio actual con los datos reales. Que posiciones ganan, cuales pierden, cuanto en $ real. Evalua la asignacion actual vs la estrategia 30/70. Identifica capital mal asignado.

**2. ANALISIS PROFUNDO POR POSICION — TESIS Y ESTADO**
Para CADA accion en el portafolio (usando los datos reales de arriba):
- Precio live de Yahoo Finance, P&L real en $ desde Robinhood, rendimiento por periodo
- Target de Wall Street vs precio actual: upside/downside %
- TESIS: que hace esta empresa (en palabras simples), por que sube o baja, noticias recientes que la afectan, si tiene fundamentales para seguir creciendo
- Decision: FUERTE COMPRA / AGREGAR / MANTENER / REDUCIR / VENDER
- Explicacion del porque basada en los numeros reales

**3. ANALISIS DE OPCIONES — CONVICTION Y PROBABILIDAD**
Para cada contrato de opciones: probabilidad de exito estimada (%), conviccion 0-100, analisis del strike vs precio actual vs target, accion recomendada con razonamiento.

**4. RECOMENDACIONES GROWTH 70% — CON DATOS REALES DE YAHOO FINANCE**
De las acciones del universe de growth de arriba, selecciona las MEJORES oportunidades. Para cada una que recomiendes:
- Nombre de la empresa y que hace (explicacion simple: "Esta empresa hace X, gana dinero cuando Y")
- Precio LIVE de Yahoo Finance (usa el dato real de arriba)
- Target Wall Street y upside % (del dato real de arriba)
- Revenue growth YoY y margenes (del dato real de arriba)
- Noticias recientes que la catalizan
- TESIS completa: por que esta empresa va a multiplicarse, que productos o servicios esta lanzando, cuando empezara a generar ingresos masivos, que dice Wall Street, y el multiplicador esperado en 3-5 años
- Cuanto % del portafolio asignar

**5. RECOMENDACIONES ESTABLES 30% — CON DATOS REALES DE YAHOO FINANCE**
De las Mag 7 y lideres, cuales agregar o mantener. Para cada una:
- Precio LIVE de Yahoo Finance (dato real)
- Target Wall Street y upside % (dato real)
- Por que agregar ahora: catalizadores proximos (earnings, lanzamientos AI, expansion)
- Explicacion simple del negocio
- Crecimiento anual esperado y % del portafolio a asignar

**6. QUE VENDER O REDUCIR — LIBERACION DE CAPITAL**
Posiciones del portafolio actual a vender o reducir (con razonamiento basado en datos reales). Cuanto capital libera y donde reasignarlo especificamente.

**7. LISTA MAESTRA DE OPORTUNIDADES — TODAS LAS QUE CUMPLEN LA TESIS**
Ranking completo de todas las acciones del universe de arriba que cumplen la tesis de growth exponencial. Para cada una: precio real, target real, upside %, multiplicador potencial 3-5 años, tesis resumida en 2-3 oraciones, y por que es oportunidad HOY.

**8. EL FUTURO — DONDE ESTA EL DINERO EN 5-10 AÑOS**
Basado en los datos de revenue growth, margins, y noticias de las empresas del universe: que sectores estan posicionados para explotar. AI Agents/AGI, computacion cuantica, fusion nuclear, humanoides, longevity, next-gen chips, space economy. Cuales son los pure plays con mejores fundamentales actuales (usa los datos reales).

**9. TESIS POR SECTOR DEL FUTURO**
Para cada sector clave: tesis completa de inversion, punto de inflexion en 2-3 años, las 2-3 mejores companias del universe con sus datos reales (precio, target, upside, revenue growth), y multiplicador esperado. Explica el negocio de cada compania en terminos simples.

**10. PLAN DE ACCION — ESTRATEGIA 30/70 CON CAPITAL DISPONIBLE**
Con el cash disponible de ${round(cash_balance,2)} y el capital que se libere vendiendo: plan paso a paso, que comprar primero, montos especificos en $, en que orden, como llegar a la asignacion 30/70 ideal. Usa precios reales para calcular cuantas acciones comprar.

**11. PROYECCION A 1, 3 Y 5 AÑOS**
Partiendo de ${round(live_total,2)}: valor proyectado implementando este plan en escenario base/bull/bear. CAGR esperado. Hitos anuales especificos con numeros en $.
"""

        ai_response = ""
        try:
            gemini_resp = client_gemini.models.generate_content(
                model='gemini-2.5-flash',
                contents=ai_prompt,
                config=types.GenerateContentConfig(
                    temperature=0.35,
                    max_output_tokens=10000,
                    tools=[types.Tool(google_search=types.GoogleSearch())]
                )
            )
            ai_response = gemini_resp.text
        except Exception:
            try:
                gemini_resp = client_gemini.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=ai_prompt,
                    config=types.GenerateContentConfig(temperature=0.35, max_output_tokens=10000)
                )
                ai_response = gemini_resp.text
            except Exception as ex2:
                ai_response = f"[AI no disponible: {str(ex2)}]"

        # ── Final safety pass: replace any nan/inf in entire response ──────
        def sanitize_json(obj):
            if isinstance(obj, float):
                return 0.0 if (math.isnan(obj) or math.isinf(obj)) else obj
            if isinstance(obj, dict):
                return {k: sanitize_json(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [sanitize_json(i) for i in obj]
            return obj

        return sanitize_json({
            "connected":          True,
            # ── Portfolio totals ───────────────────────────────────────────
            "total_value":        safe_round(live_total),          # Securities + Cash
            "securities_value":   safe_round(securities_value),    # Invested positions only
            "total_cost_basis":   safe_round(total_cost_basis),    # What you paid (Robinhood/Plaid)
            "total_pnl_dollar":   safe_round(total_pnl_dollar),    # $ gain/loss on invested positions
            "total_pnl_pct":      safe_round(total_pnl_pct),       # % gain/loss — matches Robinhood
            # ── Cash ──────────────────────────────────────────────────────
            "cash_balance":       safe_round(cash_balance),        # Uninvested cash (buying power)
            "cash_positions":     cash_positions,                  # Breakdown by account
            # ── Positions ─────────────────────────────────────────────────
            "portfolio_periods":  portfolio_periods,
            "stocks":             stocks,
            "options":            options,
            "other":              other,
            "accounts":           accounts,
            "ai_analysis":        ai_response,
            "generated_at":       datetime.now().strftime('%m/%d/%Y, %I:%M:%S %p')
        })

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Portfolio error: {str(e)}")


# ─────────────────────────────────────────────────────────────────────────────
# PORTFOLIO RISK ENGINE — factor exposure, VaR, beta, concentration (Aladdin-style)
# ─────────────────────────────────────────────────────────────────────────────
def compute_portfolio_risk(positions, lookback="1y"):
    """Quant risk engine over real holdings: annualized volatility, Value-at-Risk,
    market beta, factor exposures, concentration metrics and per-position risk
    contribution. `positions` = list of {ticker, name, value}."""
    import pandas as pd

    eq = [p for p in positions if p.get("ticker") and float(p.get("value") or 0) > 0]
    if len(eq) < 1:
        return {"ok": False, "error": "No hay posiciones de acciones para analizar."}

    total_val = sum(float(p["value"]) for p in eq)
    weights_all = {p["ticker"]: float(p["value"]) / total_val for p in eq}
    name_map = {p["ticker"]: p.get("name", p["ticker"]) for p in eq}
    tickers = list(weights_all.keys())

    factor_etfs = {
        "SPY": "Mercado (S&P 500)",
        "QQQ": "Tech (Nasdaq 100)",
        "SMH": "Semiconductores",
        "IWM": "Small Caps (Russell 2000)",
    }
    all_syms = list(set(tickers + list(factor_etfs.keys())))

    closes = {}
    for s in all_syms:
        try:
            h = yf.Ticker(s).history(period=lookback)
            if not h.empty and "Close" in h:
                ser = h["Close"]
                ser.index = [d.strftime("%Y-%m-%d") for d in ser.index]
                closes[s] = ser
        except Exception:
            pass
    if not closes:
        return {"ok": False, "error": "No se pudo obtener historial de precios."}

    px = pd.DataFrame(closes).dropna(how="all")
    rets = px.pct_change().dropna(how="all")

    avail = [t for t in tickers if t in rets.columns]
    if not avail:
        return {"ok": False, "error": "Sin datos de retorno suficientes para las posiciones."}

    # Renormalize weights to the positions we actually have data for
    w = np.array([weights_all[t] for t in avail], dtype=float)
    w = w / w.sum()
    R = rets[avail].dropna()
    if R.shape[0] < 20:
        return {"ok": False, "error": "Historial insuficiente (se necesitan ~20+ días)."}

    cov_daily = R.cov().values                      # daily covariance matrix
    port_daily = R.values @ w                        # portfolio daily returns
    port_var_daily = float(w @ cov_daily @ w)
    port_vol_daily = math.sqrt(max(port_var_daily, 1e-12))
    ann = math.sqrt(252)
    port_vol_annual = port_vol_daily * ann

    # ── Value-at-Risk (1-day) ────────────────────────────────────────────────
    mean_d = float(np.mean(port_daily))
    std_d  = float(np.std(port_daily, ddof=1)) if len(port_daily) > 1 else port_vol_daily
    hist_var95 = -float(np.percentile(port_daily, 5))
    hist_var99 = -float(np.percentile(port_daily, 1))
    par_var95  = -(mean_d - 1.645 * std_d)
    par_var99  = -(mean_d - 2.326 * std_d)

    def pos(x): return max(x, 0.0)
    var = {
        "hist_95_pct": round(pos(hist_var95) * 100, 2), "hist_95_usd": round(pos(hist_var95) * total_val, 2),
        "hist_99_pct": round(pos(hist_var99) * 100, 2), "hist_99_usd": round(pos(hist_var99) * total_val, 2),
        "param_95_pct": round(pos(par_var95) * 100, 2), "param_95_usd": round(pos(par_var95) * total_val, 2),
        "param_99_pct": round(pos(par_var99) * 100, 2), "param_99_usd": round(pos(par_var99) * total_val, 2),
    }

    # ── Market beta + factor exposures (univariate betas) ────────────────────
    def beta_to(sym):
        if sym not in rets.columns:
            return None
        joined = pd.concat([pd.Series(port_daily, index=R.index, name="p"), rets[sym].rename("f")], axis=1).dropna()
        if joined.shape[0] < 20:
            return None
        f = joined["f"].values; p = joined["p"].values
        vf = float(np.var(f, ddof=1))
        if vf <= 0:
            return None
        b = float(np.cov(p, f, ddof=1)[0, 1] / vf)
        corr = float(np.corrcoef(p, f)[0, 1])
        return {"beta": round(b, 2), "corr": round(corr, 2)}

    market = beta_to("SPY")
    factor_exposure = []
    for sym, label in factor_etfs.items():
        bt = beta_to(sym)
        if bt:
            factor_exposure.append({"symbol": sym, "label": label, "beta": bt["beta"], "corr": bt["corr"]})

    # ── Concentration ────────────────────────────────────────────────────────
    w_full = np.array([weights_all[t] for t in tickers], dtype=float)
    hhi = float(np.sum(w_full ** 2))
    eff_n = round(1.0 / hhi, 1) if hhi > 0 else None
    sorted_w = sorted(weights_all.items(), key=lambda kv: kv[1], reverse=True)
    top1 = sorted_w[0] if sorted_w else (None, 0)
    top3 = sum(v for _, v in sorted_w[:3])
    top5 = sum(v for _, v in sorted_w[:5])

    # Average pairwise correlation (hidden-concentration signal)
    corr_m = R.corr()
    n = len(avail)
    if n > 1:
        iu = np.triu_indices(n, k=1)
        avg_pair_corr = round(float(np.nanmean(corr_m.values[iu])), 2)
    else:
        avg_pair_corr = None

    # Diversification ratio = weighted avg of individual vols / portfolio vol
    indiv_vol = R.std(ddof=1).values * ann
    wavg_vol  = float(np.sum(w * indiv_vol))
    diversification_ratio = round(wavg_vol / port_vol_annual, 2) if port_vol_annual > 0 else None

    # ── Per-position risk contribution ───────────────────────────────────────
    marginal = cov_daily @ w                          # marginal contribution to variance
    ctr = w * marginal                                # contribution to variance per asset
    ctr_pct = (ctr / port_var_daily * 100) if port_var_daily > 0 else np.zeros_like(ctr)
    positions_risk = []
    for i, t in enumerate(avail):
        positions_risk.append({
            "ticker": t,
            "name": name_map.get(t, t),
            "weight_pct": round(w[i] * 100, 2),
            "annual_vol_pct": round(float(indiv_vol[i]) * 100, 1),
            "beta_spy": (beta_to(t) or {}).get("beta"),
            "risk_contribution_pct": round(float(ctr_pct[i]), 1),
        })
    positions_risk.sort(key=lambda x: x["risk_contribution_pct"], reverse=True)

    # ── Correlation matrix (for the heatmap tab later) ───────────────────────
    corr_matrix = {
        "tickers": avail,
        "matrix": [[round(float(corr_m.values[i][j]), 2) for j in range(n)] for i in range(n)],
    }

    return {
        "ok": True,
        "total_value": round(total_val, 2),
        "positions_analyzed": len(avail),
        "positions_skipped": [t for t in tickers if t not in avail],
        "lookback": lookback,
        "annual_vol_pct": round(port_vol_annual * 100, 1),
        "market_beta": market["beta"] if market else None,
        "market_corr": market["corr"] if market else None,
        "var": var,
        "factor_exposure": factor_exposure,
        "concentration": {
            "top_holding_ticker": top1[0],
            "top_holding_pct": round(top1[1] * 100, 1) if top1[0] else None,
            "top3_pct": round(top3 * 100, 1),
            "top5_pct": round(top5 * 100, 1),
            "hhi": round(hhi, 3),
            "effective_holdings": eff_n,
            "num_positions": len(tickers),
            "avg_pairwise_corr": avg_pair_corr,
            "diversification_ratio": diversification_ratio,
        },
        "positions_risk": positions_risk,
        "correlation_matrix": corr_matrix,
        "generated_at": datetime.now().strftime('%m/%d/%Y, %I:%M:%S %p'),
    }


def compute_portfolio_stress(positions, lookback_days=504):
    """Stress & scenario engine over real holdings: historical crisis replay on
    the CURRENT book, Monte Carlo (historical bootstrap), Expected Shortfall
    (CVaR), hypothetical factor shocks, and risk-adjusted performance metrics.
    `positions` = list of {ticker, name, value}."""
    import pandas as pd

    eq = [p for p in positions if p.get("ticker") and float(p.get("value") or 0) > 0]
    if len(eq) < 1:
        return {"ok": False, "error": "No hay posiciones de acciones para analizar."}

    total_val = sum(float(p["value"]) for p in eq)
    weights_all = {p["ticker"]: float(p["value"]) / total_val for p in eq}
    name_map = {p["ticker"]: p.get("name", p["ticker"]) for p in eq}
    tickers = list(weights_all.keys())

    factor_syms = ["SPY", "QQQ", "SMH", "IWM", "TLT"]
    all_syms = list(set(tickers + factor_syms))

    # ── One max-history fetch per symbol; slice locally for crisis windows ────
    full = {}
    for s in all_syms:
        try:
            h = yf.Ticker(s).history(period="max")
            if not h.empty and "Close" in h and len(h) > 1:
                ser = h["Close"].copy()
                ser.index = pd.to_datetime([d.strftime("%Y-%m-%d") for d in ser.index])
                full[s] = ser
        except Exception:
            pass
    if not full:
        return {"ok": False, "error": "No se pudo obtener historial de precios."}

    pxf = pd.DataFrame(full).sort_index()
    px = pxf.tail(lookback_days + 1)              # recent window for metrics/MC/betas
    rets = px.pct_change().dropna(how="all")
    avail = [t for t in tickers if t in rets.columns and rets[t].notna().sum() >= 40]
    if not avail:
        return {"ok": False, "error": "Sin datos de retorno suficientes para las posiciones."}

    w = np.array([weights_all[t] for t in avail], dtype=float)
    w = w / w.sum()
    R = rets[avail].dropna()
    if R.shape[0] < 60:
        return {"ok": False, "error": "Historial insuficiente para stress (~60+ dias)."}

    port_daily = R.values @ w
    ann = math.sqrt(252)

    # ── Risk-adjusted performance metrics ────────────────────────────────────
    RF = 0.043
    mean_d = float(np.mean(port_daily)); std_d = float(np.std(port_daily, ddof=1))
    ann_return = (1.0 + mean_d) ** 252 - 1.0
    ann_vol = std_d * ann
    dn = port_daily[port_daily < 0]
    dd_dev = float(np.std(dn, ddof=1)) * ann if len(dn) > 1 else 0.0
    curve = np.cumprod(1.0 + port_daily); peak = np.maximum.accumulate(curve)
    ddraw = curve / peak - 1.0; max_dd = float(ddraw.min())
    metrics = {
        "ann_return_pct": round(ann_return * 100, 1),
        "ann_vol_pct": round(ann_vol * 100, 1),
        "sharpe": round((ann_return - RF) / ann_vol, 2) if ann_vol > 0 else None,
        "sortino": round((ann_return - RF) / dd_dev, 2) if dd_dev > 0 else None,
        "calmar": round(ann_return / abs(max_dd), 2) if max_dd < 0 else None,
        "max_drawdown_pct": round(max_dd * 100, 1),
        "time_underwater_pct": round(float(np.mean(ddraw < -1e-9)) * 100, 1),
        "rf_assumed_pct": round(RF * 100, 1),
        "window_days": int(R.shape[0]),
    }

    # ── Expected Shortfall (CVaR), 1-day ─────────────────────────────────────
    def es(level):
        q = np.percentile(port_daily, level); tail = port_daily[port_daily <= q]
        return -float(np.mean(tail)) if len(tail) else 0.0
    cvar = {
        "es_95_pct": round(max(es(5), 0.0) * 100, 2), "es_95_usd": round(max(es(5), 0.0) * total_val, 2),
        "es_99_pct": round(max(es(1), 0.0) * 100, 2), "es_99_usd": round(max(es(1), 0.0) * total_val, 2),
    }

    # ── Monte Carlo (historical bootstrap of portfolio daily returns) ────────
    rng = np.random.default_rng(42)
    def mc(horizon, n=5000):
        idx = rng.integers(0, len(port_daily), size=(n, horizon))
        sampled = port_daily[idx]
        paths = np.cumprod(1.0 + sampled, axis=1)
        end = paths[:, -1] - 1.0
        peaks = np.maximum.accumulate(paths, axis=1)
        ddp = (paths / peaks - 1.0).min(axis=1)
        P = lambda a, p: round(float(np.percentile(a, p)) * 100, 1)
        return {
            "horizon_days": horizon,
            "p5": P(end, 5), "p25": P(end, 25), "p50": P(end, 50), "p75": P(end, 75), "p95": P(end, 95),
            "prob_loss": round(float(np.mean(end < 0)) * 100, 1),
            "prob_loss_10": round(float(np.mean(end < -0.10)) * 100, 1),
            "prob_loss_20": round(float(np.mean(end < -0.20)) * 100, 1),
            "exp_max_drawdown_pct": round(float(np.mean(ddp)) * 100, 1),
            "worst_p5_usd": round(float(np.percentile(end, 5)) * total_val, 2),
            "best_p95_usd": round(float(np.percentile(end, 95)) * total_val, 2),
        }
    monte_carlo = {"h21": mc(21), "h252": mc(252)}

    # ── Betas (recent) for hypothetical shocks + crisis proxy ────────────────
    def beta_pair(a_ret, b_ret):
        j = pd.concat([a_ret.rename("a"), b_ret.rename("b")], axis=1).dropna()
        if j.shape[0] < 40: return None
        vb = float(np.var(j["b"].values, ddof=1))
        if vb <= 0: return None
        return float(np.cov(j["a"].values, j["b"].values, ddof=1)[0, 1] / vb)

    port_ret_series = pd.Series(port_daily, index=R.index)
    betas = {s: (beta_pair(port_ret_series, rets[s]) if s in rets.columns else None) for s in factor_syms}

    def shock(sym, mv):
        b = betas.get(sym)
        if b is None: return None
        return {"port_pct": round(b * mv * 100, 1), "port_usd": round(b * mv * total_val, 2), "beta": round(b, 2)}
    raw_hyp = [
        ("S&P 500 \u221210%", "Correccion amplia de mercado", "SPY", -0.10),
        ("Nasdaq 100 \u221215%", "Selloff tecnologico", "QQQ", -0.15),
        ("Semiconductores \u221220%", "Shock al sector semis", "SMH", -0.20),
        ("Small Caps \u221210%", "Risk-off en small caps", "IWM", -0.10),
        ("Tasas +100 pb", "Subida de tasas (proxy TLT \u22129%)", "TLT", -0.09),
    ]
    hypotheticals = []
    for label, detail, sym, mv in raw_hyp:
        sh = shock(sym, mv)
        if sh: hypotheticals.append({"label": label, "detail": detail, **sh})

    # ── Historical crisis replay on CURRENT weights ──────────────────────────
    crises = [
        ("gfc_2008",   "Crisis Financiera 2008", "2008-09-01", "2009-03-09"),
        ("covid_2020", "Crash COVID 2020",       "2020-02-19", "2020-03-23"),
        ("bear_2022",  "Bear Market 2022",       "2022-01-03", "2022-10-12"),
        ("q4_2018",    "Selloff Q4 2018",        "2018-09-20", "2018-12-24"),
        ("dotcom",     "Dot-com 2000\u20132002", "2000-03-10", "2002-10-09"),
    ]
    spy_beta = {}
    if "SPY" in rets.columns:
        for t in avail:
            spy_beta[t] = beta_pair(rets[t], rets["SPY"])

    def window_ret(sym, start, end):
        if sym not in pxf.columns: return None
        seg = pxf[sym].loc[start:end].dropna()
        if len(seg) < 2: return None
        return float(seg.iloc[-1] / seg.iloc[0] - 1.0)

    scenarios = []
    for key, label, start, end in crises:
        spy_r = window_ret("SPY", start, end)
        port_ret = 0.0; covered = 0.0; proxied = 0.0
        for i, t in enumerate(avail):
            wr = window_ret(t, start, end)
            if wr is not None:
                port_ret += w[i] * wr; covered += w[i]
            elif spy_r is not None and spy_beta.get(t) is not None:
                port_ret += w[i] * spy_beta[t] * spy_r; proxied += w[i]
        scenarios.append({
            "key": key, "label": label, "window": f"{start} \u2192 {end}",
            "port_pct": round(port_ret * 100, 1), "port_usd": round(port_ret * total_val, 2),
            "spy_pct": round(spy_r * 100, 1) if spy_r is not None else None,
            "coverage_pct": round(covered * 100, 0), "proxied_pct": round(proxied * 100, 0),
        })

    return {
        "ok": True,
        "total_value": round(total_val, 2),
        "positions_analyzed": len(avail),
        "positions_skipped": [t for t in tickers if t not in avail],
        "metrics": metrics,
        "cvar": cvar,
        "monte_carlo": monte_carlo,
        "hypotheticals": hypotheticals,
        "scenarios": scenarios,
        "generated_at": datetime.now().strftime('%m/%d/%Y, %I:%M:%S %p'),
    }


def _resolve_positions(access_token, account_id="", with_cost=False):
    """Resolve normalized equity positions from Plaid (when an access_token is given)
    or from the stored SnapTrade snapshot otherwise — so the entire portfolio suite
    runs on SnapTrade alone, no Plaid required. Returns (positions, source)."""
    if access_token:
        resp = requests.post(f"{plaid_base_url()}/investments/holdings/get",
                             headers=plaid_headers(), json={"access_token": access_token}, timeout=20)
        resp.raise_for_status()
        return _extract_equity_positions(resp.json(), account_id, with_cost=with_cost), "plaid"
    return get_portfolio_snapshot(), "snaptrade"


@app.get("/api/portfolio-risk")
def get_portfolio_risk(access_token: str = "", account_id: str = ""):
    """Run the quant risk engine on the user's real holdings (Plaid or SnapTrade snapshot)."""
    try:
        positions, _src = _resolve_positions(access_token, account_id)
        if not positions:
            return {"ok": False, "error": "No se encontraron posiciones. Carga tus holdings con SnapTrade (o conecta Plaid)."}
        return compute_portfolio_risk(positions)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Risk engine error: {str(e)}")


@app.get("/api/portfolio-stress")
def get_portfolio_stress(access_token: str = "", account_id: str = ""):
    """Run the stress engine on the user's real holdings (Plaid or SnapTrade snapshot)."""
    try:
        positions, _src = _resolve_positions(access_token, account_id)
        if not positions:
            return {"ok": False, "error": "No se encontraron posiciones. Carga tus holdings con SnapTrade (o conecta Plaid)."}
        return compute_portfolio_stress(positions)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Stress engine error: {str(e)}")


@app.get("/api/portfolio-whatif")
def get_portfolio_whatif(access_token: str = "", ticker: str = "", action: str = "add",
                         amount: float = 0.0, account_id: str = ""):
    """Recompute portfolio risk BEFORE vs AFTER a hypothetical add/trim of `ticker`."""
    try:
        ticker = ticker.upper().strip()

        def sf(v, d=0.0):
            try:
                f = float(v); return d if (math.isnan(f) or math.isinf(f)) else f
            except (TypeError, ValueError):
                return d

        positions, _src = _resolve_positions(access_token, account_id)
        if not positions:
            return {"ok": False, "error": "No se encontraron posiciones. Carga tus holdings con SnapTrade (o conecta Plaid)."}

        after = [dict(p) for p in positions]
        found = next((p for p in after if p["ticker"] == ticker), None)
        amt = abs(sf(amount))
        if action == "add":
            if found:
                found["value"] += amt
            else:
                after.append({"ticker": ticker, "name": ticker, "value": amt})
        elif action == "trim":
            if not found:
                return {"ok": False, "error": f"{ticker} no esta en tu portafolio para recortar."}
            found["value"] = max(0.0, found["value"] - amt)
            after = [p for p in after if p["value"] > 0]
        else:
            return {"ok": False, "error": "action debe ser 'add' o 'trim'."}

        before_r = compute_portfolio_risk(positions)
        after_r = compute_portfolio_risk(after)
        if not before_r.get("ok") or not after_r.get("ok"):
            return {"ok": False, "error": before_r.get("error") or after_r.get("error") or "No se pudo calcular el riesgo."}

        def slim(r):
            c = r.get("concentration", {}); v = r.get("var", {})
            return {
                "total_value": r.get("total_value"),
                "annual_vol_pct": r.get("annual_vol_pct"),
                "market_beta": r.get("market_beta"),
                "var_95_usd": v.get("hist_95_usd"), "var_95_pct": v.get("hist_95_pct"),
                "effective_holdings": c.get("effective_holdings"),
                "top_holding_ticker": c.get("top_holding_ticker"),
                "top_holding_pct": c.get("top_holding_pct"),
                "avg_pairwise_corr": c.get("avg_pairwise_corr"),
                "num_positions": c.get("num_positions"),
            }

        b = slim(before_r); a = slim(after_r)

        def d(x, y):
            return round(y - x, 2) if (isinstance(x, (int, float)) and isinstance(y, (int, float))) else None

        delta = {
            "total_value": d(b["total_value"], a["total_value"]),
            "annual_vol_pct": d(b["annual_vol_pct"], a["annual_vol_pct"]),
            "market_beta": d(b["market_beta"], a["market_beta"]),
            "var_95_usd": d(b["var_95_usd"], a["var_95_usd"]),
            "effective_holdings": d(b["effective_holdings"], a["effective_holdings"]),
            "avg_pairwise_corr": d(b["avg_pairwise_corr"], a["avg_pairwise_corr"]),
        }

        new_w = None
        af = next((p for p in after if p["ticker"] == ticker), None)
        if af:
            tot = sum(p["value"] for p in after)
            new_w = round(af["value"] / tot * 100, 1) if tot else None

        return {"ok": True, "ticker": ticker, "action": action, "amount": amt,
                "before": b, "after": a, "delta": delta, "new_weight_pct": new_w,
                "generated_at": datetime.now().strftime('%m/%d/%Y, %I:%M:%S %p')}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"What-if error: {str(e)}")



# ── PERFORMANCE ATTRIBUTION + GUARDRAILS ──────────────────────────────────────
STABLE_LEADERS = {"AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "META", "NVDA", "BRK.B",
                  "BRK-B", "AVGO", "TSLA", "JPM", "V", "MA", "COST", "WMT", "JNJ",
                  "PG", "HD", "UNH", "XOM", "LLY",
                  # Dividend stalwarts / defensivas estables (bucket 30%)
                  "KO", "PEP", "MCD", "ABBV", "MRK", "CVX", "PFE", "T", "VZ", "CSCO",
                  "IBM", "MMM", "CAT", "HON", "TXN", "CL", "KMB", "PM", "MO", "SO",
                  "DUK", "O", "ABT", "MDT", "CB", "ADP", "LOW", "TGT", "GD", "LMT"}


def _fetch_sectors(tickers):
    """Best-effort ticker -> sector map via yfinance."""
    out = {}
    for t in tickers:
        try:
            info = yf.Ticker(t).info or {}
            out[t] = info.get("sector") or "Otros"
        except Exception:
            out[t] = "Otros"
    return out


def compute_portfolio_attribution(positions, lookback_days=252):
    """Decompose trailing return into per-position and per-sector contributions."""
    import pandas as pd
    eq = [p for p in positions if p.get("ticker") and float(p.get("value") or 0) > 0]
    if not eq:
        return {"ok": False, "error": "No hay posiciones de acciones para analizar."}
    total = sum(float(p["value"]) for p in eq)
    weights = {p["ticker"]: float(p["value"]) / total for p in eq}
    names = {p["ticker"]: p.get("name", p["ticker"]) for p in eq}
    tickers = list(weights.keys())

    closes = {}
    for t in tickers:
        try:
            h = yf.Ticker(t).history(period="2y")
            if not h.empty and "Close" in h:
                closes[t] = h["Close"]
        except Exception:
            pass
    if not closes:
        return {"ok": False, "error": "No se pudo obtener historial de precios."}

    px = pd.DataFrame(closes).dropna(how="all").tail(lookback_days + 1)
    period_ret = {}
    for t in tickers:
        if t in px.columns:
            s = px[t].dropna()
            if len(s) >= 2 and float(s.iloc[0]) > 0:
                period_ret[t] = float(s.iloc[-1] / s.iloc[0] - 1.0)
    avail = [t for t in tickers if t in period_ret]
    if not avail:
        return {"ok": False, "error": "Sin retornos suficientes para atribucion."}

    wsum = sum(weights[t] for t in avail)
    sectors = _fetch_sectors(avail)

    pos_rows = []
    total_contrib = 0.0
    for t in avail:
        w = weights[t] / wsum
        contrib = w * period_ret[t]
        total_contrib += contrib
        pos_rows.append({
            "ticker": t, "name": names.get(t, t), "sector": sectors.get(t, "Otros"),
            "weight_pct": round(w * 100, 1),
            "period_return_pct": round(period_ret[t] * 100, 1),
            "contribution_pct": round(contrib * 100, 2),
        })
    pos_rows.sort(key=lambda r: r["contribution_pct"], reverse=True)

    sec_map = {}
    for r in pos_rows:
        s = r["sector"]
        sec_map.setdefault(s, {"sector": s, "weight_pct": 0.0, "contribution_pct": 0.0, "positions": 0})
        sec_map[s]["weight_pct"] += r["weight_pct"]
        sec_map[s]["contribution_pct"] += r["contribution_pct"]
        sec_map[s]["positions"] += 1
    sectors_list = sorted(sec_map.values(), key=lambda x: x["contribution_pct"], reverse=True)
    for s in sectors_list:
        s["weight_pct"] = round(s["weight_pct"], 1)
        s["contribution_pct"] = round(s["contribution_pct"], 2)

    negatives = [r for r in pos_rows if r["contribution_pct"] < 0]
    return {
        "ok": True,
        "lookback_days": int(min(lookback_days, px.shape[0] - 1)),
        "total_return_pct": round(total_contrib * 100, 1),
        "positions": pos_rows,
        "sectors": sectors_list,
        "top_contributors": pos_rows[:3],
        "top_detractors": negatives[-3:][::-1],
        "positions_analyzed": len(avail),
        "generated_at": datetime.now().strftime('%m/%d/%Y, %I:%M:%S %p'),
    }


def compute_portfolio_guardrails(positions_pnl, risk):
    """Check the book against portfolio rules. positions_pnl: list of
    {ticker,name,value,cost_basis,sector}. risk: compute_portfolio_risk() result."""
    eq = [p for p in positions_pnl if p.get("ticker") and float(p.get("value") or 0) > 0]
    if not eq:
        return {"ok": False, "error": "No hay posiciones para evaluar."}
    total = sum(float(p["value"]) for p in eq)
    rules = []

    def add(name, status, value, threshold, detail):
        rules.append({"rule": name, "status": status, "value": value,
                      "threshold": threshold, "detail": detail})

    # 1 — single-position concentration
    top = max(eq, key=lambda p: p["value"])
    top_w = top["value"] / total * 100
    st = "breach" if top_w > 25 else "warn" if top_w > 20 else "ok"
    add("Concentracion por posicion", st, f"{top['ticker']} {round(top_w, 1)}%", "<=25%",
        f"Tu posicion mas grande es {top['ticker']} con {round(top_w, 1)}% del book.")

    # 2 — top-3 concentration
    top3 = sum(sorted([p["value"] for p in eq], reverse=True)[:3]) / total * 100
    st = "breach" if top3 > 60 else "warn" if top3 > 50 else "ok"
    add("Concentracion Top-3", st, f"{round(top3, 1)}%", "<=60%",
        f"Tus 3 posiciones mas grandes suman {round(top3, 1)}% del book.")

    # 3 — 30/70 mandate
    stable_val = sum(p["value"] for p in eq if p["ticker"].upper() in STABLE_LEADERS)
    stable_pct = stable_val / total * 100
    growth_pct = 100 - stable_pct
    st = "ok" if abs(stable_pct - 30) <= 10 else "warn"
    add("Mandato 30/70 (estable/crecimiento)", st, f"{round(stable_pct)}/{round(growth_pct)}", "30/70 +-10",
        f"Lideres estables {round(stable_pct)}% vs crecimiento {round(growth_pct)}%. Objetivo 30/70.")

    # 4 — sector concentration
    sec_w = {}
    for p in eq:
        s = p.get("sector", "Otros")
        sec_w[s] = sec_w.get(s, 0) + p["value"]
    if sec_w:
        top_sec, top_sec_val = max(sec_w.items(), key=lambda x: x[1])
        top_sec_pct = top_sec_val / total * 100
        st = "breach" if top_sec_pct > 50 else "warn" if top_sec_pct > 40 else "ok"
        add("Concentracion sectorial", st, f"{top_sec} {round(top_sec_pct, 1)}%", "<=40%",
            f"Tu sector mas pesado es {top_sec} con {round(top_sec_pct, 1)}% del book.")

    # 5 — stop-loss review (equity down hard)
    losers = []
    for p in eq:
        cb = float(p.get("cost_basis") or 0)
        if cb > 0:
            pnl = (p["value"] - cb) / cb * 100
            if pnl <= -25:
                losers.append(f"{p['ticker']} ({round(pnl)}%)")
    st = "warn" if losers else "ok"
    add("Revision stop-loss (equity -25%)", st, f"{len(losers)} posiciones", "revisar si A-grade",
        ("Posiciones con perdida >25%: " + ", ".join(losers) + ". Tu regla: equity A-grade sin stop fijo, revisa la tesis.")
        if losers else "Ninguna posicion de equity con perdida mayor a 25%.")

    # 6 — book correlation (from risk engine)
    apc = (risk or {}).get("concentration", {}).get("avg_pairwise_corr")
    if apc is not None:
        st = "breach" if apc >= 0.8 else "warn" if apc >= 0.7 else "ok"
        add("Correlacion del book", st, f"{apc}", "<0.70",
            f"Correlacion promedio entre posiciones {apc}. Alta = diversificacion oculta baja.")

    summary = {
        "breaches": sum(1 for r in rules if r["status"] == "breach"),
        "warnings": sum(1 for r in rules if r["status"] == "warn"),
        "passes": sum(1 for r in rules if r["status"] == "ok"),
    }
    return {"ok": True, "rules": rules, "summary": summary, "total_value": round(total, 2),
            "generated_at": datetime.now().strftime('%m/%d/%Y, %I:%M:%S %p')}


def _extract_equity_positions(data, account_id, with_cost=False):
    """Shared Plaid holdings -> equity positions extractor."""
    holdings_all = data.get("holdings", []); securities = data.get("securities", [])
    sec_map = {s["security_id"]: s for s in securities}
    holdings = [h for h in holdings_all if h.get("account_id") == account_id] if account_id else holdings_all

    def sf(v, d=0.0):
        try:
            f = float(v); return d if (math.isnan(f) or math.isinf(f)) else f
        except (TypeError, ValueError):
            return d

    positions = []
    for h in holdings:
        sec = sec_map.get(h.get("security_id"), {})
        tk = (sec.get("ticker_symbol") or "").strip()
        nm = sec.get("name", tk); stp = (sec.get("type") or "").lower()
        if stp == "cash" or tk.startswith("CUR:") or nm.upper() in ("USD", "CASH"):
            continue
        is_opt = (stp in ("derivative",) or (tk and len(tk) > 10) or sec.get("option_contract") is not None)
        if is_opt or not tk or len(tk) > 6:
            continue
        if stp not in ("equity", "etf", "mutual fund", ""):
            continue
        val = sf(h.get("institution_value", 0))
        if val <= 0:
            val = sf(h.get("institution_price", 0)) * sf(h.get("quantity", 0))
        if val > 0:
            pos = {"ticker": tk, "name": nm, "value": val}
            if with_cost:
                pos["cost_basis"] = sf(h.get("cost_basis", 0))
            positions.append(pos)
    return positions


@app.get("/api/portfolio-attribution")
def get_portfolio_attribution(access_token: str = "", account_id: str = "", lookback_days: int = 252):
    """Return + sector attribution over a trailing window on the real book."""
    try:
        positions, _src = _resolve_positions(access_token, account_id)
        if not positions:
            return {"ok": False, "error": "No se encontraron posiciones. Carga tus holdings con SnapTrade (o conecta Plaid)."}
        return compute_portfolio_attribution(positions, lookback_days=max(20, min(lookback_days, 504)))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Attribution error: {str(e)}")


@app.get("/api/portfolio-guardrails")
def get_portfolio_guardrails(access_token: str = "", account_id: str = ""):
    """Check the real book against portfolio rules (concentration, 70/30, stops, corr)."""
    try:
        positions, _src = _resolve_positions(access_token, account_id, with_cost=True)
        if not positions:
            return {"ok": False, "error": "No se encontraron posiciones. Carga tus holdings con SnapTrade (o conecta Plaid)."}
        sectors = _fetch_sectors([p["ticker"] for p in positions])
        for p in positions:
            p["sector"] = sectors.get(p["ticker"], "Otros")
        risk = compute_portfolio_risk([{"ticker": p["ticker"], "name": p["name"], "value": p["value"]} for p in positions])
        return compute_portfolio_guardrails(positions, risk if risk.get("ok") else None)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Guardrails error: {str(e)}")



# ── SNAPTRADE BROKERAGE CONNECTION (near real-time, via official SDK) ──────────
SNAPTRADE_CLIENT_ID = os.environ.get("SNAPTRADE_CLIENT_ID", "")        # <-- tu Client ID
SNAPTRADE_CONSUMER_KEY = os.environ.get("SNAPTRADE_CONSUMER_KEY", "")  # <-- tu Consumer Key
# Llaves PERSONALES de SnapTrade: el usuario viene provisto al registrarte (no se usa registerUser).
# Pega aquí el userId y userSecret que aparecen en tu dashboard de SnapTrade:
SNAPTRADE_USER_ID = os.environ.get("SNAPTRADE_USER_ID", "")            # <-- tu userId provisto
SNAPTRADE_USER_SECRET = os.environ.get("SNAPTRADE_USER_SECRET", "")    # <-- tu userSecret provisto
_snaptrade_client = None


def _get_snaptrade():
    global _snaptrade_client
    if not SNAPTRADE_CLIENT_ID or not SNAPTRADE_CONSUMER_KEY:
        return None
    if _snaptrade_client is None:
        try:
            from snaptrade_client import SnapTrade
            _snaptrade_client = SnapTrade(consumer_key=SNAPTRADE_CONSUMER_KEY, client_id=SNAPTRADE_CLIENT_ID)
        except Exception as e:
            print(f"[SnapTrade] init error: {e}")
            return None
    return _snaptrade_client


def _snaptrade_reason():
    """Precise diagnosis of why SnapTrade isn't ready, so the UI can tell the user
    exactly what to fix (missing keys vs SDK not installed)."""
    if not SNAPTRADE_CLIENT_ID or not SNAPTRADE_CONSUMER_KEY:
        return ("Faltan tus llaves de SnapTrade. Define SNAPTRADE_CLIENT_ID y "
                "SNAPTRADE_CONSUMER_KEY (pégalas en vertex_api.py ~línea 3960 o expórtalas "
                "como variables de entorno) y reinicia el backend.")
    try:
        from snaptrade_client import SnapTrade  # noqa: F401
    except Exception:
        return "SDK de SnapTrade no instalado. Ejecuta en tu terminal: pip install snaptrade-python-sdk  (y reinicia el backend)."
    return "SnapTrade no disponible: revisa que tus llaves sean correctas y que el backend se haya reiniciado."


def _st_body(r):
    """Normalize SDK response to plain python (body attr or the object itself)."""
    b = getattr(r, "body", None)
    return b if b is not None else r


def _snaptrade_extract_positions(raw):
    """Normalize SnapTrade positions to {ticker,name,value}. Symbol nesting varies,
    so we probe several shapes defensively."""
    out = []
    for p in (raw or []):
        sym = p.get("symbol") if isinstance(p, dict) else None
        ticker, name = "", ""
        node = sym
        # descend through possible nesting: symbol -> symbol -> symbol
        for _ in range(3):
            if isinstance(node, dict):
                if node.get("symbol") and isinstance(node.get("symbol"), str):
                    ticker = node.get("symbol")
                    name = node.get("description") or node.get("raw_symbol") or name
                    break
                if node.get("raw_symbol") and isinstance(node.get("raw_symbol"), str) and not ticker:
                    ticker = node.get("raw_symbol")
                    name = node.get("description") or name
                node = node.get("symbol")
            else:
                break
        if not ticker and isinstance(sym, dict):
            ticker = sym.get("raw_symbol") or sym.get("symbol") or ""
        units = p.get("units") or p.get("fractional_units") or 0
        price = p.get("price") or 0
        try:
            units_f = float(units)
            value = units_f * float(price)
        except (TypeError, ValueError):
            units_f, value = 0.0, 0.0
        # Cost basis: SnapTrade usually exposes average_purchase_price (per share)
        avg = p.get("average_purchase_price")
        try:
            avg = float(avg) if avg not in (None, "") else None
        except (TypeError, ValueError):
            avg = None
        cost_basis = round(units_f * avg, 2) if (avg is not None and units_f) else None
        if cost_basis is None:
            op = p.get("open_pnl")  # fallback: derive cost from open P&L if provided
            try:
                cost_basis = round(value - float(op), 2) if op not in (None, "") else None
            except (TypeError, ValueError):
                cost_basis = None
        unreal = round(value - cost_basis, 2) if cost_basis is not None else None
        unreal_pct = round((value - cost_basis) / cost_basis * 100, 2) if (cost_basis and cost_basis > 0) else None
        if isinstance(ticker, str) and ticker and value > 0:
            out.append({"ticker": ticker.upper().strip(), "name": name or ticker,
                        "value": round(value, 2), "units": round(units_f, 4),
                        "avg_price": round(avg, 2) if avg is not None else None,
                        "cost_basis": cost_basis, "unrealized_pnl": unreal,
                        "unrealized_pct": unreal_pct})
    return out


def _snaptrade_extract_options(raw):
    """Normalize SnapTrade option positions to the flat shape the Greeks engine needs.
    Symbol nesting varies by brokerage, so we probe defensively. Modular by design:
    any future source (e.g. Unusual Whales) just needs to emit this same dict shape."""
    out = []
    for p in (raw or []):
        if not isinstance(p, dict):
            continue
        # Locate the option_symbol node (may sit on p, p.symbol, or p.symbol.symbol)
        sym = p.get("symbol")
        osym = None
        for node in (p, sym, sym.get("symbol") if isinstance(sym, dict) else None):
            if isinstance(node, dict) and isinstance(node.get("option_symbol"), dict):
                osym = node["option_symbol"]; break
        if not isinstance(osym, dict):
            continue
        # Underlying ticker
        us = osym.get("underlying_symbol")
        underlying = ""
        if isinstance(us, dict):
            underlying = us.get("symbol") or us.get("raw_symbol") or ""
        elif isinstance(us, str):
            underlying = us
        if not underlying:  # last resort: parse OCC root from the option ticker
            tk = (osym.get("ticker") or "").strip()
            underlying = tk.split(" ")[0][:6] if tk else ""
        # Option type
        ot = (osym.get("option_type") or "").upper()
        otype = "call" if ot.startswith("C") else "put" if ot.startswith("P") else ""
        # Strike
        try:
            strike = float(osym.get("strike_price")) if osym.get("strike_price") not in (None, "") else None
        except (TypeError, ValueError):
            strike = None
        expiry = str(osym.get("expiration_date") or "")[:10]
        # Contracts (signed: negative = short), per-share price, avg purchase price
        try:
            contracts = float(p.get("units") or 0)
        except (TypeError, ValueError):
            contracts = 0.0
        try:
            price = float(p.get("price") or 0)
        except (TypeError, ValueError):
            price = 0.0
        avg = p.get("average_purchase_price")
        try:
            avg = float(avg) if avg not in (None, "") else None
        except (TypeError, ValueError):
            avg = None
        value = round(contracts * price * 100, 2)  # option price is per-share → ×100 per contract
        if underlying and otype and strike and expiry and contracts != 0:
            out.append({"underlying": underlying.upper().strip(), "option_type": otype,
                        "strike": strike, "expiry": expiry, "contracts": contracts,
                        "price": price, "avg_price": avg, "value": value})
    return out


@app.get("/api/snaptrade/status")
def snaptrade_status():
    if not SNAPTRADE_CLIENT_ID or not SNAPTRADE_CONSUMER_KEY:
        return {"ok": False, "configured": False, "error": "SNAPTRADE_CLIENT_ID / CONSUMER_KEY no configuradas."}
    cli = _get_snaptrade()
    if not cli:
        return {"ok": False, "configured": True, "error": "SDK no instalado. Ejecuta: pip install snaptrade-python-sdk"}
    try:
        st = cli.api_status.check(); return {"ok": True, "configured": True, "status": str(_st_body(st))}
    except Exception as e:
        return {"ok": False, "configured": True, "error": f"{e}"}


@app.get("/api/snaptrade/whoami")
def snaptrade_whoami():
    """Personal SnapTrade keys come with a pre-provisioned user. If the userId/userSecret
    are configured, return them so the frontend skips registerUser (not allowed for
    personal keys) and goes straight to the OAuth connection portal."""
    if SNAPTRADE_USER_ID and SNAPTRADE_USER_SECRET:
        return {"ok": True, "preprovisioned": True,
                "user_id": SNAPTRADE_USER_ID, "user_secret": SNAPTRADE_USER_SECRET}
    return {"ok": False, "preprovisioned": False}


@app.post("/api/snaptrade/register")
def snaptrade_register(user_id: str):
    cli = _get_snaptrade()
    if not cli:
        return {"ok": False, "error": _snaptrade_reason()}
    # Personal keys: user already provisioned — use configured creds, don't call registerUser.
    if SNAPTRADE_USER_ID and SNAPTRADE_USER_SECRET:
        return {"ok": True, "user_id": SNAPTRADE_USER_ID, "user_secret": SNAPTRADE_USER_SECRET,
                "preprovisioned": True}
    try:
        r = _st_body(cli.authentication.register_snap_trade_user(body={"userId": user_id}))
        return {"ok": True, "user_id": r.get("userId"), "user_secret": r.get("userSecret")}
    except Exception as e:
        msg = str(e)
        if "1012" in msg or "registerUser is not available" in msg or "personal" in msg.lower():
            return {"ok": False, "personal_keys": True, "error": (
                "Tus llaves de SnapTrade son PERSONALES: el usuario ya viene provisto al "
                "registrarte (no se permite registerUser). Copia tu userId y userSecret desde "
                "tu dashboard de SnapTrade y pégalos en vertex_api.py como SNAPTRADE_USER_ID y "
                "SNAPTRADE_USER_SECRET, luego reinicia el backend.")}
        return {"ok": False, "error": msg}


@app.get("/api/snaptrade/login-link")
def snaptrade_login_link(user_id: str, user_secret: str):
    cli = _get_snaptrade()
    if not cli:
        return {"ok": False, "error": _snaptrade_reason()}
    try:
        r = _st_body(cli.authentication.login_snap_trade_user(user_id=user_id, user_secret=user_secret))
        uri = r.get("redirectURI") if isinstance(r, dict) else None
        return {"ok": True, "redirect_uri": uri}
    except Exception as e:
        return {"ok": False, "error": f"{e}"}


@app.get("/api/snaptrade/accounts")
def snaptrade_accounts(user_id: str, user_secret: str):
    cli = _get_snaptrade()
    if not cli:
        return {"ok": False, "error": _snaptrade_reason()}
    try:
        r = _st_body(cli.account_information.list_user_accounts(user_id=user_id, user_secret=user_secret))
        accts = []
        for a in (r or []):
            bal = a.get("balance") if isinstance(a, dict) else None
            tot = None
            if isinstance(bal, dict):
                tot = (bal.get("total") or {}).get("amount") if isinstance(bal.get("total"), dict) else bal.get("total")
            accts.append({"id": a.get("id"), "name": a.get("name") or a.get("number"),
                          "institution": a.get("institution_name"), "balance": tot})
        return {"ok": True, "accounts": accts}
    except Exception as e:
        return {"ok": False, "error": f"{e}"}


@app.get("/api/snaptrade/holdings")
def snaptrade_holdings(user_id: str, user_secret: str, account_id: str):
    """Near-real-time positions for one connected account; saves the book snapshot
    so the agent + risk engine can use SnapTrade data interchangeably with Plaid."""
    cli = _get_snaptrade()
    if not cli:
        return {"ok": False, "error": _snaptrade_reason()}
    try:
        r = _st_body(cli.account_information.get_user_account_positions(
            user_id=user_id, user_secret=user_secret, account_id=account_id))
        positions = _snaptrade_extract_positions(r)
        if positions:
            try:
                save_portfolio_snapshot(positions, "SNAPTRADE")
            except Exception as _e:
                print(f"[SnapTrade] snapshot skip: {_e}")
        # Options live under a separate SnapTrade endpoint; fetch defensively so a
        # brokerage that doesn't expose option positions never breaks the equity load.
        options = []
        try:
            ro = _st_body(cli.options.list_option_holdings(
                user_id=user_id, user_secret=user_secret, account_id=account_id))
            if isinstance(ro, dict):
                ro = ro.get("option_positions") or ro.get("positions") or ro.get("data") or []
            options = _snaptrade_extract_options(ro)
            save_options_snapshot(options)
        except Exception as _eo:
            print(f"[SnapTrade] options skip: {_eo}")
        return {"ok": True, "account_id": account_id, "positions": positions,
                "options": options, "n_options": len(options),
                "source": "snaptrade", "generated_at": datetime.now().strftime('%m/%d/%Y, %I:%M:%S %p')}
    except Exception as e:
        return {"ok": False, "error": f"{e}"}


# ── OPTIONS GREEKS ENGINE (Black-Scholes from positions + yfinance IV) ─────────
# Modular: positions come from get_options_snapshot() (SnapTrade today). To swap in
# Unusual Whales later, feed its option list through the same compute function.
def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def _norm_pdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)

def _bs_greeks(S, K, T, sigma, r, opt_type):
    """Per-share Black-Scholes greeks. theta_day = per-calendar-day; vega_1pct = per 1% IV.
    Degrades to intrinsic delta (gamma/theta/vega = 0) at/after expiry or with no vol."""
    if S is None or S <= 0 or K <= 0:
        return {"delta": 0.0, "gamma": 0.0, "theta_day": 0.0, "vega_1pct": 0.0}
    if T <= 0 or sigma <= 0:
        if opt_type == "call":
            delta = 1.0 if S > K else 0.0
        else:
            delta = -1.0 if S < K else 0.0
        return {"delta": delta, "gamma": 0.0, "theta_day": 0.0, "vega_1pct": 0.0}
    sqT = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqT)
    d2 = d1 - sigma * sqT
    pdf = _norm_pdf(d1)
    if opt_type == "call":
        delta = _norm_cdf(d1)
        theta = (-(S * pdf * sigma) / (2 * sqT)) - r * K * math.exp(-r * T) * _norm_cdf(d2)
    else:
        delta = _norm_cdf(d1) - 1.0
        theta = (-(S * pdf * sigma) / (2 * sqT)) + r * K * math.exp(-r * T) * _norm_cdf(-d2)
    gamma = pdf / (S * sigma * sqT)
    vega = S * pdf * sqT / 100.0          # per 1% change in IV
    return {"delta": delta, "gamma": gamma, "theta_day": theta / 365.0, "vega_1pct": vega}


def _bs_price(S, K, T, sigma, r, opt_type):
    """Black-Scholes price per share. Degrades to intrinsic value at/after expiry or with zero vol.
    Used by the trade-plan structurer to estimate entry premium and the option's value at the target."""
    if S is None or S <= 0 or K is None or K <= 0:
        return 0.0
    if T <= 0 or sigma <= 0:
        intr = (S - K) if opt_type == "call" else (K - S)
        return max(0.0, intr)
    sqT = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqT)
    d2 = d1 - sigma * sqT
    if opt_type == "call":
        return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
    return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def compute_options_analytics(options, equity_positions=None):
    """Book-level Greeks from option positions. Pulls spot + IV from yfinance, prices
    each contract with Black-Scholes, aggregates net Δ/Γ/Θ/ν, expiry ladder, by-underlying
    breakdown, per-position detail, and alerts. Greeks are model estimates."""
    if not options:
        return {"ok": True, "n_options": 0,
                "note": "No se detectaron posiciones de opciones en la cuenta conectada. "
                        "(Algunos brokers no exponen opciones vía SnapTrade.)"}
    RF = 0.043
    now = datetime.now()
    spot_cache, iv_cache = {}, {}
    enriched = []
    net_delta_dollar = net_gamma = net_theta = net_vega = 0.0
    by_underlying, ladder = {}, {}

    for o in options:
        u, exp, otype = o["underlying"], o["expiry"], o["option_type"]
        strike = float(o["strike"]); contracts = float(o["contracts"])
        # Spot (cache per underlying)
        if u not in spot_cache:
            try:
                h = yf.Ticker(u).history(period="5d")
                spot_cache[u] = _safe_num(h["Close"].iloc[-1], 0.0) or None if not h.empty else None
            except Exception:
                spot_cache[u] = None
        S = spot_cache[u]
        # IV from the option chain (cache per underlying+expiry)
        ivkey = (u, exp)
        if ivkey not in iv_cache:
            chain_iv = {}
            try:
                ch = yf.Ticker(u).option_chain(exp)
                for df, typ in ((ch.calls, "call"), (ch.puts, "put")):
                    for _, row in df.iterrows():
                        chain_iv[(typ, round(_safe_num(row["strike"]), 2))] = _safe_num(row.get("impliedVolatility"))
            except Exception:
                pass
            iv_cache[ivkey] = chain_iv
        iv = iv_cache[ivkey].get((otype, round(strike, 2)))
        if not iv or iv <= 0:  # fallback: avg IV of same type on that expiry, else 0.5
            cands = [v for (t, _k), v in iv_cache[ivkey].items() if t == otype and v > 0]
            iv = (sum(cands) / len(cands)) if cands else 0.5
        # Time to expiry in years
        try:
            ed = datetime.strptime(exp, "%Y-%m-%d")
            T = max((ed - now).total_seconds() / (365.0 * 86400.0), 0.0)
            dte = max(int(round((ed - now).total_seconds() / 86400.0)), 0)
        except Exception:
            T, dte = 0.0, None
        g = _bs_greeks(S, strike, T, iv, RF, otype)
        mult = contracts * 100.0
        if S:
            d_dollar = g["delta"] * mult * S
            gm = g["gamma"] * mult
            th = g["theta_day"] * mult
            vg = g["vega_1pct"] * mult
            net_delta_dollar += d_dollar; net_gamma += gm; net_theta += th; net_vega += vg
            bu = by_underlying.setdefault(u, {"delta_dollar": 0.0, "theta_day": 0.0,
                                              "vega": 0.0, "value": 0.0, "contracts": 0.0})
            bu["delta_dollar"] += d_dollar; bu["theta_day"] += th
            bu["vega"] += vg; bu["value"] += o["value"]; bu["contracts"] += contracts
            lad = ladder.setdefault(exp, {"expiry": exp, "dte": dte, "contracts": 0.0,
                                          "value": 0.0, "theta_day": 0.0, "delta_dollar": 0.0})
            lad["contracts"] += contracts; lad["value"] += o["value"]
            lad["theta_day"] += th; lad["delta_dollar"] += d_dollar
        enriched.append({
            "underlying": u, "option_type": otype, "strike": strike, "expiry": exp, "dte": dte,
            "contracts": contracts, "value": o["value"], "spot": round(S, 2) if S else None,
            "iv": round(iv * 100, 1), "delta": round(g["delta"], 3), "gamma": round(g["gamma"], 4),
            "theta_day_$": round(g["theta_day"] * mult, 2) if S else None,
            "vega_$": round(g["vega_1pct"] * mult, 2) if S else None,
            "delta_$": round(g["delta"] * mult * S, 0) if S else None})

    # Total directional delta of the whole book: options Δ$ + stock value (stock delta = 1)
    stock_delta = sum(float(p.get("value") or 0) for p in (equity_positions or []))
    total_book_delta = net_delta_dollar + stock_delta

    for v in by_underlying.values():
        for k in ("delta_dollar", "theta_day", "vega", "value"):
            v[k] = round(v[k], 2)
    ladder_list = sorted(ladder.values(), key=lambda x: x["expiry"])
    for l in ladder_list:
        for k in ("value", "theta_day", "delta_dollar"):
            l[k] = round(l[k], 2)

    # Alerts
    alerts = []
    soon = [e for e in enriched if e.get("dte") is not None and e["dte"] <= 7]
    if soon:
        alerts.append({"level": "warn",
                       "msg": f"{len(soon)} posición(es) vencen en ≤7 días — el theta se acelera y el gamma se dispara."})
    if net_theta < 0:
        alerts.append({"level": "info",
                       "msg": f"El libro de opciones pierde ${abs(round(net_theta,0)):,.0f}/día por decaimiento temporal (theta neto)."})
    elif net_theta > 0:
        alerts.append({"level": "info",
                       "msg": f"El libro de opciones cobra ${round(net_theta,0):,.0f}/día de theta neto (el decaimiento juega a tu favor)."})
    if by_underlying:
        top_u = max(by_underlying.items(), key=lambda kv: abs(kv[1]["delta_dollar"]))
        tot_abs = sum(abs(v["delta_dollar"]) for v in by_underlying.values()) or 1
        share = abs(top_u[1]["delta_dollar"]) / tot_abs
        if share >= 0.6 and len(by_underlying) > 1:
            alerts.append({"level": "warn",
                           "msg": f"{top_u[0]} concentra {share*100:.0f}% del delta de opciones — riesgo direccional poco diversificado."})

    return _json_safe({
        "ok": True, "n_options": len(options),
        "greeks": {"net_delta_dollar": round(net_delta_dollar, 0),
                   "net_delta_shares": round(net_delta_dollar / spot_cache[options[0]["underlying"]], 0)
                   if spot_cache.get(options[0]["underlying"]) else None,
                   "net_gamma": round(net_gamma, 2),
                   "net_theta_day": round(net_theta, 2),
                   "net_vega_1pct": round(net_vega, 2)},
        "total_book_delta_dollar": round(total_book_delta, 0),
        "options_delta_dollar": round(net_delta_dollar, 0),
        "stock_delta_dollar": round(stock_delta, 0),
        "by_underlying": by_underlying, "ladder": ladder_list,
        "positions": sorted(enriched, key=lambda x: (x["expiry"], x["underlying"])),
        "alerts": alerts, "rf": RF,
        "note": "Las griegas son estimaciones del modelo Black-Scholes con IV de yfinance; "
                "pueden diferir de las de tu broker.",
        "generated_at": now.strftime('%m/%d/%Y, %I:%M:%S %p')})


@app.get("/api/portfolio-options")
def portfolio_options():
    """Book-level options Greeks panel. Reads the stored option snapshot (SnapTrade)
    + equity snapshot (for total directional delta) and prices everything with BSM."""
    try:
        opts = get_options_snapshot()
        eq = get_portfolio_snapshot()
        return compute_options_analytics(opts, eq)
    except Exception as e:
        return {"ok": False, "error": f"{e}"}


# ── PORTFOLIO OPTIMIZER (mean-variance, Monte Carlo efficient frontier) ────────
def get_agent_views(tickers):
    """Latest saved agent view per ticker for Black-Litterman: expected 12m return
    (from upside_pct) + the conviction used as view confidence."""
    out = {}
    try:
        conn = _db()
        for tk in tickers:
            row = conn.execute(
                "SELECT upside_pct, conviction FROM reports WHERE ticker=? ORDER BY created_ts DESC LIMIT 1",
                (tk.upper(),)).fetchone()
            if row and row["upside_pct"] is not None:
                try:
                    out[tk] = {"exp_return": float(row["upside_pct"]) / 100.0,
                               "conviction": float(row["conviction"] or 50)}
                except (TypeError, ValueError):
                    pass
        conn.close()
    except Exception:
        pass
    return out


def black_litterman_returns(Sigma, w_prior, view_idx, view_q, view_conf, delta=2.5, tau=0.05):
    """Canonical Black-Litterman posterior expected returns.
    Sigma: n×n annualized covariance. w_prior: n prior (benchmark/current) weights.
    view_idx: indices with absolute views; view_q: their expected returns;
    view_conf: confidence in (0,1] (higher → view trusted more, smaller Omega).
    Returns (posterior_mu (n,), pi_equilibrium (n,))."""
    Sigma = np.asarray(Sigma, dtype=float)
    w_prior = np.asarray(w_prior, dtype=float)
    n = Sigma.shape[0]
    pi = delta * Sigma @ w_prior                      # implied equilibrium excess returns
    if not view_idx:
        return pi.copy(), pi
    k = len(view_idx)
    P = np.zeros((k, n))
    for r, idx in enumerate(view_idx):
        P[r, idx] = 1.0
    Q = np.asarray(view_q, dtype=float)
    omega_diag = []
    for r, idx in enumerate(view_idx):
        c = min(0.99, max(0.01, float(view_conf[r])))
        omega_diag.append(max(1e-8, tau * float(Sigma[idx, idx]) * (1.0 - c) / c))
    Omega = np.diag(omega_diag)
    tauSigma = tau * Sigma
    try:
        inv_tauSigma = np.linalg.inv(tauSigma)
        inv_Omega = np.linalg.inv(Omega)
        A = inv_tauSigma + P.T @ inv_Omega @ P
        b = inv_tauSigma @ pi + P.T @ inv_Omega @ Q
        posterior = np.linalg.solve(A, b)
    except np.linalg.LinAlgError:
        return pi.copy(), pi
    return posterior, pi


def compute_portfolio_optimizer(positions, lookback="2y", max_weight=0.25, n_sims=8000):
    import pandas as pd
    eq = [p for p in positions if p.get("ticker") and float(p.get("value") or 0) > 0]
    if len(eq) < 2:
        return {"ok": False, "error": "Se necesitan al menos 2 posiciones para optimizar."}
    total = sum(float(p["value"]) for p in eq)
    cur_w_all = {p["ticker"]: float(p["value"]) / total for p in eq}
    names = {p["ticker"]: p.get("name", p["ticker"]) for p in eq}
    tickers = list(cur_w_all.keys())

    closes = {}
    for t in tickers:
        try:
            h = yf.Ticker(t).history(period=lookback)
            if not h.empty and "Close" in h:
                closes[t] = h["Close"]
        except Exception:
            pass
    px = pd.DataFrame(closes).dropna(how="all")
    rets = px.pct_change().dropna(how="all")
    avail = [t for t in tickers if t in rets.columns and rets[t].notna().sum() >= 60]
    if len(avail) < 2:
        return {"ok": False, "error": "Historial insuficiente para optimizar (se necesitan 2+ con ~60d)."}

    R = rets[avail].dropna()
    n = len(avail)
    mu = R.mean().values * 252.0                      # annualized expected return
    cov = R.cov().values * 252.0                      # annualized covariance
    RF = 0.043
    eff_max = max(max_weight, 1.0 / n + 1e-9)         # keep feasible if few names

    rng = np.random.default_rng(7)

    # current portfolio weights (renormalized) — used as the Black-Litterman prior
    cw = np.array([cur_w_all[t] for t in avail], dtype=float)
    cw = cw / cw.sum()

    # ── #1 BLACK-LITTERMAN: equilibrio de mercado + las vistas guardadas del agente ──
    agent_views = get_agent_views(avail)
    view_idx = [i for i, t in enumerate(avail) if t in agent_views]
    if view_idx:
        vq = [agent_views[avail[i]]["exp_return"] for i in view_idx]
        vc = [min(0.95, max(0.05, agent_views[avail[i]]["conviction"] / 100.0)) for i in view_idx]
        mu_bl, pi_eq = black_litterman_returns(cov, cw, view_idx, vq, vc)
        er_source = "black_litterman"
    else:
        mu_bl, pi_eq = mu.copy(), mu.copy()
        er_source = "historical"

    def stats(w):
        ret = float(w @ mu_bl)
        vol = float(math.sqrt(max(w @ cov @ w, 1e-12)))
        sharpe = (ret - RF) / vol if vol > 0 else 0.0
        return ret, vol, sharpe

    cur_ret, cur_vol, cur_sharpe = stats(cw)

    # ── MANDATO 70/30: bucket crecimiento 70%, bucket estable 30% ──
    G_TARGET, S_TARGET = 0.70, 0.30
    growth_idx = [i for i, t in enumerate(avail) if t.upper() not in STABLE_LEADERS]
    stable_idx = [i for i, t in enumerate(avail) if t.upper() in STABLE_LEADERS]
    mandate_feasible = len(growth_idx) >= 1 and len(stable_idx) >= 1

    def cap_block(w, idx, cap, target_sum):
        """Water-filling cap within a bucket, preserving the bucket's target sum."""
        if not idx:
            return
        ai = np.array(idx)
        bcap = max(cap, target_sum / len(idx) + 1e-12)
        sub = w[ai].astype(float)
        for _p in range(25):
            over = sub > bcap + 1e-12
            if not over.any():
                break
            excess = float((sub[over] - bcap).sum())
            sub[over] = bcap
            under = ~over
            us = float(sub[under].sum())
            if us <= 0:
                break
            sub[under] = sub[under] + excess * (sub[under] / us)
        ss = float(sub.sum())
        if ss > 0:
            sub = sub / ss * target_sum
        w[ai] = sub

    def full_waterfill(w):
        for _p in range(25):
            over = w > eff_max + 1e-12
            if not over.any():
                break
            excess = float((w[over] - eff_max).sum())
            w[over] = eff_max
            under = ~over
            us = float(w[under].sum())
            if us <= 0:
                break
            w[under] = w[under] + excess * (w[under] / us)
        s = w.sum()
        if s > 0:
            w /= s

    def sample_weights():
        if mandate_feasible:
            w = np.zeros(n)
            gw = rng.dirichlet(np.ones(len(growth_idx))) * G_TARGET
            sw = rng.dirichlet(np.ones(len(stable_idx))) * S_TARGET
            for j, i in enumerate(growth_idx):
                w[i] = gw[j]
            for j, i in enumerate(stable_idx):
                w[i] = sw[j]
            cap_block(w, growth_idx, eff_max, G_TARGET)
            cap_block(w, stable_idx, eff_max, S_TARGET)
            return w
        w = rng.dirichlet(np.ones(n))
        full_waterfill(w)
        return w

    best_sharpe = {"sharpe": -1e9}; min_vol = {"vol": 1e9}
    frontier = []
    for _ in range(n_sims):
        w = sample_weights()
        ret, vol, sharpe = stats(w)
        frontier.append((vol, ret, sharpe))
        if sharpe > best_sharpe["sharpe"]:
            best_sharpe = {"w": w.copy(), "ret": ret, "vol": vol, "sharpe": sharpe}
        if vol < min_vol["vol"]:
            min_vol = {"w": w.copy(), "ret": ret, "vol": vol, "sharpe": sharpe}

    def pack(d):
        return {"ann_return_pct": round(d["ret"] * 100, 1),
                "ann_vol_pct": round(d["vol"] * 100, 1),
                "sharpe": round(d["sharpe"], 2)}

    def weights_list(w):
        return sorted([{"ticker": avail[i], "name": names.get(avail[i], avail[i]),
                        "weight_pct": round(float(w[i]) * 100, 1),
                        "bucket": ("estable" if avail[i].upper() in STABLE_LEADERS else "crecimiento")}
                       for i in range(n)], key=lambda x: x["weight_pct"], reverse=True)

    def bucket_split(w):
        g = sum(float(w[i]) for i in growth_idx)
        s = sum(float(w[i]) for i in stable_idx)
        return round(g * 100, 1), round(s * 100, 1)

    # rebalance suggestion: current -> max-sharpe
    msw = best_sharpe["w"]
    rebalance = []
    for i, t in enumerate(avail):
        cur = round(float(cw[i]) * 100, 1)
        opt = round(float(msw[i]) * 100, 1)
        dlt = round(opt - cur, 1)
        if opt < 1.5 and cur >= 2:
            act = "vender"        # el óptimo te quiere prácticamente fuera de la posición
        elif dlt <= -3:
            act = "reducir"
        elif dlt >= 3:
            act = "aumentar"
        else:
            act = "mantener"
        rebalance.append({"ticker": t, "current_pct": cur, "optimal_pct": opt,
                          "delta_pct": dlt, "action": act,
                          "bucket": ("estable" if t.upper() in STABLE_LEADERS else "crecimiento")})
    rebalance.sort(key=lambda x: abs(x["delta_pct"]), reverse=True)

    cur_g, cur_s = bucket_split(cw)
    ms_g, ms_s = bucket_split(msw)
    views_applied = [{"ticker": avail[i],
                      "exp_return_pct": round(agent_views[avail[i]]["exp_return"] * 100, 1),
                      "conviction": round(agent_views[avail[i]]["conviction"], 0)} for i in view_idx]
    views_applied.sort(key=lambda x: x["exp_return_pct"], reverse=True)
    expected_returns = sorted([{"ticker": avail[i], "er_pct": round(float(mu_bl[i]) * 100, 1),
                                "has_view": avail[i] in agent_views} for i in range(n)],
                              key=lambda x: x["er_pct"], reverse=True)

    # subsample frontier for plotting
    step = max(1, len(frontier) // 350)
    fr = [{"vol": round(v * 100, 2), "ret": round(r * 100, 2), "sharpe": round(s, 2)}
          for (v, r, s) in frontier[::step]]

    return {
        "ok": True,
        "positions_analyzed": n,
        "rf_assumed_pct": round(RF * 100, 1),
        "max_weight_pct": round(eff_max * 100, 1),
        "expected_returns_source": er_source,
        "views_applied": views_applied,
        "expected_returns": expected_returns,
        "mandate": {"growth_target_pct": 70, "stable_target_pct": 30, "feasible": mandate_feasible,
                    "current_growth_pct": cur_g, "current_stable_pct": cur_s,
                    "optimal_growth_pct": ms_g, "optimal_stable_pct": ms_s,
                    "note": ("Mandato 70/30 aplicado como restricción dura." if mandate_feasible
                             else "No se pudo aplicar 70/30: faltan posiciones en un bucket (tu book es todo crecimiento o todo estable).")},
        "current":    {**pack({"ret": cur_ret, "vol": cur_vol, "sharpe": cur_sharpe}), "weights": weights_list(cw)},
        "max_sharpe": {**pack(best_sharpe), "weights": weights_list(best_sharpe["w"])},
        "min_vol":    {**pack(min_vol),     "weights": weights_list(min_vol["w"])},
        "rebalance": rebalance,
        "frontier": fr,
        "generated_at": datetime.now().strftime('%m/%d/%Y, %I:%M:%S %p'),
    }


@app.get("/api/portfolio-optimizer")
def get_portfolio_optimizer(access_token: str = "", account_id: str = "", max_weight: float = 0.25):
    """Black-Litterman + 70/30 mandate optimization (Monte Carlo frontier) on the real book."""
    try:
        positions, _src = _resolve_positions(access_token, account_id)
        if not positions:
            return {"ok": False, "error": "No se encontraron posiciones. Carga tus holdings con SnapTrade (o conecta Plaid)."}
        mw = max(0.05, min(float(max_weight), 1.0))
        return compute_portfolio_optimizer(positions, max_weight=mw)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Optimizer error: {str(e)}")


@app.get("/api/portfolio-ideas")
def get_portfolio_ideas(access_token: str = "", account_id: str = "", n: int = 5):
    """#Ideas — Genera ideas de inversión NUEVAS (tickers que NO tienes ni has analizado) que
    DIVERSIFIQUEN tu book concentrado en IA, con % sugerido y un razonamiento completo
    (a qué se dedica, cuánto crece hoy, cuánto se espera que crezca, deuda/rentabilidad,
    futuro de la acción vs futuro del mundo, riesgos). Gemini con búsqueda en vivo.
    La opción 'Analyze' del menú de 3 puntos corre el agente completo sobre la idea."""
    if not API_KEY:
        return {"ok": False, "error": "Falta GEMINI_API_KEY para generar ideas de inversión."}
    try:
        held = []
        try:
            positions, _src = _resolve_positions(access_token, account_id)
            held = sorted({str(p.get("ticker") or "").upper() for p in (positions or []) if p.get("ticker")})
        except Exception:
            held = []
        held = [h for h in held if h]
        n = max(2, min(int(n), 8))
        held_txt = ", ".join(held) if held else "(libro vacío)"
        prompt = f"""Eres el CIO de un fondo. El inversionista YA tiene estas posiciones: {held_txt}.
Su libro está MUY concentrado en IA y semiconductores. Propón EXACTAMENTE {n} ideas de COMPRA NUEVAS
(acciones de EE.UU. con ticker real que NO estén en su lista de arriba). Quiere COMPRAR cosas buenas, así que
incluye una MEZCLA:
(a) varias empresas de ALTO CRECIMIENTO — AUNQUE tengan valoración o riesgo MAYOR (no las descartes por caras
    si el crecimiento lo justifica; el inversionista tiene apetito de crecimiento),
(b) algunas que DIVERSIFIQUEN su concentración en IA (otros sectores/factores),
(c) idealmente con crecimiento de ingresos año-tras-año, reduciendo deuda o a punto de volverse rentables — o ya
    rentables y de calidad.
Usa datos REALES y recientes (BÚSCALOS con la herramienta de búsqueda; cifras verdaderas, no inventadas).
Devuelve SOLO un objeto JSON crudo (sin markdown, sin ```), con esta forma EXACTA:
{{"ideas":[{{"ticker":"XXX","name":"Nombre real","sector":"sector","suggested_pct":<entero 3-15>,
"idea_type":"crecimiento|diversificador|calidad","growth_tier":"alto|medio",
"reasoning":{{
"why":"por qué debería COMPRARLA — 2-3 frases concretas",
"keep_growing":"por qué la empresa seguirá creciendo",
"current_growth":"cuánto está creciendo HOY, con cifras reales (ej. ingresos +28% YoY)",
"expected_growth":"cuánto se espera que crezca, con cifras (consenso/guía)",
"what_it_does":"a qué se dedica en una frase",
"future_vs_world":"el futuro de la acción comparado con hacia dónde va el mundo (IA, energía, salud, defensa, etc.)",
"debt_profitability":"estado de su deuda y rentabilidad",
"valuation_risk":"valoración (ej. P/E, P/S) y por qué vale la pena aunque sea mayor, o el riesgo que implica",
"risks":"riesgos principales en una frase"
}}}}]}}
Reglas duras: tickers reales que NO estén en [{held_txt}]; al menos la MITAD deben ser growth_tier 'alto';
suggested_pct realista (en conjunto las ideas suman <= 40% — son ideas que se suman a un book existente, no el
book entero); todas las cifras deben ser reales y recientes."""
        resp = client_gemini.models.generate_content(
            model='gemini-2.5-flash', contents=prompt,
            config=types.GenerateContentConfig(temperature=0.5,
                tools=[types.Tool(google_search=types.GoogleSearch())]))
        raw = (resp.text or "").strip()
        if raw.startswith("```"):
            raw = re.sub(r'^```[a-z]*\n?', '', raw)
            raw = re.sub(r'\n?```$', '', raw)
        data = json.loads(raw.strip())
        ideas = data.get("ideas", []) if isinstance(data, dict) else []
        heldset = set(held)
        clean = []
        for it in ideas:
            tk = str(it.get("ticker", "")).upper().strip()
            if not tk or tk in heldset:
                continue
            try:
                it["suggested_pct"] = int(round(float(it.get("suggested_pct", 5))))
            except Exception:
                it["suggested_pct"] = 5
            it["suggested_pct"] = max(1, min(it["suggested_pct"], 25))
            it["idea_type"] = str(it.get("idea_type", "crecimiento")).lower().strip()
            it["growth_tier"] = str(it.get("growth_tier", "medio")).lower().strip()
            it["ticker"] = tk
            clean.append(it)
        return {"ok": True, "ideas": clean, "held": held,
                "generated_at": datetime.now().strftime('%m/%d/%Y, %I:%M:%S %p')}
    except HTTPException:
        raise
    except Exception as e:
        return {"ok": False, "error": f"No se pudieron generar ideas: {e}"}

# ═════════════════════════════════════════════════════════════════════════════
# #1 — COLECTOR AUTOMÁTICO DE SNAPSHOTS (scheduler in-process)
# El loop empírico (backtest, IC, calibración, confianza de proyecciones) depende
# de capturar un snapshot diario por ticker. Sin esto, todo cae a reglas fijas.
# ═════════════════════════════════════════════════════════════════════════════
VERTEX_PRIMARY_TICKERS = [t.strip().upper() for t in
    os.environ.get("VERTEX_PRIMARY_TICKERS", "SPY,NVDA,PLTR,AMD,GOOGL").split(",") if t.strip()]

_SCHED_STATE = {"started": False, "last_run": None, "last_count": 0,
                "last_tickers": [], "running": False, "next_run": None}

def _scheduler_tickers():
    """Universo a snapshotear: primarios + holdings persistidos + tickers analizados recientemente."""
    s = set(VERTEX_PRIMARY_TICKERS)
    try:
        for h in (get_portfolio_snapshot() or []):
            t = str(h.get("ticker") or "").upper().strip()
            if t and t.replace(".", "").isalnum() and len(t) <= 6:
                s.add(t)
    except Exception:
        pass
    try:
        conn = _db()
        rows = conn.execute("SELECT DISTINCT ticker FROM reports ORDER BY created_ts DESC LIMIT 40").fetchall()
        conn.close()
        for r in rows:
            t = str(r["ticker"] or "").upper().strip()
            if t:
                s.add(t)
    except Exception:
        pass
    return sorted(s)

def _run_daily_collection(throttle=1.0):
    """Captura el snapshot de hoy para cada ticker del universo. Devuelve cuántos capturó."""
    if _SCHED_STATE["running"]:
        return 0
    _SCHED_STATE["running"] = True
    n = 0
    tickers = _scheduler_tickers()
    try:
        for tk in tickers:
            try:
                _collect_signal_snapshot(tk)
                n += 1
                time.sleep(throttle)            # throttle suave para Quant Data
            except Exception:
                pass
    finally:
        _SCHED_STATE.update({"running": False, "last_run": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                             "last_count": n, "last_tickers": tickers})
    return n

def _seconds_until_next_run():
    """Próxima corrida ~21:30 UTC (post-cierre US; ~4:30pm EST / 5:30pm EDT)."""
    now = datetime.now(timezone.utc)
    target = now.replace(hour=21, minute=30, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    # saltar fines de semana
    while target.weekday() >= 5:
        target += timedelta(days=1)
    _SCHED_STATE["next_run"] = target.strftime("%Y-%m-%d %H:%M UTC")
    return max(60.0, (target - now).total_seconds())

def _scheduler_loop():
    while True:
        try:
            time.sleep(_seconds_until_next_run())
            if datetime.now(timezone.utc).weekday() < 5:    # solo días de mercado
                _run_daily_collection()
        except Exception:
            time.sleep(3600)

def _start_scheduler():
    if _SCHED_STATE["started"] or os.environ.get("VERTEX_SCHEDULER", "1") == "0":
        return
    threading.Thread(target=_scheduler_loop, daemon=True, name="vertex-collector").start()
    _SCHED_STATE["started"] = True

@app.on_event("startup")
def _vertex_startup():
    try:
        _start_scheduler()
    except Exception as e:
        print(f"[SCHED] no se pudo iniciar: {e}")
    # #6 — aviso si faltan claves (deben venir del entorno, ya no están en el código)
    try:
        _need = {"GEMINI_API_KEY": API_KEY, "QUANTDATA_API_KEY": QUANTDATA_API_KEY,
                 "OPENAI_API_KEY": OPENAI_API_KEY, "XAI_API_KEY": XAI_API_KEY,
                 "FINNHUB_API_KEY": FINNHUB_API_KEY}
        missing = [k for k, v in _need.items() if not v]
        if missing:
            print("[KEYS] ⚠ Faltan variables de entorno: " + ", ".join(missing)
                  + "  → carga vertex.env (set -a; source vertex.env; set +a) antes de uvicorn.")
    except Exception:
        pass

@app.get("/api/scheduler/status")
def scheduler_status():
    _seconds_until_next_run()
    return {"started": _SCHED_STATE["started"], "running": _SCHED_STATE["running"],
            "last_run": _SCHED_STATE["last_run"], "last_count": _SCHED_STATE["last_count"],
            "next_run": _SCHED_STATE["next_run"], "universe": _scheduler_tickers(),
            "primary": VERTEX_PRIMARY_TICKERS}

@app.get("/api/scheduler/run-now")
def scheduler_run_now():
    """Dispara la colección de snapshots de inmediato (en un hilo, para no bloquear)."""
    if _SCHED_STATE["running"]:
        return {"ok": False, "note": "Ya hay una colección en curso."}
    threading.Thread(target=_run_daily_collection, daemon=True, name="vertex-collect-now").start()
    return {"ok": True, "note": "Colección disparada en segundo plano.", "universe": _scheduler_tickers()}