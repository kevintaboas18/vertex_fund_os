"""Quick 6-agent scorecard (1-10 per category) from the MVP EDGAR packet.

Honest-scoring rules (Cerebro: "sin evidencia, no hay número"):
- Financial, Business, Risk: scored from real EDGAR fundamentals (partial
  metric sets — a *quick* read, not the full specialist methodology).
- Market, Technical, Valuation: scored from FMP market data when present
  (analyst estimates / adjusted OHLCV / price + market cap) — a *quick*
  read too, still not the full specialist engines (Phase 2). Each stays
  NOT_SCORABLE when its FMP data is absent — never imputed.
- Overall 1-10 weights only the covered categories and reports how many
  of the 100 evidence points that covers.
"""

from __future__ import annotations

from statistics import median, pstdev

from wbj.core.formulas import yoy
from wbj.i18n import L
from wbj.core.nullstates import EvidenceClass, NullState, Value
from wbj.core import adapters as _adapters
from wbj.core.scoring import CATEGORY_WEIGHTS, Category, Dimension, anchor_score

# --- Anchors aligned with Cerebro FIN/BUS band cutoffs (quick defaults) ---
_A_REV_GROWTH = [(-0.10, 0.0), (0.0, 3.0), (0.10, 6.0), (0.25, 9.0), (0.40, 10.0)]
_A_NET_MARGIN = [(-0.10, 0.0), (0.0, 3.0), (0.10, 6.0), (0.20, 8.5), (0.30, 10.0)]
_A_FCF_MARGIN = [(-0.10, 0.0), (0.0, 3.0), (0.10, 6.5), (0.25, 10.0)]
_A_DEBT_EQUITY = [(0.0, 10.0), (0.5, 8.0), (1.0, 6.0), (2.0, 3.0), (4.0, 0.0)]
_A_OP_MARGIN = [(0.0, 0.0), (0.10, 4.0), (0.20, 7.0), (0.35, 10.0)]
_A_GROSS_MARGIN = [(0.0, 0.0), (0.20, 4.0), (0.40, 7.0), (0.60, 10.0)]
_A_ROE = [(0.0, 0.0), (0.08, 4.0), (0.15, 7.0), (0.30, 10.0)]
# margin stability: 5y net-margin std-dev (lower = steadier = moat signal)
_A_MARGIN_STDEV = [(0.01, 10.0), (0.03, 7.0), (0.06, 4.0), (0.12, 0.0)]
# interest coverage: <1.5x solvency warning band, >5x comfortable
_A_INT_COVERAGE = [(0.0, 0.0), (1.5, 2.0), (3.0, 5.0), (5.0, 7.0), (10.0, 10.0)]

# --- Phase 1 quick FMP anchors (market / technical / valuation) ---
# forward revenue growth reuses the revenue-growth shape (_A_REV_GROWTH)
_A_ANALYST_BREADTH = [(0.0, 0.0), (3.0, 4.0), (10.0, 7.0), (25.0, 10.0)]
_A_PRICE_SMA = [(0.85, 2.0), (1.0, 5.0), (1.10, 8.0), (1.25, 10.0)]
_A_MOMENTUM_6M = [(-0.20, 0.0), (0.0, 4.0), (0.15, 7.0), (0.40, 10.0)]
_A_OFF_HIGH = [(-0.40, 2.0), (-0.20, 5.0), (-0.08, 8.0), (0.0, 10.0)]
# P/E and P/FCF: lower is cheaper (descending scores; anchor_score interpolates)
_A_PE = [(10.0, 10.0), (18.0, 8.0), (28.0, 5.0), (45.0, 2.0), (70.0, 0.0)]
_A_PFCF = [(12.0, 10.0), (22.0, 8.0), (35.0, 5.0), (55.0, 2.0), (90.0, 0.0)]
#: PEG: the price/earnings multiple against the growth that is being paid for.
#: These are the DEEP specialist's own anchors for VAL-PEG-028
#: (`specialists/valuation.py`), copied rather than imported so the quick does
#: not drag the whole deep module in -- `test_quick_valuation.py` pins the two
#: lists together so the copy cannot drift.
_A_PEG = [(0.5, 10.0), (1.0, 7.0), (2.0, 3.0), (3.5, 0.0)]

