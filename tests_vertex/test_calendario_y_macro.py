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

def _caja_macro(monkeypatch, eventos):
    """Monta la caja de lo macro con un calendario inventado.

    `eventos` son tuplas `(nombre, hace_cuántos_días, salió, esperado, previo)`.
    Se usa desde varias clases, así que vive aquí y no dentro de una de ellas.
    """
    import vertex_api as V

    monkeypatch.setenv("FMP_API_KEY", "x" * 20)
    hoy = date.today()
    crudo = [{"country": "US", "event": nombre,
              "date": (hoy - timedelta(days=dias)).isoformat() + " 12:30:00",
              "actual": salio, "estimate": esp, "previous": prev}
             for nombre, dias, salio, esp, prev in eventos]

    class _R:
        status_code = 200

        def json(self):
            return crudo

    monkeypatch.setattr(V.requests, "get", lambda *a, **k: _R())
    return V._macro_calcula()


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
        # Nivel 1 desde el 27/08/2026: Kevin las nombró en su lista de alto
        # impacto. Antes estaban en 2 —de relleno—, que era una opinión mía.
        ("Initial Jobless Claims", 1, "bajo"),
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

    #: Las filas del escenario. XLK sube 8% en 3M; los semis le GANAN y el
    #: software le PIERDE. KRE le gana en la semana y le pierde en el
    #: trimestre: eso es un rebote, no una rotación, y las tres ventanas son lo
    #: que permite distinguirlo.
    FILAS = {
        "XLK": {"ticker": "XLK", "nombre": "Tecnología",
                "cambios": {"7D": 2.0, "1M": 4.0, "3M": 8.0}},
        "SMH": {"ticker": "SMH", "nombre": "Semiconductores",
                "cambios": {"7D": 5.0, "1M": 9.0, "3M": 14.0}},
        "IGV": {"ticker": "IGV", "nombre": "Software",
                "cambios": {"7D": 0.5, "1M": 1.0, "3M": 3.0}},
        "XLF": {"ticker": "XLF", "nombre": "Financiero",
                "cambios": {"7D": 1.0, "1M": 2.0, "3M": 5.0}},
        "KRE": {"ticker": "KRE", "nombre": "Bancos regionales",
                "cambios": {"7D": 3.0, "1M": 1.0, "3M": 2.0}},
    }

    @classmethod
    def _siembra(cls, V, *tandas):
        """Deja en la caché las tandas indicadas, como si ya se hubieran pedido.

        La sección lee de la CACHÉ y nunca espera por la red: es un extra que se
        apunta si ya está pagado. Sembrarla es la única forma de medirla sin
        inventarse una llamada que en producción no ocurre.
        """
        import time

        for tanda in tandas:
            clave = ",".join(V._sectores_pedidos(tanda))
            with V._SECTORES_LOCK:
                V._SECTORES_CACHE[clave] = (time.time(), {
                    "ok": True,
                    "filas": [cls.FILAS[t] for t in tanda.split(",")
                              if t in cls.FILAS]})

    @pytest.fixture
    def rotacion(self, monkeypatch):
        import vertex_api as V

        monkeypatch.setattr(V, "_SECTORES_CACHE", {})
        monkeypatch.setattr(V, "api_sectores",
                            lambda tickers="": {"ok": True, "filas": []})
        self._siembra(V, "IGV,SMH,XLK", "KRE,XLF")
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

    def test_con_la_cache_VACIA_no_espera_por_la_red(self, monkeypatch):
        """La avería del 23/08: el botón «Explícame qué está pasando», muerto.

        Pedir los sesenta tickers de golpe eran CIENTO VEINTE llamadas a FMP
        —cada ticker cuesta la cotización y quince meses de velas— antes de que
        el modelo escribiera la primera palabra. En el plan free eso no es
        lento: la petición se agota y el panel no enseña nada.

        Ahora la sección se salta y se pide por detrás. La nota sale hoy sin
        ella; mañana sale con ella.
        """
        import time

        import vertex_api as V

        monkeypatch.setattr(V, "_SECTORES_CACHE", {})
        llamadas = []

        def _lento(tickers=""):
            llamadas.append(tickers)
            time.sleep(20)
            return {"ok": True, "filas": []}

        monkeypatch.setattr(V, "api_sectores", _lento)
        t = time.time()
        r = V._rotacion_dentro_de_los_sectores("SMH:XLK,IGV:XLK,KRE:XLF")
        assert time.time() - t < 2.0, "la nota está esperando por la red"
        assert r == "", "sin datos no se inventa la sección"
        time.sleep(1)
        assert llamadas, "no se pidió por detrás: mañana tampoco estará"

    def test_pide_SECTOR_A_SECTOR_y_no_los_sesenta_de_golpe(self, monkeypatch):
        """La ruta corta en `_SECTORES_MAX_PEDIDOS`. Pidiendo los sesenta de
        golpe devolvía menos de la mitad y no lo decía, así que la sección no
        podía salir completa NUNCA. Por sector son seis tickers por tanda."""
        import time

        import vertex_api as V

        monkeypatch.setattr(V, "_SECTORES_CACHE", {})
        tandas = []
        monkeypatch.setattr(V, "api_sectores",
                            lambda tickers="": (tandas.append(tickers)
                                                or {"ok": True, "filas": []}))
        V._rotacion_dentro_de_los_sectores("SMH:XLK,IGV:XLK,KRE:XLF,KBE:XLF")
        time.sleep(1)
        assert len(tandas) == 2, f"se esperaban dos tandas y salieron {tandas}"
        for t in tandas:
            assert len(t.split(",")) <= V._SECTORES_MAX_PEDIDOS, (
                f"la tanda «{t}» pasa del tope de la ruta y volvería recortada")

    def test_lo_que_YA_esta_sale_aunque_falte_el_resto(self, monkeypatch):
        """Degrada bien: con tecnología en la caché y financiero no, sale la de
        tecnología y se pide la otra. Todo o nada habría sido nada casi
        siempre."""
        import time

        import vertex_api as V

        monkeypatch.setattr(V, "_SECTORES_CACHE", {})
        pedidas = []
        monkeypatch.setattr(V, "api_sectores",
                            lambda tickers="": (pedidas.append(tickers)
                                                or {"ok": True, "filas": []}))
        self._siembra(V, "IGV,SMH,XLK")          # solo tecnología
        r = V._rotacion_dentro_de_los_sectores("SMH:XLK,IGV:XLK,KRE:XLF")
        time.sleep(1)
        assert "XLK:" in r and "XLF:" not in r
        assert any("XLF" in p for p in pedidas), "no se pidió lo que faltaba"

    def test_sin_tabla_de_industrias_NO_revienta_la_nota(self, monkeypatch):
        """Esta sección es un extra. Tumbar la nota entera porque el proveedor
        no dio una industria sería cambiar mucho por muy poco."""
        import vertex_api as V

        assert V._rotacion_dentro_de_los_sectores("") == ""
        assert V._rotacion_dentro_de_los_sectores("no-valido,;;") == ""

    def test_ni_aunque_el_proveedor_se_caiga(self, monkeypatch):
        """El calentamiento de fondo se traga el fallo: si sale mal, la sección
        no aparece y ya está. Dejar que la excepción subiera mataría un hilo
        suelto y ensuciaría el log con algo que no es un problema."""
        import time

        import vertex_api as V

        def _revienta(tickers=""):
            raise RuntimeError("FMP caído")

        monkeypatch.setattr(V, "_SECTORES_CACHE", {})
        monkeypatch.setattr(V, "api_sectores", _revienta)
        assert V._rotacion_dentro_de_los_sectores("SMH:XLK") == ""
        time.sleep(0.5)                          # el hilo de fondo ya reventó


class TestLaNotaPideTodoLoQuePidioKevin:
    """El formato de las dos notas, sección por sección.

    No mide la prosa del modelo —eso no es determinista— sino que el ENCARGO
    esté completo: una sección que no se pide no se escribe nunca.
    """

    @pytest.mark.parametrize("seccion", [
        "Qué le hace al dólar",           # el dólar
        "economía de EE.UU.",             # la economía de EE.UU.
        "Qué hace la Fed",                # las decisiones de la Fed
        "Los tipos y los bonos",          # intereses y bonos
        "para la bolsa",                  # la bolsa
        "Sector por sector",              # cada sector
        "Qué vigilar de estos mismos datos",
    ])
    def test_la_nota_macro_pide_cada_pieza(self, seccion):
        import vertex_api as V

        assert seccion in V._MACRO_LECTURA_SYSTEM

    @pytest.mark.parametrize("fuera", [
        "para la gente", "resto del mundo",
    ])
    def test_y_NO_pide_lo_que_Kevin_quito(self, fuera):
        """Las quitó el 23/08: «qué significa para la gente» y «para el resto
        del mundo». Sin este caso vuelven en el primer retoque del prompt."""
        import vertex_api as V

        assert fuera not in V._MACRO_LECTURA_SYSTEM

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


