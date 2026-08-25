"""El área de Portafolio: opciones dentro del riesgo, perfil dentro de las
reglas, y Drift dentro del libro.

Lo que estos casos protegen, en una frase cada uno:

  · Las opciones entran en el riesgo como EXPOSICIÓN delta-equivalente, nunca
    como prima, y sin inflar el «Valor Total del Portafolio».
  · Cuando no hay dato de mercado, las griegas salen vacías — no en cero, y
    no con una IV inventada del 50%.
  · Los umbrales de los guardrails salen del cuestionario, y cada regla dice
    si el suyo lo contestaste tú o es heredado.
  · Drift llega al libro SOLO para 90/120/320 días, porque son posiciones de
    semanas a meses. Un contrato a dos semanas no se lee contra el muro de
    tres meses.

Sin red: Massive y la cadena se sustituyen por dobles.

    python -m pytest tests_vertex/test_portafolio.py -q
"""

from __future__ import annotations

import inspect
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "engine"))

pytest.importorskip("fastapi", reason="requiere las deps de vertex_api")


def _solo_codigo(fn):
    """La fuente de `fn` SIN comentarios ni docstrings.

    Dos de estos guardianes nacieron rotos por mirar la prosa: uno buscaba la
    palabra «score» y la encontró en el docstring que promete no puntuar; el
    otro buscaba «QuantData PRIMARIO» y lo encontró en el comentario que
    explica por qué ya no lo es. Un guardián que se cree lo que dice un
    comentario mide la documentación, no el programa.
    """
    import ast as _ast
    import textwrap

    arbol = _ast.parse(textwrap.dedent(inspect.getsource(fn)))
    for nodo in _ast.walk(arbol):
        if isinstance(nodo, (_ast.FunctionDef, _ast.AsyncFunctionDef,
                             _ast.ClassDef, _ast.Module)):
            cuerpo = nodo.body
            if (cuerpo and isinstance(cuerpo[0], _ast.Expr)
                    and isinstance(cuerpo[0].value, _ast.Constant)
                    and isinstance(cuerpo[0].value.value, str)):
                nodo.body = cuerpo[1:] or [_ast.Pass()]
    return _ast.unparse(_ast.fix_missing_locations(arbol))


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    import vertex_api

    with TestClient(vertex_api.app) as c:
        yield c


@pytest.fixture
def cab():
    import vertex_api as V

    return {"x-vertex-token": V.VERTEX_API_TOKEN} if V.VERTEX_API_TOKEN else {}


def _libro(client, cab, posiciones, opciones):
    r = client.post("/api/portfolio/import", headers=cab,
                    json={"source": "manual", "positions": posiciones,
                          "options": opciones})
    assert r.status_code == 200, r.text[:200]
    return r.json()


# ══════════════════════════════════════════════════════════════════════════
class TestLasGriegasDicenCuandoNOSaben:
    """Con el feed caído esto devolvía `ok: true`, `iv: 50.0` y `delta: 0.0`.

    Un número inventado con la misma pinta que uno medido, y un «no pude
    calcular» escrito igual que un delta cero de verdad. La nota al pie
    remataba diciendo «IV de yfinance», que era falso: esa IV no vino de
    ningún sitio.
    """

    def test_sin_dato_las_griegas_son_NULAS_no_cero(self):
        import vertex_api as V

        # Sin spot no hay griega que publicar. `_bs_greeks` se llama igual
        # —Black-Scholes necesita una sigma o lanza— pero nada de eso sale.
        out = V.compute_options_analytics(
            [{"underlying": "ZZZZ", "option_type": "call", "strike": 100.0,
              "expiry": (date.today() + timedelta(days=120)).isoformat(),
              "contracts": 1.0, "value": 0.0}], [])
        p = out["positions"][0]
        assert p["medido"] is False
        assert p["iv"] is None, "una IV inventada no puede publicarse como dato"
        assert p["delta"] is None and p["gamma"] is None
        assert p["iv_fuente"] == "sin dato"

    def test_lo_no_medido_NO_entra_en_los_totales_del_libro(self):
        """Un contrato valorado con una IV fingida contamina el delta neto y
        después no hay forma de ver cuál fue."""
        import vertex_api as V

        out = V.compute_options_analytics(
            [{"underlying": "ZZZZ", "option_type": "call", "strike": 100.0,
              "expiry": (date.today() + timedelta(days=120)).isoformat(),
              "contracts": 1.0, "value": 0.0}], [])
        assert out["greeks"]["net_delta_dollar"] == 0.0
        assert out["by_underlying"] == {}
        assert out["ladder"] == []

    def test_se_DICE_cuantos_contratos_sostienen_los_totales(self):
        import vertex_api as V

        out = V.compute_options_analytics(
            [{"underlying": "ZZZZ", "option_type": "call", "strike": 100.0,
              "expiry": (date.today() + timedelta(days=120)).isoformat(),
              "contracts": 1.0, "value": 0.0}], [])
        assert out["datos_incompletos"] is True
        assert out["cobertura"]["con_dato"] == 0 and out["cobertura"]["de"] == 1
        assert out["cobertura"]["sin_dato"][0]["motivo"]
        assert any("sin dato de mercado" in a["msg"] for a in out["alerts"])

    def test_la_nota_ya_no_atribuye_a_yfinance_una_IV_inventada(self):
        import vertex_api as V

        fuente = inspect.getsource(V.compute_options_analytics)
        assert "IV de yfinance" not in fuente
        assert "IV_SIN_DATO" in fuente

    def test_el_panel_no_escribe_la_palabra_null_en_la_tabla(self):
        """`${p.delta}` con `null` dentro pinta «null» en pantalla."""
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        i = html.index("const posRows = d.positions.map")
        trozo = html[i:i + 2200]
        assert "${p.delta}" not in trozo
        assert "${p.gamma}" not in trozo
        assert "num(p.delta)" in trozo and "num(p.gamma)" in trozo