_TECHNICAL_MIN_SESSIONS = 200

_QUICK_LABEL = {
    "business": ("Business (quick)", "Negocio (rápido)"),
    "financial": ("Financial (quick)", "Finanzas (rápido)"),
    "market": ("Market & Growth (quick)", "Mercado y Crecimiento (rápido)"),
    "technical": ("Technical & Momentum (quick)", "Técnico y Momentum (rápido)"),
    "risk": ("Risk & Resilience (quick)", "Riesgo y Resiliencia (rápido)"),
    "valuation": ("Valuation (quick)", "Valuación (rápido)"),
}
_NS_REASON = {
    "market": ("no analyst coverage (FMP)", "sin cobertura de analistas (FMP)"),
    "technical": ("insufficient price history (FMP)", "historial de precio insuficiente (FMP)"),
    "valuation": ("no market price (FMP)", "sin precio de mercado (FMP)"),
    # `risk` faltaba en este mapa y era un KeyError esperando: la categoria
    # no podia quedar N/S mientras las anclas industriales le daban un cero a
    # cualquiera. Con el adaptador si puede, y un banco es justo el caso.
    "risk": ("leverage/coverage do not apply to this business and it reports "
             "no capex, so free cash flow is not computable either",
             "el apalancamiento y la cobertura no aplican a este negocio, y no "
             "reporta capex, así que tampoco hay flujo de caja libre"),
}

# Los motivos de la valuacion: uno por causa, porque decir "sin precio de
# mercado" cuando el precio SI esta manda a mirar donde no es.
_R_SIN_PRECIO = ("no market price (FMP)", "sin precio de mercado (FMP)")
_R_SIN_BASE = (
    "neither positive earnings nor positive normalised free cash flow: there "
    "is no multiple to compute",
    "ni beneficio positivo ni flujo de caja libre normalizado positivo: no hay "
    "múltiplo que calcular")
_R_SOLO_GANANCIAS = (
    "only the earnings multiple could be computed; normalised free cash flow "
    "is unavailable or not positive, which is under half the evidence",
    "solo se pudo calcular el múltiplo sobre beneficios; el flujo de caja libre "
    "normalizado no está o no es positivo, y eso es menos de la mitad de la "
    "evidencia")
_R_SOLO_CAJA = (
    "only the cash-flow multiple could be computed; earnings are not positive, "
    "which is under half the evidence",
    "solo se pudo calcular el múltiplo sobre el flujo de caja; el beneficio no "
    "es positivo, y eso es menos de la mitad de la evidencia")


def _val(x: float | None, name: str, unit: str = "ratio") -> Value:
    if x is None:
        return Value.null(NullState.MISSING, unit=unit, warnings=[f"MISSING: {name}"])
    return Value.of(x, unit=unit, evidence_class=EvidenceClass.C)


def _scored(v: Value, anchors: list[tuple[float, float]]) -> Value:
    if v.is_null:
        return v
    return Value.of(anchor_score(v.value, anchors), unit="score", evidence_class=EvidenceClass.C)


def _latest(series: list[dict]) -> float | None:
    return series[-1]["val"] if series else None


def _at_period(series: list[dict], end: str | None) -> float | None:
    """One series' value for a named fiscal period, or None.

    A ratio whose two sides come from different years is not that ratio.
    AAPL stopped tagging `InterestExpense` after FY2023 while its operating
    income runs through FY2025, so `_latest` on each side built an interest
    coverage out of FY2025 EBIT over FY2023 interest -- a number for a year
    that never happened.

    DATA_POLICY.md forbids it ("Do not mix annual, trailing-twelve-month, and
    quarterly periods without labeling and reconciliation") and
    SOURCE_HIERARCHY.md opens conflict resolution with "Verify period". The
    hazard is already named in `_annual_series`' own docstring, one level up:
    a stale series "mismatched against a fresher" one. This applies the same
    check across the two sides of a ratio.
    """
    if not end:
        return None
    for row in reversed(series or []):
        if row.get("end") == end:
            return row.get("val")
    return None


