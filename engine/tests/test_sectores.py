"""El mapa de sectores: el RSI y las tablas.

El RSI es el número que decide si una casilla se pinta roja, gris o verde, así
que se mide contra la serie CANÓNICA de Wilder —la de su libro, la que usan
todas las plataformas para calibrar— y no contra lo que devuelva esta
implementación el día que se escribió.
"""

from __future__ import annotations

import pytest

from wbj.sectores import (INDUSTRIAS, REFERENCIAS, RSI_PERIODO, SECTORES,
                          cambio_pct, industrias_de, nombre_de, rsi, universo)

#: La serie de Wilder («New Concepts in Technical Trading Systems», tabla del
#: capítulo del RSI). Es el patrón con el que se comprueba cualquier RSI: si
#: una implementación da 70,53 aquí, es la de Wilder; si da otra cosa, está
#: usando una media distinta y no es el número que ve el resto del mundo.
#: Va con sus CUATRO decimales a propósito: redondearla a dos mueve el RSI unas
#: siete centésimas y el test deja de medir la fórmula para medir el redondeo.
WILDER = [
    44.3389, 44.0902, 44.1497, 43.6124, 44.3278, 44.8264, 45.0955, 45.4245,
    45.8433, 46.0826, 45.8931, 46.0328, 45.6140, 46.2820, 46.2820, 46.0028,
    46.0328, 46.4116, 46.2222, 45.6439, 46.2122, 46.2521, 45.7137, 46.4515,
    45.7835, 45.3548, 44.0288, 44.1783, 44.2181, 44.5672, 43.4205, 42.6628,
    43.1314,
]


class TestElRSIEsElDeWilder:
    def test_la_serie_canonica_da_su_numero(self):
        """70,53 en el primer valor calculable. Es el patrón de la industria."""
        assert rsi(WILDER[:15]) == pytest.approx(70.53, abs=0.01)

    def test_y_sigue_cuadrando_al_avanzar(self):
        """Los tres siguientes de su tabla. Un solo punto podría acertar por
        casualidad; cuatro seguidos no."""
        assert rsi(WILDER[:16]) == pytest.approx(66.32, abs=0.01)
        assert rsi(WILDER[:17]) == pytest.approx(66.55, abs=0.01)
        assert rsi(WILDER[:18]) == pytest.approx(69.41, abs=0.01)

    def test_al_final_de_la_serie(self):
        assert rsi(WILDER) == pytest.approx(37.77, abs=0.05)

    def test_no_es_una_media_movil_simple(self):
        """La trampa clásica: promediar en vez de suavizar da un número
        PARECIDO, y por eso pasa desapercibido durante meses."""
        n = RSI_PERIODO
        sube = [max(b - a, 0) for a, b in zip(WILDER, WILDER[1:])]
        baja = [max(a - b, 0) for a, b in zip(WILDER, WILDER[1:])]
        simple_sube = sum(sube[-n:]) / n
        simple_baja = sum(baja[-n:]) / n
        simple = 100 - 100 / (1 + simple_sube / simple_baja)
        assert abs(rsi(WILDER) - simple) > 1.0, (
            "el suavizado de Wilder y la media simple están dando lo mismo: "
            "o la serie no discrimina o se coló una media simple")


class TestElRSICuandoNoHayNadaQueMedir:
    def test_sin_historia_suficiente_devuelve_None_no_50(self):
        """`None` se pinta «—». Un 50 inventado se lee como «neutral», que es
        una afirmación sobre un ETF del que no sabemos nada."""
        assert rsi([10, 11, 12]) is None
        assert rsi(list(range(RSI_PERIODO))) is None, "justo uno menos del mínimo"
        assert rsi(list(range(RSI_PERIODO + 1))) is not None

    def test_solo_subidas_es_100_y_solo_bajadas_0(self):
        assert rsi([100 + i for i in range(30)]) == 100.0
        assert rsi([100 - i for i in range(30)]) == pytest.approx(0.0, abs=1e-9)

    def test_una_serie_plana_no_es_ni_100_ni_0(self):
        """Sin movimiento no hay fuerza en ninguna dirección: 50."""
        assert rsi([50.0] * 30) == 50.0

    def test_la_basura_no_lo_tumba(self):
        assert rsi([]) is None
        assert rsi(None) is None
        assert rsi(["a", "b", "c"] * 10) is None
        assert rsi([1, None, 2]) is None      # se caen los nulos y no queda serie


