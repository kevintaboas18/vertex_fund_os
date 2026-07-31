"""Task 23: the auditable final report — assembly + rendering.

Wires the pieces Victor built but never connected end-to-end:
`aggregate` (overrides/gates/contradictions), `synthesize_levels`,
`build_final_report` (which assembles `FINAL_REPORT_SCHEMA.md`), and this
package's Markdown renderer. The one thing the schema requires and no
formula can produce — the 7-sentence `ExecutiveThesis` — is written by
Claude, grounded strictly in the specialists' own outputs.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

import pandas as pd
from pydantic import BaseModel, Field

from wbj.aggregate import (
    AggregateInputs,
    CategoryConfidences,
    CategoryPoints,
    CategoryScore10s,
    apply_gates,
    apply_overrides,
    contradictions,
    raw_total,
)
from wbj.aggregate.synthesis import synthesize_levels
from wbj.deep import (_KEY_BY_LABEL, _run_specialists, _unscored_dimension_points,
                      build_providers, is_unscored)
from wbj.engines import indicators as ind
from wbj.packet.builder import build_packet
from wbj.report.render import render_markdown
from wbj.schemas.final_report import ExecutiveThesis, build_final_report

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You are a senior investment analyst on the Ruta 2030 system. Six quantitative "
    "agents have already scored a stock and produced their findings. Write the "
    "report's 7-sentence executive thesis. Ground every sentence in the agent "
    "outputs given to you — never invent a figure, never contradict a score. "
    "Where the agents lacked coverage, say so plainly instead of guessing."
)


class _Thesis(BaseModel):
    business_quality: str = Field(description="What the company economically does")
    value_creation_durability: str = Field(description="Why value creation is or is not durable")
    growth_engine: str = Field(description="What is funding growth")
    market_validation: str = Field(description="Whether the market currently validates the thesis")
    valuation_message: str = Field(description="What assumptions the current price appears to require")
    key_levels_summary: str = Field(description="Nearest support/resistance and intrinsic-value references")
    primary_risk: str = Field(description="The single most important invalidation risk")


def _price_and_atr(packet: Any) -> tuple[float | None, float | None]:
    """Latest adjusted close and Wilder ATR14 from the packet's daily bars."""
    rows = sorted(packet.market_data.daily, key=lambda r: r.date)
    if not rows:
        return None, None
    df = pd.DataFrame({
        "open": [r.open for r in rows], "high": [r.high for r in rows],
        "low": [r.low for r in rows], "close": [r.close for r in rows],
    })
    atr_series = ind.atr14(df)
    atr = float(atr_series.iloc[-1]) if len(atr_series) and pd.notna(atr_series.iloc[-1]) else None
    return rows[-1].close, atr


def _entitlement_gaps(providers: Any) -> list[str]:
    """One line per endpoint the plan was refused, for the report.

    FMP's institutional-ownership answers 402 and FinnHub's eps- and
    revenue-estimate answer 403 on the current plans. Each came back as
    `None`, which is the same thing a company with no data returns, so the
    analysis quietly lost inputs and the reader had no way to tell a
    missing figure from an unpaid one.
    """
    gaps: list[str] = []
    seen: set[tuple[str, str, int]] = set()
    for name in ("fmp", "edgar", "finnhub", "fred"):
        provider = getattr(providers, name, None)
        for endpoint, status in sorted(getattr(provider, "blocked_endpoints", {}).items()):
            # Cache keys carry a parameter hash (`estimates_d07de7c492a6`);
            # the reader wants the endpoint, not the cache bookkeeping.
            label = re.sub(r"_[0-9a-f]{8,}$", "", endpoint)
            if (name, label, status) in seen:
                continue
            seen.add((name, label, status))
            gaps.append(
                f"{name}: ENDPOINT_NOT_IN_PLAN ({label}, HTTP {status}) — the "
                "data exists but this plan cannot reach it; inputs that rest on "
                "it stay unscored rather than being estimated")
    return gaps


