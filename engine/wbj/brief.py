"""Company brief — plain-Spanish read of the scorecard for the web app.

Four sections, assembled from data already in the packet plus two extra FMP
feeds (earnings calendar + insider trades). Pure functions, no network I/O —
`_build_packet` fetches the inputs, the webapp renders the output.

- `interpretation`: what each 0-10 category means in words + a research
  classification (favorece / neutral / evitar) — a classification with
  evidence, never a buy/sell order (CLAUDE.md).
- `probability`: volatility-based reachability toward each Bull/Base/Bear
  target. Lognormal terminal price at a 12-month horizon under a neutral
  (zero-drift, median = today) assumption: `P(S_T >= K) = Φ(ln(S0/K)/(σ√T))`.
  Separates statistical reachability from the fundamental target view.
- `where`: reuses `targets.narrative` verbatim.
- `watch`: price levels, next catalyst, insiders > $1M, and thesis-killers.

Honesty (Cerebro): every number carries a formula and declared assumptions;
any missing input makes its block NOT_SCORABLE, never imputed. The probability
is a labeled statistical model, not a promise of return.
"""

from __future__ import annotations

import math

from wbj.i18n import L
from statistics import NormalDist, pstdev

_HORIZON_YEARS = 1.0
_INSIDER_THRESHOLD_USD = 1_000_000.0
_MIN_VOL_SESSIONS = 60


# ============================================================================
# Interpretation
# ============================================================================


def _category_meaning(score10: float | None, lang: str = "en") -> str:
    if score10 is None:
        return L(lang, "not enough data to score", "sin datos suficientes para puntuar")
    if score10 >= 8.0:
        return L(lang, "very strong", "muy fuerte")
    if score10 >= 6.5:
        return L(lang, "solid", "sólido")
    if score10 >= 5.0:
        return L(lang, "mixed", "mixto")
    if score10 >= 3.5:
        return L(lang, "weak", "débil")
    return L(lang, "troubled", "problemático")


def _classification(overall_10: float | None, lang: str = "en") -> tuple[str | None, str]:
    if overall_10 is None:
        return None, L(lang, "not enough data for an investment conclusion", "sin data suficiente para una conclusión de inversión")
    if overall_10 >= 7.0:
        return "favors", L(lang, "the analysis favors investing (research classification)", "el análisis favorece invertir (clasificación de research)")
    if overall_10 >= 5.0:
        return "neutral", L(lang, "mixed signal — neither clearly for nor against", "señal mixta — ni claramente a favor ni en contra")
    return "avoid", L(lang, "the analysis favors avoiding for now (research classification)", "el análisis favorece evitar por ahora (clasificación de research)")


def _interpretation(packet: dict, scorecard: dict, next_earnings: dict | None, lang: str = "en") -> dict:
    overall = scorecard.get("overall_10")
    key, label = _classification(overall, lang)
    revisit = None
    if key == "avoid":
        if next_earnings and next_earnings.get("date"):
            revisit = L(lang, f"revisit at the next earnings ({next_earnings['date']})", f"revisitar en el próximo earnings ({next_earnings['date']})")
        else:
            revisit = L(lang, "revisit at the next 10-K/10-Q or a material change in estimates", "revisitar con el próximo 10-K/10-Q o cambio material de estimados")
    categories = [
        {
            "key": r["key"],
            "label": r["label"],
            "score10": r.get("score10"),
            "meaning": _category_meaning(r.get("score10"), lang),
        }
        for r in scorecard.get("categories", [])
    ]
    return {
        "overall_10": overall,
        "classification": key,
        "classification_label": label,
        "revisit": revisit,
        "categories": categories,
    }


# ============================================================================
# Probability (volatility-based lognormal reachability)
# ============================================================================


def _closes_chrono(ohlcv: list[dict] | None) -> list[float]:
    if not ohlcv:
        return []
    rows = sorted(ohlcv, key=lambda r: r.get("date", ""))
    out: list[float] = []
    for r in rows:
        c = r.get("adjClose", r.get("close"))
        if c is not None:
            out.append(float(c))
    return out


def annualized_vol(closes: list[float]) -> float | None:
    """Annualized volatility = stdev(daily log returns) * sqrt(252).

    Needs at least `_MIN_VOL_SESSIONS` sessions; None below that.
    """
    if len(closes) < _MIN_VOL_SESSIONS:
        return None
    rets = [
        math.log(closes[i] / closes[i - 1])
        for i in range(1, len(closes))
        if closes[i - 1] > 0 and closes[i] > 0
    ]
    if len(rets) < _MIN_VOL_SESSIONS - 1:
        return None
    return pstdev(rets) * math.sqrt(252)