class TestUnComunicadoNoOcupaCuatroHuecos:
    """La captura del 22/08: cuatro sabores del mismo dato llenando la caja.

    FMP parte cada comunicado en cortes. Las ventas minoristas de UN martes
    salían como cuatro filas —MoM, YoY, sin automóviles, y sin gasolina ni
    automóviles— y se comían cuatro de los ocho huecos, empujando fuera al
    resto de la semana. La caja parecía tener cinco datos y tenía dos.

        «No es lo que yo quiero y no sale los últimos 5 más recientes.»
    """

    @pytest.fixture
    def caja(self, monkeypatch):
        import vertex_api as V

        monkeypatch.setenv("FMP_API_KEY", "x" * 20)
        hoy = date.today()

        def _ev(nombre, dias, salio, esp=None, prev=None):
            return {"country": "US", "event": nombre,
                    "date": (hoy - timedelta(days=dias)).isoformat() + " 12:30:00",
                    "actual": salio, "estimate": esp, "previous": prev}

        crudo = [
            # Los cuatro cortes de la captura, del mismo día.
            _ev("Retail Sales Excluding Gas and Autos MoM", 8, "-0.2%", "0.3%", "0.4%"),
            _ev("Retail Sales Excluding Autos MoM", 8, "-0.3%", "0.2%", "-0.2%"),
            _ev("Retail Sales YoY", 8, "5%", "6%", "6.8%"),
            _ev("Retail Sales MoM", 8, "-0.6%", "0.1%", "0.2%"),
            _ev("Inflation Rate MoM", 10, "0.3%", "0.2%", "0.1%"),
            _ev("Inflation Rate YoY", 10, "3.1%", "3.0%", "2.9%"),
            _ev("Core CPI YoY", 10, "3.4%", "3.1%", "3.2%"),
            _ev("Nonfarm Payrolls", 14, "280K", "180K", "175K"),
            _ev("Unemployment Rate", 14, "4.4%", "4.1%", "4.2%"),
            _ev("ISM Manufacturing PMI", 18, "52.1", "50.0", "49.5"),
        ]

        class _R:
            status_code = 200

            def json(self):
                return crudo

        monkeypatch.setattr(V.requests, "get", lambda *a, **k: _R())
        return V._macro_calcula()

    def test_las_ventas_minoristas_ocupan_DOS_huecos_y_no_cuatro(self, caja):
        """Dos, no uno: el contrato cambió porque Kevin lo cambió.

        Este caso pedía UN hueco, y con eso se perdía el interanual entero.
        «Recuerda que son diferentes»: el mensual y el interanual son dos
        datos, con dos consensos, y los dos salen. Lo que sigue sin poder
        pasar —y es lo que este caso guarda— es que las cuatro variantes del
        mismo martes ocupen los cuatro huecos: los «excluyendo…» siguen sin
        titular.
        """
        cuantas = [f["evento"] for f in caja["publicados"] if "Retail" in f["evento"]]
        assert sorted(cuantas) == ["Retail Sales MoM", "Retail Sales YoY"], (
            f"el mismo comunicado ocupa {len(cuantas)} huecos: {cuantas}")

    def test_los_EXCLUYENDO_no_titulan(self, caja):
        """Son sub-agregados: sirven para entender el dato, no para titularlo."""
        malos = [f["evento"] for f in caja["publicados"]
                 if "Excluding" in f["evento"]]
        assert not malos, f"un sub-agregado se quedó con el hueco: {malos}"

    def test_de_las_ventas_manda_el_MoM(self, caja):
        """Es el corte que cita el mercado: «las ventas cayeron un 0,6%».

        «Manda» es ir DELANTE, no ser el único: el interanual sale también, una
        fila más abajo. Antes esto se resolvía descartando el otro corte, y era
        la avería.
        """
        f = next(f for f in caja["publicados"] if "Retail" in f["evento"])
        assert f["evento"] == "Retail Sales MoM" and f["salio"] == -0.6

    def test_pero_de_la_inflacion_manda_el_YoY(self, caja):
        """La inflación se cita a doce meses: «la inflación está en el 3,1%».

        Es la vuelta de tuerca del caso de arriba: la regla NO es «siempre el
        mensual», es «el que cita el mercado para esa familia» — y para el PIB
        de EE.UU. no es ninguno de los dos, es el trimestre anualizado.
        """
        f = next(f for f in caja["publicados"]
                 if f["evento"].startswith("Inflation Rate"))
        assert f["evento"] == "Inflation Rate YoY" and f["salio"] == 3.1

    def test_el_nucleo_sigue_siendo_una_familia_aparte(self, caja):
        """«Core CPI» y «CPI» no son el mismo dato: los dos se miran."""
        eventos = [f["evento"] for f in caja["publicados"]]
        assert "Core CPI YoY" in eventos and "Inflation Rate YoY" in eventos

    def test_y_ahora_caben_los_datos_de_TODA_la_semana(self, caja):
        """Lo que se ganaba al dejar de repetir: el resto de la quincena."""
        eventos = [f["evento"] for f in caja["publicados"]]
        for esperado in ("Nonfarm Payrolls", "Unemployment Rate",
                         "ISM Manufacturing PMI"):
            assert esperado in eventos, (
                f"{esperado} sigue fuera de la caja: {eventos}")


class TestLaNotaNoSePuedeCORTAR:
    """Por qué la nota moría en el quinto dato, en la palabra «Salió».

    El proveedor principal es Gemini 2.5 Flash, que es un modelo PENSANTE: su
    `max_output_tokens` cuenta los tokens de pensamiento MÁS los de respuesta.
    Con el presupuesto compartido y una nota de once secciones, el modelo se
    gastaba casi todo razonando y el texto salía cortado a media frase — dentro
    de la PRIMERA sección, así que las diez de análisis no se escribían nunca.

    Desde el panel parecía que el modelo no sabía contestar. Lo que pasaba es
    que no le dejábamos sitio para hacerlo.
    """

    def test_el_pensamiento_de_gemini_va_a_CERO(self):
        """Estas notas no razonan: el motor ya hizo la matemática y las
        clasificaciones, y el trabajo del modelo es REDACTAR lo que se le da
        resuelto. Con el pensamiento a cero, el presupuesto entero va al texto.
        """
        import inspect

        import vertex_api as V

        fuente = inspect.getsource(V._texto_llm)
        assert "ThinkingConfig(thinking_budget=0)" in fuente, (
            "sin esto, el presupuesto se lo come el pensamiento y la nota sale "
            "cortada a media frase")

    def test_y_un_SDK_viejo_no_tumba_la_llamada(self):
        """`ThinkingConfig` no existe en todas las versiones del SDK. Que falte
        tiene que costar una nota más corta, no ninguna nota."""
        import inspect

        import vertex_api as V

        fuente = inspect.getsource(V._texto_llm)
        i = fuente.index("ThinkingConfig")
        assert "except Exception" in fuente[i:i + 400], (
            "la configuración del pensamiento no está protegida")

    @pytest.mark.parametrize("ruta,minimo", [
        ("api_macro_lectura", 6000),
        ("api_sectores_lectura", 5000),
    ])
    def test_cada_nota_larga_tiene_margen_de_sobra(self, ruta, minimo):
        """Once y trece secciones, y la primera de cada una enumera datos con
        sus cifras. El margen tiene que dar para llegar al final."""
        import inspect
        import re

        import vertex_api as V

        fuente = inspect.getsource(getattr(V, ruta))
        topes = [int(x) for x in re.findall(r"max_tokens=(\d+)", fuente)]
        assert topes and max(topes) >= minimo, (
            f"{ruta} escribe con {topes}: se corta antes de acabar")



