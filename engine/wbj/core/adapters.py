"""One reading of INDUSTRY_ADAPTERS.md, shared by every specialist.

Four modules were each deciding on their own what a non-default adapter
meant, and all four decided it the same wrong way: `!= "default_nonfinancial"`.
That test treats "this adapter replaces my model" and "this adapter adds
metrics to my model" as the same thing, so a software company was told
its analysis barely fit and an oil major was refused a valuation the
document explicitly asks for. Each copy also carried a 90/40 model-fit
split with no middle.

The distinction the document actually draws:

    Banks     "Prefer excess-return or dividend-discount valuation"
    Insurers  "...and excess-return valuation"
    REITs     "Replace EPS with FFO/AFFO ... NAV, and cap-rate valuation"
    Biotech   "Use risk-adjusted NPV by program"
        -> a different primary model. Conventional formulas must not run.

    Commodities  "Normalize price/deck and margins through a cycle"
        -> same formulas, inputs that must be cycle-normalized first.

    SaaS  "Add ARR growth, NRR, GRR, churn, ..."
        -> same formulas, plus extra metrics. No caveat is warranted.

Keeping this in one place is the point: the next adapter added here is
picked up by every specialist at once, instead of needing four edits that
can silently disagree.
"""

DEFAULT_ADAPTER = "default_nonfinancial"

#: Adapters whose methodology names a *different* primary model. Running
#: the conventional formulas on these produces a number the document
#: forbids, so specialists decline or heavily caveat instead.
MODEL_REPLACING: frozenset[str] = frozenset({"banks", "insurers", "reits", "biotech"})

#: Adapters that keep the conventional model but require normalized
#: inputs. The analysis runs; it carries a caveat.
MODEL_NORMALIZING: frozenset[str] = frozenset({"commodities_cyclicals"})

#: Adaptadores donde `INDUSTRY_ADAPTERS.md` reemplaza EXPLÍCITAMENTE el
#: retorno sobre capital invertido. Son dos, y sus palabras exactas:
#:
#:   Banks     — "Replace ROIC with ROE, ROTCE, net interest margin,
#:                efficiency ratio, CET1, loan-loss reserves..."
#:   Insurers  — "Use ROE, combined ratio, reserve development, solvency
#:                capital, book-value growth, and excess-return valuation."
#:
#: Es un subconjunto ESTRICTO de `MODEL_REPLACING`, y la diferencia importa.
#: A los REITs el documento les reemplaza el EPS (por FFO/AFFO) y el
#: apalancamiento (net debt/EBITDAre), no el ROIC; a biotech le dice que no
#: puntúe P/E, FCF yield ni calidad de margen "cuando no sea significativo".
#: Meter esos dos aquí sería extender la regla más allá de lo que el
#: documento dice -- y una métrica que se retira sin autoridad es tan
#: inventada como un número sin fuente.
RETURN_MODEL_REPLACED: frozenset[str] = frozenset({"banks", "insurers"})

#: Adaptadores donde el gasto por intereses ES el costo de la materia prima,
#: no una carga de deuda -- y por eso EBIT/intereses no mide nada.
#:
#: SOLO bancos, y la diferencia con `RETURN_MODEL_REPLACED` no es un detalle.
#: `INDUSTRY_ADAPTERS.md` pone la prohibicion explicita --"Do not use
#: enterprise-value/EBITDA, net-debt/EBITDA, or conventional FCFF"-- bajo
#: **Banks** y bajo nadie mas. A las aseguradoras el documento solo les dice
#: que USEN ROE, combined ratio, reserve development y solvency capital: dice
#: que anadir, nunca que retirar la cobertura.
#:
#: Y el dato dice lo mismo. Una aseguradora se financia con PRIMAS --el
#: float-- no con deuda; lo que pide prestado es deuda corporativa corriente:
#:
#:   bancos        JPM 0,74   BAC 0,48   COF 0,14   <- roto por construccion
#:   aseguradoras  PGR 51,2   UNH 4,7    CI 6,6     <- normal (KO da 8,3)
#:
#: Este set nacio como `RETURN_MODEL_REPLACED` reutilizado por comodidad, y
#: suprimia una metrica que funciona en 65 empresas, UnitedHealth incluida.
#: Lo destapo el barrido del mercado entero, no los 12 tickers de siempre.
COST_OF_FUNDS_IS_INTEREST: frozenset[str] = frozenset({"banks"})

#: Adapters that only add metrics. Nothing about the core formulas
#: changes, so no caveat and no confidence penalty.
MODEL_ADDITIVE: frozenset[str] = frozenset({DEFAULT_ADAPTER, "saas_subscriptions"})