# ══════════════════════════════════════════════════════════════════════════
class TestLasOpcionesEntranComoEXPOSICIONNoComoPrima:
    """Una opción tiene dos números y confundirlos es el error clásico: lo que
    VALE (la prima) y lo que EXPONE (delta × contratos × 100 × spot). Un call
    de $200 de prima sobre NVDA puede exponer $12.000."""

    def _analitica(self, delta_dolar):
        return {"positions": [{"underlying": "NVDA", "medido": True,
                               "delta_$": delta_dolar}],
                "cobertura": {"con_dato": 1, "de": 1, "sin_dato": []}}

    def test_la_exposicion_se_SUMA_al_ticker_que_ya_tienes(self, monkeypatch):
        """Tener NVDA y calls de NVDA es UNA apuesta. Contarlas como dos
        posiciones diluiría la concentración justo donde importa."""
        import vertex_api as V

        monkeypatch.setattr(V, "get_options_snapshot", lambda: [{"underlying": "NVDA"}])
        monkeypatch.setattr(V, "compute_options_analytics",
                            lambda o, e=None: self._analitica(12000.0))
        pos, meta = V._posiciones_con_opciones(
            [{"ticker": "NVDA", "name": "NVIDIA", "value": 400.0}])
        assert len(pos) == 1, "no puede aparecer una segunda fila de NVDA"
        assert pos[0]["value"] == pytest.approx(12400.0)
        assert meta["incluye_opciones"] is True
        assert meta["sumados_a_una_posicion_existente"] == ["NVDA"]

    def test_un_subyacente_que_NO_tienes_entra_como_fila_nueva_marcada(self, monkeypatch):
        import vertex_api as V

        monkeypatch.setattr(V, "get_options_snapshot", lambda: [{"underlying": "NVDA"}])
        monkeypatch.setattr(V, "compute_options_analytics",
                            lambda o, e=None: self._analitica(9000.0))
        pos, meta = V._posiciones_con_opciones(
            [{"ticker": "AAPL", "name": "Apple", "value": 500.0}])
        nueva = [p for p in pos if p["ticker"] == "NVDA"][0]
        assert nueva["es_opcion"] is True
        assert meta["subyacentes_nuevos"] == ["NVDA"]

    def test_un_delta_NEGATIVO_tambien_es_exposicion(self, monkeypatch):
        """Una put larga grande concentra tanto como un call grande. Sumar el
        negativo cancelaría la exposición contra la del equity."""
        import vertex_api as V

        monkeypatch.setattr(V, "get_options_snapshot", lambda: [{"underlying": "NVDA"}])
        monkeypatch.setattr(V, "compute_options_analytics",
                            lambda o, e=None: self._analitica(-8000.0))
        pos, _ = V._posiciones_con_opciones(
            [{"ticker": "NVDA", "name": "NVIDIA", "value": 400.0}])
        assert pos[0]["value"] == pytest.approx(8400.0)

    def test_el_COSTE_de_las_acciones_no_se_compara_con_la_exposicion(self, monkeypatch):
        """El fallo que casi entra: al fundir, `value` se infla con el delta y
        la regla de stop-loss lo comparaba contra el `cost_basis` del equity.
        NVDA a $400 de coste $300 con $12.000 de delta salía «+4.000%»."""
        import vertex_api as V

        monkeypatch.setattr(V, "get_options_snapshot", lambda: [{"underlying": "NVDA"}])
        monkeypatch.setattr(V, "compute_options_analytics",
                            lambda o, e=None: self._analitica(12000.0))
        pos, _ = V._posiciones_con_opciones(
            [{"ticker": "NVDA", "name": "NVIDIA", "value": 400.0, "cost_basis": 300.0}])
        assert pos[0]["valor_equity"] == pytest.approx(400.0)
        g = V.compute_portfolio_guardrails(pos, None)
        stop = [r for r in g["rules"] if "stop-loss" in r["rule"]][0]
        assert "NVDA" not in stop["detail"], stop["detail"]

    def test_sin_dato_de_mercado_se_devuelve_el_libro_TAL_CUAL_y_se_dice(self, monkeypatch):
        """Un panel de riesgo que se cae porque un feed de opciones no
        responde es peor que uno que avisa."""
        import vertex_api as V

        monkeypatch.setattr(V, "get_options_snapshot", lambda: [{"underlying": "NVDA"}])
        monkeypatch.setattr(V, "compute_options_analytics",
                            lambda o, e=None: {"positions": [{"underlying": "NVDA",
                                                              "medido": False}],
                                               "cobertura": {"con_dato": 0, "de": 1}})
        pos, meta = V._posiciones_con_opciones(
            [{"ticker": "NVDA", "name": "NVIDIA", "value": 400.0}])
        assert pos[0]["value"] == 400.0
        assert meta["incluye_opciones"] is False
        assert "ningún contrato" in meta["motivo"]

    def test_las_SEIS_rutas_de_riesgo_pasan_por_la_capa(self):
        """Cablearlo en cuatro y olvidar dos dejaba dos pantallas midiendo un
        libro distinto del de las otras cuatro, sin que nada lo dijera."""
        import vertex_api as V

        fuente = inspect.getsource(V)
        assert fuente.count("_posiciones_con_opciones(") == 7, (
            "1 definición + 6 llamadas: risk, stress, whatif, atribución, "
            "guardrails y optimizador")


