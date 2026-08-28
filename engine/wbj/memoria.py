"""Agent memory: persist predictions and evaluate them against reality.

The honest learning loop (Cerebro: BACKTESTING_AND_CALIBRATION.md):

1. Every analysis SAVES its prediction (price, targets, score, date) to
   Reportes/<TICKER>/<date>/prediccion.json — the seed of memory.
2. `wbj track` HARVESTS: compares every saved prediction against today's
   price, measures bias, and writes Memoria/calibracion.md.
3. The orchestrator (Claude) reads Memoria/ before each analysis and
   records qualitative lessons after it — code handles numbers, the
   agent handles judgment.

No prediction is ever edited or deleted: misses are the learning signal.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

MATURITY_DAYS = 365  # targets are 12-month — only then is a hit/miss final


#: Which analysis produced a prediction. The quick scorecard and the strict
#: Cerebro profile score the same company differently (AAPL: 8.0/10 quick vs
#: 3.9/10 strict), so a calibration that mixed them would be measuring two
#: systems as one. Each writes its own file; `prediccion.json` stays the
#: quick one so every prediction already on disk keeps its meaning.
SOURCE_QUICK = "quick"
SOURCE_CEREBRO = "cerebro"
_FILENAME_BY_SOURCE = {
    SOURCE_QUICK: "prediccion.json",
    SOURCE_CEREBRO: "prediccion.cerebro.json",
}


def save_prediction(reports_dir: Path, ticker: str, day: date,
                    scorecard: dict, targets: dict,
                    source: str = SOURCE_QUICK) -> Path | None:
    """Persist one analysis' numeric prediction. Returns path or None."""
    if targets.get("status") != "ok":
        return None
    out_dir = reports_dir / ticker.upper() / day.isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    by = {s["key"]: s["target"] for s in targets["scenarios"]}
    record = {
        "ticker": ticker.upper(),
        "date": day.isoformat(),
        "price": targets["price"],
        "bear": by["bear"], "base": by["base"], "bull": by["bull"],
        "growth_base": targets["growth_base"],
        "pe_now": targets["pe_now"],
        "score10": scorecard.get("overall_10"),
        "evidence_points": scorecard.get("evidence_points_covered"),
        "horizon_days": MATURITY_DAYS,
        "source": source,
    }
    path = out_dir / _FILENAME_BY_SOURCE.get(source, "prediccion.json")
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return path


_REQUIRED_FIELDS = ("ticker", "date", "price", "bear", "base", "bull")


def _is_usable(rec: object) -> bool:
    """Whether a loaded record can actually be evaluated.

    Checking only that the keys are PRESENT was not enough: a record whose
    `date` was present but unparseable passed the filter and then raised out
    of `date.fromisoformat` inside `track`, so one corrupt file took the
    whole calibration run down with it. The promise in `load_predictions`'
    docstring is that malformed files are ignored, so it has to hold for
    malformed *contents*, not just missing keys.
    """
    if not isinstance(rec, dict) or not all(k in rec for k in _REQUIRED_FIELDS):
        return False
    if not all(isinstance(rec[k], (int, float)) for k in ("price", "bear", "base", "bull")):
        return False
    try:
        date.fromisoformat(str(rec["date"]))
    except (TypeError, ValueError):
        return False
    horizon = rec.get("horizon_days", MATURITY_DAYS)
    return isinstance(horizon, int) and horizon > 0


#: Files under `Reportes/*/*/prediccion*.json` that parsed as JSON but that
#: `_is_usable` rejected, from the most recent `load_predictions` call.
#:
#: Skipping what cannot be read is right; skipping it in SILENCE is what let
#: this rot for weeks. Measured 2026-08-28: of 29 prediction files, 11 were
#: readable and **18 were dropped without a word** -- every single one written
#: by the web panel, which used `fecha`/`price_at_analysis`/`targets_12m`
#: while this module requires `date`/`price`/`bear`/`base`/`bull`. The panel
#: now writes both, but a silent drop must never again be indistinguishable
#: from an empty record: `track` reports this count, and `calibracion.md`
#: prints it.
DESCARTADAS: list[str] = []


