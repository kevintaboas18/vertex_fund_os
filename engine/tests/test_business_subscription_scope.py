"""Qué negocios "ganan" las métricas de suscripción, y cuáles no.

`BUS-NRR-020..BUS-PAYBACK-026` —retención neta, retención bruta, churn, LTV,
CAC, LTV/CAC y CAC payback— sólo miden algo en un negocio de contratos
recurrentes. `FORMULAS.md` las tipa así de forma explícita: BUS-NRR-020 es
"Subscription/business-model adapter only" y BUS-PAYBACK-026 es "subscription
adapter".

La distinción decide si un dato ausente sale del denominador de la cobertura
(`NOT_APPLICABLE`) o le cuesta la dimensión a la empresa (`MISSING`). Y esa es
la razón por la que dos empresas del mismo tamaño y calidad pueden salir con
coberturas muy distintas sin que nada esté mal en sus datos.

Este test nació de la pregunta de Victor: por qué dos empresas comparables dan
coberturas distintas. Medido: UNH salía en 0,583 de business y NVDA en 0,913,
con el MISMO adaptador `default_nonfinancial`. La diferencia eran estas siete
metricas, aplicables para una y no para la otra.
"""

from __future__ import annotations

import pytest

from wbj.specialists import business as bus


def test_health_plans_do_not_earn_the_saas_metric_set():
    """`INDUSTRY_ADAPTERS.md` le da a las aseguradoras un juego COMPLETAMENTE
    distinto: "ROE, combined ratio, reserve development, solvency capital,
    book-value growth". No nombra NRR, GRR ni churn en ninguna parte.

    Un plan de salud cobra primas recurrentes, cierto. Pero no publica un
    puente de ingresos por cohorte ni un CAC payback: publica afiliados y
    ratio de siniestralidad. Cobrarle siete metricas que su industria no
    reporta en esa forma es el mismo error que el comentario del modulo dice
    haber arreglado para Coca-Cola, con otra etiqueta.
    """
    assert not any("healthcare plan" in i for i in bus._SUBSCRIPTION_INDUSTRIES), (
        "'healthcare plans' volvio a la lista de suscripcion: eso le cuesta a "
        "UNH siete metricas que su industria no publica")


def test_software_still_earns_them():
    """El otro lado del mismo cuidado. Un SaaS que no publica su retencion
    neta tiene un hueco REAL, y tiene que verse: sacarlo del denominador seria
    premiarlo por no divulgar."""
    assert any("software" in i or "saas" in i for i in bus._SUBSCRIPTION_INDUSTRIES)


def test_the_membership_list_is_affirmative():
    """Se entra por parecerse a un negocio de suscripcion, no por no estar en
    una lista de excepciones. Al reves, cualquier industria nueva entraria por
    defecto y pagaria metricas que no le tocan."""
    assert bus._SUBSCRIPTION_INDUSTRIES, "la lista no puede quedar vacia"
    for entrada in bus._SUBSCRIPTION_INDUSTRIES:
        assert entrada == entrada.lower(), (
            f"{entrada!r} no esta en minusculas: la comparacion la hace en "
            "minusculas y una mayuscula lo dejaria fuera en silencio")


# --- BUS-REC-002: ingresos recurrentes -------------------------------------

def test_recurring_revenue_crosses_the_same_door():
    """`DATASET.md` tipa `recurring_revenue_5y` como **conditional** y lo define
    como "contractual or subscription revenue with recurring character".

    Un negocio que no corre sobre contratos recurrentes no tiene esa cifra
    ausente: no la tiene. Es el paso 1 del arbol de `MISSING_DATA_POLICY.md`,
    la misma puerta que ya cruzaban BUS-NRR-020..026 -- y de la que esta fila
    se habia quedado fuera, cobrandosela a Coca-Cola y a Exxon.
    """
    import inspect

    fuente = inspect.getsource(bus)
    bloque = fuente[fuente.index("---- BUS-REC-002"):]
    bloque = bloque[:bloque.index('add("BUS-REC-002"')]
    assert "NOT_APPLICABLE" in bloque, (
        "BUS-REC-002 volvio a cobrarse a todo negocio, tenga o no ingreso "
        "recurrente que reportar")


def test_an_analyst_can_say_it_does_apply_after_all():
    """La industria es solo un PROXY de una pregunta que es de empresa.

    NVDA es "Semiconductors" y aun asi tiene ingreso recurrente que no reporta
    -- NVIDIA AI Enterprise. Su propio archivo lo dice: "no es que no aplique,
    es que no la reporta". Sin este escape se marcaria como inaplicable algo
    que un humano ya verifico que aplica, y NVDA saltaba a una cobertura
    perfecta de 1,000 tapando un hueco real.
    """
    import inspect

    fuente = inspect.getsource(bus)
    assert "recurring_revenue_applies" in fuente, (
        "se perdio el escape del analista sobre el proxy de industria")


def test_nvda_declares_it_because_its_own_note_says_so():
    """El archivo de NVDA documentaba el hallazgo en prosa desde antes. Ahora
    ademas lo declara donde el motor puede leerlo."""
    import json
    from pathlib import Path

    ruta = Path(__file__).parent.parent.parent / "Entradas" / "NVDA.json"
    if not ruta.exists():
        return  # el archivo del analista es opcional
    d = json.loads(ruta.read_text(encoding="utf-8"))
    if "_recurring_ausente" in d:
        assert d.get("recurring_revenue_applies") is True, (
            "la nota dice que SI aplica; la clave que lee el motor tiene que "
            "decir lo mismo, o el hueco real de NVDA se esconderia")