# ══════════════════════════════════════════════════════════════════════════
class TestLosGuardrailsSalenDelPerfil:
    """Estaban fijos —25%, 60%, mandato 30/70, 40%, −25%— y ninguno se había
    preguntado nunca. A un inversionista agresivo con $1.000 le decían que su
    mandato era 30/70."""

    LIBRO = [{"ticker": "NVDA", "name": "NVIDIA", "value": 600.0, "cost_basis": 500.0},
             {"ticker": "SOFI", "name": "SoFi", "value": 400.0, "cost_basis": 500.0}]

    def test_el_tope_por_posicion_es_el_del_cuestionario(self):
        import vertex_api as V

        perfil = {"max_posicion_pct": [20, 30], "tolerancia": "agresivo",
                  "capital": 1000.0, "sin_contestar": []}
        g = V.compute_portfolio_guardrails(self.LIBRO, None, perfil=perfil)
        r = [x for x in g["rules"] if x["rule"] == "Concentracion por posicion"][0]
        assert r["threshold"] == "<=30%"
        assert r["origen"] == "perfil"

    def test_un_perfil_CONSERVADOR_aprieta_el_mismo_libro(self):
        """Si el umbral no se moviera con el perfil, leerlo no serviría."""
        import vertex_api as V

        agresivo = V.compute_portfolio_guardrails(
            self.LIBRO, None,
            perfil={"max_posicion_pct": [20, 30], "tolerancia": "agresivo",
                    "capital": 1000.0, "sin_contestar": []})
        conservador = V.compute_portfolio_guardrails(
            self.LIBRO, None,
            perfil={"max_posicion_pct": [5, 10], "tolerancia": "conservador",
                    "capital": 1000.0, "sin_contestar": []})
        ra = [x for x in agresivo["rules"] if x["rule"] == "Concentracion por posicion"][0]
        rc = [x for x in conservador["rules"] if x["rule"] == "Concentracion por posicion"][0]
        assert ra["threshold"] == "<=30%" and rc["threshold"] == "<=10%"
        assert rc["status"] == "breach"

    def test_el_mandato_estable_crecimiento_sigue_a_la_tolerancia(self):
        import vertex_api as V

        for tol, esperado in (("conservador", "60/40"), ("moderado", "40/60"),
                              ("agresivo", "20/80"), ("especulativo", "10/90")):
            g = V.compute_portfolio_guardrails(
                self.LIBRO, None,
                perfil={"max_posicion_pct": [20, 30], "tolerancia": tol,
                        "capital": 1000.0, "sin_contestar": []})
            assert any(esperado in r["rule"] for r in g["rules"]), (tol, esperado)

    def test_el_mandato_NUNCA_es_breach(self):
        """El cuestionario no pregunta por este reparto: castigar por él sería
        inventarse una regla que nadie fijó."""
        import vertex_api as V

        g = V.compute_portfolio_guardrails(
            [{"ticker": "SOFI", "name": "SoFi", "value": 1000.0}], None,
            perfil={"max_posicion_pct": [20, 30], "tolerancia": "conservador",
                    "capital": 1000.0, "sin_contestar": []})
        m = [r for r in g["rules"] if "Mandato" in r["rule"]][0]
        assert m["status"] != "breach"
        assert "orientativa" in m["detail"]

    def test_cada_regla_dice_si_el_umbral_lo_contestaste_TU(self):
        import vertex_api as V

        g = V.compute_portfolio_guardrails(
            self.LIBRO, None,
            perfil={"max_posicion_pct": [20, 30], "tolerancia": "agresivo",
                    "capital": 1000.0, "sin_contestar": []})
        assert all("origen" in r for r in g["rules"])
        assert g["perfil"]["de_tu_perfil"], "ninguna regla salió del perfil"
        assert g["perfil"]["max_posicion_pct"] == [20, 30]

    def test_una_pregunta_SIN_contestar_no_se_presenta_como_tuya(self):
        import vertex_api as V

        g = V.compute_portfolio_guardrails(
            self.LIBRO, None,
            perfil={"max_posicion_pct": [20, 30], "tolerancia": "agresivo",
                    "capital": 1000.0, "sin_contestar": ["max_posicion_pct"]})
        r = [x for x in g["rules"] if x["rule"] == "Concentracion por posicion"][0]
        assert r["origen"] == "heredado"
        assert "heredado" in r["detail"]

    def test_la_regla_de_TAMANO_existe_y_usa_tu_capital(self):
        """El perfil lo pide literal: con ese capital y opciones, el sizing
        manda. Una pantalla que solo avisa de estar demasiado concentrado no
        sirve a quien el problema que tiene es el contrario."""
        import vertex_api as V

        g = V.compute_portfolio_guardrails(
            self.LIBRO, None,
            perfil={"max_posicion_pct": [20, 30], "tolerancia": "agresivo",
                    "capital": 1000.0, "sin_contestar": []})
        r = [x for x in g["rules"] if "Tamano viable" in x["rule"]][0]
        assert "$300" in r["value"]

    def test_sin_capital_declarado_la_regla_de_tamano_no_se_inventa(self):
        import vertex_api as V

        g = V.compute_portfolio_guardrails(
            self.LIBRO, None,
            perfil={"max_posicion_pct": [20, 30], "tolerancia": "agresivo",
                    "capital": 0, "sin_contestar": ["capital"]})
        assert not [x for x in g["rules"] if "Tamano viable" in x["rule"]]