class TestCadaDatoSeExplicaSOLO:
    """«Cada dato tiene su flechita y cuando presiono para que se amplíe ahí se
    genera lo que es explícame los datos y ahí sale la explicación solo de ese
    dato.» — Kevin, 23/08/2026.

    Antes había UN botón que escribía una nota con los ocho datos dentro.
    Obligaba a buscar en un muro de texto el que interesaba, y mezclaba en el
    mismo párrafo un IPC en línea con unas nóminas desplomadas: dos cosas que
    no se leen juntas.
    """

    @pytest.mark.parametrize("seccion", [
        "Qué salió", "Qué mide y por qué importa", "El dólar",
        "La economía de EE.UU.", "La Fed", "Los tipos y los bonos",
        "La bolsa", "Sector por sector", "Qué vigilar de este mismo dato",
    ])
    def test_el_prompt_de_UN_dato_pide_cada_pieza(self, seccion):
        import vertex_api as V

        assert seccion in V._MACRO_DATO_SYSTEM

    @pytest.mark.parametrize("fuera", ["para la gente", "resto del mundo"])
    def test_y_no_pide_lo_que_Kevin_quito(self, fuera):
        import vertex_api as V

        assert fuera not in V._MACRO_DATO_SYSTEM

    def test_pide_palabras_llanas_y_lo_dice_con_un_ejemplo(self):
        """«Palabras simples que cualquier persona pueda entender y directo.»

        Se exige el EJEMPLO y no solo la orden: «escribe sencillo» lo cumple
        cualquier texto en su propia opinión. Un ejemplo de qué NO decir y qué
        decir en su lugar es lo que de verdad cambia la salida.
        """
        import vertex_api as V

        assert "se comprimen los múltiplos" in V._MACRO_DATO_SYSTEM

    def test_avisa_de_que_mas_alto_no_es_mejor(self):
        import vertex_api as V

        assert "MÁS ALTO NO ES «MEJOR»" in V._MACRO_DATO_SYSTEM

    def test_el_bloque_de_datos_lleva_la_direccion_resuelta(self):
        """Dárselo hecho es más barato que corregirlo con una regla."""
        import vertex_api as V

        alto = V._macro_dato_datos({"evento": "Nonfarm Payrolls",
                                    "fecha": "2026-08-07 12:30", "salio": 280.0,
                                    "esperado": 180.0, "anterior": 175.0,
                                    "mejor": "alto", "bueno": True,
                                    "sorpresa": 100.0})
        assert "MÁS ALTO ES MEJOR" in alto and "Sorprendió BIEN." in alto
        bajo = V._macro_dato_datos({"evento": "Unemployment Rate",
                                    "fecha": "2026-08-07 12:30", "salio": 4.4,
                                    "esperado": 4.1, "anterior": 4.2,
                                    "mejor": "bajo", "bueno": False,
                                    "sorpresa": 0.3})
        assert "MÁS BAJO ES MEJOR" in bajo and "Sorprendió MAL." in bajo

    def test_una_DECISION_de_tipos_no_se_juzga(self):
        d = None
        import vertex_api as V

        d = V._macro_dato_datos({"evento": "Fed Interest Rate Decision",
                                 "fecha": "2026-08-06 18:00", "salio": 5.5,
                                 "esperado": 5.5, "anterior": 5.25,
                                 "mejor": None, "bueno": None, "sorpresa": 0.0})
        assert "no digas que es buena ni mala noticia" in d

    def test_un_dato_EN_LINEA_se_dice_asi(self):
        import vertex_api as V

        d = V._macro_dato_datos({"evento": "CPI YoY", "fecha": "2026-08-12 12:30",
                                 "salio": 3.4, "esperado": 3.4, "anterior": 3.5,
                                 "mejor": "bajo", "bueno": None, "sorpresa": 0.0})
        assert "EN LÍNEA" in d and "ya estaba en el precio" in d