#: La casa de bolsa (SIC 6211), que NO esta en ninguno de los tres sets de
#: arriba -- y esa ausencia es la decision, no un olvido.
#:
#: `INDUSTRY_ADAPTERS.md` abre diciendo que "the default formulas are designed
#: for NON-FINANCIAL operating companies", y da adaptador a bancos,
#: aseguradoras, REITs, SaaS, biotech, ciclicas y pre-ingresos. Una casa de
#: bolsa no es ninguna de las siete. Quedaba entonces con
#: `default_nonfinancial`, o sea valorada por FCFF DCF con las mismas formulas
#: que Coca-Cola -- exactamente lo que esa primera linea niega.
#:
#: Las dos salidas faciles estaban mal, y se midieron las dos:
#:
#: - Meterla con los BANCOS afirmaria que su interes es costo de fondeo y que
#:   le aplican CET1, reservas de credito y cartera vencida. El SIC 6211
#:   mezcla mesas de trading (GS 0,33x de cobertura, MS 0,45x) con corredores
#:   (IBKR 2,09x, SCHW 3,05x) y gestoras (BLK 11,20x): 7 de 17 bajo 1,5x. No
#:   es una clase, y suprimir la metrica en las diez que la tienen sana es el
#:   mismo error que ya costo a las aseguradoras.
#: - Dejarla en el DEFECTO afirma que las formulas convencionales le sirven,
#:   que es lo que el documento niega en su primera frase.
#:
#: La tercera es la unica cierta y el motor ya la implementa: un nombre que
#: `is_classified()` no reconoce es "one nobody has checked the conventional
#: formulas against". Con eso `business.py` hunde la confianza de modelo a 40
#: y `valuation.py` se NIEGA a poner precio en vez de fabricar uno. No afirma
#: que ninguna metrica sea inaplicable --el dato no lo respalda-- ni que el
#: modelo encaje. Dice lo unico que se sabe: empresa financiera, sin modelo
#: validado. Es el mismo sitio donde ya cae un REIT sin NAV/AFFO capturados.
#:
#: Registrarla en `MODEL_REPLACING` la volveria "clasificada" y desharia todo
#: esto. Si algun dia el Cerebro publica un adaptador de casas de bolsa, se
#: registra AHI y esta constante desaparece.
BROKER_DEALERS: str = "broker_dealers"
assert BROKER_DEALERS not in (MODEL_REPLACING | MODEL_NORMALIZING | MODEL_ADDITIVE), (
    "broker_dealers debe quedar SIN clasificar: registrarlo afirma un modelo "
    "que INDUSTRY_ADAPTERS.md no publica para una casa de bolsa.")

#: Adaptadores donde NO corren Beneish, Altman ni Piotroski.
#:
#: `DECISION_RULES.md` de riesgo: "Exclude financial companies and other
#: inapplicable industries". Son las DOS cosas, y por eso este set no es
#: `MODEL_REPLACING` a secas: los REITs y biotech entran por "otras industrias
#: inaplicables", y la casa de bolsa entra por "financial companies" aunque a
#: proposito no tenga modelo registrado.
#:
#: Sin esto, el Altman Z'' corria sobre GS y daba -0,30, sobre MS 0,90 y sobre
#: SCHW -1,98 -- lecturas de quiebra inminente en firmas sanas, que es la
#: misma alarma falsa que la cobertura de intereses en un banco. Un balance de
#: casa de bolsa es capital de trabajo ajeno: el Z'' lo lee como insolvencia.
FORENSIC_SCREENS_EXCLUDED: frozenset[str] = MODEL_REPLACING | frozenset({BROKER_DEALERS})


def excludes_forensic_screens(adapter: str | None) -> bool:
    """True cuando Beneish/Altman/Piotroski no aplican a este negocio.

    Distinto de `replaces_model`, a proposito: una casa de bolsa no tiene
    modelo de valuacion registrado --no esta en `MODEL_REPLACING`-- y aun asi
    es una empresa financiera, que es lo que `DECISION_RULES.md` excluye.
    """
    return (adapter or "").strip().lower() in FORENSIC_SCREENS_EXCLUDED

_MODEL_FIT: dict[str, float] = {
    DEFAULT_ADAPTER: 90.0,
    "saas_subscriptions": 90.0,      # additive; core formulas unchanged
    "commodities_cyclicals": 65.0,   # same formulas, cycle-normalized inputs
    "biotech": 50.0,                 # margin/FCF quality often not meaningful
    "banks": 40.0,                   # ROIC replaced by ROE/ROTCE
    "insurers": 40.0,                # ROE/combined ratio/excess return
    "reits": 40.0,                   # EPS replaced by FFO/AFFO
}


def replaces_return_model(adapter: str | None) -> bool:
    """True cuando el adaptador reemplaza el retorno sobre capital invertido.

    Predicado y no consulta directa al frozenset, por dos razones. La primera
    es consistencia: TODA pregunta sobre adaptadores en el engine se hace por
    aqui --`replaces_model`, `normalizes_inputs`, `is_classified`,
    `needs_caveat`-- y `RETURN_MODEL_REPLACED` era el unico set que los
    llamadores abrian a pelo, en dos sitios, copiando la expresion.

    La segunda es que esa copia normalizaba con `.lower()` y `replaces_model`
    no, asi que dos sets del mismo modulo se preguntaban de dos maneras a tres
    lineas de distancia. `valuation.py` documenta lo que eso cuesta: un
    `industry_adapter="bank_adapter"` --la ortografia de Victor-- valoro un
    banco por FCFF DCF. La normalizacion vive aqui dentro, como ya hace
    `is_subscription_business`, no en quien pregunta.
    """
    return (adapter or "").strip().lower() in RETURN_MODEL_REPLACED