# ══════════════════════════════════════════════════════════════════════════
class TestDriftLlegaAlLibroSoloParaLosPlazosLargos:
    """«Solo será con el drift para los días de 90/120/320, porque son
    posiciones que uno aguanta por semanas y tal vez hasta meses.»"""

    def test_los_plazos_son_EXACTAMENTE_los_tres_largos(self):
        import vertex_api as V

        assert V._DRIFT_LIBRO_PLAZOS == (90, 120, 320)
        assert 30 not in V._DRIFT_LIBRO_PLAZOS, (
            "el de ~30 solapa con los horizontes del motor y ahí manda él")

    def test_un_contrato_CORTO_no_se_lee_contra_el_muro_de_tres_meses(self):
        import vertex_api as V

        buckets = [{"dte_objetivo": 90}, {"dte_objetivo": 120}, {"dte_objetivo": 320}]
        assert V._drift_bucket_para(buckets, 10) is None
        assert V._drift_bucket_para(buckets, 30) is None
        assert V._drift_bucket_para(buckets, None) is None

    def test_cada_contrato_cae_en_el_plazo_mas_cercano(self):
        import vertex_api as V

        buckets = [{"dte_objetivo": 90}, {"dte_objetivo": 120}, {"dte_objetivo": 320}]
        assert V._drift_bucket_para(buckets, 95)["dte_objetivo"] == 90
        assert V._drift_bucket_para(buckets, 118)["dte_objetivo"] == 120
        assert V._drift_bucket_para(buckets, 300)["dte_objetivo"] == 320

    def test_el_corto_se_DEVUELVE_con_el_motivo_en_vez_de_desaparecer(self, client, cab):
        """Un contrato que no sale en ninguna lista se lee como un olvido."""
        corto = (date.today() + timedelta(days=10)).isoformat()
        _libro(client, cab, [{"ticker": "NVDA", "name": "NVIDIA", "value": 400}],
               [{"underlying": "NVDA", "option_type": "put", "strike": 150,
                 "expiry": corto, "contracts": 1}])
        d = client.get("/api/portfolio-drift", headers=cab).json()
        assert d["ok"] is True
        assert len(d["fuera_de_alcance"]) == 1
        assert "Proyecciones" in d["fuera_de_alcance"][0]["fuera_de_alcance"]

    def test_sin_cadena_de_Massive_se_dice_CUAL_fallo(self, client, cab):
        largo = (date.today() + timedelta(days=115)).isoformat()
        _libro(client, cab, [{"ticker": "NVDA", "name": "NVIDIA", "value": 400}],
               [{"underlying": "NVDA", "option_type": "call", "strike": 200,
                 "expiry": largo, "contracts": 2}])
        d = client.get("/api/portfolio-drift", headers=cab).json()
        assert d["ok"] is True
        c0 = d["contratos"][0]
        assert c0.get("error") or c0.get("muro_calls") is not None

    def test_NO_puntua_ni_recomienda(self):
        """Es contexto de posicionamiento. En cuanto publique un score, el
        portafolio pasa a ser un tercer agente que nadie auditó."""
        import vertex_api as V

        codigo = _solo_codigo(V.portfolio_drift)
        for prohibido in ("run_scorecard", "predict_pro", "verdict", "score"):
            assert prohibido not in codigo, prohibido

    def test_una_sola_bajada_de_cadena_por_subyacente(self, monkeypatch):
        """Seis contratos del mismo papel no pueden ser seis descargas."""
        import vertex_api as V

        fuente = inspect.getsource(V.portfolio_drift)
        assert "if u not in por_subyacente and u not in fallos:" in fuente

    def test_la_lectura_avisa_cuando_tu_strike_esta_sobre_el_muro(self):
        import vertex_api as V

        txt = V._drift_lectura_del_contrato("call", 400.0, 150.0, 200.0, 250.0, False)
        assert "POR ENCIMA del muro de calls" in txt
        txt = V._drift_lectura_del_contrato("put", 100.0, 150.0, 200.0, 250.0, False)
        assert "POR DEBAJO del muro de puts" in txt
        txt = V._drift_lectura_del_contrato("call", 200.0, 150.0, 200.0, 250.0, True)
        assert "imán" in txt