def prob_reach(s0: float, k: float, sigma: float, t: float = _HORIZON_YEARS) -> float:
    """P(price >= K at horizon t) under a lognormal, zero-drift (median = S0)
    model: `Φ(ln(S0/K) / (σ·√t))`. K == S0 -> 0.50."""
    return NormalDist().cdf(math.log(s0 / k) / (sigma * math.sqrt(t)))


def _probability(packet: dict, targets: dict, lang: str = "en") -> dict:
    if targets.get("status") != "ok":
        return {"status": "not_scorable",
                "reason": targets.get("reason", L(lang, "no price targets", "sin targets de precio"))}
    s0 = targets.get("price")
    closes = _closes_chrono((packet.get("market_data") or {}).get("ohlcv"))
    sigma = annualized_vol(closes)
    if not s0 or sigma is None or sigma == 0:
        return {"status": "not_scorable",
                "reason": L(lang, "insufficient price history to estimate volatility", "historial de precio insuficiente para estimar volatilidad")}

    rows = []
    for s in targets["scenarios"]:
        rows.append({
            "key": s["key"], "label": s["label"], "target": s["target"],
            "prob_reach": round(prob_reach(s0, s["target"], sigma), 3),
        })

    # Modal zone across the three sorted targets (partition of the terminal
    # distribution into below-low / low-mid / mid-high / above-high).
    ordered = sorted(targets["scenarios"], key=lambda s: s["target"])
    lo, mid, hi = (ordered[0], ordered[1], ordered[2])
    p_lo = prob_reach(s0, lo["target"], sigma)
    p_mid = prob_reach(s0, mid["target"], sigma)
    p_hi = prob_reach(s0, hi["target"], sigma)
    zones = [
        (1 - p_lo, L(lang, f"below {lo['label']} (${lo['target']:,.0f})", f"por debajo de {lo['label']} (${lo['target']:,.0f})")),
        (p_lo - p_mid, L(lang, f"between {lo['label']} and {mid['label']}", f"entre {lo['label']} y {mid['label']}")),
        (p_mid - p_hi, L(lang, f"between {mid['label']} and {hi['label']}", f"entre {mid['label']} y {hi['label']}")),
        (p_hi, L(lang, f"above {hi['label']} (${hi['target']:,.0f})", f"por encima de {hi['label']} (${hi['target']:,.0f})")),
    ]
    modal_prob, modal_zone = max(zones, key=lambda z: z[0])

    return {
        "status": "ok",
        "price": s0,
        "volatility": round(sigma, 4),
        "horizon": targets.get("horizon", L(lang, "12 months", "12 meses")),
        "assumptions": L(lang,
            f"historical volatility {sigma:.0%} annual · 12-month horizon · no directional bias (median = current price)",
            f"volatilidad histórica {sigma:.0%} anual · horizonte 12 meses · sin sesgo direccional (mediana = precio actual)"),
        "targets": rows,
        "modal_zone": modal_zone,
        "modal_prob": round(modal_prob, 3),
    }


# ============================================================================
# Watch-list
# ============================================================================


def _latest(series: list[dict]) -> float | None:
    return series[-1]["val"] if series else None


def _levels(targets: dict, lang: str = "en") -> dict:
    if targets.get("status") != "ok":
        return {"status": "not_scorable",
                "reason": targets.get("reason", L(lang, "no market price", "sin precio de mercado"))}
    by = {s["key"]: s for s in targets["scenarios"]}
    return {
        "status": "ok",
        "entrada": targets["price"],
        "invalidacion": by["bear"]["target"] if "bear" in by else None,
        "salida_base": by["base"]["target"] if "base" in by else None,
        "salida_bull": by["bull"]["target"] if "bull" in by else None,
        "nota": L(lang, "reference levels (research), not a buy/sell order", "niveles de referencia (research), no una orden de compra/venta"),
    }


def _next_earnings(earnings: list[dict] | None, as_of: str) -> dict | None:
    future = [e for e in (earnings or []) if e.get("date", "") > as_of]
    return min(future, key=lambda e: e["date"]) if future else None


def _catalysts(earnings: list[dict] | None, as_of: str) -> dict:
    row = _next_earnings(earnings, as_of)
    if not row:
        return {"next_earnings": None}
    return {"next_earnings": {
        "date": row.get("date"),
        "eps_est": row.get("epsEstimated"),
        "rev_est": row.get("revenueEstimated"),
    }}