def _interest_coverage(operating_income: float | None,
                       interest_expense: float | None) -> Value:
    """EBIT proxy / interest expense, distinguishing absent from zero.

    Both used to return NOT_MEANINGFUL, which states as fact that the company
    has no interest cost. MISSING_DATA_POLICY.md step 2 separates them: "Is
    the source expected to report it? If yes but absent, use `MISSING`." A
    reported zero genuinely leaves the ratio undefined; an unreported figure
    is simply unknown.

    AAPL is the live case -- it stopped tagging `InterestExpense` after
    FY2023 while carrying $112B of debt, so from FY2024 on the figure is
    absent, not zero.
    """
    if interest_expense is None:
        return Value.null(NullState.MISSING, unit="x",
                          warnings=["interest expense not reported for this period"])
    if not interest_expense:
        return Value.null(NullState.NOT_MEANINGFUL, unit="x",
                          warnings=["zero interest expense reported"])
    return _val(_ratio(operating_income, interest_expense), "interest_coverage")


def _ratio(num: float | None, den: float | None) -> float | None:
    if num is None or not den:
        return None
    return num / den


def _margin_history(ni: list[dict], rev: list[dict], years: int = 5) -> list[float]:
    by_end = {r["end"]: r["val"] for r in rev if r.get("val")}
    pairs = [(r["val"] / by_end[r["end"]]) for r in ni if r["end"] in by_end]
    return pairs[-years:]


def _dim(name: str, max_points: float, scores: list[Value]) -> Dimension:
    w = 1.0 / len(scores)
    return Dimension(name=name, max_points=max_points, metric_scores=[(w, s) for s in scores])


# --- Phase 1 quick FMP helpers (pure over the packet's market_data block) ---


def _closes_chrono(ohlcv: list[dict] | None) -> list[float]:
    """FMP daily bars (any order) -> ascending list of adjusted closes.

    Prefers `adjClose` when the feed provides it, else `close` (the
    `/stable/historical-price-eod/full` fixture carries only `close`).
    """
    if not ohlcv:
        return []
    rows = sorted(ohlcv, key=lambda r: r.get("date", ""))
    closes: list[float] = []
    for r in rows:
        c = r.get("adjClose", r.get("close"))
        if c is not None:
            closes.append(float(c))
    return closes


def _sma(closes: list[float], n: int) -> float | None:
    return sum(closes[-n:]) / n if len(closes) >= n else None


def _momentum(closes: list[float], lookback: int) -> float | None:
    if len(closes) <= lookback:
        return None
    past = closes[-1 - lookback]
    return (closes[-1] / past - 1.0) if past else None


def _off_high(closes: list[float], window: int = 252) -> float | None:
    """Fraction below the trailing-`window` high (0 = at the high, <0 below)."""
    if not closes:
        return None
    high = max(closes[-window:])
    return (closes[-1] / high - 1.0) if high else None


def _nearest_future_estimate(estimates: list[dict] | None, as_of: str) -> dict | None:
    """Soonest analyst-estimate row dated strictly after `as_of`."""
    future = [e for e in (estimates or []) if e.get("date", "") > as_of]
    return min(future, key=lambda e: e["date"]) if future else None


def _first_present(row: dict, *keys: str) -> float | None:
    """First non-None value among `keys` (tolerates FMP field-name drift:
    live `/stable/` uses `revenueAvg`/`numAnalystsRevenue`, legacy `/api/v3/`
    used `estimatedRevenueAvg`/`numberAnalystEstimatedRevenue`)."""
    for k in keys:
        v = row.get(k)
        if v is not None:
            return v
    return None


def _market_category(md: dict, rev: list[dict], as_of: str) -> Category:
    """Market & Growth (20 pts): forward revenue growth + analyst breadth."""
    row = _nearest_future_estimate(md.get("estimates"), as_of)
    latest_rev = _latest(rev)
    fwd = _first_present(row, "revenueAvg", "estimatedRevenueAvg") if row else None
    growth = (fwd / latest_rev - 1.0) if (fwd and latest_rev) else None
    breadth = _first_present(row, "numAnalystsRevenue", "numberAnalystEstimatedRevenue") if row else None
    return Category(name="market", max_points=20.0, dimensions=[
        _dim("Growth outlook", 20.0, [
            _scored(_val(growth, "forward_rev_growth"), _A_REV_GROWTH),
            _scored(_val(breadth, "analyst_breadth", unit="count"), _A_ANALYST_BREADTH),
        ]),
    ])