# ══════════════════════════════════════════════════════════════════════════
class TestElRiesgoDeRuinaSeMideSobreElCAMINO:
    """No sobre el resultado final. Un camino que se hunde un 45% a mitad y
    remonta hasta un −5% es, sobre el papel, una pérdida moderada; en la vida
    real es la que te saca del mercado."""

    def test_se_mide_sobre_el_minimo_del_camino(self):
        import vertex_api as V

        fuente = inspect.getsource(V.compute_portfolio_stress)
        assert "def ruina(" in fuente
        assert "np.maximum.accumulate" in fuente
        assert "prob_caida_25" in fuente and "prob_caida_50" in fuente

    def test_no_se_simula_NADA_nuevo(self):
        """Los mismos caminos del Monte Carlo, leídos por otro sitio."""
        import vertex_api as V

        fuente = inspect.getsource(V.compute_portfolio_stress)
        assert fuente.count("rng.integers(0, len(port_daily)") == 2

    def test_el_sizing_sale_del_capital_del_perfil(self):
        import vertex_api as V

        fuente = inspect.getsource(V.compute_portfolio_stress)
        assert "_perfil_leer()" in fuente
        assert "max_por_posicion_usd" in fuente
        assert '"origen"' in fuente

    def test_el_sizing_NO_tumba_el_stress_si_el_perfil_falla(self):
        """El sizing es contexto: un perfil ilegible no puede dejar sin VaR.

        Medido sobre el ÁRBOL, no sobre una ventana de caracteres. La versión
        anterior buscaba el `except` dentro de los 2.600 caracteres siguientes
        al `sizing = None`, y al crecer el bloque —el efectivo y el
        apalancamiento entraron ahí— el `except` se salió de la ventana y el
        caso cayó sin que nada estuviera mal. Una medida que depende del
        tamaño del código caduca sola.
        """
        import ast
        import textwrap

        import vertex_api as V

        arbol = ast.parse(textwrap.dedent(inspect.getsource(V.compute_portfolio_stress)))
        fn = arbol.body[0]
        # El `try` que sigue a `sizing = None`, en el mismo nivel.
        idx = next(i for i, n in enumerate(fn.body)
                   if isinstance(n, ast.Assign)
                   and getattr(n.targets[0], "id", None) == "sizing")
        siguiente = fn.body[idx + 1]
        assert isinstance(siguiente, ast.Try), type(siguiente).__name__
        assert any(h.type is None or getattr(h.type, "id", "") == "Exception"
                   for h in siguiente.handlers), "no atrapa un fallo del perfil"


# ══════════════════════════════════════════════════════════════════════════
class TestQuantDataYaNoSeAnunciaComoPrimario:
    """El proveedor salió del proyecto: su plan quedó inactivo y responde 403
    en todo. `_quantdata_request` ya cortaba sin clave, así que no salía a la
    red — pero seguía recorriendo tres capas de parseo en cada fallo de caché
    para llegar a un `None` sabido, y anunciándose como fuente primaria."""

    def test_la_llamada_esta_guardada(self):
        import vertex_api as V

        codigo = _solo_codigo(V.get_gex_cached)
        assert "_quantdata_ready()" in codigo
        # La llamada a QD vive DENTRO de la guarda, no al lado de ella.
        i_guarda = codigo.index("_quantdata_ready()")
        i_qd = codigo.index("_gex_from_quantdata")
        assert i_guarda < i_qd, "la guarda tiene que ir antes de la llamada"

    def test_sin_clave_NO_se_toca_quantdata(self, monkeypatch):
        import vertex_api as V

        llamadas = []
        monkeypatch.setattr(V, "QUANTDATA_API_KEY", "")
        monkeypatch.setattr(V, "_gex_from_quantdata",
                            lambda t: llamadas.append(t))
        monkeypatch.setattr(V, "compute_gex", lambda t: {"ok": True})
        V._GEX_CACHE.pop("ZZZZ", None)
        V.get_gex_cached("ZZZZ")
        assert llamadas == []

    def test_con_clave_SI_se_intenta(self, monkeypatch):
        """La capa no se quita: si Kevin vuelve a contratar el plan, funciona."""
        import vertex_api as V

        llamadas = []
        monkeypatch.setattr(V, "QUANTDATA_API_KEY", "una-clave")
        monkeypatch.setattr(V, "_gex_from_quantdata",
                            lambda t: (llamadas.append(t), None)[1])
        monkeypatch.setattr(V, "compute_gex", lambda t: {"ok": True})
        V._GEX_CACHE.pop("ZZZZ", None)
        V.get_gex_cached("ZZZZ")
        assert llamadas == ["ZZZZ"]


