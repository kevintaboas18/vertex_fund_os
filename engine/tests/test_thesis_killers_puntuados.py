"""Los thesis killers vuelven con número, no sólo con prosa.

`RSK-THESIS-035` es `Probability * Impact * (1-Detectability) * TimeUrgency`, y
`FORMULAS.md` tipa sus insumos como "explicit 0-1 assumptions". El juez
respondía los riesgos en palabras —cuál es, su métrica de alerta temprana, su
mitigante— y la métrica se quedaba `NOT_SCORABLE` en todos los tickers.

Esa respuesta no puede volver por `judgment_slots`: ese camino sustituye un
slot por un 0-10 ya hecho, y aquí lo que vuelve es una **cuarteta** que
todavía tiene que pasar por la fórmula. `merge_overlay` dice sin rodeos que
nunca rehace aritmética de puntos.

Así que vuelve por donde salió, a `overlay["thesis_killers"]`, y Riesgo se
corre otra vez — el mismo patrón, y las mismas razones, que la segunda pasada
de Market con los catalizadores puntuados.

El motor sigue sin inventar nada: quien declara las cifras es el agente de
juicio, igual que declara la clasificación de moat. Lo que la regla prohíbe es
que el MOTOR se las invente.
"""

from __future__ import annotations

import json
from pathlib import Path

from wbj.deep import _RISK_LABEL, _rerun_risk_with_judged_thesis_killers
from wbj.schemas.packet import Packet

_FIXTURE = Path(__file__).parent / "fixtures" / "packet" / "NVDA_packet.json"


class _Juicio:
    def __init__(self, answer):
        self.request_id = "risk_analysis:thesis_killers"
        self.answer = answer


def _packet() -> Packet:
    return Packet.model_validate(json.loads(_FIXTURE.read_text()))


_CUARTETA = {"risk": "Concentración de clientes", "probability": 0.3,
             "impact_0_1": 0.6, "detectability": 0.4, "time_urgency": 0.5}


def test_a_priced_killer_reaches_the_metric():
    pk = _packet()
    from wbj.specialists import risk as rsk

    antes = rsk.run(pk, {})
    labelled = [(_RISK_LABEL, antes)]
    fuera = _rerun_risk_with_judged_thesis_killers(
        labelled, [_Juicio({"items": [_CUARTETA]})], pk, {})
    m = [r for _, o in fuera for r in o.metrics if r.metric_id == "RSK-THESIS-035"]
    assert m and m[0].value is not None, (
        "la cuarteta del juez no llego a RSK-THESIS-035")
    # 0.3 * 0.6 * (1 - 0.4) * 0.5
    assert abs(m[0].value - 0.054) < 1e-6


def test_a_silent_judge_changes_nothing():
    """Si el juez no contesta, o contesta sin numeros, la salida es la misma:
    la metrica se queda NOT_SCORABLE, que es la respuesta honesta."""
    pk = _packet()
    from wbj.specialists import risk as rsk

    labelled = [(_RISK_LABEL, rsk.run(pk, {}))]
    for respuesta in ([], [_Juicio({"items": []})],
                      [_Juicio({"items": [{"risk": "sin numeros"}]})]):
        fuera = _rerun_risk_with_judged_thesis_killers(labelled, respuesta, pk, {})
        assert fuera is labelled, "se re-corrio sin cuartetas completas"


def test_a_factor_outside_zero_to_one_is_dropped():
    """`FORMULAS.md` los tipa como "explicit 0-1 assumptions". Un 1,5 no es una
    probabilidad, y multiplicarlo daria una prioridad por encima de 1."""
    pk = _packet()
    from wbj.specialists import risk as rsk

    labelled = [(_RISK_LABEL, rsk.run(pk, {}))]
    malo = {**_CUARTETA, "probability": 1.5}
    assert _rerun_risk_with_judged_thesis_killers(
        labelled, [_Juicio({"items": [malo]})], pk, {}) is labelled


def test_the_judge_is_asked_for_the_four_factors():
    """Si la pregunta vuelve a pedir solo prosa, la cuarteta no llegara nunca
    y este arreglo quedaria muerto sin que nada fallara."""
    import inspect

    from wbj.specialists import risk as rsk

    fuente = inspect.getsource(rsk)
    for campo in ("impact_0_1", "detectability", "time_urgency"):
        assert campo in fuente, f"el juez ya no recibe {campo} en la pregunta"
