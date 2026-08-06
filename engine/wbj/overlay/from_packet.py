"""Build the specialist `overlay` from data the packet already carries.

`business.run(packet, overlay)` reads its hardest inputs from an overlay
dict, but nothing ever built one — `deep.py` called `run(packet)` with no
overlay at all. Since `overlay["wacc"]` is, in that module's own words,
"required for every ROIC/spread/EVA-based metric", five BUS metrics were
NOT_SCORABLE by construction and the Business category came back N/S on
every ticker regardless of how good the data was.

Everything here is *computed from reported figures*, never guessed:

- `wacc` follows the same recipe `specialists/valuation.py` already uses
  (CAPM cost of equity + synthetic cost of debt), from the FRED
  risk-free rate and the disclosed beta already in the packet.
- `segment_shares` come from FMP's reported product segmentation.
- `peer_roic` is each peer's own ROIC, computed from its own filings.

Nothing on `PROHIBITED_IMPUTATION` is produced here. Customer
concentration, retention/churn and unit economics are disclosure-only
figures with no structured source, so they stay absent and the affected
dimensions stay honestly unscored.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from wbj.core import adapters as _adapters
from wbj.core import taxes as _taxes
from wbj.core.periods import fiscal_year
from wbj.engines import valuation_engine as ve

logger = logging.getLogger(__name__)

#: Peer history depth, matching the packet's own.
from wbj.packet.builder import _ANNUAL_HISTORY_YEARS

# Equity risk premium. Same documented default `specialists/valuation.py`
# uses, kept in one place so both paths move together.
_ERP = 0.045
# `wbj.core.scoring.peer_score` needs at least this many peers to engage.
_MIN_PEERS = 8
# DATASET.md asks for eight quarters of backlog/RPO history.
_BACKLOG_QUARTERS = 8
# Bump when the extractor's logic or schema changes, so cached answers
# do not outlive the code that produced them.
_EXTRACT_VERSION = 1
# Our own consensus series, since no configured provider serves one.
_CONSENSUS_CACHE_KEY = "consensus_history"
# DATASET.md asks for "90 days + 8 quarters" of revision history. A
# revision measured over a shorter window is mostly noise.
_REVISION_MIN_DAYS = 30


def _cost_of_equity_from_packet(packet: Any) -> float | None:
    """Cost of equity for the security, from the same inputs WACC uses.

    DECISION_RULES.md's wide-moat condition 1 has an alternative branch —
    "or an approved financial-sector adapter shows equivalent excess
    returns" — and for a bank or insurer INDUSTRY_ADAPTERS.md replaces
    ROIC with ROE. The excess return is then ROE against the cost of
    equity, which needs no input `_wacc_from_packet` does not already
    read.
    """
    cs = getattr(packet, "capital_structure", None) or {}
    rf = (getattr(packet, "estimates", None) or {}).get("risk_free_rate")
    beta = cs.get("beta")
    if rf is None or beta is None:
        return None
    ke = ve.cost_of_equity(float(rf), float(beta), _ERP)
    return None if ke.is_null else ke.value


def _wacc_from_packet(packet: Any) -> float | None:
    """CAPM cost of equity + synthetic cost of debt, weighted."""
    rf = (packet.estimates or {}).get("risk_free_rate")
    cs = packet.capital_structure or {}
    beta = cs.get("beta")
    if rf is None or beta is None:
        return None

    ke = ve.cost_of_equity(float(rf), float(beta), _ERP)
    if ke.is_null:
        return None

    annual = (packet.fundamentals or {}).get("annual") or []
    latest = annual[0] if annual else {}
    ebit = latest.get("ebit") or latest.get("operatingIncome")
    interest = latest.get("interest_expense")
    # Mirrors valuation.py: a real coverage ratio when we have one, else
    # its documented mid-grade 3.0x synthetic fallback.
    coverage = (float(ebit) / float(interest)
                if ebit is not None and interest not in (None, 0) else 3.0)
    kd = ve.synthetic_kd(float(rf), coverage)
    if kd.is_null:
        return None

    equity = cs.get("market_cap")
    if equity is None:
        # FMP leaves market_cap unset on some tickers; rebuild it from the
        # newest close (FMP returns bars newest-first, so the last row is
        # the *oldest* one) and diluted shares.
        shares = cs.get("diluted_shares")
        daily = getattr(getattr(packet, "market_data", None), "daily", None) or []
        price = max(daily, key=lambda r: r.date).close if daily else None
        if price and shares:
            equity = float(price) * float(shares)
    if equity is None:
        return None

    pretax, tax = latest.get("income_before_tax"), latest.get("income_tax_expense")
    if pretax in (None, 0) or tax is None:
        # No reported tax figures, no WACC. Substituting a 21% statutory
        # rate would put an invented number under every ROIC, spread and
        # EVA metric in Business — MISSING_DATA_POLICY.md's whole point
        # is that the absence has to propagate instead.
        return None
    tax_rate = max(0.0, min(float(tax) / float(pretax), 0.5))

    # `capital_structure.total_debt` is unset on some tickers (NVDA among
    # them) even though the balance sheet in `fundamentals.annual`
    # reports it. Read the filed figure rather than treat the gap as
    # zero debt, which would understate WACC on a levered company.
    total_debt = cs.get("total_debt")
    if total_debt is None:
        total_debt = latest.get("total_debt")
    if total_debt is None:
        return None
    w = ve.wacc(float(equity), float(total_debt), ke.value, kd.value, tax_rate)
    return w.value if w.is_valid else None



#: Forecast horizon and default terminal growth for the margin-of-safety
#: derivation. Both mirror valuation.py's own defaults so the two readings of
#: the same company cannot drift apart silently.
_MOS_FORECAST_YEARS = 5
_MOS_TERMINAL_GROWTH = 0.025


def _margin_of_safety_from_packet(packet: Any) -> float | None:
    """VAL-MOS-040 `(Estimated value - Price) / Estimated value`, from the
    common packet.

    risk/SCORING.md's valuation-compression row says "Use valuation-agent
    packet; do not duplicate valuation score". This is the same cross-agent
    pattern Victor already defines for financial's `wacc` ("valuation agent
    input packet") and market's `roic_reinvestment` ("validated specialist
    packet"), and it is resolved the same way `_wacc_from_packet` resolves
    that one: derive the figure from the COMMON packet using the shared
    valuation engine, never by reading the valuation specialist's output.

    That keeps all three of Victor's rules intact at once --
    ORCHESTRATION.md Phase 2 ("Run the six agents independently"),
    CLAUDE.md ("Ningun agente ve ni altera el score de otro"), and the
    SCORING.md row itself: what crosses is a *number* (a price-to-value
    ratio), never a score.

    Mirrors valuation.py's base scenario: fundamental growth
    (`reinvestment rate * ROIC`, VAL-REINV-043), the latest operating
    margin, and the constant-growth model the engine shares with
    reverse-DCF and scenarios. Returns None -- never a guess -- whenever any
    input is missing.
    """
    # INDUSTRY_ADAPTERS.md names a different primary model for banks,
    # insurers, REITs and biotech ("Prefer excess-return or dividend-discount
    # valuation"; "NAV, and cap-rate valuation"), and valuation.py refuses
    # those securities outright with ADAPTER_UNSUPPORTED rather than running
    # an FCFF DCF the methodology forbids. This derivation is the same model,
    # so it has to refuse the same securities: left unguarded it priced JPM at
    # a +831% margin of safety, an FCFF valuation of a bank whose "debt" is
    # deposits.
    adapter = getattr(getattr(packet, "analysis", None), "industry_adapter", None)
    if _adapters.replaces_model(adapter):
        return None

    wacc_value = _wacc_from_packet(packet)
    if wacc_value is None:
        return None

    annual = (packet.fundamentals or {}).get("annual") or []
    if not annual:
        return None
    latest = annual[0]
    facts = getattr(packet, "facts_table", None) or {}

    def _fact(key: str) -> float | None:
        v = facts.get(key)
        value = getattr(v, "value", None) if v is not None else None
        return float(value) if isinstance(value, (int, float)) else None

    def _num(row: dict, key: str) -> float | None:
        v = row.get(key)
        return float(v) if isinstance(v, (int, float)) else None

    revenue0 = _fact("revenue") or _num(latest, "revenue")
    price = _fact("price")
    shares = _fact("diluted_shares") or _num(latest, "diluted_shares")
    ebit = _num(latest, "ebit")
    if not revenue0 or not price or not shares or ebit is None:
        return None

    cash = _fact("cash") or _num(latest, "cash") or 0.0
    total_debt = _fact("total_debt") or _num(latest, "total_debt") or 0.0
    net_debt = total_debt - cash

    tax_rate = _taxes.tax_rate_or_statutory(
        _num(latest, "income_before_tax"), _num(latest, "income_tax_expense"))

    # Invested capital is a BOOK reconciliation (DATA_DICTIONARY.md: "debt
    # plus equity minus excess cash") -- the same correction valuation.py
    # carries. Market cap here would collapse ROIC and with it the growth.
    equity_book = _num(latest, "total_equity")
    if equity_book is None:
        return None
    nopat_v = ve.nopat(ebit, tax_rate)
    if nopat_v.is_null or nopat_v.value == 0:
        return None
    prior = annual[1] if len(annual) > 1 else None
    ic_end = total_debt + equity_book - cash
    if prior is not None:
        debt_p, eq_p = _num(prior, "total_debt"), _num(prior, "total_equity")
        cash_p = _num(prior, "cash") or 0.0
        ic = ((debt_p + eq_p - cash_p) + ic_end) / 2 if debt_p is not None and eq_p is not None else ic_end
    else:
        ic = ic_end
    if ic <= 0:
        return None
    roic_v = ve.roic(nopat_v.value, ic)
    if roic_v.is_null:
        return None

    capex = abs(_num(latest, "capex") or 0.0)
    growth_v = ve.fundamental_growth(capex / nopat_v.value, roic_v.value)
    if growth_v.is_null:
        return None

    # DECISION_RULES.md consistency rule 3: terminal growth must stay below
    # WACC. valuation.py clamps rather than refusing; do the same.
    tv_growth = _MOS_TERMINAL_GROWTH
    if tv_growth >= wacc_value:
        tv_growth = max(0.0, wacc_value - 0.005)
    if tv_growth >= wacc_value:
        return None

    try:
        base_per_share = ve._constant_growth_per_share(
            growth=growth_v.value, margin=ebit / revenue0, wacc_value=wacc_value,
            tv_growth=tv_growth, revenue0=revenue0, tax_rate=tax_rate,
            roic_value=roic_v.value, years=_MOS_FORECAST_YEARS,
            shares=shares, net_debt=net_debt,
        )
    except (ValueError, ZeroDivisionError):
        return None
    if base_per_share == 0:
        return None
    mos = ve.margin_of_safety(base_per_share, price)
    return mos.value if mos.is_valid else None


def _segment_shares(fmp: Any, ticker: str) -> list[float] | None:
    """Latest fiscal year's product revenue split, as 0-1 decimals."""
    return _shares_from_segmentation(fmp.revenue_product_segmentation(ticker))


