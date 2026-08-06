"""Task 23: render a `FinalReport` as the auditable Markdown document.

`build_final_report` (Task 21) assembles the structured report; nothing
rendered it until now. This module is presentation only — it never
computes or reinterprets a number, so the rendered document and the
`FinalReport` JSON always agree.
"""

from __future__ import annotations

from wbj.i18n import L
from wbj.schemas.final_report import FinalReport


def _fmt(x: float | None, dec: int = 2) -> str:
    return "—" if x is None else f"{x:,.{dec}f}"


def _bullets(items: list[str], empty: str) -> str:
    return "\n".join(f"- {i}" for i in items) if items else f"_{empty}_"


def render_markdown(report: FinalReport, lang: str = "en",
                    coverage: dict[str, float] | None = None,
                    unscored: dict[str, bool] | None = None,
                    unscored_points: dict[str, float] | None = None,
                    calibration: dict | None = None,
                    ownership: dict | None = None,
                    insiders: dict | None = None,
                    revisit: str | None = None) -> str:
    """The full report as Markdown, in `lang` (labels only — the narrative
    prose is already in the language it was generated in)."""
    s, p, cs = report.security, report.profile, report.category_scorecard
    t = report.executive_thesis

    coverage, unscored = coverage or {}, unscored or {}
    # SCORING_ENGINE.md reweights "only within a dimension": a dimension below
    # 70% valid metric weight earns none of its points and there is no
    # category-level rescaling. Without this column a reader cannot tell
    # "measured and weak" from "never measurable" -- NVDA's market category
    # reads 5.2/20 with 13 of those points behind dimensions that were never
    # assessable.
    unscored_points = unscored_points or {}
    # CLAUDE.md's memory protocol, step 2: "Lee Memoria/calibracion.md: si
    # reporta sesgo medio > +/-10%, decláralo en el reporte y ajusta la
    # confianza de los targets a la baja." Nothing read it, so a measured
    # track-record bias never reached a reader.
    calib = ""
    if calibration and calibration.get("material"):
        bias = calibration["sesgo_medio"]
        direction = L(lang,
                      "optimistic — realised returns have run BELOW the base scenario",
                      "optimista — los retornos reales han ido POR DEBAJO del escenario Medio"
                      ) if bias < 0 else L(lang,
                      "conservative — realised returns have run ABOVE the base scenario",
                      "conservador — los retornos reales han ido POR ENCIMA del escenario Medio")
        advice = L(
            lang,
            "Treat the scenario targets below with correspondingly lower confidence "
            "(`Memoria/calibracion.md`).",
            "Toma los targets de escenario de abajo con confianza proporcionalmente menor "
            "(`Memoria/calibracion.md`).",
        )
        calib = (
            f"\n\n> **{L(lang, 'Track-record bias', 'Sesgo del track record')}:** "
            f"{bias:+.1%} {L(lang, 'across', 'sobre')} {calibration['total']} "
            f"{L(lang, 'recorded predictions', 'predicciones registradas')} — "
            f"{direction}. {advice}"
        )
    own = ownership or {}
    ins = insiders or {}

    # CLAUDE.md item 5: Form 4 activity, material above $1M in total.
    if ins.get("trades"):
        insider_rows = "\n".join(
            f"| {tr['name']} | {tr.get('title') or '—'} | "
            f"{L(lang, 'Buy', 'Compra') if tr['side'] == 'buy' else L(lang, 'Sell', 'Venta')} | "
            f"{_fmt(tr['value'], 0)} | {tr.get('last') or '—'} |"
            for tr in ins["trades"])
    elif ins.get("available"):
        insider_rows = ("| _" + L(lang,
            "No insider transaction exceeded $1M in total over the reported window.",
            "Ninguna transacción de insiders superó $1M en total en la ventana reportada.")
            + "_ | — | — | — | — |")
    else:
        insider_rows = ("| _" + L(lang, "Form 4 data unavailable.",
                                  "Datos de Formulario 4 no disponibles.")
                        + "_ | — | — | — | — |")

    if own.get("holders"):
        holder_rows = "\n".join(
            f"| {h['name']} | {_fmt(h.get('shares'), 0)} | {_fmt(h.get('value'), 0)} |"
            for h in own["holders"])
    else:
        holder_rows = ("| _" + L(lang,
            "13F institutional holdings are not available on the current data plan "
            "(FMP gates them above Starter; SEC EDGAR has no per-company holdings "
            "endpoint). Not estimated.",
            "Las tenencias institucionales 13F no están disponibles en el plan de datos "
            "actual (FMP las restringe sobre Starter; SEC EDGAR no tiene endpoint de "
            "tenencias por empresa). No se estiman.") + "_ | — | — |")

    exec_rows = "\n".join(
        f"| {e['name']} | {e.get('title') or '—'} | {_fmt(e.get('pay'), 0)} |"
        for e in own.get("executives", [])
    ) or f"| _{L(lang, 'no executive data', 'sin datos de directivos')}_ | — | — |"
    cats = [
        (L(lang, "Business", "Negocio"), cs.business),
        (L(lang, "Financial", "Finanzas"), cs.financial),
        (L(lang, "Market & Growth", "Mercado y Crecimiento"), cs.market),
        (L(lang, "Technical & Momentum", "Técnico y Momentum"), cs.technical),
        (L(lang, "Risk & Resilience", "Riesgo y Resiliencia"), cs.risk),
        (L(lang, "Valuation", "Valuación"), cs.valuation),
    ]
    _keys = ["business", "financial", "market", "technical", "risk", "valuation"]
    _ns = L(lang, "N/S — no evidence", "N/S — sin evidencia")
    cat_rows = "\n".join(
        f"| {name} | "
        f"{_ns if unscored.get(k) else f'{_fmt(e.points)} / {_fmt(e.max, 0)}'} | "
        f"{_fmt(e.confidence, 0)} | "
        f"{_fmt((coverage.get(k) or 0) * 100, 0)}% | "
        f"{(f'{_fmt(unscored_points.get(k), 1)} / {_fmt(e.max, 0)}' if unscored_points.get(k) else '—')} |"
        for (name, e), k in zip(cats, _keys)
    )

    level_rows = "\n".join(
        f"| {lv.label} | {_fmt(lv.value)} | {_fmt(lv.distance_percent, 1)}% |"
        for lv in report.important_levels
    ) or f"| _{L(lang, 'no levels available', 'sin niveles disponibles')}_ | — | — |"

    scen_rows = "\n".join(
        f"| {sc.name} | {_fmt(sc.per_share_value)} | {_fmt(sc.probability, 2)} | {sc.assumptions or '—'} |"
        for sc in report.valuation_scenarios
    ) or f"| _{L(lang, 'no scenarios available', 'sin escenarios disponibles')}_ | — | — | — |"

    # DECISION_RULES.md: "the main report shows each value AND the weighted
    # value; it does not show only the average." The rows above are each
    # value; this is the one the rule names alongside them.
    if report.valuation_weighted_value is not None:
        _weighted_label = L(lang, "Probability-weighted", "Ponderado por probabilidad")
        _weighted_note = L(lang, "sum(probability x value)", "suma(probabilidad x valor)")
        scen_rows += (
            "\n| **" + _weighted_label + " (VAL-SCEN-036)** | **"
            + _fmt(report.valuation_weighted_value) + "** | 1.00 | " + _weighted_note + " |"
        )

    # INSTITUTIONAL_VALUATION_ENGINE.md's terminal-value checklist: "terminal-
    # value share and implied exit multiple are shown." Section 6.6 makes the
    # share load-bearing -- above 75% it triggers the high-sensitivity warning
    # -- so the threshold is stated next to the number a reader checks it with.
    if report.terminal_value_share is not None:
        _share_label = L(lang, "Terminal-value share", "Peso del valor terminal")
        _share_note = L(lang, "above 75% triggers HIGH_TERMINAL_SENSITIVITY",
                        "sobre 75% dispara HIGH_TERMINAL_SENSITIVITY")
        scen_rows += (
            "\n| " + _share_label + " (VAL-TVS-042) | "
            + _fmt(report.terminal_value_share * 100, 1) + "% | — | " + _share_note + " |"
        )
    if report.implied_exit_multiple is not None:
        _mult_label = L(lang, "Implied exit multiple", "Múltiplo de salida implícito")
        _mult_note = L(lang, "what the DCF's own terminal value implies; cross-check only",
                       "lo que implica su propio valor terminal; solo chequeo cruzado")
        scen_rows += (
            "\n| " + _mult_label + " (VAL-TVE-013) | "
            + _fmt(report.implied_exit_multiple, 1) + "x | — | " + _mult_note + " |"
        )

    # Tres notas al pie se calculan AQUÍ y no dentro de la plantilla, aunque
    # visualmente quedaría más cerca de su sitio.
    #
    # El motivo es de lenguaje, no de estilo: hasta Python 3.11 la parte de
    # EXPRESIÓN de una f-string no puede contener una barra invertida, y estas
    # tres la llevan —las comillas escapadas de «"Not assessable"» y los
    # `ins.get('bought')` de una f-string anidada—. PEP 701 lo permite desde
    # 3.12, pero `runtime.txt` fija python-3.11.9, así que ahí el módulo entero
    # deja de importar con un `SyntaxError`: no falla el reporte, falla
    # `wbj.report` completo y con él `run_report` y `_insiders`.
    #
    # Sacarlas fuera cuesta tres nombres y hace que el archivo cargue en las dos
    # versiones. Los tests de `engine/tests/report/` lo fijan.
    _nota_ns = L(
        lang,
        'N/S means every dimension fell below the coverage threshold: the '
        'category is unscored for lack of evidence, not scored zero on merit. '
        '"Not assessable" counts the points behind dimensions whose data fell '
        'below 70% coverage: they earn nothing, so a low score there reflects '
        'missing evidence, not a judgment against the company.',
        'N/S significa que todas las dimensiones quedaron bajo el umbral de '
        'cobertura: la categoría no se evaluó por falta de evidencia, no sacó '
        'cero por mérito. "No evaluable" cuenta los puntos detrás de dimensiones '
        'cuya data quedó bajo 70% de cobertura: no ganan nada, así que un '
        'puntaje bajo ahí refleja evidencia faltante, no un juicio en contra de '
        'la empresa.',
    )
    _comprado = _fmt(ins.get("bought") or 0, 0)
    _vendido = _fmt(ins.get("sold") or 0, 0)
    _nota_insiders = L(
        lang,
        f"Bought {_comprado} · sold {_vendido}. "
        "Only insiders whose transactions exceed $1M in total are listed; smaller "
        "activity is reported by the SEC but is not treated as material here.",
        f"Compras {_comprado} · ventas {_vendido}. "
        "Solo se listan insiders cuyas transacciones superan $1M en total; la "
        "actividad menor la reporta la SEC pero aquí no se considera material.",
    )

    return f"""# {L(lang, "Investment research report", "Reporte de research de inversión")} — {s.ticker}

**{L(lang, "Exchange", "Bolsa")}:** {s.exchange} · **{L(lang, "Currency", "Moneda")}:** {s.currency}
**{L(lang, "Analysis timestamp", "Fecha de análisis")}:** {s.analysis_timestamp}
**{L(lang, "Data as of", "Datos al")}:** {s.knowledge_timestamp}

---

## 1. {L(lang, "Profile", "Perfil")}

**{p.label}** · {L(lang, "raw score", "puntaje crudo")} **{_fmt(p.raw_score, 2)}/100** · {L(lang, "confidence", "confianza")} {_fmt(p.total_confidence, 1)}

| | |
|---|---|
| {L(lang, "Passed gates", "Gates pasados")} | {", ".join(p.passed_gates) or "—"} |
| {L(lang, "Failed gates", "Gates fallados")} | {", ".join(p.failed_gates) or "—"} |
| {L(lang, "Overrides", "Overrides")} | {", ".join(p.overrides) or "—"} |
{f'| {L(lang, "Revisit on", "Revisitar en")} | {revisit} — {L(lang, "next scheduled earnings", "próximo reporte de resultados")} |' if revisit else ""}{calib}

## 2. {L(lang, "Category scorecard", "Scorecard por categoría")}

| {L(lang, "Category", "Categoría")} | {L(lang, "Points", "Puntos")} | {L(lang, "Confidence", "Confianza")} | {L(lang, "Coverage", "Cobertura")} | {L(lang, "Not assessable", "No evaluable")} |
|---|---|---|---|---|
{cat_rows}

_{_nota_ns}_

## 3. {L(lang, "Executive thesis", "Tesis ejecutiva")}

1. **{L(lang, "Business quality", "Calidad del negocio")}** — {t.business_quality}
2. **{L(lang, "Durability of value creation", "Durabilidad de la creación de valor")}** — {t.value_creation_durability}
3. **{L(lang, "Growth engine", "Motor de crecimiento")}** — {t.growth_engine}
4. **{L(lang, "Market validation", "Validación del mercado")}** — {t.market_validation}
5. **{L(lang, "Valuation message", "Mensaje de la valuación")}** — {t.valuation_message}
6. **{L(lang, "Key levels", "Niveles clave")}** — {t.key_levels_summary}
7. **{L(lang, "Primary risk", "Riesgo principal")}** — {t.primary_risk}

## 4. {L(lang, "Institutional holders and management", "Inversionistas institucionales y management")}

| {L(lang, "Holder (13F)", "Tenedor (13F)")} | {L(lang, "Shares", "Acciones")} | {L(lang, "Value", "Valor")} |
|---|---|---|
{holder_rows}

| {L(lang, "Executive", "Directivo")} | {L(lang, "Title", "Cargo")} | {L(lang, "Disclosed pay", "Compensación")} |
|---|---|---|
{exec_rows}

_{L(lang,
   "Whether management has a track record at other successful companies is a "
   "qualitative call, not a datapoint — it is left to the analyst rather than inferred here.",
   "Si el management tiene historial en otras empresas exitosas es un juicio "
   "cualitativo, no un dato — se deja al analista en vez de inferirlo aquí.")}_

## 5. {L(lang, "Insider activity (Form 4)", "Actividad de insiders (Formulario 4)")}

| {L(lang, "Insider", "Insider")} | {L(lang, "Role", "Rol")} | {L(lang, "Side", "Lado")} | {L(lang, "Total USD", "Total USD")} | {L(lang, "Last trade", "Última operación")} |
|---|---|---|---|---|
{insider_rows}

_{_nota_insiders}_

## 6. {L(lang, "Important levels", "Niveles importantes")}

| {L(lang, "Level", "Nivel")} | {L(lang, "Value", "Valor")} | {L(lang, "Distance", "Distancia")} |
|---|---|---|
{level_rows}

## 7. {L(lang, "Valuation scenarios", "Escenarios de valuación")}

| {L(lang, "Scenario", "Escenario")} | {L(lang, "Per share", "Por acción")} | {L(lang, "Probability", "Probabilidad")} | {L(lang, "Assumptions", "Supuestos")} |
|---|---|---|---|
{scen_rows}

## 8. {L(lang, "Thesis killers", "Rompe-tesis")}

{_bullets(report.thesis_killers, L(lang, "none identified", "ninguno identificado"))}

## 9. {L(lang, "Monitoring triggers", "Disparadores de monitoreo")}

{_bullets(report.monitoring_triggers, L(lang, "none defined", "ninguno definido"))}

## 10. {L(lang, "Missing or conflicted data", "Datos faltantes o en conflicto")}

{_bullets(report.missing_or_conflicted_data, L(lang, "none", "ninguno"))}

## 11. {L(lang, "Audit trail", "Trail de auditoría")}

- **{L(lang, "Report version", "Versión del reporte")}:** {report.report_version}
- **{L(lang, "Formula versions", "Versiones de fórmulas")}:** {", ".join(report.audit.formula_versions) or "—"}
- **{L(lang, "Validation", "Validación")}:** {report.audit.validation_summary or "—"}

---

_{L(lang,
    "Research classification with declared assumptions — not investment advice, "
    "not a promise of return. Every number traces to the packet and formulas above.",
    "Clasificación de research con supuestos declarados — no es asesoría de inversión "
    "ni promesa de retorno. Cada número es trazable al packet y a las fórmulas de arriba.")}_
"""