def _technical_category(md: dict) -> Category | None:
    """Technical & Momentum (20 pts): price vs SMA50/SMA200, 6-mo momentum,
    % off the 52-week high. Needs >=200 sessions of history (else N/S)."""
    closes = _closes_chrono(md.get("ohlcv"))
    if len(closes) < _TECHNICAL_MIN_SESSIONS:
        return None
    price = closes[-1]
    return Category(name="technical", max_points=20.0, dimensions=[
        _dim("Trend", 10.0, [
            _scored(_val(_ratio(price, _sma(closes, 50)), "price_vs_sma50"), _A_PRICE_SMA),
            _scored(_val(_ratio(price, _sma(closes, 200)), "price_vs_sma200"), _A_PRICE_SMA),
        ]),
        _dim("Momentum", 10.0, [
            _scored(_val(_momentum(closes, 126), "momentum_6m"), _A_MOMENTUM_6M),
            _scored(_val(_off_high(closes), "off_52w_high"), _A_OFF_HIGH),
        ]),
    ])


def _fcf_normalizado(annual: dict, years: int = 3) -> float | None:
    """Free cash flow of the last `years` fiscal years, as a MEDIAN.

    SCORING.md's cash-flow row says it with all its letters: "Use normalized,
    not peak-cycle, cash flow". A single fiscal year is exactly the peak-cycle
    read it forbids -- one heavy capex year or one working-capital swing
    doubles the multiple and halves the category.

    The two sides are paired BY PERIOD with `_at_period`, not each on its own
    `_latest`: operating cash flow and capex from different years is not that
    year's free cash flow, the same hazard `_at_period` was written for.

    The median, not the mean: one outlier year is what normalising is meant to
    survive, and a mean carries it straight through.
    """
    ocf, capex = annual.get("operating_cash_flow") or [], annual.get("capex") or []
    vals: list[float] = []
    for row in reversed(ocf):
        if len(vals) >= years:
            break
        cx = _at_period(capex, row.get("end"))
        if row.get("val") is not None and cx is not None:
            vals.append(row["val"] - cx)
    if not vals:
        return None
    return median(vals)


def _crecimiento_eps(md: dict, annual: dict, as_of: str) -> float | None:
    """Forward EPS growth from the analyst consensus, as a fraction.

    Same source `_market_category` already uses for revenue, read on the
    earnings line. Returns None unless both sides are positive: a growth rate
    computed off a negative base is a number without a meaning.
    """
    row = _nearest_future_estimate(md.get("estimates"), as_of)
    if not row:
        return None
    fwd = _first_present(row, "epsAvg", "estimatedEpsAvg")
    eps = _eps_actual(annual)
    if not fwd or not eps or fwd <= 0 or eps <= 0:
        return None
    return fwd / eps - 1.0


def _eps_actual(annual: dict) -> float | None:
    ni_l = _latest(annual.get("net_income") or [])
    shares_l = _latest(annual.get("diluted_shares") or [])
    return (ni_l / shares_l) if (ni_l is not None and shares_l) else None


