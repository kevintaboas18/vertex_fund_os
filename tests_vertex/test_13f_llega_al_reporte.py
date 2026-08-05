"""Los tenedores 13F tienen que LLEGAR al reporte, no sólo existir.

`CLAUDE.md` punto 4 pide los fondos reconocidos con posición en la empresa.
El dato se obtenía bien y el reporte decía "0 tenedores" — porque había DOS
caminos y `institutional_13f` leía el vacío:

  - `insiders["institutional"]` se llenaba del `institutional_holders`
    estilo yfinance, que hoy devuelve None: FMP responde 402 en las SEIS
    rutas de `institutional-ownership` con este plan.
  - `insiders["edgar"]["holders_5pct"]` sí traía los de verdad — BlackRock,
    Vanguard, State Street con acciones y dólares — desde el conjunto
    trimestral 13F de la SEC.

Los datos estaban en memoria, en la misma estructura, una clave más abajo.

Es el mismo patrón que ya me había engañado leyendo
`mandatory_report.insiders` (que no existe) en vez de `insiders_over_1m`:
un dato presente que no se ve porque se busca en el sitio equivocado.
"""

from __future__ import annotations

import vertex_api


def _reporte(insiders):
    return vertex_api._wbj_mandatory_report(insiders, "DESFAVORABLE")


def test_the_edgar_holders_reach_the_report_when_fmp_is_empty():
    """El caso real de hoy: FMP da 402, EDGAR trae la lista."""
    insiders = {
        "institutional": [],                       # FMP 402 -> vacío
        "edgar": {"holders_5pct": [
            {"name": "BlackRock, Inc.", "shares": 1_928_629_174,
             "value": 336_352_928_002, "period": "01mar2026-31may2026",
             "source_locator": "13F-HR accession 0000..."},
            {"name": "VANGUARD CAPITAL MANAGEMENT LLC", "shares": 1_538_550_382,
             "value": 268_519_177_197, "period": "01mar2026-31may2026"},
        ]},
    }
    out = _reporte(insiders)
    filas = out["institutional_13f"]
    assert len(filas) == 2, f"los tenedores de EDGAR no llegaron: {filas}"
    assert filas[0]["holder"] == "BlackRock, Inc.", (
        "el campo se llama `holder` en la interfaz; EDGAR lo trae como "
        f"`name` y hay que normalizarlo: {filas[0]}")
    assert filas[0]["value"] == 336_352_928_002


def test_fmp_wins_when_it_actually_answers():
    """EDGAR es respaldo: si FMP responde, manda FMP."""
    insiders = {
        "institutional": [{"holder": "FMP Capital", "shares": 1, "value": 2}],
        "edgar": {"holders_5pct": [{"name": "EDGAR Capital", "shares": 9, "value": 9}]},
    }
    filas = _reporte(insiders)["institutional_13f"]
    assert len(filas) == 1 and filas[0]["holder"] == "FMP Capital", (
        f"el respaldo pisó a la fuente principal: {filas}")


def test_no_holders_anywhere_is_an_empty_list_not_a_crash():
    """Sin ninguna de las dos, la lista queda vacía y el reporte sigue."""
    assert _reporte({"institutional": [], "edgar": {}})["institutional_13f"] == []
    assert _reporte({})["institutional_13f"] == []


def test_the_report_never_shows_more_than_ten():
    """El punto 4 pide los reconocidos, no el censo entero."""
    muchos = [{"name": f"Fondo {i}", "shares": i, "value": i} for i in range(30)]
    filas = _reporte({"institutional": [], "edgar": {"holders_5pct": muchos}})
    assert len(filas["institutional_13f"]) <= 10
