"""Cuentas, cuestionario, perfil por usuario y aprendizaje compartido.

Lo que se vigila aquí, en una frase por bloque:

- **Cuentas**: que la contraseña no esté en claro en ninguna parte, que el login
  no sirva de directorio de emails y que una cuenta sirva desde otro dispositivo.
- **Cuestionario**: que las preguntas salgan de `Kevin.md`, que lo no contestado
  herede su respuesta, y que el sistema DIGA cuánto heredaste.
- **Perfil por usuario**: que el capital de una persona no se le cuente a otra —
  ni en Proyecciones, ni en el prompt del agente de acciones, ni en el
  especialista de riesgo del engine.
- **Privacidad**: que tu archivo de reportes sea tuyo, y que lo compartido sean
  las series, no los análisis.

    python -m pytest tests_vertex/test_cuentas_y_perfil.py -q
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "engine"))

pytest.importorskip("fastapi", reason="requiere las deps de vertex_api")


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    """Una base y un directorio de perfiles limpios por test.

    Sin esto, la suite reescribiría el perfil real de Kevin — que es justo el
    archivo del que dependen los otros dos agentes — y las cuentas de un test
    se colarían en el siguiente.
    """
    import vertex_api as V

    db = tmp_path / "test.db"
    monkeypatch.setattr(V, "DB_PATH", str(db))
    monkeypatch.setattr(V, "_PERFIL_DIR", str(tmp_path / "Perfil Inversionista"))
    os.makedirs(tmp_path / "Perfil Inversionista" / "usuarios", exist_ok=True)

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS reports (
        report_id TEXT PRIMARY KEY, ticker TEXT, created_at TEXT, created_ts REAL,
        payload TEXT, usuario_id TEXT)""")
    V._CU.crear_tablas(conn)
    conn.commit()
    conn.close()

    # El contexto de petición no puede sobrevivir de un test a otro: sería un
    # usuario de un test contestándole al siguiente. Se restaura con el token
    # que devuelve `set`, que es la forma que tiene `contextvars` de deshacer.
    token = V._USUARIO_CTX.set(None)
    yield tmp_path
    V._USUARIO_CTX.reset(token)


@pytest.fixture
def cliente(entorno):
    """Un navegador nuevo, sin sesión."""
    from fastapi.testclient import TestClient
    import vertex_api as V

    return TestClient(V.app)


def _registra(cliente, email="kevin@x.com", nombre="Kevin", password="clave-larga-1"):
    return cliente.post("/api/auth/registro",
                        json={"email": email, "nombre": nombre, "password": password}).json()


# ═══════════════════════════════════════════════════════════════════════════
#  CUENTAS
# ═══════════════════════════════════════════════════════════════════════════