def _llm_failure_reason(exc: BaseException) -> tuple[str, str]:
    """Why the narrative call failed, in Spanish and English.

    Every one of these sends the reader somewhere different: a spent
    balance is a billing page, a bad model name is a config file, a
    rejected key is the key itself. Reporting them all as "no API key"
    pointed at the one thing that was demonstrably fine.
    """
    text = str(exc).lower()
    if "credit balance" in text or "billing" in text or "quota" in text:
        return ("la cuenta de Anthropic no tiene saldo (la clave si funciona)",
                "the Anthropic account is out of credit (the key itself works)")
    if "model" in text and ("not_found" in text or "not found" in text
                            or "invalid" in text):
        return ("JUDGE_MODEL no nombra un modelo valido",
                "JUDGE_MODEL does not name a valid model")
    if "authentication" in text or "invalid x-api-key" in text or "401" in text:
        return ("ANTHROPIC_API_KEY fue rechazada",
                "ANTHROPIC_API_KEY was rejected")
    if "rate limit" in text or "429" in text:
        return ("limite de peticiones de Anthropic alcanzado",
                "Anthropic rate limit reached")
    if isinstance(exc, ImportError):
        return ("el SDK de anthropic no esta instalado",
                "the anthropic SDK is not installed")
    return (f"la llamada fallo ({type(exc).__name__})",
            f"the call failed ({type(exc).__name__})")


def _executive_thesis(ticker: str, profile: Any, outs: dict, levels: Any,
                      settings: Any, lang: str) -> ExecutiveThesis:
    """Claude writes the 7 narrative sentences; falls back to an honest
    'not available' thesis when no API key or the call fails.

    The fallback names the ACTUAL reason. It used to say "no
    ANTHROPIC_API_KEY" for every failure, including the case where the key
    is present and working and the account simply has no credit balance —
    which sent the reader to check the one thing that was already right.
    """
    target = "Spanish" if lang == "es" else "English"

    def _fallback(reason_es: str, reason_en: str) -> ExecutiveThesis:
        text = (f"Narrativa no disponible: {reason_es}." if lang == "es"
                else f"Narrative unavailable: {reason_en}.")
        return ExecutiveThesis(**{f: text for f in _Thesis.model_fields})

    if not getattr(settings, "anthropic_api_key", None):
        return _fallback("falta ANTHROPIC_API_KEY",
                         "ANTHROPIC_API_KEY is not set")
    try:
        import anthropic

        findings = "\n".join(
            f"- {key}: "
            + ("NOT SCORED (no evidence — never call this a bad score), "
               if is_unscored(o) else f"points={o.category.awarded_points}/{o.category.max_points}, ")
            + f"verdict={getattr(o, 'verdict', None)!r}, "
            f"coverage={round(o.coverage, 2) if o.coverage is not None else None}"
            for key, o in outs.items()
        )
        lvl = "\n".join(f"- {x.label}: {x.value}" for x in levels.levels[:8]) or "- (none)"
        user = (
            f"Ticker: {ticker}\n"
            f"Profile: {profile.label} · raw {profile.raw_score:.2f}/100 · "
            f"confidence {profile.total_confidence:.0f}\n"
            f"Failed gates: {', '.join(profile.failed_gates) or 'none'}\n"
            f"Overrides: {', '.join(str(o) for o in profile.overrides) or 'none'}\n\n"
            f"AGENT FINDINGS:\n{findings}\n\nKEY LEVELS:\n{lvl}\n\n"
            f"Write the 7 executive-thesis sentences in {target}."
        )
        resp = anthropic.Anthropic(api_key=settings.anthropic_api_key).messages.parse(
            model=settings.judge_model, max_tokens=8192, system=_SYSTEM,
            messages=[{"role": "user", "content": user}], output_format=_Thesis,
        )
        if resp.parsed_output is None:
            return _fallback("el modelo no devolvio la estructura pedida",
                             "the model returned no structured answer")
        return ExecutiveThesis(**resp.parsed_output.model_dump())
    except Exception as exc:
        logger.warning("executive thesis generation failed; using fallback", exc_info=True)
        es, en = _llm_failure_reason(exc)
        return _fallback(es, en)