def load_predictions(reports_dir: Path) -> list[dict]:
    """All saved predictions, oldest first.

    Files that cannot be parsed or that `_is_usable` rejects are skipped and
    their paths recorded in `DESCARTADAS`, so "no predictions" and "plenty of
    predictions nobody could read" stop looking the same from the outside.
    """
    preds = []
    DESCARTADAS.clear()
    if not reports_dir.exists():
        return preds
    for p in sorted(reports_dir.glob("*/*/prediccion*.json")):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            DESCARTADAS.append(str(p))
            continue
        if not _is_usable(rec):
            DESCARTADAS.append(str(p))
            continue
        # Files written before the split carry no `source`; they are all
        # quick-scorecard predictions.
        rec.setdefault("source", SOURCE_QUICK)
        preds.append(rec)
    return preds


def maturity_date(pred: dict) -> date:
    """The date a prediction's horizon closes — when its verdict is fixed."""
    from datetime import timedelta

    return date.fromisoformat(pred["date"]) + timedelta(
        days=pred.get("horizon_days", MATURITY_DAYS))


def evaluate(pred: dict, price_now: float, today: date,
             price_at_maturity: float | None = None) -> dict:
    """Judge one prediction against the price at the end of its horizon.

    - deviation: actual return so far MINUS the pro-rated base-scenario
      return (positive = the stock is running ahead of our base case).
    - mature (>= horizon): final verdict — inside [bear, bull] = "en rango".

    A matured prediction is judged at `price_at_maturity`, the close on the
    day its 12-month horizon ended — NOT at today's price. Judging a
    12-month call by a price three years later is look-ahead, which
    BACKTESTING_AND_CALIBRATION.md's anti-bias rules forbid outright: a
    prediction that landed inside its bear-bull range on schedule was being
    recorded as "superó Bull" purely because the stock kept climbing after
    the horizon closed. When the historical close cannot be retrieved the
    verdict is withheld (`sin_precio_al_vencimiento`) rather than decided on
    the wrong day.
    """
    d0 = date.fromisoformat(pred["date"])
    days = (today - d0).days
    p0 = pred["price"]
    if not p0:
        # A prediction recorded against a zero price has no return to
        # measure; dividing would manufacture an infinity.
        return {
            "ticker": pred["ticker"], "date": pred["date"], "days": days,
            "price_then": p0, "price_now": round(price_now, 2),
            "change": None, "base_prorated": None, "deviation": None,
            "mature": False, "outcome": "sin_precio_base",
            "judged_price": None, "judged_on": None, "source": pred.get("source", SOURCE_QUICK),
            "bear": pred["bear"], "base": pred["base"], "bull": pred["bull"],
        }

    # Each prediction carries its own horizon; the module default is only a
    # fallback for records written before the field existed.
    horizon = pred.get("horizon_days", MATURITY_DAYS)
    base_ret = pred["base"] / p0 - 1
    mature = days >= horizon

    judged_price = price_at_maturity if mature else None
    if mature and judged_price is None:
        outcome = "sin_precio_al_vencimiento"
    elif mature:
        outcome = ("supero_bull" if judged_price > pred["bull"]
                   else "rompio_bear" if judged_price < pred["bear"]
                   else "en_rango")
    else:
        outcome = "en_curso"

    # The deviation is measured over the SAME window as the verdict. A
    # matured prediction is measured at its maturity close against the full
    # base return; an in-flight one at today's close against the pro-rated
    # part of it. Measuring realised return to today while pro-rating the
    # expectation to the horizon compares three years of price against one
    # year of forecast -- the same look-ahead the verdict was fixed for, and
    # it lands in `sesgo_medio`, the number CLAUDE.md's +/-10% rule reads.
    if mature and judged_price is None:
        # The verdict is withheld for want of the maturity close, and so is
        # the deviation: falling back to today's price here would put the
        # very look-ahead this function refuses straight into `sesgo_medio`.
        realised = expected = None
    elif mature:
        realised, expected = judged_price / p0 - 1, base_ret
    else:
        realised = price_now / p0 - 1
        expected = base_ret * min(days, horizon) / horizon

    return {
        "ticker": pred["ticker"], "date": pred["date"], "days": days,
        "price_then": p0, "price_now": round(price_now, 2),
        "change": round(realised, 4) if realised is not None else None,
        "base_prorated": round(expected, 4) if expected is not None else None,
        "deviation": (round(realised - expected, 4)
                      if realised is not None and expected is not None else None),
        "mature": mature, "outcome": outcome,
        "judged_price": round(judged_price, 2) if judged_price is not None else None,
        "judged_on": maturity_date(pred).isoformat() if mature else None,
        "source": pred.get("source", SOURCE_QUICK),
        "bear": pred["bear"], "base": pred["base"], "bull": pred["bull"],
    }


