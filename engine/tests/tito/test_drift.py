"""Drift Sentiment: los muros y el imán MEDIDOS, no modelados.

Lo que se fija aquí, por orden de importancia:

1. El filtro de mensuales (3.er viernes) y su límite conocido con los festivos.
2. **La tolerancia de DTE**, que es el guardián de verdad: la cadena se corta
   en 40 páginas y los vencimientos lejanos son los primeros en caer, así que
   sin tope el plazo de 320 días resolvería al de 120 y lo etiquetaría como
   320. No falla — miente. Cada test de este bloque va rojo si se quita.
3. Que muros e imán son un RECUENTO, no un modelo: el strike con más interés
   abierto y el de mayor nocional neto en valor absoluto.
4. Que el sesgo del nocional hacia los strikes altos existe y es medible.
"""

from __future__ import annotations

import math
from datetime import date

import pytest

from wbj.tito.drift import (
    DTE_OBJETIVO,
    clasifica_deriva,
    drift_analysis,
    es_mensual,
    magneto,
    muro_calls,
    muro_puts,
    nocional_por_strike,
    sigma_proyectada,
    tolerancia_dte,
    vencimiento_mas_cercano,
    vencimientos_mensuales,
)
from wbj.tito.structure import ChainRow

HOY = date(2026, 8, 20)


def fila(strike: float, tipo: str, oi: int, exp: str) -> ChainRow:
    return ChainRow(
        contract_type=tipo,  # type: ignore[arg-type]
        expiration=exp,
        strike=strike,
        open_interest=oi,
        volume=0,
        notional_value=strike * oi * 100,
    )


class TestElFiltroDeMensuales:
    def test_el_tercer_viernes_pasa(self):
        # 2026-09-18 es viernes y cae en el rango 15-21.
        assert es_mensual(date(2026, 9, 18))

    def test_un_semanal_no_pasa(self):
        # 2026-09-25 también es viernes, pero es el CUARTO.
        assert not es_mensual(date(2026, 9, 25))
        assert not es_mensual(date(2026, 9, 11))

    def test_un_jueves_no_pasa_aunque_caiga_en_el_rango(self):
        assert not es_mensual(date(2026, 9, 17))

    def test_la_limitacion_del_festivo_esta_documentada_y_se_cumple(self):
        # Viernes Santo adelanta el vencimiento al jueves y esta regla lo
        # descarta. Es la limitación que hereda de su agente, escrita a
        # propósito: se prefiere perder ese mes a inventar una excepción.
        assert not es_mensual(date(2026, 4, 2))  # jueves 2 de abril

    def test_solo_los_mensuales_salen_de_la_cadena(self):
        filas = [fila(100, "call", 10, "2026-09-18"),
                 fila(100, "call", 10, "2026-09-25")]
        from wbj.tito.drift import _a_filas
        assert vencimientos_mensuales(_a_filas(filas, HOY)) == [date(2026, 9, 18)]


class TestLaToleranciaDeDTE:
    """El guardián contra la cadena cortada."""

    def test_un_ciclo_y_medio_o_el_25_por_ciento(self):
        assert tolerancia_dte(30) == 45     # el 25% son 7; manda el suelo
        assert tolerancia_dte(90) == 45     # el 25% son 22; manda el suelo
        assert tolerancia_dte(120) == 45    # el 25% son 30; manda el suelo
        assert tolerancia_dte(320) == 80    # el 25% manda

    def test_el_plazo_de_320_NO_se_pinta_con_un_vencimiento_de_120(self):
        # Exactamente el fallo silencioso: la cadena se corta y el mensual más
        # lejano que llegó está a ~120 días. Sin tolerancia, saldría rotulado
        # "Largo ~320 DTE".
        vencs = ["2026-09-18", "2026-10-16", "2026-11-20", "2026-12-18"]
        filas = [f for v in vencs
                 for f in (fila(100, "call", 50, v), fila(90, "put", 50, v))]
        a = drift_analysis(filas, spot=100.0, hoy=HOY, iv=0.4)
        etiquetas = [b.etiqueta for b in a.buckets]
        assert "Largo ~320 DTE" not in etiquetas
        motivos = {x["etiqueta"]: x["motivo"] for x in a.sin_datos}
        assert "Largo ~320 DTE" in motivos
        assert "no llega tan lejos" in motivos["Largo ~320 DTE"]

    def test_y_el_motivo_dice_a_cuantos_dias_estaba_el_mas_cercano(self):
        vencs = ["2026-09-18", "2026-10-16"]
        filas = [f for v in vencs
                 for f in (fila(100, "call", 50, v), fila(90, "put", 50, v))]
        a = drift_analysis(filas, spot=100.0, hoy=HOY, iv=0.4)
        motivo = next(x["motivo"] for x in a.sin_datos
                      if x["etiqueta"] == "Largo ~320 DTE")
        assert "57 días" in motivo and "320" in motivo