class TestElCambioDelDia:
    def test_lo_normal(self):
        assert cambio_pct(110, 100) == pytest.approx(10.0)
        assert cambio_pct(90, 100) == pytest.approx(-10.0)

    def test_un_cierre_previo_de_cero_no_es_infinito_por_ciento(self):
        assert cambio_pct(10, 0) is None

    def test_sin_dato_no_hay_numero(self):
        assert cambio_pct(None, 100) is None
        assert cambio_pct(100, None) is None
        assert cambio_pct("hola", 100) is None


class TestLasTablas:
    def test_la_parrilla_son_los_tres_indices_y_los_once_sectores(self):
        u = universo()
        assert u[:3] == ("SPY", "RSP", "QQQ"), "las referencias van primero"
        assert len(u) == 14
        assert len(set(u)) == 14, "hay un ticker repetido en la parrilla"

    def test_ningun_sector_se_llama_como_su_ticker(self):
        """Una casilla que diga «XLE, XLE» no informa de nada."""
        for t, n in SECTORES + REFERENCIAS:
            assert n and n.upper() != t, f"{t} sin nombre de sector"

    def test_las_industrias_cuelgan_de_un_sector_que_existe(self):
        for sector in INDUSTRIAS:
            assert sector in dict(SECTORES), f"{sector} no es uno de los once"

    def test_ningun_ETF_de_industria_se_repite_entre_sectores(self):
        """El mismo ETF en dos sectores significaría que uno de los dos está mal
        clasificado, y la pantalla lo enseñaría dos veces sin decir por qué."""
        vistos = {}
        for sector, filas in INDUSTRIAS.items():
            for t, _ in filas:
                assert t not in vistos, f"{t} está en {vistos[t]} y en {sector}"
                vistos[t] = sector

    def test_ninguna_industria_repite_el_ticker_de_su_sector(self):
        for sector, filas in INDUSTRIAS.items():
            assert sector not in {t for t, _ in filas}

    def test_los_tres_indices_NO_se_despliegan(self):
        """SPY, RSP y QQQ son índices, no sectores: desglosarlos sería
        inventarse unas industrias que no tienen."""
        for t, _ in REFERENCIAS:
            assert industrias_de(t) == ()

    def test_un_sector_sin_desglose_honesto_se_queda_sin_el(self):
        """XLU: los ETF que se venden como «industrias de utilities» son
        temáticos de energía limpia, que es otra cosa."""
        assert industrias_de("XLU") == ()
        assert "XLU" in dict(SECTORES), "pero el sector sí está en la parrilla"

    def test_preguntar_por_algo_raro_no_revienta(self):
        assert industrias_de("NOEXISTE") == ()
        assert industrias_de("") == ()

    def test_el_nombre_se_resuelve_en_los_tres_niveles(self):
        assert nombre_de("XLE") == "Energía"
        assert nombre_de("spy") == "S&P 500"
        assert nombre_de("SMH") == "Semiconductores"
        assert nombre_de("ZZZZ") == "ZZZZ", "lo desconocido se dice como es"

    def test_cada_sector_con_desglose_tiene_al_menos_dos_industrias(self):
        """Una industria sola no es un desglose: es el mismo sector otra vez."""
        for sector, filas in INDUSTRIAS.items():
            assert len(filas) >= 2, f"{sector} tiene {len(filas)}"


