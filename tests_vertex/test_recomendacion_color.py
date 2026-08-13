"""El badge de recomendacion salia GRIS para todas.

Fichero aparte de `test_navegador.py`, que se salta entero cuando no hay
navegador -- estas pruebas solo leen el HTML y tienen que correr siempre.
"""

# ============================================================================
# El badge de recomendacion salia GRIS para todas
# ============================================================================


def _html():
    from pathlib import Path
    return (Path(__file__).parent.parent / "vertex_fund_os_platform.html").read_text(
        encoding="utf-8")


def test_el_color_de_la_recomendacion_conoce_el_vocabulario_real():
    """Habia DOS mapas de color, los dos con las cuatro claves en ingles
    (BUY/HOLD/SELL/AVOID), y el motor dejo de emitir esas palabras: hoy
    devuelve FAVORABLE / CONDICIONAL / ESPECULATIVO / DESFAVORABLE
    (`_WBJ_PROFILE_TO_RECO` en vertex_api.py). Ninguna casaba, asi que todo
    caia al gris -- incluido DESFAVORABLE, que es justo el que tiene que
    gritar en rojo.
    """
    h = _html()
    assert "function recClass(" in h, "tiene que haber UN helper, no dos mapas"
    for palabra in ("FAVORABLE", "CONDICIONAL", "ESPECULATIVO", "DESFAVORABLE"):
        assert palabra + ":" in h, f"{palabra} sin color asignado"
    # Y los nombres viejos siguen mapeados: los reportes en disco los conservan.
    for legado in ("BUY:", "HOLD:", "AVOID:", "SELL:"):
        assert legado in h, f"{legado} se perdio y romperia el historico"


def test_ya_no_quedan_dos_mapas_de_color_que_puedan_contradecirse():
    h = _html()
    assert "const recColors = {BUY:" not in h
    assert "const rcMap = {BUY:" not in h


def test_la_lista_de_puntajes_no_sale_dos_veces():
    """`vScoreCard` y el `wbjScorecard` de dentro del panel WBJ mostraban los
    MISMOS seis numeros: los dos leen `victor_scorecard`."""
    h = _html()
    assert h.count('<div id="wbjScorecard" class="space-y-2.5"></div>') == 0
    assert "renderVictorScore" in h, "el que se queda tiene que seguir vivo"


# ============================================================================
# Los thesis killers son del juez, no del motor determinista
# ============================================================================


def test_los_thesis_killers_viven_en_la_pestana_de_juicio():
    """Son contenido cualitativo (clase Q) y no mueven un punto del score
    determinista -- su propio texto lo decia mientras se mostraba en el area
    del motor, al lado de las metricas que si puntuan."""
    h = _html()
    i = h.index('id="wbjAiPanel"')
    j = h.index('id="frThesisKillers"')
    k = h.index('id="sentimentTab"')
    assert i < j < k, "el bloque tiene que quedar dentro de la pestaña Juicio AI"


def test_y_NO_dentro_del_panel_que_se_oculta_sin_juez():
    """`wbjAiPanel` se oculta cuando el juez no corrio, y es justo entonces
    cuando este bloque tiene algo que decir: explica que quedan
    NOT_SCORABLE en vez de dejar un hueco sin motivo."""
    h = _html()
    panel = h.index('id="wbjAiPanel"')
    vacio = h.index('id="wbjAiEmpty"')
    killers = h.index('id="frThesisKillers"')
    assert killers > vacio > panel, (
        "hermano de los dos paneles, no hijo del que se oculta")


def test_sigue_habiendo_quien_lo_pinte():
    h = _html()
    assert "renderVictorThesisKillers()" in h


# ============================================================================
# "Puntaje de los agentes" decia Quick score y mostraba el deep
# ============================================================================


def test_el_panel_se_alimenta_del_quick_no_del_raw_total():
    """La narrativa de al lado la escribe `targets.py:216` de Victor:

        f"Quick score: {scorecard['overall_10']}/10, computed from
         {covered} of 100 evidence points"

    y se le pasaba un scorecard construido desde `sc["raw_total"]`, o sea el
    agregado PROFUNDO. El texto decia "Quick score" mostrando el numero del
    deep. Medido en APH: el panel ponia 4,5 y el quick real es 7,9 -- el
    mismo que sale en Descubrir empresas.

    Ademas el deep ya se ve dos veces: el gauge de Raw Score y, con juez, la
    pestaña de Juicio AI.
    """
    from pathlib import Path

    api = (Path(__file__).parent.parent / "vertex_api.py").read_text(encoding="utf-8")
    i = api.index('sc["victor_scorecard"] = _victor_sc')
    bloque = api[max(0, i - 2600):i]
    assert "quick_scorecard as _qsc" in bloque, (
        "el panel tiene que alimentarse del quick de Victor")
    assert "_es_deep" in bloque, (
        "si no hay packet EDGAR se cae al deep, y hay que DECIRLO")


def test_el_rotulo_dice_cual_de_los_dos_es():
    h = _html()
    assert "Puntaje quick" in h
    assert "_es_deep" in h, "la interfaz tiene que distinguir el respaldo"