class TestElVencimientoMasCercano:
    def test_elige_el_de_DTE_mas_proximo_al_objetivo(self):
        vencs = [date(2026, 9, 18), date(2026, 11, 20), date(2027, 7, 16)]
        assert vencimiento_mas_cercano(vencs, 30, HOY) == date(2026, 9, 18)
        assert vencimiento_mas_cercano(vencs, 90, HOY) == date(2026, 11, 20)
        assert vencimiento_mas_cercano(vencs, 320, HOY) == date(2027, 7, 16)

    def test_los_vencidos_no_cuentan(self):
        vencs = [date(2026, 1, 16), date(2026, 9, 18)]
        assert vencimiento_mas_cercano(vencs, 30, HOY) == date(2026, 9, 18)

    def test_sin_futuros_devuelve_None(self):
        assert vencimiento_mas_cercano([date(2026, 1, 16)], 30, HOY) is None


class TestMurosEIman:
    def test_el_muro_es_el_STRIKE_con_mas_interes_abierto(self):
        from wbj.tito.drift import _a_filas
        f = _a_filas([fila(100, "call", 500, "2026-09-18"),
                      fila(110, "call", 9000, "2026-09-18"),
                      fila(120, "call", 300, "2026-09-18"),
                      fila(90, "put", 4000, "2026-09-18")], HOY)
        assert muro_calls(f) == (110.0, 9000)
        assert muro_puts(f) == (90.0, 4000)

    def test_sin_un_lado_devuelve_None(self):
        from wbj.tito.drift import _a_filas
        f = _a_filas([fila(100, "call", 500, "2026-09-18")], HOY)
        assert muro_puts(f) is None

    def test_el_nocional_lleva_signo_calls_positivo_puts_negativo(self):
        from wbj.tito.drift import _a_filas
        f = _a_filas([fila(100, "call", 10, "2026-09-18"),
                      fila(100, "put", 4, "2026-09-18")], HOY)
        # (10 - 4) contratos × 100 acciones × 100 de strike
        assert nocional_por_strike(f) == {100.0: 6 * 100 * 100}

    def test_el_iman_es_el_mayor_en_VALOR_ABSOLUTO_no_el_mayor(self):
        from wbj.tito.drift import _a_filas
        f = _a_filas([fila(100, "call", 10, "2026-09-18"),
                      fila(80, "put", 100, "2026-09-18")], HOY)
        s, n = magneto(f)
        assert s == 80.0 and n < 0        # los puts mandan, y se dice con el signo

    def test_el_sesgo_del_nocional_es_de_DESEMPATE_y_solo_eso(self):
        """A igual número de contratos gana el strike más alto, y ya.

        El nocional multiplica por el strike, así que entre dos
        concentraciones PARECIDAS el desempate lo gana la de arriba. Ese es el
        tamaño exacto del sesgo — ni más.
        """
        from wbj.tito.drift import _a_filas
        f = _a_filas([fila(100, "call", 1000, "2026-09-18"),
                      fila(400, "call", 1000, "2026-09-18")], HOY)
        assert magneto(f)[0] == 400.0

    def test_el_iman_NO_tira_hacia_arriba_sale_donde_esta_el_nocional(self):
        """La corrección de Kevin, fijada como test.

        Decir que el imán «tiende a salir por encima del precio» es falso: sale
        donde esté el nocional. Con una pared de puts POR DEBAJO del precio se
        va abajo, y el desempate por strike no la salva.

        La aritmética, que es el punto: 6.000 puts en el 80 son 80 × 6.000 ×
        100 = 48 M, y 1.000 calls en el 400 son 40 M. Gana el 80 aunque el
        strike sea cinco veces menor, porque los contratos NO empatan — y el
        sesgo del nocional solo desempata cuando empatan.
        """
        from wbj.tito.drift import _a_filas
        spot = 100.0
        f = _a_filas([fila(400, "call", 1000, "2026-09-18"),
                      fila(80, "put", 6000, "2026-09-18")], HOY)
        strike, neto = magneto(f)
        assert strike == 80.0 < spot, "el imán se fue arriba con los puts mandando"
        assert neto < 0, "el signo tiene que decir que mandan los puts"

        # Y la otra cara: con las calls mandando, arriba. El imán sigue al
        # nocional en las dos direcciones.
        f = _a_filas([fila(120, "call", 3000, "2026-09-18"),
                      fila(80, "put", 100, "2026-09-18")], HOY)
        strike, neto = magneto(f)
        assert strike == 120.0 > spot and neto > 0

    def test_el_analisis_completo_tambien_pone_el_iman_ABAJO(self):
        """No es solo la función suelta: el bucket que llega al panel también."""
        vencs = ["2026-09-18", "2026-11-20", "2026-12-18", "2027-07-16"]
        filas = []
        for v in vencs:
            filas += [fila(110, "call", 200, v), fila(70, "put", 9000, v)]
        a = drift_analysis(filas, spot=100.0, hoy=HOY, iv=0.4)
        assert a.buckets
        for b in a.buckets:
            assert b.magneto == 70.0 < 100.0
            assert b.magneto_nocional < 0
            # …y la lectura lo cuenta como rechazo, no como atracción.
            assert "rechazo" in b.deriva or "RUPTURA" in b.deriva