class TestCuentas:

    def test_la_contrasena_NUNCA_se_guarda_en_claro(self, cliente, entorno):
        """El fallo que había: `localStorage` con `{email, name, password}` en
        texto plano, marcado en el propio código como «demo only». Cualquiera
        con la consola del navegador abierta leía la contraseña."""
        _registra(cliente, password="mi-contraseña-secreta")
        conn = sqlite3.connect(entorno / "test.db")
        fila = conn.execute("SELECT pass_hash FROM usuarios").fetchone()[0]
        conn.close()
        assert "mi-contraseña-secreta" not in fila
        assert fila.startswith("pbkdf2_sha256$"), "no es un hash con sal e iteraciones"
        # La sal es por usuario: dos personas con la misma contraseña no pueden
        # compartir hash, o una tabla arcoíris valdría para las dos.
        cliente2_hash = None
        _registra(cliente, email="otro@x.com", nombre="Otro",
                  password="mi-contraseña-secreta")
        conn = sqlite3.connect(entorno / "test.db")
        hashes = [r[0] for r in conn.execute("SELECT pass_hash FROM usuarios").fetchall()]
        conn.close()
        assert len(set(hashes)) == 2, "misma contraseña → mismo hash: falta la sal"

    def test_el_hash_se_verifica_y_una_contrasena_mala_no_pasa(self):
        import vertex_cuentas as C

        h = C.hash_password("correcta-y-larga")
        assert C.verificar_password("correcta-y-larga", h) is True
        assert C.verificar_password("otra-cosa", h) is False
        # Un hash corrupto es un `False`, no un 500 que le diga al atacante que
        # dio con algo raro.
        assert C.verificar_password("x", "basura") is False
        assert C.verificar_password("x", "") is False

    def test_el_login_no_es_un_directorio_de_emails(self, cliente):
        """«No existe» y «contraseña mala» dan el MISMO mensaje. Distinguirlos
        deja averiguar qué emails tienen cuenta probando de uno en uno."""
        _registra(cliente)
        a = cliente.post("/api/auth/entrar",
                         json={"email": "kevin@x.com", "password": "mala"}).json()
        b = cliente.post("/api/auth/entrar",
                         json={"email": "nadie@x.com", "password": "mala"}).json()
        assert a["error"] == b["error"]

    def test_la_cuenta_sirve_desde_otro_dispositivo(self, entorno):
        """Lo que el login de `localStorage` no podía hacer: la cuenta existía
        solo en aquel navegador."""
        from fastapi.testclient import TestClient
        import vertex_api as V

        portatil, movil = TestClient(V.app), TestClient(V.app)
        _registra(portatil)
        assert movil.get("/api/auth/yo").json()["usuario"] is None
        r = movil.post("/api/auth/entrar",
                       json={"email": "kevin@x.com", "password": "clave-larga-1"})
        assert r.status_code == 200 and r.json()["ok"] is True
        assert movil.get("/api/auth/yo").json()["usuario"]["email"] == "kevin@x.com"

    def test_no_se_puede_registrar_dos_veces_el_mismo_email(self, cliente):
        _registra(cliente)
        r = _registra(cliente, nombre="Impostor", password="otra-clave-9")
        assert r["ok"] is False and "existe" in r["error"].lower()
        # Y el email se normaliza: mayúsculas y espacios no crean una segunda
        # cuenta que luego no podría iniciar sesión.
        r = _registra(cliente, email="  KEVIN@X.COM ", password="otra-clave-9")
        assert r["ok"] is False

    def test_rechaza_credenciales_que_no_valen(self, cliente):
        assert _registra(cliente, password="corta")["ok"] is False
        assert _registra(cliente, email="no-es-un-email")["ok"] is False
        assert _registra(cliente, nombre="   ")["ok"] is False

    def test_salir_cierra_la_sesion_EN_EL_SERVIDOR(self, cliente, entorno):
        """Borrar la cookie y dejar la fila viva deja el token válido para
        siempre en cualquier sitio donde se hubiera copiado."""
        _registra(cliente)
        conn = sqlite3.connect(entorno / "test.db")
        antes = conn.execute("SELECT COUNT(*) FROM sesiones").fetchone()[0]
        conn.close()
        assert antes == 1

        cliente.post("/api/auth/salir")
        conn = sqlite3.connect(entorno / "test.db")
        despues = conn.execute("SELECT COUNT(*) FROM sesiones").fetchone()[0]
        conn.close()
        assert despues == 0, "la sesión sigue viva en la base"
        assert cliente.get("/api/auth/yo").json()["usuario"] is None

    def test_el_token_de_sesion_no_esta_en_disco(self, cliente, entorno):
        """En la base va solo su SHA-256. Llevarse la base no es llevarse
        sesiones vivas: del hash no se reconstruye el token."""
        _registra(cliente)
        token = cliente.cookies.get("vertex_usuario")
        conn = sqlite3.connect(entorno / "test.db")
        guardado = conn.execute("SELECT token_hash FROM sesiones").fetchone()[0]
        conn.close()
        assert token and guardado != token
        assert len(guardado) == 64                # sha256 en hex

    def test_la_cookie_no_la_puede_leer_el_javascript(self, cliente):
        """HttpOnly: un XSS no se lleva la sesión. SameSite=Strict: la defensa
        contra CSRF."""
        r = cliente.post("/api/auth/registro",
                         json={"email": "k@x.com", "nombre": "K", "password": "clave-larga-1"})
        cookie = r.headers.get("set-cookie", "")
        assert "httponly" in cookie.lower()
        assert "samesite=strict" in cookie.lower().replace(" ", "")


# ═══════════════════════════════════════════════════════════════════════════
#  EL CUESTIONARIO
# ═══════════════════════════════════════════════════════════════════════════