def _shares_from_segmentation(rows: Any) -> list[float] | None:
    """Latest fiscal year's revenue split, as 0-1 decimals, sorted desc."""
    if not isinstance(rows, list) or not rows:
        return None
    data = (rows[0] or {}).get("data") or {}
    values = [float(v) for v in data.values() if isinstance(v, (int, float)) and v > 0]
    total = sum(values)
    # One line item is not a split — reporting it as a 100% share would
    # fake concentration evidence the filing never gave.
    if total <= 0 or len(values) < 2:
        return None
    return sorted((v / total for v in values), reverse=True)


def _named_segment_revenue(rows: Any) -> dict[str, float] | None:
    """Latest fiscal year's revenue by segment NAME, for VAL-SOTP-026.

    `_shares_from_segmentation` sorts the same payload into bare decimals,
    which is all the concentration metrics (BUS-HHI-005, RSK-PROD-018) need.
    A sum-of-the-parts needs to know *which* segment each figure belongs to,
    because its whole point is that each one carries its own multiple --
    FORMULAS.md's "avoid applying a multiple to consolidated metrics twice",
    and the matrix's "one blended multiple without segment logic".
    """
    if not isinstance(rows, list) or not rows:
        return None
    data = (rows[0] or {}).get("data") or {}
    named = {str(k): float(v) for k, v in data.items()
             if isinstance(v, (int, float)) and v > 0}
    # One line item is not a split, same rule as the shares parser.
    return named if len(named) >= 2 else None


def _segmento_del_mercado(segmentos: Any, patrones: Any) -> tuple[str, float] | None:
    """El segmento cuyo nombre encaja con el mercado del TAM.

    Los nombres los pone cada emisor, no una norma: NVDA y AMD dicen
    "Data Center", Intel "Data Center Group". Por eso se casa por PATRÓN y
    no por igualdad. Si encajan varios, no se elige: dos segmentos que
    parecen el mercado significa que el patrón está mal escrito, y sumarlos
    o quedarse con el mayor sería inventar la respuesta.
    """
    if not isinstance(segmentos, dict) or not segmentos:
        return None
    pats = [str(p).lower() for p in (patrones or []) if str(p).strip()]
    if not pats:
        return None
    encajan = [(n, v) for n, v in segmentos.items()
               if isinstance(v, (int, float)) and v > 0
               and any(p in str(n).lower() for p in pats)]
    if len(encajan) != 1:
        if len(encajan) > 1:
            logger.warning("varios segmentos encajan con %s: %s — no se elige uno",
                           pats, [n for n, _ in encajan])
        return None
    return encajan[0]


_REGIONES_US = ("united states", "u.s.", "us ", "usa", "north america",
                "americas", "domestic")


def _ingreso_en_el_ambito(fmp: Any, ticker: str) -> tuple[float, str] | None:
    """Los ingresos de la empresa en EE.UU., del desglose geográfico del 10-K.

    Hace falta cuando el denominador es doméstico. Las encuestas del Census
    miden EE.UU.; los ingresos de un emisor son mundiales. Dividir uno entre
    otro es el error de capa de Gartner/NVDA otra vez, sólo que en el eje
    geográfico: a AAPL le salía 1.900% de participación.

    Es un NUMERADOR APROXIMADO y hay que leerlo como tal: "Americas" de AAPL
    y "North America" de KO incluyen Canadá y Latinoamérica, y son ingresos de
    TODA la empresa en la región, no del producto que compite en ese mercado.
    FMP no publica el cruce producto × geografía porque el 10-K tampoco lo
    trae. Se prefiere aproximado y declarado a exacto en el ámbito equivocado.
    """
    try:
        filas = fmp.revenue_geographic_segmentation(ticker)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(filas, list) or not filas:
        return None
    datos = (filas[0] or {}).get("data") or {}
    if not isinstance(datos, dict):
        return None
    encajan = [(n, v) for n, v in datos.items()
               if isinstance(v, (int, float)) and v > 0
               and any(r in str(n).lower() for r in _REGIONES_US)]
    if len(encajan) != 1:
        return None
    return float(encajan[0][1]), encajan[0][0]


def _share_automatico(fmp: Any, ticker: str, segmentos: dict,
                      tam: float | None, tam_history: Any,
                      patrones: Any, ambito: str | None = None) -> dict[str, Any]:
    """Participación de mercado calculada SOLA, sin que nadie la declare.

    Es el paso que faltaba para que analizar un ticker nuevo no exigiera
    trabajo manual. El denominador ya lo comparte la industria; el numerador
    —el ingreso del segmento que compite en ese mercado— lo publica FMP a
    partir del propio 10-K. Dividirlos no es imputar: son dos cifras
    reportadas en la misma capa de la cadena de valor.

    `share_history` importa más que el nivel. `MKT-SHDELTA-007` mide si la
    empresa GANA o PIERDE captura, y cualquier sesgo del segmento (NVDA mete
    networking en "Data Center") aparece en los dos años por igual y se
    cancela al restar. El nivel se queda como cota superior.

    Devuelve `{}` en cuanto algo no cuadre. Nada aquí adivina: sin patrón,
    sin segmento único, sin TAM o sin dos años comparables, la métrica sigue
    NOT_SCORABLE, que es la respuesta honesta.
    """
    if not tam or tam <= 0:
        return {}

    # El numerador se elige por el ÁMBITO del denominador, no por comodidad.
    # Un TAM doméstico (las encuestas del Census, vía FRED) pide los ingresos
    # domésticos; uno mundial (un estudio de industria) pide el segmento de
    # producto. Mezclarlos daba a AAPL un 1.900% de participación.
    if (ambito or "").upper() == "US":
        dom = _ingreso_en_el_ambito(fmp, ticker)
        if not dom:
            logger.info("%s: TAM domestico sin desglose geografico reportado — "
                        "sin participacion", ticker)
            return {}
        actual: tuple[str, float] = (dom[1], dom[0])
    else:
        encontrado = _segmento_del_mercado(segmentos, patrones)
        if not encontrado:
            return {}
        actual = encontrado

    # Una participación por encima del 100% no es una empresa dominante: es la
    # prueba de que el denominador no era el suyo. Se rechaza en vez de
    # recortarse a 100%, porque recortar esconde el error en vez de mostrarlo.
    if actual[1] > tam:
        logger.warning("%s: participacion implicita %.0f%% > 100%% — el "
                       "denominador no es el de esta empresa, se descarta",
                       ticker, actual[1] / tam * 100)
        return {}

    salida: dict[str, Any] = {
        "share": {"company_sales": actual[1], "total_market_sales": float(tam)}}

    # El historial necesita el MISMO segmento un año antes y el TAM de ese
    # año. Si falta cualquiera de los dos, se entrega sólo el nivel.
    historia = list(tam_history or [])
    if len(historia) < 2:
        return salida
    try:
        # El año anterior se lee del MISMO desglose que el actual. Tomarlo del
        # de producto cuando el nivel salió del geográfico compararía dos
        # numeradores distintos, y la variación —que es lo que de verdad mide
        # `MKT-SHDELTA-007`— saldría de restar peras y manzanas.
        domestico = (ambito or "").upper() == "US"
        filas = (fmp.revenue_geographic_segmentation(ticker) if domestico
                 else fmp.revenue_product_segmentation(ticker))
        if not isinstance(filas, list) or len(filas) < 2:
            return salida
        previos = (filas[1] or {}).get("data") or {}
        if domestico:
            encajan = [(n, v) for n, v in previos.items()
                       if isinstance(v, (int, float)) and v > 0
                       and any(r in str(n).lower() for r in _REGIONES_US)]
            anterior = (encajan[0][0], encajan[0][1]) if len(encajan) == 1 else None
        else:
            anterior = _segmento_del_mercado(previos, patrones)
        if not anterior:
            return salida
        tam_anterior = float(historia[-2])
        if anterior[1] > tam_anterior:
            return salida  # mismo motivo que arriba: denominador ajeno
        if tam_anterior <= 0:
            return salida
        salida["share_history"] = [round(anterior[1] / tam_anterior, 6),
                                   round(actual[1] / float(tam), 6)]
    except Exception:
        logger.warning("historial de participación no disponible para %s",
                       ticker, exc_info=True)
    return salida