def _charts(packet: Any, outs: dict, price: float | None, charts_dir: Any,
            targets: dict | None = None) -> list[str]:
    """Render the report's charts (Task 22). Never fatal: a chart that
    lacks honest range data is skipped rather than faked."""
    from pathlib import Path

    from wbj.report.charts import (
        football_field_chart, price_levels_chart, scenario_fan_chart, scorecard_chart,
    )

    d = Path(charts_dir)
    made: list[str] = []
    # visual-report.md: every chart carries "título, unidades, fuente y
    # timestamp de la data". The ticker and knowledge timestamp are what make
    # a chart attributable once it leaves this folder.
    sec = getattr(packet, "security", None)
    ticker = getattr(sec, "ticker", "") or ""
    currency = getattr(sec, "reporting_currency", None) or "USD"
    as_of = getattr(getattr(packet, "analysis", None), "knowledge_timestamp", None)
    stamp = {"source": f"{ticker} · Warren Buffett Jr (Ruta 2030) · FMP/EDGAR".strip(" ·"),
             "as_of": str(as_of)[:19] if as_of else None}

    try:  # 1. Category scorecard (N/S bars stay empty, never a zero bar).
        cats = [{"key": k, "label": k.title(),
                 "points": o.category.awarded_points or 0.0,
                 "max_points": o.category.max_points,
                 "unscored": is_unscored(o)} for k, o in outs.items()]
        made.append(str(scorecard_chart(cats, d / "scorecard.png", **stamp)))
    except Exception:
        logger.warning("scorecard chart skipped", exc_info=True)

    try:  # 2. Price with support/resistance zones + SMAs.
        rows = sorted(packet.market_data.daily, key=lambda r: r.date)[-250:]
        df = [{"date": r.date, "close": r.close} for r in rows]
        lv = outs["technical"].important_levels
        zones = [{"label": "support", "lower": z.lower, "upper": z.upper}
                 for z in lv.nearest_support[:2]]
        zones += [{"label": "resistance", "lower": z.lower, "upper": z.upper}
                  for z in lv.nearest_resistance[:2]]
        smas = {m.label: m.value for m in lv.moving_averages}
        if df and zones:
            made.append(str(price_levels_chart(df, zones, smas, d / "price_levels.png",
                                               currency=currency, **stamp)))
    except Exception:
        logger.warning("price-levels chart skipped", exc_info=True)

    rb = getattr(outs["valuation"], "reference_bands", None)
    bear, base, bull = (getattr(rb, k, None) for k in ("bear", "base", "bull"))

    try:  # 3. Football field: real ranges between reference values.
        bands = []
        if bear is not None and bull is not None and bear != bull:
            bands.append({"label": "DCF bear–bull", "low": min(bear, bull), "high": max(bear, bull),
                          "assumptions": "reverse-DCF reference band"})
        mos15, mos25 = getattr(rb, "margin_of_safety_15pct", None), getattr(rb, "margin_of_safety_25pct", None)
        if mos15 is not None and mos25 is not None and mos15 != mos25:
            bands.append({"label": "Margin of safety 15–25%", "low": min(mos15, mos25),
                          "high": max(mos15, mos25), "assumptions": "MOS 15% / 25%"})
        if bands and price:
            made.append(str(football_field_chart(bands, price, d / "football_field.png",
                                                 currency=currency, **stamp)))
    except Exception:
        logger.warning("football-field chart skipped", exc_info=True)

    # 4. Scenario fan over *price* targets (12-month). The DCF reference
    # bands belong on the football field, not here: projecting intrinsic
    # value onto a price timeline would compare two different things.
    try:
        rows = sorted(packet.market_data.daily, key=lambda r: r.date)[-120:]
        history = [{"date": r.date, "value": r.close} for r in rows]
        by = {t["key"]: t for t in (targets or {}).get("scenarios", [])}
        scen = []
        for lo_key, hi_key, name in (("bear", "base", "Bear→Base"), ("base", "bull", "Base→Bull")):
            lo, hi = by.get(lo_key), by.get(hi_key)
            if lo and hi and lo["target"] != hi["target"]:
                scen.append({
                    "name": name,
                    "low": min(lo["target"], hi["target"]),
                    "high": max(lo["target"], hi["target"]),
                    "assumptions": hi.get("assumptions", ""),
                })
        if history and scen:
            made.append(str(scenario_fan_chart(history, scen, d / "scenarios.png",
                                               currency=currency, **stamp)))
    except Exception:
        logger.warning("scenario-fan chart skipped", exc_info=True)

    return made


_INSIDER_MATERIAL_USD = 1_000_000.0