class TestCuestionario:

    def test_las_preguntas_cubren_las_secciones_de_Kevin(self):
        """No es una lista inventada: cada apartado que Kevin contestó en
        prosa tiene aquí su pregunta."""
        import vertex_cuentas as C

        secciones = {p["seccion"] for p in C.PREGUNTAS}
        for esperada in ("Objetivos", "Tolerancia al riesgo", "Instrumentos",
                         "Universo", "Capital", "Reglas de dimensionamiento",
                         "Qué espero del sistema"):
            assert esperada in secciones, f"falta la sección «{esperada}» de Kevin.md"

    def test_cada_pregunta_trae_su_default_y_su_ayuda(self):
        import vertex_cuentas as C

        for p in C.PREGUNTAS:
            assert p.get("ayuda"), f"«{p['id']}» no explica para qué sirve"
            assert "defecto" in p, f"«{p['id']}» no tiene valor por defecto"
            if p["tipo"] in ("opcion", "multi"):
                validos = {o["valor"] for o in p["opciones"]}
                defecto = p["defecto"] if isinstance(p["defecto"], list) else [p["defecto"]]
                assert set(defecto) <= validos, f"el default de «{p['id']}» no es una opción"

    def test_el_perfil_por_defecto_se_construye_DESDE_las_preguntas(self):
        """Una segunda copia se desincronizaría con el primer cambio, y el
        default que se enseña y el que se guarda serían distintos."""
        import vertex_cuentas as C

        d = C.perfil_por_defecto()
        for p in C.PREGUNTAS:
            assert p["campo"] in d, f"el campo de «{p['id']}» no está en el perfil"
            assert d[p["campo"]] == p["defecto"]

    def test_lo_que_no_contestas_hereda_el_valor_de_Kevin(self, cliente):
        _registra(cliente)
        cliente.post("/api/perfil", json={"respuestas": {"capital": 5000}})
        d = cliente.get("/api/perfil").json()
        assert d["perfil"]["capital"] == 5000, "lo contestado no se guardó"
        assert d["perfil"]["tolerancia"] == "agresivo", "no heredó el default"
        assert d["perfil"]["mercados"] == ["EE.UU."]

    def test_el_sistema_DICE_cuanto_heredaste(self, cliente):
        """Un perfil heredado presentado como propio hace que el reporte hable
        con una confianza que no tiene."""
        import vertex_cuentas as C

        _registra(cliente)
        d = cliente.get("/api/perfil").json()
        assert d["progreso"]["contestadas"] == 0
        assert d["progreso"]["total"] == len(C.PREGUNTAS)
        assert len(d["progreso"]["sin_contestar"]) == len(C.PREGUNTAS)

        cliente.post("/api/perfil", json={"respuestas": {"capital": 5000,
                                                         "tolerancia": "moderado"}})
        d = cliente.get("/api/perfil").json()
        assert d["progreso"]["contestadas"] == 2
        assert "capital" not in d["progreso"]["sin_contestar"]
        assert "objetivos" in d["progreso"]["sin_contestar"]

    def test_el_md_declara_las_preguntas_pendientes(self):
        """El `.md` acaba en el prompt del agente. Si no dice qué es heredado,
        el agente habla del perfil de Kevin creyendo que es el tuyo."""
        import vertex_cuentas as C

        p = C.perfil_por_defecto()
        md = C.perfil_a_markdown(p)
        assert "sin contestar" in md.lower()

        contestado, err = C.perfil_desde_respuestas(
            {q["id"]: q["defecto"] for q in C.PREGUNTAS})
        assert not err
        assert "sin contestar" not in C.perfil_a_markdown(contestado).lower()

    def test_una_respuesta_invalida_aborta_el_guardado_ENTERO(self, cliente):
        """Un perfil a medias es peor que uno viejo: nadie sabría qué parte
        es suya."""
        _registra(cliente)
        cliente.post("/api/perfil", json={"respuestas": {"capital": 5000}})
        r = cliente.post("/api/perfil", json={"respuestas": {
            "tolerancia": "moderado", "horizonte": "cuando me jubile"}})
        assert r.json()["ok"] is False
        d = cliente.get("/api/perfil").json()["perfil"]
        assert d["tolerancia"] == "agresivo", "guardó la mitad buena de un envío inválido"

    def test_rechaza_basura_de_cada_tipo(self, cliente):
        _registra(cliente)
        for malo in ({"capital": "mucho"}, {"tolerancia": "kamikaze"},
                     {"objetivos": "crecimiento"}, {"objetivos": ["hacerme_rico"]},
                     {"max_posicion_pct": [90, 10]}, {"pregunta_inventada": 1}):
            r = cliente.post("/api/perfil", json={"respuestas": malo}).json()
            assert r["ok"] is False, f"aceptó basura: {malo}"

    def test_sin_sesion_no_se_puede_guardar_pero_si_leer(self, cliente):
        """Sin cuenta no hay dónde guardar. Se dice con un 401 en vez de fingir
        que se guardó, y el cuestionario se puede leer para poder enseñarlo."""
        assert cliente.get("/api/perfil").json()["editable"] is False
        assert cliente.get("/api/perfil").json()["preguntas"]
        assert cliente.post("/api/perfil",
                            json={"respuestas": {"capital": 1}}).status_code == 401


# ═══════════════════════════════════════════════════════════════════════════
#  UN PERFIL POR USUARIO
# ═══════════════════════════════════════════════════════════════════════════

