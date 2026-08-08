"""CLI for wbj compute engine.

MVP pipeline: EDGAR companyfacts (no API key needed) -> mini packet ->
formulas -> anchor scores -> Financial category points. FMP enriches
the packet when an API key is configured, but is not required.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import typer

# Reports use box-drawing and typographic characters; the Windows console
# defaults to cp1252 and raises UnicodeEncodeError on them.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

from wbj.config import load_settings
from wbj.core.nullstates import EvidenceClass, NullState, Value
from wbj.core.scoring import Category, Dimension, anchor_score
from wbj.core.formulas import yoy
from wbj.providers.cache import Cache
from wbj.providers.edgar import EdgarProvider
from wbj.providers.fmp import FMPProvider
from wbj.quick import quick_scorecard

app = typer.Typer()

# Concept tags to try, in preference order, per us-gaap taxonomy drift.
_REVENUE_TAGS = [
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
]
_NET_INCOME_TAGS = ["NetIncomeLoss"]
_OCF_TAGS = ["NetCashProvidedByUsedInOperatingActivities"]
_CAPEX_TAGS = ["PaymentsToAcquirePropertyPlantAndEquipment"]
_DEBT_TAGS = ["LongTermDebtNoncurrent", "LongTermDebt"]
_EQUITY_TAGS = ["StockholdersEquity"]
_OP_INCOME_TAGS = ["OperatingIncomeLoss"]
_GROSS_PROFIT_TAGS = ["GrossProfit"]
_INTEREST_TAGS = ["InterestExpense", "InterestExpenseNonoperating"]
_SHARES_TAGS = [
    "WeightedAverageNumberOfDilutedSharesOutstanding",
    "WeightedAverageNumberOfSharesOutstandingBasic",
]

# Scoring anchors (0-10) — MVP defaults, aligned with Cerebro-style bands.
_ANCHORS_REV_GROWTH = [(-0.10, 0.0), (0.0, 3.0), (0.10, 6.0), (0.25, 9.0), (0.40, 10.0)]
_ANCHORS_NET_MARGIN = [(-0.10, 0.0), (0.0, 3.0), (0.10, 6.0), (0.20, 8.5), (0.30, 10.0)]
_ANCHORS_FCF_MARGIN = [(-0.10, 0.0), (0.0, 3.0), (0.10, 6.5), (0.25, 10.0)]
_ANCHORS_DEBT_EQUITY = [(0.0, 10.0), (0.5, 8.0), (1.0, 6.0), (2.0, 3.0), (4.0, 0.0)]


def _providers():
    settings = load_settings()
    cache = Cache(settings.cache_dir)
    return settings, EdgarProvider(settings, cache), FMPProvider(settings, cache)


def _is_annual_period(r: dict) -> bool:
    """True for a full-fiscal-year duration, or a fiscal-year-end instant
    (balance-sheet snapshot). Drops quarterly rows EDGAR mislabels `fp=FY`."""
    start, end = r.get("start"), r.get("end")
    if not start or start == end:
        return True  # instant (balance-sheet item: equity, debt, ...)
    try:
        days = (date.fromisoformat(end) - date.fromisoformat(start)).days
    except (ValueError, TypeError):
        return False
    return 350 <= days <= 380


def _annual_series(facts: dict, tags: list[str]) -> list[dict]:
    """Annual (10-K FY) datapoints, from the tag carrying the *freshest* data.

    A company can migrate concepts across years (e.g. NVDA's revenue moved
    from `RevenueFromContractWithCustomerExcludingAssessedTax`, which stops
    at FY2022, to `Revenues`, current through FY2026). Picking the first tag
    with *any* data would return the stale series and mismatch it against a
    fresher net-income series — so we pick the tag whose latest full-year
    end is the most recent, breaking ties by preference order.
    """
    gaap = facts.get("facts", {}).get("us-gaap", {})
    best: list[dict] = []
    best_end = ""
    for tag in tags:
        units = gaap.get(tag, {}).get("units", {})
        rows = units.get("USD") or units.get("shares") or []
        annual = [
            r for r in rows
            if r.get("form") == "10-K" and r.get("fp") == "FY" and _is_annual_period(r)
        ]
        if not annual:
            continue
        # Deduplicate restatements: keep the latest filing per fiscal year end.
        by_end: dict[str, dict] = {}
        for r in sorted(annual, key=lambda r: r.get("filed", "")):
            by_end[r["end"]] = r
        series = sorted(by_end.values(), key=lambda r: r["end"])
        if series[-1]["end"] > best_end:  # freshest tag wins; ties keep preference
            best, best_end = series, series[-1]["end"]
    return best


def _latest(series: list[dict]) -> float | None:
    return series[-1]["val"] if series else None


def _build_packet(ticker: str) -> dict:
    settings, edgar, fmp = _providers()
    cik = edgar.cik_for(ticker)
    if cik is None:
        raise typer.BadParameter(f"Ticker {ticker!r} not found in SEC EDGAR.")
    facts = edgar.companyfacts(cik)
    if facts is None:
        raise typer.Exit(code=1)

    packet: dict = {
        "ticker": ticker.upper(),
        "cik": cik,
        "as_of": date.today().isoformat(),
        "entity": facts.get("entityName"),
        "sources": {"edgar": "companyfacts", "fmp": fmp.available},
        "annual": {
            "revenue": _annual_series(facts, _REVENUE_TAGS),
            "net_income": _annual_series(facts, _NET_INCOME_TAGS),
            "operating_cash_flow": _annual_series(facts, _OCF_TAGS),
            "capex": _annual_series(facts, _CAPEX_TAGS),
            "long_term_debt": _annual_series(facts, _DEBT_TAGS),
            "equity": _annual_series(facts, _EQUITY_TAGS),
            "operating_income": _annual_series(facts, _OP_INCOME_TAGS),
            "gross_profit": _annual_series(facts, _GROSS_PROFIT_TAGS),
            "interest_expense": _annual_series(facts, _INTEREST_TAGS),
            "diluted_shares": _annual_series(facts, _SHARES_TAGS),
        },
    }
    if fmp.available:
        profile = fmp.profile(ticker)
        packet["fmp_profile"] = profile
        prof0 = (profile[0] if isinstance(profile, list) and profile
                 else profile if isinstance(profile, dict) else {})
        # Phase 1 quick FMP scoring: raw price/estimates only — every
        # indicator (SMA/momentum, multiples) is derived downstream in
        # quick.py. Missing sub-keys keep the dependent category N/S.
        packet["market_data"] = {
            "price": prof0.get("price"),
            "market_cap": prof0.get("mktCap"),
            "ohlcv": fmp.ohlcv_daily(ticker, years=1, today=date.today()),
            "estimates": fmp.analyst_estimates(ticker),
            "earnings": fmp.earnings_calendar(ticker),
            "insiders": fmp.insider_trades(ticker),
        }
    return packet


def _metric(name: str, raw: float | None, unit: str = "ratio") -> Value:
    if raw is None:
        return Value.null(NullState.MISSING, unit=unit, warnings=[f"MISSING: {name}"])
    return Value.of(raw, unit=unit, evidence_class=EvidenceClass.C)


def _score(v: Value, anchors: list[tuple[float, float]]) -> Value:
    if v.is_null:
        return v
    return Value.of(anchor_score(v.value, anchors), unit="score", evidence_class=EvidenceClass.C)


def _compute(packet: dict) -> dict:
    a = packet["annual"]
    rev, ni = a["revenue"], a["net_income"]
    ocf, capex = a["operating_cash_flow"], a["capex"]
    debt, eq = a["long_term_debt"], a["equity"]

    # Metrics from the two most recent fiscal years.
    growth = (
        yoy(rev[-1]["val"], rev[-2]["val"])
        if len(rev) >= 2
        else Value.null(NullState.MISSING, unit="ratio", warnings=["MISSING: revenue history"])
    )
    rev_latest, ni_latest = _latest(rev), _latest(ni)
    ocf_latest, capex_latest = _latest(ocf), _latest(capex)
    debt_latest, eq_latest = _latest(debt), _latest(eq)

    net_margin = _metric(
        "net_margin",
        (ni_latest / rev_latest) if rev_latest and ni_latest is not None else None,
    )
    fcf = (
        (ocf_latest - capex_latest)
        if ocf_latest is not None and capex_latest is not None
        else None
    )
    fcf_margin = _metric("fcf_margin", (fcf / rev_latest) if rev_latest and fcf is not None else None)
    debt_equity = _metric(
        "debt_to_equity",
        (debt_latest / eq_latest) if eq_latest and debt_latest is not None else None,
    )

    profitability = Dimension(
        name="Profitability",
        max_points=7.5,
        metric_scores=[
            (0.5, _score(net_margin, _ANCHORS_NET_MARGIN)),
            (0.5, _score(fcf_margin, _ANCHORS_FCF_MARGIN)),
        ],
    )
    growth_health = Dimension(
        name="Growth & Balance Sheet",
        max_points=7.5,
        metric_scores=[
            (0.5, _score(growth, _ANCHORS_REV_GROWTH)),
            (0.5, _score(debt_equity, _ANCHORS_DEBT_EQUITY)),
        ],
    )
    financial = Category(name="Financial", max_points=15.0, dimensions=[profitability, growth_health])

    def fmt(v: Value) -> float | str:
        return round(v.value, 4) if v.is_valid else str(v.state)

    return {
        "ticker": packet["ticker"],
        "entity": packet["entity"],
        "as_of": packet["as_of"],
        "fiscal_year_end": rev[-1]["end"] if rev else None,
        "metrics": {
            "revenue_usd": rev_latest,
            "revenue_yoy": fmt(growth),
            "net_margin": fmt(net_margin),
            "fcf_margin": fmt(fcf_margin),
            "debt_to_equity": fmt(debt_equity),
        },
        "scores": {
            "dimensions": {
                d.name: fmt(d.score10_value()) for d in financial.dimensions
            },
            "category": {
                "name": financial.name,
                "points": round(financial.points(), 2),
                "max_points": financial.max_points,
                "score10": round(financial.score10(), 2),
                "coverage": round(financial.coverage(), 2),
            },
        },
        "scorecard": quick_scorecard(packet),
    }


def _out_dir(settings, ticker: str) -> Path:
    d = settings.reports_dir / ticker.upper() / date.today().isoformat()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _run_pipeline(fn, *args, **kwargs):
    """Run a pipeline entry point, reporting a packet rejection cleanly.

    `PacketRejected` is a documented Phase 0/1 outcome -- ORCHESTRATION.md
    rejects a packet that lacks a currency, a timestamp, a share count or
    252 sessions -- so it is a data-availability answer, not a crash. Letting
    it escape printed a full traceback, which reads as a broken tool rather
    than a guardrail doing its job. The webapp already draws this
    distinction (`_fail`); the CLI did not.
    """
    from wbj.packet.builder import PacketRejected

    try:
        return fn(*args, **kwargs)
    except PacketRejected as exc:
        typer.echo(f"\nNo analysis: {exc}")
        typer.echo("The Cerebro refuses to score a packet it cannot validate "
                   "(currency, timestamps, diluted shares, 252 sessions). "
                   "Nothing was written.")
        raise typer.Exit(1) from None



@app.command()
def entradas(
    tickers: list[str] = typer.Argument(..., help="Uno o varios tickers."),
    force: bool = typer.Option(False, "--force", help="Sobrescribe un archivo existente."),
) -> None:
    """Escribe el esqueleto de `Entradas/<TICKER>.json` para cada ticker.

    Todos los valores salen en `null`, que se comporta igual que no tener
    archivo -- el esqueleto documenta las llaves, nunca inventa cifras. Un
    archivo que ya existe no se toca salvo `--force`: lo que hay ahi es
    captura hecha a mano que ningun proveedor puede devolver.
    """
    from wbj.entradas import write_skeleton

    settings, _, _ = _providers()
    directory = getattr(settings, "inputs_dir", None) or (
        Path(settings.reports_dir).parent / "Entradas")
    wrote = 0
    for ticker in tickers:
        ok, message = write_skeleton(directory, ticker, force=force)
        wrote += 1 if ok else 0
        typer.echo(("  " if ok else "  - ") + message)
    typer.echo("")
    typer.echo(f"{wrote}/{len(tickers)} escritos en {directory}")


@app.command("tam-todas")
def tam_todas(
    limite: int = typer.Option(0, help="Cuantas industrias intentar (0 = todas)."),
    minimo: int = typer.Option(2, help="Empresas minimas para que cuente."),
) -> None:
    """Resuelve el TAM de cada industria del mercado de EE.UU. que no lo tenga.

    Va en orden de cobertura -- primero las industrias con mas empresas --
    porque cada intento cuesta peticiones y la cuota del proveedor de busqueda
    es finita. Se corta sola en cuanto la cuota se agota y lo dice.

    Lo que no se pueda comprobar en la pagina de su propia fuente NO se guarda
    como TAM: queda como sugerencia sin puntuar. Eso es lo que separa este
    barrido de la version que llenaba archivos con cifras que nadie podia
    abrir.
    """
    from wbj.overlay.tam_mundial import resolver_todas_las_industrias

    settings, _, fmp = _providers()
    filas = resolver_todas_las_industrias(settings, fmp, limite=limite,
                                          minimo_empresas=minimo)
    if not filas:
        typer.echo("No se pudo enumerar el universo.")
        return
    marca = {"resuelto": "  ok", "ya estaba": "  = ", "sin fuente": "  - ",
             "cuota agotada": "  !!"}
    for f in filas:
        typer.echo(f"{marca.get(f['estado'], '   ')} {f['industria'][:34]:36} "
                   f"{f['empresas']:>4} empresas  {f['estado']}")
    hechas = sum(1 for f in filas if f["estado"] == "resuelto")
    typer.echo("")
    typer.echo(f"{hechas} industrias resueltas y verificadas contra su fuente.")


@app.command("tam-revisar")
def tam_revisar(
    forzar: bool = typer.Option(False, "--forzar",
                                help="Revisa aunque no toque todavia."),
) -> None:
    """Vuelve a leer cada TAM de industria en la pagina de su fuente.

    Un TAM no es un hecho permanente: WSTS revisa sus ventas mundiales cada
    trimestre y la IEA su demanda cada mes. Esto es lo que permite que la
    cifra la escriba el agente y no un analista sin volver al problema de
    origen -- no se recuerda, se vuelve a leer.

    Correr cada 1-3 meses. No borra nada: si la cifra ya no aparece, marca el
    archivo con `_revisar_a_mano` y conserva el numero anterior, porque una
    fuente caida no es una correccion y adivinar el nuevo seria repetir
    exactamente el error que esta verificacion existe para impedir.
    """
    from wbj.overlay.tam_mundial import revisar_tam_industrias

    settings, _, _ = _providers()
    filas = revisar_tam_industrias(settings, forzar=forzar)
    if not filas:
        typer.echo("No hay TAM de industria que revisar.")
        return
    orden = {"CAMBIO": 0, "fuente inaccesible": 1, "confirmado": 2}
    for f in sorted(filas, key=lambda x: orden.get(x["estado"], 9)):
        marca = {"CAMBIO": "  !!", "fuente inaccesible": "  ? ",
                 "confirmado": "  ok"}.get(f["estado"], "  - ")
        typer.echo(f"{marca} {f['slug']:32} {f['estado']}"
                   + (f"  {f.get('detalle') or f.get('url') or ''}"[:70]))
    cambios = [f for f in filas if f["estado"] == "CAMBIO"]
    typer.echo("")
    if cambios:
        typer.echo(f"{len(cambios)} necesitan revision a mano: su fuente ya no "
                   "publica la cifra guardada.")
    else:
        typer.echo("Ninguna cifra cambio en su fuente.")


@app.command()
def fetch(ticker: str) -> None:
    """Fetch raw EDGAR data for a ticker (cache-first)."""
    _, edgar, _ = _providers()
    cik = edgar.cik_for(ticker)
    if cik is None:
        typer.echo(f"{ticker}: not found in EDGAR")
        raise typer.Exit(1)
    facts = edgar.companyfacts(cik)
    n = len(facts.get("facts", {}).get("us-gaap", {})) if facts else 0
    typer.echo(f"{ticker.upper()}: CIK {cik}, {n} us-gaap concepts fetched")


@app.command()
def packet(ticker: str) -> None:
    """Build and save the data packet for a ticker."""
    settings, _, _ = _providers()
    p = _build_packet(ticker)
    path = _out_dir(settings, ticker) / "packet.json"
    path.write_text(json.dumps(p, indent=2), encoding="utf-8")
    typer.echo(f"packet -> {path}")


@app.command()
def compute(ticker: str) -> None:
    """Compute metrics and scores for a ticker."""
    typer.echo(json.dumps(_compute(_build_packet(ticker)), indent=2))


@app.command()
def analyze(ticker: str) -> None:
    """Run the full MVP pipeline: fetch -> packet -> compute -> save report."""
    from wbj.memoria import save_prediction
    from wbj.targets import live_price, price_targets

    settings, _, _ = _providers()
    p = _build_packet(ticker)
    result = _compute(p)

    out = _out_dir(settings, ticker)
    (out / "packet.json").write_text(json.dumps(p, indent=2), encoding="utf-8")
    (out / "scores.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    # Seed the agent's memory: persist today's prediction for `wbj track`.
    targets = price_targets(p, live_price(ticker, fmp_api_key=settings.fmp_api_key))
    if save_prediction(settings.reports_dir, ticker, date.today(),
                       result["scorecard"], targets):
        typer.echo("prediccion.json guardada (memoria del agente)")

    m, cat = result["metrics"], result["scores"]["category"]
    typer.echo(f"\n=== {result['entity']} ({result['ticker']}) — FY end {result['fiscal_year_end']} ===")
    typer.echo(f"Revenue:        ${m['revenue_usd']:,.0f}" if m["revenue_usd"] else "Revenue: MISSING")
    typer.echo(f"Revenue YoY:    {m['revenue_yoy']}")
    typer.echo(f"Net margin:     {m['net_margin']}")
    typer.echo(f"FCF margin:     {m['fcf_margin']}")
    typer.echo(f"Debt/Equity:    {m['debt_to_equity']}")
    # `scorecard` says "quick" and `aggregate` says "Cerebro profile"; this
    # one said neither, so its numbers read as the strict methodology's. They
    # are not: this is the MVP pipeline, with no gates, overrides or coverage
    # rules. Run `wbj report` for the Cerebro profile.
    typer.echo("--- Scores (0-10, quick MVP — no gates/overrides; see `wbj report`) ---")
    for name, s in result["scores"]["dimensions"].items():
        typer.echo(f"{name}: {s}")
    typer.echo(
        f"Category {cat['name']}: {cat['points']}/{cat['max_points']} pts "
        f"(score {cat['score10']}/10, coverage {cat['coverage']:.0%})"
    )
    typer.echo(f"\nSaved: {out}/packet.json, scores.json")


@app.command()
def scorecard(ticker: str) -> None:
    """Quick 1-10 scorecard across the 6 agent categories."""
    settings, _, _ = _providers()
    p = _build_packet(ticker)
    sc = quick_scorecard(p)
    out = _out_dir(settings, ticker)
    (out / "scorecard.json").write_text(json.dumps(sc, indent=2), encoding="utf-8")

    typer.echo(f"\n=== Quick Scorecard — {p['entity']} ({p['ticker']}) ===")
    for row in sc["categories"]:
        if row["status"] == "scored":
            bar = "█" * int(round(row["score10"])) + "░" * (10 - int(round(row["score10"])))
            typer.echo(f"{row['label']:<28} {bar}  {row['score10']}/10  ({row['points']}/{row['max_points']:.0f} pts)")
        else:
            typer.echo(f"{row['label']:<28} {'·' * 10}  N/S — {row['reason']}")
    typer.echo(f"\nOverall (quick): {sc['overall_10']}/10 on {sc['evidence_points_covered']}/100 evidence pts")
    typer.echo(f"Saved: {out}/scorecard.json")


@app.command()
def track() -> None:
    """Evaluate every saved prediction vs today's prices (agent memory)."""
    from wbj.memoria import track as run_track
    from wbj.targets import live_price

    settings, providers, _ = _providers()
    memoria_dir = settings.repo_root / "Memoria"

    def price_fn(ticker: str, on: date | None = None) -> float | None:
        """Today's close, or the adjusted close on `on`.

        A matured prediction has to be judged on the day its horizon closed
        (`memoria.evaluate`): today's price would be look-ahead, which
        BACKTESTING_AND_CALIBRATION.md forbids. The daily bars the packet
        builder already fetches carry that history.
        """
        if on is None:
            return live_price(ticker, fmp_api_key=settings.fmp_api_key)
        try:
            bars = providers.fmp.ohlcv_daily(ticker, years=6, today=date.today()) or []
        except Exception:
            return None
        # Bars are newest-first; take the last close on or before `on`.
        target = on.isoformat()
        for bar in bars:
            if str(bar.get("date", ""))[:10] <= target:
                close = bar.get("adjClose", bar.get("close"))
                return float(close) if isinstance(close, (int, float)) else None
        return None

    s = run_track(settings.reports_dir, memoria_dir, price_fn, today=date.today())
    typer.echo(f"\n=== Track record al {s['as_of']} ===")
    typer.echo(f"Predicciones: {s['total']} ({s['maduras']} maduras >=12m)")
    if s["hit_rate"] is not None:
        typer.echo(f"Acierto en rango Bear-Bull: {s['hit_rate']:.0%}")
    if s["sesgo_medio"] is not None:
        typer.echo(f"Sesgo medio vs escenario Medio: {s['sesgo_medio']:+.1%}")
    for r in s["rows"]:
        typer.echo(f"  {r['ticker']:<6} {r['date']}  {r['change']:+.1%} real "
                   f"vs {r['base_prorated']:+.1%} esperado  -> {r['outcome']}")
    typer.echo(f"\nReporte escrito en {memoria_dir / 'calibracion.md'}")