def _insiders(ticker: str, settings: Any) -> dict:
    """CLAUDE.md's mandatory report item 5: Form 4 insider buying and
    selling, counting only positions that exceed $1M USD in total.

    Trades are grouped by insider and direction before the threshold is
    applied -- the requirement is written "en total", so a person who
    sells in six chunks over the window is material even when no single
    chunk clears $1M.
    """
    from wbj.providers.cache import Cache
    from wbj.providers.fmp import FMPProvider

    rows = FMPProvider(settings, Cache(settings.cache_dir)).insider_trades(ticker)
    if not isinstance(rows, list):
        return {"available": False, "trades": [], "bought": 0.0, "sold": 0.0}

    grouped: dict[tuple[str, str], dict] = {}
    for r in rows:
        # Form 3 is an initial holdings statement and Form 5 a late
        # report; neither is a transaction. Rows with no price (grants,
        # gifts) carry no dollar value to threshold on.
        if r.get("formType") != "4":
            continue
        shares, price = r.get("securitiesTransacted") or 0, r.get("price") or 0
        value = float(shares) * float(price)
        if value <= 0:
            continue
        side = "buy" if (r.get("acquisitionOrDisposition") or "").upper() == "A" else "sell"
        key = (r.get("reportingName") or "unknown", side)
        g = grouped.setdefault(key, {"name": key[0], "side": side, "value": 0.0,
                                     "title": r.get("typeOfOwner"), "last": ""})
        g["value"] += value
        g["last"] = max(g["last"], r.get("transactionDate") or "")

    material = sorted((g for g in grouped.values() if g["value"] > _INSIDER_MATERIAL_USD),
                      key=lambda g: g["value"], reverse=True)
    return {
        "available": True,
        "trades": material,
        "bought": sum(g["value"] for g in material if g["side"] == "buy"),
        "sold": sum(g["value"] for g in material if g["side"] == "sell"),
    }


def _next_earnings(ticker: str, settings: Any) -> str | None:
    """The next scheduled earnings date, used as the concrete revisit
    event CLAUDE.md requires whenever the classification is "avoid"."""
    from datetime import date as _date

    from wbj.providers.cache import Cache
    from wbj.providers.fmp import FMPProvider

    rows = FMPProvider(settings, Cache(settings.cache_dir)).earnings_calendar(ticker)
    if not isinstance(rows, list):
        return None
    today = _date.today().isoformat()
    upcoming = sorted(r["date"] for r in rows
                      if r.get("date") and r["date"] >= today and r.get("epsActual") is None)
    return upcoming[0] if upcoming else None


