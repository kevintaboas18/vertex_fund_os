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