def _income_statement_keys(packet: Any) -> dict[str, Any]:
    """Risk reads these from the overlay, but they are plain income-
    statement lines the packet already carries."""
    annual = (packet.fundamentals or {}).get("annual") or []
    if not annual:
        return {}
    latest = annual[0]
    prior = annual[1] if len(annual) > 1 else {}
    out: dict[str, Any] = {}

    def put(key: str, row: dict, field: str) -> None:
        v = row.get(field)
        if v is not None:
            out[key] = float(v)

    put("interest_expense", latest, "interest_expense")
    # The maturity wall for the coming year is the balance sheet's own
    # short-term debt line. PROHIBITED_IMPUTATION bars *inferring* debt
    # maturity amounts; this is the reported figure, which is what the
    # metric asks for. `expected_fcf_before_maturity` is deliberately
    # left unset — projecting next year's cash flow would be exactly the
    # assumption the policy forbids, and risk.py already defaults it to
    # zero, which is the conservative reading.
    put("debt_due", latest, "short_term_debt")
    put("sga", latest, "sga")
    put("sga_prior", prior, "sga")
    # `depreciation` is NOT set from D&A here on purpose. Beneish DEPI wants
    # depreciation alone, and risk.py already falls back to the packet's D&A
    # field -- while raising DA_PROXY_FOR_DEPRECIATION to say it did. Filling
    # the key from D&A here made that flag (`overlay.get("depreciation") is
    # None`) permanently False, so the substitution happened silently on every
    # ticker and the warning was dead code. DATA_POLICY.md: "Do not silently
    # change ... denominator definitions." The true standalone tag arrives via
    # `Packet.xbrl_inputs` when the company files it.
    return out


def _balance_sheet_keys(fmp: Any, ticker: str) -> dict[str, Any]:
    """PP&E and retained earnings, for the earnings-quality checks."""
    rows = fmp.balance_annual(ticker, limit=2)
    if not isinstance(rows, list) or not rows:
        return {}
    latest = rows[0]
    prior = rows[1] if len(rows) > 1 else {}
    out: dict[str, Any] = {}
    # Same reason as `depreciation` above: Beneish AQI wants GROSS PP&E, and
    # risk.py falls back to the packet's net figure while raising
    # PPE_NET_PROXY_FOR_GROSS_PPE. Setting the key from the net balance here
    # kept that flag False forever, so net stood in for gross with nothing
    # said. The gross tag arrives via `Packet.xbrl_inputs` when filed.
    if latest.get("retainedEarnings") is not None:
        out["retained_earnings"] = float(latest["retainedEarnings"])
    return out


def _historical_multiples(packet: Any) -> list[float] | None:
    """Trailing P/E on each session in the price history.

    VAL-ZHIST-035 z-scores today's multiple against its own history, and
    the whole historical/peer dimension rests on it. Both inputs are
    reported: the daily close from the packet's market data, and diluted
    EPS from the income statement. MISSING_DATA_POLICY step 3 allows a
    figure calculated from validated components.

    Each session is priced against the newest fiscal year that had
    *ended* by that date. Using a later year's earnings against an
    earlier price would score the multiple against results the market
    could not yet have seen.
    """
    daily = getattr(getattr(packet, "market_data", None), "daily", None) or []
    annual = (packet.fundamentals or {}).get("annual") or []
    if len(daily) < 60 or not annual:
        return None

    eps_by_end: list[tuple[str, float]] = []
    for row in annual:
        end, ni = row.get("date"), row.get("net_income")
        shares = row.get("diluted_shares")
        if end and ni is not None and shares:
            eps = float(ni) / float(shares)
            if eps > 0:  # a negative multiple carries no valuation signal
                eps_by_end.append((end, eps))
    if not eps_by_end:
        return None
    eps_by_end.sort()

    out: list[float] = []
    for bar in sorted(daily, key=lambda r: r.date):
        eps = next((e for end, e in reversed(eps_by_end) if end <= bar.date), None)
        if eps:
            out.append(float(bar.close) / eps)
    return out or None


def _rs_universe(fmp: Any, packet: Any) -> list[dict] | None:
    """Each peer's relative strength vs the same benchmark, as of today.

    Feeds TECH-RSC-013, which otherwise stays NOT_SCORABLE and drags the
    whole relative-strength dimension under its 70% floor (2 of 3
    metrics = 67%) even though RS and sector-RS both computed fine.

    This is a point-in-time snapshot — today's RS for today's peers — so
    it carries no survivorship claim. It would be invalid to reuse for a
    historical backtest, which is what the metric's
    "POINT_IN_TIME_REQUIRED" guard is warning about.

    `technical.py` scores the composite off its 63-day RS in all four
    windows, so the universe is built the same way: comparing a 63-day
    figure against a 21/126/252-day distribution would rank the security
    against a yardstick it was never measured on.
    """
    import pandas as pd

    from wbj.engines import indicators as ind

    bench = packet.market_data.benchmark or []
    peers = (packet.estimates or {}).get("peers") or []
    symbols = [p.get("symbol") for p in peers if isinstance(p, dict) and p.get("symbol")]
    if len(bench) < 64 or len(symbols) < _MIN_PEERS:
        return None

    bench_close = pd.Series([r.close for r in sorted(bench, key=lambda r: r.date)])
    rows: list[dict] = []
    for sym in symbols:
        try:
            bars = fmp.ohlcv_daily(sym) or []
            if len(bars) < 64:
                continue
            closes = pd.Series([float(b["close"]) for b in
                                sorted(bars, key=lambda b: b["date"])])
            n = min(len(closes), len(bench_close))
            rs = ind.relative_strength(closes.tail(n).reset_index(drop=True),
                                       bench_close.tail(n).reset_index(drop=True), 63)
            rs = rs.dropna()
            if rs.empty:
                continue
            v = float(rs.iloc[-1])
            rows.append({"RS21": v, "RS63": v, "RS126": v, "RS252": v})
        except Exception:
            logger.warning("peer RS failed for %s; skipping peer", sym, exc_info=True)

    return rows if len(rows) >= _MIN_PEERS else None



#: What each analyst-supplied block must contain to be usable. A block
#: with the right name but the wrong keys reads downstream as "no data",
#: which is indistinguishable from never having written the file at all
#: -- so the analyst gets a silent zero and no way to tell why. These
#: shapes let the loader say so instead.
_ANALYST_BLOCK_SHAPES: dict[str, frozenset[str]] = {
    "retention": frozenset({"begin", "expansion", "contraction", "churn"}),
    "churn": frozenset({"lost", "begin_customers"}),
    "customer_economics": frozenset({"arpu", "gross_margin", "customer_life_years"}),
}


def _analyst_block_warnings(data: dict) -> list[str]:
    """Name every analyst block that will be silently ignored.

    The metrics take component inputs, not finished ratios: net revenue
    retention is computed from begin/expansion/contraction/churn, not
    read from an `nrr` key. Writing the ratio directly is the natural
    mistake, and it produced a dimension that scored 0/3 while looking
    correctly filled in.
    """
    out: list[str] = []
    for block, required in _ANALYST_BLOCK_SHAPES.items():
        value = data.get(block)
        if not isinstance(value, dict) or not value:
            continue
        missing = required - value.keys()
        if missing:
            out.append(
                f"analyst input {block!r} ignored: missing {sorted(missing)}; "
                f"got {sorted(value)}. These metrics take component inputs, not "
                f"finished ratios."
            )
    return out



#: The macro driver RSK-CYC-034 is regressed against. FORMULAS.md requires an
#: "economic rationale": nominal GDP is the broadest measure of the nominal
#: demand a company's own nominal revenue is earned out of, so revenue growth
#: on GDP growth is the one pairing that needs no company-specific story. A
#: sector-specific driver (oil for an energy name, rates for a bank) would be
#: better still, but choosing one per company is judgment this engine does not
#: have -- an analyst can override with overlay["macro_series"].
_MACRO_SERIES_ID = "GDP"

#: FORMULAS.md: "Require ... sufficient observations". `macro_sensitivity_beta`
#: itself refuses below 6 points; DATASET.md asks for 5 years of history, so a
#: 5-observation growth series (6 annual revenue points) is the floor here too.
_MACRO_MIN_OBSERVATIONS = 6