def _media(vals: list[float]) -> float | None:
    """The mean, or None for an empty sample. Zero is a measurement; None is
    the absence of one, and they must not print the same."""
    return round(sum(vals) / len(vals), 4) if vals else None


def track(reports_dir: Path, memoria_dir: Path, price_fn, today: date) -> dict:
    """Evaluate every saved prediction and write Memoria/calibracion.md.

    price_fn(ticker) -> float | None (injected so tests stay offline).
    """
    def _price(ticker: str, on: date | None = None) -> float | None:
        """Today's close, or the close on `on` when the caller's `price_fn`
        can serve a historical one. A one-argument `price_fn` (the older
        shape, and what the tests inject) simply has no history to give."""
        if on is None:
            return price_fn(ticker)
        try:
            return price_fn(ticker, on)
        except TypeError:
            return None

    rows = []
    predicciones = load_predictions(reports_dir)
    # Lo que no se pudo leer viaja con el resumen. Un track record vacío
    # porque no hay predicciones y uno vacío porque nadie supo leerlas se
    # veían idénticos desde fuera, y así estuvo semanas.
    descartadas = list(DESCARTADAS)
    for pred in predicciones:
        price_now = _price(pred["ticker"])
        if price_now is None:
            # BACKTESTING_AND_CALIBRATION.md's anti-bias rules: "no
            # survivorship bias" and "delisted securities remain in the
            # sample". Dropping the row was survivorship bias in its purest
            # form -- a name that went to zero, was acquired, or simply
            # stopped trading has no quote, and those are precisely the
            # predictions that went worst. Deleting them flatters the record.
            # The row stays, visibly unpriced, and is excluded from the
            # measured aggregates rather than from the sample.
            rows.append({
                "ticker": pred["ticker"], "date": pred["date"],
                "days": (today - date.fromisoformat(pred["date"])).days,
                "price_then": pred["price"], "price_now": None,
                "change": None, "base_prorated": None, "deviation": None,
                "mature": False, "outcome": "sin_precio_actual",
                "judged_price": None, "judged_on": None,
                "source": pred.get("source", SOURCE_QUICK),
                "bear": pred["bear"], "base": pred["base"], "bull": pred["bull"],
            })
            continue
        # A matured prediction is judged on the day its horizon closed, not
        # today (see `evaluate`): using today's price would be look-ahead.
        at_maturity = (_price(pred["ticker"], maturity_date(pred))
                       if (today - date.fromisoformat(pred["date"])).days
                       >= pred.get("horizon_days", MATURITY_DAYS) else None)
        rows.append(evaluate(pred, price_now, today, price_at_maturity=at_maturity))

    scored = [r for r in rows if r["deviation"] is not None]
    mature = [r for r in rows if r["mature"]]
    # Only a prediction we could price on its own maturity date has a
    # verdict; one we could not is excluded from the hit rate rather than
    # counted as a miss.
    judged = [r for r in mature if r["outcome"] != "sin_precio_al_vencimiento"]
    in_range = [r for r in judged if r["outcome"] == "en_rango"]
    summary = {
        "as_of": today.isoformat(),
        "total": len(rows),
        # Ficheros de predicción que existen y NO se pudieron leer. No entran
        # en ninguna media: no se sabe qué decían. Pero se cuentan y se dicen.
        "ilegibles": len(descartadas),
        "ilegibles_rutas": descartadas,
        # Rows kept in the sample but not measurable -- a delisted name, a
        # matured call with no maturity close. Reported so the reader can see
        # how much of the record the aggregates below do NOT cover.
        "sin_medir": len(rows) - len(scored),
        "maduras": len(mature),
        "en_rango": len(in_range),
        "juzgadas": len(judged),
        "hit_rate": round(len(in_range) / len(judged), 3) if judged else None,
        # Overall bias, kept for continuity -- but the quick scorecard and
        # the strict Cerebro profile score the same company differently, so a
        # single blended number measures two systems as one. The per-source
        # split is what a caller should read.
        "sesgo_medio": (round(sum(r["deviation"] for r in scored) / len(scored), 4)
                        if scored else None),
        # ...and the same blend across TIME. A matured row's deviation is the
        # full realised return against the full base case: a verdict. An
        # in-flight row's is today's return against the pro-rated slice of it:
        # a progress reading, and a noisy one early on -- three weeks of price
        # against three weeks of a twelve-month forecast moves on nothing.
        #
        # They answer different questions and were being averaged into one
        # number. Split, the in-flight figure gives signal from week one --
        # which is the whole point, because a verdict needs a year -- without
        # ever being mistaken for a verdict.
        "sesgo_en_curso": _media([r["deviation"] for r in scored if not r["mature"]]),
        "n_en_curso": len([r for r in scored if not r["mature"]]),
        "sesgo_maduras": _media([r["deviation"] for r in scored if r["mature"]]),
        "n_maduras_medidas": len([r for r in scored if r["mature"]]),
        "sesgo_por_fuente": {
            src: {
                "sesgo_medio": round(sum(r["deviation"] for r in group) / len(group), 4),
                "total": len(group),
            }
            for src in sorted({r["source"] for r in scored})
            for group in [[r for r in scored if r["source"] == src]]
        },
        "rows": rows,
    }

    memoria_dir.mkdir(parents=True, exist_ok=True)
    (memoria_dir / "calibracion.md").write_text(_render(summary), encoding="utf-8")
    # The markdown is for a reader; this is for the report pipeline, which
    # CLAUDE.md requires to declare a material bias. Parsing the prose back
    # out would be brittle, so the same summary is written structured.
    (memoria_dir / "calibracion.json").write_text(
        json.dumps({k: v for k, v in summary.items()
                    if k not in ("rows", "ilegibles_rutas")}, indent=2),
        encoding="utf-8")
    return summary