# ══════════════════════════════════════════════════════════════════════════
class TestElEfectivoSeGUARDANoSoloSeEnsenia:
    """`/api/portfolio` sacaba el cash de Plaid, lo pintaba como «Cash
    Disponible» y ahí moría: ni el import lo aceptaba, ni el snapshot lo
    guardaba, ni `_resolve_positions` lo devolvía. El motor de riesgo, el
    optimizador y la regla de tamaño calculaban como si el 100% de la cuenta
    estuviera invertido."""

    def test_no_saber_y_tener_cero_NO_son_lo_mismo(self):
        """La diferencia lleva a cuentas distintas, y presentar la primera
        como la segunda es la clase de silencio que este proyecto trata como
        peor que un error."""
        import vertex_api as V

        V.save_portfolio_cash(None)
        assert V.get_portfolio_cash() is None, "sin dato tiene que ser None"
        V.save_portfolio_cash(0.0)
        c = V.get_portfolio_cash()
        assert c is not None and c["efectivo"] == 0.0, "un cero declarado es un dato"
        V.save_portfolio_cash(None)

    def test_el_poder_de_compra_va_APARTE_del_efectivo(self):
        """Con margen es mayor; con colateral en puts vendidas, menor."""
        import vertex_api as V

        V.save_portfolio_cash(250.0, 900.0, "manual")
        c = V.get_portfolio_cash()
        assert c["efectivo"] == 250.0 and c["poder_de_compra"] == 900.0
        V.save_portfolio_cash(None)

    def test_el_import_lo_acepta_y_el_snapshot_lo_devuelve(self, client, cab):
        import vertex_api as V

        _libro(client, cab, [{"ticker": "NVDA", "name": "NVIDIA", "value": 600}], [])
        client.post("/api/portfolio/import", headers=cab, json={"cash": 250.0})
        d = client.get("/api/portfolio/snapshot", headers=cab).json()
        assert d["efectivo"]["efectivo"] == 250.0
        client.post("/api/portfolio/import", headers=cab, json={"cash": None})
        assert client.get("/api/portfolio/snapshot", headers=cab).json()["efectivo"] is None

    def test_borrar_el_libro_se_lleva_el_efectivo(self, client, cab):
        """Dejarlo detrás haría que la próxima cuenta arrastre el saldo de la
        anterior, y el sizing se calcularía sobre dinero de otro libro."""
        import vertex_api as V

        client.post("/api/portfolio/import", headers=cab,
                    json={"positions": [{"ticker": "NVDA", "value": 600}], "cash": 250.0})
        client.post("/api/portfolio/clear", headers=cab)
        assert V.get_portfolio_cash() is None

    def test_el_camino_de_Plaid_lo_PERSISTE(self):
        import inspect

        import vertex_api as V

        assert "save_portfolio_cash(safe_float(cash_balance)" in inspect.getsource(V.get_portfolio)

    def test_el_panel_entiende_la_linea_CASH(self):
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        i = html.index("function parseImportText")
        trozo = html[i:i + 1800]
        assert "'CASH', 'EFECTIVO'" in trozo
        assert "cuerpo.cash = cash" in html