class TestElPanelDespliegaDatoAdato:
    @pytest.fixture(scope="class")
    def html(self):
        return (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")

    def test_cada_fila_lleva_su_flecha(self, html):
        assert "vx-flecha" in html and "vxMacroDespliega(" in html

    def test_la_flecha_dice_si_esta_abierta(self, html):
        """`aria-expanded` no es decoración: es lo único que le dice a un lector
        de pantalla que esa fila se puede abrir y si lo está."""
        assert 'aria-expanded="false"' in html and "aria-expanded', 'true'" in html

    def test_se_pide_la_explicacion_de_UN_evento(self, html):
        """Se mide el HECHO —que se pide un evento concreto—, no la forma
        exacta del literal: el `?` vive dentro de la consulta para que el
        guardián del idioma no lea la ruta como una frase sin traducir, y atar
        el caso a la cadena entera lo rompía en cuanto se movía esa coma."""
        i = html.index("async function vxMacroDespliega")
        trozo = html[i:i + 3000]
        assert "'?evento=' + encodeURIComponent(f.evento)" in trozo
        assert "/api/dashboard/macro/lectura${q}" in trozo

    def test_no_se_vuelve_a_pedir_lo_ya_escrito(self, html):
        """Cerrar y volver a abrir no puede costar otra llamada al modelo."""
        assert "dataset.escrita" in html

    def test_el_boton_GLOBAL_ya_no_esta(self, html):
        """Era el que escribía los ocho datos de golpe."""
        assert "cargaMacroLectura()" not in html.replace(
            "async function cargaMacroLectura(", "")

    def test_la_explicacion_pasa_por_el_SITIO_UNICO_que_escapa(self, html):
        """No arma el HTML por su cuenta: usa `vxProsaDelModelo`.

        Este caso empezó comprobando el escapado aquí dentro, y esa era la
        avería: había DOS sitios convirtiendo prosa del modelo en HTML —el de
        la lectura del mercado y este—, los dos correctos hoy y los dos por
        mantener mañana. Se unificaron, así que aquí lo que hay que exigir es
        que se pase por el único, y que nadie vuelva a escapar por su cuenta.

        Que ese sitio único escape ANTES de marcar las negritas lo mide
        `test_el_panel_escapa_ANTES_de_marcar_las_negritas`.
        """
        i = html.index("async function vxMacroDespliega")
        trozo = html[i:i + 3000]
        assert "vxProsaDelModelo(d.texto" in trozo, (
            "la explicación por dato arma el HTML por su cuenta: es la segunda "
            "copia del mismo paso, y una de las dos acabará sin escapar")
        assert "_vcEsc(d.texto)" not in trozo


# ══════════════════════════════════════════════════════════════════════════
PANEL = ROOT / "vertex_fund_os_platform.html"


class TestLosOchoSonLosULTIMOSDeAltoImpacto:
    """La caja se llama «Ya salieron» y ordenaba por IMPORTANCIA.

    Capturado por Kevin: un IPC de hace doce días ocupando sitio mientras lo
    publicado ayer no aparecía. Los dos criterios que pidió, en este orden:
    **los últimos ocho**, y **sólo de alto impacto**.
    """

    def test_manda_la_fecha_dentro_del_alto_impacto(self, monkeypatch):
        """Medido sobre la caja, no sobre el texto de la función.

        Empezó siendo tres `assert ... in inspect.getsource(...)`, y se rompió
        a la primera vez que la clave de ordenación creció —por añadirle el
        desempate del corte, que no cambia nada de lo que este caso vigila—.
        Un guardián que se cae cuando el comportamiento NO cambia no vigila
        nada: sólo obliga a reescribirlo. Lo que hay que exigir es el hecho,
        y el hecho es que dentro del alto impacto el más reciente va primero.
        """
        caja = _caja_macro(monkeypatch, [
            ("CPI YoY", 12, "3.1%", "3.0%", "2.9%"),          # importante y VIEJO
            ("Unemployment Rate", 1, "4.4%", "4.1%", "4.2%"),  # importante y de AYER
        ])
        eventos = [f["evento"] for f in caja["publicados"]]
        assert eventos[0] == "Unemployment Rate", (
            f"un dato de hace doce días le gana al de ayer: {eventos}")

    def test_el_nivel_2_NO_SALE_aunque_SOBREN_huecos(self, monkeypatch):
        """Kevin: «solo noticias de alto impacto. Si son mediano impacto o bajo
        no me interesan y no quiero que salgan en el panel.»

        Antes el nivel 2 rellenaba: con un solo dato de alto impacto, la caja
        salía con ocho filas. Este caso medía la línea del código —
        `inspect.getsource`—, que es medir la prosa y no el resultado: se
        habría puesto en rojo por reordenar una línea sin cambiar nada, y en
        verde si el relleno se colara por otro sitio. Ahora mide la caja.
        """
        caja = _caja_macro(monkeypatch, [
            ("Core Inflation Rate YoY", 3, "2.9%", "3.0%", "3.1%"),
            ("Chicago PMI", 1, "48.1", "47.0", "46.5"),
            ("New Home Sales", 1, "670K", "660K", "655K"),
            ("Continuing Jobless Claims", 1, "1.92M", "1.90M", "1.89M"),
            ("Industrial Production MoM", 2, "0.4%", "0.2%", "0.1%"),
        ])
        nombres = [f["evento"] for f in caja["publicados"]]
        assert nombres == ["Core Inflation Rate YoY"], (
            f"sobraban siete huecos y se rellenaron igual: {nombres}")

    def test_la_ventana_atras_da_para_LLENAR_la_caja(self):
        """En EE.UU. salen dos o tres de alto impacto por semana. Con la caja
        en doce filas, la ventana tiene que dar para doce: si no, se pide una
        caja que el calendario no puede llenar."""
        import vertex_api as V

        assert V._MACRO_DIAS_ATRAS >= 35
        # Dos y medio por semana es el ritmo flojo; que la ventana dé para la
        # caja con ese ritmo, no con el bueno.
        assert V._MACRO_DIAS_ATRAS / 7 * 2.5 >= V._MACRO_FILAS

    def test_el_macro_se_refresca_en_MINUTOS_no_en_un_dia(self):
        """«Si sale uno en 1 minuto y ya salió el reporte, que salga.»"""
        import vertex_api as V

        assert V._CALENDARIO_TTL_MACRO <= 300
        assert V._CALENDARIO_TTL >= 3600     # los resultados, no

    def test_una_caja_LLENA_ya_no_se_lee_como_vacia(self):
        """`_calendario_ttl` miraba `macro["filas"]`, que el camino normal deja
        vacía: `llenas` era SIEMPRE falso. Funcionaba de casualidad."""
        import vertex_api as V

        assert V._caja_tiene_datos({"publicados": [{"a": 1}]}) is True
        assert V._caja_tiene_datos({"filas": [{"a": 1}]}) is True
        assert V._caja_tiene_datos({"dias": [{"a": 1}]}) is True
        assert V._caja_tiene_datos({"publicados": [], "proximos": []}) is False
        assert V._caja_tiene_datos(None) is False
        lleno = {"resultados": {"filas": [1]}, "macro": {"publicados": [1]}}
        assert V._calendario_ttl(lleno) == V._CALENDARIO_TTL_MACRO

    def test_refrescar_el_macro_no_vuelve_a_pedir_los_RESULTADOS(self, monkeypatch):
        """Si no, cada cinco minutos se rebajaban catorce días de empresas para
        obtener exactamente lo mismo."""
        from datetime import datetime, timezone

        import vertex_api as V

        veces = {"n": 0}
        monkeypatch.setattr(
            V, "_resultados_calcula",
            lambda: (veces.__setitem__("n", veces["n"] + 1), {"filas": [1]})[1])
        monkeypatch.setattr(V, "_macro_calcula", lambda: {"publicados": [1]})
        previo = {"resultados": {"filas": [1]},
                  "generado_resultados": datetime.now(timezone.utc).isoformat()}
        V._calendario_calcula(previo)
        assert veces["n"] == 0, "se recalcularon los resultados estando frescos"

    def test_unos_resultados_VIEJOS_si_se_recalculan(self):
        """La contraparte: si nunca se recalcularan, la caja se congelaría."""
        import inspect

        import vertex_api as V

        fuente = inspect.getsource(V._calendario_calcula)
        assert "< _CALENDARIO_TTL" in fuente
        assert "resultados = _resultados_calcula()" in fuente


class TestLaHoraDePublicacion:
    """«En los que aún no han salido, me gustaría que tuvieran la hora.»"""

    def test_el_sello_de_FMP_se_convierte_a_ET(self):
        """FMP sella en UTC y el panel se abre desde cualquier huso: la
        referencia de un mercado es la hora de SU plaza. Los dos casos son el
        IPC real, que sale a las 8:30 ET tanto en verano como en invierno."""
        import vertex_api as V

        assert V._macro_hora_et("2026-08-26 12:30") == "08:30"   # verano, UTC-4
        assert V._macro_hora_et("2026-01-14 13:30") == "08:30"   # invierno, UTC-5

    def test_sin_hora_no_se_INVENTA_una(self):
        import vertex_api as V

        assert V._macro_hora_et("2026-08-26") is None
        assert V._macro_hora_et("") is None
        assert V._macro_hora_et(None) is None

    def test_cada_fila_la_lleva(self):
        import inspect

        import vertex_api as V

        assert '"hora_et": _macro_hora_et(cuando)' in inspect.getsource(V._macro_calcula)

    def test_el_panel_la_pinta_y_dice_el_huso(self):
        """Una hora sin huso al lado se lee mal desde otro país, y encima no es
        falsable: si el proveedor cambiara de zona, nadie lo vería."""
        html = PANEL.read_text(encoding="utf-8")
        assert "f.hora_et" in html
        assert "ET</span>" in html

    def test_un_dato_RETRASADO_no_lleva_hora(self):
        """La que tenía ya pasó: repetirla sería una cita que no existe."""
        html = PANEL.read_text(encoding="utf-8")
        assert "(!f.retrasado && f.hora_et)" in html


class TestLosQueReportanSALENDentroDeSuSector:
    """La caja de resultados etiqueta cada ticker con su sector REAL —el que da
    FMP— y al entrar en el sector la empresa no estaba. Medido sobre la captura
    de Kevin: 32 de las 48 que salían allí no aparecen en ninguna industria.

    No es un fallo de la etiqueta ni de la lista: `VX_ACCIONES` son las mayores
    posiciones de unos cuarenta ETF de industria, no el sector entero. Faltaba
    el puente."""

    @pytest.fixture(scope="class")
    def html(self):
        return PANEL.read_text(encoding="utf-8")

    def test_existe_el_puente_y_se_pinta_al_abrir_el_sector(self, html):
        assert "function vxReportanDelSectorHTML(sector)" in html
        assert "+ vxReportanDelSectorHTML(tk);" in html

    def test_filtra_por_el_sector_que_se_esta_mirando(self, html):
        i = html.index("function vxReportanDelSectorHTML(sector)")
        assert "filter(f => f.sector === sector)" in html[i:i + 900]

    def test_marca_las_que_NO_estan_escritas_abajo(self, html):
        """Si salieran mezcladas, la pregunta de Kevin —«¿por qué no está?»—
        seguiría sin respuesta en pantalla."""
        i = html.index("function vxReportanDelSectorHTML(sector)")
        trozo = html[i:i + 3000]
        assert "escritas.has(f.ticker)" in trozo
        assert "no el sector entero" in trozo

    def test_sin_resultados_no_pinta_una_caja_vacia(self, html):
        i = html.index("function vxReportanDelSectorHTML(sector)")
        assert "if (!filas.length) return '';" in html[i:i + 700]

    def test_el_ticker_lleva_al_analizador_SIN_lanzar_el_analisis(self, html):
        """Un análisis son seis sub-agentes y varios minutos: dispararlo con un
        clic en una etiqueta convierte un vistazo en una espera que nadie
        pidió."""
        assert "function abreTicker(ticker)" in html
        i = html.index("function abreTicker(ticker)")
        trozo = html[i:i + 900]
        assert "fetchQuote(tk)" in trozo
        for lanza in ("runAnalysis", "analizar(", "startAnalysis"):
            assert lanza not in trozo, lanza

    def test_TODO_switchView_apunta_a_una_vista_que_EXISTE(self, html):
        """Escribí `analyzeView` de memoria y ese id no existe: `switchView`
        habría escondido todas las vistas sin mostrar ninguna — un botón que
        deja la pantalla en blanco. Esto ata cada llamada al marcado real."""
        import re

        reales = set(re.findall(r'id="([a-zA-Z]+View)"', html))
        assert len(reales) >= 8, sorted(reales)
        pedidas = set(re.findall(r"switchView\('([a-zA-Z]+)'", html))
        assert pedidas <= reales, sorted(pedidas - reales)


# ══════════════════════════════════════════════════════════════════════════
class TestElNombreYElCorteNoSeConfunden:
    """Kevin, el 27/08/2026, sobre la caja de lo macro:

        «no me estas dando todas las noticias de alto impacto y creo que te
         confundes de nombre y si es MoM o YoY, recuerda que son diferentes.»

    Eran dos averías, y la primera explicaba la primera queja.

    **Los nombres.** FMP publica la MISMA serie con dos rótulos. En la captura
    salían a la vez «Core Inflation Rate YoY» 2,50 / 2,50 / 2,60 y «Core CPI
    YoY» 2,50 / 2,50 / 2,60 —los tres números idénticos—, y también «Inflation
    Rate YoY» 3,40 junto a «CPI YoY» 3,40. **Cuatro de los ocho huecos eran dos
    datos repetidos**: por eso faltaban noticias de alto impacto, la mitad de la
    caja estaba gastada en decir dos veces lo mismo.

    **El corte.** Al revés: «CPI MoM» y «CPI YoY» se trataban como el mismo
    dato y sólo salía uno. Un IPC mensual del +0,3% y uno interanual del +3,4%
    son dos cifras, con dos consensos, que mueven el precio por separado.
    """

    @pytest.fixture(scope="function")
    def caja(self, monkeypatch):
        return _caja_macro(monkeypatch, [
            # Los dos rótulos del mismo dato, con los mismos números.
            ("Core Inflation Rate YoY", 3, "2.5%", "2.5%", "2.6%"),
            ("Core CPI YoY",            3, "2.5%", "2.5%", "2.6%"),
            ("Inflation Rate YoY",      3, "3.4%", "3.4%", "3.5%"),
            ("CPI YoY",                 3, "3.4%", "3.4%", "3.5%"),
            # Y el mensual del mismo comunicado, que es OTRO dato.
            ("CPI MoM",                 3, "0.3%", "0.2%", "0.1%"),
            ("Core CPI MoM",            3, "0.2%", "0.2%", "0.3%"),
            # El informe de empleo: titular, sub-agregado y salarios.
            ("Non Farm Payrolls",           6, "-23K", "80K", "20K"),
            ("Nonfarm Payrolls Private",    6, "30K",  "78K", "30K"),
            ("Average Hourly Earnings MoM", 6, "0.3%", "0.3%", "0.2%"),
            ("Unemployment Rate",           6, "4.4%", "4.3%", "4.2%"),
        ])

    def _eventos(self, caja):
        return [f["evento"] for f in caja["publicados"]]

    def _ipc_general(self, caja):
        """Las filas del IPC general: ni el subyacente, ni ningún otro dato.

        Filtrar por «MoM» a secas era un colador —«Average Hourly Earnings
        MoM» también lo lleva— y hacía pasar el caso sin el arreglo. Se mide
        el IPC, que es de lo que iba la queja.
        """
        return [f["evento"] for f in caja["publicados"]
                if ("CPI" in f["evento"] or "Inflation Rate" in f["evento"])
                and "Core" not in f["evento"]]

    def test_los_dos_rotulos_del_MISMO_dato_ocupan_UN_hueco(self, caja):
        """«Core Inflation Rate YoY» y «Core CPI YoY» son la misma serie."""
        nucleo = [e for e in self._eventos(caja)
                  if "Core" in e and "YoY" in e]
        assert len(nucleo) == 1, (
            f"el mismo dato ocupa {len(nucleo)} huecos de los ocho: {nucleo}")

    def test_y_lo_mismo_con_el_general(self, caja):
        general = [e for e in self._eventos(caja)
                   if "YoY" in e and "Core" not in e]
        assert len(general) == 1, (
            f"el mismo dato ocupa {len(general)} huecos de los ocho: {general}")

    def test_pero_el_NUCLEO_no_se_funde_con_el_GENERAL(self, caja):
        """Colapsar sinónimos no puede llevarse por delante el matiz: el IPC
        subyacente y el general son dos datos distintos y los dos se miran."""
        eventos = self._eventos(caja)
        assert any("Core" in e and "YoY" in e for e in eventos)
        assert any("Core" not in e and "YoY" in e for e in eventos)

    def test_el_MoM_y_el_YoY_salen_LOS_DOS(self, caja):
        """«Recuerda que son diferentes.» Antes sólo salía el interanual."""
        ipc = self._ipc_general(caja)
        assert len(ipc) == 2, f"el IPC general ocupa {len(ipc)} filas: {ipc}"
        assert any("YoY" in e for e in ipc), ipc
        assert any("MoM" in e for e in ipc), f"el IPC mensual no aparece: {ipc}"

    def test_el_titular_va_DELANTE_del_otro_corte(self, caja):
        """Los dos salen, pero la inflación se cita a doce meses: el interanual
        arriba y el mensual debajo. Sin esto quedaban en el orden en que los
        mandara FMP, que es ninguno."""
        ipc = self._ipc_general(caja)
        assert "YoY" in ipc[0] and "MoM" in ipc[-1], ipc

    def test_el_sub_agregado_no_le_quita_el_hueco_al_titular(self, caja):
        """«Nonfarm Payrolls Private» (30K) y «Non Farm Payrolls» (−23K) son el
        mismo comunicado. La cifra que se cita es la segunda."""
        nominas = [f for f in caja["publicados"] if "ayroll" in f["evento"]]
        assert len(nominas) == 1, f"dos huecos para un comunicado: {nominas}"
        # «-23K» llega expandido a miles: lo que se mide aquí es CUÁL de las
        # dos cifras se quedó con el hueco, no cómo se escribe.
        assert nominas[0]["salio"] == -23_000.0, (
            f"tituló el sub-agregado: {nominas[0]['evento']}")

    def test_los_salarios_del_informe_de_empleo_son_ALTO_impacto(self):
        """Salen el mismo minuto que las nóminas y son la mitad de la lectura.
        Estaban en nivel 2 —de relleno—, así que el día de empleo la caja
        enseñaba dos tercios del comunicado."""
        import vertex_api as V

        assert V._macro_ficha("Average Hourly Earnings MoM")[0] == 1

    def test_en_la_caja_no_hay_DOS_FILAS_CON_LOS_MISMOS_NUMEROS(self, caja):
        """Lo que Kevin vio con sus ojos, medido tal cual.

        Contar filas no sirve: sin el arreglo también salían ocho. Lo que
        delataba la avería es que dos de ellas traían los MISMOS tres números
        —2,50 / 2,50 / 2,60— porque eran el mismo dato con dos rótulos.
        """
        vistos: dict[tuple, str] = {}
        for f in caja["publicados"]:
            huella = (f["salio"], f["esperado"], f.get("previo"))
            if huella[0] is None:
                continue
            gemelo = vistos.get(huella)
            assert gemelo is None, (
                f"«{f['evento']}» y «{gemelo}» traen los mismos números "
                f"{huella}: son el mismo dato ocupando dos de los ocho huecos")
            vistos[huella] = f["evento"]

    def test_del_PIB_el_titular_es_el_TRIMESTRAL_no_el_interanual(self):
        """En EE.UU. el PIB se cita por su trimestre anualizado: «la economía
        creció un 3,0%». La preferencia era un `frozenset` de familias
        «interanuales» y el PIB caía del lado equivocado."""
        import vertex_api as V

        assert V._macro_orden_corte({"evento": "GDP Growth Rate QoQ"}) == 0
        assert V._macro_orden_corte({"evento": "GDP Growth Rate YoY"}) == 1

    def test_el_corte_forma_parte_de_la_identidad_de_la_fila(self):
        """La regla en una línea, por si algún día se vuelve a agrupar por
        familia a secas: la fila es indicador MÁS corte."""
        import vertex_api as V

        assert V._macro_grupo("CPI MoM") != V._macro_grupo("CPI YoY")
        assert V._macro_grupo("CPI YoY") == V._macro_grupo("Inflation Rate YoY")
        assert V._macro_grupo("CPI YoY") != V._macro_grupo("Core CPI YoY")

    def test_todo_sinonimo_apunta_a_una_clave_QUE_EXISTE(self):
        """`_macro_ficha` indexa `_MACRO_TABLA` con lo que devuelve
        `_macro_familia`, y eso ahora es el nombre canónico. Un sinónimo que
        apuntara a un nombre que no está en la tabla reventaría con `KeyError`
        la primera vez que FMP publicara ese dato — en producción, no aquí."""
        import vertex_api as V

        for rotulo, canonico in V._MACRO_SINONIMOS.items():
            assert canonico in V._MACRO_TABLA, (
                f"«{rotulo}» se colapsa a «{canonico}», que no está en la tabla")
            assert rotulo in V._MACRO_TABLA, (
                f"«{rotulo}» no está en la tabla, así que _macro_familia nunca "
                f"lo verá y el sinónimo no se aplica jamás")

    def test_toda_preferencia_de_corte_apunta_a_una_familia_CANONICA(self):
        """El mapa se consulta con el nombre ya canonizado. Poner ahí
        «inflation rate» dejaría una entrada muerta que nadie volvería a
        mirar, y la preferencia del IPC dejaría de aplicarse en silencio."""
        import vertex_api as V

        for fam in V._MACRO_CORTE_DEL_MERCADO:
            assert fam in V._MACRO_TABLA, f"«{fam}» no está en la tabla"
            assert fam not in V._MACRO_SINONIMOS, (
                f"«{fam}» es un rótulo que se colapsa a "
                f"«{V._MACRO_SINONIMOS[fam]}»: esta entrada nunca se consulta")

    def test_la_nota_del_modelo_SABE_si_es_mensual_o_interanual(self):
        """El nombre lo dice —«CPI MoM»— pero el modelo escribe la nota leyendo
        el nombre, y un +0,3% mensual explicado como si fuera interanual es una
        nota que miente con confianza. Se le dice con todas las letras."""
        import vertex_api as V

        base = {"fecha": "2026-08-24", "salio": 0.3, "esperado": 0.2,
                "anterior": 0.1, "mejor": "bajo", "bueno": False}
        mensual = V._macro_dato_datos({**base, "evento": "CPI MoM"})
        anual = V._macro_dato_datos({**base, "evento": "CPI YoY"})
        assert "MENSUAL" in mensual and "INTERANUAL" not in mensual
        assert "INTERANUAL" in anual
        # Y lo que no lleva corte no se inventa uno.
        suelto = V._macro_dato_datos({**base, "evento": "Unemployment Rate"})
        assert "MENSUAL" not in suelto and "INTERANUAL" not in suelto


# ══════════════════════════════════════════════════════════════════════════
#: La lista que Kevin mandó el 27/08/2026, literal, con sus nombres tal cual
#: los escribe FMP. Es el contrato: **todas** son de alto impacto.
#:
#:     «las noticias de alto impacto te las dare porque las estas haciendo
#:      mal. son estas: …»
_ALTO_IMPACTO_DE_KEVIN = (
    "Building Permits Prel",
    "Housing Starts",
    "FOMC Minutes",
    "Initial Jobless Claims",
    "CB Consumer Confidence",
    "Personal Income",
    "Personal Spending MoM",
    "Durable Goods Orders",
    "Core PCE Price Index MoM",
    "GDP Growth Rate QoQ 2nd Est",
    "Core Personal Consumption Expenditure",
    "UoM Consumer Sentiment Index",
    "Non Farm Payrolls Annual Revision Prel",
    "Fed Chair Speech",
    "JOLTs Job Openings",
    "ISM Manufacturing PMI",
    "ADP National Employment Report",
    "ISM Services PMI",
    "Average Hourly Earnings MoM",
    "Nonfarm Payrolls",
    "Unemployment Rate",
    "Existing Home Sales",
    "Core Inflation Rate MoM",
    "Core Inflation Rate YoY",
    "Inflation Rate MoM",
    "Core PPI",
    "PPI MoM",
    "Retail Sales Ex Autos MoM",
    "Retail Sales MoM",
    "University of Michigan Consumer Sentiment Index",
    "Treasury Refunding Financing Estimates",
    "Treasury Refunding Announcement",
)


class TestLaListaDeAltoImpactoEsLaDeKevin:
    """De las 32 que mandó, 17 estaban mal.

    Once en nivel 2 —que es sólo relleno y nunca desplaza a un nivel 1— y
    **seis no entraban siquiera en la caja**, porque su nombre no casaba con
    ninguna clave de la tabla: `Personal Income`, `Personal Spending`,
    `Core Personal Consumption Expenditure`, `UoM Consumer Sentiment Index`,
    `Fed Chair Speech` y las dos del Tesoro.

    Dos de esas seis eran errores de clave, no de criterio: la tabla decía
    `adp employment`, y el evento se llama «ADP National Employment Report»
    —no contiene esa cadena—; y decía `michigan consumer sentiment`, que
    «UoM Consumer Sentiment Index» tampoco contiene. El dato existía, la
    llave no abría.
    """

    @pytest.mark.parametrize("evento", _ALTO_IMPACTO_DE_KEVIN)
    def test_esta_en_la_tabla_y_es_de_ALTO_impacto(self, evento):
        import vertex_api as V

        ficha = V._macro_ficha(evento)
        assert ficha is not None, (
            f"«{evento}» no casa con ninguna clave: no entra en la caja")
        assert ficha[0] == 1, (
            f"«{evento}» entra como nivel {ficha[0]}: el nivel 2 es relleno y "
            f"nunca desplaza a un nivel 1, así que quedaría fuera de la caja")

    @pytest.mark.parametrize("evento", _ALTO_IMPACTO_DE_KEVIN)
    def test_y_el_filtro_de_la_caja_lo_deja_pasar(self, evento):
        """`_macro_ficha` y `_es_evento_macro` tienen que decir lo mismo: es el
        segundo el que decide quién entra, y son dos sitios."""
        import vertex_api as V

        assert V._es_evento_macro(evento) is True

    @pytest.mark.parametrize("a,b", [
        # Son DOS comunicados distintos, no dos variantes del mismo.
        ("Nonfarm Payrolls", "Non Farm Payrolls Annual Revision Prel"),
        # El «sin automóviles» va marcado aparte en los calendarios, y Kevin
        # lo nombró aparte: es su propia familia, no un sub-agregado.
        ("Retail Sales MoM", "Retail Sales Ex Autos MoM"),
        ("Treasury Refunding Announcement", "Treasury Refunding Financing Estimates"),
    ])
    def test_los_que_son_DOS_datos_ocupan_dos_filas(self, a, b):
        import vertex_api as V

        assert V._macro_grupo(a) != V._macro_grupo(b), (
            f"«{a}» y «{b}» caen en la misma fila: uno de los dos no se vería")

    @pytest.mark.parametrize("evento", [
        "Retail Sales Excluding Gas and Autos MoM",
        "Retail Sales Control Group",
        "Nonfarm Payrolls Private",
    ])
    def test_pero_los_sub_agregados_siguen_sin_titular(self, evento):
        """Quitar el « ex » de la regla de sub-agregados no puede abrirle la
        puerta a los recortes del comunicado que Kevin NO nombró: en su
        captura del 22/08 dos de los cinco huecos eran «Excluyendo…»."""
        import vertex_api as V

        titular = {"evento": evento.replace(" Excluding Gas and Autos", "")
                             .replace(" Control Group", "")
                             .replace(" Private", ""), "fecha": "2026-08-24"}
        assert V._macro_titular({"evento": evento, "fecha": "2026-08-24"}) \
            > V._macro_titular(titular), (
            f"«{evento}» le ganaría el hueco a su titular")

    def test_lo_que_el_PROVEEDOR_marca_de_alto_impacto_entra_aunque_no_este(self):
        """La red de abajo: una tabla escrita a mano nunca está completa.

        FMP manda un campo `impact` que ya se guardaba en la fila y que nadie
        miraba. Un evento que no casa con ninguna clave pero que el proveedor
        marca `High` entra igual — sin dirección, porque no sabemos leerlo.
        """
        import vertex_api as V

        assert V._macro_alto_del_proveedor({"impact": "High"}) is True
        assert V._macro_alto_del_proveedor({"impact": "high"}) is True
        assert V._macro_alto_del_proveedor({"impact": "Medium"}) is False
        assert V._macro_alto_del_proveedor({"impact": "Low"}) is False
        # Y si el campo no viene, no cambia nada: es lo que hacía antes.
        assert V._macro_alto_del_proveedor({}) is False
        assert V._macro_alto_del_proveedor({"impact": None}) is False

    def test_la_red_de_abajo_NO_pinta_de_colores_lo_que_no_conoce(self, monkeypatch):
        """Entra en la caja, pero sin `mejor`: inventarle un «más alto es
        mejor» a un dato que no hemos leído sería pintar de verde algo que no
        entendemos. Igual que una decisión de la Fed."""
        import vertex_api as V

        hoy = date.today()
        crudo = [{"country": "US", "event": "Wholesale Inventories MoM",
                  "date": (hoy - timedelta(days=1)).isoformat() + " 12:30:00",
                  "actual": "0.3%", "estimate": "0.1%", "previous": "0.2%",
                  "impact": "High"}]

        class _R:
            status_code = 200

            def json(self):
                return crudo

        monkeypatch.setenv("FMP_API_KEY", "x" * 20)
        monkeypatch.setattr(V.requests, "get", lambda *a, **k: _R())
        caja = V._macro_calcula()
        filas = [f for f in caja["publicados"] if "Wholesale" in f["evento"]]
        assert len(filas) == 1, "el proveedor lo marcó de alto impacto y no entró"
        assert filas[0]["nivel"] == 1
        assert filas[0]["mejor"] is None, "no sabemos su dirección: sin color"

    def test_y_lo_que_NO_es_de_alto_impacto_ni_esta_en_la_tabla_sigue_fuera(
            self, monkeypatch):
        """El filtro es lo que mantiene la caja limpia. Sin él, ocho ruidos
        semanales empujarían fuera al IPC del martes."""
        import vertex_api as V

        hoy = date.today()
        crudo = [{"country": "US", "event": "Redbook YoY",
                  "date": (hoy - timedelta(days=1)).isoformat() + " 12:30:00",
                  "actual": "5.1%", "estimate": "5.0%", "previous": "4.9%",
                  "impact": "Low"}]

        class _R:
            status_code = 200

            def json(self):
                return crudo

        monkeypatch.setenv("FMP_API_KEY", "x" * 20)
        monkeypatch.setattr(V.requests, "get", lambda *a, **k: _R())
        caja = V._macro_calcula()
        assert not [f for f in caja["publicados"] if "Redbook" in f["evento"]]

    @pytest.mark.parametrize("crudo,esperado", [
        ("4.1M", 4_100_000.0),
        ("275K", 275_000.0),
        ("1.2B", 1_200_000_000.0),
        ("-23K", -23_000.0),
        ("2.9%", 2.9),
        ("1,250", 1250.0),
    ])
    def test_las_unidades_no_dejan_basura_de_coma_flotante(self, crudo, esperado):
        """`4.1 * 1e6` da 4099999.9999999995, y eso salía en la caja debajo de
        «Existing Home Sales». No es un error de cálculo, es cómo se guardan
        los decimales en binario — pero se lee como un dato roto."""
        import vertex_api as V

        assert V._num(crudo) == esperado


#: Publicaciones de EE. UU. que la lista de Kevin no nombra y que los
#: calendarios del oficio marcan igual de fuerte que las que sí nombra.
#:
#: Él pidió «investiga y ponlos todos». **No pude abrir ningún calendario**:
#: forexfactory, investing, fxstreet, tradingeconomics, babypips y hasta
#: bls.gov los rechaza el proxy de salida con un 403 de política. Esto sale
#: del calendario de publicaciones oficiales (BLS, BEA, Census, Fed), no de
#: una fuente que se haya podido leer. Por eso existe la red de abajo.
_ALTO_IMPACTO_QUE_KEVIN_NO_NOMBRO = (
    # El coste laboral trimestral del BLS: la medida de salarios que la Fed
    # cita en cada conferencia de prensa.
    "Employment Cost Index QoQ",
)

#: Lo que se miró y **NO** es de alto impacto. No sale en el panel:
#:
#: > «solo noticias de alto impacto. Si son mediano impacto o bajo no me
#: > interesan y no quiero que salgan en el panel.» — Kevin, 27/08/2026.
#:
#: Estar en la tabla en nivel 2 no es estar a medias: es estar fuera. Y sirve
#: para algo más que documentar — blinda contra la red de abajo, que si no
#: promovería a alto impacto cualquier cosa que FMP marque `High`.
_EXCLUIDOS = (
    "Philadelphia Fed Manufacturing Index",
    "Philly Fed Business Conditions",
    "NY Empire State Manufacturing Index",
    "Chicago PMI",
    "Factory Orders MoM",
    "Michigan Consumer Expectations Prel",
    # Las tres de expectativas de inflación de Michigan: las había puesto yo
    # en alto impacto por criterio propio y no lo puedo sostener.
    "Michigan 1 Year Inflation Expectations Prel",
    "Michigan 5 Year Inflation Expectations Final",
    "Michigan Inflation Expectations",
    "NFIB Business Optimism Index",
    "Challenger Job Cuts",
    "Nonfarm Productivity QoQ Final",
    "Unit Labour Costs QoQ Final",
    "Unit Labor Costs QoQ Prel",
    "Balance of Trade",
    "Trade Balance",
    "Fed Beige Book",
)


class TestLoQueLaListaDeKevinNoNombra:
    """«nose si me faltan mas, investiga y ponlos todos.»"""

    @pytest.mark.parametrize("evento", _ALTO_IMPACTO_QUE_KEVIN_NO_NOMBRO)
    def test_entra_como_ALTO_impacto(self, evento):
        import vertex_api as V

        ficha = V._macro_ficha(evento)
        assert ficha is not None, f"«{evento}» no casa con ninguna clave"
        assert ficha[0] == 1, f"«{evento}» entraría sólo como relleno"
        assert V._es_evento_macro(evento) is True

    @pytest.mark.parametrize("evento", _EXCLUIDOS)
    def test_lo_que_NO_es_de_alto_impacto_NO_entra(self, evento):
        """Están en la tabla —hicieron falta para reconocerlos— pero el filtro
        no los deja pasar. Antes rellenaban huecos vacíos; Kevin los quiere
        fuera, y una caja con menos filas dice la verdad."""
        import vertex_api as V

        ficha = V._macro_ficha(evento)
        assert ficha is not None, (
            f"«{evento}» no casa con ninguna clave: sin ficha, la red de abajo "
            f"lo dejaría entrar si FMP lo marcase «High»")
        assert ficha[0] == 2, f"«{evento}» está en nivel {ficha[0]}"
        assert V._es_evento_macro(evento) is False

    def test_los_DOS_horizontes_de_Michigan_son_DOS_filas(self):
        """Un año y cinco años no son la misma expectativa: si cayeran en la
        misma familia, sólo se vería una de las dos. (El de cinco entra como
        relleno, pero eso es su NIVEL, no su fila.)"""
        import vertex_api as V

        uno = V._macro_grupo("Michigan 1 Year Inflation Expectations Prel")
        cinco = V._macro_grupo("Michigan 5 Year Inflation Expectations Final")
        assert uno != cinco
        # Y ninguno de los dos se confunde con el IPC.
        assert uno != V._macro_grupo("Inflation Rate YoY")
        assert cinco != V._macro_grupo("Inflation Rate YoY")

    def test_las_expectativas_no_se_confunden_con_el_SENTIMIENTO(self):
        """Michigan publica sentimiento, expectativas del consumidor y
        expectativas de inflación el mismo día. Son tres cosas."""
        import vertex_api as V

        grupos = {
            V._macro_grupo("UoM Consumer Sentiment Index"),
            V._macro_grupo("Michigan Consumer Expectations Prel"),
            V._macro_grupo("Michigan Inflation Expectations"),
        }
        assert len(grupos) == 3, f"se solapan: {grupos}"

    @pytest.mark.parametrize("a,b", [
        # `unit labo` casa con las dos grafías, que es lo que se quiere.
        ("Unit Labour Costs QoQ Final", "Unit Labor Costs QoQ Prel"),
        ("Balance of Trade", "Trade Balance"),
        ("Philadelphia Fed Manufacturing Index", "Philly Fed Business Conditions"),
    ])
    def test_el_mismo_dato_con_DOS_nombres_es_UNA_fila(self, a, b):
        import vertex_api as V

        assert V._macro_grupo(a) == V._macro_grupo(b), (
            f"«{a}» y «{b}» son el mismo dato y ocuparían dos huecos")

    @pytest.mark.parametrize("a,b", [
        # `nonfarm productivity` es más larga que `nonfarm payroll`, y
        # `_MACRO_CLAVES` ordena de larga a corta: la productividad no puede
        # acabar contada como nóminas.
        ("Nonfarm Productivity QoQ Final", "Nonfarm Payrolls"),
        # «NY Empire State **Manufacturing** Index» no es el ISM.
        ("NY Empire State Manufacturing Index", "ISM Manufacturing PMI"),
        # Las expectativas de inflación no son la tasa de inflación.
        ("Michigan 1 Year Inflation Expectations Prel", "Inflation Rate YoY"),
    ])
    def test_las_claves_nuevas_no_se_TRAGAN_a_las_viejas(self, a, b):
        import vertex_api as V

        assert V._macro_grupo(a) != V._macro_grupo(b)

    def test_la_caja_se_queda_CORTA_antes_que_rellenarse(self, monkeypatch):
        """Diecisiete excluidos de AYER contra un IPC de hace diez días.

        Antes rellenaban los huecos y la caja salía con ocho filas. Ahora sale
        con UNA: «esto es todo lo de alto impacto que ha salido» es verdad, y
        una caja rellenada de segunda fila es una mentira cómoda.
        """
        import vertex_api as V

        eventos = [(n, 1, "1.0%", "0.9%", "0.8%") for n in _EXCLUIDOS]
        eventos.append(("Core Inflation Rate YoY", 10, "2.9%", "3.0%", "3.1%"))
        caja = _caja_macro(monkeypatch, eventos)
        nombres = [f["evento"] for f in caja["publicados"]]
        assert nombres == ["Core Inflation Rate YoY"], (
            f"la caja se rellenó con lo que no es de alto impacto: {nombres}")

    def test_la_tabla_no_tiene_direcciones_INVENTADAS(self):
        """`mejor` sólo puede ser «alto», «bajo» o nada. Un valor escrito mal
        —«bajo »,«low», True— no rompería nada y pintaría el color al revés."""
        import vertex_api as V

        for clave, (nivel, mejor) in V._MACRO_TABLA.items():
            assert nivel in (1, 2), f"«{clave}» tiene nivel {nivel!r}"
            assert mejor in ("alto", "bajo", None), (
                f"«{clave}» tiene mejor={mejor!r}")

    def test_cada_SINONIMO_tiene_su_clave_en_la_tabla(self):
        """`_MACRO_CLAVES` se arma de la TABLA. Un sinónimo cuya clave no esté
        en la tabla no casa nunca: es un alias muerto, y así se perdieron
        «Philly Fed» y «Trade Balance» hasta que se les dio clave propia."""
        import vertex_api as V

        huerfanos = [k for k in V._MACRO_SINONIMOS if k not in V._MACRO_TABLA]
        assert not huerfanos, f"sinónimos sin clave en la tabla: {huerfanos}"

    def test_y_cada_sinonimo_apunta_a_una_familia_REAL(self):
        import vertex_api as V

        rotos = [(k, d) for k, d in V._MACRO_SINONIMOS.items()
                 if d not in V._MACRO_TABLA]
        assert not rotos, f"sinónimos que apuntan a la nada: {rotos}"

    def test_el_dia_de_MICHIGAN_el_IPC_sigue_en_la_caja(self, monkeypatch):
        """Michigan suelta cuatro cifras el mismo viernes. La caja tiene ocho
        huecos y ordena por fecha: con las cuatro en nivel 1 se llevaban tres
        y el IPC de la semana pasada se caía de la caja."""
        import vertex_api as V

        caja = _caja_macro(monkeypatch, [
            ("UoM Consumer Sentiment Index", 1, "67.8", "66.0", "65.4"),
            ("Michigan Consumer Expectations Prel", 1, "58.1", "57.0", "56.4"),
            ("Michigan 1 Year Inflation Expectations Prel", 1, "3.1%", "3.0%", "3.2%"),
            ("Michigan 5 Year Inflation Expectations Prel", 1, "2.9%", "3.0%", "3.0%"),
            ("ISM Services PMI", 2, "54.2", "53.0", "52.8"),
            ("Initial Jobless Claims", 2, "221K", "230K", "228K"),
            ("Nonfarm Payrolls", 3, "275K", "240K", "215K"),
            ("Unemployment Rate", 3, "4.1%", "4.0%", "4.0%"),
            ("Average Hourly Earnings MoM", 3, "0.4%", "0.3%", "0.3%"),
            ("Core Inflation Rate YoY", 8, "2.9%", "3.0%", "3.1%"),
            ("Inflation Rate YoY", 8, "3.4%", "3.4%", "3.5%"),
        ])
        nombres = [f["evento"] for f in caja["publicados"]]
        assert "Core Inflation Rate YoY" in nombres, (
            f"Michigan se comió el hueco del IPC: {nombres}")
        michigan = [n for n in nombres if "Michigan" in n or "UoM" in n]
        assert len(michigan) <= 2, f"Michigan ocupa {len(michigan)} huecos: {michigan}"

    def test_las_ACTAS_no_son_el_COMUNICADO_de_la_Fed(self, monkeypatch):
        """Las actas salen tres semanas después del comunicado, y la caja mira
        35 días atrás: los dos caben dentro. Con la clave genérica `fomc` caían
        en la misma fila y **uno de los dos desaparecía** — y Kevin nombró las
        actas expresamente en su lista."""
        import vertex_api as V

        assert V._macro_grupo("FOMC Minutes") != V._macro_grupo("FOMC Statement")
        assert V._macro_grupo("FOMC Minutes") \
            != V._macro_grupo("FOMC Economic Projections")

        caja = _caja_macro(monkeypatch, [
            ("FOMC Minutes", 2, "", "", ""),
            ("FOMC Statement", 23, "4.25%", "4.25%", "4.50%"),
        ])
        nombres = [f["evento"] for f in caja["publicados"]]
        assert "FOMC Minutes" in nombres and "FOMC Statement" in nombres, (
            f"uno se comió al otro: {nombres}")

    @pytest.mark.parametrize("a,b", [
        # El mismo acto de las 2 p. m. con tres rótulos: UNA fila.
        ("Fed Interest Rate Decision", "FOMC Statement"),
        ("Fed Interest Rate Decision", "Federal Funds Rate"),
    ])
    def test_pero_el_COMUNICADO_de_tipos_es_UNA_sola_fila(self, a, b):
        import vertex_api as V

        assert V._macro_grupo(a) == V._macro_grupo(b), (
            f"«{a}» y «{b}» son el mismo acto y gastarían dos huecos")

    def test_nada_de_la_Fed_lleva_COLOR(self):
        """Un tipo más alto no es «peor dato»: es política. Pintarlo de rojo
        sería una opinión sobre la Fed, no una lectura del dato."""
        import vertex_api as V

        for nombre in ("FOMC Minutes", "FOMC Statement", "FOMC Economic Projections",
                       "Fed Interest Rate Decision", "Federal Funds Rate",
                       "Fed Press Conference", "Fed Chair Powell Speech"):
            ficha = V._macro_ficha(nombre)
            assert ficha is not None and ficha[1] is None, (
                f"«{nombre}» lleva dirección {ficha}")

    @pytest.mark.parametrize("evento", [
        "FOMC Minutes",
        "Fed Chair Powell Speech",
        "Fed Press Conference",
        "Treasury Refunding Announcement",
        "Treasury Refunding Financing Estimates",
    ])
    def test_un_COMUNICADO_sin_cifra_SALE_igual(self, monkeypatch, evento):
        """No traen número porque no lo tienen, no porque se hayan retrasado.

        La regla «publicado = tiene dato» los mandaba a «Próximos datos»
        marcados «retrasado» **para siempre**. Kevin nombró cinco de éstos en
        su lista de alto impacto y ninguno salía nunca.
        """
        import vertex_api as V

        caja = _caja_macro(monkeypatch, [(evento, 3, "", "", "")])
        nombres = [f["evento"] for f in caja["publicados"]]
        assert evento in nombres, (
            f"«{evento}» no llegó a publicados: {caja['proximos']}")
        fila = next(f for f in caja["publicados"] if f["evento"] == evento)
        assert fila["salio"] is None, "no tiene cifra, y no hay que inventarla"
        assert fila["mejor"] is None, "un comunicado no se pinta de colores"

    def test_pero_un_DATO_sin_cifra_sigue_siendo_un_RETRASO(self, monkeypatch):
        """La excepción es sólo para los comunicados. Unas nóminas que debían
        salir ayer y no han salido son lo que más se quiere ver, y siguen
        estando por venir — marcadas «retrasado», que es como estaban."""
        import vertex_api as V

        caja = _caja_macro(monkeypatch, [("Nonfarm Payrolls", 1, "", "", "")])
        assert not [f for f in caja["publicados"] if "Payroll" in f["evento"]]
        fila = next(f for f in caja["proximos"] if "Payroll" in f["evento"])
        assert fila["retrasado"] is True

    def test_y_un_comunicado_que_AUN_NO_ha_ocurrido_no_se_da_por_dado(
            self, monkeypatch):
        """La rueda de prensa del mes que viene no es un acto ocurrido."""
        import vertex_api as V

        caja = _caja_macro(monkeypatch, [("Fed Press Conference", -6, "", "", "")])
        assert not [f for f in caja["publicados"] if "Press" in f["evento"]]
        assert [f for f in caja["proximos"] if "Press" in f["evento"]]

    def test_si_el_comunicado_SI_trae_cifra_entra_por_la_puerta_de_siempre(
            self, monkeypatch):
        """FMP a veces mete el tipo dentro del comunicado. Entonces no se mira
        la excepción: es un dato con número, como cualquier otro."""
        import vertex_api as V

        caja = _caja_macro(monkeypatch, [
            ("Fed Interest Rate Decision", 3, "4.25%", "4.25%", "4.50%")])
        fila = next(f for f in caja["publicados"]
                    if "Interest Rate" in f["evento"])
        assert fila["salio"] == 4.25
        assert fila["mejor"] is None, "un tipo más alto no es un dato «peor»"

    def test_cada_COMUNICADO_apunta_a_una_familia_REAL(self):
        """El conjunto se compara contra `_macro_familia`, que devuelve claves
        ya colapsadas por sinónimos: una entrada que no sea una familia real es
        una excepción que no se aplica nunca."""
        import vertex_api as V

        reales = {V._MACRO_SINONIMOS.get(k, k) for k in V._MACRO_TABLA}
        huerfanos = sorted(V._MACRO_COMUNICADOS - reales)
        assert not huerfanos, f"comunicados que no son familia: {huerfanos}"

    def test_el_PROVEEDOR_no_puede_pisar_una_EXCLUSION_explicita(self, monkeypatch):
        """La red de abajo sube lo que NO conocemos y FMP marca `High`. Pero
        el nivel 2 es una decisión tomada —«esto se miró y no es de alto
        impacto»—, y si el `impact` del proveedor pudiera pisarla, la lista de
        exclusión no excluiría nada."""
        import vertex_api as V

        hoy = date.today()
        crudo = [{"country": "US", "event": "Chicago PMI",
                  "date": (hoy - timedelta(days=1)).isoformat() + " 12:30:00",
                  "actual": "48.1", "estimate": "47.0", "previous": "46.5",
                  "impact": "High"}]

        class _R:
            status_code = 200

            def json(self):
                return crudo

        monkeypatch.setenv("FMP_API_KEY", "x" * 20)
        monkeypatch.setattr(V.requests, "get", lambda *a, **k: _R())
        caja = V._macro_calcula()
        assert not [f for f in caja["publicados"] if "Chicago" in f["evento"]], (
            "FMP lo marcó «High» y se coló pese a estar excluido a mano")
        assert not [f for f in caja["proximos"] if "Chicago" in f["evento"]]

    def test_y_lo_EXCLUIDO_tampoco_sale_en_PROXIMOS_datos(self, monkeypatch):
        """«no quiero que salgan en el panel» es el panel entero: la caja de
        lo que ya salió y la de lo que viene."""
        import vertex_api as V

        caja = _caja_macro(monkeypatch, [
            ("Chicago PMI", -3, "", "", ""),
            ("Fed Beige Book", -4, "", "", ""),
            ("Core Inflation Rate YoY", -2, "", "", ""),
        ])
        nombres = [f["evento"] for f in caja["proximos"]]
        assert nombres == ["Core Inflation Rate YoY"], (
            f"lo excluido se coló en «Próximos datos»: {nombres}")

    @pytest.mark.parametrize("evento", _ALTO_IMPACTO_DE_KEVIN)
    def test_y_NADA_de_la_lista_de_Kevin_se_perdio_por_el_camino(self, evento):
        """El filtro nuevo es más estrecho. Este caso es el que avisa si al
        estrecharlo se llevó por delante algo que él sí pidió."""
        import vertex_api as V

        assert V._es_evento_macro(evento) is True

    def test_la_caja_da_DOCE_filas_y_no_ocho(self, monkeypatch):
        """«sube el corte a 12 filas.» — Kevin, 27/08/2026.

        Eran ocho. Con el relleno fuera, las ocho pasaron a ser de alto impacto
        de verdad, y se vio el efecto: en una ventana cargada las nóminas de
        hace tres semanas se caían por abajo, no por ruido sino porque había
        ocho cosas más recientes.
        """
        import vertex_api as V

        assert V._MACRO_FILAS == 12
        # Quince datos de alto impacto, uno por día hacia atrás.
        eventos = [
            ("Core Inflation Rate YoY", 1), ("Inflation Rate YoY", 2),
            ("Core PPI", 3), ("PPI MoM", 4), ("Nonfarm Payrolls", 5),
            ("Unemployment Rate", 6), ("Average Hourly Earnings MoM", 7),
            ("ISM Manufacturing PMI", 8), ("ISM Services PMI", 9),
            ("Retail Sales MoM", 10), ("Personal Spending MoM", 11),
            ("Durable Goods Orders", 12), ("Housing Starts", 13),
            ("Existing Home Sales", 14), ("CB Consumer Confidence", 15),
        ]
        caja = _caja_macro(monkeypatch, [
            (n, d, "1.0%", "0.9%", "0.8%") for n, d in eventos])
        assert len(caja["publicados"]) == 12, (
            f"la caja dio {len(caja['publicados'])} filas")
        # Y son las DOCE más recientes, no doce cualesquiera.
        assert [f["evento"] for f in caja["publicados"]] == \
            [n for n, _ in eventos[:12]]

    def test_pero_PROXIMOS_datos_se_queda_en_ocho(self, monkeypatch):
        """Mira siete días adelante y rara vez llega a llenarse: subirlo no
        enseñaría ni una fila más, y alargaría la caja en el móvil."""
        import vertex_api as V

        assert V._MACRO_FILAS_PROXIMOS == 8
        caja = _caja_macro(monkeypatch, [
            (n, -d, "", "", "") for d, n in enumerate([
                "Core Inflation Rate YoY", "Inflation Rate YoY", "Core PPI",
                "PPI MoM", "Nonfarm Payrolls", "Unemployment Rate",
                "Average Hourly Earnings MoM", "ISM Manufacturing PMI",
                "ISM Services PMI", "Retail Sales MoM"], start=1)])
        assert len(caja["proximos"]) == 8
