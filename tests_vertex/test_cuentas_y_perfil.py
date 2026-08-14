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
                         "Universo", "Capital", "Reglas de dimensionamiento"):
            assert esperada in secciones, f"falta la sección «{esperada}» de Kevin.md"
        # «Qué espero del sistema» NO está: es una constante del sistema, no una
        # pregunta. Ver `TestElContratoDelSistemaNoEsUnaPregunta`.
        assert "Qué espero del sistema" not in secciones

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
        cliente.post("/api/perfil", json={"modo": "personalizado",
                                          "respuestas": {"capital": 5000}})
        d = cliente.get("/api/perfil").json()
        assert d["perfil"]["capital"] == 5000, "lo contestado no se guardó"
        assert d["perfil"]["tolerancia"] == "agresivo", "no heredó el default"
        assert d["perfil"]["mercados"] == ["EE.UU."]

    def test_el_sistema_DICE_cuanto_heredaste(self, cliente):
        """Un perfil heredado presentado como propio hace que el reporte hable
        con una confianza que no tiene."""
        import vertex_cuentas as C

        _registra(cliente)
        cliente.post("/api/perfil", json={"modo": "personalizado"})
        d = cliente.get("/api/perfil").json()
        assert d["progreso"]["contestadas"] == 0
        assert d["progreso"]["total"] == len(C.OBLIGATORIAS)
        assert d["progreso"]["opcionales"] == len(C.PREGUNTAS) - len(C.OBLIGATORIAS)
        # Pendientes son solo las OBLIGATORIAS: una opcional en blanco es una
        # respuesta válida, no algo heredado.
        assert d["progreso"]["sin_contestar"] == C.OBLIGATORIAS

        cliente.post("/api/perfil", json={"modo": "personalizado",
                                          "respuestas": {"capital": 5000,
                                                         "tolerancia": "moderado"}})
        d = cliente.get("/api/perfil").json()
        assert d["progreso"]["contestadas"] == 2
        assert "capital" not in d["progreso"]["sin_contestar"]
        assert "objetivos" in d["progreso"]["sin_contestar"]

    def test_el_md_declara_las_preguntas_pendientes(self):
        """El `.md` acaba en el prompt del agente. Si no dice qué es heredado,
        el agente habla del perfil de Kevin creyendo que es el tuyo."""
        import vertex_cuentas as C

        p = {**C.perfil_por_defecto(), "modo": "personalizado"}
        md = C.perfil_a_markdown(p)
        assert "sin contestar" in md.lower()

        contestado, err = C.perfil_desde_respuestas(
            {q["id"]: q["defecto"] for q in C.PREGUNTAS},
            {**C.perfil_por_defecto(), "modo": "personalizado"})
        assert not err
        assert "sin contestar" not in C.perfil_a_markdown(contestado).lower()

    def test_una_respuesta_invalida_aborta_el_guardado_ENTERO(self, cliente):
        """Un perfil a medias es peor que uno viejo: nadie sabría qué parte
        es suya."""
        _registra(cliente)
        cliente.post("/api/perfil", json={"modo": "personalizado",
                                          "respuestas": {"capital": 5000}})
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
        # `modo` explícito: sin él el perfil se queda en «por defecto» y lo
        # efectivo son los valores de referencia, no los contestados.
        kevin.post("/api/perfil", json={"modo": "personalizado", "respuestas": {
            "capital": 1000, "tolerancia": "agresivo", "max_posicion_pct": [20, 30]}})
        ana.post("/api/perfil", json={"modo": "personalizado", "respuestas": {
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

        p, err = C.perfil_desde_respuestas(
            {"capital": 1000, "horizonte": "1-3 años", "max_posicion_pct": [20, 30]},
            {**C.perfil_por_defecto(), "modo": "personalizado"})
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

        p, _ = C.perfil_desde_respuestas(
            {"capital": 1000, "tolerancia": "agresivo"},
            {**C.perfil_por_defecto(), "modo": "personalizado"})
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

        p, err = C.perfil_desde_respuestas(
            {"horizonte": horizonte}, {**C.perfil_por_defecto(), "modo": "personalizado"})
        assert not err
        r = self._parsea(C.perfil_a_markdown(p), tmp_path, monkeypatch)
        assert r["horizon_years"] == esperado
        assert "horizon_years" not in r["fields_defaulted"]

    def test_el_texto_del_inversionista_no_desplaza_al_capital(self, tmp_path, monkeypatch):
        """Alguien escribe «perdí $50,000 en 2022» en el texto libre. Si ese
        importe quedara antes que el capital, sería el que el engine leyera."""
        import vertex_cuentas as C

        p, _ = C.perfil_desde_respuestas(
            {"capital": 3000, "texto": "Perdí $50,000 en 2022."},
            {**C.perfil_por_defecto(), "modo": "personalizado"})
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
        # Ni un enunciado a mano. Los enunciados SÍ aparecen ahora dentro de
        # `VX_ES2EN`, y eso es otra cosa: allí son la CLAVE de una traducción,
        # no una segunda definición del cuestionario. Se comprueba que fuera del
        # diccionario no hay ninguna copia.
        dic = h.split("const VX_ES2EN = {", 1)[1].split("\n};", 1)[0]
        assert "¿Qué buscas con este dinero?" in dic, (
            "el enunciado no está ni en el diccionario: en inglés saldría en español")
        assert "¿Qué buscas con este dinero?" not in h.replace(dic, "")

    def test_cada_pregunta_del_servidor_tiene_su_traduccion(self):
        """El acoplamiento que crea el diccionario, vigilado.

        La clave de `VX_ES2EN` es la frase española LITERAL. Si alguien cambia
        el enunciado de una pregunta en `vertex_cuentas.py` y no toca el
        diccionario, la clave deja de casar y esa pregunta vuelve al español —
        sin error, sin aviso, solo media pantalla en el idioma equivocado. Este
        test es lo único que lo convierte en un fallo ruidoso.
        """
        import vertex_cuentas as C

        # Se escriben igual en los dos idiomas, así que no tienen entrada —y no
        # pueden tenerla: otro test prohíbe las traducciones que no traducen
        # nada. Van declaradas para que una pregunta NUEVA no se cuele aquí por
        # descuido: lo que no esté en esta lista tiene que estar en el
        # diccionario.
        IGUALES = {"Capital", "ETFs", "Forex", "Penny stocks", "Asia",
                   "NYSE, NASDAQ, AMEX."}

        dic = _html().split("const VX_ES2EN = {", 1)[1].split("\n};", 1)[0]
        faltan = []
        for p in C.PREGUNTAS:
            textos = [p.get("seccion"), p.get("pregunta"), p.get("ayuda")]
            for o in p.get("opciones") or []:
                textos += [o.get("label"), o.get("detalle")]
            for t in textos:
                if not t or t in IGUALES:
                    continue
                # `json.dumps` porque en el archivo están escapadas.
                if json.dumps(t, ensure_ascii=False) + ":" not in dic:
                    faltan.append(t)
        assert not faltan, (
            f"{len(faltan)} textos del cuestionario sin traducir; en inglés saldrían "
            f"en español: {faltan[:4]}")

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
        assert "heredada" in fn
        # `respondidas` ya no se lee aquí: la pregunta «¿contestó ESTA persona?»
        # se mudó a `pfContestada`, porque la usan el pintado, el manejador de
        # clics y la insignia, y tres copias de la misma regla acaban
        # divergiendo. Se comprueba en su sitio nuevo.
        assert "pfContestada(preg)" in fn
        helper = h[h.index("function pfContestada(preg)"):]
        helper = helper[:helper.index("\n}") + 2]
        assert "respondidas" in helper and "pfRespuestas" in helper

    def test_el_formulario_sale_EN_BLANCO_hasta_que_contestas(self):
        """Lo heredado se dice con la etiqueta, no premarcando la respuesta de
        Kevin. Ver `TestElCuestionarioNoEligePorTi` en test_navegador.py, que lo
        mide en un navegador; esto vigila que el cableado siga en su sitio."""
        h = _sin_comentarios(_html())
        fn = h[h.index("function pfPreguntaHTML(preg)"):]
        fn = fn[:fn.index("\nfunction ")]
        assert "pfValorEnBlanco(preg)" in fn, (
            "el formulario volvió a leer el valor efectivo: saldría con las "
            "respuestas de Kevin ya elegidas")
        assert "const val = pfValor(preg)" not in fn
        # Y el gemelo: el clic en una múltiple no puede partir de lo heredado.
        clic = h[h.index("if (preg.tipo === 'multi')"):]
        clic = clic[:clic.index("} else {")]
        assert "pfValorEnBlanco(preg)" in clic, (
            "el primer clic quitaría una opción de Kevin en vez de añadir la tuya")

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


class TestPreguntasOpcionales:
    """Una opcional en blanco es una RESPUESTA, no un valor heredado.

    Las demás preguntas tienen una respuesta correcta para cada persona, y
    dejarlas en blanco significa heredar la de Kevin. El texto libre no: el
    contexto personal de otra gente no es contexto tuyo, así que no hay nada
    que heredar y el perfil está completo sin él.
    """

    def test_el_texto_libre_es_opcional(self):
        import vertex_cuentas as C

        preg = next(p for p in C.PREGUNTAS if p["id"] == "texto")
        assert preg.get("opcional") is True

    def test_una_opcional_NUNCA_sale_como_pendiente(self):
        import vertex_cuentas as C

        # En modo personalizado, que es donde el cuestionario existe.
        p = {**C.perfil_por_defecto(), "modo": "personalizado"}
        pendientes = C.preguntas_sin_contestar(p)
        assert "texto" not in pendientes
        assert set(pendientes) == set(C.OBLIGATORIAS)

    def test_contestar_las_obligatorias_completa_el_perfil(self, cliente):
        """Sin esto, el perfil se quedaría eternamente incompleto por no
        escribir un texto que nadie tiene que escribir — y la advertencia de
        «usa el perfil de Kevin» sería falsa."""
        import vertex_cuentas as C

        _registra(cliente)
        cliente.post("/api/perfil", json={"modo": "personalizado", "respuestas": {
            p["id"]: p["defecto"] for p in C.PREGUNTAS if not p.get("opcional")}})
        d = cliente.get("/api/perfil").json()
        assert d["progreso"]["sin_contestar"] == []
        assert d["perfil"]["texto"] == "", "se rellenó sola una pregunta opcional"

    def test_el_md_no_lista_las_opcionales_como_pendientes(self):
        import vertex_cuentas as C

        p, err = C.perfil_desde_respuestas(
            {q["id"]: q["defecto"] for q in C.PREGUNTAS if not q.get("opcional")},
            {**C.perfil_por_defecto(), "modo": "personalizado"})
        assert not err
        md = C.perfil_a_markdown(p)
        assert "sin contestar" not in md.lower()
        assert "texto" not in md.lower().split("---")[0].replace("contexto", "")

    def test_la_opcional_sigue_guardandose_si_la_escribes(self, cliente):
        _registra(cliente)
        cliente.post("/api/perfil", json={"modo": "personalizado",
                                          "respuestas": {"texto": "Sin cripto."}})
        assert cliente.get("/api/perfil").json()["perfil"]["texto"] == "Sin cripto."


class TestElContratoDelSistemaNoEsUnaPregunta:
    """«¿Qué esperas que el sistema haga por ti?» estuvo en el cuestionario.

    No era una pregunta: era el contrato del sistema disfrazado de preferencia.
    La matemática es determinista y el LLM solo explica — eso no cambia porque
    alguien conteste otra cosa, así que preguntarlo insinuaba una elección que
    no existe.
    """

    def test_ya_no_se_pregunta(self):
        import vertex_cuentas as C

        assert not any(p["id"] == "espero_del_sistema" for p in C.PREGUNTAS)
        assert "espero_del_sistema" not in C.perfil_por_defecto()

    def test_no_queda_en_la_pantalla(self):
        h = _html()
        assert "espero_del_sistema" not in h
        assert "¿Qué esperas que el sistema haga por ti?" not in h

    def test_pero_el_contrato_SIGUE_en_el_md_que_leen_los_agentes(self):
        """Quitar la pregunta no puede quitarle al agente el contexto de cómo
        trabaja. Sigue ahí, pero como lo que es: una constante igual para
        todos, no una respuesta que alguien pudiera cambiar."""
        import vertex_cuentas as C

        md = C.perfil_a_markdown(C.perfil_por_defecto())
        assert "Cómo trabaja este sistema" in md
        assert "solo **explica**" in md
        assert "Nunca una orden de compra o venta" in md

    def test_el_contrato_es_IGUAL_para_todo_el_mundo(self):
        import vertex_cuentas as C

        def contrato(md):
            return md[md.index("## Cómo trabaja este sistema"):md.index("\n---")]

        base = {**C.perfil_por_defecto(), "modo": "personalizado"}
        a, _ = C.perfil_desde_respuestas({"capital": 1000, "tolerancia": "agresivo"}, base)
        b, _ = C.perfil_desde_respuestas({"capital": 250000, "tolerancia": "conservador",
                                          "texto": "Yo quiero otra cosa del sistema."}, base)
        assert contrato(C.perfil_a_markdown(a)) == contrato(C.perfil_a_markdown(b))


class TestModoDelPerfil:
    """Por defecto o personalizado.

    Once preguntas no pueden ser lo primero que ve alguien que entra a su
    perfil. El de referencia ya funciona; personalizar es una decisión que se
    toma cuando uno quiere.
    """

    def test_una_cuenta_nueva_arranca_en_por_defecto(self, cliente):
        _registra(cliente)
        d = cliente.get("/api/perfil").json()
        assert d["perfil"]["modo"] == "default"

    def test_en_por_defecto_no_hay_nada_pendiente(self, cliente):
        """Usar el perfil de referencia es una decisión tomada, no un
        formulario a medias. Marcarlo como incompleto sería regañar a alguien
        por haber elegido."""
        _registra(cliente)
        d = cliente.get("/api/perfil").json()
        assert d["progreso"]["sin_contestar"] == []

    def test_cambiar_de_modo_NO_borra_lo_contestado(self, cliente):
        """Borrar las respuestas al volver a «por defecto» castigaría la
        curiosidad de quien solo quiso ver cómo era el otro modo."""
        _registra(cliente)
        cliente.post("/api/perfil", json={"modo": "personalizado",
                                          "respuestas": {"capital": 250000}})
        assert cliente.get("/api/perfil").json()["perfil"]["capital"] == 250000

        cliente.post("/api/perfil", json={"modo": "default"})
        d = cliente.get("/api/perfil").json()
        assert d["perfil"]["capital"] == 1000, "el efectivo no volvió al de referencia"
        assert d["perfil"]["respuestas"]["capital"] == 250000, "se perdió lo contestado"

        cliente.post("/api/perfil", json={"modo": "personalizado"})
        assert cliente.get("/api/perfil").json()["perfil"]["capital"] == 250000

    def test_en_por_defecto_el_sizing_usa_el_de_referencia(self, cliente):
        """Lo que decide no es lo guardado, es lo EFECTIVO. Si el modo no
        llegara al sizing, el selector sería decorativo."""
        import vertex_cuentas as C

        _registra(cliente)
        cliente.post("/api/perfil", json={"modo": "personalizado",
                                          "respuestas": {"capital": 250000}})
        cliente.post("/api/perfil", json={"modo": "default"})
        d = cliente.get("/api/perfil").json()["perfil"]
        assert d["riesgo_por_trade"] == C.TOLERANCIAS["agresivo"]["riesgo_pct"] * 1000 / 100

    def test_el_md_DECLARA_que_el_perfil_es_el_de_referencia(self, cliente, entorno):
        """El agente tiene que poder distinguir «este es su capital» de «este
        es el de referencia porque no eligió personalizar»."""
        _registra(cliente)
        md = next((entorno / "Perfil Inversionista" / "usuarios").iterdir()
                  ).read_text(encoding="utf-8")
        assert "Perfil por defecto" in md
        assert "NO ha personalizado" in md

        cliente.post("/api/perfil", json={"modo": "personalizado",
                                          "respuestas": {"capital": 5000}})
        md = next((entorno / "Perfil Inversionista" / "usuarios").iterdir()
                  ).read_text(encoding="utf-8")
        assert "Perfil por defecto" not in md
        assert "$5,000" in md

    def test_contestar_en_modo_por_defecto_guarda_pero_no_surte_efecto(self, cliente):
        """Comportamiento declarado, no un accidente.

        Las respuestas se guardan siempre —para que estén ahí cuando pases a
        personalizado— pero lo EFECTIVO lo manda el modo. El payload devuelve
        las dos caras (`respuestas` y el nivel de arriba) precisamente para que
        esto se pueda ver en pantalla en vez de descubrirse.
        """
        _registra(cliente)
        cliente.post("/api/perfil", json={"respuestas": {"capital": 999999}})
        d = cliente.get("/api/perfil").json()["perfil"]
        assert d["respuestas"]["capital"] == 999999, "no guardó la respuesta"
        assert d["capital"] == 1000, "la aplicó estando en modo por defecto"

    def test_rechaza_un_modo_inventado(self, cliente):
        _registra(cliente)
        r = cliente.post("/api/perfil", json={"modo": "turbo"}).json()
        assert r["ok"] is False
        assert cliente.get("/api/perfil").json()["perfil"]["modo"] == "default"

    def test_el_perfil_devuelve_las_dos_caras(self, cliente):
        """`respuestas` es lo que escribiste; el resto es lo efectivo. Sin
        separarlas, el formulario en modo por defecto enseñaría los valores de
        Kevin como si fueran tuyos, y guardarlos los volvería tuyos."""
        import vertex_cuentas as C

        _registra(cliente)
        d = cliente.get("/api/perfil").json()["perfil"]
        assert "respuestas" in d
        assert set(d["respuestas"]) == {p["campo"] for p in C.PREGUNTAS}


class TestEntrarLlevaAlDashboard:

    def test_registrarse_no_secuestra_al_cuestionario(self):
        """Estuvo mandando al perfil a quien acababa de crear la cuenta: una
        barrera de once preguntas antes de haber visto nada."""
        h = _html()
        fn = h[h.index("async function authLogin("):]
        fn = fn[:fn.index("\n}")]
        assert "switchView('sectorsView')" in fn, (
            "entrar tiene que llevar al Dashboard, que es la pantalla de "
            "arranque del agente")
        assert "switchView('perfilView')" not in fn

    def test_al_perfil_se_llega_por_el_menu_de_cuenta(self):
        h = _html()
        assert "closeUserMenu(); switchView('perfilView');" in h


class TestLaPantallaDelModo:

    def test_el_selector_existe_y_ofrece_los_dos(self):
        h = _html()
        assert 'id="pfModo"' in h
        assert "function pfPintaModo()" in h
        fn = h[h.index("function pfPintaModo()"):]
        fn = fn[:fn.index("\ndocument.addEventListener")]
        assert "'default'" in fn and "'personalizado'" in fn

    def test_las_preguntas_solo_salen_si_es_personalizado(self):
        h = _html()
        fn = h[h.index("function pfPintaModo()"):]
        fn = fn[:fn.index("\ndocument.addEventListener")]
        assert "const personalizado = modo === 'personalizado'" in fn
        assert "personalizado\n        ? VX_PREGUNTAS.map(pfPreguntaHTML).join('') : ''" in fn

    def test_el_modo_va_en_data_no_en_un_onclick(self):
        h = _html()
        assert "data-modo=" in h and "dataset.modo" in h
        assert "pfPintaModo('${" not in h

    def test_guardar_manda_el_modo(self):
        h = _html()
        fn = h[h.index("async function pfGuardar()"):]
        fn = fn[:fn.index("\n}\n")]
        assert "modo: pfModoActual()" in fn

    def test_cambiar_solo_el_modo_ya_es_un_cambio_que_guardar(self):
        """Sin esto, elegir «personalizado» y pulsar Guardar diría «no has
        cambiado nada» y no guardaría el modo."""
        h = _html()
        fn = h[h.index("async function pfGuardar()"):]
        fn = fn[:fn.index("\n}\n")]
        assert "modoCambia" in fn


# ═══════════════════════════════════════════════════════════════════════════
#  EL PERFIL LLEGA AL AGENTE DE ACCIONES
#
#  Dos eslabones que estaban rotos y hacían que el perfil no cambiara nada de
#  lo que el usuario ve:
#
#   · `profile_fit` tenía los hechos ESCRITOS A MANO (el capital y el universo
#     de Kevin), así que le contaba a todo el mundo el perfil de otra persona.
#   · La explicación en palabras —el único sitio donde entra el texto libre—
#     vivía detrás de `?explain=1` y la pantalla nunca lo pedía.
# ═══════════════════════════════════════════════════════════════════════════

class TestProfileFitUsaTuPerfil:

    @staticmethod
    def _dos(entorno):
        from fastapi.testclient import TestClient
        import vertex_api as V

        kevin, ana = TestClient(V.app), TestClient(V.app)
        _registra(kevin, "k@x.com", "Kevin", "clave-larga-1")
        _registra(ana, "a@x.com", "Ana", "otra-clave-2")
        kevin.post("/api/perfil", json={"modo": "personalizado", "respuestas": {
            "capital": 1000, "tolerancia": "agresivo", "mercados": ["EE.UU."],
            "max_posicion_pct": [20, 30]}})
        ana.post("/api/perfil", json={"modo": "personalizado", "respuestas": {
            "capital": 250000, "tolerancia": "conservador",
            "mercados": ["Europa", "EE.UU."], "max_posicion_pct": [5, 10]}})
        return kevin, ana

    @staticmethod
    def _fit(cli, info, reco="ESPECULATIVO"):
        import vertex_api as V

        conn = V._db()
        u = V._CU.usuario_de_sesion(conn, cli.cookies.get("vertex_usuario"))
        conn.close()
        V._USUARIO_CTX.set(u)
        try:
            return V._wbj_profile_fit(info, reco)
        finally:
            V._USUARIO_CTX.set(None)

    AAPL = {"exchange": "NMS", "country": "United States"}
    SAP = {"exchange": "GER", "country": "Germany"}

    def test_el_capital_ya_no_esta_escrito_a_mano(self, entorno):
        """Decía «~$1,000 USD» literal en el código. Con cuentas, eso le
        contaba a cada persona el capital de Kevin."""
        kevin, ana = self._dos(entorno)
        assert self._fit(kevin, self.AAPL)["capital"] == "$1,000"
        assert self._fit(ana, self.AAPL)["capital"] == "$250,000"

    def test_el_universo_es_EL_TUYO_no_EEUU_por_decreto(self, entorno):
        """Decía «Kevin invierte solo en EE.UU.». A alguien que hubiera marcado
        Europa se le decía que una acción alemana estaba fuera de su universo."""
        kevin, ana = self._dos(entorno)
        assert self._fit(kevin, self.SAP)["fit"] == "fuera-de-universo"
        assert self._fit(ana, self.SAP)["fit"] != "fuera-de-universo"
        assert self._fit(ana, self.AAPL)["fit"] != "fuera-de-universo"

    def test_el_motivo_nombra_TUS_mercados(self, entorno):
        kevin, _ = self._dos(entorno)
        razon = self._fit(kevin, self.SAP)["fit_reason"]
        assert "EE.UU." in razon and "Germany" in razon

    def test_el_aviso_de_ruina_se_calibra_al_capital(self, entorno):
        """Con $1.000 y opciones el riesgo de ruina es urgente; con $250.000 es
        una nota al pie. Repetirlo igual lo convierte en ruido que se ignora."""
        kevin, ana = self._dos(entorno)
        assert "ruina" in self._fit(kevin, self.AAPL)["fit_reason"]
        assert "ruina" not in self._fit(ana, self.AAPL)["fit_reason"]

    def test_el_sizing_da_el_rango_en_DOLARES(self, entorno):
        """Un tope del 20-30% no dice nada solo. En dólares, sí."""
        kevin, ana = self._dos(entorno)
        assert "$200" in self._fit(kevin, self.AAPL)["sizing_note"]
        assert "$12,500" in self._fit(ana, self.AAPL)["sizing_note"]

    def test_declara_cuando_el_perfil_es_el_de_referencia(self, entorno):
        """El lector tiene derecho a saber que estos hechos no los declaró
        nadie."""
        from fastapi.testclient import TestClient
        import vertex_api as V

        nuevo = TestClient(V.app)
        _registra(nuevo, "n@x.com", "Nuevo", "clave-larga-1")
        assert self._fit(nuevo, self.AAPL)["es_por_defecto"] is True
        nuevo.post("/api/perfil", json={"modo": "personalizado"})
        assert self._fit(nuevo, self.AAPL)["es_por_defecto"] is False

    def test_sin_mercados_marcados_no_inventa_un_universo(self, entorno):
        """Afirmar que algo está «fuera de tu universo» sin saber cuál es tu
        universo sería peor que no comprobarlo."""
        import vertex_api as V

        kevin, _ = self._dos(entorno)
        conn = V._db()
        u = V._CU.usuario_de_sesion(conn, kevin.cookies.get("vertex_usuario"))
        conn.close()
        V._USUARIO_CTX.set(u)
        try:
            V._perfil_leer()          # calienta el camino normal
            base = V._perfil_leer()
            import unittest.mock as M
            with M.patch.object(V, "_perfil_leer", lambda request=None: {**base, "mercados": []}):
                assert V._wbj_profile_fit(self.SAP, "FAVORABLE")["universe_ok"] is True
        finally:
            V._USUARIO_CTX.set(None)


class TestLaExplicacionLlegaALaPantalla:

    @staticmethod
    def _con_reporte(entorno, texto="Trabajo en semiconductores."):
        from fastapi.testclient import TestClient
        import vertex_api as V

        kevin, ana = TestClient(V.app), TestClient(V.app)
        _registra(kevin, "k@x.com", "Kevin", "clave-larga-1")
        _registra(ana, "a@x.com", "Ana", "otra-clave-2")
        kevin.post("/api/perfil", json={"modo": "personalizado",
                                        "respuestas": {"capital": 1000, "texto": texto}})
        conn = V._db()
        uid = V._CU.buscar_usuario(conn, email="k@x.com")["id"]
        conn.execute("INSERT INTO reports (report_id,ticker,created_at,created_ts,"
                     "payload,usuario_id) VALUES (?,?,?,?,?,?)",
                     ("r-1", "NVDA", "hoy", time.time(), json.dumps({
                         "ticker": "NVDA", "nombre_completo": "NVIDIA Corp",
                         "precio_actual": 180.0, "wbj": {"profile": "CALIDAD",
                                                         "raw_total": 78, "categories": {}},
                         "profile_fit": {"fit": "apto", "fit_reason": "x",
                                         "capital": "$1,000", "riesgo_por_operacion": "$150",
                                         "max_posicion_pct": "20% – 30%",
                                         "horizonte": "1-3 años", "universo": "EE.UU."}}), uid))
        conn.commit()
        conn.close()
        return kevin, ana

    def test_la_ruta_existe_y_solo_sirve_TU_reporte(self, entorno):
        """De nada serviría un archivo privado si esta ruta explicara el de
        otro. Y «no existe» y «no es tuyo» dan el mismo mensaje: distinguirlos
        convertiría la ruta en un oráculo de qué ha analizado la gente."""
        kevin, ana = self._con_reporte(entorno)
        assert ana.get("/api/wbj-explicacion?report_id=r-1").json() == \
            kevin.get("/api/wbj-explicacion?report_id=inventado").json()

    def test_el_texto_libre_del_perfil_ENTRA_en_el_contexto(self, entorno):
        """El eslabón que estaba roto. Si el texto no llega al prompt, la
        pregunta «¿algo más que el agente deba saber de ti?» es decorativa."""
        import vertex_api as V

        kevin, _ = self._con_reporte(entorno, texto="Ya tengo el 40% en semiconductores.")
        conn = V._db()
        u = V._CU.usuario_de_sesion(conn, kevin.cookies.get("vertex_usuario"))
        payload = json.loads(conn.execute(
            "SELECT payload FROM reports WHERE report_id='r-1'").fetchone()[0])
        conn.close()
        V._USUARIO_CTX.set(u)
        try:
            ctx = V._wbj_explain_context("NVDA", "NVIDIA Corp", 180.0, payload)
        finally:
            V._USUARIO_CTX.set(None)
        assert "Ya tengo el 40% en semiconductores." in ctx
        assert "=== MI PERFIL" in ctx

    def test_los_hechos_duros_van_EXPLICITOS_no_solo_en_la_prosa(self, entorno):
        """Enterrados en el markdown se pierden. El LLM los necesita sueltos
        para calibrar el tamaño de lo que describe."""
        import vertex_api as V

        kevin, _ = self._con_reporte(entorno)
        conn = V._db()
        u = V._CU.usuario_de_sesion(conn, kevin.cookies.get("vertex_usuario"))
        payload = json.loads(conn.execute(
            "SELECT payload FROM reports WHERE report_id='r-1'").fetchone()[0])
        conn.close()
        V._USUARIO_CTX.set(u)
        try:
            ctx = V._wbj_explain_context("NVDA", "NVIDIA Corp", 180.0, payload)
        finally:
            V._USUARIO_CTX.set(None)
        assert "HECHOS DE TU PERFIL" in ctx
        assert "$1,000" in ctx and "20% – 30%" in ctx

    def test_avisa_al_LLM_cuando_el_perfil_es_el_de_referencia(self, entorno):
        """Sin el aviso, el LLM le hablaría a alguien de «tu capital de $1.000»
        cuando esa persona nunca declaró nada."""
        import vertex_api as V

        payload = {"profile_fit": {"fit": "apto", "fit_reason": "x", "capital": "$1,000",
                                   "es_por_defecto": True, "riesgo_por_operacion": "$150",
                                   "max_posicion_pct": "20% – 30%", "horizonte": "1-3 años",
                                   "universo": "EE.UU."}, "wbj": {}}
        ctx = V._wbj_explain_context("NVDA", "NVIDIA", 180.0, payload)
        assert "NO ha personalizado" in ctx

    def test_no_se_paga_dos_veces_por_el_mismo_texto(self, entorno, monkeypatch):
        """Cuesta ~18 s. Si ya está en el reporte, se devuelve."""
        import vertex_api as V

        kevin, _ = self._con_reporte(entorno)
        llamadas = []
        monkeypatch.setattr(V, "_wbj_explain",
                            lambda ctx, temp=0.3: (llamadas.append(1), ({"resumen": "hola"}, "test"))[1])
        a = kevin.get("/api/wbj-explicacion?report_id=r-1").json()
        assert a["ok"] is True and a["cacheada"] is False
        b = kevin.get("/api/wbj-explicacion?report_id=r-1").json()
        assert b["cacheada"] is True
        assert len(llamadas) == 1, "generó la explicación dos veces"

    def test_la_pantalla_la_pide_DESPUES_del_analisis(self):
        """Meterla dentro de `/api/analyze` sumaría ~18 s a un endpoint que ya
        roza el corte de Render — que es justo por lo que quedó desconectada."""
        h = _html()
        assert "API_BASE}/api/wbj-explicacion" in h
        # El análisis se pide SIN `explain=1`.
        assert "api/analyze?ticker=${ticker}&explain" not in h
        # Y la llamada a la explicación no se espera con `await`.
        i = h.index("cargaExplicacion(data.report_id);")
        assert "await cargaExplicacion" not in h[i - 40:i + 40]

    def test_el_panel_existe_y_dice_que_NO_calcula(self):
        """Un texto de un LLM junto a unos números invita a creer que los
        produjo. Tiene que decir que solo los explica."""
        h = _html()
        assert 'id="qtExplicacion"' in h
        fn = h[h.index("function pintaExplicacion("):]
        fn = fn[:fn.index("\n}")]
        assert "no los calcula" in fn

    def test_un_fallo_deja_el_panel_con_su_motivo(self):
        """Esconderlo dejaría al usuario sin saber que esta parte existe y que
        hoy falló."""
        h = _html()
        fn = h[h.index("async function cargaExplicacion("):]
        fn = fn[:fn.index("\nfunction pintaExplicacion") if "\nfunction pintaExplicacion" in fn else 4000]
        assert "d.error" in fn and "Reintentar" in fn

    def test_el_id_del_reporte_no_va_dentro_de_un_onclick(self):
        h = _html()
        assert "cargaExplicacion('${" not in h
        assert "data-reintentar=" in h and "dataset.reintentar" in h


class TestMigracionAUnMundoConCuentas:
    """Estrenar las cuentas no puede costarte tu historial.

    Dos formas de perderlo, las dos silenciosas, las dos encontradas simulando
    la base que de verdad hay en Render:

     · los reportes de antes tienen `usuario_id` NULL, así que al registrarte
       tu archivo sale VACÍO;
     · el navegador borraba su copia local en CADA login, y los reportes cuyo
       `payload` nunca llegó al servidor solo viven ahí.
    """

    @staticmethod
    def _base_vieja(entorno):
        """Una base del mundo de un solo usuario: reportes sin dueño."""
        import vertex_api as V

        conn = V._db()
        for t in ("AAPL", "JPM", "NVDA"):
            conn.execute("INSERT INTO reports (report_id,ticker,created_at,created_ts,"
                         "payload,usuario_id) VALUES (?,?,?,?,?,NULL)",
                         (f"viejo_{t}", t, "antes", time.time(),
                          json.dumps({"ticker": t})))
        conn.commit()
        conn.close()

    def test_la_primera_cuenta_ADOPTA_el_archivo_huerfano(self, cliente, entorno):
        """Quien se registra primero es quien los generó: era el único usuario
        que había."""
        self._base_vieja(entorno)
        r = _registra(cliente)
        assert r["primera_cuenta"] is True
        assert r["reportes_adoptados"] == 3
        vistos = [x["ticker"] for x in cliente.get("/api/reports/list").json()["reports"]]
        assert sorted(vistos) == ["AAPL", "JPM", "NVDA"]

    def test_la_SEGUNDA_cuenta_no_se_queda_con_el_archivo_de_nadie(self, entorno):
        """Regalar los huérfanos a cualquiera que se registre sería entregarle
        el archivo de otro."""
        from fastapi.testclient import TestClient
        import vertex_api as V

        self._base_vieja(entorno)
        kevin, ana = TestClient(V.app), TestClient(V.app)
        _registra(kevin, "k@x.com", "Kevin", "clave-larga-1")
        r = _registra(ana, "a@x.com", "Ana", "otra-clave-2")

        assert r["primera_cuenta"] is False and r["reportes_adoptados"] == 0
        assert ana.get("/api/reports/list").json()["reports"] == []
        assert len(kevin.get("/api/reports/list").json()["reports"]) == 3

    def test_una_base_VIEJA_arranca_sin_perder_nada(self, tmp_path, monkeypatch):
        """Las columnas y tablas nuevas se añaden sobre lo que ya hay; los
        reportes anteriores siguen ahí."""
        import vertex_api as V

        db = tmp_path / "vieja.db"
        conn = sqlite3.connect(db)
        conn.execute("""CREATE TABLE reports (report_id TEXT PRIMARY KEY, ticker TEXT,
                        created_at TEXT, created_ts REAL, thesis TEXT)""")
        conn.execute("INSERT INTO reports VALUES ('r','AAPL','antes',1.0,'tesis')")
        conn.commit()
        conn.close()

        monkeypatch.setattr(V, "DB_PATH", str(db))
        V.init_db()

        conn = sqlite3.connect(db)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(reports)").fetchall()}
        tablas = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        n = conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
        conn.close()
        assert {"usuario_id", "payload"} <= cols
        assert {"usuarios", "sesiones", "contribuciones"} <= tablas
        assert n == 1, "la migración perdió reportes"

    def test_el_navegador_NO_borra_el_archivo_al_entrar_la_misma_persona(self):
        """El fallo caro: los reportes sin `payload` en el servidor solo viven
        en el navegador, y `syncReportsFromServer` no puede devolverlos."""
        h = _html()
        fn = h[h.index("async function authLogin("):]
        fn = fn[:fn.index("\n}")]
        assert "ARCHIVO_DUENO_KEY" in fn, "no distingue de quién es el archivo local"
        assert "dueno !== user.id" in fn, "borra sin comprobar si cambió la persona"

    def test_sin_dueno_guardado_el_archivo_se_CONSERVA(self):
        """Un archivo sin dueño es de antes de las cuentas: de la única persona
        que había, que es la que está entrando."""
        h = _html()
        fn = h[h.index("async function authLogin("):]
        fn = fn[:fn.index("\n}")]
        i = fn.index("if (dueno")
        assert "dueno &&" in fn[i:i + 60], "sin dueño también borraría"

    def test_al_SALIR_si_se_limpia(self):
        """Es una salida deliberada, y lo que se pierde ya está en el servidor
        bajo su cuenta."""
        h = _html()
        fn = h[h.index("async function authSignOut()"):]
        fn = fn[:fn.index("\n}")]
        assert "localStorage.removeItem(STORAGE_KEY)" in fn
        assert "ARCHIVO_DUENO_KEY" in fn


class TestElPayloadGuardadoSiempreSePuedeLEER:
    """Un JSON cortado por la mitad no es un JSON.

    `save_report_payload` aplicaba su tope de 2 MB con `[:2_000_000]`. La fila
    quedaba escrita pero **ilegible**: `/api/reports/list` la saltaba con su
    `except: continue` y el reporte desaparecía del archivo sin que nada
    avisara. Es un fallo anterior a las cuentas, pero el endpoint de
    explicación añade una segunda escritura al mismo payload.
    """

    @staticmethod
    def _guarda_y_lee(entorno, payload):
        import vertex_api as V

        conn = V._db()
        conn.execute("INSERT OR REPLACE INTO reports (report_id,ticker,created_at,"
                     "created_ts) VALUES ('r','AAPL','hoy',1.0)")
        conn.commit()
        conn.close()
        V.save_report_payload("r", payload)
        conn = V._db()
        fila = conn.execute("SELECT payload FROM reports WHERE report_id='r'").fetchone()[0]
        conn.close()
        return fila

    def test_un_payload_normal_se_guarda_entero(self, entorno):
        blob = self._guarda_y_lee(entorno, {
            "ticker": "AAPL", "wbj": {"raw_total": 78},
            "chart_history": [{"time": "2026-08-06", "value": 180.4}] * 252})
        d = json.loads(blob)                      # no lanza = es JSON válido
        assert d["wbj"]["raw_total"] == 78
        assert len(d["chart_history"]) == 252

    def test_uno_gigante_pierde_las_SERIES_no_la_legibilidad(self, entorno):
        """Las series de precio son lo que pesa y lo que la gráfica puede
        volver a pedir. El análisis —que no se puede regenerar— se queda."""
        blob = self._guarda_y_lee(entorno, {
            "ticker": "AAPL", "wbj": {"raw_total": 78}, "tesis": "importante",
            "chart_history": [{"time": "2026-08-06", "value": 180.4}] * 40_000,
            "historial_ohlc": [{"o": 1, "h": 2, "l": 3, "c": 4}] * 40_000})
        d = json.loads(blob)                      # sigue siendo JSON válido
        assert d["tesis"] == "importante", "se perdió lo que no se puede regenerar"
        assert "chart_history" not in d
        assert "_series_omitidas" in d, "no declara que quitó las series"

    def test_si_ni_asi_cabe_NO_se_escribe_basura(self, entorno):
        """Un payload ausente se nota y se puede regenerar; uno corrupto se lee
        como si el reporte no existiera."""
        blob = self._guarda_y_lee(entorno, {"ticker": "AAPL", "tesis": "z" * 3_000_000})
        assert blob is None

    def test_nunca_se_corta_un_json_a_lo_bruto(self):
        import inspect

        import vertex_api as V

        # Sin comentarios: el de arriba EXPLICA el corte viejo citándolo, y un
        # test que lea comentarios falla por la documentación del propio
        # arreglo. Regla del proyecto: se lee el código, no lo que dice de él.
        fuente = "\n".join(l for l in inspect.getsource(V.save_report_payload).splitlines()
                           if not l.strip().startswith("#"))
        assert "[:2_000_000]" not in fuente, "vuelve a cortar el JSON por la mitad"


class TestElTopeDelPayloadSeSubeSinRomperNada:
    """Subir el tope por reporte sin freno en la lista es peor que no subirlo.

    2 MB no es un límite de SQLite —aguanta 1 GB por columna—: es el que hace
    servible a `/api/reports/list`, que devuelve hasta 60 payloads COMPLETOS de
    una vez. El tope por reporte se multiplica por 60, así que a 10 MB serían
    600 MB en una sola respuesta.
    """

    def test_el_tope_por_reporte_es_configurable(self, monkeypatch):
        import vertex_api as V

        assert V._payload_max() == 2_000_000, "el default cambió sin querer"
        monkeypatch.setenv("VERTEX_PAYLOAD_MAX", "10000000")
        assert V._payload_max() == 10_000_000
        # Basura o cero → el default, nunca un tope de 0 que no guardaría nada.
        monkeypatch.setenv("VERTEX_PAYLOAD_MAX", "muchos")
        assert V._payload_max() == 2_000_000
        monkeypatch.setenv("VERTEX_PAYLOAD_MAX", "0")
        assert V._payload_max() == 2_000_000

    def test_el_tope_nuevo_se_RESPETA_al_guardar(self, entorno, monkeypatch):
        import vertex_api as V

        monkeypatch.setenv("VERTEX_PAYLOAD_MAX", "10000000")
        conn = V._db()
        conn.execute("INSERT INTO reports (report_id,ticker,created_at,created_ts) "
                     "VALUES ('r','AAPL','hoy',1.0)")
        conn.commit()
        conn.close()
        # 5 MB: pasaba del tope viejo, cabe en el nuevo, y las series se quedan.
        V.save_report_payload("r", {"ticker": "AAPL", "relleno": "z" * 5_000_000,
                                    "chart_history": [{"t": 1}] * 10})
        conn = V._db()
        blob = conn.execute("SELECT payload FROM reports WHERE report_id='r'").fetchone()[0]
        conn.close()
        d = json.loads(blob)
        assert len(blob) > 2_000_000, "no aplicó el tope nuevo"
        assert "chart_history" in d, "tiró las series aunque cabían"

    def test_la_lista_se_corta_por_PESO_no_solo_por_numero(self, entorno, cliente,
                                                           monkeypatch):
        """`limit` acota cuántas filas se piden, no cuánto pesan. Sin este
        freno, subir el tope por reporte tumba el proceso al hidratar."""
        import vertex_api as V

        _registra(cliente)
        conn = V._db()
        uid = V._CU.buscar_usuario(conn, email="kevin@x.com")["id"]
        for i in range(20):
            conn.execute("INSERT INTO reports (report_id,ticker,created_at,created_ts,"
                         "payload,usuario_id) VALUES (?,?,?,?,?,?)",
                         (f"r{i:02}", "AAPL", "hoy", 1000 - i,
                          json.dumps({"ticker": "AAPL", "relleno": "z" * 3_000_000}), uid))
        conn.commit()
        conn.close()

        d = cliente.get("/api/reports/list").json()
        assert d["recortados"] > 0, "sirvió 60 MB de golpe"
        assert len(d["reports"]) < 20
        # Se DICE. Un archivo recortado en silencio parece uno que perdió cosas.
        assert d["motivo_recorte"]

    def test_siempre_devuelve_AL_MENOS_uno(self, entorno, cliente, monkeypatch):
        """Un reporte más grande que el tope de la respuesta no puede dejar el
        archivo vacío: se sirve igual y el freno actúa a partir del segundo."""
        import vertex_api as V

        monkeypatch.setenv("VERTEX_LISTA_MAX", "1000")      # tope absurdo
        _registra(cliente)
        conn = V._db()
        uid = V._CU.buscar_usuario(conn, email="kevin@x.com")["id"]
        conn.execute("INSERT INTO reports (report_id,ticker,created_at,created_ts,"
                     "payload,usuario_id) VALUES ('r','AAPL','hoy',1.0,?,?)",
                     (json.dumps({"ticker": "AAPL", "relleno": "z" * 50_000}), uid))
        conn.commit()
        conn.close()
        assert len(cliente.get("/api/reports/list").json()["reports"]) == 1