def _macro_pair(packet: Any, fred: Any) -> tuple[list[float], list[float]] | None:
    """(company_series, macro_series) of year-over-year growth rates.

    RSK-CYC-034 is the "OLS coefficient of company metric changes ... on
    macro-factor changes". Both sides are growth rates, not levels: a
    regression of revenue *levels* on GDP *levels* measures shared trend, not
    sensitivity, and would report a large beta for any growing company.

    Annual revenue is paired with the calendar year of the fiscal period, so
    each company observation lands against the macro year it was earned in.
    """
    if fred is None or not getattr(fred, "available", False):
        return None
    annual = list(reversed((getattr(packet, "fundamentals", {}) or {}).get("annual") or []))
    revenue_by_year: dict[int, float] = {}
    for row in annual:
        # `fiscal_year` reads calendarYear/fiscalYear/date in turn -- the
        # packet's canonical rows carry only `date` for some providers, and
        # reading `calendarYear` alone returned None for every row here.
        year = fiscal_year(row)
        revenue = row.get("revenue")
        if year and isinstance(revenue, (int, float)) and revenue:
            revenue_by_year[int(year)] = float(revenue)
    if len(revenue_by_year) < _MACRO_MIN_OBSERVATIONS + 1:
        return None

    try:
        payload = fred.series(_MACRO_SERIES_ID, limit=200)
    except Exception:
        logger.warning("%s unavailable; macro sensitivity will be omitted",
                       _MACRO_SERIES_ID, exc_info=True)
        return None
    observations = (payload or {}).get("observations") if isinstance(payload, dict) else None
    if not observations:
        return None

    # GDP is quarterly; average the quarters of each year so one company
    # observation meets one macro observation.
    by_year: dict[int, list[float]] = {}
    for o in observations:
        date, value = str(o.get("date") or ""), o.get("value")
        try:
            by_year.setdefault(int(date[:4]), []).append(float(value))
        except (TypeError, ValueError):
            continue
    macro_by_year = {y: sum(v) / len(v) for y, v in by_year.items() if v}

    company, macro = [], []
    for year in sorted(revenue_by_year):
        prior = year - 1
        if prior not in revenue_by_year or year not in macro_by_year or prior not in macro_by_year:
            continue
        if revenue_by_year[prior] == 0 or macro_by_year[prior] == 0:
            continue
        company.append(revenue_by_year[year] / revenue_by_year[prior] - 1.0)
        macro.append(macro_by_year[year] / macro_by_year[prior] - 1.0)
    if len(company) < _MACRO_MIN_OBSERVATIONS:
        return None
    return company, macro


def _recession_years(fred: Any) -> list[int]:
    """Calendar years the NBER dated as recessionary, from FRED's USREC.

    BUS-STAB-009 asks for a "recession-year drawdown" alongside the
    standard deviation and range, and nothing supplied the calendar it
    needs. USREC is the NBER indicator itself — a monthly 0/1 series — so
    the years come from the authority that declares them rather than from
    a judgement about which years felt like recessions.
    """
    if fred is None or not getattr(fred, "available", False):
        return []
    try:
        payload = fred.series("USREC", limit=1200)
    except Exception:
        logger.warning("USREC unavailable; recession-year drawdown will be omitted",
                       exc_info=True)
        return []
    observations = (payload or {}).get("observations") if isinstance(payload, dict) else None
    if not observations:
        return []
    years = {
        int(o["date"][:4])
        for o in observations
        if o.get("value") == "1" and (o.get("date") or "")[:4].isdigit()
    }
    return sorted(years)


def _slug_industria(nombre: str) -> str:
    """`Semiconductors` -> `semiconductors`, `Banks - Diversified` -> `banks-diversified`."""
    limpio = re.sub(r"[^a-z0-9]+", "-", str(nombre or "").lower()).strip("-")
    return limpio


def _overlay_industria(settings: Any, industria: str | None,
                       ticker: str = "") -> dict[str, Any]:
    """Entradas compartidas por INDUSTRIA, de `Entradas/_industrias/<slug>.json`.

    El TAM no es propiedad de una empresa: es del mercado en el que compite.
    El de Omdia para chips de datacenter ($207.000M en 2025) cubre a NVDA, a
    AMD con sus Instinct, a Broadcom y a Marvell por igual — el comunicado
    los nombra a todos. Guardado sólo en `Entradas/NVDA.json`, AMD salía con
    `tam=ninguno` y Market en 1.82 mientras NVDA sacaba 4.87, no porque el
    dato faltara sino porque estaba escrito en el archivo de otro.

    El archivo del TICKER siempre gana: lo de la industria son cimientos, no
    una imposición. Una empresa con un mercado propio —o con un desglose
    mejor— lo declara en el suyo y esto no la toca.

    La validación es la misma: un `tam` sin `tam_source` y sin
    `tam_source_tier` 1-4 se cae igual aquí que en el archivo del ticker.
    Compartir un dato no lo exime de estar atribuido.
    """
    import json
    from pathlib import Path

    if not industria:
        return {}
    root = getattr(settings, "inputs_dir", None) or (
        Path(getattr(settings, "reports_dir", ".")).parent / "Entradas")
    path = Path(root) / "_industrias" / f"{_slug_industria(industria)}.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("no se pudo leer el TAM de industria en %s", path, exc_info=True)
        return {}
    if not isinstance(data, dict):
        return {}

    # Lista explícita de a quién cubre. Una industria de GICS es más ancha
    # que un mercado: `Semiconductors` mete en la misma bolsa a NVIDIA, que
    # vende aceleradores, y a Micron, que vende memoria. Sin esta lista MU
    # heredaba el TAM de aceleradores de Omdia — un denominador que no es el
    # suyo — y su participación habría salido absurdamente pequeña en vez de
    # ausente. Un número equivocado es peor que un hueco: el hueco se ve.
    #
    # Sin `_aplica_a` el archivo cubre a toda la industria, que es lo
    # razonable cuando el mercado sí coincide con la clasificación.
    cubre = data.get("_aplica_a")
    if isinstance(cubre, list) and cubre:
        if ticker.upper() not in {str(t).upper() for t in cubre}:
            return {}

    # `_segmento_patrones` lleva guion bajo pero NO es un comentario: es la
    # regla que dice cómo reconocer, en la segmentación del 10-K, el segmento
    # que compite en este mercado. Se rescata antes de barrer las notas.
    patrones = data.get("_segmento_patrones")
    ambito = data.get("_ambito")

    for key in [k for k in data if k.startswith("_")]:
        data.pop(key)
    if data.get("tam") is not None:
        if not data.get("tam_source") or data.get("tam_source_tier") not in (1, 2, 3, 4):
            logger.warning("TAM de industria en %s descartado: falta tam_source "
                           "o tam_source_tier 1-4", path)
            for k in ("tam", "tam_history", "tam_source", "tam_source_tier"):
                data.pop(k, None)
    fuera = {k: v for k, v in data.items() if v is not None}
    # Sólo viaja si hay TAM: sin denominador no hay participación que calcular.
    if isinstance(patrones, list) and patrones and fuera.get("tam"):
        fuera["_segmento_patrones"] = patrones
    if ambito and fuera.get("tam"):
        fuera["_ambito"] = ambito
    return fuera