def _ownership(ticker: str, settings: Any) -> dict:
    """CLAUDE.md's mandatory report item 4: institutional holders (13F) and
    who runs the company.

    FMP's institutional endpoint sits above the current plan and returns 402.
    This module used to record that "SEC EDGAR has no per-company holdings
    endpoint", and on the paths it had tried that was true: a 13F is filed by
    the INVESTOR, so a company's own filing history never lists one -- AAPL's
    submissions carry zero 13F-HR.

    EDGAR full-text search inverts the lookup. It indexes filing *contents*,
    and every information table names the CUSIP of each position, so searching
    13F-HR for the company's CUSIP returns its holders. Free, and tier 1 in
    SOURCE_HIERARCHY.md where the FMP endpoint is tier 5. FMP is still tried
    first, because it carries share counts and values that the search index
    does not.
    """
    from datetime import date as _date
    from datetime import timedelta

    from wbj.providers.cache import Cache
    from wbj.providers.edgar import EdgarProvider
    from wbj.providers.fmp import FMPProvider

    fmp = FMPProvider(settings, Cache(settings.cache_dir))
    holders_raw = fmp.institutional_holders(ticker)
    execs_raw = fmp.key_executives(ticker) or []

    holders = []
    source = None
    if isinstance(holders_raw, list):
        source = "FMP institutional-ownership"
        for h in holders_raw[:8]:
            name = h.get("investorName") or h.get("holder") or h.get("name")
            if name:
                holders.append({"name": name, "shares": h.get("shares"),
                                "value": h.get("marketValue") or h.get("value")})

    if not holders:
        # Respaldo tier 1, en tres escalones de mejor a peor:
        #   1. Conjunto de datos estructurado 13F de la SEC -> nombres RECONOCIDOS
        #      con posicion real (acciones y dolares), ordenados por tamaño.
        #   2. Schedule 13D/G -> los >5% por definicion, pero su CUSIP dejo de
        #      indexarse a texto completo tras el formato estructurado de 2024.
        #   3. Busqueda de 13F-HR en el indice -> nombres sin cifras, por fecha.
        try:
            cusip = ((fmp.profile(ticker) or [{}])[0] or {}).get("cusip")
            if cusip:
                edgar = EdgarProvider(settings, Cache(settings.cache_dir))
                today = _date.today()
                company_cik = edgar.cik_for(ticker)

                # (1) El dataset trimestral. Es el unico camino que devuelve a
                # los grandes: el indice de texto completo sirve 300 hits de los
                # 10.000+ que mencionan el CUSIP, y las tablas de Vanguard o
                # BlackRock son tan grandes que EDGAR deja de indexarlas -- sus
                # hits mas recientes ahi son de 2009-2016. Un zip por trimestre,
                # cacheado y compartido por todos los tickers.
                _dataset_ok = False
                for r in edgar.holders_13f_dataset(str(cusip), top=10):
                    _dataset_ok = True
                    holders.append({
                        "name": r["name"], "shares": r["shares"], "value": r["value"],
                        "cik": None, "filing_date": None, "basis": "13F dataset",
                        "stake": None, "stale": False,
                        "source_locator": r["source_locator"],
                    })
                if holders:
                    _periodo = holders[0]["source_locator"].split("data set ")[-1].rstrip(")")
                    source = (
                        f"SEC Form 13F structured data set ({_periodo}): "
                        f"{len(holders)} mayores tenedores institucionales de CUSIP {cusip}, "
                        "ORDENADOS POR VALOR DE POSICION reportado, con acciones y dolares. "
                        "Es el conjunto completo de 13F del trimestre, no una muestra del "
                        "indice de texto completo."
                    )

                # (2) Schedule 13D/G: los >5% por definicion, sin ranking ni
                # parseo. Solo si el dataset no dio nada -- cuando si lo da,
                # trae los MISMOS nombres con cifras y al dia, y añadir aqui
                # los de 2024 seria repetirlos peor.
                if company_cik and not _dataset_ok:
                    major = edgar.major_holders_13d_g(
                        str(cusip), company_cik,
                        (today - timedelta(days=1825)).isoformat(), today.isoformat())
                    for r in major[:8]:
                        # Edad declarada. Desde el formato estructurado que la SEC
                        # exige a los 13D/G (dic-2024), su CUSIP deja de ser
                        # buscable a texto completo, así que este barrido devuelve
                        # las últimas ANTERIORES a ese cambio -- para NVDA, de
                        # 2024. Un 5% de hace dos años no es un 5% de hoy, y
                        # presentarlo sin fecha lo haría pasar por vigente.
                        edad = None
                        try:
                            edad = (today - _date.fromisoformat(r["filing_date"])).days
                        except (ValueError, TypeError):
                            pass
                        holders.append({
                            "name": r["name"], "shares": None, "value": None,
                            "cik": r["cik"], "filing_date": r["filing_date"],
                            "stake": ">5% of class", "basis": "13D/G",
                            "age_days": edad,
                            "stale": (edad is not None and edad > 400),
                            "source_locator": f"{r['form']} accession {r['accession']}",
                        })
                    if holders:
                        _viejos = [h for h in holders if h.get("stale")]
                        source = (
                            f"SEC EDGAR: {len(major)} beneficial owners above 5% "
                            f"(Schedule 13D/13G on CUSIP {cusip}). A 13D/G is filed only "
                            "on crossing 5%, so the filer set is the recognised holders "
                            "by definition -- not a ranking of a longer list."
                            + (f" AVISO: {len(_viejos)} de {len(holders)} tienen mas de "
                               "400 dias -- desde el formato estructurado de dic-2024 el "
                               "CUSIP de un 13D/G no es buscable a texto completo, asi que "
                               "estas son las ultimas indexadas, no necesariamente las "
                               "vigentes." if _viejos else "")
                        )

                # (3) Ultimo escalon: barrido del indice de texto completo.
                # Nombres sin cifras y por fecha de presentacion. Se gatilla en
                # que el DATASET fallara, no en que ya haya nombres: cuando solo
                # respondio el 13D/G, sus filas son de 2024 y este barrido añade
                # las presentaciones del trimestre en curso. Bloquearlo por
                # "ya hay holders" era servir el dato viejo teniendo el actual.
                _vistos = {h.get("cik") for h in holders}
                rows = [] if _dataset_ok else [
                    r for r in edgar.institutional_holders_13f(
                        str(cusip), (today - timedelta(days=270)).isoformat(),
                        today.isoformat()) if r.get("cik") not in _vistos]
                _n_13dg = len(holders)
                for r in rows[:8]:
                    holders.append({
                        "name": r["name"], "shares": None, "value": None,
                        "cik": r["cik"], "filing_date": r["filing_date"],
                        "basis": "13F", "stake": None, "stale": False,
                        "source_locator": f"{r['form']} accession {r['accession']}",
                    })
                # La nota del 13F se AÑADE a la del 13D/G en vez de pisarla: cada
                # bloque de nombres lo produjo un formulario distinto y con una
                # definicion distinta, y atribuirlos todos a uno solo seria
                # acreditar al filing equivocado.
                if rows:
                    _n13f = (
                        f"SEC EDGAR full-text search of 13F-HR for CUSIP {cusip} "
                        f"({len(rows)} managers reported a position, ultimos 270 dias). "
                        "Ordered by filing date, NOT by position size: the search index "
                        "names the filer but the share count lives inside the information "
                        "table -- recency is not recognition."
                    )
                    source = f"{source} | {_n13f}" if source else _n13f
        except Exception:
            logger.warning("13F fallback unavailable for %s", ticker, exc_info=True)

    execs = []
    if isinstance(execs_raw, list):
        for e in execs_raw[:8]:
            if e.get("name"):
                execs.append({"name": e["name"], "title": e.get("title"),
                              "pay": e.get("pay"), "active": e.get("active")})

    return {
        "holders": holders,
        "holders_available": bool(holders),
        "holders_source": source,
        "executives": execs,
    }