class TestElCono:
    def test_sigma_es_spot_por_iv_por_raiz_de_dte_entre_365(self):
        s = sigma_proyectada(100.0, 0.40, 90)
        assert s == pytest.approx(100 * 0.40 * math.sqrt(90 / 365))

    def test_sin_iv_no_hay_cono(self):
        assert sigma_proyectada(100.0, None, 90) is None
        assert sigma_proyectada(100.0, 0.0, 90) is None
        assert sigma_proyectada(100.0, 0.4, 0) is None


class TestLaLectura:
    def test_dentro_del_rango_manda_la_polaridad_del_iman(self):
        txt, ruptura = clasifica_deriva(100, mc=110, mp=90, mag_strike=110,
                                        mag_nocional=1e6)
        assert not ruptura and "atracción" in txt
        txt, _ = clasifica_deriva(100, mc=110, mp=90, mag_strike=90,
                                  mag_nocional=-1e6)
        assert "rechazo" in txt

    def test_fuera_del_rango_es_ruptura_hacia_el_muro_del_viaje(self):
        txt, ruptura = clasifica_deriva(120, mc=110, mp=90, mag_strike=110,
                                        mag_nocional=1e6)
        assert ruptura and "al alza" in txt
        txt, ruptura = clasifica_deriva(80, mc=110, mp=90, mag_strike=90,
                                        mag_nocional=-1e6)
        assert ruptura and "a la baja" in txt


def _cadena_completa():
    """Cuatro mensuales que cubren los cuatro plazos con holgura."""
    vencs = ["2026-09-18", "2026-11-20", "2026-12-18", "2027-07-16"]
    filas = []
    for v in vencs:
        filas += [fila(110, "call", 9000, v), fila(120, "call", 1000, v),
                  fila(90, "put", 4000, v), fila(80, "put", 500, v)]
    return filas


