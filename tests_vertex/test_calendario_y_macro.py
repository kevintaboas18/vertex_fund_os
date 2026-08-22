"""Las dos cajas del panel: los resultados que vienen y lo macro.

Kevin, el 22/08/2026, sobre la caja de resultados:

    «que se vean los próximos 14 días como dice ya que no se ven.
     Solo se ven 6 días.»

Y no era un fallo de carga ni del proveedor. La caja pedía catorce días y los
recibía; lo que se los comía era el recorte. `_RESULTADOS_MAX` cortaba la lista
YA ORDENADA POR FECHA a 400 filas, así que en temporada alta —cuando reportan
cientos de empresas grandes cada día— los primeros días agotaban la cuota y del
séptimo en adelante no llegaba ni una. Medido con sesenta empresas al día:
sobrevivían 7 de los 14. La mitad del calendario se caía por el borde de una
lista, en silencio y con el rótulo «próximos 14 días» encima.

Y sobre lo que se ve al pasar el cursor:

    «se supone que todos los ticker le pertenezcan a un sector. […] me diga el
     nombre de la empresa con una (-) y seguido del sector.»
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: Cuántas empresas grandes reportan cada día en el escenario de temporada
#: alta. Sesenta es realista para febrero o agosto y es lo que hace visible el
#: recorte: 14 × 60 = 840 filas contra un tope global de 400.
_POR_DIA = 60


class _Respuesta:
    status_code = 200

    def __init__(self, cuerpo):
        self._cuerpo = cuerpo

    def json(self):
        return self._cuerpo


@pytest.fixture
def calendario_lleno(monkeypatch):
    """FMP contestando una temporada alta: 14 días × 60 empresas grandes."""
    import vertex_api as V

    monkeypatch.setenv("FMP_API_KEY", "x" * 20)
    hoy = date.today()
    cal = [{"symbol": f"T{d:02d}{i:03d}",
            "date": (hoy + timedelta(days=d)).isoformat(), "when": "amc"}
           for d in range(14) for i in range(_POR_DIA)]
    scr = [{"symbol": f"T{d:02d}{i:03d}", "marketCap": 1e12 - i * 1e9,
            "companyName": f"Empresa {d}-{i}", "sector": "Consumer Cyclical"}
           for d in range(14) for i in range(_POR_DIA)]

    def _get(url, **kw):
        return _Respuesta(cal if "earnings-calendar" in url else scr)

    monkeypatch.setattr(V.requests, "get", _get)
    return V


class TestLosCatorceDiasEstanTodos:
    def test_los_14_dias_estan_representados(self, calendario_lleno):
        """El caso exacto: catorce días pedidos, catorce días enseñados."""
        r = calendario_lleno._resultados_calcula()
        dias = sorted({f["fecha"] for f in r["filas"]})
        assert len(dias) == 14, (
            f"la caja dice «próximos 14 días» y solo enseña {len(dias)}: el "
            "recorte se está comiendo la cola del calendario, que es lo que "
            "Kevin veía como «solo se ven 6 días»")

    def test_el_ULTIMO_dia_del_calendario_llega(self, calendario_lleno):
        """El día 14 es el que se perdía primero, y el que nadie echa de menos
        hasta que busca una empresa que reporta la semana que viene."""
        r = calendario_lleno._resultados_calcula()
        ultimo = (date.today() + timedelta(days=13)).isoformat()
        assert any(f["fecha"] == ultimo for f in r["filas"]), (
            f"del {ultimo} —el último día de la ventana— no llegó ni una fila")

    def test_recortar_por_el_TOTAL_amputaba_la_mitad(self):
        """La medida de la avería, para que el número no se pierda.

        No toca el código: reproduce el recorte viejo sobre los mismos datos y
        cuenta los días que sobrevivían. Si algún día alguien vuelve a cortar
        por el total, este caso explica en una línea qué se rompe.
        """
        hoy = date.today()
        todas = sorted(
            ({"fecha": (hoy + timedelta(days=d)).isoformat(),
              "cap": 1e12 - i * 1e9}
             for d in range(14) for i in range(_POR_DIA)),
            key=lambda x: (x["fecha"], -x["cap"]))
        sobreviven = len({f["fecha"] for f in todas[:400]})
        assert sobreviven <= 7, (
            "el escenario ya no reproduce la avería y el caso dejó de medir "
            "nada")

    def test_y_el_recorte_se_DICE(self, calendario_lleno):
        """Recortar está bien; callarlo no. Es la misma regla que el umbral."""
        r = calendario_lleno._resultados_calcula()
        assert r["recortadas"] > 0, (
            "con 60 empresas al día tiene que sobrar gente: si no se cuenta, "
            "la caja no puede decir cuántas se quedaron fuera")


class TestCadaTickerDiceDeQuienEsYDeQueSector:
    def test_TODAS_las_filas_traen_su_sector(self, calendario_lleno):
        """«Se supone que todos los ticker le pertenezcan a un sector.»

        Antes el sector salía solo de `MIEMBROS` —114 componentes escritos a
        mano—, así que cualquier otra empresa salía sin sector y la ayuda decía
        «fuera de los once sectores», que era falso: sí pertenece a uno, solo
        que no estaba escrito aquí. Ahora se traduce el sector que ya viene en
        el listado del tamaño.
        """
        r = calendario_lleno._resultados_calcula()
        sin = [f["ticker"] for f in r["filas"] if not f.get("sector")]
        assert not sin, f"{len(sin)} filas sin sector, p. ej. {sin[:5]}"

    def test_TODAS_traen_el_nombre_de_la_empresa(self, calendario_lleno):
        """Sin el nombre, la ayuda emergente no puede decir de quién es."""
        r = calendario_lleno._resultados_calcula()
        sin = [f["ticker"] for f in r["filas"] if not f.get("nombre")]
        assert not sin, f"{len(sin)} filas sin nombre, p. ej. {sin[:5]}"

    def test_el_sector_de_FMP_se_traduce_a_su_casilla(self, calendario_lleno):
        r = calendario_lleno._resultados_calcula()
        f = r["filas"][0]
        assert f["sector"] == "XLY" and f["sector_nombre"] == "Consumo discrecional", (
            f"«Consumer Cyclical» tenía que caer en XLY y cayó en "
            f"{f['sector']} / {f['sector_nombre']}")

    def test_MIEMBROS_manda_cuando_lo_tiene(self, monkeypatch):
        """El mapa escrito a mano es el autoritativo: está revisado.

        FMP mete a las farmacéuticas grandes en «Healthcare» igual que a las
        aseguradoras de salud, y donde los dos discrepen gana el que alguien
        miró.
        """
        import vertex_api as V
        from wbj.sectores import MIEMBROS

        monkeypatch.setenv("FMP_API_KEY", "x" * 20)
        tk = MIEMBROS["XLE"][0]                  # una de Energía, escrita a mano
        hoy = date.today().isoformat()
        cal = [{"symbol": tk, "date": hoy, "when": "bmo"}]
        # FMP la manda a OTRO sector a propósito.
        scr = [{"symbol": tk, "marketCap": 5e11, "companyName": "Petrolera",
                "sector": "Technology"}]
        monkeypatch.setattr(V.requests, "get",
                            lambda url, **kw: _Respuesta(
                                cal if "earnings-calendar" in url else scr))
        r = V._resultados_calcula()
        assert r["filas"][0]["sector"] == "XLE", (
            "el sector escrito a mano tiene que ganarle al de FMP")


class TestLaAyudaEmergenteDiceNombreYSector:
    def test_el_panel_arma_nombre_guion_sector(self):
        """«el nombre de la empresa con una (-) y seguido del sector»."""
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        assert "[f.nombre, f.sector_nombre].filter(Boolean).join(' - ')" in html, (
            "la ficha ya no arma «Nombre - Sector» para la ayuda emergente")


class TestMayorNoEsSiempreMejor:
    """La avería de lectura de la caja macro.

    El panel pintaba de VERDE todo lo que saliera por encima del consenso y de
    ROJO todo lo que saliera por debajo, para los veinte indicadores por igual.
    Un paro que sube más de lo esperado salía en verde. Una inflación disparada,
    también.

    El comentario del código se defendía diciendo «sin juicio de bueno o malo»,
    pero un verde PEGADO A UNA CIFRA es exactamente ese juicio, y así lo lee
    quien mira. Medido sobre siete casos: acertaba cuatro y fallaba tres, y los
    tres eran el paro y el IPC — los dos que más se miran.

        «Recuerda que no siempre si es mayor a lo esperado es bueno o si es
         menos a lo esperado es malo.» — Kevin, 22/08/2026
    """

    @pytest.mark.parametrize("nombre,nivel,mejor", [
        ("Core CPI", 1, "bajo"),
        ("CPI", 1, "bajo"),
        ("Core PCE Price Index", 1, "bajo"),
        ("Unemployment Rate", 1, "bajo"),
        ("Initial Jobless Claims", 2, "bajo"),
        ("Nonfarm Payrolls", 1, "alto"),
        ("GDP Growth Rate", 1, "alto"),
        ("Retail Sales", 1, "alto"),
        ("ISM Manufacturing PMI", 1, "alto"),
        ("Fed Interest Rate Decision", 1, None),
    ])
    def test_cada_indicador_sabe_hacia_donde_es_bueno(self, nombre, nivel, mejor):
        import vertex_api as V

        assert V._macro_ficha(nombre) == (nivel, mejor)

    def test_la_clave_mas_LARGA_gana(self):
        """«core cpi» tiene que ganarle a «cpi», o el matiz se pierde.

        Buscando por inclusión sin ordenar, la primera coincidencia manda y
        «cpi» está dentro de «core cpi». Aquí las dos comparten dirección, así
        que el fallo sería mudo — y por eso se fija el orden, no el resultado.
        """
        import vertex_api as V

        assert V._MACRO_CLAVES == tuple(sorted(V._MACRO_TABLA, key=len,
                                               reverse=True))

    @pytest.mark.parametrize("salio,esperado,mejor,bueno", [
        # El paro POR ENCIMA de lo esperado: sorprende al alza y es PEOR.
        (4.4, 4.1, "bajo", False),
        (3.8, 4.1, "bajo", True),
        # El IPC más caliente: peor, aunque el número sea mayor.
        (3.4, 3.1, "bajo", False),
        # Las nóminas sí: más es mejor.
        (280.0, 180.0, "alto", True),
        (90.0, 180.0, "alto", False),
    ])
    def test_el_juicio_sigue_a_la_direccion_del_indicador(self, salio, esperado,
                                                          mejor, bueno):
        import vertex_api as V

        assert V._macro_lectura_del_dato(salio, esperado, mejor)["bueno"] is bueno

    def test_un_dato_EN_LINEA_no_es_ni_bueno_ni_malo(self):
        """Ya estaba en el precio: no hay sorpresa que juzgar."""
        import vertex_api as V

        d = V._macro_lectura_del_dato(3.1, 3.1, "bajo")
        assert d["sorpresa"] == 0.0 and d["bueno"] is None

    def test_una_DECISION_de_tipos_no_lleva_juicio(self):
        """Un tipo más alto no es «peor dato»: es política.

        Pintarlo de rojo sería una opinión sobre la Fed disfrazada de lectura
        del dato.
        """
        import vertex_api as V

        assert V._macro_lectura_del_dato(5.5, 5.25, None)["bueno"] is None

    def test_la_SORPRESA_sigue_siendo_el_hecho_desnudo(self):
        """El hecho y el juicio son cosas distintas y no se mezclan: la
        sorpresa dice por encima o por debajo, sin opinar."""
        import vertex_api as V

        assert V._macro_lectura_del_dato(4.4, 4.1, "bajo")["sorpresa"] > 0


class TestLaCajaMacroFiltraYFecha:
    @pytest.fixture
    def macro(self, monkeypatch):
        import vertex_api as V

        monkeypatch.setenv("FMP_API_KEY", "x" * 20)
        hoy = date.today()
        crudo = [
            # Ocho peticiones de desempleo (nivel 2) más recientes que el IPC.
            *[{"country": "US", "event": "Initial Jobless Claims",
               "date": (hoy - timedelta(days=d)).isoformat() + " 12:30:00",
               "actual": "220K", "estimate": "215K", "previous": "218K"}
              for d in range(1, 9)],
            # El IPC (nivel 1) es más viejo, y es el que no puede caerse.
            {"country": "US", "event": "Core CPI", "actual": "3.4%",
             "date": (hoy - timedelta(days=12)).isoformat() + " 12:30:00",
             "estimate": "3.1%", "previous": "3.2%"},
            # Uno que viene dentro de la ventana…
            {"country": "US", "event": "Nonfarm Payrolls", "actual": None,
             "date": (hoy + timedelta(days=3)).isoformat() + " 12:30:00",
             "estimate": "180K", "previous": "175K"},
            # …y uno atrasado, con fecha VIEJA y sin cifra: no es «lo que viene».
            {"country": "US", "event": "Retail Sales", "actual": None,
             "date": (hoy - timedelta(days=5)).isoformat() + " 12:30:00",
             "estimate": "0.4%", "previous": "0.2%"},
        ]

        class _R:
            status_code = 200

            def json(self):
                return crudo

        monkeypatch.setattr(V.requests, "get", lambda url, **kw: _R())
        return V._macro_calcula()

    def test_el_IPC_no_lo_expulsan_ocho_peticiones_de_desempleo(self, macro):
        """«Los más recientes y los más importantes. No todos son importantes.»

        Ordenando solo por fecha, ocho datos semanales de nivel 2 empujaban
        fuera de la caja al dato de nivel 1 de la semana pasada.
        """
        eventos = [f["evento"] for f in macro["publicados"]]
        assert any("CPI" in e for e in eventos), (
            f"el IPC se cayó de la caja, empujado por lo semanal: {eventos}")

    def test_un_dato_RETRASADO_no_desaparece_del_panel(self, macro):
        """Publicado = tiene DATO, no «la fecha ya pasó».

        Lo quité para que «Próximos datos» empezara hoy, y estaba mal: unas
        ventas minoristas que debían salir hace cinco días y no han salido son
        justo lo que más se mira, y desaparecían sin dejar rastro. Lo que había
        que arreglar no era que estuvieran, sino que enseñaran una fecha pasada
        como si fuera una cita futura.
        """
        eventos = [f["evento"] for f in macro["proximos"]]
        assert any("Retail" in e for e in eventos), (
            f"el dato retrasado se perdió por el camino: {eventos}")

    def test_y_va_MARCADO_como_retrasado(self, macro):
        """Para que el panel diga «retrasado» en vez de una fecha vieja."""
        f = next(f for f in macro["proximos"] if "Retail" in f["evento"])
        assert f.get("retrasado") is True

    def test_el_retrasado_va_DELANTE(self, macro):
        """Es lo más inminente que hay, no lo más viejo."""
        assert "Retail" in macro["proximos"][0]["evento"], (
            "un dato que debía salir y no ha salido tiene que ir el primero")

    def test_lo_que_SI_esta_programado_no_se_marca(self, macro):
        f = next(f for f in macro["proximos"] if "Payrolls" in f["evento"])
        assert not f.get("retrasado")

    def test_la_ventana_de_lo_que_viene_es_de_SIETE_dias(self):
        """«Desde hoy y 6 días más en adelante.»"""
        import vertex_api as V

        assert V._MACRO_DIAS_ADELANTE == 6

    def test_cada_publicado_trae_su_fecha_y_su_lectura(self, macro):
        for f in macro["publicados"]:
            assert f["fecha"], f"{f['evento']} sin fecha de publicación"
            assert "bueno" in f and "sorpresa" in f, (
                f"{f['evento']} llega sin lectura: el panel no puede colorearlo")


class TestLaRotacionDentroDeUnSector:
    """«Investiga y explica la rotaciones dentro de un mismo sector de industria
    a otra industria.» — Kevin, 22/08/2026.

    Es una pregunta distinta de la rotación sectorial y se mide distinto. La de
    fuera compara cada sector contra el SPY; esta compara cada industria contra
    **su propio sector**, que es lo único que la hace intra-sector: que los
    semiconductores suban un 8% no dice nada si XLK subió un 8% también. Lo que
    informa es la diferencia.

    Es lo que separa «entra dinero en tecnología» de «entra en semiconductores
    Y SALE de software» — por fuera es la misma tecnología, por dentro son dos
    apuestas opuestas.
    """

    @pytest.fixture
    def rotacion(self, monkeypatch):
        import vertex_api as V

        # XLK sube 8% en 3M. Los semis le GANAN, el software le PIERDE.
        filas = {
            "XLK": {"ticker": "XLK", "nombre": "Tecnología",
                    "cambios": {"7D": 2.0, "1M": 4.0, "3M": 8.0}},
            "SMH": {"ticker": "SMH", "nombre": "Semiconductores",
                    "cambios": {"7D": 5.0, "1M": 9.0, "3M": 14.0}},
            "IGV": {"ticker": "IGV", "nombre": "Software",
                    "cambios": {"7D": 0.5, "1M": 1.0, "3M": 3.0}},
            "XLF": {"ticker": "XLF", "nombre": "Financiero",
                    "cambios": {"7D": 1.0, "1M": 2.0, "3M": 5.0}},
            # KRE: le gana en la semana y le pierde en el trimestre. Es un
            # rebote, no una rotación, y las tres ventanas son lo que permite
            # distinguirlo.
            "KRE": {"ticker": "KRE", "nombre": "Bancos regionales",
                    "cambios": {"7D": 3.0, "1M": 1.0, "3M": 2.0}},
        }
        monkeypatch.setattr(
            V, "api_sectores",
            lambda tickers="": {"ok": True,
                                "filas": [filas[t] for t in tickers.split(",")
                                          if t in filas]})
        return V._rotacion_dentro_de_los_sectores("SMH:XLK,IGV:XLK,KRE:XLF")

    def test_mide_la_industria_contra_SU_sector_y_no_contra_el_indice(self, rotacion):
        """Los semis suben 14% y XLK 8%: la diferencia es +6pp, no 14%."""
        assert "SMH (Semiconductores) vs XLK" in rotacion
        assert "3M +6.00pp" in rotacion, (
            "la diferencia contra su propio sector no está: si aquí sale el "
            "retorno a secas, esto es otra vez la rotación de fuera")

    def test_la_industria_que_PIERDE_sale_en_negativo(self, rotacion):
        assert "IGV (Software) vs XLK" in rotacion and "3M -5.00pp" in rotacion

    def test_las_TRES_ventanas_separan_el_rebote_de_la_rotacion(self, rotacion):
        """KRE le gana en la semana y le pierde en el trimestre.

        Con una sola ventana los dos casos se leen igual. Con tres, no.
        """
        linea = next(l for l in rotacion.splitlines() if "KRE" in l)
        assert "7D +2.00pp" in linea and "3M -3.00pp" in linea, (
            f"la línea de KRE no deja ver el rebote: {linea}")

    def test_se_agrupa_por_sector(self, rotacion):
        assert "  XLK:" in rotacion and "  XLF:" in rotacion

    def test_sin_tabla_de_industrias_NO_revienta_la_nota(self, monkeypatch):
        """Esta sección es un extra. Tumbar la nota entera porque el proveedor
        no dio una industria sería cambiar mucho por muy poco."""
        import vertex_api as V

        assert V._rotacion_dentro_de_los_sectores("") == ""
        assert V._rotacion_dentro_de_los_sectores("no-valido,;;") == ""

    def test_ni_aunque_el_proveedor_se_caiga(self, monkeypatch):
        import vertex_api as V

        def _revienta(tickers=""):
            raise RuntimeError("FMP caído")

        monkeypatch.setattr(V, "api_sectores", _revienta)
        assert V._rotacion_dentro_de_los_sectores("SMH:XLK") == ""


class TestLaNotaPideTodoLoQuePidioKevin:
    """El formato de las dos notas, sección por sección.

    No mide la prosa del modelo —eso no es determinista— sino que el ENCARGO
    esté completo: una sección que no se pide no se escribe nunca.
    """

    @pytest.mark.parametrize("seccion", [
        "Qué le hace al dólar",           # el dólar
        "economía de EE.UU.",             # la economía de EE.UU.
        "resto del mundo",                # y la del mundo
        "Qué hace la Fed",                # las decisiones de la Fed
        "Los tipos y los bonos",          # intereses y bonos
        "para la bolsa",                  # la bolsa
        "Sector por sector",              # cada sector
        "Qué viene y qué vigilar",        # los próximos, con fecha
    ])
    def test_la_nota_macro_pide_cada_pieza(self, seccion):
        import vertex_api as V

        assert seccion in V._MACRO_LECTURA_SYSTEM

    def test_la_nota_macro_avisa_de_que_mas_alto_no_es_mejor(self):
        """La regla que impide repetir con palabras el error del color."""
        import vertex_api as V

        assert "MÁS ALTO NO ES «MEJOR»" in V._MACRO_LECTURA_SYSTEM

    def test_y_de_que_buena_economia_no_es_buena_bolsa(self):
        import vertex_api as V

        assert "NO son lo mismo" in V._MACRO_LECTURA_SYSTEM

    @pytest.mark.parametrize("seccion", [
        "Dónde entra el dinero",
        "De dónde sale",
        "Sector por sector",                    # los once, uno por uno
        "Rotación dentro de los sectores",      # industria a industria
        "Las próximas semanas",
        "Los próximos tres meses",
        "Dónde está la oportunidad de verdad",
    ])
    def test_la_nota_de_rotacion_pide_cada_pieza(self, seccion):
        import vertex_api as V

        assert seccion in V._LECTURA_SYSTEM

    def test_pide_los_ONCE_sectores_por_su_nombre(self):
        """«Me explique lo que está pasando por cada sector.»

        Sin nombrarlos, el modelo cuenta los cuatro llamativos y se deja siete.
        """
        import vertex_api as V

        for etf in ("XLK", "XLF", "XLV", "XLY", "XLC", "XLI", "XLP", "XLE",
                    "XLU", "XLRE", "XLB"):
            assert etf in V._LECTURA_SYSTEM, f"{etf} no se le pide al modelo"