class TestElApalancamientoRealSaleDeLosDosNumeros:
    """Ni el efectivo ni la exposición delta lo dicen por separado. Con $1.000
    en la cuenta y $12.000 de delta-equivalente estás a 12×, y ese número no
    aparecía en ninguna pantalla del sistema."""

    LIBRO = [{"ticker": "NVDA", "name": "NVIDIA", "value": 1000.0}]
    PERFIL = {"max_posicion_pct": [20, 30], "tolerancia": "agresivo",
              "capital": 1000.0, "sin_contestar": []}

    def test_un_libro_de_solo_acciones_con_efectivo_sale_por_debajo_de_1(self):
        import vertex_api as V

        V.save_portfolio_cash(250.0, 250.0, "test")
        try:
            g = V.compute_portfolio_guardrails(self.LIBRO, None, perfil=self.PERFIL)
            r = [x for x in g["rules"] if x["rule"] == "Apalancamiento real"][0]
            assert r["value"] == "0.8x"          # 1000 de exposición / 1250 de cuenta
            assert r["status"] == "ok"
        finally:
            V.save_portfolio_cash(None)

    def test_el_delta_de_las_opciones_lo_dispara(self):
        import vertex_api as V

        libro = [{"ticker": "NVDA", "name": "NVIDIA", "value": 12400.0,
                  "valor_equity": 400.0, "exposicion_opciones": 12000.0}]
        V.save_portfolio_cash(600.0, 600.0, "test")
        try:
            g = V.compute_portfolio_guardrails(libro, None, perfil=self.PERFIL)
            r = [x for x in g["rules"] if x["rule"] == "Apalancamiento real"][0]
            # cuenta = 12400 - 12000 + 600 = 1000; exposición = 12400 → 12,4x
            assert r["value"] == "12.4x"
            assert r["status"] == "breach"
            assert "delta de las" in r["detail"]
        finally:
            V.save_portfolio_cash(None)

    def test_con_opciones_y_SIN_efectivo_no_se_inventa_un_1x(self):
        """Decir «1,0x» sin saber el efectivo sería mentir en la dirección
        peligrosa: hacia abajo."""
        import vertex_api as V

        libro = [{"ticker": "NVDA", "name": "NVIDIA", "value": 12400.0,
                  "valor_equity": 400.0, "exposicion_opciones": 12000.0}]
        V.save_portfolio_cash(None)
        g = V.compute_portfolio_guardrails(libro, None, perfil=self.PERFIL)
        r = [x for x in g["rules"] if x["rule"] == "Apalancamiento real"][0]
        assert r["value"] == "—" and r["status"] == "warn"
        assert "no sé cuánto efectivo" in r["detail"]

    def test_sin_efectivo_y_SIN_opciones_la_regla_no_aparece(self):
        """No hay nada que avisar y una regla de más es ruido."""
        import vertex_api as V

        V.save_portfolio_cash(None)
        g = V.compute_portfolio_guardrails(self.LIBRO, None, perfil=self.PERFIL)
        assert not [x for x in g["rules"] if x["rule"] == "Apalancamiento real"]


class TestElSizingResponde_en_vez_de_recitar_un_tope:
    """Con $700 ya desplegados de $1.000, decir «$300 por posición» es cierto
    y no sirve de nada."""

    def test_el_stress_lee_el_efectivo(self):
        import inspect

        import vertex_api as V

        fuente = inspect.getsource(V.compute_portfolio_stress)
        assert "get_portfolio_cash()" in fuente
        assert "cabe_hoy_sin_vender_usd" in fuente
        assert "apalancamiento" in fuente

    def test_lo_que_cabe_hoy_es_el_MENOR_de_los_dos_limites(self):
        """El poder de compra y el tope por posición: manda el que ate más."""
        import inspect

        import vertex_api as V

        fuente = inspect.getsource(V.compute_portfolio_stress)
        assert "min(_pc, _cap * _tope / 100.0)" in fuente

    def test_sin_efectivo_lo_DICE_en_vez_de_callarse(self):
        import inspect

        import vertex_api as V

        fuente = inspect.getsource(V.compute_portfolio_stress)
        assert "No sé cuánto efectivo tienes" in fuente
        assert '"efectivo_conocido"' in fuente


class TestElOptimizadorDejaDeProponerSoloVENTAS:
    """Normaliza a 100% de lo invertido, así que solo sabe hablar en
    porcentajes de lo que ya está dentro — y en una cuenta chica eso siempre se
    traduce en «vende para rebalancear», que paga spread y comisión dos veces
    por algo que el efectivo resolvía gratis."""

    def test_la_ruta_adjunta_el_efectivo(self):
        import inspect

        import vertex_api as V

        fuente = inspect.getsource(V.get_portfolio_optimizer)
        assert "get_portfolio_cash()" in fuente
        assert '"peso_efectivo_pct"' in fuente

    def test_sin_efectivo_se_declara_en_vez_de_omitirse(self):
        import inspect

        import vertex_api as V

        fuente = inspect.getsource(V.get_portfolio_optimizer)
        assert '"conocido": False' in fuente
        assert "todo ajuste sale como una" in fuente


