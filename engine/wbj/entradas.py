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

#: Every key the skeleton writes, flattened.
SKELETON_KEYS: tuple[str, ...] = tuple(
    key for _, _, keys in _SECTIONS for key in keys
)

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