class TestPerfilPorUsuario:

    @staticmethod
    def _dos(entorno):
        from fastapi.testclient import TestClient
        import vertex_api as V

        kevin, ana = TestClient(V.app), TestClient(V.app)
        _registra(kevin, "k@x.com", "Kevin", "clave-larga-1")
        _registra(ana, "a@x.com", "Ana", "otra-clave-2")
        kevin.post("/api/perfil", json={"respuestas": {
            "capital": 1000, "tolerancia": "agresivo", "max_posicion_pct": [20, 30]}})
        ana.post("/api/perfil", json={"respuestas": {
            "capital": 250000, "tolerancia": "conservador", "horizonte": "5+ años",
            "max_posicion_pct": [5, 10]}})
        return kevin, ana

    def test_el_capital_de_uno_no_se_le_cuenta_al_otro(self, entorno):
        kevin, ana = self._dos(entorno)
        assert kevin.get("/api/perfil").json()["perfil"]["capital"] == 1000
        assert ana.get("/api/perfil").json()["perfil"]["capital"] == 250000

    def test_cada_uno_tiene_su_md(self, entorno):
        kevin, ana = self._dos(entorno)
        dir_u = entorno / "Perfil Inversionista" / "usuarios"
        archivos = sorted(dir_u.iterdir())
        assert len(archivos) == 2, "los dos perfiles se escribieron en el mismo archivo"
        textos = {f.name: f.read_text(encoding="utf-8") for f in archivos}
        assert any("$1,000" in t for t in textos.values())
        assert any("$250,000" in t for t in textos.values())
        # El id va en el nombre: dos personas llamadas Kevin no se pisan.
        assert all("-" in f.name for f in archivos)

    def test_el_prompt_del_agente_de_acciones_recibe_TU_perfil(self, entorno):
        """`_load_investor_profile()` es lo que Analyze y Explore ya llamaban.
        Lo único que cambió es CUÁL archivo resuelve."""
        import vertex_api as V

        kevin, ana = self._dos(entorno)
        vistos = {}
        for cli, quien in ((kevin, "kevin"), (ana, "ana")):
            conn = V._db()
            u = V._CU.usuario_de_sesion(conn, cli.cookies.get("vertex_usuario"))
            conn.close()
            V._USUARIO_CTX.set(u)
            nombre, texto = V._load_investor_profile()
            vistos[quien] = (nombre, texto)
        V._USUARIO_CTX.set(None)

        assert vistos["kevin"][0] == "Kevin" and vistos["ana"][0] == "Ana"
        assert "$1,000" in vistos["kevin"][1] and "$250,000" in vistos["ana"][1]
        assert "$250,000" not in vistos["kevin"][1]

    def test_el_especialista_de_riesgo_del_engine_usa_TU_perfil(self, entorno):
        """El fallo silencioso: `risk.PROFILE` se resuelve al IMPORTAR, así que
        sin esto le contaba a cada usuario su análisis con el capital, el
        horizonte y el tope de posición de Kevin.

        La misma posición del 25% es válida para uno e incumplimiento para el
        otro. Si los dos veredictos salen iguales, el perfil no llegó.
        """
        import vertex_api as V
        import wbj.specialists.risk as R

        kevin, ana = self._dos(entorno)
        veredictos = {}
        for cli, quien in ((kevin, "kevin"), (ana, "ana")):
            conn = V._db()
            u = V._CU.usuario_de_sesion(conn, cli.cookies.get("vertex_usuario"))
            conn.close()
            V._USUARIO_CTX.set(u)
            R.PERFIL_ACTUAL.set(V._perfil_para_el_engine())
            veredictos[quien] = R.profile_fit(0.25)
        V._USUARIO_CTX.set(None)
        R.PERFIL_ACTUAL.set(None)

        assert veredictos["kevin"]["capital_usd"] == 1000
        assert veredictos["ana"]["capital_usd"] == 250000
        assert veredictos["kevin"]["within_position_cap"] is True    # tope 30%
        assert veredictos["ana"]["within_position_cap"] is False     # tope 10%
        assert veredictos["ana"]["horizon_years_range"] == [5, 10]

    def test_sin_sesion_cae_al_perfil_de_referencia(self, entorno):
        """Scripts, cron y el preflight no tienen cookie. No pueden quedarse sin
        perfil: caen al de Kevin, que es el archivo de referencia."""
        import vertex_api as V

        (entorno / "Perfil Inversionista" / "Kevin.md").write_text(
            "# Kevin\n\nCapital ~$1,000 USD. Horizonte de 1 a 3 años.\n", encoding="utf-8")
        V._USUARIO_CTX.set(None)
        nombre, texto = V._load_investor_profile()
        assert nombre == "Kevin" and "$1,000" in texto

    def test_el_editor_y_el_lector_usan_EL_MISMO_directorio(self):
        """Estuvieron calculándolo cada uno por su cuenta, y se separaron: el
        editor escribía en un sitio y el agente leía de otro, sin que nada
        fallara porque el archivo viejo seguía existiendo."""
        import inspect

        import vertex_api as V

        fuente = inspect.getsource(V._load_investor_profile)
        assert "_PERFIL_DIR" in fuente
        assert 'os.path.join(os.path.dirname(os.path.abspath(__file__)), "Perfil' not in fuente


