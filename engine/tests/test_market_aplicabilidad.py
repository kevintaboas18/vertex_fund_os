"""Market cobraba a toda empresa métricas que su modelo de negocio no produce.

`market.py` no tenía **ni una sola línea** de `NOT_APPLICABLE`. Todo lo que no
podía calcular contaba como "me falta", así que a Coca-Cola se le exigía ARPU
—no tiene usuarios que promediar, vende concentrado a embotelladores— y a una
refresquera se le exigía backlog contratado.

`MISSING_DATA_POLICY.md` empieza su árbol de decisión justo ahí: *"¿la métrica
aplica? Si no, `NOT_APPLICABLE` e invoca el adaptador de industria"*. La
distinción decide si un dato ausente sale del denominador de la cobertura o le
cuesta la dimensión a la empresa — y es la razón por la que dos empresas sanas
daban coberturas muy distintas sin que ninguna tuviera un problema de datos.

`business.py` ya contestaba esta pregunta para sus propias métricas de
suscripción. Este módulo no la contestaba en absoluto. Ahora las dos usan la
misma definición, en `core/adapters.py`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wbj.core import adapters as _adapters
from wbj.core.nullstates import NullState
from wbj.schemas.packet import Packet
import wbj.specialists.market as mkt

_FIXTURE = Path(__file__).parent / "fixtures" / "packet" / "NVDA_packet.json"


def _packet(industry: str = "Semiconductors", adapter: str = "default_nonfinancial") -> Packet:
    data = json.loads(_FIXTURE.read_text())
    data["analysis"]["industry_adapter"] = adapter
    data["security"]["industry"] = industry
    return Packet.model_validate(data)


def _estado(salida, metric_id: str):
    for m in salida.metrics:
        if m.metric_id == metric_id:
            return m.state
    pytest.fail(f"{metric_id} no aparece en la salida")


# --- métricas de modelo de negocio ----------------------------------------

@pytest.mark.parametrize("metric_id", ["MKT-ARPU-022", "MKT-ADOPT-021"])
def test_a_business_without_users_does_not_owe_an_arpu(metric_id):
    """Coca-Cola vende concentrado a embotelladores. `FORMULAS.md` pide para
    ARPU "revenue and average users or issuer KPI": sin usuarios que promediar
    su ARPU no falta, no existe."""
    fuera = mkt.run(_packet(industry="Beverages - Non-Alcoholic"))
    assert _estado(fuera, metric_id) is NullState.NOT_APPLICABLE


@pytest.mark.parametrize("metric_id", ["MKT-ARPU-022", "MKT-ADOPT-021"])
def test_a_subscription_business_still_owes_it(metric_id):
    """El otro lado del mismo cuidado: un SaaS que no publica su ARPU tiene un
    hueco REAL y tiene que verse. Sacarlo del denominador seria premiarlo por
    no divulgar."""
    fuera = mkt.run(_packet(industry="Software - Infrastructure",
                            adapter="saas_subscriptions"))
    assert _estado(fuera, metric_id) is NullState.MISSING


def test_both_specialists_answer_it_the_same_way():
    """La pregunta ("¿este negocio corre sobre contratos recurrentes?") la
    hacian los dos y la contestaba uno. Ahora sale del modulo compartido, que
    existe para que no puedan discrepar en silencio."""
    assert callable(_adapters.is_subscription_business)
    assert _adapters.is_subscription_business(_packet("Software - Infrastructure"))
    assert not _adapters.is_subscription_business(_packet("Beverages - Non-Alcoholic"))


# --- backlog: evidencia de EDGAR, no una lista de industrias ---------------

@pytest.mark.parametrize("metric_id", ["MKT-BACK-015", "MKT-COVER-016"])
def test_an_issuer_that_files_no_rpo_does_not_owe_a_backlog(metric_id):
    """La NIIF 15 / ASC 606 obliga a divulgar las obligaciones de desempeño
    pendientes a quien tiene contratos de mas de un ano. Quien no etiqueta
    nunca ese concepto no se lo callo: no tiene ese backlog.

    Se decide con EVIDENCIA de EDGAR y no con una lista de industrias, porque
    ahi seria adivinar: dos empresas de la misma industria pueden diferir.
    """
    fuera = mkt.run(_packet(), {"_backlog_reportado": False})
    assert _estado(fuera, metric_id) is NullState.NOT_APPLICABLE


@pytest.mark.parametrize("metric_id", ["MKT-BACK-015", "MKT-COVER-016"])
def test_an_issuer_that_does_file_one_owes_it(metric_id):
    fuera = mkt.run(_packet(), {"_backlog_reportado": True})
    assert _estado(fuera, metric_id) is NullState.MISSING


def test_a_reported_backlog_history_settles_it_on_its_own():
    """Si ya hay historia de backlog en el overlay, la pregunta esta resuelta
    sin necesidad de la bandera."""
    fuera = mkt.run(_packet(), {"backlog_history": [100.0]})
    assert _estado(fuera, "MKT-BACK-015") is NullState.MISSING


# --- el estado tiene que sobrevivir hasta la dimensión ---------------------

def test_the_state_survives_into_the_dimension_slot():
    """Este era el fallo de verdad, y sin el nada de lo de arriba servia.

    El modulo convertia TODO lo no puntuado en NOT_SCORABLE al construir los
    slots, asi que el NOT_APPLICABLE se perdia por el camino y la metrica
    seguia pesando en el denominador. Marcar bien la metrica y aplanarla
    despues no cambia una sola cifra de cobertura.
    """
    fuera = mkt.run(_packet(industry="Beverages - Non-Alcoholic"),
                    {"_backlog_reportado": False})
    estados = [v.state for d in fuera.dimensions for _, v in d.metric_scores]
    assert NullState.NOT_APPLICABLE in estados, (
        "el NOT_APPLICABLE no llego a ningun slot: se esta aplanando otra vez")


def test_not_applicable_raises_coverage_and_missing_does_not():
    """La consecuencia medible, que es el punto entero del cambio."""
    sin_modelo = mkt.run(_packet(industry="Beverages - Non-Alcoholic"),
                         {"_backlog_reportado": False})
    con_modelo = mkt.run(_packet(industry="Software - Infrastructure",
                                 adapter="saas_subscriptions"),
                         {"_backlog_reportado": True})
    assert sin_modelo.coverage > con_modelo.coverage, (
        "la refresquera deberia cubrir MAS que el SaaS: a ella no se le exigen "
        "ARPU, adopcion ni backlog, y al SaaS si")