def _manual_overlay(settings: Any, ticker: str,
                    industria: str | None = None) -> dict[str, Any]:
    """Analyst-supplied inputs for `Entradas/<TICKER>.json`.

    Se apoya sobre `Entradas/_industrias/<industria>.json` cuando existe: el
    TAM y su fuente son del MERCADO, no de la empresa. Lo del ticker gana
    siempre — ver `_overlay_industria`.

    Market sizing has no free structured source, and the methodology is
    explicit about what that means: DECISION_RULES.md's tier 5 is an
    "Unattributed market-size claim — 0; not scorable", and AGENT.md's
    boundaries say plainly "Do not score an unattributed market-size
    claim". So the engine must never invent a TAM. What it *can* do is
    accept one an analyst attributes to a source, which AGENT.md's
    no-speculation rule allows as an "explicitly disclosed assumption",
    and which then scores at the confidence its tier earns (tier 4,
    issuer estimate, is 45 — below the 60 that caps the dimension at 6).

    A `tam` without both `tam_source` and a 1-4 `tam_source_tier` is
    dropped and said so out loud. Refusing it is the documented
    behaviour, not a limitation to work around.
    """
    import json
    from pathlib import Path

    root = getattr(settings, "inputs_dir", None) or (
        Path(getattr(settings, "reports_dir", ".")).parent / "Entradas")
    base = _overlay_industria(settings, industria, ticker)
    path = Path(root) / f"{ticker.upper()}.json"
    if not path.is_file():
        return base

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("could not read analyst inputs at %s", path, exc_info=True)
        return {}
    if not isinstance(data, dict):
        return {}

    # Every underscore-prefixed key is a comment, not data. The file is
    # hand-written and needs room to explain itself -- `EJEMPLO.NVDA.json`
    # carries thirty-odd notes -- and popping only the exact name `_comment`
    # let all the numbered ones through into the overlay. Nothing reads them,
    # but an overlay is audit-trail material and prose sitting in it can be
    # mistaken for a supplied figure.
    for key in [k for k in data if k.startswith("_")]:
        data.pop(key)
    # A template must never score. `Entradas/EJEMPLO.*.json` ships with
    # illustrative figures to document the shape of the file; loading
    # them would put invented market sizing behind a real number.
    if path.name.upper().startswith("EJEMPLO"):
        return {}
    # `is not None`, not `in`: a skeleton file ships every key as null so the
    # analyst can fill it in place, and a null TAM is not an unattributed
    # claim -- it is no claim at all. Keying on presence dropped the whole
    # block and logged an attribution warning on every run of every ticker
    # that had a skeleton but no market sizing yet.
    if data.get("tam") is not None:
        tier = data.get("tam_source_tier")
        if not data.get("tam_source") or tier not in (1, 2, 3, 4):
            logger.warning(
                "TAM in %s dropped: needs tam_source and tam_source_tier 1-4 "
                "(tier 5 is an unattributed claim, which DECISION_RULES.md "
                "scores at 0)", path)
            for k in ("tam", "tam_history", "tam_source", "tam_source_tier"):
                data.pop(k, None)

    # Victor's `DATASET.md` names these fields, and the engine grew different
    # key names for three of them. CLAUDE.md step 2 validates the packet
    # against that contract, so an analyst writing the contract's own name --
    # `management_guidance_history` rather than `guidance_history` -- must be
    # honoured rather than silently ignored.
    #
    # The engine key wins when both appear: it is what every consumer reads,
    # and quietly overwriting a figure already under it would be worse than
    # ignoring the duplicate. The collision is logged either way.
    from wbj.entradas import VICTOR_FIELD_ALIASES

    for victor_name, engine_key in VICTOR_FIELD_ALIASES.items():
        if data.get(victor_name) is None:
            continue
        if data.get(engine_key) is not None:
            logger.warning("%s: both `%s` and `%s` are set; keeping `%s`",
                           path.name, victor_name, engine_key, engine_key)
            continue
        data[engine_key] = data[victor_name]

    # A null is "I have not filled this in", which must behave exactly like no
    # file at all. Carrying it through breaks the engine outright: the read
    # pattern all over the specialists is `float(overlay.get(k, default))`, and
    # a default only applies when the key is ABSENT -- `float(None)` raises.
    #
    # The skeleton ships every key null, so once it covered the full set of
    # inputs a freshly-analysed ticker crashed the financial specialist on
    # `equity_issuance` alone, with eleven more keys behind it (`lease_charge`,
    # `tv_growth`, `pretax_kd`, `forecast_years`, ...). Stripping here fixes
    # every call site at once and is what "inert skeleton" always meant.
    data = {k: v for k, v in data.items() if v is not None}

    for warning in _analyst_block_warnings(data):
        logger.warning("%s: %s", path.name, warning)
        data.setdefault("analyst_input_warnings", []).append(warning)
    # La industria son cimientos; lo que el analista escribió para ESTE
    # ticker va encima y gana en cada clave que traiga.
    return {**base, **data}


def _cached_extract(cache: Any, ticker: str, filing: dict, kind: str,
                    compute: Any) -> Any:
    """Extract once per filing, then reuse the answer forever.

    A filing is immutable, so reading it twice and getting two different
    answers is a defect, not variance to be tolerated. Re-extracting was
    doing exactly that: one run found two catalysts, the next found one,
    and the same ticker scored 4.71 then 4.67 on the same day.

    The key carries `_EXTRACT_VERSION` so improving the extractor
    invalidates what it produced, rather than leaving old answers to
    outlive the logic that made them.
    """
    key = f"extract_{kind}_v{_EXTRACT_VERSION}_{filing.get('accession')}"
    hit = cache.get(ticker, key)
    if isinstance(hit, dict) and "value" in hit:
        return hit["value"]
    value = compute()
    cache.put(ticker, key, {"value": value})
    return value


def _xbrl_series(edgar: Any, cik: int, tag: str,
                 unit: str = "USD") -> list[tuple[str, float]]:
    """One us-gaap tag's history from EDGAR companyfacts, oldest first.

    Companies tag this in XBRL, so the figure is available as structured
    data at no cost and with no model in the loop. Same filing, same
    tier-1 evidence — just read from the machine-readable copy.

    Duplicates are collapsed keeping the latest-filed value for a period,
    since a 10-K restates what a 10-Q first reported.
    """
    facts = (edgar.companyfacts(cik) or {}).get("facts", {}).get("us-gaap", {})
    rows = (facts.get(tag) or {}).get("units", {}).get(unit) or []
    by_period: dict[str, tuple[str, float]] = {}
    for r in rows:
        end, val, filed = r.get("end"), r.get("val"), r.get("filed") or ""
        if not end or val is None:
            continue
        prior = by_period.get(end)
        if prior is None or filed >= prior[0]:
            by_period[end] = (filed, float(val))
    return [(end, v) for end, (_, v) in sorted(by_period.items())]


#: A fiscal year, loosely: 52/53-week filers land near 364, calendar filers on
#: 365/366, and a few report a stub. Wide enough for all of them, narrow enough
#: to exclude a 9-month cumulative (273) or a quarter (91).
_ANNUAL_DAYS = (340, 400)


def _xbrl_annual_duration_series(edgar: Any, cik: int, tag: str,
                                 unit: str = "USD") -> list[tuple[str, float]]:
    """A *duration* tag's annual history from EDGAR companyfacts, oldest first.

    `_xbrl_series` keys on `end` alone, which is right for an instant fact
    (a balance) and wrong for a duration one: AAPL tags
    `CommonStockDividendsPerShareDeclared` for the quarter AND the
    year-to-date period ending the same day, so 2024-06-29 carries both 0.25
    and 0.73 and keying on `end` picks whichever was filed later. Filtering on
    the start-to-end span is what separates a fiscal year from a quarter.

    Duration is used rather than `fp == "FY"` because it is the semantic
    itself; the `fp` tag agrees here but is filer-supplied metadata, and a
    period's length is not.

    Duplicates within one period are collapsed keeping the latest-filed value,
    since a 10-K restates what a 10-Q first reported.
    """
    from datetime import date

    facts = (edgar.companyfacts(cik) or {}).get("facts", {}).get("us-gaap", {})
    rows = (facts.get(tag) or {}).get("units", {}).get(unit) or []
    by_period: dict[str, tuple[str, float]] = {}
    for r in rows:
        start, end, val = r.get("start"), r.get("end"), r.get("val")
        if not start or not end or val is None:
            continue
        try:
            span = (date.fromisoformat(end) - date.fromisoformat(start)).days
        except ValueError:
            continue
        if not _ANNUAL_DAYS[0] <= span <= _ANNUAL_DAYS[1]:
            continue
        filed = r.get("filed") or ""
        prior = by_period.get(end)
        if prior is None or filed >= prior[0]:
            by_period[end] = (filed, float(val))
    return [(end, v) for end, (_, v) in sorted(by_period.items())]


def _backlog_history(edgar: Any, settings: Any, cik: int,
                     latest_10k: dict | None) -> list[float] | None:
    """Contracted backlog per period, oldest first.

    MKT-BACK-015 compares the two most recent observations, and
    DATASET.md asks for eight quarters of history. One 10-K is a single
    point, so the series comes from the quarterlies, each read with the
    same quote verification.

    Periods are de-duplicated and sorted by the *report* date rather
    than the filing date: a 10-K filed after a 10-Q can still cover an
    earlier period, and growth computed on a mis-ordered series would
    invent a trend.
    """
    from wbj.extract.filing import extract_backlog
    from wbj.providers.cache import Cache

    # XBRL first: companies tag RevenueRemainingPerformanceObligation, so
    # the series is structured, free, deterministic and needs no model.
    # Reading prose is the fallback, for issuers that report a backlog
    # without tagging it.
    tagged = _xbrl_series(edgar, cik, "RevenueRemainingPerformanceObligation")
    if len(tagged) >= 2:
        return [v for _, v in tagged[-_BACKLOG_QUARTERS:]]

    docs = edgar.recent_filings_text(cik, form="10-Q", limit=_BACKLOG_QUARTERS)
    if latest_10k:
        docs = [*docs, latest_10k]

    cache = Cache(settings.cache_dir)
    ticker = f"CIK{cik:010d}"
    by_period: dict[str, float] = {}
    for doc in docs:
        period = doc.get("period") or doc.get("filing_date")
        if not period or period in by_period:
            continue
        value = _cached_extract(cache, ticker, doc, "backlog",
                                lambda d=doc: extract_backlog(d, settings))
        if value is not None:
            by_period[period] = value

    if len(by_period) < 2:
        return None
    return [v for _, v in sorted(by_period.items())]


def _estimates(fmp: Any, ticker: str) -> dict[str, Any] | None:
    """The one revision-family input FMP actually supports.

    MKT-SURP-014 is `(Actual - pre-release consensus) / |consensus|`, and
    FMP's earnings calendar carries both figures per quarter. The rest of
    the family stays absent on purpose:

    - MKT-DISP-013 needs `stdev(individual analyst estimates)`. FMP
      publishes only low/high/avg, and deriving a standard deviation
      from a range means assuming a distribution the data never stated.
    - MKT-REVBR-011 needs counts of upward *estimate* revisions. FMP's
      grades endpoints track *rating* upgrades, which is a different
      quantity — substituting it would repeat the receivables-for-
      revenue mistake the filing extractor already guards against.
    """
    rows = fmp.earnings_calendar(ticker)
    if not isinstance(rows, list):
        return None
    reported = [r for r in rows
                if r.get("epsActual") is not None and r.get("epsEstimated")]
    if not reported:
        return None
    latest = max(reported, key=lambda r: r.get("date") or "")
    return {"actual": float(latest["epsActual"]),
            "pre_release_consensus": float(latest["epsEstimated"]),
            "snapshot_before_release": True}