class TestElEdgeDelAgenteDeOPCIONESLlegaAlLibro:
    """`get_track_record` lee `SELECT * FROM reports` — la tabla del agente de
    ACCIONES. El de opciones guardaba sus predicciones en su propio store desde
    el primer día y nadie las cruzaba con el libro: el bucle de aprendizaje
    estaba cerrado para acciones y abierto justo para el agente con el que se
    opera."""

    def test_el_umbral_de_muestra_es_el_de_VICTOR(self):
        """5, el mismo con el que su motor decide si se auto-corrige. Dos
        umbrales distintos para la misma pregunta se separan solos."""
        import inspect

        import vertex_api as V
        from wbj.tito.prediction import CALIBRATION

        assert CALIBRATION["min_samples"] == 5
        fuente = inspect.getsource(V.portfolio_edge_opciones)
        assert 'CALIBRATION.get("min_samples")' in fuente
        assert "5" not in fuente.split('CALIBRATION.get("min_samples")')[0][-40:], (
            "el mínimo no puede ir a mano al lado de la constante")

    def test_solo_cuentan_las_predicciones_VENCIDAS(self):
        """Juzgar una a mitad de su horizonte mide ruido, no acierto."""
        import inspect

        import vertex_api as V

        fuente = inspect.getsource(V.portfolio_edge_opciones)
        assert "review_predictions" in fuente
        assert "matured_count" in fuente

    def test_sin_opciones_no_inventa_un_libro(self, client, cab):
        client.post("/api/portfolio/clear", headers=cab)
        _libro(client, cab, [{"ticker": "NVDA", "name": "NVIDIA", "value": 600}], [])
        d = client.get("/api/portfolio-edge-opciones", headers=cab).json()
        assert d["ok"] is True and d["n_options"] == 0
        assert d["subyacentes"] == []

    def test_sin_historial_lo_dice_y_explica_como_se_llena(self, client, cab):
        largo = (date.today() + timedelta(days=115)).isoformat()
        _libro(client, cab, [{"ticker": "NVDA", "name": "NVIDIA", "value": 600}],
               [{"underlying": "ZZZZ", "option_type": "call", "strike": 200,
                 "expiry": largo, "contracts": 1}])
        d = client.get("/api/portfolio-edge-opciones", headers=cab).json()
        f = [x for x in d["subyacentes"] if x["underlying"] == "ZZZZ"][0]
        assert f["estado"] in ("sin_historial", "sin_precios")
        assert f.get("lectura") or f.get("error")

    def test_una_muestra_corta_NO_se_presenta_como_ventaja(self, monkeypatch, client, cab):
        """4 predicciones acertadas de 4 es un 100% que no significa nada."""
        import vertex_api as V
        from wbj.tito import stores as st

        largo = (date.today() + timedelta(days=115)).isoformat()
        _libro(client, cab, [], [{"underlying": "ZZZZ", "option_type": "call",
                                  "strike": 200, "expiry": largo, "contracts": 1}])
        monkeypatch.setattr(st, "load_journal", lambda t: [{"date": "2026-01-01"}])
        monkeypatch.setattr("wbj.tito.bars_store.daily_bars_for_panel", lambda t: [1])
        monkeypatch.setattr(st, "review_predictions",
                            lambda j, b, n: {"matured_count": 4, "direction_hit_rate": 100.0,
                                             "base_touch_rate": 100.0,
                                             "mean_abs_error_pct": 1.0, "bias_pct": 0.0,
                                             "best_counts": {"base": 4}})
        d = client.get("/api/portfolio-edge-opciones", headers=cab).json()
        f = [x for x in d["subyacentes"] if x["underlying"] == "ZZZZ"][0]
        assert f["estado"] == "muestra_corta", f
        assert "no ventaja" in f["lectura"] or "significa algo" in f["lectura"]

    def test_con_muestra_suficiente_SI_clasifica(self, monkeypatch, client, cab):
        """La contraparte: si nunca saliera «con ventaja», el caso de arriba
        pasaría con un `estado` clavado y nadie lo notaría."""
        import vertex_api as V
        from wbj.tito import stores as st

        largo = (date.today() + timedelta(days=115)).isoformat()
        _libro(client, cab, [], [{"underlying": "ZZZZ", "option_type": "call",
                                  "strike": 200, "expiry": largo, "contracts": 1}])
        monkeypatch.setattr(st, "load_journal", lambda t: [{"date": "2026-01-01"}])
        monkeypatch.setattr("wbj.tito.bars_store.daily_bars_for_panel", lambda t: [1])
        for hit, esperado in ((72.0, "con_ventaja"), (50.0, "sin_ventaja_clara"),
                              (30.0, "por_debajo_del_azar")):
            monkeypatch.setattr(st, "review_predictions",
                                lambda j, b, n, _h=hit: {"matured_count": 12,
                                                         "direction_hit_rate": _h,
                                                         "base_touch_rate": 40.0,
                                                         "mean_abs_error_pct": 5.0,
                                                         "bias_pct": 1.0,
                                                         "best_counts": {"base": 12}})
            d = client.get("/api/portfolio-edge-opciones", headers=cab).json()
            f = [x for x in d["subyacentes"] if x["underlying"] == "ZZZZ"][0]
            assert f["estado"] == esperado, (hit, f["estado"])

    def test_NO_puntua_ni_recomienda(self):
        import vertex_api as V

        codigo = _solo_codigo(V.portfolio_edge_opciones)
        for prohibido in ("run_scorecard", "predict_pro", "verdict"):
            assert prohibido not in codigo, prohibido