# ═══════════════════════════════════════════════════════════════════════════
#  ROTACIÓN
# ═══════════════════════════════════════════════════════════════════════════

from wbj.sectores import (CAIDA_DIA_ROJO, CATEGORIAS, CUADRANTES,  # noqa: E402
                          UMBRAL_VOLUMEN, VENTANAS_ROC, categoria_de,
                          clasifica_sector, cuadrante, diagnostico, dispersion,
                          flujo_de_capital, lideres_del_dia_rojo, media, roc,
                          salud_del_mercado, serie_rs)


class TestLasFamiliasDeSectores:
    def test_los_once_estan_y_ninguno_en_dos_familias(self):
        vistos = []
        for _, _, ts in CATEGORIAS:
            vistos += list(ts)
        assert len(vistos) == 11, f"{len(vistos)} sectores repartidos, no 11"
        assert len(set(vistos)) == 11, "un sector está en dos familias"
        assert set(vistos) == {t for t, _ in SECTORES}

    def test_lo_que_no_es_sector_no_tiene_familia(self):
        assert categoria_de("SPY") == ""
        assert categoria_de("") == ""


class TestLaPendiente:
    def test_roc_mide_el_cambio_contra_hace_n(self):
        assert roc([100, 101, 102, 110], 3) == pytest.approx(10.0)
        assert roc([110, 101, 102, 100], 3) == pytest.approx(-9.0909, abs=1e-3)

    def test_sin_historia_para_la_ventana_no_hay_pendiente(self):
        assert roc([100, 110], 5) is None
        assert roc([], 5) is None

    def test_una_base_de_cero_no_da_infinito(self):
        assert roc([0, 5], 1) is None

    def test_la_media_necesita_la_ventana_entera(self):
        assert media([1, 2, 3, 4], 4) == pytest.approx(2.5)
        assert media([1, 2, 3], 4) is None


class TestElRSRatio:
    def test_es_la_division_sesion_a_sesion(self):
        assert serie_rs([10, 20, 30], [1, 2, 3]) == [10.0, 10.0, 10.0]

    def test_las_series_se_alinean_por_el_FINAL(self):
        """Un festivo que un proveedor cuenta y el otro no desplazaría todo un
        día, y la pendiente saldría de comparar el martes con el miércoles."""
        assert serie_rs([9, 10, 20, 30], [2, 3]) == [10.0, 10.0]

    def test_un_sector_que_sube_MENOS_que_el_indice_baja_su_RS(self):
        """El caso que hace falta que se entienda: subir no es liderar."""
        rs = serie_rs([100, 101], [100, 105])
        assert rs[-1] < rs[0], "el RS tiene que caer aunque el precio suba"

    def test_la_basura_devuelve_lista_vacia(self):
        assert serie_rs(["a"], [1]) == []
        assert serie_rs([], []) == []


class TestLosCuatroCuadrantes:
    def test_las_cuatro_casillas(self):
        assert cuadrante(1, 1) == "leading"
        assert cuadrante(1, -1) == "weakening"
        assert cuadrante(-1, -1) == "lagging"
        assert cuadrante(-1, 1) == "improving"

    def test_fuerte_perdiendo_impulso_NO_es_debil_ganandolo(self):
        """Los dos están «a medio camino» y significan lo contrario: uno se
        agota, el otro despierta."""
        assert cuadrante(5, -1) != cuadrante(-5, 1)

    def test_sin_dato_no_hay_cuadrante(self):
        assert cuadrante(None, 1) is None
        assert cuadrante(1, None) is None

    def test_los_cuatro_estan_documentados(self):
        assert set(CUADRANTES) == {"leading", "weakening", "lagging", "improving"}
        assert all(v for v in CUADRANTES.values())


