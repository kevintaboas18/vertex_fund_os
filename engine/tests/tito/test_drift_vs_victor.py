"""Drift, medido contra SU repositorio de verdad (`drift-sentiment-agent`).

Existe porque el port se rompió una vez y nadie lo notó: se «arreglaron» los
muros para que miraran el lado del precio —resistencia arriba, soporte
abajo—, que suena razonable y **no es lo que él hace**. Sus muros son el
strike con más interés abierto de cada lado, sin mirar el spot, y que la
resistencia salga por debajo del soporte es exactamente su condición de
ruptura, no un fallo.

Un test que compara contra la idea que uno tiene del código no sirve para
esto. Este compara contra SU ARCHIVO.

Aquí se fijan las PROPIEDADES que definen su algoritmo. La comparación
número a número contra su Python vive en `engine/scripts/diff_drift.sh`,
como los otros dieciséis diferenciales — no como test, para no meter un skip
permanente en una batería que presume de no tener ninguno.
"""

from __future__ import annotations

from datetime import date

import pytest

from wbj.tito.drift import (
    DTE_OBJETIVO,
    _a_filas,
    drift_analysis,
    es_mensual,
    magneto,
    muro_calls,
    muro_puts,
    nocional_por_strike,
    vencimiento_mas_cercano,
)
from wbj.tito.structure import ChainRow

HOY = date(2026, 8, 20)
SPOT = 180.0

#: La cadena que destapó la divergencia: el mayor OI de calls está DENTRO del
#: dinero y el de puts fuera, que es lo normal en una acción que se ha movido.
def _cadena() -> list[ChainRow]:
    filas = []
    for k in range(100, 261, 10):
        oi_c = oi_p = 500
        if k == 120:
            oi_c = 14000
        if k == 200:
            oi_c = 6000
        if k == 230:
            oi_p = 11000
        if k == 160:
            oi_p = 7000
        filas += [
            ChainRow("call", "2026-09-18", float(k), oi_c, 0, k * oi_c * 100),
            ChainRow("put", "2026-09-18", float(k), oi_p, 0, k * oi_p * 100),
        ]
    return filas