# ═══════════════════════════════════════════════════════════════════════════
#  EL MARKDOWN SIGUE SIENDO LEGIBLE PARA EL ENGINE
# ═══════════════════════════════════════════════════════════════════════════

class TestElMdSigueSiendoLegibleParaElEngine:
    """El eslabón frágil, y el único que falla EN SILENCIO.

    `engine/wbj/specialists/risk.py::_load_profile` no lee el JSON: parsea el
    `.md` con tres regex. Estos tests corren la función REAL sobre el markdown
    REAL — no una copia del regex, que se actualizaría sola y dejaría de
    proteger nada.
    """

    @staticmethod
    def _parsea(md, tmp_path, monkeypatch):
        import wbj.specialists.risk as R

        raiz = tmp_path / "arbol" / "engine" / "wbj" / "specialists"
        raiz.mkdir(parents=True, exist_ok=True)
        (tmp_path / "arbol" / "Perfil Inversionista").mkdir(parents=True, exist_ok=True)
        (tmp_path / "arbol" / "Perfil Inversionista" / "Kevin.md").write_text(
            md, encoding="utf-8")
        monkeypatch.setattr(R, "__file__", str(raiz / "risk.py"))
        return R._load_profile()

    def test_el_md_del_cuestionario_conserva_los_tres_campos(self, tmp_path, monkeypatch):
        import vertex_cuentas as C

        p, err = C.perfil_desde_respuestas({"capital": 1000, "horizonte": "1-3 años",
                                            "max_posicion_pct": [20, 30]})
        assert not err
        r = self._parsea(C.perfil_a_markdown(p), tmp_path, monkeypatch)
        assert r["fields_defaulted"] == [], (
            f"el especialista cayó a valores por defecto: {r['fields_defaulted']}")
        assert r["capital_usd"] == 1000
        assert r["horizon_years"] == (1, 3)
        assert r["max_position_pct"] == (0.20, 0.30)

    def test_el_capital_es_el_PRIMER_dolar_del_documento(self, tmp_path, monkeypatch):
        """El fallo real que esto cazó: la sección de Tolerancia iba delante y
        su «riesgo máximo por operación ($150)» era el primer `$`. El
        especialista concluía que la cuenta entera eran $150."""
        import vertex_cuentas as C

        p, _ = C.perfil_desde_respuestas({"capital": 1000, "tolerancia": "agresivo"})
        md = C.perfil_a_markdown(p)
        assert md.index("$1,000") < md.index("$150"), (
            "el riesgo por operación aparece antes que el capital")
        assert self._parsea(md, tmp_path, monkeypatch)["capital_usd"] == 1000

    @pytest.mark.parametrize("horizonte,esperado", [
        ("días", (0, 1)), ("semanas a meses", (0, 1)),
        ("1-3 años", (1, 3)), ("5+ años", (5, 10)),
    ])
    def test_cada_horizonte_llega_ENTERO_al_especialista(self, horizonte, esperado,
                                                         tmp_path, monkeypatch):
        """El otro fallo real: el rango en años estuvo DERIVADO de los días, y
        «5+ años» acababa impreso como «1 a 3 años». El especialista reportaba
        un horizonte que el inversionista nunca eligió."""
        import vertex_cuentas as C

        p, err = C.perfil_desde_respuestas({"horizonte": horizonte})
        assert not err
        r = self._parsea(C.perfil_a_markdown(p), tmp_path, monkeypatch)
        assert r["horizon_years"] == esperado
        assert "horizon_years" not in r["fields_defaulted"]

    def test_el_texto_del_inversionista_no_desplaza_al_capital(self, tmp_path, monkeypatch):
        """Alguien escribe «perdí $50,000 en 2022» en el texto libre. Si ese
        importe quedara antes que el capital, sería el que el engine leyera."""
        import vertex_cuentas as C

        p, _ = C.perfil_desde_respuestas({"capital": 3000,
                                          "texto": "Perdí $50,000 en 2022."})
        md = C.perfil_a_markdown(p)
        assert md.index("$3,000") < md.index("$50,000")
        assert self._parsea(md, tmp_path, monkeypatch)["capital_usd"] == 3000


# ═══════════════════════════════════════════════════════════════════════════
#  ARCHIVO PRIVADO · APRENDIZAJE COMPARTIDO
# ═══════════════════════════════════════════════════════════════════════════

