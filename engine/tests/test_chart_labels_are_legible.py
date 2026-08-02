"""Ninguna etiqueta de un gráfico puede salirse del área de dibujo.

Los cuatro gráficos se generaron por primera vez en la auditoría del
2026-08-02 — hasta entonces nunca habían corrido, porque la ruta de reporte
completo depende del LLM. Al mirarlos aparecieron tres defectos:

  - el scorecard anclaba "11.4/20" justo en el máximo del eje, así que las
    categorías de 20 puntos perdían la etiqueta contra el marco;
  - `price_levels` cortaba la última cifra de las SMA ("193.1" por
    "193.11") — un número a medias es peor que ninguno;
  - el fan de escenarios escribía el rango encima de las propias líneas
    punteadas que lo definen, tapando lo que pretendía explicar.

Un gráfico no se puede "probar" a ojo en cada cambio, pero sí se puede
exigir que el texto quepa: se mide la caja de cada anotación contra la caja
de los ejes, con el mismo renderizador que produce el PNG.
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pytest

from wbj.report.charts import (
    football_field_chart, price_levels_chart, scenario_fan_chart, scorecard_chart,
)

#: Ojo con las dos formas: `scenario_fan_chart` recibe `{date, value}` y
#: `price_levels_chart` recibe las filas crudas del packet, con `close`.
#: Pasarle la primera a la segunda da un KeyError, no un gráfico feo.
_HISTORIA = [{"date": f"2026-0{1 + i // 28}-{1 + i % 28:02d}", "value": 190.0 + i % 17}
             for i in range(120)]
_FILAS = [{"date": h["date"], "close": h["value"]} for h in _HISTORIA]


@pytest.fixture
def figuras(monkeypatch):
    """Las figuras que dibujó el gráfico, capturadas antes de cerrarse.

    `_save` termina en `plt.close(fig)`, así que mirar `plt.gcf()` después
    de que la función retorna no examina el gráfico: examina una figura
    nueva y vacía, y toda comprobación pasa por vacuidad. La primera
    versión de este archivo tenía justo ese fallo.

    Se captura en `close` a propósito: para entonces ya corrió
    `tight_layout`, que es lo que fija la posición definitiva del texto.
    """
    from wbj.report import charts

    capturadas = []
    original = charts.plt.close

    def espia(fig=None):
        if hasattr(fig, "canvas"):
            capturadas.append(fig)
            return
        original(fig)

    monkeypatch.setattr(charts.plt, "close", espia)
    yield capturadas
    for f in capturadas:
        original(f)


def _etiquetas_fuera(figs) -> list[str]:
    """Anotaciones cuya caja se sale de los ejes, según el renderizador."""
    assert figs, "no se capturó ninguna figura: el gráfico no llegó a dibujarse"
    fuera = []
    for fig in figs:
        fig.canvas.draw()
        r = fig.canvas.get_renderer()
        for ax in fig.axes:
            caja = ax.get_window_extent(renderer=r)
            for hijo in ax.texts:
                texto = hijo.get_text().strip()
                if not texto:
                    continue
                t = hijo.get_window_extent(renderer=r)
                if t.x0 < caja.x0 - 1 or t.x1 > caja.x1 + 1 \
                        or t.y0 < caja.y0 - 1 or t.y1 > caja.y1 + 1:
                    fuera.append(texto.splitlines()[0][:60])
    return fuera


def test_the_scorecard_keeps_its_value_labels_inside(tmp_path, figuras):
    """`11.4/20`: la categoría con el máximo del eje es la que se salía."""
    cats = [
        {"key": "business", "label": "Business", "points": 11.4, "max_points": 20.0},
        {"key": "financial", "label": "Financial", "points": 10.1, "max_points": 15.0},
        {"key": "market", "label": "Market", "points": 5.1, "max_points": 20.0},
        {"key": "valuation", "label": "Valuation", "points": 5.0, "max_points": 10.0},
    ]
    scorecard_chart(cats, tmp_path / "s.png")
    assert not _etiquetas_fuera(figuras)


def test_the_price_chart_never_clips_an_sma_value(tmp_path, figuras):
    """Una SMA truncada da un nivel falso al que lea el gráfico."""
    price_levels_chart(
        _FILAS,
        [{"label": "support", "lower": 186.0, "upper": 191.7},
         {"label": "resistance", "lower": 206.1, "upper": 221.9}],
        {"SMA50": 206.17, "SMA200": 193.11},
        tmp_path / "p.png",
    )
    assert not _etiquetas_fuera(figuras)


def test_the_scenario_fan_labels_do_not_sit_on_the_bands(tmp_path, figuras):
    """La etiqueta va ENCIMA del borde superior, no en el centro de la
    banda: en el centro tapaba las dos punteadas del escenario."""
    scen = [
        {"name": "Bear→Base", "low": 199.0, "high": 281.0,
         "assumptions": "growth +40% (5y earnings) · multiple 41.0x"},
        {"name": "Base→Bull", "low": 281.0, "high": 323.0,
         "assumptions": "growth +40% (5y earnings) · multiple 47.1x"},
    ]
    scenario_fan_chart(_HISTORIA, scen, tmp_path / "f.png")
    assert not _etiquetas_fuera(figuras)

    ax = figuras[0].axes[0]
    for s in scen:
        centro = (s["low"] + s["high"]) / 2
        # `.xy` es el ancla en coordenadas de datos. `.get_position()` no
        # sirve aquí: con `textcoords="offset points"` devuelve el
        # desplazamiento en puntos ((-2, 6)), no la altura del precio.
        alturas = [t.xy[1] for t in ax.texts
                   if t.get_text().startswith(s["name"])]
        assert alturas, f"falta la etiqueta de {s['name']}"
        assert alturas[0] >= s["high"], (
            f"{s['name']} vuelve a estar dentro de la banda (y={alturas[0]}, "
            f"centro={centro})")


def test_the_football_field_keeps_its_range_labels_inside(tmp_path, figuras):
    football_field_chart(
        [{"label": "DCF bear–bull", "low": 84.0, "high": 144.0,
          "assumptions": "reverse-DCF reference band"},
         {"label": "Margin of safety 15–25%", "low": 83.0, "high": 95.0,
          "assumptions": "MOS 15% / 25%"}],
        200.75,
        tmp_path / "ff.png",
    )
    assert not _etiquetas_fuera(figuras)


def test_every_chart_still_declares_its_source_and_timestamp(tmp_path, figuras):
    """Regla de visualización: un gráfico que sale de la carpeta tiene que
    poder atribuirse. El sello va en la figura, fuera de los ejes."""
    scorecard_chart(
        [{"key": "risk", "label": "Risk", "points": 5.9, "max_points": 15.0}],
        tmp_path / "s.png",
        source="NVDA · Warren Buffett Jr", as_of="2026-08-02T13:10:41",
    )
    sellos = [t.get_text() for f in figuras for t in f.texts]
    assert any("NVDA" in s and "2026-08-02" in s for s in sellos), sellos