def _valuation_category(md: dict, annual: dict,
                        as_of: str = "") -> tuple[Category | None,
                                                  tuple[str, str] | None]:
    """Valuation (10 pts): the earnings multiple and the cash-flow multiple.

    Returns `(category, reason)` -- the reason is the truthful N/S text when
    there is no category, and `None` when there is one.

    Four things were wrong with the version this replaces, and all four were
    measured on the engine itself:

    1. A non-meaningful multiple was DROPPED from the list instead of passed
       in as a null `Value`. The dimension never learnt there was a hole, so
       `coverage` kept reporting 1.00 with half the evidence gone -- while the
       same gap in `financial` and `risk` correctly took theirs to 0.50.
    2. Because of (1), a company that LOSES money scored HIGHER than the same
       company earning: 5.7 against 4.5, since the negative P/E vanished and
       only the surviving cash multiple was averaged. Losing money improved
       the score.
    3. With a price present but no positive base, the row said "no market
       price (FMP)". The price was there; the base was not.
    4. There was no growth adjustment at all, while SCORING.md's LARGEST
       valuation dimension is "Growth-adjusted multiples ... VAL-PEG-028 ...
       reverse DCF", whose 7-10 band is "price embeds conservative growth
       relative to quality". A fast grower was scored as if it were an
       ex-growth utility.

    The earnings slot now carries the PEG when the consensus gives a growth
    rate -- the same ratio and the same anchors as the deep specialist's
    VAL-PEG-028 -- and falls back to the raw P/E when it does not. Both slots
    are always present, so 50% coverage lands under the engine's own 70%
    usable floor and the category goes N/S with its reason instead of
    inventing half a verdict.
    """
    price = md.get("price")
    if not price:
        return None, _R_SIN_PRECIO

    eps = _eps_actual(annual)
    fcf = _fcf_normalizado(annual)
    mcap = md.get("market_cap")
    crecimiento = _crecimiento_eps(md, annual, as_of)

    # --- Slot 1: the earnings multiple, growth-adjusted when growth is known.
    if not eps or eps <= 0:
        # Not absent -- reported and non-positive. A P/E off negative earnings
        # is undefined, which is `NOT_MEANINGFUL`, the same state
        # `_interest_coverage` already uses for a zero denominator.
        ganancias = Value.null(NullState.NOT_MEANINGFUL, unit="score",
                               warnings=["EPS_NOT_POSITIVE"])
    elif crecimiento and crecimiento > 0:
        ganancias = _scored(_val((price / eps) / (crecimiento * 100.0),
                                 "peg", unit="ratio"), _A_PEG)
    else:
        ganancias = _scored(_val(price / eps, "pe", unit="x"), _A_PE)

    # --- Slot 2: the cash-flow multiple, on normalised free cash flow.
    if fcf is None or not mcap:
        caja = Value.null(NullState.MISSING, unit="score",
                          warnings=["FCF_OR_MARKET_CAP_UNAVAILABLE"])
    elif fcf <= 0:
        caja = Value.null(NullState.NOT_MEANINGFUL, unit="score",
                          warnings=["NORMALISED_FCF_NOT_POSITIVE"])
    else:
        caja = _scored(_val(mcap / fcf, "pfcf", unit="x"), _A_PFCF)

    dim = _dim("Multiples", 10.0, [ganancias, caja])
    if dim.score10_value().is_null:
        # The engine's own 70% rule said no. Say WHICH side is missing --
        # "no market price" was a lie here, and a lie in a reason is worse
        # than no reason.
        return None, (_R_SIN_BASE if (ganancias.is_null and caja.is_null)
                      else _R_SOLO_GANANCIAS if caja.is_null
                      else _R_SOLO_CAJA)
    return Category(name="valuation", max_points=10.0, dimensions=[dim]), None