class TestPrivacidadYAprendizaje:

    @staticmethod
    def _con_reportes(entorno):
        from fastapi.testclient import TestClient
        import vertex_api as V

        kevin, ana = TestClient(V.app), TestClient(V.app)
        _registra(kevin, "k@x.com", "Kevin", "clave-larga-1")
        _registra(ana, "a@x.com", "Ana", "otra-clave-2")
        conn = V._db()
        for email, tk, rid in (("k@x.com", "AAPL", "r-k"), ("a@x.com", "JPM", "r-a")):
            uid = V._CU.buscar_usuario(conn, email=email)["id"]
            conn.execute("INSERT INTO reports (report_id,ticker,created_at,created_ts,"
                         "payload,usuario_id) VALUES (?,?,?,?,?,?)",
                         (rid, tk, "hoy", time.time(), json.dumps({"ticker": tk}), uid))
            V._CU.registrar_contribucion(conn, "acciones", tk, uid)
        conn.commit()
        conn.close()
        return kevin, ana

    def test_tu_archivo_de_reportes_es_TUYO(self, entorno):
        """Devolvía TODOS los reportes a CUALQUIERA. Con un solo usuario era lo
        mismo que devolver los suyos; con cuentas, es el análisis de una persona
        leído por las demás."""
        kevin, ana = self._con_reportes(entorno)
        assert [r["ticker"] for r in kevin.get("/api/reports/list").json()["reports"]] == ["AAPL"]
        assert [r["ticker"] for r in ana.get("/api/reports/list").json()["reports"]] == ["JPM"]

    def test_no_puedes_borrar_el_reporte_de_otro(self, entorno):
        """Los `report_id` llevan ticker y fecha: adivinarlos no es difícil."""
        kevin, ana = self._con_reportes(entorno)
        r = ana.post("/api/report-delete?report_id=r-k").json()
        assert r["borrado"] is False
        assert [x["ticker"] for x in kevin.get("/api/reports/list").json()["reports"]] == ["AAPL"]
        # Y lo tuyo sí se borra.
        assert ana.post("/api/report-delete?report_id=r-a").json()["borrado"] is True

    def test_el_analisis_de_cada_quien_alimenta_al_pool(self, entorno):
        kevin, ana = self._con_reportes(entorno)
        d = ana.get("/api/aprendizaje").json()
        assert d["agentes"]["acciones"]["analisis"] == 2
        assert d["agentes"]["acciones"]["personas"] == 2
        assert d["tuyo"]["acciones"]["analisis"] == 1, "no distingue lo tuyo del total"

    def test_el_pool_NO_dice_quien_analizo_que(self, entorno):
        """Alimentar al agente y publicar tu trabajo no son lo mismo."""
        kevin, ana = self._con_reportes(entorno)
        crudo = json.dumps(ana.get("/api/aprendizaje").json())
        for filtrado in ("AAPL", "k@x.com", "Kevin"):
            assert filtrado not in crudo, f"el pool filtró «{filtrado}»"

    def test_los_dos_agentes_aprenden_DISTINTO_y_se_cuentan_aparte(self, entorno):
        """Contarlos juntos escondería justo lo que los diferencia: uno cierra
        un lazo de calibración, el otro acumula una serie que nadie vende."""
        kevin, _ = self._con_reportes(entorno)
        d = kevin.get("/api/aprendizaje").json()
        assert set(d["agentes"]) == {"acciones", "opciones"}
        assert d["agentes"]["acciones"]["como_aprende"] == "calibración"
        assert d["agentes"]["opciones"]["como_aprende"] == "acumulación hacia adelante"
        for a in d["agentes"].values():
            assert a["explicacion"], "no explica cómo aprende"
            assert "listo" in a, "no dice si ya tiene bastante"

    def test_el_umbral_de_IV_sale_del_motor_no_de_una_copia(self, entorno):
        """Un número copiado aquí diría «ya está listo» mientras el motor sigue
        usando el proxy de volatilidad realizada."""
        from wbj.tito.ivcontext import MIN_IV_HISTORY_DAYS

        kevin, _ = self._con_reportes(entorno)
        d = kevin.get("/api/aprendizaje").json()
        assert d["agentes"]["opciones"]["dias_necesarios"] == MIN_IV_HISTORY_DAYS

    def test_dice_sin_ambiguedad_que_se_comparte_y_que_no(self, entorno):
        kevin, _ = self._con_reportes(entorno)
        p = kevin.get("/api/aprendizaje").json()["privacidad"]
        assert p["compartido"] and p["privado"] and p["nunca"]

    def test_analizar_un_ticker_YA_alimenta_al_agente_de_opciones(self, entorno):
        """Su forma de aprender no necesita que salga un reporte: la foto de
        hoy es lo que hará posible el IV Rank de dentro de un año."""
        from fastapi.testclient import TestClient
        import vertex_api as V

        cli = TestClient(V.app)
        _registra(cli)
        conn = V._db()
        uid = V._CU.buscar_usuario(conn, email="kevin@x.com")["id"]
        V._CU.registrar_contribucion(conn, "opciones", "TSLA", uid)
        conn.close()
        d = cli.get("/api/aprendizaje").json()
        assert d["agentes"]["opciones"]["analisis"] == 1
        assert d["tuyo"]["opciones"]["tickers"] == 1