def _consensus_eps_growth(fmp: Any, ticker: str,
                          today: str | None = None) -> float | None:
    """VAL-PEG-028's denominator, desde el consenso de FMP.

    Esto lo daba Yahoo (`earnings_estimate.loc["0y"]["growth"]`). Yahoo
    queda FUERA por decisión de Victor: las fuentes del sistema son FMP,
    FinnHub, FRED y EDGAR, y yfinance raspa un endpoint no documentado que
    puede cambiar sin aviso — un PEG que depende de eso es un PEG que
    puede desaparecer entre dos corridas sin que nadie lo note.

    FMP publica el mismo panel por año fiscal, así que el crecimiento del
    año en curso sale de dividir el primer año FORWARD entre el último ya
    pasado — consenso contra consenso, el mismo panel a ambos lados. En
    NVDA (2026-08-03) da FY2027/FY2026 = 9.001/4.694 = **+91.8%**, contra
    el +88.6% que devolvía Yahoo: la misma magnitud medida sobre un panel
    ligeramente distinto.

    Comparar contra el EPS *reportado* del año pasado mezclaría un dato
    auditado con una estimación, que es precisamente lo que
    `NORMALIZATION_AND_RESTATEMENTS.md` manda no hacer en un mismo ratio.

    Devuelve fracción decimal (0.918), no puntos porcentuales: es lo que
    espera `valuation.py`, que multiplica por 100 al formar el PEG.
    """
    from datetime import date as _date

    rows = fmp.analyst_estimates(ticker)
    if not isinstance(rows, list) or not rows:
        return None
    today = today or _date.today().isoformat()
    con_eps = [r for r in rows if r.get("date") and r.get("epsAvg")]
    pasados = sorted((r for r in con_eps if r["date"] <= today), key=lambda r: r["date"])
    futuros = sorted((r for r in con_eps if r["date"] > today), key=lambda r: r["date"])
    if not pasados or not futuros:
        return None
    base = float(pasados[-1]["epsAvg"])
    if base <= 0:
        # Un crecimiento contra una base negativa o nula no significa nada;
        # el PEG lo trataría como una cifra buena. Mejor sin métrica.
        return None
    return float(futuros[0]["epsAvg"]) / base - 1.0


def _consensus_history(fmp: Any, settings: Any, ticker: str,
                       today: str | None = None) -> dict[str, Any] | None:
    """Build our own timestamped consensus series, then read a revision
    off it.

    MKT-REVMAG-012 is `(current consensus - prior consensus) / |prior|`,
    and DATASET.md wants that from a "consensus provider" with
    timestamped revisions. No configured provider serves one: FMP's
    analyst-estimates is a snapshot with no history, its
    estimates-revisions endpoint does not exist (404), and FinnHub's
    estimate endpoints return 403 on this plan.

    What FMP does give is today's consensus, so each run records it and
    the revision is measured against the oldest snapshot still inside
    the window. That makes this the one metric here that cannot work on
    day one — it needs `_REVISION_MIN_DAYS` of history to exist first,
    and says MISSING until then rather than comparing a number to
    itself.

    Snapshots are keyed by the estimate's own fiscal year: comparing
    this year's FY2027 estimate against last quarter's FY2026 estimate
    would read a change of subject as a revision.
    """
    from datetime import date as _date

    from wbj.providers.cache import Cache

    rows = fmp.analyst_estimates(ticker)
    if not isinstance(rows, list) or not rows:
        return None
    today = today or _date.today().isoformat()
    forward = sorted((r for r in rows
                      if r.get("date") and r["date"] > today and r.get("epsAvg")),
                     key=lambda r: r["date"])
    if not forward:
        return None
    target = forward[0]
    fiscal_year, consensus = target["date"], float(target["epsAvg"])

    cache = Cache(settings.cache_dir)
    stored = cache.get(ticker, _CONSENSUS_CACHE_KEY) or {}
    series: list[dict] = [s for s in (stored.get("series") or [])
                          if s.get("fiscal_year") == fiscal_year]

    if not any(s.get("as_of") == today for s in series):
        series.append({"as_of": today, "fiscal_year": fiscal_year,
                       "consensus": consensus})
        cache.put(ticker, _CONSENSUS_CACHE_KEY, {"series": series})

    series.sort(key=lambda s: s["as_of"])
    prior = next((s for s in series
                  if _days_between(s["as_of"], today) >= _REVISION_MIN_DAYS), None)
    if prior is None:
        return None
    return {"current_consensus": consensus,
            "prior_consensus": float(prior["consensus"]),
            "prior_as_of": prior["as_of"]}


def _days_between(start: str, end: str) -> int:
    from datetime import date as _date

    try:
        return (_date.fromisoformat(end) - _date.fromisoformat(start)).days
    except (TypeError, ValueError):
        return 0


def _ntm_revenue_estimate(fmp: Any, ticker: str) -> float | None:
    """Consensus revenue for the next unreported fiscal year.

    The denominator of MKT-COVER-016. Rows dated in the past describe
    years already reported, so the estimate has to be the next one
    forward — comparing contracted backlog against a historical estimate
    would answer nothing.
    """
    from datetime import date as _date

    rows = fmp.analyst_estimates(ticker)
    if not isinstance(rows, list):
        return None
    today = _date.today().isoformat()
    forward = sorted((r for r in rows
                      if r.get("date") and r["date"] > today and r.get("revenueAvg")),
                     key=lambda r: r["date"])
    return float(forward[0]["revenueAvg"]) if forward else None


def _peer_roic(fmp: Any, packet: Any) -> list[float] | None:
    """Each peer's own ROIC, from its own income and balance sheets."""
    return (_peer_economics(fmp, packet) or {}).get("roic")


def _peer_operating_margin(fmp: Any, packet: Any) -> list[float] | None:
    """Each peer's own operating margin.

    SCORING.md's competitive-position row names "peer ROIC/margins" as one
    input and only ROIC was compared. The margin comes from the same
    income statement the ROIC fetch already reads, so this costs no extra
    request.
    """
    return (_peer_economics(fmp, packet) or {}).get("operating_margin")


def _recession_margin_drawdown(rows: list[dict], recession_years: list[int]) -> float | None:
    """One issuer's worst operating-margin fall into a recession year.

    The measure behind DECISION_RULES.md's wide-moat condition 2
    alternative, "peer-relative resilience is in the top quartile through
    a cycle": a company whose margin barely moves through a downturn is
    more resilient than one whose margin collapses. Same construction the
    business specialist applies to the security itself, so the company
    and its peers are ranked on the same quantity.
    """
    if not recession_years:
        return None
    by_year: dict[int, float] = {}
    for row in rows:
        # Raw FMP peer rows carry `calendarYear`, not `fiscalYear`: reading
        # only the latter left `by_year` empty for every peer, so this returned
        # None each time and condition 2's peer alternative never had the eight
        # comparables SCORING_ENGINE.md requires. `wbj.core.periods` is the one
        # reading the business specialist uses, so the company and its peers
        # are placed on the same years — what this docstring already promised.
        year, revenue = fiscal_year(row), row.get("revenue")
        ebit = row.get("operatingIncome") or row.get("ebit")
        if year is None or not revenue or ebit is None:
            continue
        by_year[int(year)] = float(ebit) / float(revenue)
    drops = [by_year[y - 1] - by_year[y]
             for y in sorted(by_year)
             if y in set(recession_years) and (y - 1) in by_year]
    return max(drops) if drops else None


def _peer_economics(fmp: Any, packet: Any,
                    recession_years: list[int] | None = None
                    ) -> dict[str, list[float]] | None:
    peers = (packet.estimates or {}).get("peers") or []
    symbols = [p.get("symbol") for p in peers if isinstance(p, dict) and p.get("symbol")]
    if len(symbols) < _MIN_PEERS:
        return None

    out: list[float] = []
    margins: list[float] = []
    drawdowns: list[float] = []
    for sym in symbols:
        try:
            # The same request count as a single year — the history is
            # what condition 2's alternative branch needs, and the latest
            # row of it is what the ROIC and margin comparisons need.
            inc = fmp.income_annual(sym, limit=_ANNUAL_HISTORY_YEARS) or []
            bal = fmp.balance_annual(sym, limit=1) or []
            if not inc or not bal:
                continue
            drawdown = _recession_margin_drawdown(inc, recession_years or [])
            if drawdown is not None:
                drawdowns.append(drawdown)
            i, b = inc[0], bal[0]
            ebit = i.get("operatingIncome") or i.get("ebit")
            if ebit is None:
                continue
            pretax, tax = i.get("incomeBeforeTax"), i.get("incomeTaxExpense")
            if pretax in (None, 0) or tax is None:
                continue  # no reported rate; a statutory guess is not this peer's
            rate = max(0.0, min(float(tax) / float(pretax), 0.5))
            # A missing balance-sheet line is unknown, not zero. Reading
            # absent debt as zero flatters a peer's invested capital and
            # so its ROIC, which then shifts the percentile this company
            # is ranked against.
            debt, equity_b, cash_b = (b.get("totalDebt"),
                                      b.get("totalStockholdersEquity"),
                                      b.get("cashAndCashEquivalents"))
            if debt is None or equity_b is None or cash_b is None:
                continue
            nopat = ve.nopat(float(ebit), rate)
            ic = ve.invested_capital(float(debt), float(equity_b),
                                     float(cash_b)).financing_view
            if nopat.is_null or ic.is_null or not ic.value:
                continue
            r = ve.roic(nopat.value, ic.value)
            if r.is_valid:
                out.append(r.value)
                revenue = i.get("revenue")
                if revenue:
                    margins.append(float(ebit) / float(revenue))
        except Exception:
            logger.warning("peer economics failed for %s; skipping peer", sym, exc_info=True)

    if len(out) < _MIN_PEERS:
        return None
    result = {"roic": out}
    # SCORING_ENGINE.md needs eight valid peers for a percentile; a peer
    # whose revenue line is absent drops out of the margin set without
    # dropping out of the ROIC set.
    if len(margins) >= _MIN_PEERS:
        result["operating_margin"] = margins
    if len(drawdowns) >= _MIN_PEERS:
        result["recession_margin_drawdown"] = drawdowns
    return result