class TestElFlujoNecesitaVolumen:
    def test_batir_al_indice_con_volumen_es_ENTRADA(self):
        assert flujo_de_capital(2.0, 0.5, 1_300_000, 1_000_000) == "entrada"

    def test_quedarse_atras_con_volumen_es_SALIDA(self):
        assert flujo_de_capital(-1.0, 0.5, 1_300_000, 1_000_000) == "salida"

    def test_SIN_volumen_no_hay_flujo_aunque_bata_al_indice(self):
        """Sin esta condición el «flujo» sería un ranking de rendimiento con
        otro nombre: subir más que el mercado en un día flojo no prueba que
        haya entrado un dólar institucional."""
        assert flujo_de_capital(5.0, 0.5, 900_000, 1_000_000) is None

    def test_el_umbral_es_exactamente_el_declarado(self):
        justo = UMBRAL_VOLUMEN * 1_000_000
        assert flujo_de_capital(2.0, 0.5, justo, 1_000_000) == "entrada"
        assert flujo_de_capital(2.0, 0.5, justo - 1, 1_000_000) is None

    def test_empatar_con_el_indice_no_es_ni_lo_uno_ni_lo_otro(self):
        assert flujo_de_capital(1.0, 1.0, 2_000_000, 1_000_000) is None

    def test_la_basura_no_lo_tumba(self):
        assert flujo_de_capital(None, 1, 1, 1) is None
        assert flujo_de_capital(1, 1, 1, 0) is None


class TestLaDispersion:
    def test_todo_igual_es_cero(self):
        assert dispersion([1.0] * 11) == pytest.approx(0.0)

    def test_unos_arriba_y_otros_abajo_da_dispersion_alta(self):
        bloque = dispersion([0.9, 1.0, 1.1] * 3)
        rotando = dispersion([-3.0, 2.5, -2.0, 3.1] * 2)
        assert rotando > bloque * 3, (
            "la dispersión no distingue rotación de mercado en bloque")

    def test_con_menos_de_dos_no_hay_dispersion(self):
        assert dispersion([1.0]) is None
        assert dispersion([]) is None


class TestLaSaludDelMercado:
    @staticmethod
    def _serie(n, paso):
        return [1.0 + i * paso for i in range(n)]

    def test_RSP_ganando_y_SPY_subiendo_es_amplitud_solida(self):
        clave, frase, p = salud_del_mercado(self._serie(30, 0.01), 0.8)
        assert clave == "amplia" and p > 0 and frase

    def test_RSP_perdiendo_con_SPY_arriba_es_rally_ESTRECHO(self):
        clave, frase, _ = salud_del_mercado(self._serie(30, -0.005), 0.8)
        assert clave == "estrecho"
        assert "mega-caps" in frase

    def test_RSP_ganando_con_SPY_plano_es_ROTACION(self):
        clave, _, _ = salud_del_mercado(self._serie(30, 0.01), -0.3)
        assert clave == "rotacion"

    def test_los_dos_cayendo_es_debilidad_amplia(self):
        clave, _, _ = salud_del_mercado(self._serie(30, -0.005), -0.9)
        assert clave == "debil"

    def test_sin_serie_no_se_inventa_un_regimen(self):
        clave, frase, p = salud_del_mercado([1.0, 1.1], 0.5)
        assert clave is None and p is None and "suficiente" in frase


class TestElDiaRojo:
    def test_solo_cuenta_cuando_el_SPY_cae_de_verdad(self):
        """En una sesión plana, el que menos baja es el que menos se movió."""
        assert lideres_del_dia_rojo(-0.2, {"XLE": 0.5}) == []
        assert lideres_del_dia_rojo(-1.5, {"XLE": 0.5}) == [("XLE", 0.5)]

    def test_ordena_del_que_mas_aguanta_al_que_menos(self):
        out = lideres_del_dia_rojo(-1.2, {"XLE": 0.5, "XLF": -0.1, "XLK": -2.0})
        assert [t for t, _ in out] == ["XLE", "XLF"]
        assert "XLK" not in dict(out), "el que cae MÁS que el índice no aguanta"

    def test_el_corte_es_el_declarado(self):
        assert lideres_del_dia_rojo(CAIDA_DIA_ROJO + 0.01, {"XLE": 2.0}) == []
        assert lideres_del_dia_rojo(CAIDA_DIA_ROJO, {"XLE": 2.0}) != []

    def test_un_retorno_ilegible_no_tumba_la_lista(self):
        assert lideres_del_dia_rojo("x", {"XLE": 1}) == []
        assert lideres_del_dia_rojo(-2.0, {"XLE": None, "XLF": 1.0}) == [("XLF", 1.0)]