def quick_scorecard(packet: dict, lang: str = "en") -> dict:
    """Compute the quick 6-category scorecard from an MVP packet."""
    a = packet["annual"]
    rev, ni = a["revenue"], a["net_income"]
    ocf, capex = a["operating_cash_flow"], a["capex"]
    debt, eq = a["long_term_debt"], a["equity"]
    op = a.get("operating_income", [])
    gp = a.get("gross_profit", [])
    interest = a.get("interest_expense", [])

    # Solo bancos: ver el comentario en la categoria de riesgo.
    _interes_es_costo = _adapters.cost_of_funds_is_interest(
        packet.get("industry_adapter"))

    rev_l, ni_l = _latest(rev), _latest(ni)
    ocf_l, capex_l = _latest(ocf), _latest(capex)
    debt_l, eq_l = _latest(debt), _latest(eq)
    op_l, gp_l = _latest(op), _latest(gp)
    # Interest is read AT the operating-income period, not at its own latest:
    # see `_at_period`. Absent there, it is absent -- not borrowed from an
    # older year.
    op_end = op[-1].get("end") if op else None
    int_l = _at_period(interest, op_end)

    growth = (
        yoy(rev[-1]["val"], rev[-2]["val"])
        if len(rev) >= 2
        else Value.null(NullState.MISSING, unit="ratio", warnings=["MISSING: revenue history"])
    )
    fcf = (ocf_l - capex_l) if ocf_l is not None and capex_l is not None else None
    margins = _margin_history(ni, rev)
    margin_stdev = _val(pstdev(margins) if len(margins) >= 3 else None, "margin_stability")
    # ROE on average equity when two years exist (Cerebro convention).
    eq_avg = (eq[-1]["val"] + eq[-2]["val"]) / 2 if len(eq) >= 2 else eq_l
    roe = _val(_ratio(ni_l, eq_avg), "roe")

    net_margin = _val(_ratio(ni_l, rev_l), "net_margin")
    fcf_margin = _val(_ratio(fcf, rev_l), "fcf_margin")
    d_e = _val(_ratio(debt_l, eq_l), "debt_to_equity")
    op_margin = _val(_ratio(op_l, rev_l), "operating_margin")
    gross_margin = _val(_ratio(gp_l, rev_l), "gross_margin")
    coverage = _interest_coverage(op_l, int_l)
    fcf_positive = _val(None if fcf is None else (1.0 if fcf > 0 else 0.0), "fcf_positive")

    categories: dict[str, Category] = {
        "financial": Category(name="financial", max_points=15.0, dimensions=[
            _dim("Profitability", 7.5, [_scored(net_margin, _A_NET_MARGIN),
                                        _scored(fcf_margin, _A_FCF_MARGIN)]),
            _dim("Growth & Balance Sheet", 7.5, [_scored(growth, _A_REV_GROWTH),
                                                 _scored(d_e, _A_DEBT_EQUITY)]),
        ]),
        "business": Category(name="business", max_points=20.0, dimensions=[
            _dim("Margin power", 10.0, [_scored(op_margin, _A_OP_MARGIN),
                                        _scored(gross_margin, _A_GROSS_MARGIN)]),
            _dim("Durability & returns", 10.0, [_scored(margin_stdev, _A_MARGIN_STDEV),
                                                _scored(roe, _A_ROE)]),
        ]),
        "risk": Category(name="risk", max_points=15.0, dimensions=[
            # SOLO la cobertura de intereses, no el apalancamiento.
            #
            # La primera version marco las DOS y era pasarse. Medido:
            # deuda/patrimonio de JPM es 0,74 y puntua 7,04/10 -- MEJOR que
            # Coca-Cola (1,11 -> 5,67) y que Apple (1,06 -> 5,82). El
            # apalancamiento de un banco no vive en `long_term_debt`, vive en
            # los DEPOSITOS, y EDGAR no los mete en esa etiqueta. Asi que ese
            # ratio mide deuda corporativa corriente y significa lo de
            # siempre. El cero de JPM salia entero de la cobertura (0,4x).
            #
            # Retirar una metrica que funciona es el mismo error que puntuar
            # una que no aplica, solo que en la otra direccion.
            #
            # Tampoco se retira para las casas de bolsa, aunque su balance
            # parezca de banco: el SIC 6211 va de SEIC 0,00 y RJF 0,10 a MS
            # 3,06 -- no es una clase, igual que no lo era para la cobertura.
            _dim("Solvency", 7.5, [
                _scored(d_e, _A_DEBT_EQUITY),
                _scored(coverage, _A_INT_COVERAGE) if not _interes_es_costo
                else Value.null(NullState.NOT_APPLICABLE, unit="score",
                                warnings=["INTEREST_COVERAGE_NOT_APPLICABLE_BANK_ADAPTER"]),
            ]),
            # El flujo de caja libre tampoco: INDUSTRY_ADAPTERS.md, Banks --
            # "Do not use enterprise-value/EBITDA, net-debt/EBITDA, or
            # conventional FCFF". Con todas las letras, y solo bajo Banks.
            #
            # No es una laguna de datos: JPM y BAC no etiquetan capex porque
            # un banco no tiene ciclo de reinversion en activo fijo. Dejarlo
            # como MISSING lo mantenia en el denominador y hundia la
            # categoria a la mitad -- JPM salia 3,5 teniendo 7,04 en lo unico
            # que si le aplica.
            _dim("Cash generation", 7.5, (
                [_scored(fcf_positive, [(0.0, 2.0), (1.0, 10.0)]),
                 _scored(fcf_margin, _A_FCF_MARGIN)]
                if not _interes_es_costo
                else [Value.null(NullState.NOT_APPLICABLE, unit="score",
                                 warnings=["FCF_NOT_APPLICABLE_BANK_ADAPTER"]),
                      Value.null(NullState.NOT_APPLICABLE, unit="score",
                                 warnings=["FCF_MARGIN_NOT_APPLICABLE_BANK_ADAPTER"])])),
        ]),
    }

    # Quick FMP categories (Phase 1): only included when their data covers at
    # least one metric — otherwise they fall through to the N/S branch with a
    # truthful reason ("sin evidencia, no hay número").
    md = packet.get("market_data") or {}
    as_of = packet.get("as_of", "")
    val_cat, val_reason = _valuation_category(md, a, as_of)
    # El motivo de la valuacion se decide DENTRO, no en la tabla fija: la causa
    # («no hay precio» / «no hay base positiva» / «solo la mitad») no se puede
    # saber desde aqui, y la tabla fija decia siempre la primera.
    razones = dict(_NS_REASON)
    if val_reason:
        razones["valuation"] = val_reason
    fmp_builders = {
        "market": _market_category(md, rev, as_of),
        "technical": _technical_category(md),
        "valuation": val_cat,
    }
    for key, cat in fmp_builders.items():
        if cat is not None and cat.coverage() > 0:
            categories[key] = cat

    rows: list[dict] = []
    covered_pts = 0.0
    weighted = 0.0
    for key in ("business", "financial", "market", "technical", "risk", "valuation"):
        max_pts = float(CATEGORY_WEIGHTS[key])
        if key in categories:
            cat = categories[key]
            cov = cat.coverage()
            score10 = round(cat.score10(), 1) if cov > 0 else None
            if score10 is not None:
                covered_pts += max_pts
                weighted += max_pts * score10
            # `status` se decide por si HAY puntaje, no por si la categoria
            # se construyo. Un banco entra aqui --el diccionario se llena
            # antes de saber si algo puntua-- y salia con status "scored" y
            # score10 None: una fila que dice puntuada sin puntaje, que es
            # justo lo que la interfaz no sabe pintar.
            rows.append({
                "key": key, "label": L(lang, *_QUICK_LABEL[key]), "max_points": max_pts,
                "score10": score10, "points": round(cat.points(), 2),
                "coverage": round(cov, 2),
                "status": "scored" if score10 is not None else "not_scorable",
                **({} if score10 is not None else {"reason": L(lang, *razones.get(
                    key, ("no usable data for this category",
                          "sin datos utilizables para esta categoría")))}),
            })
        else:
            rows.append({
                "key": key, "label": L(lang, *_QUICK_LABEL[key]), "max_points": max_pts,
                "score10": None, "points": None, "coverage": 0.0,
                "status": "not_scorable",
                "reason": L(lang, *razones.get(
                    key, ("no usable data for this category",
                          "sin datos utilizables para esta categoría"))),
            })

    overall = round(weighted / covered_pts, 1) if covered_pts else None
    return {
        "categories": rows,
        "overall_10": overall,
        "evidence_points_covered": int(covered_pts),
        "evidence_points_total": 100,
        "disclaimer": L(lang,
            f"Quick scorecard from SEC EDGAR fundamentals + FMP market data "
            f"(price/estimates); {int(covered_pts)}/100 evidence points covered. "
            f"Not the full methodology.",
            f"Puntaje rápido con fundamentales de SEC EDGAR + datos de mercado FMP "
            f"(precio/estimados); {int(covered_pts)}/100 puntos de evidencia cubiertos. "
            f"No es la metodología completa."),
    }