class TestSuAlgoritmoTalCUAL:
    """Las cuatro decisiones suyas que un «arreglo» razonable se carga."""

    def test_los_muros_NO_miran_el_spot(self):
        """`walls.py`: `max(contracts, key=open_interest)` filtrando SOLO por
        lado. Ni una palabra sobre el precio.

        Filtrarlos por lado del spot parece más correcto —una resistencia
        debería estar arriba— y cambia el número que él publica. Si algún día
        se decide divergir, que sea a propósito y con esta línea en rojo.
        """
        f = _a_filas(_cadena(), HOY)
        assert muro_calls(f) == (120.0, 14000), (
            "el muro de calls dejó de ser el mayor OI de calls a secas")
        assert muro_puts(f) == (230.0, 11000)

    def test_la_resistencia_PUEDE_salir_por_debajo_del_soporte(self):
        """Y no es un fallo: es su `breakout`.

        `classify_drift` ordena los dos strikes (`sorted`) y compara el spot
        con ese rango. Si el precio queda fuera, hay ruptura. Ese `sorted` es
        SUYO y es justo lo que permite que los muros vengan en cualquier
        orden.
        """
        f = _a_filas(_cadena(), HOY)
        mc, mp = muro_calls(f)[0], muro_puts(f)[0]
        assert mc < mp, "la cadena de prueba ya no reproduce el caso"
        a = drift_analysis(_cadena(), spot=SPOT, hoy=HOY, iv=0.35)
        assert a.buckets
        # spot 180 está entre 120 y 230 → NO es ruptura, es rango.
        assert not a.buckets[0].breakout

    def test_el_iman_es_el_NETO_no_el_bruto(self):
        """`magneto.py`: `acc[c.strike] += c.notional`, con las puts en
        negativo. Un strike con calls y puts se ANULA, y eso es su
        definición: mide el desequilibrio, no el volumen de dinero.
        """
        f = _a_filas([ChainRow("call", "2026-09-18", 100.0, 10000, 0, 0),
                      ChainRow("put", "2026-09-18", 100.0, 10000, 0, 0),
                      ChainRow("call", "2026-09-18", 110.0, 900, 0, 0)], HOY)
        assert nocional_por_strike(f)[100.0] == 0, "dejó de anularse: ya no es neto"
        assert magneto(f)[0] == 110.0, "el imán dejó de salir del nocional NETO"

    def test_el_iman_NO_se_recorta_a_la_banda_de_los_muros(self):
        """Él lo busca en toda la cadena del vencimiento. Un imán muy dentro
        del dinero es un resultado legítimo de su modelo."""
        a = drift_analysis(_cadena(), spot=SPOT, hoy=HOY, iv=0.35)
        b = a.buckets[0]
        assert b.magneto == 230.0, "el imán se recortó a una banda que él no usa"

    def test_sus_cuatro_plazos_y_solo_esos(self):
        """`chain_filter.DTE_TARGETS` — 320/120/90/30. Sin 60."""
        assert [d for _, d in DTE_OBJETIVO] == [320, 120, 90, 30]
        assert [s for s, _ in DTE_OBJETIVO] == ["Largo", "Largo", "Corto", "Corto"]

    def test_el_mensual_es_el_TERCER_viernes(self):
        """`is_monthly_expiration`: viernes y día 15-21."""
        assert es_mensual(date(2026, 9, 18))
        assert not es_mensual(date(2026, 9, 25))
        assert not es_mensual(date(2026, 9, 17))

    def test_el_vencimiento_es_el_de_DTE_mas_cercano(self):
        """`nearest_expiration`: `min(|dte - target|)` entre los futuros."""
        vencs = [date(2026, 9, 18), date(2026, 11, 20), date(2027, 7, 16)]
        assert vencimiento_mas_cercano(vencs, 320, HOY) == date(2027, 7, 16)
        assert vencimiento_mas_cercano(vencs, 30, HOY) == date(2026, 9, 18)


class TestLoQueVertexAÑADEEncimaDeLoSuyo:
    """Dos cosas que él no tiene, y que aquí están DECLARADAS como añadidos.

    No son divergencias silenciosas: cambian lo que se pinta, así que tienen
    que estar escritas donde alguien las vea.
    """

    def test_la_tolerancia_de_DTE_es_de_Vertex(self):
        """Él acepta el mensual más cercano sea cual sea su DTE. Aquí se
        rechaza si está demasiado lejos del objetivo.

        El motivo es de Vertex, no suyo: la cadena de Massive se corta en 40
        páginas y los vencimientos lejanos son los primeros en caer, así que
        el plazo de 320 días resolvería al de 120 y lo pintaría con la
        etiqueta de 320. Él lee la cadena entera y no tiene ese problema.
        """
        from wbj.tito.drift import tolerancia_dte

        assert tolerancia_dte(320) == 80
        vencs = ["2026-09-18", "2026-10-16"]
        filas = [ChainRow(t, v, 100.0, 500, 0, 0)
                 for v in vencs for t in ("call", "put")]
        a = drift_analysis(filas, spot=100.0, hoy=HOY, iv=0.4)
        assert any(x["dte_objetivo"] == 320 for x in a.sin_datos)

    def test_la_marca_de_DUPLICADO_es_de_Vertex(self):
        """Su `select_buckets` dice literalmente «the same expiration may
        serve more than one target if the chain is sparse», y no lo señala.
        Aquí sí: dos filas idénticas en pantalla parecen un error de cálculo.
        """
        vencs = ["2026-09-18", "2026-11-20", "2027-07-16"]
        filas = [ChainRow(t, v, k, 500, 0, 0)
                 for v in vencs for t in ("call", "put") for k in (90.0, 110.0)]
        a = drift_analysis(filas, spot=100.0, hoy=HOY, iv=0.4)
        assert any(b.duplicado for b in a.buckets)