def _insider_highlights(trades: list[dict] | None,
                        threshold: float = _INSIDER_THRESHOLD_USD) -> list[dict]:
    """Form-4 rows whose transaction value exceeds `threshold`, newest first.

    Value = shares * price (gifts/awards carry price 0 -> value 0 -> dropped).
    Side from `transactionType`: "P…" purchase (compra), "S…" sale (venta).
    """
    out = []
    for t in (trades or []):
        shares = t.get("securitiesTransacted") or 0
        price = t.get("price") or 0
        value = shares * price
        if value <= threshold:
            continue
        ttype = (t.get("transactionType") or "").upper()
        side = "compra" if ttype.startswith("P") else "venta" if ttype.startswith("S") else "otro"
        if side == "otro":
            continue
        out.append({
            "name": t.get("reportingName"),
            "side": side,
            "value": round(value, 2),
            "shares": shares,
            "price": price,
            "date": t.get("transactionDate") or t.get("filingDate"),
        })
    out.sort(key=lambda r: r.get("date") or "", reverse=True)
    return out[:6]


def _insiders_flow(trades: list[dict] | None) -> dict:
    """Net open-market insider flow across the feed: total purchase vs sale
    dollars (`shares * price`). Gifts/awards (price 0) contribute nothing.
    Feeds the buy-vs-sell bar; a coarser, fuller-picture lens than the >$1M
    highlights."""
    buy = sell = 0.0
    buy_n = sell_n = 0
    for t in (trades or []):
        value = (t.get("securitiesTransacted") or 0) * (t.get("price") or 0)
        if value <= 0:
            continue
        ttype = (t.get("transactionType") or "").upper()
        if ttype.startswith("P"):
            buy += value
            buy_n += 1
        elif ttype.startswith("S"):
            sell += value
            sell_n += 1
    return {
        "buy_usd": round(buy, 2), "sell_usd": round(sell, 2),
        "net_usd": round(buy - sell, 2), "buy_count": buy_n, "sell_count": sell_n,
    }


def _risks(packet: dict, scorecard: dict, lang: str = "en") -> list[str]:
    a = packet["annual"]
    rev, ni = a["revenue"], a["net_income"]
    ocf, capex = a["operating_cash_flow"], a["capex"]
    debt, eq = a["long_term_debt"], a["equity"]
    out: list[str] = []

    debt_l, eq_l = _latest(debt), _latest(eq)
    if debt_l is not None and eq_l:
        de = debt_l / eq_l
        if de >= 2.0:
            out.append(L(lang, f"High debt: owes ${de:,.1f} for every $1 of equity.", f"Deuda alta: debe ${de:,.1f} por cada $1 de capital propio."))

    ocf_l, capex_l = _latest(ocf), _latest(capex)
    if ocf_l is not None and capex_l is not None and (ocf_l - capex_l) < 0:
        out.append(L(lang, "Burning cash: negative free cash flow — watch financing.", "Quema efectivo: flujo de caja libre negativo — vigilar financiamiento."))

    if len(ni) >= 2 and rev and len(rev) >= 2 and rev[-1]["val"] and rev[-2]["val"]:
        m_now = ni[-1]["val"] / rev[-1]["val"]
        m_prev = ni[-2]["val"] / rev[-2]["val"]
        if m_now < m_prev - 0.02:
            out.append(L(lang, f"Margins falling: net margin dropped from {m_prev:.0%} to {m_now:.0%}.", f"Márgenes cayendo: margen neto bajó de {m_prev:.0%} a {m_now:.0%}."))

    if rev and len(rev) >= 2 and rev[-2]["val"]:
        g = rev[-1]["val"] / rev[-2]["val"] - 1
        if g < 0:
            out.append(L(lang, f"Revenue falling: sales {g:+.0%} in the last fiscal year.", f"Ventas cayendo: ingresos {g:+.0%} en el último año fiscal."))

    risk_row = next((r for r in scorecard.get("categories", []) if r["key"] == "risk"), None)
    if risk_row and risk_row.get("score10") is not None and risk_row["score10"] < 5.0:
        out.append(L(lang, f"Low risk score ({risk_row['score10']}/10): fragile resilience.", f"Score de riesgo bajo ({risk_row['score10']}/10): resiliencia frágil."))

    return out[:4]


# ============================================================================
# Public entry point
# ============================================================================


def company_brief(packet: dict, scorecard: dict, targets: dict, lang: str = "en") -> dict:
    """Assemble the four-section plain-Spanish brief from already-computed
    scorecard + targets and the packet's market_data feeds."""
    from wbj.targets import narrative

    md = packet.get("market_data") or {}
    as_of = packet.get("as_of", "")
    next_earn = _next_earnings(md.get("earnings"), as_of)

    return {
        "interpretation": _interpretation(packet, scorecard, next_earn, lang),
        "probability": _probability(packet, targets, lang),
        "where": narrative(packet, scorecard, targets, lang),
        "watch": {
            "levels": _levels(targets, lang),
            "catalysts": _catalysts(md.get("earnings"), as_of),
            "insiders": _insider_highlights(md.get("insiders")),
            "insiders_flow": _insiders_flow(md.get("insiders")),
            "risks": _risks(packet, scorecard, lang),
        },
    }