# ═══════════════════════════════════════════════════════════════════════════
#  LA PANTALLA
#
#  Estos leen el HTML como TEXTO: comprueban que algo existe y que alguien lo
#  llama, no que funcione. Lo que se EJECUTA lo prueba
#  `engine/scripts/_smoke_perfil.mjs`, que corre el JS vivo contra un DOM.
# ═══════════════════════════════════════════════════════════════════════════

def _html():
    return (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")


def _sin_comentarios(js):
    import re
    js = re.sub(r"/\*[\s\S]*?\*/", "", js)
    return re.sub(r"^\s*//.*$", "", js, flags=re.M)


class TestLaPantallaDeCuentas:

    def test_ya_no_queda_una_base_de_usuarios_en_el_navegador(self):
        """El fallo: `vertex_users_db` en localStorage, con las contraseñas en
        texto plano y el propio código diciendo «demo only»."""
        h = _sin_comentarios(_html())
        assert "vertex_users_db" not in h
        assert "AUTH_USERS_KEY" not in h
        assert "authGetUsers" not in h and "authSaveUsers" not in h

    def test_no_se_PERSISTE_ninguna_contrasena_ni_token_en_el_navegador(self):
        """La contraseña puede estar en el CUERPO de la petición —ahí es donde
        va— pero no puede quedarse guardada en ningún sitio del navegador. Y la
        cookie de sesión es HttpOnly: el JavaScript no puede leerla, que es la
        gracia; guardar un token aquí sería deshacerlo."""
        h = _html()
        assert "user.password" not in h, "compara contraseñas en el navegador"
        assert "AUTH_SESSION_KEY" not in h
        # Nada relacionado con credenciales entra en localStorage.
        import re
        for m in re.findall(r"localStorage\.setItem\(([^,)]+)", h):
            assert not re.search(r"pass|token|sesi|session|auth", m, re.I), \
                f"se persiste algo de credenciales: {m}"

    def test_el_login_habla_con_el_servidor(self):
        h = _sin_comentarios(_html())
        for ruta in ("/api/auth/registro", "/api/auth/entrar",
                     "/api/auth/salir", "/api/auth/yo"):
            assert f"API_BASE}}{ruta}" in h, f"la pantalla no llama a {ruta}"

    def test_quien_esta_dentro_lo_dice_el_servidor(self):
        """No se puede leer de localStorage: la cookie es HttpOnly, así que la
        única forma de saberlo es preguntar. Es también lo que hace que la
        sesión sobreviva a cambiar de dispositivo."""
        # Sobre el HTML CRUDO: `_sin_comentarios` aplicado al archivo entero es
        # destructivo — un `/*` dentro de una cadena se come el resto. Aquí se
        # busca estructura, no ausencia, así que el crudo vale.
        h = _html()
        assert "async function authQuienSoy()" in h
        arranque = h[h.index("document.addEventListener('DOMContentLoaded', async () =>"):]
        arranque = arranque[:arranque.index("\n});")]
        assert "authQuienSoy()" in arranque, "el arranque no pregunta quién está dentro"
        assert "authGetSession()" not in arranque, "sigue leyendo la sesión de localStorage"

    def test_salir_cierra_la_sesion_en_el_servidor(self):
        h = _sin_comentarios(_html())
        fn = h[h.index("async function authSignOut()"):]
        fn = fn[:fn.index("\n}")]
        assert "/api/auth/salir" in fn

    def test_al_salir_se_limpia_el_archivo_local(self):
        """El archivo es por usuario. Sin limpiarlo, el siguiente que entre en
        este navegador vería los reportes del anterior."""
        h = _sin_comentarios(_html())
        fn = h[h.index("async function authSignOut()"):]
        fn = fn[:fn.index("\n}")]
        assert "localStorage.removeItem(STORAGE_KEY)" in fn

    def test_una_cuenta_nueva_va_derecha_al_cuestionario(self):
        """Un perfil vacío no es neutral: hereda el de Kevin entero, y el
        agente recomendaría con el capital de otra persona."""
        h = _sin_comentarios(_html())
        fn = h[h.index("async function authLogin("):]
        fn = fn[:fn.index("\n}")]
        assert "switchView('perfilView')" in fn and "nuevo" in fn


class TestLaPantallaDelCuestionario:

    def test_las_preguntas_NO_estan_escritas_en_el_html(self):
        """Vienen de `/api/perfil`. Una copia aquí se desincronizaría con la
        primera pregunta que se añadiera, y el formulario preguntaría una cosa
        mientras el servidor guarda otra."""
        h = _sin_comentarios(_html())
        assert "VX_PREGUNTAS" in h
        cargar = h[h.index("async function pfCargar()"):]
        cargar = cargar[:cargar.index("\n}")]
        assert "d.preguntas" in cargar
        # Ni un enunciado a mano.
        assert "¿Qué buscas con este dinero?" not in h

    def test_pinta_cada_tipo_de_pregunta(self):
        h = _sin_comentarios(_html())
        fn = h[h.index("function pfPreguntaHTML(preg)"):]
        fn = fn[:fn.index("\nfunction ")]
        for tipo in ("'opcion'", "'multi'", "'numero'", "'rango_pct'"):
            assert tipo in fn, f"no sabe pintar el tipo {tipo}"
        assert "<textarea" in fn

    def test_distingue_lo_contestado_de_lo_heredado(self):
        """Presentar un valor heredado como propio hace que el reporte hable
        con una confianza que no tiene."""
        h = _sin_comentarios(_html())
        fn = h[h.index("function pfPreguntaHTML(preg)"):]
        fn = fn[:fn.index("\nfunction ")]
        assert "heredada" in fn and "respondidas" in fn

    def test_el_id_de_pregunta_no_va_dentro_de_un_onclick(self):
        """`_vcEsc` escapa para HTML, que no es escapar para un literal de JS:
        una comilla se saldría del string. Se lee por delegación."""
        h = _html()
        assert "pfRepintaPregunta('${" not in h
        assert "data-preg=" in h and "dataset.preg" in h

    def test_el_area_de_texto_no_se_repinta_al_teclear(self):
        """Reemplazar el `<textarea>` en cada tecla te manda el cursor al
        principio y hace imposible escribir."""
        h = _html()
        i = h.index("document.addEventListener('input', e => {\n    const el = e.target;")
        cuerpo = h[i:h.index("\n});", i)]
        assert "pfActualizaInsignia" in cuerpo
        assert "pfRepintaPregunta" not in cuerpo

    def test_guardar_manda_SOLO_lo_que_tocaste(self):
        """Solo los ids enviados cuentan como contestados. Mandar el formulario
        entero marcaría como «tuyas» respuestas que nunca miraste."""
        h = _sin_comentarios(_html())
        fn = h[h.index("async function pfGuardar()"):]
        fn = fn[:fn.index("\n}\n")]
        assert "respuestas: pfRespuestas" in fn

    def test_guardar_invalida_ideas_Y_wheel(self):
        """Las dos se ordenan con el perfil. Si el capital cambia y las tablas
        se quedan igual, la pantalla está mintiendo."""
        h = _sin_comentarios(_html())
        fn = h[h.index("async function pfGuardar()"):]
        fn = fn[:fn.index("\n}\n")]
        assert "vcTabCargada.ideas = false" in fn
        assert "vcTabCargada.wheel = false" in fn

    def test_el_progreso_dice_cuanto_heredaste_y_por_que_importa(self):
        h = _sin_comentarios(_html())
        fn = h[h.index("function pfPintaProgreso()"):]
        fn = fn[:fn.index("\n}")]
        assert "de ${total} preguntas contestadas" in fn
        assert "Kevin" in fn, "no dice de quién es el valor heredado"


class TestLaPantallaDelAprendizaje:

    def test_existe_y_llama_a_su_ruta(self):
        h = _sin_comentarios(_html())
        assert "async function pfCargaAprendizaje()" in h
        assert "API_BASE}/api/aprendizaje" in h

    def test_separa_los_dos_agentes_y_dice_como_aprende_cada_uno(self):
        h = _sin_comentarios(_html())
        fn = h[h.index("async function pfCargaAprendizaje()"):]
        fn = fn[:fn.index("\n}\n")] if "\n}\n" in fn[100:] else fn
        assert "acciones" in fn and "opciones" in fn
        assert "a.como_aprende" in fn and "a.explicacion" in fn

    def test_distingue_lo_de_todos_de_lo_tuyo(self):
        h = _sin_comentarios(_html())
        fn = h[h.index("async function pfCargaAprendizaje()"):]
        assert "d.tuyo" in fn

    def test_dice_en_pantalla_que_se_comparte_y_que_no(self):
        """Es la pregunta que se hace cualquiera que comparte una herramienta.
        No puede quedarse solo en el JSON."""
        h = _sin_comentarios(_html())
        fn = h[h.index("async function pfCargaAprendizaje()"):]
        for k in ("privacidad.compartido", "privacidad.privado", "privacidad.nunca"):
            assert k in fn, f"la pantalla no enseña `{k}`"