def _calibration(settings: Any) -> dict | None:
    """The recorded track-record bias, for CLAUDE.md's memory protocol.

    Step 2 of that protocol requires a material bias to be declared in the
    report and the target confidence lowered accordingly. Never fatal: an
    unharvested track record simply means there is nothing to declare.
    """
    try:
        from wbj.memoria import SOURCE_CEREBRO, calibration_bias

        # This is the strict Cerebro report, so it declares the strict
        # profile's own track record -- never the quick scorecard's.
        return calibration_bias(settings.repo_root / "Memoria", source=SOURCE_CEREBRO)
    except Exception:
        logger.warning("calibration unavailable; bias not declared", exc_info=True)
        return None


def _ensure_entradas_skeleton(settings: Any, ticker: str) -> None:
    """Put an empty `Entradas/<TICKER>.json` in place the first time a ticker
    is analysed.

    Twelve metrics take inputs no free structured source carries, and each
    names its missing key in its own warning. Discovering that list one
    NOT_SCORABLE at a time is the slow way round; the file appears with the
    ticker instead, every value `null` and every source named.

    A skeleton scores exactly nothing -- `null` behaves like no file at all --
    so writing one cannot change a result. `write_skeleton` refuses an
    existing file, which is what keeps captured figures safe: analysing NVDA
    again must never overwrite the numbers someone read out of its 10-K.

    Never raises. A read-only directory is a reason to skip a convenience,
    not to fail the analysis.
    """
    from pathlib import Path

    try:
        from wbj.entradas import write_skeleton

        directory = getattr(settings, "inputs_dir", None) or (
            Path(getattr(settings, "reports_dir", ".")).parent / "Entradas")
        written, message = write_skeleton(directory, ticker)
        if written:
            logger.info("Entradas: %s", message)
    except Exception:
        logger.warning("could not prepare the Entradas skeleton", exc_info=True)


