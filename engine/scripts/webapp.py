"""WBJ mini web app: search any US-listed company, analyze on demand.

Usage: .venv/bin/python scripts/webapp.py  ->  http://localhost:8765
Stdlib http.server only — no extra dependencies.
Card-dashboard UI (light, big numbers, plain-Spanish explanations) modeled
on the reference screenshots in Referencias/.
"""

from __future__ import annotations

import json
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from wbj.cli import _build_packet, _compute
from wbj.config import load_settings
from wbj.providers.cache import Cache
from wbj.providers.edgar import (
    _EDGAR_HEADERS,
    _GLOBAL_CACHE_TICKER,
    _MAX_AGE_TICKERS,
    TICKERS_URL,
    EdgarProvider,
)
from wbj.screener import screen as run_screen
from wbj.brief import company_brief
from wbj.quick import quick_scorecard
from wbj.targets import live_price, narrative, price_history, price_targets

PORT = 8765
_lock = threading.Lock()


settings = load_settings()
edgar = EdgarProvider(settings, Cache(settings.cache_dir))


def _run_dir(ticker: str) -> Path:
    """This run's output directory — same layout the CLI uses
    (`Reportes/<TICKER>/<YYYY-MM-DD>/`) so both paths agree."""
    return settings.reports_dir / ticker.upper() / date.today().isoformat()


def _charts_dir(ticker: str) -> Path:
    """Writable variant, for the report run that produces the PNGs."""
    d = _run_dir(ticker)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _chart_path(ticker: str, name: str) -> Path | None:
    """Resolve a chart for *serving*. Read-only on purpose: creating the
    directory here let any 404 image request write to disk."""
    if not ticker.isalnum() or not name.endswith(".png"):
        return None
    base = _run_dir(ticker).resolve()
    path = (base / name).resolve()
    # Reject anything that escapes the ticker's own directory.
    if base not in path.parents or not path.is_file():
        return None
    return path


def ticker_map() -> list[dict]:
    payload = edgar.get_json(
        TICKERS_URL, {}, "tickers", _GLOBAL_CACHE_TICKER,
        max_age_days=_MAX_AGE_TICKERS, headers=_EDGAR_HEADERS,
    )
    if not isinstance(payload, dict):
        return []
    return [e for e in payload.values() if isinstance(e, dict)]


def search(q: str, limit: int = 8) -> list[dict]:
    q = q.strip().upper()
    if not q:
        return []
    exact, prefix, name = [], [], []
    for e in ticker_map():
        t = str(e.get("ticker", "")).upper()
        n = str(e.get("title", "")).upper()
        row = {"ticker": t, "name": e.get("title", "")}
        if t == q:
            exact.append(row)
        elif t.startswith(q):
            prefix.append(row)
        elif q in n:
            name.append(row)
    return (exact + prefix + name)[:limit]


def _history(packet: dict) -> list[dict]:
    """Yearly revenue + net margin for the charts."""
    rev = {r["end"]: r["val"] for r in packet["annual"]["revenue"]}
    ni = {r["end"]: r["val"] for r in packet["annual"]["net_income"]}
    rows = []
    for end in sorted(rev)[-6:]:
        rows.append({
            "year": end[:4],
            "revenue": rev[end],
            "margin": (ni[end] / rev[end]) if end in ni and rev[end] else None,
        })
    return rows


#: Category order and weights, straight out of `CLAUDE.md`'s table.
_VICTOR_ORDER = ("business", "financial", "market", "technical", "risk", "valuation")


def victor_scorecard(ticker: str, lang: str = "en") -> dict | None:
    """The six specialists, in the shape `quick_scorecard` returns.

    This is the methodology: `CLAUDE.md` step 3 runs the six specialists and
    step 4 aggregates them with gates and overrides. `wbj.quick` says of
    itself that it is "a *quick* read, **not the full specialist
    methodology**" — partial metric sets, no gates, no 70% coverage floor —
    so it cannot be the headline number without the page claiming Victor's
    method for something that is not it.

    Same output shape as the quick path so the narrative, brief and saved
    prediction keep working unchanged; the caller labels which is which.

    Returns None when the packet cannot be built (an unlisted ticker, a
    provider outage). The caller then falls back to the quick read and says
    so, rather than showing nothing.
    """
    from datetime import datetime, timezone

    from wbj.deep import build_providers
    from wbj.overlay.from_packet import build_overlay
    from wbj.packet.builder import build_packet
    from wbj.specialists import (business, financial, market, risk,
                                 technical, valuation)

    mods = {"business": business, "financial": financial, "market": market,
            "technical": technical, "risk": risk, "valuation": valuation}
    try:
        packet = build_packet(ticker, build_providers(settings),
                              now=datetime.now(timezone.utc))
        overlay = build_overlay(packet, settings)
    except Exception:
        return None

    rows, covered, raw_total = [], 0.0, 0.0
    for key in _VICTOR_ORDER:
        try:
            out = mods[key].run(packet, overlay)
        except Exception:
            rows.append({"key": key, "label": key.title(), "max_points": None,
                         "score10": None, "points": None, "coverage": 0.0,
                         "status": "not_scorable", "reason": "specialist failed"})
            continue
        cat = out.category
        s10 = round(cat.score_10, 1) if cat.score_10 is not None else None
        if s10 is not None:
            covered += cat.max_points
        # `raw_total` is the sum of awarded points out of 100 --
        # SCORING_AND_GATES.md's own quantity, the one the gates read. A
        # weighted average of the per-category 0-10s is a different number
        # (4.3 vs 4.4 on AAPL) and is not the one Victor defines.
        raw_total += cat.awarded_points or 0.0
        rows.append({
            "key": key, "label": key.title(), "max_points": cat.max_points,
            "score10": s10, "points": cat.awarded_points,
            "coverage": out.coverage, "confidence": cat.confidence,
            "status": "scored" if s10 is not None else "not_scorable",
        })
    if not covered:
        return None
    return {
        "categories": rows,
        "overall_10": round(raw_total / 10.0, 1),
        "raw_total": round(raw_total, 2),
        # Coverage-weighted, so a category scored on half its inputs says so.
        "evidence_points_covered": int(round(
            sum((r["max_points"] or 0) * (r["coverage"] or 0) for r in rows))),
        "evidence_points_total": 100,
        "method": "victor_full",
    }