#: CLAUDE.md: "si reporta sesgo medio > ±10%, decláralo en el reporte y
#: ajusta la confianza de los targets a la baja" -- and calibracion.md's own
#: learning rule pairs that threshold with a minimum sample.
BIAS_THRESHOLD = 0.10
BIAS_MIN_PREDICTIONS = 10


def calibration_bias(memoria_dir: Path, source: str | None = None) -> dict | None:
    """The recorded track-record bias, or None when none has been harvested.

    Returns `{"sesgo_medio", "total", "material", "source"}`. `material` is
    True only when the bias exceeds ±10% over at least 10 predictions -- the
    threshold CLAUDE.md and calibracion.md both state. A bias measured over
    three predictions is noise, and declaring it would be worse than silence.

    `source` selects whose track record to read. The quick scorecard and the
    strict Cerebro profile score the same company differently, so a report
    produced by one must not declare the other's bias as its own; passing
    None reads the blended figure, which is only meaningful when a single
    system produced everything.
    """
    path = Path(memoria_dir) / "calibracion.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if source is not None:
        per_source = (data.get("sesgo_por_fuente") or {}).get(source)
        if not per_source:
            return None
        data = per_source
    bias, total = data.get("sesgo_medio"), data.get("total")
    if not isinstance(bias, (int, float)) or not isinstance(total, int):
        return None
    return {
        "sesgo_medio": bias,
        "total": total,
        "source": source,
        "material": abs(bias) > BIAS_THRESHOLD and total >= BIAS_MIN_PREDICTIONS,
    }


