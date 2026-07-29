"""Skeletons for `Entradas/<TICKER>.json`, the analyst-input file.

Six specialist metrics and six valuation models take inputs no free
structured source carries: market sizing, customer concentration, guidance
history, and the forecast assumptions APV/SOTP/real-option valuation need.
`overlay/from_packet._manual_overlay` reads them from `Entradas/<TICKER>.json`
and the engine names the missing key in each metric's own warning.

Writing that file by hand means knowing fourteen key names and their shapes.
This renders the skeleton instead: every key present, every value `null`, and
the source of each figure named beside it.

Two rules the generator must never break:

- **A skeleton scores nothing.** Every value is `null`, which behaves exactly
  like no file at all. Shipping placeholder numbers would put invented market
  sizing behind a real score, which is what `MISSING_DATA_POLICY.md`'s
  PROHIBITED_IMPUTATION list forbids and what makes the loader refuse
  `EJEMPLO.*` files outright.
- **It never overwrites.** `Entradas/NVDA.json` holds figures an analyst
  captured by hand; regenerating over it would destroy work silently.
"""

from __future__ import annotations

import json
from pathlib import Path

#: Victor's `DATASET.md` field name for every overlay key, so the file can be
#: audited against the contract `CLAUDE.md` step 2 says to validate against.
#: The engine grew its own vocabulary and only two keys ever matched his.
#:
#: Five are a true 1:1 rename and are accepted under Victor's name (see
#: `VICTOR_FIELD_ALIASES`). The rest are *elements* of a bundled field --
#: `tam_sam_som_sources` is "market size, scope, geography, product definition,
#: and forecast", not one number -- so renaming them 1:1 would either collapse
#: distinct inputs into one key or invent sub-names Victor never wrote. Those
#: keep their key and declare their parent field instead.
#: `specialists/business.py` already keeps `_OVERLAY_LINEAGE`, the same
#: mapping for every key IT reads -- including a reasoned `tam_source_tier ->
#: market_share_company_industry_3y` that this table contradicted with a
#: plainer `tam_sam_som_sources`. Two tables disagreeing about the same key is
#: worse than either answer, so business's entries win and this one only adds
#: the keys that table does not cover (valuation, market sizing).
def _dataset_home(field: str) -> str:
    """Which specialist's DATASET.md declares `field`.

    Derived, not assumed: a key business.py reads can be declared in another
    agent's contract. `tam_source_tier`'s field `tam_sam_som_sources` lives in
    `03_market_analysis`, and labelling it `01_business_analysis` because
    business is the module that reads it would send the auditor to a table
    that does not contain the row.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "Cerebro"
    for path in sorted(root.glob("0[1-6]_*/DATASET.md")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"| {field} "):
                return path.parent.name
    return ""


_BUSINESS_LINEAGE: dict[str, str] = {}
try:
    from wbj.specialists.business import _OVERLAY_LINEAGE as _BL

    _BUSINESS_LINEAGE = {
        k: (f"{v} ({h})" if (h := _dataset_home(v)) else v) for k, v in _BL.items()
    }
except Exception:  # pragma: no cover - import cycle safety
    pass

_EXTRA_DATASET_FIELD: dict[str, str] = {
    # The share form of `recurring_revenue`; business.py's lineage names only
    # the amount form, so this one is declared here.
    "recurring_revenue_share": "recurring_revenue_5y (01_business_analysis)",
    "catalysts": "catalyst_registry (03_market_analysis)",
    "backlog_history": "backlog_rpo_bookings (03_market_analysis)",
    "ntm_contracted": "backlog_rpo_bookings (03_market_analysis)",
    "ntm_revenue_estimate": "consensus_estimates_history (03_market_analysis)",
    "scenarios": "tam_sam_som_sources (03_market_analysis)",
    "scenario_overrides": "scenario_probabilities (06_valuation_analysis)",
    "thesis_killers": "regulatory_legal_events (05_risk_analysis)",
    "organic_growth_bridge": "organic_growth_bridge (02_financial_analysis)",
    "individual_estimates": "consensus_estimates_history (03_market_analysis)",
    "sector_breadth": "sector_breadth_and_relative_strength (03_market_analysis)",
    "share_history": "market_share_series (02_financial_analysis)",
    "peer_multiples": "peer_multiples_and_fundamentals (06_valuation_analysis)",
    "tam": "tam_sam_som_sources (03_market_analysis)",
    "tam_source": "tam_sam_som_sources (03_market_analysis)",
    "tam_history": "industry_revenue_history (03_market_analysis)",
    "company_relevant_revenue": "company_relevant_revenue (03_market_analysis)",
    # These four have no DATASET.md row, but Victor names their inputs
    # directly in FORMULAS.md's "inputs" column. Citing a stretched dataset
    # field instead would look authoritative and send the auditor to the wrong
    # row -- `forecast_drivers` is "units/users, pricing, share, revenue,
    # margins, reinvestment, ROIC, taxes", which contains none of them.
    "exit_multiple": "FORMULAS.md VAL-TVE-013 (terminal EBITDA/revenue/earnings, multiple)",
    "segment_multiples": "FORMULAS.md VAL-SOTP-026 (segment forecasts/peers, corporate items)",
    "unlevered_fcf": "FORMULAS.md VAL-APV-019 (unlevered FCF, unlevered cost, debt schedule)",
    "real_option_project": "FORMULAS.md VAL-ROPT-038 (project PV, investment, volatility, time)",
    # These two do have a dataset row, and it names them explicitly:
    # `share_claims` lists "convertibles", `debt_schedule` is the field name.
    "debt_schedule": "debt_schedule (02_financial_analysis)",
    "convertibles": "share_claims (06_valuation_analysis)",
    "rd_useful_life": "FORMULAS.md VAL-RD-002 (historical R&D, useful life)",
    "lease_commitments": "lease_schedule (02_financial_analysis)",
}

#: business.py's own entries win; this module adds the rest.
VICTOR_DATASET_FIELD: dict[str, str] = {**_EXTRA_DATASET_FIELD, **_BUSINESS_LINEAGE}

#: Victor's field name -> the overlay key it maps to, for the five where the
#: correspondence is exact. Writing `management_guidance_history` in the file
#: must work, because that is what his contract calls it.
VICTOR_FIELD_ALIASES: dict[str, str] = {
    "industry_revenue_history": "tam_history",
    "management_guidance_history": "guidance_history",
    "customer_revenue_shares": "customer_shares",
}

#: One entry per analyst-supplied key: the key, and the note that goes above
#: it saying where the figure comes from. Ordered as written to the file.
#:
#: `tests/test_entradas_skeleton.py` greps the specialists for the keys they
#: name in their own "set `key`" warnings and asserts this list covers them,
#: so a newly-added input cannot silently go undocumented here.
_SECTIONS: list[tuple[str, list[str], list[str]]] = [
    (
        "TAM / tamano de mercado",
        ["MKT-TAM-001, MKT-CAGR-004, MKT-PEN-005.",
         "No hay fuente estructurada gratuita: sale de un estudio de industria",
         "o de una presentacion del emisor, CON fecha y atribucion.",
         "DECISION_RULES.md: un TAM sin atribuir es tier 5 y puntua 0.",
         "Si pones `tam` sin `tam_source` y `tam_source_tier` (1-4), se descarta."],
        ["tam", "tam_history", "tam_source", "tam_source_tier"],
    ),
    (
        "Ingreso en ESE mercado",
        ["Denominador de la penetracion (MKT-PEN-005). 10-K, nota de segmentos.",
         "Debe cubrir el mismo perimetro que el TAM: si el TAM es de",
         "smartphones, aqui va la linea de smartphones, no el ingreso total."],
        ["company_relevant_revenue"],
    ),
    (
        "Concentracion de clientes",
        ["BUS-CONC-003, BUS-HHI-004, RSK-CUST-017.",
         "10-K, nota 'Concentrations of Credit Risk' o Item 1A.",
         "Fraccion 0-1 sobre ingreso total.",
         "Si el filing no divulga ningun cliente sobre el umbral de reporte,",
         "no hay cifra que poner: dejarlo en null ES la respuesta correcta."],
        ["largest_customer_share", "customer_shares"],
    ),
    (
        "Cumplimiento de guia",
        ["BUS-GUIDE-027. Punto medio de la guia dada vs. lo que se reporto.",
         "Guia: 8-K / earnings release del trimestre anterior.",
         "Actual: 10-Q o 10-K del trimestre en cuestion.",
         "Forma: [{period, guidance_midpoint, actual}, ...]. Minimo 2 trimestres.",
         "Ojo: no toda empresa da guia formal; si no la da, va null."],
        ["guidance_history"],
    ),
    (
        "Valuacion: SUPUESTOS TUYOS, no datos del filing",
        ["DATA_POLICY.md los clasifica clase A (assumption), no R (reported).",
         "Cada uno que declares queda en el rastro de auditoria como supuesto.",
         "",
         "exit_multiple (VAL-TVE-013): EV/EBIT de salida, solo cross-check.",
         "  El multiplo debe cuadrar con crecimiento terminal, margenes, ROIC",
         "  y riesgo. No se copia el multiplo del ciclo actual a perpetuidad.",
         "segment_multiples (VAL-SOTP-026): EV/Ventas por segmento REPORTADO.",
         "  Los nombres deben calzar con los segmentos del 10-K."],
        ["exit_multiple", "segment_multiples"],
    ),
    (
        "APV: necesita LAS DOS llaves",
        ["VAL-APV-019. Saldo de deuda por ano proyectado Y flujo libre",
         "desapalancado por ano. Con una sola, la metrica sigue MISSING.",
         "DECISION_RULES.md limita APV a apalancamiento cambiante; un solo",
         "saldo reportado es justo el supuesto estatico que APV reemplaza."],
        ["debt_schedule", "unlevered_fcf"],
    ),
    (
        "Catalizadores y backlog -- dimension product_and_business_catalysts",
        ["MKT-BACK-015, COVER-016, CAT-019, TDEC-020 (4 pts).",
         "",
         "catalysts: lista. AGENT.md pide 'at least three catalysts with",
         "  evidence class and date'. CADA uno necesita las seis llaves o",
         "  MKT-CAT-019 no puntua -- solo con event/months no basta:",
         "  {event, months_to_event, probability (0-1), impact (usd),",
         "   evidence_quality (0-1), source}",
         "  Fuente: 10-K/8-K -- lanzamientos, capacidad, regulatorio,",
         "  contratos, precios. Solo eventos que el emisor haya declarado.",
         "",
         "backlog_history: RPO/backlog por trimestre, 8 trimestres.",
         "  Si la empresa etiqueta RevenueRemainingPerformanceObligation en",
         "  XBRL el motor ya lo saca solo; esto es para las que no.",
         "",
         "ntm_contracted / ntm_revenue_estimate: backlog que se reconoce en",
         "  12 meses, y el ingreso estimado de esos 12 meses. Su cociente es",
         "  MKT-COVER-016. Varios emisores dejaron de etiquetar el porcentaje",
         "  a 12 meses (NVDA en 2019, Microsoft en 2018), y el motor no toma",
         "  un par viejo: sin captura, la metrica queda MISSING.",
         "",
         "Verificado en AAPL: la dimension pasa de 0% a 100% y Mercado de",
         "5.10 a 7.81 de 20."],
        ["catalysts", "backlog_history", "ntm_contracted", "ntm_revenue_estimate"],
    ),
    (
        "Calidad del crecimiento -- dimension revenue_quality_and_growth",
        ["FIN-GR-004 y FIN-GR-005. La dimension exige 4 de 5 metricas validas",
         "(SCORING.md) y sin estas solo llega a 3, asi que NO puntua nada.",
         "",
         "share_history: cuota de mercado por ano, minimo 3 puntos, en decimal",
         "  (0.28 = 28%). La misma serie que usa MKT-SHDELTA-007. Victor da la",
         "  banda: +/-0.25 punto porcentual al ano separa perder / estable /",
         "  ganar cuota.",
         "",
         "organic_growth_bridge: {organic_growth, total_growth} en decimal.",
         "  Del 10-K o la llamada de resultados: cuanto del crecimiento fue",
         "  organico y cuanto por adquisiciones/FX. Se reporta la cifra pero",
         "  NO puntua: FORMULAS.md no le da banda numerica, asi que el score",
         "  sigue viniendo del juez (BAD/GOOD/EXCELLENT)."],
        ["share_history", "organic_growth_bridge"],
    ),
    (
        "Amplitud de sector y dispersion -- cierra 3 dimensiones",
        ["sector_breadth (MKT-SECB-023 y TECH-BREAD-039): cuantos miembros del",
         "  sector estan sobre su media de 50 dias, y cuantos miembros validos",
         "  hay. {above_50dma_count, valid_members}. Se acepta tambien",
         "  `above_50dma` como nombre del primero.",
         "  Victor lo exige explicito: 'Point-in-time sector membership required",
         "  for breadth' -- la lista de miembros debe ser la de ESA fecha, no la",
         "  de hoy, o el resultado tiene sesgo de supervivencia.",
         "  UNA sola llave cierra dos dimensiones: operating_leverage (Mercado)",
         "  y sector_breadth_and_volatility_quality (Tecnico).",
         "",
         "individual_estimates (MKT-DISP-013): la lista de estimados de CADA",
         "  analista, no low/high/avg. FORMULAS.md pide stdev sobre los",
         "  individuales y ningun proveedor configurado los sirve: derivar una",
         "  desviacion estandar de un rango seria asumir una distribucion que",
         "  el dato nunca declaro."],
        ["sector_breadth", "individual_estimates"],
    ),
    (
        "Escenarios y thesis killers",
        ["OJO: son DOS llaves distintas que antes compartian nombre y se",
         "tumbaban entre si. Cada una tiene su forma.",
         "",
         "scenarios (MKT-SCEN-025): LISTA de pares [probabilidad, resultado_usd].",
         "  Las probabilidades DEBEN sumar 1.0 o sale CONFLICTED.",
         "  Ej: [[0.25, 1.4e11], [0.50, 1.95e11], [0.25, 2.6e11]]",
         "",
         "scenario_overrides (valuacion): DICT con bear/base/bull para",
         "  sobreescribir los supuestos del DCF.",
         "  Ej: {\"bear\": {\"growth\": 0.02, \"margin\": 0.15}, ...}",
         "",
         "thesis_killers (RSK-THESIS-035): DECISION_RULES.md pide al menos 3.",
         "  Cada uno necesita los cuatro factores como supuestos 0-1 -- eso es",
         "  lo que FORMULAS.md llama 'explicit 0-1 assumptions':",
         "  {risk, probability, impact, detectability, time_urgency,",
         "   early_warning_metric}",
         "  La formula es Probability * Impact * (1-Detectability) * TimeUrgency,",
         "  y la fila reporta el killer de mayor prioridad."],
        ["scenarios", "scenario_overrides", "thesis_killers"],
    ),
    (
        "Anulaciones opcionales",
        ["scenarios (MKT-SCEN-025): resultado de mercado ponderado por",
         "  escenario. Lista de pares [probabilidad, resultado_usd]. Las",
         "  probabilidades DEBEN sumar 1.0 o la metrica sale CONFLICTED --",
         "  FORMULAS.md, y el motor no renormaliza en silencio.",
         "  Ej: [[0.25, 8.0e10], [0.50, 1.0e11], [0.25, 1.3e11]]",
         "",
         "peer_multiples (VAL-REL-034): lista de multiplos precio/ventas de",
         "  los comparables. Si la dejas en null el motor los calcula del",
         "  panel de peers; ponla solo si tienes un set mejor."],
        ["scenarios", "peer_multiples"],
    ),
    (
        "Economia de clientes -- SOLO negocios de suscripcion",
        ["BUS-NRR-020, GRR-021, CHURN-022, LTV-023, CAC-024, LTVCAC-025,",
         "PAYBACK-026. Es la dimension `customer_economics` (3 pts).",
         "",
         "Si la empresa NO es de suscripcion, deja las tres en null: el",
         "adaptador industrial las marca NOT_APPLICABLE y la dimension se",
         "reescala fuera sin costar puntos (MISSING_DATA_POLICY paso 1).",
         "Si SI lo es y las dejas vacias, la dimension cuesta sus 3 puntos.",
         "",
         "retention  = {begin, expansion, contraction, churn}   (en $ de ingreso)",
         "churn      = {lost, begin_customers}                  (en # de clientes)",
         "customer_economics = {arpu, monthly_arpu, gross_margin,",
         "                      customer_life_years, cac_spend, new_customers}",
         "",
         "Fuente: 10-K/10-Q, seccion de metricas operativas o carta a",
         "accionistas. Pocas empresas divulgan todo esto; lo que no este",
         "divulgado va null."],
        ["retention", "churn", "customer_economics"],
    ),
    (
        "Ajustes de negocio (opcionales)",
        ["capitalized_rd_adjustment (BUS-REINV-018 -> BUS-SG-019): I+D",
         "  capitalizada que se suma a la reinversion. Capitalizar I+D exige",
         "  una vida de amortizacion que ningun filing declara, asi que es",
         "  ajuste del analista. Verificado en AAPL: la reinversion pasa de",
         "  0.34 a 0.60 y el crecimiento sostenible de 0.25 a 0.46.",
         "  En null no se ajusta (se trata como 0). DATASET.md:",
         "  capital_allocation_10y.",
         "contract_protection (BUS-STAB-009 / durabilidad): fraccion 0-1 del",
         "  ingreso concentrado que esta bajo contrato. SCORING.md levanta el",
         "  tope por concentracion solo 'unless contract protection is",
         "  quantified' -- esta llave es esa cuantificacion.",
         "recurring_revenue: el MONTO absoluto de ingreso recurrente, si lo",
         "  tienes en vez de la fraccion. El motor lo divide entre el ingreso."],
        ["capitalized_rd_adjustment", "contract_protection", "recurring_revenue"],
    ),
    (
        "Ingreso recurrente",
        ["BUS-REC-002. FRACCION 0-1 del ingreso total, no el monto.",
         "10-K: \"subscription revenue was 45% of total net revenue\" -> 0.45.",
         "Si tienes el monto absoluto en vez de la fraccion, usa la llave",
         "`recurring_revenue` y el motor lo divide entre el ingreso."],
        ["recurring_revenue_share"],
    ),
    (
        "I+D capitalizada y arrendamientos",
        ["VAL-RD-002 / VAL-RDA-003: el historial de I+D sale del 10-K, pero la",
         "vida util NO. FORMULAS.md: \"Useful life is an industry assumption\",",
         "asi que el motor no elige una: sin este dato ambas quedan MISSING.",
         "Tipico: 3 anos software, 5-10 industrial, 10+ farmaceutica.",
         "",
         "VAL-LEASE-004: pagos de arrendamiento operativo por ano futuro,",
         "10-K nota de arrendamientos. Se descuentan al costo de deuda pre-tax."],
        ["rd_useful_life", "lease_commitments"],
    ),
    (
        "Opcion real",
        ["VAL-ROPT-038. {project_pv, investment, volatility, years}.",
         "Solo aplica si hay un proyecto discreto y opcional que valuar."],
        ["real_option_project"],
    ),
    (
        "Convertibles y dilucion",
        ["VAL-CONV-039. 10-K, nota de deuda.",
         "[{name, face, conversion_price, conversion_shares}, ...]",
         "Si no hay convertibles vivos, null es correcto."],
        ["convertibles"],
    ),
]

def _template_keys() -> tuple[str, ...]:
    """Every analyst key `Entradas/EJEMPLO.NVDA.json` documents.

    That file is the reference: it carries an illustrative value and a note
    for each input, and `tests/test_entradas_template.py` already holds it to
    covering everything the specialists read. The generated skeleton used to
    list only the two dozen keys curated in `_SECTIONS`, so analysing a new
    ticker produced a file missing most of what the engine reads -- an input
    the analyst never sees is one they cannot supply.

    Deriving the rest from the template keeps one source of truth instead of
    a third list that drifts from the other two.
    """
    import json as _json
    from pathlib import Path as _Path

    try:
        root = _Path(__file__).resolve().parents[2] / "Entradas" / "EJEMPLO.NVDA.json"
        data = _json.loads(root.read_text(encoding="utf-8"))
    except Exception:  # pragma: no cover - template absent
        return ()
    return tuple(k for k in data if not k.startswith("_"))


#: Curated sections first, then everything else the template documents.
_CURATED: tuple[str, ...] = tuple(key for _, _, keys in _SECTIONS for key in keys)
_REST: tuple[str, ...] = tuple(k for k in _template_keys()
                               if k not in _CURATED and k != "judgments")

#: Every key the skeleton writes, flattened.
SKELETON_KEYS: tuple[str, ...] = _CURATED + _REST

_HEADER = [
    "ENTRADAS DEL ANALISTA -- {ticker}",
    "Todos los valores estan en null a proposito: un null se comporta igual",
    "que no tener archivo, y la metrica queda MISSING.",
    "",
    "REGLA: no inventes ninguna cifra. Si el filing no la divulga, dejala null.",
    "MISSING_DATA_POLICY.md: la ausencia se propaga, nunca se imputa.",
]


def render_skeleton(ticker: str) -> str:
    """The skeleton file's text for one ticker, values all `null`."""
    out: dict[str, object] = {}
    n = 0

    def note(line: str) -> None:
        nonlocal n
        n += 1
        out[f"_{n:02d}"] = line

    for line in _HEADER:
        note(line.format(ticker=ticker.upper()))

    for title, lines, keys in _SECTIONS:
        note("")
        note(f"--- {title} ---")
        for line in lines:
            note(line)
        for key in keys:
            # Name the DATASET.md field each key belongs to. CLAUDE.md step 2
            # validates the packet against that contract, and the engine's own
            # key names do not match it -- without this line an auditor cannot
            # map the file back to what Victor actually specified.
            field = VICTOR_DATASET_FIELD.get(key)
            if field:
                # Four keys cite FORMULAS.md and carry their own doc name;
                # prefixing "DATASET.md:" onto those read "DATASET.md:
                # FORMULAS.md ...", pointing the auditor at a row that is not
                # there.
                where = field if field.startswith("FORMULAS.md") else f"DATASET.md {field}"
                alias = next((v for v, k in VICTOR_FIELD_ALIASES.items() if k == key), None)
                note(f"`{key}` -> {where}"
                     + (f" -- tambien aceptado como `{alias}`" if alias else ""))
            out[key] = None

    if _REST:
        note("")
        note("--- Otras entradas que el motor lee ---")
        note("Su forma y un ejemplo estan en Entradas/EJEMPLO.NVDA.json.")
        note("Todas opcionales: en null, su metrica queda MISSING y se dice.")
        for key in _REST:
            out[key] = None

    note("")
    note("Juicios cualitativos: corre `wbj judgments` para los slots pendientes.")
    out["judgments"] = {}
    return json.dumps(out, indent=2, ensure_ascii=True) + "\n"


def write_skeleton(directory: Path, ticker: str, *, force: bool = False) -> tuple[bool, str]:
    """Write `<TICKER>.json` into `directory`. Returns (written, message).

    Refuses an existing file unless `force`. A populated `Entradas/` file is
    hand-captured work; silently regenerating over it would destroy figures no
    provider can give back.
    """
    path = Path(directory) / f"{ticker.upper()}.json"
    if path.exists() and not force:
        return False, f"{path.name}: ya existe, no se toca"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_skeleton(ticker), encoding="utf-8")
    return True, f"{path.name}: esqueleto escrito ({len(SKELETON_KEYS)} llaves)"