def analyze(ticker: str, lang: str = "en") -> dict:
    from datetime import date

    from wbj.memoria import save_prediction

    packet = _build_packet(ticker)
    result = _compute(packet)
    # The headline is Victor's methodology — the six specialists with their
    # gates and coverage floors. The quick read stays on the page beside it,
    # labelled as what it is: `wbj.quick`'s own docstring calls it "a *quick*
    # read, not the full specialist methodology".
    #
    # They disagree on purpose and by a lot: AAPL is 7.5/10 quick and 4.4/10
    # under the full method, which also raises OVERRIDE_6 (coverage below
    # 70% -> no profile gate may pass). Showing the quick number as the
    # headline read as Victor's answer when it is not.
    quick = quick_scorecard(packet, lang)
    victor = victor_scorecard(ticker, lang)
    result["scorecard"] = victor or quick
    result["scorecard_quick"] = quick
    result["scorecard_method"] = "victor_full" if victor else "quick_fallback"
    price = live_price(ticker, fmp_api_key=settings.fmp_api_key)
    targets = price_targets(packet, price, lang)
    # Seed agent memory: every web analysis also records its prediction.
    save_prediction(settings.reports_dir, ticker, date.today(),
                    result["scorecard"], targets)
    result["targets"] = targets
    result["narrative"] = narrative(packet, result["scorecard"], targets, lang)
    result["brief"] = company_brief(packet, result["scorecard"], targets, lang)
    result["history"] = _history(packet)
    # La serie ya no viene de Yahoo: se pide a FMP con la misma clave que el
    # precio. Sin clave devuelve vacio y la grafica no se dibuja, que es
    # preferible a dibujarla desde una fuente que el sistema no declara.
    result["chart"] = price_history(ticker, fmp_api_key=settings.fmp_api_key)
    return result


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Warren Buffett Jr — Scorecard</title>
<style>
  :root { color-scheme: light;
    --page:#edeff5; --card:#ffffff; --ink:#16181d; --ink2:#5b6270; --muted:#9aa1ad;
    --grid:#eef0f4; --green:#22a06b; --green-bg:#e7f6ef; --purple:#7c5cfc;
    --purple-bg:#f0ecfe; --orange:#f5a623; --orange-bg:#fef4e2; --red:#e5484d;
    --red-bg:#fdecec; --blue:#3b82f6; --blue-bg:#e8f1fe; }
  * { margin:0; box-sizing:border-box; }
  body { background:var(--page); color:var(--ink);
    font-family:system-ui,-apple-system,"Segoe UI",sans-serif; padding:36px 20px 60px; }
  .wrap { max-width:1040px; margin:0 auto; }
  .kicker { font-size:12px; letter-spacing:.14em; text-transform:uppercase;
    color:var(--muted); font-weight:600; margin-bottom:6px; }
  h1 { font-size:26px; margin-bottom:16px; letter-spacing:-.02em; }
  .topbar { display:flex; gap:12px; align-items:stretch; max-width:760px; }
  .searchbox { position:relative; flex:1; max-width:560px; }
  .discover { font:inherit; font-size:14.5px; font-weight:700; padding:0 22px;
    border:0; border-radius:14px; background:var(--purple); color:#fff; cursor:pointer;
    box-shadow:0 1px 3px rgba(20,22,30,.15); white-space:nowrap; }
  .discover:hover { filter:brightness(1.08); }
  .langtoggle { position:fixed; top:18px; right:18px; z-index:20; font:inherit; font-size:13px;
    font-weight:800; width:44px; height:36px; border:1px solid var(--grid); border-radius:12px;
    background:var(--card); color:var(--ink2); cursor:pointer; box-shadow:0 1px 3px rgba(20,22,30,.1); }
  .langtoggle:hover { color:var(--purple); border-color:var(--purple); }
  table.scr { width:100%; border-collapse:collapse; margin-top:14px; font-size:13.5px; }
  table.scr th { text-align:left; font-size:11.5px; text-transform:uppercase;
    letter-spacing:.08em; color:var(--muted); padding:0 10px 10px 0; font-weight:600; }
  table.scr td { padding:11px 10px 11px 0; border-top:1px solid var(--grid);
    font-variant-numeric:tabular-nums; }
  table.scr td.nm { max-width:230px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  table.scr .tk { font-weight:800; cursor:pointer; color:var(--purple); }
  table.scr .num { text-align:right; }
  .scorepill { display:inline-flex; align-items:center; gap:8px; }
  .scorepill .mini { width:64px; height:7px; background:var(--grid); border-radius:5px; overflow:hidden; }
  .scorepill .mini i { display:block; height:100%; background:var(--green); border-radius:5px; }
  input { width:100%; font:inherit; font-size:16px; padding:14px 18px;
    border-radius:14px; border:1px solid transparent; background:var(--card);
    color:var(--ink); outline:none; box-shadow:0 1px 3px rgba(20,22,30,.07); }
  input:focus { border-color:var(--purple); }
  .sugg { position:absolute; top:calc(100% + 8px); left:0; right:0; z-index:5;
    background:var(--card); border-radius:14px; overflow:hidden;
    box-shadow:0 12px 32px rgba(20,22,30,.16); display:none; }
  .sugg button { display:flex; gap:12px; width:100%; text-align:left; font:inherit;
    font-size:14px; padding:12px 18px; border:0; background:none; color:var(--ink);
    cursor:pointer; border-bottom:1px solid var(--grid); }
  .sugg button:last-child { border-bottom:none; }
  .sugg button:hover { background:var(--grid); }
  .sugg b { min-width:56px; } .sugg span { color:var(--ink2); }
  #status { margin:20px 2px; color:var(--ink2); font-size:14px; }
  .grid { display:grid; grid-template-columns:repeat(12, 1fr); gap:18px;
    margin-top:22px; display:none; }
  .card { background:var(--card); border-radius:18px; padding:24px;
    box-shadow:0 1px 3px rgba(20,22,30,.06); }
  .c-hero { grid-column:span 7; } .c-words { grid-column:span 5; }
  .c-chart { grid-column:span 12; background:#0e1113; color:#e8eaed; }
  .c-score { grid-column:span 5; } .c-target { grid-column:span 7; }
  .c-brief { grid-column:span 12; }
  @media (max-width:860px) { .c-hero,.c-words,.c-chart,.c-score,.c-target { grid-column:span 12; } }
  /* --- company brief panel --- */
  .brief-grid { display:grid; grid-template-columns:repeat(12,1fr); gap:18px; margin-top:6px; }
  .brief-col { grid-column:span 6; } .brief-col.full { grid-column:span 12; }
  @media (max-width:860px) { .brief-col { grid-column:span 12; } }
  .bh { font-size:13px; font-weight:800; text-transform:uppercase; letter-spacing:.04em;
    color:var(--ink2); margin:2px 0 12px; }
  .classpill { display:inline-flex; align-items:center; gap:8px; padding:8px 14px;
    border-radius:999px; font-weight:800; font-size:15px; margin-bottom:6px; }
  .classpill.favors { background:var(--green-bg); color:var(--green); }
  .classpill.neutral { background:var(--orange-bg); color:var(--orange); }
  .classpill.avoid { background:var(--red-bg); color:var(--red); }
  .revisit { font-size:13px; color:var(--ink2); margin:4px 0 14px; }
  .cat-row { display:grid; grid-template-columns:1fr auto; gap:10px; padding:7px 0;
    border-top:1px solid var(--grid); font-size:14px; }
  .cat-row .mn { font-weight:700; }
  .cat-row .mn.ns { color:var(--muted); font-weight:600; }
  .probbar { display:flex; height:26px; border-radius:8px; overflow:hidden; margin:4px 0 12px;
    border:1px solid var(--grid); }
  .probbar i { display:block; }
  .prow { display:grid; grid-template-columns:70px 1fr 54px; gap:10px; align-items:center;
    font-size:14px; padding:5px 0; }
  .prow .tag { font-weight:800; } .prow .tag.bull { color:var(--green); }
  .prow .tag.base { color:var(--blue); } .prow .tag.bear { color:var(--red); }
  .prow .pv { text-align:right; font-weight:800; }
  .prow .pt { color:var(--ink2); }
  .modal { font-size:14px; margin:8px 0 4px; }
  .modal b { color:var(--ink); }
  .levels { display:grid; grid-template-columns:repeat(3,1fr); gap:12px; margin:6px 0 10px; }
  .lvl { background:var(--page); border-radius:12px; padding:12px 14px; }
  .lvl .k { font-size:11px; text-transform:uppercase; letter-spacing:.04em; color:var(--ink2); }
  .lvl .v { font-size:20px; font-weight:800; margin-top:2px; }
  .lvl.entrada .v { color:var(--ink); } .lvl.inval .v { color:var(--red); }
  .lvl.salida .v { color:var(--green); }
  .wl { list-style:none; margin:2px 0 10px; padding:0; }
  .wl li { padding:7px 0; border-top:1px solid var(--grid); font-size:14px; display:flex;
    justify-content:space-between; gap:10px; }
  .ins-buy { color:var(--green); font-weight:800; } .ins-sell { color:var(--red); font-weight:800; }
  .risk li::before { content:"⚠ "; color:var(--orange); }
  .brief-note { font-size:12px; color:var(--muted); margin-top:6px; }
  .bh.sm { font-size:12px; margin-bottom:8px; }
  .info { cursor:help; color:var(--muted); font-weight:700; margin-left:4px; }
  /* health strip */
  .hstrip { display:flex; flex-wrap:wrap; gap:8px 16px; margin:12px 0 4px; }
  .hdot { display:flex; align-items:center; gap:8px; cursor:help; }
  .hdot .dot { width:14px; height:14px; border-radius:50%; flex:0 0 auto; }
  .hdot .hl { font-size:14px; font-weight:600; }
  /* bell curve */
  svg.bell { display:block; margin:6px 0 2px; overflow:visible; }
  /* catalyst */
  .cat-big { font-size:26px; font-weight:800; letter-spacing:-.02em; margin:2px 0; }
  /* insider net bar */
  .netbar { display:flex; height:24px; border-radius:8px; overflow:hidden;
    border:1px solid var(--grid); margin:4px 0 8px; }
  .netbar i { display:block; } .netbar .buy { background:var(--green); } .netbar .sell { background:var(--red); }
  .netlegend { display:flex; justify-content:space-between; font-size:13px; font-weight:700; }
  .okflag { color:var(--green); font-weight:700; font-size:14px; margin-top:6px;
    background:var(--green-bg); padding:8px 12px; border-radius:10px; display:inline-block; }
  .c-chart h2 { color:#fff; } .c-chart .sub { color:#8b929c; }
  .chart-head { display:flex; align-items:baseline; gap:14px; flex-wrap:wrap; margin:8px 0 4px; }
  .chart-head .px { font-size:34px; font-weight:800; letter-spacing:-.02em; }
  .chart-head .chg { font-size:14px; font-weight:700; }
  .chart-head .chg.up { color:#26d07c; } .chart-head .chg.down { color:#ff5a5f; }
  .chart-head .rng { color:#8b929c; font-size:13px; }
  #tvchart { height:340px; margin-top:10px; }
  .periods { display:flex; gap:8px; margin-top:12px; }
  .periods button { font:inherit; font-size:13px; font-weight:700; padding:6px 16px;
    border:0; border-radius:99px; background:transparent; color:#8b929c; cursor:pointer; }
  .periods button.on { background:#1d2b22; color:#26d07c; }
  .tglegend { display:flex; gap:18px; margin-top:10px; font-size:12.5px; flex-wrap:wrap; }
  .tglegend span { display:inline-flex; align-items:center; gap:7px; color:#aab1bb; }
  .tglegend i { width:18px; border-top:2px dashed; display:inline-block; }
  .card h2 { font-size:16px; font-weight:700; }
  .card .sub { color:var(--muted); font-size:12.5px; margin-top:3px; }
  .hero-num { display:flex; align-items:baseline; gap:12px; margin:16px 0 4px; }
  .hero-num .n { font-size:38px; font-weight:800; letter-spacing:-.03em; }
  .hero-num .u { font-size:13px; color:var(--ink2); }
  .chip { font-size:12.5px; font-weight:700; padding:3px 9px; border-radius:99px; }
  .chip.up { color:var(--green); background:var(--green-bg); }
  .chip.down { color:var(--red); background:var(--red-bg); }
  .bars { display:flex; align-items:flex-end; gap:14px; height:130px; margin-top:18px; }
  .bar { flex:1; display:flex; flex-direction:column; justify-content:flex-end;
    align-items:center; gap:6px; height:100%; }
  .bar .stick { width:100%; max-width:38px; border-radius:8px 8px 4px 4px;
    background:var(--purple); transition:height .5s ease; }
  .bar.last .stick { background:var(--green); }
  .bar .y { font-size:11.5px; color:var(--muted); }
  .bar .v { font-size:11.5px; color:var(--ink2); font-weight:600; }
  .legend { display:flex; gap:16px; margin-top:12px; font-size:12.5px; color:var(--ink2); }
  .legend i { display:inline-block; width:9px; height:9px; border-radius:50%; margin-right:6px; }
  ul.words { list-style:none; margin-top:14px; }
  ul.words li { display:flex; gap:10px; padding:9px 0; font-size:14px; line-height:1.5;
    border-bottom:1px solid var(--grid); }
  ul.words li:last-child { border-bottom:none; }
  ul.words .dot { flex:none; width:8px; height:8px; border-radius:50%; margin-top:7px; }
  .gaugebox { display:flex; align-items:center; gap:6px; flex-direction:column; margin:6px 0 12px; }
  .gauge { width:190px; height:110px; }
  .gauge .track { stroke:var(--grid); } .gauge .arc { stroke:var(--green); }
  .gauge-num { font-size:34px; font-weight:800; margin-top:-58px; }
  .gauge-of { font-size:12px; color:var(--muted); margin-bottom:16px; }
  .srow { display:grid; grid-template-columns:150px 1fr 52px; gap:10px;
    align-items:center; margin-bottom:10px; font-size:13px; }
  .srow .nm { color:var(--ink2); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .srow .v { text-align:right; font-weight:700; font-variant-numeric:tabular-nums; }
  .track2 { height:9px; background:var(--grid); border-radius:6px; overflow:hidden; }
  .fill2 { height:100%; border-radius:6px; transition:width .5s ease; }
  .srow.ns .nm,.srow.ns .v { color:var(--muted); font-weight:500; }
  .price-now { display:flex; align-items:baseline; gap:10px; margin:14px 0 4px; }
  .price-now .n { font-size:30px; font-weight:800; }
  .price-now .u { font-size:12.5px; color:var(--muted); }
  .trow { display:grid; grid-template-columns:86px 1fr 96px 90px; gap:12px;
    align-items:center; padding:13px 0; border-bottom:1px solid var(--grid); }
  .trow:last-of-type { border-bottom:none; }
  .tag { font-size:12.5px; font-weight:700; padding:4px 0; border-radius:10px; text-align:center; }
  .tag.bull { color:var(--green); background:var(--green-bg); }
  .tag.base { color:var(--blue); background:var(--blue-bg); }
  .tag.bear { color:var(--red); background:var(--red-bg); }
  .trow .as { font-size:12px; color:var(--muted); line-height:1.45; }
  .trow .tp { font-size:19px; font-weight:800; text-align:right; font-variant-numeric:tabular-nums; }
  .trow .up { text-align:right; }
  .range { position:relative; height:54px; margin:18px 6px 2px; }
  .range .line { position:absolute; top:24px; left:0; right:0; height:8px;
    border-radius:6px; background:linear-gradient(90deg, var(--red-bg), var(--grid), var(--green-bg)); }
  .range .pt { position:absolute; top:20px; width:16px; height:16px; border-radius:50%;
    border:3px solid #fff; box-shadow:0 1px 4px rgba(0,0,0,.25); transform:translateX(-50%); }
  .range .lb { position:absolute; top:0; font-size:11px; font-weight:700; transform:translateX(-50%); white-space:nowrap; }
  .range .lb.below { top:auto; bottom:0; font-weight:600; color:var(--ink2); }
  .foot { margin-top:22px; color:var(--muted); font-size:12px; line-height:1.6; max-width:760px; }
  .spin { display:inline-block; width:14px; height:14px; border:2px solid var(--grid);
    border-top-color:var(--purple); border-radius:50%; animation:r .7s linear infinite;
    vertical-align:-2px; margin-right:7px; }
  @keyframes r { to { transform:rotate(360deg); } }
  /* --- Estados de la app: landing (bienvenida centrada) vs results --- */
  .hero { transition: all .38s cubic-bezier(.4,0,.2,1); }
  .subtitle { font-size:17px; color:var(--ink2); margin:2px 0 0; letter-spacing:-.01em; }
  #brand { cursor:default; }
  /* Landing: bloque de bienvenida centrado en la ventana */
  .app.landing { min-height:calc(100vh - 96px); display:flex; flex-direction:column;
    justify-content:center; }
  .app.landing .hero { text-align:center; }
  .app.landing .kicker { text-align:center; margin-bottom:10px; }
  .app.landing #brand { font-size:44px; margin-bottom:2px; letter-spacing:-.03em; }
  .app.landing .subtitle { font-size:19px; margin-bottom:8px; }
  .app.landing .topbar { margin:26px auto 0; justify-content:center; }
  .app.landing .foot { display:none; }
  /* Results: encabezado colapsado a una barra superior compacta */
  .app.results .kicker, .app.results .subtitle { display:none; }
  .app.results .hero { display:flex; align-items:center; gap:20px; margin-bottom:6px; }
  .app.results #brand { font-size:20px; margin-bottom:0; white-space:nowrap; cursor:pointer;
    color:var(--purple); }
  .app.results #brand:hover { filter:brightness(1.1); }
  .app.results .topbar { flex:1; margin:0; }
  /* --- Tarjeta de loading: checklist de etapas del análisis --- */
  .loadcard h2 { font-size:18px; }
  ul.stages { list-style:none; margin:18px 0 6px; }
  ul.stages li { display:flex; align-items:center; gap:12px; padding:9px 0;
    font-size:14.5px; color:var(--muted); transition:color .3s; }
  ul.stages li .ic { flex:none; width:20px; height:20px; border-radius:50%;
    border:2px solid var(--grid); display:inline-flex; align-items:center;
    justify-content:center; transition:background .3s,border-color .3s; }
  ul.stages li.active { color:var(--ink); font-weight:600; }
  ul.stages li.active .ic { border-color:var(--purple); border-top-color:transparent;
    animation:r .7s linear infinite; }
  ul.stages li.done { color:var(--ink2); }
  ul.stages li.done .ic { background:var(--green); border-color:var(--green); }
  ul.stages li.done .ic::after { content:'✓'; color:#fff; font-size:12px; font-weight:800; }
  .loadbar { height:10px; background:var(--grid); border-radius:6px; overflow:hidden; margin-top:14px; }
  .loadbar i { display:block; height:100%; width:0%; border-radius:6px;
    background:linear-gradient(90deg,var(--purple),var(--green)); transition:width .6s ease; }
  .loadpct { text-align:right; font-size:12.5px; color:var(--muted); margin-top:6px;
    font-variant-numeric:tabular-nums; font-weight:600; }
</style></head><body><div class="app landing" id="app"><div class="wrap">
  <button id="langBtn" class="langtoggle" onclick="setLang(LANG==='en'?'es':'en')">ES</button>
  <header class="hero">
    <div class="kicker">Warren Buffett Jr · Analysis Engine · Live SEC EDGAR</div>
    <h1 id="brand">Welcome to Warren Buffett Jr</h1>
    <p class="subtitle">Your Financial Specialist</p>
    <div class="topbar">
      <div class="searchbox">
        <input id="q" placeholder="Type a ticker or name — e.g. NFLX, Disney, Coca-Cola…"
          autocomplete="off" autofocus>
        <div class="sugg" id="sugg"></div>
      </div>
      <button class="discover" id="discoverBtn">✨ Discover companies</button>
    </div>
  </header>
  <div id="status"></div>
  <div class="card" id="screenCard" style="display:none;margin-top:22px"></div>
  <div class="card loadcard" id="loadCard" style="display:none;margin-top:22px"></div>
  <div class="grid" id="grid">
    <div class="card c-hero" id="heroCard"></div>
    <div class="card c-words" id="wordsCard"></div>
    <div class="card c-chart" id="chartCard"></div>
    <div class="card c-score" id="scoreCard"></div>
    <div class="card c-target" id="targetCard"></div>
    <div class="card c-brief" id="briefCard"></div>
    <div class="card c-levels" id="levelsCard" style="grid-column:span 12"></div>
    <div class="card c-agg" id="aggCard" style="grid-column:span 12"></div>
    <div class="card c-report" id="reportCard" style="grid-column:span 12"></div>
  </div>
  <div class="foot" id="foot"><b>Note:</b> Quick score from official SEC (EDGAR) data.
  No evidence, no number: pending categories show as N/S, never fabricated.
  Targets are reference ranges with declared assumptions — research classification,
  not investment advice.</div>
</div></div>
<script>
const q = document.getElementById('q'), sugg = document.getElementById('sugg'),
      status = document.getElementById('status'), grid = document.getElementById('grid'),
      app = document.getElementById('app'), brand = document.getElementById('brand');
let timer = null;
let CURRENT_TICKER = null;

// --- i18n (UI chrome). Dynamic analysis text comes localized from the server. ---
const I18N = {
  en: {
    kicker: 'Warren Buffett Jr · Analysis Engine · Live SEC EDGAR',
    brand_landing: 'Welcome to Warren Buffett Jr', subtitle: 'Your Financial Specialist',
    placeholder: 'Type a ticker or name — e.g. NFLX, Disney, Coca-Cola…',
    discover_btn: '✨ Discover companies',
    foot: '<b>Note:</b> Quick score from official SEC (EDGAR) data. No evidence, no number: pending categories show as N/S, never fabricated. Targets are reference ranges with declared assumptions — research classification, not investment advice.',
    hero_fy: 'Fiscal year ended', hero_rev: 'annual revenue',
    hero_legend1: 'Revenue by year (billions $)', hero_legend2: 'Latest year',
    words_h: 'In plain words', words_sub: 'What the numbers say, no jargon',
    score_h: 'Agent scorecard', score_percat: '1–10 per category', score_evpts: 'evidence points',
    score_of10: 'of 10 (quick)', cls_favors: 'FAVORS', cls_neutral: 'NEUTRAL', cls_avoid: 'AVOID', scn_base: 'Base', seg_below: 'below', seg_above: 'above',
    target_scenarios: 'Scenarios with declared assumptions (never a single line)',
    target_current: 'current price', target_today: 'Today', target_cannot: 'Cannot compute',
    tgt_price: 'Price target', tgt_bbb: 'Bull / Base / Bear',
    brief_means: 'What the score means', brief_nodata: 'no data',
    // Panel visual (campana, tira de salud, barra neta de insiders). Victor los
    // trae en español fijo; aquí pasan por TR para que el toggle EN siga vivo.
    brief_verdict: 'The verdict',
    brief_dots_hint: 'Hover each dot to see its score.',
    brief_where12: 'Where the price could be in 12 months',
    brief_where: 'Where the price could be',
    brief_most_likely: 'Most likely:',
    brief_bell_note: 'Dotted curve = forward estimate, not history. Area = how likely each price is.',
    brief_breaks: 'Breaks', brief_target: 'Target',
    brief_next_report: 'Next earnings report',
    brief_buys: 'Buys', brief_sells: 'Sells', brief_net: 'Net:',
    brief_side_buy: 'buying', brief_side_sell: 'selling',
    brief_no_ins_flow: 'No open-market insider buys or sells.',
    brief_no_risk_flag: 'No material risk flags',
    brief_prob12: 'Price probability (12 months)', brief_prob: 'Price probability',
    brief_reach: 'reach', brief_modal: 'Most likely outcome:', brief_notcomp: 'Not computable:',
    brief_entry: 'Approx. entry', brief_inval: 'Invalidation', brief_exit: 'Exit (Base)',
    brief_lvl_unavail: 'Levels unavailable:', brief_next_earn: 'Next earnings', brief_est_eps: 'Est. EPS',
    brief_no_earn: 'No upcoming earnings date available.', brief_buy: 'BUY', brief_sell: 'SELL',
    brief_no_ins: 'No insider transactions above $1M.', brief_no_risk: 'No material risk flags in the data.',
    brief_watch: 'Key things to watch', brief_lvls: 'Price levels', brief_cats: 'Upcoming catalysts',
    brief_risks: 'Risks / thesis breakers', brief_h: 'Investor read',
    brief_sub: 'What the score means, price probability, and what to watch',
    load_h: 'Analyzing', load_sub: 'Reading official SEC data and computing the score',
    load1: 'Reading SEC EDGAR filings', load2: 'Analyzing business and financials',
    load3: 'Assessing risk and valuation', load4: 'Computing price target', load5: 'Synthesizing score',
    chart_h: 'Chart & targets', chart_nohist: 'No price history available for',
    chart_sub: 'Latest year · 12-month targets with declared assumptions',
    retry: 'Retry',
    disc_scan: 'Scanning the whole SEC universe: mid-cap ($0.8B–$30B), profitable and growing companies… the first run takes 1–2 min.',
    disc_h: 'Discovered companies — top', disc_by: 'by score',
    disc_sub: 'Pre-filter: revenue $0.8B–$30B · net margin > 8% · growth > 5% · SEC EDGAR data · research classification, not advice. Tap a ticker for the full analysis.',
    th_company: 'Company', th_rev: 'Revenue', th_growth: 'Growth', th_margin: 'Margin', th_target: 'Base target', th_score: 'Score',
    lvl_h: 'Price levels — entry & exit', lvl_sub: 'Real support/resistance zones from price action, moving averages and anchored VWAP. No AI — pure price structure.',
    lvl_loading: 'Computing price levels…', lvl_price: 'Current price', lvl_support: 'Support (buy zones)',
    lvl_resist: 'Resistance (exit zones)', lvl_ma: 'Moving averages', lvl_avwap: 'Anchored VWAP',
    lvl_touches: 'touches', lvl_away: 'away', lvl_none: 'No levels available for this ticker.',
    agg_h: 'Cerebro profile — strict methodology', agg_run: 'Run strict profile',
    agg_sub: 'The full Ruta 2030 scoring: veto gates, mandatory overrides and contradiction rules over the 6 specialists. Stricter than the scorecard above by design.',
    agg_loading: 'Running gates and overrides…', agg_raw: 'raw', agg_conf: 'confidence',
    agg_cats: 'Categories', agg_ovr: 'Overrides applied', agg_gates: 'Failed gates',
    agg_contra: 'Contradictions', agg_cov: 'coverage',
    agg_ns: 'N/S — no evidence', agg_quick: 'quick', agg_na: 'not assessable',
    agg_noconc: 'Not enough data to reach an investment conclusion',
    agg_noconc_sub: '{pts} of the 100 points were never in play for lack of evidence ({cats}). '
      + 'The score below is what the available evidence supports — read it as incomplete, not as a verdict.',
    agg_why: 'quick score → strict score. They differ because they measure different metric sets: the quick score uses a few always-available metrics; the strict one needs ~30 per category. A dimension whose data falls below 70% coverage earns none of its points, so a strict score can be low because a dimension was never measurable — the “not assessable” figure beside each row says how many points that is. A category shows N/S only when every one of its dimensions is unmeasurable.',
    agg_note: 'A low score here usually means missing data coverage, not a judgment that the company is bad — check the coverage column.',
    rep_h: 'Full research report', rep_run: 'Generate full report',
    rep_sub: 'The complete auditable document: profile, category scorecard, 7-sentence executive thesis, institutional holders, insider activity over $1M, key levels, scenarios, thesis killers, charts and audit trail.',
    rep_loading: 'Building the full report…', rep_err: 'Could not build the report:', rep_dl: 'Download .md',
    agg_err: 'Could not compute the profile:',
    lvl_err: 'Could not compute levels:',
    err_analyze: 'Could not analyze', err_discover: 'Could not complete discovery:',
  },
  es: {
    kicker: 'Warren Buffett Jr · Motor de Análisis · SEC EDGAR en vivo',
    brand_landing: 'Bienvenido a Warren Buffett Jr', subtitle: 'Tu Especialista Financiero',
    placeholder: 'Escribe un ticker o nombre — ej. NFLX, Disney, Coca-Cola…',
    discover_btn: '✨ Descubrir empresas',
    foot: '<b>Nota:</b> Puntaje rápido con datos oficiales de la SEC (EDGAR). Sin evidencia no hay número: las categorías pendientes se muestran como N/S, nunca se inventan. Los targets son rangos de referencia con supuestos declarados — clasificación de research, no es asesoría de inversión.',
    hero_fy: 'Año fiscal terminado', hero_rev: 'ventas anuales',
    hero_legend1: 'Ventas por año (miles de millones $)', hero_legend2: 'Último año',
    words_h: 'En palabras simples', words_sub: 'Qué dicen los números, sin jerga',
    score_h: 'Puntaje de los agentes', score_percat: '1–10 por categoría', score_evpts: 'puntos de evidencia',
    score_of10: 'de 10 (rápido)', cls_favors: 'FAVORECE', cls_neutral: 'NEUTRAL', cls_avoid: 'EVITAR', scn_base: 'Medio', seg_below: 'por debajo de', seg_above: 'por encima de',
    target_scenarios: 'Escenarios con supuestos declarados (nunca una sola línea)',
    target_current: 'precio actual', target_today: 'Hoy', target_cannot: 'No se puede calcular',
    tgt_price: 'Precio objetivo', tgt_bbb: 'Bull / Medio / Bear',
    brief_means: 'Qué significa el puntaje', brief_nodata: 'sin dato',
    // Textos de Victor, tal cual los escribió él.
    brief_verdict: 'El veredicto',
    brief_dots_hint: 'Pasa el mouse por cada punto para ver su nota.',
    brief_where12: 'Dónde podría estar el precio en 12 meses',
    brief_where: 'Dónde podría estar el precio',
    brief_most_likely: 'Lo más probable:',
    brief_bell_note: 'Curva punteada = estimación a futuro, no historia. Área = qué tan probable es cada precio.',
    brief_breaks: 'Se rompe', brief_target: 'Objetivo',
    brief_next_report: 'Próximo reporte de resultados',
    brief_buys: 'Compras', brief_sells: 'Ventas', brief_net: 'Neto:',
    brief_side_buy: 'compra', brief_side_sell: 'venta',
    brief_no_ins_flow: 'Sin compras/ventas de insiders en el mercado abierto.',
    brief_no_risk_flag: 'Sin banderas de riesgo materiales',
    brief_prob12: 'Probabilidad hacia el precio (12 meses)', brief_prob: 'Probabilidad hacia el precio',
    brief_reach: 'llegar a', brief_modal: 'Resultado más probable:', brief_notcomp: 'No calculable:',
    brief_entry: 'Entrada aprox.', brief_inval: 'Invalidación', brief_exit: 'Salida (Medio)',
    brief_lvl_unavail: 'Niveles no disponibles:', brief_next_earn: 'Próximo earnings', brief_est_eps: 'EPS estimado',
    brief_no_earn: 'Sin fecha de earnings próxima disponible.', brief_buy: 'COMPRA', brief_sell: 'VENTA',
    brief_no_ins: 'Sin transacciones de insiders sobre $1M.', brief_no_risk: 'Sin banderas de riesgo materiales en los datos.',
    brief_watch: 'Puntos clave a vigilar', brief_lvls: 'Niveles de precio', brief_cats: 'Próximos catalizadores',
    brief_risks: 'Riesgos / rompe-tesis', brief_h: 'Lectura para el inversionista',
    brief_sub: 'Qué significa el puntaje, probabilidad hacia el precio y qué observar',
    load_h: 'Analizando', load_sub: 'Leyendo datos oficiales de la SEC y calculando el puntaje',
    load1: 'Leyendo reportes SEC EDGAR', load2: 'Analizando negocio y finanzas',
    load3: 'Evaluando riesgo y valuación', load4: 'Calculando precio objetivo', load5: 'Sintetizando puntaje',
    chart_h: 'Gráfica y targets', chart_nohist: 'Sin historial de precio disponible para',
    chart_sub: 'Último año · targets a 12 meses con supuestos declarados',
    retry: 'Reintentar',
    disc_scan: 'Escaneando todo el universo de la SEC: empresas medianas ($0.8B–$30B), rentables y creciendo… la primera vez tarda 1–2 min.',
    disc_h: 'Empresas descubiertas — top', disc_by: 'por puntaje',
    disc_sub: 'Prefiltro: ventas $0.8B–$30B · margen neto > 8% · crecimiento > 5% · datos SEC EDGAR · clasificación de research, no asesoría. Toca un ticker para el análisis completo.',
    th_company: 'Empresa', th_rev: 'Ventas', th_growth: 'Crec.', th_margin: 'Margen', th_target: 'Target medio', th_score: 'Puntaje',
    lvl_h: 'Niveles de precio — entrada y salida', lvl_sub: 'Zonas reales de soporte/resistencia del comportamiento del precio, medias móviles y VWAP anclado. Sin IA — pura estructura de precio.',
    lvl_loading: 'Calculando niveles de precio…', lvl_price: 'Precio actual', lvl_support: 'Soporte (zonas de compra)',
    lvl_resist: 'Resistencia (zonas de salida)', lvl_ma: 'Medias móviles', lvl_avwap: 'VWAP anclado',
    lvl_touches: 'toques', lvl_away: 'de distancia', lvl_none: 'Sin niveles disponibles para este ticker.',
    agg_h: 'Perfil del Cerebro — metodología estricta', agg_run: 'Correr perfil estricto',
    agg_sub: 'El puntaje completo de Ruta 2030: gates de veto, overrides obligatorios y reglas de contradicción sobre los 6 especialistas. Más estricto que el scorecard de arriba, por diseño.',
    agg_loading: 'Corriendo gates y overrides…', agg_raw: 'crudo', agg_conf: 'confianza',
    agg_cats: 'Categorías', agg_ovr: 'Overrides aplicados', agg_gates: 'Gates fallados',
    agg_contra: 'Contradicciones', agg_cov: 'cobertura',
    agg_ns: 'N/S — sin evidencia', agg_quick: 'rápido', agg_na: 'no evaluable',
    agg_noconc: 'No tengo data suficiente para llegar a una conclusión de inversión',
    agg_noconc_sub: '{pts} de los 100 puntos nunca entraron en juego por falta de evidencia ({cats}). '
      + 'El puntaje de abajo es lo que la evidencia disponible sostiene — léelo como incompleto, no como veredicto.',
    agg_why: 'puntaje rápido → puntaje estricto. Difieren porque miden conjuntos de métricas distintos: el rápido usa pocas métricas siempre disponibles; el estricto necesita ~30 por categoría. Una dimensión cuya cobertura cae bajo 70% no gana ninguno de sus puntos, así que un puntaje estricto puede ser bajo porque una dimensión nunca fue medible — la cifra “no evaluable” junto a cada fila dice cuántos puntos son. Una categoría muestra N/S solo cuando TODAS sus dimensiones son no medibles.',
    agg_note: 'Un puntaje bajo aquí normalmente significa falta de cobertura de datos, no un juicio de que la empresa sea mala — mira la columna de cobertura.',
    rep_h: 'Reporte completo de research', rep_run: 'Generar reporte completo',
    rep_sub: 'El documento auditable completo: perfil, scorecard por categoría, tesis ejecutiva de 7 frases, tenedores institucionales, actividad de insiders sobre $1M, niveles clave, escenarios, rompe-tesis, gráficas y trail de auditoría.',
    rep_loading: 'Construyendo el reporte completo…', rep_err: 'No pude construir el reporte:', rep_dl: 'Descargar .md',
    agg_err: 'No pude calcular el perfil:',
    lvl_err: 'No pude calcular los niveles:',
    err_analyze: 'No pude analizar', err_discover: 'No pude completar el descubrimiento:',
  },
};
let LANG = localStorage.getItem('wbj_lang') || 'en';
const TR = k => (I18N[LANG] && I18N[LANG][k]) || I18N.en[k] || k;

function applyStaticText() {
  document.documentElement.lang = LANG;
  document.querySelector('.kicker').textContent = TR('kicker');
  document.querySelector('.subtitle').textContent = TR('subtitle');
  q.placeholder = TR('placeholder');
  document.getElementById('discoverBtn').textContent = TR('discover_btn');
  document.getElementById('foot').innerHTML = TR('foot');
  const lb = document.getElementById('langBtn'); if (lb) lb.textContent = LANG === 'en' ? 'ES' : 'EN';
  brand.textContent = app.classList.contains('results') ? 'Warren Buffett Jr' : TR('brand_landing');
}
function setLang(l) {
  LANG = l; localStorage.setItem('wbj_lang', l);
  applyStaticText();
  if (CURRENT_TICKER) run(CURRENT_TICKER);
}

function setMode(mode) {
  const landing = mode === 'landing';
  app.classList.toggle('landing', landing);
  app.classList.toggle('results', !landing);
  brand.textContent = landing ? TR('brand_landing') : 'Warren Buffett Jr';
}

// Clic en el título (en modo resultados) regresa a la bienvenida centrada.
brand.addEventListener('click', () => {
  if (!app.classList.contains('results')) return;
  grid.style.display = 'none'; screenCard.style.display = 'none';
  status.textContent = ''; q.value = ''; sugg.style.display = 'none';
  setMode('landing'); q.focus();
});

// --- Loading: checklist de etapas mientras corre /api/analyze -------------
const LOAD_STAGES = [
  TR('load1'),
  TR('load2'),
  TR('load3'),
  TR('load4'),
  TR('load5'),
];
const LOAD_PCTS = [16, 38, 58, 78, 92];  // el último se sostiene hasta que llega la respuesta
let loadTimer = null;

function startLoading(t) {
  const el = document.getElementById('loadCard');
  el.style.display = 'block';
  el.innerHTML = `<h2>${TR('load_h')} ${t}…</h2>
    <div class="sub">${TR('load_sub')}</div>
    <ul class="stages">${LOAD_STAGES.map((s, i) =>
      `<li id="st${i}" class="pending"><span class="ic"></span><span>${s}</span></li>`).join('')}</ul>
    <div class="loadbar"><i id="loadfill"></i></div>
    <div class="loadpct" id="loadpct">0%</div>`;
  const fill = document.getElementById('loadfill'), pct = document.getElementById('loadpct');
  function setStage(n) {
    for (let i = 0; i < LOAD_STAGES.length; i++) {
      document.getElementById('st' + i).className = i < n ? 'done' : (i === n ? 'active' : 'pending');
    }
    fill.style.width = LOAD_PCTS[n] + '%'; pct.textContent = LOAD_PCTS[n] + '%';
  }
  let cur = 0; setStage(0);
  clearInterval(loadTimer);
  loadTimer = setInterval(() => {
    if (cur < LOAD_STAGES.length - 1) setStage(++cur);
    else clearInterval(loadTimer);  // se queda en la última etapa (~92%) hasta finishLoading
  }, 850);
}

function finishLoading(ok, cb) {
  clearInterval(loadTimer);
  const el = document.getElementById('loadCard');
  if (!ok) { el.style.display = 'none'; if (cb) cb(); return; }
  for (let i = 0; i < LOAD_STAGES.length; i++) {
    const li = document.getElementById('st' + i); if (li) li.className = 'done';
  }
  const fill = document.getElementById('loadfill'), pct = document.getElementById('loadpct');
  if (fill) fill.style.width = '100%'; if (pct) pct.textContent = '100%';
  setTimeout(() => { el.style.display = 'none'; if (cb) cb(); }, 320);
}

q.addEventListener('input', () => {
  clearTimeout(timer);
  timer = setTimeout(async () => {
    const v = q.value.trim();
    if (!v) { sugg.style.display = 'none'; return; }
    const r = await fetch('/api/search?q=' + encodeURIComponent(v));
    const items = await r.json();
    sugg.innerHTML = items.map(i =>
      `<button data-t="${i.ticker}"><b>${i.ticker}</b><span>${i.name}</span></button>`).join('');
    sugg.style.display = items.length ? 'block' : 'none';
  }, 180);
});
q.addEventListener('keydown', e => {
  if (e.key === 'Enter') {
    const first = sugg.querySelector('button');
    run(first ? first.dataset.t : q.value.trim().toUpperCase());
  }
});
sugg.addEventListener('click', e => {
  const b = e.target.closest('button');
  if (b) run(b.dataset.t);
});

const fmtB = x => '$' + (x / 1e9).toFixed(1) + 'B';
const CAT_COLORS = { business:'var(--purple)', financial:'var(--green)',
  risk:'var(--blue)', market:'var(--orange)', technical:'var(--orange)', valuation:'var(--orange)' };
const WORD_COLORS = ['var(--purple)','var(--green)','var(--blue)','var(--orange)','var(--red)','var(--purple)'];

function heroHtml(d) {
  const h = d.history, last = h[h.length - 1];
  const maxRev = Math.max(...h.map(r => r.revenue));
  const yoy = d.metrics.revenue_yoy;
  const chip = typeof yoy === 'number'
    ? `<span class="chip ${yoy >= 0 ? 'up' : 'down'}">${yoy >= 0 ? '▲' : '▼'} ${(Math.abs(yoy) * 100).toFixed(1)}% vs prior year</span>` : '';
  const bars = h.map((r, i) => `
    <div class="bar ${i === h.length - 1 ? 'last' : ''}">
      <span class="v">${(r.revenue / 1e9).toFixed(0)}</span>
      <div class="stick" data-h="${(r.revenue / maxRev * 100).toFixed(0)}" style="height:4%"></div>
      <span class="y">${r.year}</span>
    </div>`).join('');
  return `<h2>${d.entity} · ${d.ticker}</h2>
    <div class="sub">${TR('hero_fy')} ${d.fiscal_year_end} · Form 10-K · SEC EDGAR</div>
    <div class="hero-num"><span class="n">${fmtB(last.revenue)}</span>
      <span class="u">${TR('hero_rev')}</span>${chip}</div>
    <div class="bars">${bars}</div>
    <div class="legend"><span><i style="background:var(--purple)"></i>${TR('hero_legend1')}</span>
      <span><i style="background:var(--green)"></i>${TR('hero_legend2')}</span></div>`;
}

function wordsHtml(d) {
  const lis = d.narrative.map((s, i) =>
    `<li><span class="dot" style="background:${WORD_COLORS[i % WORD_COLORS.length]}"></span><span>${s}</span></li>`).join('');
  return `<h2>${TR('words_h')}</h2>
    <div class="sub">${TR('words_sub')}</div><ul class="words">${lis}</ul>`;
}

function gaugeSvg(score) {
  const frac = Math.max(0, Math.min(1, score / 10));
  const R = 80, C = Math.PI * R;
  return `<svg class="gauge" viewBox="0 0 200 110">
    <path class="track" d="M 20 100 A 80 80 0 0 1 180 100" fill="none" stroke-width="14" stroke-linecap="round"/>
    <path class="arc" d="M 20 100 A 80 80 0 0 1 180 100" fill="none" stroke-width="14"
      stroke-linecap="round" stroke-dasharray="${(frac * C).toFixed(1)} ${C.toFixed(1)}"/>
  </svg>`;
}

function scoreHtml(d) {
  const sc = d.scorecard;
  const rows = sc.categories.map(r => {
    if (r.status === 'scored') {
      return `<div class="srow"><span class="nm">${r.label}</span>
        <div class="track2"><div class="fill2" data-w="${r.score10 * 10}"
          style="width:0%;background:${CAT_COLORS[r.key]}"></div></div>
        <span class="v">${r.score10.toFixed(1)}/10</span></div>`;
    }
    return `<div class="srow ns"><span class="nm" title="${r.reason}">${r.label}</span>
      <div class="track2"></div><span class="v">N/S</span></div>`;
  }).join('');
  const overall = sc.overall_10 === null ? '—' : sc.overall_10.toFixed(1);
  return `<h2>${TR('score_h')}</h2>
    <div class="sub">${TR('score_percat')} · ${sc.evidence_points_covered}/100 ${TR('score_evpts')}</div>
    <div class="gaugebox">${gaugeSvg(sc.overall_10 ?? 0)}
      <div class="gauge-num">${overall}</div><div class="gauge-of">${TR('score_of10')}</div></div>${rows}`;
}

function targetHtml(d) {
  const t = d.targets;
  if (t.status !== 'ok') {
    return `<h2>${TR('tgt_price')} — 12 months</h2>
      <div class="sub">${TR('tgt_bbb')}</div>
      <p style="margin-top:16px;color:var(--ink2);font-size:14px">
      ${TR('target_cannot')}: ${t.reason}.</p>`;
  }
  const order = ['bull', 'base', 'bear'];
  const by = {}; t.scenarios.forEach(s => by[s.key] = s);
  const rows = order.map(k => {
    const s = by[k];
    const cls = k === 'base' ? 'base' : k;
    const chip = `<span class="chip ${s.upside >= 0 ? 'up' : 'down'}">${s.upside >= 0 ? '+' : ''}${(s.upside * 100).toFixed(0)}%</span>`;
    return `<div class="trow"><span class="tag ${cls}">${s.label}</span>
      <span class="as">${s.assumptions}</span>
      <span class="tp">$${s.target.toLocaleString('en-US', {minimumFractionDigits: 2})}</span>
      <span class="up">${chip}</span></div>`;
  }).join('');
  const pts = [ {v: t.price, lb: TR('target_today'), c: 'var(--ink)', below: true},
    {v: by.bear.target, lb: 'Bear', c: 'var(--red)'},
    {v: by.base.target, lb: 'Base', c: 'var(--blue)'},
    {v: by.bull.target, lb: 'Bull', c: 'var(--green)'} ];
  const lo = Math.min(...pts.map(p => p.v)) * 0.97, hi = Math.max(...pts.map(p => p.v)) * 1.03;
  const pos = v => ((v - lo) / (hi - lo) * 100).toFixed(1) + '%';
  const marks = pts.map(p => `
    <span class="lb ${p.below ? 'below' : ''}" style="left:${pos(p.v)};color:${p.c}">${p.lb} $${p.v.toFixed(0)}</span>
    <span class="pt" style="left:${pos(p.v)};background:${p.c}"></span>`).join('');
  return `<h2>${TR('tgt_price')} — ${t.horizon}</h2>
    <div class="sub">${TR('target_scenarios')}</div>
    <div class="price-now"><span class="n">$${t.price.toLocaleString('en-US', {minimumFractionDigits: 2})}</span>
      <span class="u">${TR('target_current')} · EPS $${t.eps} · P/E ${t.pe_now}x</span></div>
    <div class="range"><div class="line"></div>${marks}</div>${rows}
    <div class="sub" style="margin-top:12px">${t.disclaimer}</div>`;
}

function money(x, dec) {
  return '$' + Number(x).toLocaleString('en-US', {minimumFractionDigits: dec || 0, maximumFractionDigits: dec || 0});
}

const fmtUSD = x => {
  const a = Math.abs(x);
  if (a >= 1e9) return '$' + (x / 1e9).toFixed(1) + 'B';
  if (a >= 1e6) return '$' + (x / 1e6).toFixed(1) + 'M';
  if (a >= 1e3) return '$' + (x / 1e3).toFixed(0) + 'K';
  return '$' + x.toFixed(0);
};
const scoreColor = s => s == null ? 'var(--muted)' :
  s >= 6.5 ? 'var(--green)' : s >= 3.5 ? 'var(--orange)' : 'var(--red)';
const shortLabel = l => l.replace(' (quick)', '');

// Lognormal "bell" of where the price could land in 12 months (dashed = it's
// a projection, not observed history — Cerebro rule 3). Neutral single-hue
// fill; today + each target as labelled status-colored ticks.
function bellSvg(price, sigma, targets) {
  const W = 620, H = 200, PADX = 16, PADT = 22, PADB = 50, N = 140;
  const lo = price * Math.exp(-3 * sigma), hi = price * Math.exp(3 * sigma);
  const X = x => PADX + (Math.log(x / lo) / Math.log(hi / lo)) * (W - 2 * PADX);
  const clamp = x => Math.max(lo, Math.min(hi, x));
  let ymax = 0; const pts = [];
  for (let i = 0; i <= N; i++) {
    const x = lo * Math.pow(hi / lo, i / N);
    const z = Math.log(x / price) / sigma;
    const dens = Math.exp(-z * z / 2) / x;
    pts.push([x, dens]); if (dens > ymax) ymax = dens;
  }
  const Y = dRatio => PADT + (1 - dRatio) * (H - PADT - PADB);
  const line = pts.map((p, i) => `${i ? 'L' : 'M'}${X(p[0]).toFixed(1)},${Y(p[1] / ymax).toFixed(1)}`).join(' ');
  const area = `M${X(lo).toFixed(1)},${H - PADB} ` +
    pts.map(p => `L${X(p[0]).toFixed(1)},${Y(p[1] / ymax).toFixed(1)}`).join(' ') +
    ` L${X(hi).toFixed(1)},${H - PADB} Z`;

  // "Hoy" label above the chart; target labels below, de-cluttered into
  // stacked rows so near-equal targets never overlap (Cerebro rule: look at it).
  const col = {bull: 'var(--green)', base: 'var(--blue)', bear: 'var(--red)'};
  const by = {}; targets.forEach(t => by[t.key] = t);
  const vlines = ['bear', 'base', 'bull'].filter(k => by[k]).map(k => {
    const px = X(clamp(by[k].target));
    return `<line x1="${px}" y1="${PADT}" x2="${px}" y2="${H - PADB}" stroke="${col[k]}"
      stroke-width="1.5" stroke-dasharray="3 3" />`;
  }).join('');
  const bottom = ['bear', 'base', 'bull'].filter(k => by[k])
    .map(k => ({x: X(clamp(by[k].target)), color: col[k],
                text: `${by[k].label} ${(by[k].prob_reach * 100).toFixed(0)}%`}))
    .sort((a, b) => a.x - b.x);
  const rowLastX = [];
  bottom.forEach(it => {
    let r = 0;
    while (r < rowLastX.length && it.x - rowLastX[r] < 60) r++;
    it.row = r; rowLastX[r] = it.x;
  });
  const bottomLabels = bottom.map(it =>
    `<text x="${it.x.toFixed(1)}" y="${(H - PADB + 16 + it.row * 15).toFixed(1)}"
      text-anchor="middle" font-size="12" font-weight="800" fill="${it.color}">${it.text}</text>`).join('');
  const hx = X(clamp(price));
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" preserveAspectRatio="xMidYMid meet" class="bell">
    <path d="${area}" fill="var(--purple-bg)" />
    <path d="${line}" fill="none" stroke="var(--purple)" stroke-width="2.5" stroke-dasharray="6 4" />
    <line x1="${hx}" y1="${PADT - 8}" x2="${hx}" y2="${H - PADB}" stroke="var(--ink)" stroke-width="2.5" />
    <text x="${hx.toFixed(1)}" y="${PADT - 11}" text-anchor="middle" font-size="12"
      font-weight="800" fill="var(--ink)">Hoy ${fmtUSD(price)}</text>
    ${vlines}${bottomLabels}
  </svg>`;
}

function briefHtml(d) {
  const b = d.brief;
  if (!b) return '';
  const it = b.interpretation, pr = b.probability, w = b.watch;

  // 1) Veredicto + tira de salud (color por categoría; /10 en hover)
  const cls = it.classification || 'neutral';
  const revisit = it.revisit ? `<div class="revisit">↻ ${it.revisit}</div>` : '';
  const dots = (it.categories || []).map(c => {
    const tip = c.score10 == null ? `${shortLabel(c.label)}: sin datos` :
      `${shortLabel(c.label)}: ${c.score10}/10 · ${c.meaning}`;
    return `<div class="hdot" title="${tip}">
      <span class="dot" style="background:${scoreColor(c.score10)}"></span>
      <span class="hl">${shortLabel(c.label)}</span></div>`;
  }).join('');
  const interpCol = `<div class="brief-col">
    <div class="bh">${TR('brief_verdict')}</div>
    <span class="classpill ${cls}">${cls ? TR('cls_'+cls) : TR('brief_nodata').toUpperCase()}</span>
    <div class="revisit">${it.classification_label}</div>${revisit}
    <div class="hstrip">${dots}</div>
    <div class="brief-note">${TR('brief_dots_hint')}</div></div>`;

  // 2) Probabilidad → campana
  let probCol;
  if (pr.status === 'ok') {
    probCol = `<div class="brief-col">
      <div class="bh">${TR('brief_where12')}
        <span class="info" title="${pr.assumptions}">ⓘ</span></div>
      <div class="modal">${TR('brief_most_likely')} <b>${pr.modal_zone}</b> (${(pr.modal_prob * 100).toFixed(0)}%)</div>
      ${bellSvg(pr.price, pr.volatility, pr.targets)}
      <div class="brief-note">${TR('brief_bell_note')}</div></div>`;
  } else {
    probCol = `<div class="brief-col"><div class="bh">${TR('brief_where')}</div>
      <p style="color:var(--ink2);font-size:14px">${TR('brief_notcomp')} ${pr.reason}.</p></div>`;
  }

  // 3) Puntos clave a vigilar
  const lv = w.levels;
  const levelsBlock = lv.status === 'ok' ? `<div class="levels">
      <div class="lvl entrada"><div class="k">${TR('brief_entry')}</div><div class="v">${money(lv.entrada, 0)}</div></div>
      <div class="lvl inval"><div class="k">${TR('brief_breaks')}</div><div class="v">${lv.invalidacion != null ? money(lv.invalidacion, 0) : '—'}</div></div>
      <div class="lvl salida"><div class="k">${TR('brief_target')}</div><div class="v">${lv.salida_base != null ? money(lv.salida_base, 0) : '—'}</div></div>
    </div><div class="brief-note">${lv.nota}</div>` :
    `<p style="color:var(--ink2);font-size:14px">${TR('brief_lvl_unavail')} ${lv.reason}.</p>`;

  const ne = w.catalysts && w.catalysts.next_earnings;
  const catBlock = ne ? `<div class="cat-big">${ne.date}</div>
      <div class="brief-note">${TR('brief_next_report')}${ne.eps_est != null ? ` · ${TR('brief_est_eps')} $${ne.eps_est}` : ''}</div>` :
    `<div class="brief-note">${TR('brief_no_earn')}</div>`;

  // Insiders → barra neta compra vs venta
  const fl = w.insiders_flow || {buy_usd: 0, sell_usd: 0, net_usd: 0};
  const tot = fl.buy_usd + fl.sell_usd;
  let insBlock;
  if (tot > 0) {
    const bw = (fl.buy_usd / tot * 100).toFixed(1), sw = (fl.sell_usd / tot * 100).toFixed(1);
    const netSide = fl.net_usd >= 0 ? TR('brief_side_buy') : TR('brief_side_sell');
    insBlock = `<div class="netbar">
        <i class="buy" style="width:${bw}%" title="${TR('brief_buys')} ${fmtUSD(fl.buy_usd)}"></i>
        <i class="sell" style="width:${sw}%" title="${TR('brief_sells')} ${fmtUSD(fl.sell_usd)}"></i></div>
      <div class="netlegend"><span class="ins-buy">▲ ${TR('brief_buys').toLowerCase()} ${fmtUSD(fl.buy_usd)}</span>
        <span class="ins-sell">${TR('brief_sells').toLowerCase()} ${fmtUSD(fl.sell_usd)} ▼</span></div>
      <div class="brief-note">${TR('brief_net')} <b class="${fl.net_usd >= 0 ? 'ins-buy' : 'ins-sell'}">${netSide} ${fmtUSD(Math.abs(fl.net_usd))}</b> (Forms 4)</div>`;
  } else {
    insBlock = `<div class="brief-note">${TR('brief_no_ins_flow')}</div>`;
  }

  const risks = w.risks || [];
  const riskBlock = risks.length ? `<ul class="wl risk">${risks.map(r => `<li><span>${r}</span></li>`).join('')}</ul>` :
    `<div class="okflag">✓ ${TR('brief_no_risk_flag')}</div>`;

  const watchCol = `<div class="brief-col full">
    <div class="bh">${TR('brief_watch')}</div>
    <div class="brief-grid">
      <div class="brief-col"><div class="bh sm">${TR('brief_lvls')}</div>${levelsBlock}</div>
      <div class="brief-col"><div class="bh sm">${TR('brief_cats')}</div>${catBlock}</div>
      <div class="brief-col"><div class="bh sm">Insiders (Forms 4)</div>${insBlock}</div>
      <div class="brief-col"><div class="bh sm">${TR('brief_risks')}</div>${riskBlock}</div>
    </div></div>`;

  return `<h2>${TR('brief_h')}</h2>
    <div class="sub">${TR('brief_sub')}</div>
    <div class="brief-grid" style="margin-top:16px">${interpCol}${probCol}${watchCol}</div>`;
}

// --- Gráfica SVG propia (cero dependencias externas) ---------------------
let tvData = [], tvTargets = null;
const PERIODS = { '1M': 21, '3M': 63, '6M': 126, '1A': 9999 };

function renderChart(d) {
  const el = document.getElementById('chartCard');
  tvData = d.chart || [];
  tvTargets = d.targets && d.targets.status === 'ok' ? d.targets : null;
  if (!tvData.length) {
    el.innerHTML = `<h2>${TR('chart_h')}</h2>
      <div class="sub">${TR('chart_nohist')} ${d.ticker}.</div>`;
    return;
  }
  el.innerHTML = `<h2>${d.ticker} — price & targets</h2>
    <div class="sub">${TR('chart_sub')}</div>
    <div class="chart-head" id="chartHead"></div>
    <div id="tvchart"></div>
    <div class="periods" id="periods">
      ${Object.keys(PERIODS).map(p =>
        `<button data-p="${p}" class="${p === '1A' ? 'on' : ''}">${p}</button>`).join('')}
    </div>`;
  document.getElementById('periods').addEventListener('click', e => {
    const b = e.target.closest('button'); if (!b) return;
    document.querySelectorAll('.periods button').forEach(x => x.classList.toggle('on', x === b));
    setPeriod(b.dataset.p);
  });
  setPeriod('1A');
}

function setPeriod(p) {
  const slice = tvData.slice(-PERIODS[p]);
  const W = 1000, H = 340, PADL = 14, PADR = 150, PADT = 18, PADB = 30;
  const vals = slice.map(r => r.value);
  const tg = tvTargets ? tvTargets.scenarios.map(s => s.target) : [];
  let lo = Math.min(...vals, ...(tg.length ? tg : vals));
  let hi = Math.max(...vals, ...(tg.length ? tg : vals));
  const pad = (hi - lo) * 0.07 || 1; lo -= pad; hi += pad;
  const X = i => PADL + i / (slice.length - 1) * (W - PADL - PADR);
  const Y = v => PADT + (1 - (v - lo) / (hi - lo)) * (H - PADT - PADB);

  const pts = slice.map((r, i) => `${X(i).toFixed(1)},${Y(r.value).toFixed(1)}`).join(' ');
  const area = `M ${X(0).toFixed(1)},${(H - PADB)} L ${pts.replaceAll(' ', ' L ')} L ${X(slice.length - 1).toFixed(1)},${H - PADB} Z`;

  // grid + eje de fechas
  const grid = [0.25, 0.5, 0.75].map(f => {
    const y = PADT + f * (H - PADT - PADB);
    return `<line x1="${PADL}" x2="${W - PADR + 60}" y1="${y}" y2="${y}"
      stroke="rgba(139,146,156,.14)" stroke-width="1"/>`;
  }).join('');
  const dates = [0, Math.floor(slice.length / 2), slice.length - 1].map(i =>
    `<text x="${X(i)}" y="${H - 8}" fill="#8b929c" font-size="11.5"
      text-anchor="${i === 0 ? 'start' : i === slice.length - 1 ? 'end' : 'middle'}">${slice[i].time}</text>`).join('');

  // líneas de target punteadas con etiqueta tipo píldora (como la referencia)
  let tlines = '';
  if (tvTargets) {
    const by = {}; tvTargets.scenarios.forEach(s => by[s.key] = s);
    const defs = [
      ['bull', '#26d07c', 'rgba(38,208,124,.14)', 'Bull'],
      ['base', '#f5a623', 'rgba(245,166,35,.14)', 'Medio'],
      ['bear', '#ff5a5f', 'rgba(255,90,95,.14)', 'Bear'],
    ];
    tlines = defs.map(([k, c, bg, lb]) => {
      const s = by[k], y = Y(s.target);
      const label = `${lb} $${s.target.toFixed(0)} (${s.upside >= 0 ? '+' : ''}${(s.upside * 100).toFixed(0)}%)`;
      return `<line x1="${PADL}" x2="${W - PADR + 4}" y1="${y}" y2="${y}"
          stroke="${c}" stroke-width="1.3" stroke-dasharray="7 6" opacity=".9"/>
        <rect x="${W - PADR + 8}" y="${y - 12}" width="${PADR - 16}" height="24" rx="7"
          fill="${bg}" stroke="${c}" stroke-width="1"/>
        <text x="${W - PADR / 2}" y="${y + 4}" fill="${c}" font-size="12.5"
          font-weight="700" text-anchor="middle">${label}</text>`;
    }).join('');
  }

  document.getElementById('tvchart').innerHTML = `
    <svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;display:block"
        font-family="system-ui,-apple-system,sans-serif">
      <defs><linearGradient id="ga" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="rgba(38,208,124,.28)"/>
        <stop offset="1" stop-color="rgba(38,208,124,.02)"/>
      </linearGradient></defs>
      ${grid}
      <path d="${area}" fill="url(#ga)"/>
      <polyline points="${pts}" fill="none" stroke="#26d07c" stroke-width="2.2"
        stroke-linejoin="round" stroke-linecap="round"/>
      ${tlines}${dates}
    </svg>`;

  const first = slice[0].value, last = slice[slice.length - 1].value;
  const chg = last - first, pct = chg / first * 100;
  document.getElementById('chartHead').innerHTML = `
    <span class="px">$${last.toLocaleString('en-US', {minimumFractionDigits: 2})}</span>
    <span class="chg ${chg >= 0 ? 'up' : 'down'}">${chg >= 0 ? '+' : ''}$${Math.abs(chg).toFixed(2)}
      (${chg >= 0 ? '+' : ''}${pct.toFixed(2)}%)</span>
    <span class="rng">${p}</span>`;
}

const screenCard = document.getElementById('screenCard');
document.getElementById('discoverBtn').addEventListener('click', async () => {
  setMode('results');
  grid.style.display = 'none'; screenCard.style.display = 'none';
  status.innerHTML = `<span class="spin"></span>${TR('disc_scan')}`;
  try {
    const r = await fetch('/api/screen');
    if (!r.ok) throw new Error((await r.json()).error || r.status);
    const rows = await r.json();
    const trs = rows.map((x, i) => `<tr>
      <td>${i + 1}</td>
      <td class="tk" data-t="${x.ticker}">${x.ticker}</td>
      <td class="nm">${x.name}</td>
      <td class="num">$${(x.revenue / 1e9).toFixed(1)}B</td>
      <td class="num">${(x.growth * 100).toFixed(0)}%</td>
      <td class="num">${(x.margin * 100).toFixed(0)}%</td>
      <td class="num">${x.target_base ? '$' + x.target_base.toFixed(0) +
        ' <span class="chip ' + (x.upside_base >= 0 ? 'up' : 'down') + '">' +
        (x.upside_base >= 0 ? '+' : '') + (x.upside_base * 100).toFixed(0) + '%</span>' : '—'}</td>
      <td><span class="scorepill"><span class="mini"><i style="width:${x.score10 * 10}%"></i></span>
        <b>${x.score10.toFixed(1)}</b></span></td>
    </tr>`).join('');
    screenCard.innerHTML = `<h2>${TR('disc_h')} ${rows.length} ${TR('disc_by')}</h2>
      <div class="sub">${TR('disc_sub')}</div>
      <table class="scr"><thead><tr><th>#</th><th>Ticker</th><th>${TR('th_company')}</th>
        <th style="text-align:right">${TR('th_rev')}</th><th style="text-align:right">${TR('th_growth')}</th>
        <th style="text-align:right">${TR('th_margin')}</th><th style="text-align:right">${TR('th_target')}</th>
        <th>${TR('th_score')}</th></tr></thead><tbody>${trs}</tbody></table>`;
    screenCard.style.display = 'block';
    status.textContent = '';
    screenCard.querySelectorAll('.tk').forEach(el =>
      el.addEventListener('click', () => { screenCard.style.display = 'none'; run(el.dataset.t); }));
  } catch (err) {
    status.innerHTML = `${TR('err_discover')} ${err.message}`;
  }
});

async function run(t) {
  if (!t) return;
  setMode('results');
  sugg.style.display = 'none'; grid.style.display = 'none';
  screenCard.style.display = 'none'; status.textContent = '';
  q.value = t;
  startLoading(t);
  try {
    CURRENT_TICKER = t;
    const r = await fetch('/api/analyze?ticker=' + encodeURIComponent(t) + '&lang=' + LANG);
    if (!r.ok) throw new Error((await r.json()).error || r.status);
    const d = await r.json();
    document.getElementById('heroCard').innerHTML = heroHtml(d);
    document.getElementById('wordsCard').innerHTML = wordsHtml(d);
    document.getElementById('scoreCard').innerHTML = scoreHtml(d);
    document.getElementById('targetCard').innerHTML = targetHtml(d);
    document.getElementById('briefCard').innerHTML = briefHtml(d);
    document.getElementById('aggCard').innerHTML = aggButtonHtml(t);
    document.getElementById('reportCard').innerHTML = reportButtonHtml(t);
    loadLevels(t);
    finishLoading(true, () => {
      grid.style.display = 'grid';
      renderChart(d);
      requestAnimationFrame(() => {
        document.querySelectorAll('.stick[data-h]').forEach(b => b.style.height = b.dataset.h + '%');
        document.querySelectorAll('.fill2[data-w]').forEach(f => f.style.width = f.dataset.w + '%');
      });
    });
  } catch (err) {
    finishLoading(false);
    status.innerHTML = `${TR('err_analyze')} <b>${t}</b>: ${err.message}`;
  }
}

function zoneRow(z, color) {
  const dist = z.distance_percent === null ? '' :
    `<span style="color:var(--muted);font-size:12.5px">${Math.abs(z.distance_percent).toFixed(1)}% ${TR('lvl_away')}</span>`;
  return `<div style="display:flex;justify-content:space-between;align-items:baseline;gap:10px;padding:8px 0;border-top:1px solid var(--grid)">
    <span><b style="color:${color}">$${z.center.toFixed(2)}</b>
      <span style="color:var(--muted);font-size:12.5px">($${z.lower.toFixed(2)}–$${z.upper.toFixed(2)}) · ${z.touches} ${TR('lvl_touches')}</span></span>
    ${dist}</div>`;
}

async function loadLevels(t) {
  const card = document.getElementById('levelsCard');
  card.innerHTML = `<h2>${TR('lvl_h')}</h2><div class="sub">${TR('lvl_loading')}</div>`;
  try {
    const r = await fetch('/api/levels?ticker=' + encodeURIComponent(t));
    if (!r.ok) throw new Error((await r.json()).error || r.status);
    const d = await r.json();
    const sup = (d.support || []).map(z => zoneRow(z, 'var(--green)')).join('');
    const res = (d.resistance || []).map(z => zoneRow(z, 'var(--red)')).join('');
    const mas = (d.moving_averages || []).map(m =>
      `<div style="display:flex;justify-content:space-between;padding:8px 0;border-top:1px solid var(--grid)">
        <span>${m.label}</span><b>$${m.value.toFixed(2)}</b></div>`).join('');
    const avw = (d.avwaps || []).map(a =>
      `<div style="display:flex;justify-content:space-between;padding:8px 0;border-top:1px solid var(--grid)">
        <span style="font-size:13.5px">${a.reason}</span><b>$${a.value.toFixed(2)}</b></div>`).join('');
    if (!sup && !res && !mas && !avw) {
      card.innerHTML = `<h2>${TR('lvl_h')}</h2><div class="sub">${TR('lvl_none')}</div>`;
      return;
    }
    card.innerHTML = `<h2>${TR('lvl_h')}</h2>
      <div class="sub">${TR('lvl_sub')}</div>
      <div style="margin-top:10px;font-size:14px"><b>${TR('lvl_price')}: $${(d.price ?? 0).toFixed(2)}</b></div>
      <div class="brief-grid" style="margin-top:10px">
        <div class="brief-col"><div class="bh">${TR('lvl_resist')}</div>${res || '—'}</div>
        <div class="brief-col"><div class="bh">${TR('lvl_support')}</div>${sup || '—'}</div>
        <div class="brief-col"><div class="bh">${TR('lvl_ma')}</div>${mas || '—'}</div>
        <div class="brief-col"><div class="bh">${TR('lvl_avwap')}</div>${avw || '—'}</div>
      </div>`;
  } catch (err) {
    card.innerHTML = `<h2>${TR('lvl_h')}</h2>
      <div class="sub" style="color:var(--red)">${TR('lvl_err')} ${err.message}</div>`;
  }
}

function reportButtonHtml(t) {
  return `<h2>${TR('rep_h')}</h2><div class="sub">${TR('rep_sub')}</div>
    <button class="discover" style="margin-top:14px" onclick="loadReport('${t}')">${TR('rep_run')}</button>`;
}

function mdToHtml(md) {
  const esc = x => x.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  const lines = esc(md).split(String.fromCharCode(10)); const out = []; let tbl = [];
  const flush = () => {
    if (!tbl.length) return;
    const rows = tbl.filter(r => !/^\|[\s|:-]+\|$/.test(r));
    out.push('<table class="scr">' + rows.map((r,i) => {
      const cells = r.split('|').slice(1,-1).map(c => c.trim());
      const tag = i === 0 ? 'th' : 'td';
      return '<tr>' + cells.map(c => `<${tag}>${c}</${tag}>`).join('') + '</tr>';
    }).join('') + '</table>');
    tbl = [];
  };
  for (const ln of lines) {
    if (ln.startsWith('|')) { tbl.push(ln); continue; }
    flush();
    if (/^# /.test(ln)) out.push(`<h2>${ln.slice(2)}</h2>`);
    else if (/^## /.test(ln)) out.push(`<div class="bh" style="margin-top:18px">${ln.slice(3)}</div>`);
    else if (/^- /.test(ln)) out.push(`<div style="margin-left:14px;font-size:13.5px">• ${ln.slice(2)}</div>`);
    else if (/^\d+\. /.test(ln)) out.push(`<div style="margin:6px 0;font-size:13.5px">${ln}</div>`);
    else if (ln.trim() === '---') out.push('<hr style="border:0;border-top:1px solid var(--grid);margin:14px 0">');
    else if (ln.trim()) out.push(`<div style="font-size:13.5px;margin:4px 0">${ln}</div>`);
  }
  flush();
  return out.join('').replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/_(.+?)_/g, '<i>$1</i>');
}

async function loadReport(t) {
  const card = document.getElementById('reportCard');
  card.innerHTML = `<h2>${TR('rep_h')}</h2><div class="sub">${TR('rep_loading')}</div>`;
  try {
    const r = await fetch('/api/report?ticker=' + encodeURIComponent(t) + '&lang=' + LANG);
    if (!r.ok) throw new Error((await r.json()).error || r.status);
    const d = await r.json();
    if (d.status !== 'ok') throw new Error('incomplete: ' + (d.missing || []).join(', '));
    const blob = 'data:text/markdown;charset=utf-8,' + encodeURIComponent(d.markdown);
    card.innerHTML = `<div style="display:flex;justify-content:space-between;align-items:center;gap:12px">
        <h2 style="margin:0">${TR('rep_h')}</h2>
        <a class="discover" style="text-decoration:none;padding:8px 16px" download="${t}-report.md" href="${blob}">${TR('rep_dl')}</a>
      </div>
      <div style="margin-top:12px">${mdToHtml(d.markdown)}</div>
      ${(d.charts || []).map(function(c) {
        return '<img src="' + c + '" alt="" '
             + 'style="width:100%;max-width:820px;margin-top:16px;border-radius:8px">';
      }).join('')}`;
  } catch (err) {
    card.innerHTML = `<h2>${TR('rep_h')}</h2>
      <div class="sub" style="color:var(--red)">${TR('rep_err')} ${err.message}</div>
      <button class="discover" style="margin-top:14px" onclick="loadReport('${t}')">${TR('retry')}</button>`;
  }
}

function aggButtonHtml(t) {
  return `<h2>${TR('agg_h')}</h2><div class="sub">${TR('agg_sub')}</div>
    <button class="discover" style="margin-top:14px" onclick="loadAgg('${t}')">${TR('agg_run')}</button>`;
}

async function loadAgg(t) {
  const card = document.getElementById('aggCard');
  card.innerHTML = `<h2>${TR('agg_h')}</h2><div class="sub">${TR('agg_loading')}</div>`;
  try {
    const r = await fetch('/api/aggregate?ticker=' + encodeURIComponent(t));
    if (!r.ok) throw new Error((await r.json()).error || r.status);
    const d = await r.json();
    if (d.status !== 'ok') throw new Error('incomplete: ' + (d.missing || []).join(', '));
    const cats = (d.categories || []).map(c =>
      `<div style="display:flex;justify-content:space-between;padding:7px 0;border-top:1px solid var(--grid);gap:10px">
        <span>${c.key}</span>
        <span style="text-align:right">
          <span style="color:var(--muted);font-size:12.5px">${TR('agg_quick')} ${c.quick_score10 === null || c.quick_score10 === undefined ? 'N/S' : c.quick_score10 + '/10'}</span>
          &nbsp;→&nbsp;
          ${c.unscored
            ? `<b style="color:var(--muted)">${TR('agg_ns')}</b>`
            : `<b>${c.score10 === null ? 'N/S' : c.score10 + '/10'}</b> <span style="color:var(--muted);font-size:12px">(${c.points}/${c.max_points})</span>`}
          <span style="color:var(--muted);font-size:12px">· ${TR('agg_cov')} ${Math.round(c.coverage*100)}%</span>${
            c.unscored || !c.unscored_points ? '' :
            `<span style="color:var(--orange);font-size:12px"> · ${c.unscored_points}/${c.max_points} ${TR('agg_na')}</span>`
          }</span></div>`).join('');
    const list = (arr, color) => (arr || []).map(x =>
      `<li style="margin:3px 0;color:${color};font-size:13px">${x}</li>`).join('');
    const contra = (d.contradictions || []).map(c =>
      `<li style="margin:3px 0;font-size:13px"><b>${c.combination}</b> → ${c.label}</li>`).join('');
    card.innerHTML = `<h2>${TR('agg_h')}</h2>
      <div class="sub">${TR('agg_sub')}</div>
      ${d.conclusive === false ? `<div style="margin:14px 0 4px;padding:12px;border-radius:8px;
          background:rgba(255,165,0,.10);border:1px solid var(--orange)">
          <b style="font-size:16px">${TR('agg_noconc')}</b>
          <div style="color:var(--ink2);font-size:13px;margin-top:6px">${TR('agg_noconc_sub')
            .replace('{pts}', d.unscorable_points)
            .replace('{cats}', (d.unscored_categories||[]).join(', '))}</div></div>` : ''}
      <div style="margin:14px 0 4px;font-size:20px"><b>${d.label}</b>
        <span style="color:var(--ink2);font-size:14px"> · ${d.raw_score}/100 ${TR('agg_raw')} · ${TR('agg_conf')} ${d.confidence}</span></div>
      <div style="color:var(--muted);font-size:13px">${d.band}</div>
      <div style="margin-top:14px"><div class="bh">${TR('agg_cats')}</div>
        <div style="color:var(--muted);font-size:12px;margin-bottom:4px">${TR('agg_why')}</div>${cats}</div>
      ${d.overrides.length ? `<div style="margin-top:14px"><div class="bh">${TR('agg_ovr')}</div><ul style="margin-left:18px">${list(d.overrides,'var(--orange)')}</ul></div>` : ''}
      ${d.failed_gates.length ? `<div style="margin-top:14px"><div class="bh">${TR('agg_gates')}</div><ul style="margin-left:18px">${list(d.failed_gates,'var(--red)')}</ul></div>` : ''}
      ${contra ? `<div style="margin-top:14px"><div class="bh">${TR('agg_contra')}</div><ul style="margin-left:18px">${contra}</ul></div>` : ''}
      <div style="margin-top:16px;color:var(--muted);font-size:12px">${TR('agg_note')}</div>`;
  } catch (err) {
    card.innerHTML = `<h2>${TR('agg_h')}</h2>
      <div class="sub" style="color:var(--red)">${TR('agg_err')} ${err.message}</div>
      <button class="discover" style="margin-top:14px" onclick="loadAgg('${t}')">${TR('retry')}</button>`;
  }
}

</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _json(self, obj: dict | list, code: int = 200) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _fail(self, e: Exception) -> None:
        """A packet the Cerebro refuses to score is a data-availability
        answer, not a server fault — `builder.py` rejects a ticker with
        under 252 sessions on purpose. Reporting those as 500 made a
        working guardrail look like a crash."""
        msg = str(e)
        # Match only builder.py's own rejection prefix. A looser test
        # ("fewer than" anywhere) would relabel genuine server bugs as
        # data problems and hide them.
        rejected = msg.startswith("packet rejected")
        self._json({"error": msg, "kind": "data" if rejected else "server"},
                   422 if rejected else 500)

    def do_GET(self) -> None:  # noqa: N802 (stdlib API)
        url = urlparse(self.path)
        qs = parse_qs(url.query)
        if url.path == "/":
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif url.path.startswith("/charts/"):
            # /charts/<TICKER>/<name>.png -> today's rendered chart.
            parts = url.path.strip("/").split("/")
            path = _chart_path(parts[1], parts[2]) if len(parts) == 3 else None
            if path is None:
                self._json({"error": "not found"}, 404)
                return
            body = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif url.path == "/api/search":
            self._json(search(qs.get("q", [""])[0]))
        elif url.path == "/api/screen":
            try:
                with _lock:
                    self._json(run_screen(limit=15))
            except Exception as e:
                self._fail(e)
        elif url.path == "/api/analyze":
            ticker = qs.get("ticker", [""])[0].strip().upper()
            lang = qs.get("lang", ["en"])[0]
            if not ticker:
                self._json({"error": "missing ticker"}, 400)
                return
            try:
                # One analysis at a time: providers share one httpx client/cache.
                with _lock:
                    result = analyze(ticker, lang)
                self._json(result)
            except Exception as e:  # surface as JSON, keep server alive
                self._fail(e)
        elif url.path == "/api/report":
            ticker = qs.get("ticker", [""])[0].strip().upper()
            lang = qs.get("lang", ["en"])[0]
            if not ticker:
                self._json({"error": "missing ticker"}, 400)
                return
            try:
                # Full auditable report (FINAL_REPORT_SCHEMA.md).
                # charts_dir is required for the mandatory visual layer —
                # without it run_report silently returns charts: [].
                from wbj.report import run_report
                with _lock:
                    result = run_report(ticker, settings, lang=lang,
                                        charts_dir=_charts_dir(ticker))
                # Hand the browser URLs, not filesystem paths.
                result["charts"] = [f"/charts/{ticker}/{Path(p).name}"
                                    for p in result.get("charts", [])]
                self._json(result)
            except Exception as e:
                self._fail(e)
        elif url.path == "/api/aggregate":
            ticker = qs.get("ticker", [""])[0].strip().upper()
            if not ticker:
                self._json({"error": "missing ticker"}, 400)
                return
            try:
                # Strict Cerebro profile: gates + overrides + contradictions.
                from wbj.deep import run_aggregate
                with _lock:
                    result = run_aggregate(ticker, settings)
                self._json(result)
            except Exception as e:
                self._fail(e)
        elif url.path == "/api/levels":
            ticker = qs.get("ticker", [""])[0].strip().upper()
            if not ticker:
                self._json({"error": "missing ticker"}, 400)
                return
            try:
                # Price-action levels (no Claude, no cost).
                from wbj.deep import run_levels
                with _lock:
                    result = run_levels(ticker, settings)
                self._json(result)
            except Exception as e:
                self._fail(e)
        else:
            self._json({"error": "not found"}, 404)

    def log_message(self, fmt: str, *args) -> None:
        print(f"[wbj] {self.address_string()} {fmt % args}")


if __name__ == "__main__":
    print(f"WBJ web app -> http://localhost:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