def _render(s: dict) -> str:
    lines = [
        "# Calibración del agente",
        "",
        f"**Actualizado:** {s['as_of']} · generado por `wbj track` — no editar a mano.",
        "",
        f"- Predicciones registradas: **{s['total']}** ({s['maduras']} maduras ≥12 meses, "
        f"{s.get('juzgadas', 0)} con precio al vencimiento"
        + (f", {s['sin_medir']} sin medir" if s.get("sin_medir") else "") + ")",
    ]
    if s.get("ilegibles"):
        lines += [
            f"- ⚠️ **{s['ilegibles']} ficheros de predicción no se pudieron leer** y "
            "no entran en ninguna media de aquí abajo.",
            "  No se sabe qué decían, así que no se cuentan ni a favor ni en contra —",
            "  pero tampoco se callan: un track record vacío porque no hay predicciones",
            "  y uno vacío porque nadie supo leerlas se veían idénticos desde fuera.",
        ]
    if s["hit_rate"] is not None:
        lines.append(f"- Acierto en rango Bear–Bull (juzgadas al vencimiento): "
                     f"**{s['hit_rate']:.0%}**")
    def _adj(v):
        return ("optimista — los targets Medio van por encima de la realidad"
                if v < 0 else
                "conservador — la realidad va por encima del escenario Medio")

    if s["sesgo_medio"] is not None:
        lines.append(f"- Sesgo medio vs escenario Medio (prorrateado): "
                     f"**{s['sesgo_medio']:+.1%}** ({_adj(s['sesgo_medio'])})")
    # El veredicto y el «cómo va» son dos preguntas distintas y estaban
    # promediadas en el mismo número. Separadas, la de en curso da señal desde
    # la primera semana —que es de lo que se trata, porque un veredicto tarda
    # un año— sin que nunca se pueda leer como un veredicto.
    if s.get("sesgo_maduras") is not None:
        lines.append(f"- **Veredicto** (sólo las {s['n_maduras_medidas']} vencidas, "
                     f"medidas el día que cerró su horizonte): "
                     f"**{s['sesgo_maduras']:+.1%}** ({_adj(s['sesgo_maduras'])})")
    if s.get("sesgo_en_curso") is not None:
        lines.append(f"- **Cómo va** (las {s['n_en_curso']} en curso, contra la parte "
                     f"prorrateada del escenario Medio): **{s['sesgo_en_curso']:+.1%}**")
        lines.append("  No es un veredicto: tres semanas de precio contra tres semanas "
                     "de un pronóstico a doce meses se mueven con casi nada. Sirve para "
                     "ver la deriva temprano, no para dar por buena ni por mala la tesis.")
    by_source = s.get("sesgo_por_fuente") or {}
    if len(by_source) > 1:
        lines.append("- Sesgo por sistema (el rápido y el estricto puntúan distinto, "
                     "no son comparables entre sí):")
        for src, agg in sorted(by_source.items()):
            lines.append(f"  - `{src}`: **{agg['sesgo_medio']:+.1%}** sobre {agg['total']} predicciones")
    lines += ["", "| Ticker | Fecha | Días | Precio→Hoy | Real | Medio esperado | Desvío | Estado |",
              "|---|---|---|---|---|---|---|---|"]
    for r in s["rows"]:
        estado = {"en_curso": "⏳ en curso", "en_rango": "✅ en rango",
                  "supero_bull": "🚀 superó Bull", "rompio_bear": "🔻 rompió Bear",
                  "sin_precio_al_vencimiento": "⚠️ sin precio al vencimiento",
                  "sin_precio_base": "⚠️ sin precio base",
                  "sin_precio_actual": "⚠️ sin cotización (deslistada?)"}[r["outcome"]]
        if r["deviation"] is None:
            lines.append(f"| {r['ticker']} | {r['date']} | {r['days']} | — | — | — | — | {estado} |")
            continue
        lines.append(
            f"| {r['ticker']} | {r['date']} | {r['days']} "
            f"| ${r['price_then']:,.2f}→${r['price_now']:,.2f} "
            f"| {r['change']:+.1%} | {r['base_prorated']:+.1%} "
            f"| {r['deviation']:+.1%} | {estado} |"
        )
    lines += ["", "> Regla de aprendizaje: si el sesgo medio supera ±10% con ≥10 predicciones,",
              "> ajustar los factores de escenario en `engine/wbj/targets.py` (_SCENARIOS)",
              "> y registrar el cambio en `Memoria/errores.md`.", ""]
    return "\n".join(lines)