def _fred_of(settings: Any) -> Any:
    """The FRED provider, or None when no key is configured."""
    try:
        from wbj.deep import build_providers
        return build_providers(settings).fred
    except Exception:
        return None


def _active_estimate_count(fmp: Any, ticker: str,
                           today: str | None = None) -> int | None:
    """Analyst coverage for MKT-REVBR-011's ">=5 active estimates" caveat.

    `03_market_analysis/FORMULAS.md` gates the metric on *active estimates* —
    the analyst count — not on the revision counts its ratio divides. FMP
    publishes that count as `numAnalystsEps`, on a tier this plan serves;
    the FinnHub estimates endpoint, which would also carry it, returns 403.

    FMP orders the rows newest-date-FIRST, so row zero is the FARTHEST
    forward year, and coverage thins out with distance: MSFT carries one EPS
    analyst on FY2030 against thirty-one on FY2027. Reading row zero would
    report near-zero coverage for the most heavily covered names alive.
    MKT-REVBR-011's window is "30d / 90d", so the estimate under revision is
    the NEAREST fiscal period that has not ended yet.

    Returns None — never a substitute figure — when no forward row carries a
    count, which leaves `revision_breadth` on its conservative fallback.
    """
    from datetime import date as _date

    try:
        rows = [r for r in (fmp.analyst_estimates(ticker) or [])
                if isinstance(r, dict) and r.get("date")]
    except Exception:
        logger.warning("active estimate count unavailable", exc_info=True)
        return None

    cutoff = today or _date.today().isoformat()
    forward = [r for r in rows if str(r["date"]) >= cutoff]
    if not forward:
        return None
    count = min(forward, key=lambda r: str(r["date"])).get("numAnalystsEps")
    try:
        return int(count) if count else None
    except (TypeError, ValueError):
        return None