@app.command()
def screen(limit: int = 15) -> None:
    """Discover well-scoring companies you may not know (research list)."""
    from wbj.screener import screen as run_screen

    typer.echo("Escaneando universo SEC (primera vez puede tardar 1-2 min)...")
    rows = run_screen(limit=limit, progress=lambda i, n, t: typer.echo(f"  [{i}/{n}] {t}"))
    typer.echo(f"\n=== Descubrimiento — top {len(rows)} por puntaje (research, no asesoria) ===")
    for i, r in enumerate(rows, 1):
        up = f"  target medio ${r['target_base']:,.0f} ({r['upside_base']:+.0%})" if r["target_base"] else ""
        typer.echo(
            f"{i:>2}. {r['ticker']:<6} {r['name'][:34]:<34} "
            f"{r['score10']}/10  ventas ${r['revenue'] / 1e9:.1f}B  "
            f"crec {r['growth']:+.0%}  margen {r['margin']:.0%}{up}"
        )


@app.command()
def judgments(ticker: str) -> None:
    """Show which open qualitative slots are yours and which are the judge's.

    Cerebro admits three sources for a scored claim (01/03 AGENT.md: "a claim
    that cannot be tied to a formula result, reported evidence, or explicitly
    disclosed assumption is context only"). This splits what is still open by
    which of them it draws on, so the "write it or pay for it" question is
    answered from the methodology rather than from a habit.
    """
    from wbj.deep import _run_specialists, build_providers, pending_judgments
    from wbj.overlay.from_packet import build_overlay
    from wbj.packet.builder import build_packet

    settings = load_settings()
    packet = _run_pipeline(build_packet, ticker.upper(), build_providers(settings),
                           now=datetime.now(timezone.utc))
    overlay = build_overlay(packet, settings)
    result = _run_specialists(packet, settings)
    outputs = [o for _, o in result[0]] if isinstance(result, tuple) else []
    groups = pending_judgments(outputs, overlay)

    headers = {
        "assumption": ("YOURS — declare it (evidence class A)",
                       "Nobody can be accountable for an assumption they did not make."),
        "filing": ("THE JUDGE'S — reported evidence",
                   "Answering means reading the 10-K; the judge receives its passages."),
        "external": ("NEITHER — supply as data in Entradas/<TICKER>.json",
                     "Not in any filing and not an assumption."),
    }
    typer.echo(f"\n=== Open qualitative slots — {ticker.upper()} ===")
    for kind, (title, note) in headers.items():
        rows = groups.get(kind) or []
        typer.echo(f"\n{title}  [{len(rows)}]")
        typer.echo(f"  {note}")
        for r in rows:
            typer.echo(f"    • {r['metric_id']:<38} {r['schema_hint'][:44]}")
            typer.echo(f"      {r['why']}")
    n_assume = len(groups.get("assumption") or [])
    if n_assume:
        typer.echo(f"\nWriting those {n_assume} in Entradas/{ticker.upper()}.json under "
                   "`judgments` costs nothing and removes them from the API queue.")