def cost_of_funds_is_interest(adapter: str | None) -> bool:
    """True cuando el gasto por intereses es la materia prima del negocio.

    Distinto de `replaces_return_model`, a proposito: un banco y una
    aseguradora comparten que el ROIC no les aplica, y NO comparten como se
    financian. El banco toma depositos --el interes es su costo de ventas--;
    la aseguradora cobra primas y pide prestado como cualquiera.

    Ver `COST_OF_FUNDS_IS_INTEREST` para la cita del Cerebro y las cifras.
    """
    return (adapter or "").strip().lower() in COST_OF_FUNDS_IS_INTEREST


def replaces_model(adapter: str | None) -> bool:
    """True when the adapter's methodology calls for a different primary
    model than the conventional non-financial formulas."""
    return (adapter or "") in MODEL_REPLACING


def normalizes_inputs(adapter: str | None) -> bool:
    """True when the model still applies but its inputs must be
    normalized (through a cycle) before the output can be trusted."""
    return (adapter or "") in MODEL_NORMALIZING


def is_classified(adapter: str | None) -> bool:
    """True when INDUSTRY_ADAPTERS.md places this adapter in one of its
    three treatments (replacing, normalizing, additive).

    A name in none of them is one nobody has checked the conventional
    formulas against. `business.py` already floors its model-fit
    confidence on exactly this test — "claiming a good fit for it would be
    an assertion without evidence" — and valuation uses it to refuse
    rather than price the company with formulas that may not apply.
    """
    return (adapter or "") in (MODEL_REPLACING | MODEL_NORMALIZING | MODEL_ADDITIVE)


def needs_caveat(adapter: str | None) -> bool:
    """True when a specialist running conventional formulas owes the
    reader a warning. Additive adapters do not."""
    return replaces_model(adapter) or normalizes_inputs(adapter)


def model_fit(adapter: str | None) -> float:
    """Model-fit component (0-100) of the confidence formula.

    An unknown adapter returns the floor: a name nobody has classified
    is one nobody has checked the formulas against, and claiming a good
    fit for it would be an assertion without evidence.
    """
    return _MODEL_FIT.get(adapter or "", 40.0)


# ---------------------------------------------------------------------------
# Negocios de contratos recurrentes
#
# Vivía dentro de `business.py`, y `market.py` no tenía nada equivalente: ni
# una sola linea de NOT_APPLICABLE en todo el modulo. El resultado es que la
# misma pregunta -- "¿este negocio produce este dato?" -- se contestaba en un
# especialista y se ignoraba en el otro, que es exactamente lo que el docstring
# de arriba dice que este archivo existe para evitar.
#
# `MISSING_DATA_POLICY.md` empieza su arbol por ahi: "¿la metrica aplica? Si
# no, NOT_APPLICABLE e invoca el adaptador de industria". La distincion decide
# si un dato ausente sale del denominador de la cobertura o le cuesta la
# dimension a la empresa, y por eso dos empresas sanas pueden dar coberturas
# muy distintas sin que ninguna tenga un problema de datos.
#
# La pertenencia es AFIRMATIVA: un modelo gana estas metricas por correr sobre
# contratos recurrentes, no por no aparecer en una lista de excepciones. Al
# reves, cada industria nueva entraria por defecto y pagaria por no publicar
# algo que no produce.

SUBSCRIPTION_INDUSTRIES: tuple[str, ...] = (
    "software", "saas", "internet content", "information technology services",
    "telecom", "entertainment", "streaming", "broadcasting", "publishing",
    "security & protection services", "staffing",
    "specialty business services", "data processing",
)

#: Adaptadores que nombran economia de suscripcion directamente.
SUBSCRIPTION_ADAPTERS: tuple[str, ...] = ("saas", "subscription")

#: Claves de overlay que, si un analista las lleno, zanjan la pregunta: quien
#: aporto un puente de retencion ya establecio que la metrica existe aqui,
#: diga lo que diga la etiqueta del sector.
SUBSCRIPTION_OVERLAY_KEYS: tuple[str, ...] = ("retention", "customer_economics", "churn")


def is_subscription_business(packet: object, overlay: dict | None = None) -> bool:
    """True cuando la economia de clientes de esta empresa es de suscripcion.

    Compartida por `business.py` (BUS-NRR-020..BUS-PAYBACK-026) y `market.py`
    (MKT-ARPU-022, MKT-ADOPT-021): las dos preguntan lo mismo y tenian que
    contestarlo igual.
    """
    overlay = overlay or {}
    if any(isinstance(overlay.get(k), dict) and overlay.get(k)
           for k in SUBSCRIPTION_OVERLAY_KEYS):
        return True

    adapter = (getattr(getattr(packet, "analysis", None), "industry_adapter", "") or "").lower()
    if any(tag in adapter for tag in SUBSCRIPTION_ADAPTERS):
        return True

    industry = (getattr(getattr(packet, "security", None), "industry", "") or "").lower()
    return any(tag in industry for tag in SUBSCRIPTION_INDUSTRIES)