def build_overlay(packet: Any, settings: Any) -> dict[str, Any]:
    """Assemble every overlay key the packet can legitimately support.

    A key is present only when it was computed from reported data; a key
    that cannot be sourced is left out so the specialist reports
    NOT_SCORABLE rather than scoring a guess.
    """
    from wbj.providers.cache import Cache
    from wbj.providers.fmp import FMPProvider

    overlay: dict[str, Any] = {}
    ticker = getattr(getattr(packet, "security", None), "ticker", None) or ""

    try:
        wacc_value = _wacc_from_packet(packet)
        if wacc_value is not None:
            overlay["wacc"] = wacc_value
        ke_value = _cost_of_equity_from_packet(packet)
        if ke_value is not None:
            overlay["cost_of_equity"] = ke_value
    except Exception:
        logger.warning("overlay wacc unavailable", exc_info=True)

    # Income-statement lines risk.py expects via overlay but which the
    # packet already carries.
    try:
        overlay.update(_income_statement_keys(packet))
    except Exception:
        logger.warning("overlay income-statement keys unavailable", exc_info=True)

    try:
        multiples = _historical_multiples(packet)
        if multiples:
            overlay["historical_multiples"] = multiples
    except Exception:
        logger.warning("historical multiples unavailable", exc_info=True)

    # Disclosure-only figures, read from the 10-K itself (tier 1 in
    # SOURCE_HIERARCHY.md) and accepted only when quote-verified.
    try:
        from wbj.extract.filing import extract_disclosures
        from wbj.providers.edgar import EdgarProvider

        edgar = EdgarProvider(settings, Cache(settings.cache_dir))
        cik = edgar.cik_for(ticker)
        if cik:
            # A ticker whose registrant files no us-gaap facts voids every
            # EDGAR-derived overlay value at once, silently. It happens after
            # a reorganisation: SEC's own ticker map is tier 1 and points XOM
            # at ExxonMobil Holdings (CIK 2115436, 0 us-gaap tags) while the
            # 438-tag history sits under the predecessor. Resolving successor
            # to predecessor is not something to guess at, so this states the
            # condition rather than silently returning an empty overlay.
            # A declared registrant succession is data with a source, but it is
            # still a substitution: the analysis is reading a CIK the SEC's
            # ticker map does not point at. Saying so is the difference between
            # a documented override and a silent redirect.
            applied = (getattr(edgar, "overrides_applied", None) or {}).get(ticker.upper())
            if applied:
                overlay.setdefault("warnings", []).append(
                    f"EDGAR_CIK_OVERRIDE: {ticker} reads CIK {applied['cik']} instead of "
                    f"{applied['replaced_cik']}, which the SEC ticker map points at but which "
                    f"files no us-gaap XBRL. Declared in Entradas/cik_overrides.json, sourced "
                    f"to {applied['source_locator']}. Reason: {applied.get('reason', '')}"
                )
            _facts = (edgar.companyfacts(cik) or {}).get("facts") or {}
            if not _facts.get("us-gaap"):
                overlay.setdefault("warnings", []).append(
                    f"EDGAR_NO_USGAAP_FACTS: {ticker} maps to CIK {cik}, which files no "
                    "us-gaap XBRL. Every EDGAR-derived overlay value (dividends, RPO/"
                    "backlog, filing disclosures) is unavailable for this ticker, and the "
                    "absence is a registrant boundary, not a company that reports nothing."
                )
            # MKT-COVER-016's numerator, from XBRL when both parts are
            # tagged for the same period. Pairing a current total with a
            # stale percentage would state a 12-month figure the company
            # never reported, so the periods have to match.
            totals = dict(_xbrl_series(edgar, cik,
                                       "RevenueRemainingPerformanceObligation"))
            shares = dict(_xbrl_series(edgar, cik,
                                       "RevenueRemainingPerformanceObligationPercentage",
                                       unit="pure"))
            # And the pair must be the *current* period. Several issuers
            # tagged the percentage for a while and stopped: NVDA's last
            # is 2019, Microsoft's 2018. Taking the newest pair available
            # would have reported a 2018 figure as this year's contracted
            # backlog. If the company no longer tags it, we do not have
            # the number.
            latest_period = max(totals) if totals else None
            if latest_period and latest_period in shares:
                overlay["ntm_contracted"] = (totals[latest_period]
                                             * shares[latest_period])
            filing = edgar.latest_10k_text(cik)
            if filing:
                overlay.update(_cached_extract(
                    Cache(settings.cache_dir), ticker, filing, "disclosures",
                    lambda: extract_disclosures(filing, settings)) or {})
            history = _backlog_history(edgar, settings, cik, filing)
            if history:
                overlay["backlog_history"] = history

            # Dividends per share, for VAL-DDM-024/VAL-HDDM-025. EDGAR is
            # tier 1 in SOURCE_HIERARCHY.md and FMP tier 5, and the figure is
            # tagged in XBRL, so the primary source is also the free one --
            # the FMP provider has no dividends method at all.
            #
            # Two us-gaap tags carry it and filers pick one: AAPL and JPM tag
            # "Declared", XOM tags "CashPaid". Reading only the first reported
            # a large, long-standing payer as paying nothing, which is worse
            # than reporting no data -- a DDM would have read it as a
            # non-payer rather than as unavailable.
            for tag in ("CommonStockDividendsPerShareDeclared",
                        "CommonStockDividendsPerShareCashPaid"):
                dps = _xbrl_annual_duration_series(edgar, cik, tag, unit="USD/shares")
                if dps:
                    overlay["dividends_per_share_history"] = [v for _, v in dps]
                    overlay["dividend_per_share"] = dps[-1][1]
                    overlay["dividend_period_end"] = dps[-1][0]
                    overlay["dividend_tag"] = tag
                    break

            # Where a REIT states FFO/AFFO/NOI and its acquisition and
            # disposition cap rates. None of those is tagged in XBRL, so this
            # locates and cites the document rather than parsing a number out
            # of a laid-out HTML rendering -- a mis-read column would put a
            # wrong cap rate into a valuation.
            if packet.analysis.industry_adapter == "reits":
                sup = edgar.latest_reit_supplement(cik)
                if sup:
                    overlay["reit_supplement"] = sup
    except Exception:
        logger.warning("filing disclosures unavailable", exc_info=True)

    try:
        fmp = FMPProvider(settings, Cache(settings.cache_dir))
        segmentation = fmp.revenue_product_segmentation(ticker)
        named = _named_segment_revenue(segmentation)
        if named:
            overlay["segment_revenue"] = named
        shares = _segment_shares(fmp, ticker)
        if shares:
            # Business reads segment_shares, risk reads product_shares;
            # same reported split, so feed both from one fetch.
            overlay["segment_shares"] = shares
            overlay["product_shares"] = shares
        geo = _shares_from_segmentation(fmp.revenue_geographic_segmentation(ticker))
        if geo:
            overlay["geographic_shares"] = geo
        # The NBER calendar has to be in hand before the peer fetch: a
        # peer's resilience through a cycle cannot be measured without
        # knowing which years were the cycle. Set late, this read None
        # and the drawdown series never formed.
        fred = getattr(packet, "_fred", None) or _fred_of(settings)
        recession_years = _recession_years(fred)
        if recession_years:
            overlay["recession_years"] = recession_years

        # RSK-CYC-034: revenue growth regressed on nominal-GDP growth. The
        # risk specialist's regulatory/macro dimension has no other
        # mechanically scorable member, so without this it was NOT_SCORABLE
        # on every company and the whole 2-point dimension earned nothing.
        macro_pair = _macro_pair(packet, fred)
        if macro_pair:
            overlay["company_series"], overlay["macro_series"] = macro_pair

        # risk/SCORING.md valuation-compression: "Use valuation-agent packet;
        # do not duplicate valuation score." Derived from the common packet
        # with the shared engine -- the same way `_wacc_from_packet` resolves
        # financial's cross-agent WACC -- so no specialist reads another's
        # output and Phase 2 stays parallel.
        mos = _margin_of_safety_from_packet(packet)
        if mos is not None:
            overlay["margin_of_safety"] = mos

        peer_economics = _peer_economics(fmp, packet, recession_years) or {}
        if peer_economics.get("roic"):
            overlay["peer_roic"] = peer_economics["roic"]
        if peer_economics.get("operating_margin"):
            overlay["peer_operating_margin"] = peer_economics["operating_margin"]
        if peer_economics.get("recession_margin_drawdown"):
            overlay["peer_recession_drawdown"] = peer_economics["recession_margin_drawdown"]
        universe = _rs_universe(fmp, packet)
        if universe:
            overlay["rs_universe"] = universe
        est = _estimates(fmp, ticker) or {}
        # Nuestra propia serie de snapshots de FMP es AHORA la única fuente
        # del consenso previo. Antes Yahoo lo servía ya hecho y esto era el
        # respaldo; al quedar Yahoo fuera, MKT-REVMAG-012 necesita
        # `_REVISION_MIN_DAYS` de historia propia antes de poder decir algo,
        # y hasta entonces reporta MISSING con esa razón. Se cura con el uso
        # y no depende de ningún endpoint que nadie documenta.
        revision = _consensus_history(fmp, settings, ticker) or {}
        est.update({k: v for k, v in revision.items() if k != "prior_as_of"})
        # `eps_growth_pct` lo lee valuation.py en la RAÍZ del overlay,
        # mientras market.py lee las revisiones dentro de `estimates`.
        # Ponerlos juntos escondía el crecimiento de VAL-PEG-028.
        growth = _consensus_eps_growth(fmp, ticker)
        if growth is not None:
            overlay["eps_growth_pct"] = growth
        count = _active_estimate_count(fmp, ticker)
        if count is not None:
            est["active_estimates"] = count
        if est:
            overlay["estimates"] = est
        # Only useful alongside the filing's contracted backlog, which
        # the extractor above may or may not have found.
        ntm = _ntm_revenue_estimate(fmp, ticker)
        if ntm and overlay.get("ntm_contracted"):
            overlay["ntm_revenue_estimate"] = ntm
        overlay.update(_balance_sheet_keys(fmp, ticker))
    except Exception:
        logger.warning("overlay market data unavailable", exc_info=True)

    # Before the analyst file: keys that companyfacts reports outright. These
    # were being asked of a person even though the number is filed as a us-gaap
    # tag -- and two of them (`ppe` gross, standalone `depreciation`) have a
    # documented proxy that fires on absence and permanently lowers model-fit
    # confidence, so leaving them empty was not neutral.
    #
    # Only fills what is ABSENT: nothing computed here gets clobbered, and the
    # analyst file still runs afterwards and still wins.
    try:
        for key, value in (getattr(packet, "xbrl_inputs", None) or {}).items():
            if overlay.get(key) is None:
                overlay[key] = value
                logger.info("XBRL supplied analyst input %s for %s", key, ticker)
    except Exception:
        logger.warning("XBRL analyst inputs unavailable", exc_info=True)

    # Amplitud de sector (MKT-SECB-023): cuantos miembros del sector van por
    # encima de su media de 50 sesiones. Es un numero del SECTOR, no del
    # ticker, asi que se cachea por sector y por dia y lo comparten todos.
    try:
        from wbj.overlay.amplitud_sector import amplitud_de_sector

        _sec = getattr(getattr(packet, "security", None), "sector", None)
        # SOLO CACHE. Ver el docstring de `amplitud_de_sector`: pedirla por red
        # aqui agotaba el limite de FMP y los 429 caian sobre las llamadas del
        # propio ticker. Se calienta aparte; si no esta, la metrica queda
        # NOT_SCORABLE, que cuesta 3 puntos y no el analisis entero.
        _amp = amplitud_de_sector(fmp, _sec, permitir_red=False)
        if _amp:
            overlay["sector_breadth"] = _amp
            logger.info("amplitud de %s: %d/%d sobre su media de 50",
                        _sec, _amp["above_50dma_count"], _amp["valid_members"])
    except Exception:
        logger.warning("amplitud de sector no disponible", exc_info=True)

    # Applied last: an analyst who has read a sourced market study knows
    # something no endpoint here can supply, and TAM is the whole reason
    # this channel exists. Overrides are logged so the audit trail shows
    # a figure came from a person rather than a feed.
    try:
        _ind = getattr(getattr(packet, "security", None), "industry", None)
        # El TAM se resuelve ANTES de leer `Entradas/`, porque lo que escribe
        # es justo el archivo que `_manual_overlay` está a punto de leer. Así
        # una industria que nadie había mirado nunca llega con denominador al
        # primer análisis, en vez de exigir que alguien se siente a teclearlo.
        # El TAM es MUNDIAL y viene de quien mide el mercado: la asociación
        # de industria (WSTS para semiconductores) o, si no la hay, una casa
        # de análisis. Máximo dos fuentes. Ver `overlay/tam_mundial.py`.
        try:
            from wbj.overlay.tam_mundial import asegurar_tam_industria

            logger.info("TAM de industria: %s",
                        asegurar_tam_industria(settings, _ind, ticker))
        except Exception:
            logger.warning("resolucion oficial de TAM no disponible", exc_info=True)
        manual = _manual_overlay(settings, ticker, industria=_ind)
        for key in sorted(set(manual) & set(overlay)):
            logger.info("analyst input overrides computed %s for %s", key, ticker)
        for key, value in manual.items():
            computed = overlay.get(key)
            # A dict the engine also computes is MERGED, not replaced. The
            # analyst supplies the sub-keys no provider serves -- `estimates`
            # carries `individual_estimates` (MKT-DISP-013) and the revision
            # counts -- while the computed block holds `surprise`, `revision`
            # and `active_estimates` that MKT-REVMAG-012 and MKT-SURP-014
            # read. Replacing wholesale traded two working metrics for one:
            # supplying the template's `estimates` dropped the revisions
            # dimension from 75% to 50%. Analyst sub-keys still win.
            if isinstance(value, dict) and isinstance(computed, dict):
                overlay[key] = {**computed, **value}
            else:
                overlay[key] = value
    except Exception:
        logger.warning("analyst inputs unavailable", exc_info=True)

    # Participación de mercado calculada sola, para CUALQUIER ticker: el
    # denominador lo pone la industria y el numerador el segmento del 10-K
    # que compite en ese mercado. Va DESPUÉS de la fusión manual por dos
    # razones, y las dos importan: antes de ella el TAM heredado todavía no
    # existe -- el cálculo salía vacío para todos menos NVDA, que lo traía
    # escrito a mano -- y `setdefault` deja intacto lo que el analista ya
    # declaró. Quien leyó el estudio manda; esto sólo cubre el hueco.
    try:
        segmentos = overlay.get("segment_revenue")
        # Con ámbito doméstico el numerador sale del desglose GEOGRÁFICO, así
        # que exigir segmentos de producto para entrar dejaba fuera justo al
        # caso que cubre el TAM oficial: JPM, KO y PLTR salían sin
        # participación teniendo el denominador delante.
        domestico = str(overlay.get("_ambito") or "").upper() == "US"
        if domestico or (isinstance(segmentos, dict) and segmentos):
            auto = _share_automatico(
                fmp, ticker, segmentos or {}, overlay.get("tam"),
                overlay.get("tam_history"), overlay.pop("_segmento_patrones", None),
                ambito=overlay.pop("_ambito", None))
            for key, value in auto.items():
                overlay.setdefault(key, value)
    except Exception:
        logger.warning("automatic market share unavailable", exc_info=True)
    overlay.pop("_segmento_patrones", None)
    overlay.pop("_ambito", None)

    return overlay