@app.command()
def aggregate(ticker: str) -> None:
    """Aggregate specialist outputs for a ticker."""
    from wbj.deep import run_aggregate

    settings = load_settings()
    typer.echo(f"Aggregating {ticker.upper()} — 6 specialists + gates/overrides...")
    d = _run_pipeline(run_aggregate, ticker, settings)
    if d.get("status") != "ok":
        typer.echo(f"Incomplete: missing {', '.join(d.get('missing', []))}")
        raise typer.Exit(1)

    out = _out_dir(settings, ticker)
    (out / "aggregate.json").write_text(json.dumps(d, indent=2), encoding="utf-8")

    typer.echo(f"\n=== Cerebro profile — {d['ticker']} ===")
    typer.echo(f"{d['label']}  ·  {d['raw_score']}/100 raw  ·  confidence {d['confidence']}")
    typer.echo(f"Band: {d['band']}\n")
    for c in d["categories"]:
        if c.get("unscored"):
            typer.echo(f"  {c['key']:<11} {'N/S — no evidence':<24} coverage {c['coverage']:.0%}")
        else:
            s = f"{c['score10']}/10" if c["score10"] is not None else "N/S"
            typer.echo(f"  {c['key']:<11} {c['points']:>5}/{c['max_points']:<5} {s:<8} coverage {c['coverage']:.0%}")
    if d["overrides"]:
        typer.echo("\nOverrides applied:")
        for o in d["overrides"]:
            typer.echo(f"  • {o}")
    if d["failed_gates"]:
        typer.echo("\nFailed gates:")
        for g in d["failed_gates"]:
            typer.echo(f"  • {g}")
    for c in d["contradictions"]:
        typer.echo(f"\nContradiction: {c['combination']} -> {c['label']}")
    typer.echo("\nStricter than the quick scorecard by design: categories the data "
               "cannot cover score low and can trip the coverage gate.")
    typer.echo(f"Saved: {out}/aggregate.json")


@app.command()
def report(ticker: str, lang: str = "en") -> None:
    """Full auditable research report (FINAL_REPORT_SCHEMA.md): profile,
    scorecard, executive thesis, levels, scenarios, risks and audit trail."""
    from wbj.report import run_report

    settings = load_settings()
    typer.echo(f"Building the full report for {ticker.upper()}...")
    d = _run_pipeline(run_report, ticker, settings, lang=lang,
                      charts_dir=_out_dir(settings, ticker))
    if d.get("status") != "ok":
        typer.echo(f"Incomplete: missing {', '.join(d.get('missing', []))}")
        raise typer.Exit(1)

    out = _out_dir(settings, ticker)
    (out / "report.md").write_text(d["markdown"], encoding="utf-8")
    (out / "report.json").write_text(json.dumps(d["report"], indent=2), encoding="utf-8")
    typer.echo(d["markdown"])
    if d.get("charts"):
        typer.echo("Charts:")
        for c in d["charts"]:
            typer.echo(f"  • {c}")
    typer.echo(f"\nSaved: {out}/report.md and report.json")


if __name__ == "__main__":
    app()