class TestElSectorEntero:
    @staticmethod
    def _sube(n=60, paso=1.0, base=100.0):
        return [base + i * paso for i in range(n)]

    def test_un_sector_que_bate_al_indice_sale_LEADING(self):
        d = clasifica_sector(self._sube(60, 1.0), [1_000_000] * 60,
                             self._sube(60, 0.2))
        assert d["cuadrante"] == "leading", d
        assert d["fuerza"] > 0 and d["impulso"] > 0

    def test_uno_que_se_queda_atras_sale_LAGGING(self):
        d = clasifica_sector(self._sube(60, 0.1), [1_000_000] * 60,
                             self._sube(60, 1.0))
        assert d["cuadrante"] == "lagging", d

    def test_trae_las_tres_ventanas_de_la_especificacion(self):
        d = clasifica_sector(self._sube(80), [1_000_000] * 80, self._sube(80, 0.5))
        for n in VENTANAS_ROC:
            assert f"roc_{n}" in d, f"falta la pendiente de {n} sesiones"
            assert d[f"roc_{n}"] is not None

    def test_el_volumen_relativo_sale_de_su_media_de_20(self):
        vols = [1_000_000] * 59 + [2_000_000]
        d = clasifica_sector(self._sube(60), vols, self._sube(60, 0.5))
        assert d["volumen_rel"] == pytest.approx(2_000_000 / ((1_000_000 * 19 + 2_000_000) / 20))

    def test_sin_historia_devuelve_huecos_y_no_revienta(self):
        d = clasifica_sector([100, 101], [1], [100, 100])
        assert d["cuadrante"] is None and d["rs"] is not None

    def test_con_series_vacias_tampoco_revienta(self):
        d = clasifica_sector([], [], [])
        assert d["rs"] is None and d["cuadrante"] is None


class TestElDiagnostico:
    def test_crecimiento_flojo_y_defensivos_fuertes_es_RISK_OFF(self):
        por = {"XLK": {"cuadrante": "lagging"}, "XLC": {"cuadrante": "weakening"},
               "XLP": {"cuadrante": "leading"}, "XLV": {"cuadrante": "leading"}}
        fr = diagnostico(por)
        assert any("protección" in f for f in fr), fr

    def test_crecimiento_flojo_y_ciclicos_fuertes_es_ROTACION_SANA(self):
        por = {"XLK": {"cuadrante": "weakening"}, "XLF": {"cuadrante": "leading"},
               "XLI": {"cuadrante": "improving"}}
        fr = diagnostico(por)
        assert any("economía real" in f for f in fr), fr

    def test_el_rally_estrecho_se_avisa_aparte(self):
        fr = diagnostico({}, salud_clave="estrecho")
        assert any("amplitud" in f for f in fr), fr

    def test_el_flujo_se_resume_cuando_hay_de_los_dos_lados(self):
        por = {"XLF": {"cuadrante": "leading", "flujo": "entrada"},
               "XLK": {"cuadrante": "lagging", "flujo": "salida"}}
        fr = diagnostico(por)
        assert any("XLF" in f and "XLK" in f for f in fr), fr

    def test_un_dia_sin_nada_que_decir_NO_inventa_una_frase(self):
        """Llenar el hueco con una frase genérica es lo que hace que nadie
        vuelva a leer esta sección."""
        assert diagnostico({"XLK": {"cuadrante": "leading"}}) == []
        assert diagnostico({}) == []