def run_report(ticker: str, settings: Any, now: datetime | None = None,
               lang: str = "en", charts_dir: Any = None) -> dict:
    """Build and render the auditable final report for `ticker`."""
    now = now or datetime.now(timezone.utc)
    _ensure_entradas_skeleton(settings, ticker)
    providers = build_providers(settings)
    packet = build_packet(ticker, providers, now=now)
    data_gaps: list[str] = []
    labelled, judged = _run_specialists(packet, settings, notes=data_gaps)
    data_gaps.extend(_entitlement_gaps(providers))
    outs = {_KEY_BY_LABEL[lbl]: o for lbl, o in labelled if lbl in _KEY_BY_LABEL}
    missing = [k for k in _KEY_BY_LABEL.values() if k not in outs]
    if missing:
        return {"ticker": ticker.upper(), "status": "incomplete", "missing": missing}

    inputs = AggregateInputs(**outs, facts_table=getattr(packet, "facts_table", None))
    pts = CategoryPoints(**{k: (o.category.awarded_points or 0.0) for k, o in outs.items()})
    confs = CategoryConfidences(**{k: (o.category.confidence or 0.0) for k, o in outs.items()})
    s10 = CategoryScore10s(**{k: (o.category.score_10 or 0.0) for k, o in outs.items()})

    overrides = apply_overrides(inputs)
    raw = raw_total(pts)
    profile = apply_gates(raw, pts, confs, overrides)
    unscored_keys = {k for k, o in outs.items() if is_unscored(o)}
    clashes = [
        c for c in contradictions(s10, raw)
        if not any(k in c.combination.lower() for k in unscored_keys)
    ]

    price, atr = _price_and_atr(packet)
    levels = synthesize_levels(outs["technical"], outs["valuation"], price or 0.0, atr or 0.0)
    thesis = _executive_thesis(ticker, profile, outs, levels, settings, lang)

    sec = outs["business"].security
    report = build_final_report(
        inputs=inputs, profile=profile, contradictions=clashes, levels=levels,
        executive_thesis=thesis,
        exchange=getattr(sec, "exchange", "") or "",
        currency=getattr(sec, "currency", "") or "",
        analysis_timestamp=now.isoformat(),
        data_gaps=data_gaps,
    )
    # 12-month price targets: they drive the scenario fan AND seed the
    # agent's memory. Computed unconditionally now -- gating them on
    # `charts_dir` meant a report run without charts recorded no prediction,
    # so the strict Cerebro analysis contributed nothing to calibration
    # while the quick scorecard (which scores the same company very
    # differently) was the only thing being tracked.
    targets = None
    try:
        from wbj.cli import _build_packet
        from wbj.targets import price_targets
        targets = price_targets(_build_packet(ticker), price)
    except Exception:
        logger.warning("price targets unavailable; scenario fan skipped", exc_info=True)
        targets = None

    if targets:
        try:
            from wbj.memoria import SOURCE_CEREBRO, save_prediction

            save_prediction(
                settings.reports_dir, ticker, now.date(),
                # `raw_score` is 0-100; the record's `score10` is 0-10.
                {"overall_10": round(profile.raw_score / 10.0, 2),
                 "evidence_points_covered": round(
                     100.0 * sum((o.coverage or 0.0) for o in outs.values()) / len(outs))},
                targets, source=SOURCE_CEREBRO,
            )
        except Exception:
            logger.warning("could not record the prediction for %s", ticker, exc_info=True)
    charts = _charts(packet, outs, price, charts_dir, targets) if charts_dir else []
    ownership = _ownership(ticker, settings)
    insiders = _insiders(ticker, settings)
    # Only "avoid" classifications owe a revisit event (CLAUDE.md item 2).
    revisit = (_next_earnings(ticker, settings)
               if "avoid" in (report.profile.label or "").lower() else None)
    return {
        "ticker": ticker.upper(),
        "status": "ok",
        "judgments_applied": judged,
        "charts": charts,
        "markdown": render_markdown(
            report, lang,
            coverage={k: (o.coverage or 0.0) for k, o in outs.items()},
            unscored={k: is_unscored(o) for k, o in outs.items()},
            unscored_points={k: _unscored_dimension_points(o) for k, o in outs.items()},
            calibration=_calibration(settings),
            ownership=ownership, insiders=insiders, revisit=revisit,
        ),
        "ownership": ownership,
        "insiders": insiders,
        "revisit": revisit,
        "report": report.model_dump(mode="json"),
    }