class TestElAnalisisCompleto:
    def test_resuelve_los_cuatro_plazos(self):
        a = drift_analysis(_cadena_completa(), spot=100.0, hoy=HOY, iv=0.4)
        assert [b.dte_objetivo for b in a.buckets] == [d for _, d in DTE_OBJETIVO]
        assert a.sin_datos == []

    def test_solo_UNO_solapa_con_los_horizontes_del_motor(self):
        # El motor corta en 30 días (`run_scorecard(horizons=(10, 20, 30))`),
        # así que solo el bucket de ~30 se pinta al lado del suyo. Es la regla
        # que decide el formato `agente/Drift` del panel.
        a = drift_analysis(_cadena_completa(), spot=100.0, hoy=HOY, iv=0.4)
        assert len([b for b in a.buckets if b.dte_objetivo <= 30]) == 1

    def test_marca_el_duplicado_cuando_dos_plazos_caen_en_el_mismo_vencimiento(self):
        vencs = ["2026-09-18", "2026-11-20", "2027-07-16"]
        filas = [f for v in vencs
                 for f in (fila(110, "call", 90, v), fila(90, "put", 40, v))]
        a = drift_analysis(filas, spot=100.0, hoy=HOY, iv=0.4)
        # 120 y 90 días resuelven los dos al mensual de noviembre.
        dups = [b for b in a.buckets if b.duplicado]
        assert len(dups) == 1 and dups[0].dte_objetivo == 90

    def test_sin_mensuales_lo_DICE_en_vez_de_usar_semanales(self):
        filas = [fila(110, "call", 90, "2026-09-25"),
                 fila(90, "put", 40, "2026-09-25")]
        a = drift_analysis(filas, spot=100.0, hoy=HOY, iv=0.4)
        assert a.buckets == [] and "mensuales" in a.motivo

    def test_sin_cadena_no_inventa_nada(self):
        a = drift_analysis([], spot=100.0, hoy=HOY, iv=0.4)
        assert a.buckets == [] and a.motivo

    def test_sin_spot_no_inventa_nada(self):
        a = drift_analysis(_cadena_completa(), spot=0.0, hoy=HOY, iv=0.4)
        assert a.buckets == [] and "precio" in a.motivo

    def test_sin_iv_el_resto_del_analisis_SIGUE_en_pie(self):
        a = drift_analysis(_cadena_completa(), spot=100.0, hoy=HOY, iv=None)
        assert a.buckets and all(b.sigma is None for b in a.buckets)
        assert all(b.muro_calls is not None for b in a.buckets)

    def test_la_basura_de_la_cadena_se_descarta_sin_reventar(self):
        basura = [
            fila(0, "call", 10, "2026-09-18"),          # strike 0
            fila(100, "call", 0, "2026-09-18"),         # sin interés abierto
            fila(100, "otra", 10, "2026-09-18"),        # tipo desconocido
            fila(100, "call", 10, "no-es-fecha"),       # fecha ilegible
        ] + _cadena_completa()
        a = drift_analysis(basura, spot=100.0, hoy=HOY, iv=0.4)
        assert len(a.buckets) == 4

    def test_no_baja_nada_ni_mira_el_reloj(self):
        # `hoy` entra por parámetro: dos llamadas con la misma fecha dan lo
        # mismo, hoy y dentro de un año.
        a = drift_analysis(_cadena_completa(), spot=100.0, hoy=HOY, iv=0.4)
        b = drift_analysis(_cadena_completa(), spot=100.0, hoy=HOY, iv=0.4)
        assert [x.vencimiento for x in a.buckets] == [x.vencimiento for x in b.buckets]


class TestDriftYElGexMidenCosasDISTINTAS:
    def test_sobre_la_misma_cadena_apuntan_a_strikes_distintos(self):
        """El motivo por el que se pintan los dos y no uno.

        El GEX es gamma de Black-Scholes y se desploma lejos del dinero; el
        muro de Drift es un recuento de contratos. Con spot 100 y el mayor OI
        en el strike 140, el GEX no lo elige y Drift sí.
        """
        import math as _m
        from datetime import datetime, timezone

        from wbj.tito.gex import gex_analysis

        rows = [fila(105, "call", 800, "2026-09-18"),
                fila(140, "call", 9000, "2026-09-18"),
                fila(95, "put", 700, "2026-09-18")]
        closes = [100 + _m.sin(i) * 2 for i in range(22)]
        g = gex_analysis(rows, closes=closes, spot=100.0,
                         now=datetime(2026, 8, 20, 15, 0, tzinfo=timezone.utc))
        gex_calls = [n for n in g.nodes if n.side == "call"]
        mayor_gex = max(gex_calls, key=lambda n: abs(n.net_gex)).strike if gex_calls else None

        a = drift_analysis(rows, spot=100.0, hoy=HOY, iv=0.4)
        muro_drift = a.buckets[-1].muro_calls

        assert muro_drift == 140.0
        assert mayor_gex != muro_drift
