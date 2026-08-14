"""Un TAM de hace tres anos no describe el mercado de hoy.

`tam_anio` se le pedia al modelo en el PROMPT desde siempre y NO se leia en
ninguna parte. Medido: el barrido acepto dos TAM citando 2023 --IDC para
hardware y para videojuegos-- entrando como si fueran del ultimo ano
publicado.

`DATA_POLICY.md` da 18 meses a un "annual market-size study" antes de exigir
corroboracion. Esta regla es mas estricta y se prefiere por auditable: 18
meses se cuentan desde una fecha de publicacion que la fuente no siempre
declara, mientras que el ANO del dato viene en la propia respuesta.
"""
from datetime import date

import pytest

from wbj.overlay.tam_mundial import ANOS_MAXIMOS_DE_ANTIGUEDAD, _anio_del_tam


def _limite() -> int:
    return date.today().year - ANOS_MAXIMOS_DE_ANTIGUEDAD


def test_el_campo_del_modelo_manda():
    assert _anio_del_tam({"tam_anio": 2026}) == 2026
    assert _anio_del_tam({"tam_anio": "2025"}) == 2025


def test_si_no_lo_da_se_lee_del_nombre_de_la_fuente():
    """El PROMPT pide "asociacion o casa + nombre del dato + ano", asi que el
    ano suele venir dentro de `tam_source` aunque el campo llegue vacio."""
    assert _anio_del_tam(
        {"tam_source": "IDC + Worldwide Server Market Revenue + 2023"}) == 2023
    assert _anio_del_tam(
        {"tam_source": "WSTS - ventas mundiales de semiconductores (2026)"}) == 2026


def test_de_un_rango_se_toma_el_ano_mayor():
    """Una fuente puede citar "2024-2025"; la cifra es del ultimo."""
    assert _anio_del_tam({"tam_source": "IBISWorld 2024-2025"}) == 2025


def test_sin_ano_no_hay_ano():
    """Y sin ano no se puede juzgar si sigue vigente: se rechaza."""
    assert _anio_del_tam({"tam_source": "IBISWorld, informe sectorial"}) is None
    assert _anio_del_tam({}) is None


def test_los_dos_TAM_de_2023_habrian_sido_rechazados():
    """El caso real que destapo el hueco."""
    for fuente in ("IDC + Worldwide Server Market Revenue + 2023",
                   "IDC + the global videogames market + 2023"):
        assert _anio_del_tam({"tam_source": fuente}) < _limite()


def test_los_cuatro_que_si_valian_siguen_valiendo():
    """La regla no puede tirar lo que estaba bien."""
    for fuente in ("IBISWorld Global Commercial Banks industry revenue 2026",
                   "IBISWorld, Global Insurance Brokers & Agencies Market Size 2025",
                   "Ibisworld - Global Oil & Gas Exploration & Production, 2025",
                   "WSTS / Semiconductor Industry Association (2026)"):
        assert _anio_del_tam({"tam_source": fuente}) >= _limite(), fuente


def test_un_ano_del_futuro_no_se_cuela():
    """Una proyeccion a 2030 no es el mercado de hoy."""
    assert _anio_del_tam({"tam_source": "Grand View Research forecast 2030"}) is None


def test_la_validacion_usa_la_constante_y_no_un_numero_suelto():
    import inspect

    from wbj.overlay import tam_mundial

    src = inspect.getsource(tam_mundial)
    assert "date.today().year - ANOS_MAXIMOS_DE_ANTIGUEDAD" in src
