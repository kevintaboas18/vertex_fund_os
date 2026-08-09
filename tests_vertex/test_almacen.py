"""El almacén: que nada se pierda, y que lo sensible no se filtre.

Estas dos cosas tiran en direcciones opuestas —guardarlo todo en un repositorio
es lo que hace que sobreviva, y también lo que lo pondría al alcance de quien
lea el repo—, así que la mitad de estos tests miden lo que SÍ se guarda y la
otra mitad lo que NUNCA debe salir en claro.

El caso que de verdad importa es `TestUnContenedorNuevo`: se borra el disco
entero, como hace Render en cada redeploy, y se comprueba que vuelve todo.

    python -m pytest tests_vertex/test_almacen.py -q
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "engine"))

pytest.importorskip("fastapi", reason="requiere las deps de vertex_api")

if not __import__("shutil").which("git"):        # pragma: no cover
    pytest.skip("hace falta git", allow_module_level=True)


@pytest.fixture
def remoto(tmp_path):
    """Un repositorio vacío que hace de GitHub. Todo local: cero red."""
    r = tmp_path / "remoto.git"
    subprocess.run(["git", "init", "-q", "--bare", str(r)], check=True)
    return f"file://{r}"


def _aisla(tmp_path, monkeypatch, remoto):
    """Deja el proceso como si acabara de arrancar, apuntando a `tmp_path`.

    Lo importante es la línea de `DB_PATH`: `vertex_api` la lee del entorno
    **al importarse**, así que poner `VERTEX_DB` desde un test llega tarde y
    todos los casos acabarían compartiendo la base del repositorio — que es
    exactamente lo que hacía fallar a estos tests con «ya existe una cuenta con
    ese email». La variable se pone igual, para lo que arranque después, pero
    lo que de verdad manda es el parcheo del módulo.
    """
    import vertex_almacen as VA
    import vertex_api as V

    monkeypatch.setenv("VERTEX_ALMACEN", str(tmp_path / "almacen"))
    monkeypatch.setenv("VERTEX_ALMACEN_REMOTO", remoto)
    monkeypatch.setenv("VERTEX_GIT_TOKEN", "x")
    monkeypatch.setenv("VERTEX_DB", str(tmp_path / "vertex.db"))
    monkeypatch.setenv("MASSIVE_API_KEY", "x" * 32)
    monkeypatch.setattr(V, "DB_PATH", str(tmp_path / "vertex.db"))
    # `_arranca_almacen` respeta un `WBJ_TITO_DATA` puesto a mano —un disco real
    # configurado por el operador tiene que ganar—, así que se quita para medir
    # el comportamiento por defecto.
    monkeypatch.delenv("WBJ_TITO_DATA", raising=False)
    monkeypatch.setattr(VA, "almacen", VA.Almacen())


@pytest.fixture
def alm(tmp_path, remoto):
    from vertex_almacen import Almacen

    a = Almacen(raiz=tmp_path / "almacen", remoto=remoto, token="ficticio")
    a.restaura()
    return a


class TestLoBasico:
    def test_escribe_y_lee(self, alm):
        alm.guarda("Reportes/AAPL/2026-08-07/reporte.json", {"score": 78})
        assert alm.lee_json("Reportes/AAPL/2026-08-07/reporte.json") == {"score": 78}

    def test_la_escritura_es_atomica(self, alm):
        """Un `.tmp` a medias no puede quedar como si fuera el archivo bueno."""
        alm.guarda("x.json", {"a": 1})
        assert not list(alm.raiz.glob("*.tmp"))

    def test_lo_que_no_existe_devuelve_None_y_no_revienta(self, alm):
        assert alm.lee("no/existe.json") is None
        assert alm.lee_json("no/existe.json") is None

    def test_un_json_corrupto_no_tumba_la_lectura(self, alm):
        alm.guarda("roto.json", "{esto no es json")
        assert alm.lee_json("roto.json") is None

    @pytest.mark.parametrize("mala", [
        "../fuera.json", "../../etc/passwd", "Reportes/../../fuera.json",
        "/etc/passwd",
    ])
    def test_no_se_puede_escribir_fuera_del_almacen(self, alm, mala):
        """El ticker viene de la caja de texto y acaba siendo una carpeta. Un
        archivo escrito fuera del almacén no se respalda ni se restaura: se
        perdería en silencio, que es justo lo que este módulo evita."""
        with pytest.raises(ValueError):
            alm.guarda(mala, {"x": 1})

    def test_borra(self, alm):
        alm.guarda("a.json", {"x": 1})
        assert alm.borra("a.json") is True
        assert alm.lee("a.json") is None
        assert alm.borra("a.json") is False


class TestElRespaldo:
    def test_sube_y_deja_la_cola_vacia(self, alm):
        alm.guarda("Reportes/AAPL/2026-08-07/reporte.json", {"score": 78})
        est = alm.sincroniza()
        assert est["ultimo_error"] is None
        assert est["ultimo_push"]
        assert alm.estado()["pendientes"] == 0

    def test_sin_cambios_no_hace_commits(self, alm):
        alm.guarda("a.json", {"x": 1})
        alm.sincroniza()
        n = alm.estado()["commits"]
        for _ in range(3):
            alm.sincroniza()
        assert alm.estado()["commits"] == n, "un ciclo en vacío no puede ensuciar el historial"

    def test_los_temporales_no_se_versionan(self, alm):
        (alm.raiz / "basura.tmp").write_text("a medias")
        (alm.raiz / "algo.json.lock").write_text("")
        alm.guarda("bueno.json", {"x": 1})
        alm.sincroniza()
        seguidos = alm._git("ls-files", tolera=True).stdout.split()
        assert "basura.tmp" not in seguidos
        assert "algo.json.lock" not in seguidos
        assert "bueno.json" in seguidos

    def test_la_rama_es_la_de_datos_no_main(self, alm):
        """Si los datos cayeran en `main`, cada análisis dispararía un
        despliegue nuevo en Render y el historial del código sería ilegible."""
        alm.guarda("a.json", {"x": 1})
        alm.sincroniza()
        r = alm._git("branch", "--show-current", tolera=True)
        assert r.stdout.strip() == "datos"

    def test_dos_procesos_a_la_vez_no_pierden_lo_del_otro(self, tmp_path, remoto):
        """Render puede correr más de un worker. Sin el rebase del `_empuja`,
        el segundo push se rechaza y ese trabajo se queda sin subir."""
        from vertex_almacen import Almacen

        a = Almacen(raiz=tmp_path / "w1", remoto=remoto, token="x"); a.restaura()
        a.guarda("Reportes/AAA/2026-08-07/reporte.json", {"n": 1}); a.sincroniza()
        b = Almacen(raiz=tmp_path / "w2", remoto=remoto, token="x"); b.restaura()
        b.guarda("Reportes/BBB/2026-08-07/reporte.json", {"n": 2}); b.sincroniza()
        a.guarda("Reportes/CCC/2026-08-07/reporte.json", {"n": 3})
        assert a.sincroniza()["ultimo_error"] is None

        c = Almacen(raiz=tmp_path / "w3", remoto=remoto, token="x"); c.restaura()
        vistos = {p.parts[-3] for p in c.lista("Reportes")}
        assert vistos == {"AAA", "BBB", "CCC"}, "ningún worker perdió lo suyo"


class TestSinTokenNoSeRompeNada:
    """El modo local: se guarda todo, no se sube nada, y se DICE."""

    @pytest.fixture
    def local(self, tmp_path, monkeypatch):
        from vertex_almacen import Almacen

        monkeypatch.delenv("VERTEX_GIT_TOKEN", raising=False)
        a = Almacen(raiz=tmp_path / "solo-local", token="")
        a.restaura()
        return a

    def test_se_guarda_igual(self, local):
        local.guarda("Reportes/AAPL/2026-08-07/reporte.json", {"score": 78})
        assert local.lee_json("Reportes/AAPL/2026-08-07/reporte.json")["score"] == 78

    def test_dice_que_no_respalda_y_por_que(self, local):
        e = local.estado()
        assert e["respalda"] is False
        assert "VERTEX_GIT_TOKEN" in e["motivo"]
        assert "redeploy" in e["motivo"], "tiene que decir la CONSECUENCIA, no solo el hecho"

    def test_sincronizar_no_lanza(self, local):
        local.guarda("a.json", {"x": 1})
        assert local.sincroniza()["respalda"] is False


class TestElTokenNoSeFiltra:
    """Git mete la URL del remoto —con el token dentro— en casi todos sus
    errores. Los logs de Render son visibles en el dashboard y se guardan."""

    def test_se_borra_de_cualquier_texto(self, monkeypatch):
        from vertex_almacen import _sin_secretos

        monkeypatch.setenv("VERTEX_GIT_TOKEN", "ghp_secreto1234567890")
        msg = ("fatal: no se pudo acceder a "
               "https://x-access-token:ghp_secreto1234567890@github.com/k/v.git")
        limpio = _sin_secretos(msg)
        assert "ghp_secreto" not in limpio
        assert "github.com/k/v.git" in limpio, "el diagnóstico útil se conserva"

    def test_el_estado_publico_no_lo_lleva(self, alm):
        assert "ficticio" not in json.dumps(alm.estado())

    def test_un_push_fallido_no_lo_escribe(self, tmp_path, monkeypatch):
        from vertex_almacen import Almacen

        monkeypatch.setenv("VERTEX_GIT_TOKEN", "ghp_secretodeprueba")
        a = Almacen(raiz=tmp_path / "roto",
                    remoto="https://github.com/no/existe-de-verdad.git",
                    token="ghp_secretodeprueba")
        a.restaura()
        a.guarda("a.json", {"x": 1})
        est = a.sincroniza()
        assert "ghp_secreto" not in json.dumps(est)


class TestLoPrivadoViajaCifrado:
    @pytest.fixture(autouse=True)
    def entorno(self, tmp_path, monkeypatch, remoto):
        _aisla(tmp_path, monkeypatch, remoto)
        monkeypatch.setenv("VERTEX_DB_KEY", "clave-de-prueba-suficientemente-larga")

    def test_el_paquete_va_cifrado_y_solo_el_enc_se_sube(self, tmp_path):
        import vertex_api as V
        from vertex_almacen import Almacen

        a = Almacen(raiz=tmp_path / "almacen",
                    remoto=os.environ["VERTEX_ALMACEN_REMOTO"], token="x")
        a.restaura()
        a.guarda("Privado/esto_esta_en_claro.json", {"pass_hash": "pbkdf2$secreto"})
        assert V._respalda_privado(a) == ""
        a.sincroniza()
        subidos = [x for x in a._git("ls-files", tolera=True).stdout.split()
                   if x.startswith("Privado/")]
        assert "Privado/privado.enc" in subidos
        assert not any("claro" in s for s in subidos), \
            "solo los .enc salen de Privado/; el resto se queda en el disco efímero"

    def test_lo_cifrado_no_deja_ver_el_contenido(self, tmp_path):
        import vertex_api as V
        import vertex_cuentas as C
        from vertex_almacen import Almacen

        V.init_db()
        conn = V._db(); C.crear_tablas(conn); conn.commit()
        C.crear_usuario(conn, "ana@ejemplo.com", "Ana", "contrasena-larga-1234")
        conn.commit(); conn.close()

        a = Almacen(raiz=tmp_path / "almacen",
                    remoto=os.environ["VERTEX_ALMACEN_REMOTO"], token="x")
        a.restaura()
        V._respalda_privado(a)
        enc = a.lee("Privado/privado.enc")
        assert enc and b"ana@ejemplo.com" not in enc
        assert b"SQLite format" not in enc

    def test_sin_clave_NO_se_sube_nada_privado(self, tmp_path, monkeypatch):
        """Preferir «se pierde» a «se filtra», y decirlo en voz alta."""
        import vertex_api as V
        from vertex_almacen import Almacen

        monkeypatch.delenv("VERTEX_DB_KEY", raising=False)
        a = Almacen(raiz=tmp_path / "almacen",
                    remoto=os.environ["VERTEX_ALMACEN_REMOTO"], token="x")
        a.restaura()
        aviso = V._respalda_privado(a)
        assert "VERTEX_DB_KEY" in aviso
        assert a.lee("Privado/privado.enc") is None

    def test_sin_cambios_no_se_recifra(self, tmp_path):
        """Fernet usa un IV aleatorio: cifrar dos veces lo mismo da bytes
        distintos. Sin el testigo del SHA, cada ciclo parecería un cambio y el
        repo se llenaría de commits idénticos."""
        import vertex_api as V
        from vertex_almacen import Almacen

        V.init_db()
        a = Almacen(raiz=tmp_path / "almacen",
                    remoto=os.environ["VERTEX_ALMACEN_REMOTO"], token="x")
        a.restaura()
        V._respalda_privado(a)
        antes = a.lee("Privado/privado.enc")
        V._respalda_privado(a)
        assert a.lee("Privado/privado.enc") == antes


class TestUnContenedorNuevo:
    """LA prueba. Se borra el disco entero, como hace Render, y vuelve todo."""

    @pytest.fixture(autouse=True)
    def entorno(self, tmp_path, monkeypatch, remoto):
        self.dir = tmp_path
        _aisla(tmp_path, monkeypatch, remoto)
        monkeypatch.setenv("VERTEX_DB_KEY", "clave-de-prueba-suficientemente-larga")

    def _sesion_uno(self):
        import vertex_api as V
        import vertex_cuentas as C
        from vertex_almacen import almacen

        V._arranca_almacen()
        V._archiva_acciones({"ticker": "AAPL", "recommendation": "Comprar",
                             "total_score": 78, "thesis": "Servicios crece 14%."})
        V._archiva_opciones({"ok": True, "ticker": "WULF", "score": 61,
                             "verdict": "Oportunidad Moderada", "spot": 18.42,
                             "scores": {"aggression": 7.0}})
        V.init_db()
        conn = V._db(); C.crear_tablas(conn); conn.commit()
        C.crear_usuario(conn, "ana@ejemplo.com", "Ana", "contrasena-larga-1234")
        conn.commit(); conn.close()
        almacen.sincroniza(incluir_series=True)

    def _borra_el_disco(self):
        import shutil

        shutil.rmtree(self.dir / "almacen", ignore_errors=True)
        for sufijo in ("", "-journal", "-wal", "-shm"):
            try:
                (self.dir / f"vertex.db{sufijo}").unlink()
            except OSError:
                pass
        # Y el almacén en memoria se reapunta, como haría un proceso nuevo.
        import vertex_almacen as VA

        VA.almacen = VA.Almacen()

    def test_vuelven_los_reportes_de_LOS_DOS_agentes(self):
        import vertex_api as V
        import vertex_archivo as AR

        self._sesion_uno()
        self._borra_el_disco()
        V._arranca_almacen()

        acc = AR.lista_reportes(AR.ACCIONES, alm=__import__("vertex_almacen").almacen)
        opc = AR.lista_reportes(AR.OPCIONES, alm=__import__("vertex_almacen").almacen)
        assert [(r["ticker"], r["titular"]) for r in acc] == [("AAPL", "Comprar · 78/100")]
        assert [(r["ticker"], r["titular"]) for r in opc] == \
            [("WULF", "Oportunidad Moderada · 61/100")]

    def test_vuelven_las_cuentas_Y_LA_CONTRASENA_SIGUE_SIRVIENDO(self):
        import vertex_api as V
        import vertex_cuentas as C

        self._sesion_uno()
        self._borra_el_disco()
        V._arranca_almacen()

        conn = V._db()
        try:
            u = C.autenticar(conn, "ana@ejemplo.com", "contrasena-larga-1234")
            assert u["nombre"] == "Ana"
        finally:
            conn.close()

    def test_vuelven_las_series_del_motor_de_victor(self):
        """Son las que encienden el sub-agente 6, el IV Rank real y la
        calibración: las tres necesitan DÍAS de historia y hasta ahora
        empezaban de cero en cada redeploy."""
        import vertex_api as V
        from vertex_almacen import almacen

        V._arranca_almacen()
        from wbj.tito import stores as st

        st.save_prediction("WULF", st.PredictionSnapshot(
            date="2026-08-07", horizon_days=20, spot=18.4, bear=16.1, base=19.3,
            bull=22.0, direction="up", confidence=48,
            saved_at="2026-08-07T14:00:00.000Z"))
        almacen.sincroniza(incluir_series=True)

        self._borra_el_disco()
        V._arranca_almacen()
        assert st.load_journal("WULF"), "el diario de predicciones no volvió"

    def test_las_series_apuntan_DENTRO_del_almacen(self):
        import vertex_api as V

        V._arranca_almacen()
        assert str(self.dir / "almacen") in os.environ["WBJ_TITO_DATA"]

    def test_una_base_CON_datos_no_se_pisa_con_la_del_remoto(self):
        """Un contenedor que ya trabajó no puede perder lo suyo porque llegue
        una foto vieja. Se comprueba con datos reales, no con el archivo: el
        esquema vacío que crea `init_db()` al importar no cuenta como datos —
        y confundir las dos cosas hacía que la restauración no actuara NUNCA."""
        import vertex_api as V
        import vertex_cuentas as C

        self._sesion_uno()
        conn = V._db()
        C.crear_usuario(conn, "nuevo@ejemplo.com", "Nuevo", "otra-clave-larga-99")
        conn.commit(); conn.close()

        assert V._base_con_datos() is True
        assert "ya tiene datos" in V._restaura_privado()
        conn = V._db()
        try:
            assert C.autenticar(conn, "nuevo@ejemplo.com", "otra-clave-larga-99")
        finally:
            conn.close()


class TestLaCuentaSobreviveOLoDICE:
    """«Creo una cuenta, cierro sesión, y al volver no me deja entrar».

    Es el fallo más caro del despliegue y el único que no se nota hasta que ya
    pasó. Las cuentas viajan en `Privado/privado.enc`, cifradas con
    `VERTEX_DB_KEY`. **Sin esa clave no se suben**, a propósito: un hash de
    contraseña en un repositorio —aunque sea privado— es un objetivo de fuerza
    bruta offline, y se prefiere perderlo a filtrarlo.

    Esa decisión es correcta. Lo que estaba mal es que se tomaba EN SILENCIO,
    justo en el instante en que el usuario cree lo contrario: registras, entra,
    funciona… y cuando Render se duerme —el plan free borra el disco al
    despertar— la cuenta se fue con él y el mensaje que recibes es «email o
    contraseña incorrectos».
    """

    @pytest.fixture(autouse=True)
    def entorno(self, tmp_path, monkeypatch, remoto):
        self.dir = tmp_path
        self.remoto = remoto
        _aisla(tmp_path, monkeypatch, remoto)

    def _cliente(self):
        from fastapi.testclient import TestClient

        import vertex_api as V

        V._arranca_almacen()
        V.init_db()
        return TestClient(V.app), V

    def test_con_la_clave_la_cuenta_sobrevive_al_borrado(self, monkeypatch):
        monkeypatch.setenv("VERTEX_DB_KEY", "clave-de-prueba-suficientemente-larga")
        c, V = self._cliente()
        r = c.post("/api/auth/registro", json={
            "email": "Kevin@Ejemplo.com ", "nombre": "Kevin",
            "password": "ClaveLarga123!"})
        assert r.json().get("ok") is True, r.json()
        from vertex_almacen import almacen
        almacen.sincroniza()

        # El disco se borra entero, como en un redeploy de Render.
        import shutil
        shutil.rmtree(self.dir / "almacen", ignore_errors=True)
        (self.dir / "vertex.db").unlink(missing_ok=True)
        import vertex_almacen as VA
        monkeypatch.setattr(VA, "almacen", VA.Almacen())
        c2, V2 = self._cliente()

        r2 = c2.post("/api/auth/entrar", json={
            "email": "kevin@ejemplo.com", "password": "ClaveLarga123!"})
        assert r2.json().get("ok") is True, (
            "la cuenta no sobrevivió al borrado del disco: " + str(r2.json()))

    def test_sin_la_clave_se_AVISA_al_crear_la_cuenta(self, monkeypatch):
        """Sin `VERTEX_DB_KEY` la cuenta no se respalda. Se puede vivir con
        eso; lo que no se puede es no decirlo."""
        monkeypatch.delenv("VERTEX_DB_KEY", raising=False)
        c, V = self._cliente()
        d = c.post("/api/auth/registro", json={
            "email": "kevin@ejemplo.com", "nombre": "Kevin",
            "password": "ClaveLarga123!"}).json()
        assert d.get("ok") is True
        aviso = d.get("aviso_persistencia") or ""
        assert aviso, "la cuenta no se respalda y no se avisa de nada"
        assert "VERTEX_DB_KEY" in aviso, "no dice qué falta"
        assert "registrarte de nuevo" in aviso, "no dice la consecuencia"

    def test_con_la_clave_no_inventa_un_aviso(self, monkeypatch):
        monkeypatch.setenv("VERTEX_DB_KEY", "clave-de-prueba-suficientemente-larga")
        c, V = self._cliente()
        d = c.post("/api/auth/registro", json={
            "email": "kevin@ejemplo.com", "nombre": "Kevin",
            "password": "ClaveLarga123!"}).json()
        assert not d.get("aviso_persistencia"), d.get("aviso_persistencia")

    def test_el_aviso_llega_ANTES_de_registrarse(self, monkeypatch):
        """`/api/auth/status` lo consulta la pantalla al cargar. Decirlo
        después de crear la cuenta llega tarde."""
        monkeypatch.delenv("VERTEX_DB_KEY", raising=False)
        c, V = self._cliente()
        d = c.get("/api/auth/status").json()
        assert d.get("aviso_persistencia"), "el login no avisa antes de registrar"

    def test_el_email_no_se_puede_repetir_ni_cambiando_mayusculas(self, monkeypatch):
        monkeypatch.setenv("VERTEX_DB_KEY", "clave-de-prueba-suficientemente-larga")
        c, V = self._cliente()
        assert c.post("/api/auth/registro", json={
            "email": "kevin@ejemplo.com", "nombre": "Kevin",
            "password": "ClaveLarga123!"}).json().get("ok") is True
        for repetido in ("kevin@ejemplo.com", "KEVIN@EJEMPLO.COM",
                         "  Kevin@Ejemplo.Com  "):
            d = c.post("/api/auth/registro", json={
                "email": repetido, "nombre": "Otro",
                "password": "OtraClave123!"}).json()
            assert d.get("ok") is False, f"aceptó el email repetido «{repetido}»"
            assert "Ya existe una cuenta" in (d.get("error") or "")

    def test_se_entra_escribiendo_el_email_como_sea(self, monkeypatch):
        monkeypatch.setenv("VERTEX_DB_KEY", "clave-de-prueba-suficientemente-larga")
        c, V = self._cliente()
        c.post("/api/auth/registro", json={
            "email": "kevin@ejemplo.com", "nombre": "Kevin",
            "password": "ClaveLarga123!"})
        for variante in ("kevin@ejemplo.com", "KEVIN@EJEMPLO.COM",
                         " Kevin@Ejemplo.Com "):
            d = c.post("/api/auth/entrar", json={
                "email": variante, "password": "ClaveLarga123!"}).json()
            assert d.get("ok") is True, f"no deja entrar con «{variante}»"

    def test_la_pantalla_pinta_el_aviso(self):
        html = (ROOT / "vertex_fund_os_platform.html").read_text(encoding="utf-8")
        assert "d.aviso_persistencia" in html, "el registro no lee el aviso"
        assert "function vxAvisoPersistencia(" in html
        assert "vxAvisoPersistencia(st.aviso_persistencia)" in html, \
            "el login no lo pinta antes de registrar"
        assert 'id="authPersistAviso"' in html


class TestElRemotoSeDeduceEnRender:
    """El fallo que hacía que NADA se guardara teniendo el token puesto.

    El almacén deducía el repositorio del `origin` del propio código, con
    `git remote get-url`. Eso exige tres cosas que en local se cumplen siempre
    y en Render pueden fallar todas: que el directorio desplegado traiga
    `.git`, que `git` esté en el PATH del proceso, y que la URL sea `https://`
    (una `git@github.com:…` se descartaba entera). Si cualquiera fallaba, la
    URL salía vacía, `respalda` daba `False`, y el operador veía un aviso
    genérico de «no se pudo deducir el repositorio» al lado de un token
    perfectamente válido.

    Resultado: se respaldaba en desarrollo y no en producción — que es el peor
    reparto posible, porque el sitio donde el disco SÍ se borra es el segundo.
    """

    @staticmethod
    def _alm(monkeypatch, **env):
        import vertex_almacen as AL

        for k in ("VERTEX_ALMACEN_REMOTO", "RENDER_GIT_REPO_SLUG", "VERTEX_GIT_TOKEN"):
            monkeypatch.delenv(k, raising=False)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        return AL.Almacen()

    def test_render_lo_dice_en_el_entorno_y_con_eso_basta(self, monkeypatch):
        """`RENDER_GIT_REPO_SLUG` no necesita `git`, ni `.git`, ni poder lanzar
        subprocesos: es el propio servicio diciendo de qué repo salió."""
        a = self._alm(monkeypatch, VERTEX_GIT_TOKEN="tok",
                      RENDER_GIT_REPO_SLUG="kevintaboas18/vertex_fund_os")
        assert a._repo_de_render() == "https://github.com/kevintaboas18/vertex_fund_os.git"
        assert a.respalda is True

    def test_no_construye_una_url_con_lo_que_venga(self, monkeypatch):
        """El slug entra en una URL: si no son dos segmentos limpios, no se usa."""
        for malo in ("", "solo-un-segmento", "a/b/c", "../../etc", "a b/c",
                     "https://otro.com/x/y"):
            a = self._alm(monkeypatch, VERTEX_GIT_TOKEN="tok",
                          RENDER_GIT_REPO_SLUG=malo)
            assert a._repo_de_render() == "", f"aceptó «{malo}»"

    def test_la_variable_declarada_manda_sobre_render(self, monkeypatch):
        a = self._alm(monkeypatch, VERTEX_GIT_TOKEN="tok",
                      RENDER_GIT_REPO_SLUG="otro/repo",
                      VERTEX_ALMACEN_REMOTO="https://github.com/mio/datos.git")
        assert "mio/datos" in a._url()

    @pytest.mark.parametrize("origin, esperado", [
        ("git@github.com:kevintaboas18/vertex_fund_os.git",
         "https://github.com/kevintaboas18/vertex_fund_os.git"),
        ("git@github.com:owner/repo", "https://github.com/owner/repo.git"),
        ("https://github.com/owner/repo.git", "https://github.com/owner/repo.git"),
        # Las credenciales que traiga se van: las pone `_url` con el token que
        # controla el operador.
        ("https://alguien:secreto@github.com/owner/repo.git",
         "https://github.com/owner/repo.git"),
        ("", ""),
    ])
    def test_traduce_el_origin_venga_como_venga(self, monkeypatch, origin, esperado):
        """La forma SSH se descartaba entera por no empezar con `https://`. Es
        el MISMO repositorio escrito de otra manera, y es el `origin` de media
        maquina de desarrollo."""
        import subprocess

        import vertex_almacen as AL

        class _R:
            stdout = origin + "\n"

        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _R())
        assert AL.Almacen._origin_del_codigo() == esperado

    def test_con_token_y_sin_remoto_el_motivo_dice_QUE_se_intento(self, monkeypatch):
        """«No se pudo deducir el repositorio» al lado de un token puesto se
        lee como una contradicción. Tiene que decir las tres fuentes que probó,
        o el operador no tiene por dónde empezar."""
        import vertex_almacen as AL

        a = self._alm(monkeypatch, VERTEX_GIT_TOKEN="tok")
        monkeypatch.setattr(AL.Almacen, "_origin_del_codigo", staticmethod(lambda: ""))
        m = a._motivo_apagado()
        assert "TIENES el token" in m
        assert "RENDER_GIT_REPO_SLUG" in m and "VERTEX_ALMACEN_REMOTO" in m
        assert "origin del código" in m

    def test_sin_token_sigue_diciendo_lo_de_siempre(self, monkeypatch):
        a = self._alm(monkeypatch)
        assert "VERTEX_GIT_TOKEN" in a._motivo_apagado()
        assert a.respalda is False


class TestLasSeriesCaenDentroDelAlmacenPaseLoQuePase:
    """`WBJ_TITO_DATA` se ponía DESPUÉS de restaurar.

    Si la restauración fallaba —red, token, rama— el proceso seguía vivo y
    sirviendo, pero todo lo que analizara a partir de ahí caía en
    `./data/tito`, fuera de lo que se respalda, y se perdía entero. El aviso
    que se pinta es el de la restauración, así que el segundo fallo viajaba
    escondido detrás del primero.
    """

    def test_la_ruta_se_fija_antes_de_restaurar(self):
        import pathlib
        import re

        raiz = pathlib.Path(__file__).resolve().parents[1]
        src = (raiz / "vertex_api.py").read_text(encoding="utf-8")
        i = src.index("def _arranca_almacen():")
        cuerpo = src[i:src.index("\n# ──", i)]
        i_var = cuerpo.index('os.environ.setdefault("WBJ_TITO_DATA"')
        i_res = cuerpo.index("_alm.restaura()")
        assert i_var < i_res, (
            "`WBJ_TITO_DATA` vuelve a fijarse después de restaurar: si la "
            "restauración falla, las series caen fuera del almacén y se pierden")


class TestLasRutas:
    @pytest.fixture(autouse=True)
    def entorno(self, tmp_path, monkeypatch, remoto):
        _aisla(tmp_path, monkeypatch, remoto)

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient

        import vertex_api as V

        V._arranca_almacen()
        return TestClient(V.app)

    def test_estado_del_almacen(self, client):
        d = client.get("/api/almacen").json()
        assert d["respalda"] is True
        assert d["rama"] == "datos"
        assert set(d["archivos"]) == {"acciones", "opciones", "memoria",
                                      "perfiles", "privado"}

    def test_el_estado_nunca_lleva_el_token(self, client):
        assert "x-access-token" not in client.get("/api/almacen").text

    def test_sincronizar_a_peticion(self, client):
        import vertex_api as V

        V._archiva_acciones({"ticker": "AAPL", "recommendation": "Comprar",
                             "total_score": 78})
        d = client.post("/api/almacen/sincronizar").json()
        assert d["ultimo_error"] is None
        assert d["pendientes"] == 0

    def test_la_lista_sale_del_DIRECTORIO_no_de_la_base(self, client):
        """Si SQLite desapareciera ahora mismo, esta ruta sigue contestando."""
        import vertex_api as V

        V._archiva_acciones({"ticker": "AAPL", "recommendation": "Comprar",
                             "total_score": 78})
        V._archiva_opciones({"ok": True, "ticker": "WULF", "score": 61,
                             "verdict": "Oportunidad Moderada"})
        acc = client.get("/api/archivo/acciones").json()
        opc = client.get("/api/archivo/opciones").json()
        assert acc["carpeta"] == "Reportes" and acc["n"] == 1
        assert opc["carpeta"] == "Proyecciones" and opc["n"] == 1
        assert acc["reportes"][0]["ticker"] == "AAPL"

    def test_un_agente_que_no_existe_es_400(self, client):
        assert client.get("/api/archivo/inventado").status_code == 400


class TestLosDosAgentesNoSeMezclan:
    @pytest.fixture
    def a(self, tmp_path):
        from vertex_almacen import Almacen

        x = Almacen(raiz=tmp_path / "almacen", token="")
        x.restaura()
        return x

    def test_cada_uno_en_su_carpeta(self, a):
        import vertex_archivo as AR

        AR.guarda_reporte_acciones("AAPL", {"recommendation": "Comprar"},
                                   cuando="2026-08-07", alm=a)
        AR.guarda_reporte_opciones("AAPL", {"verdict": "Neutral"},
                                   cuando="2026-08-07", alm=a)
        assert (a.raiz / "Reportes/AAPL/2026-08-07/reporte.json").is_file()
        assert (a.raiz / "Proyecciones/AAPL/2026-08-07/scorecard.json").is_file()

    def test_el_MISMO_ticker_el_MISMO_dia_no_se_pisa(self, a):
        """Los dos agentes pueden analizar AAPL hoy. Si compartieran archivo,
        el segundo borraría el primero — y son dos análisis distintos."""
        import vertex_archivo as AR

        AR.guarda_reporte_acciones("AAPL", {"recommendation": "Comprar"},
                                   cuando="2026-08-07", alm=a)
        AR.guarda_reporte_opciones("AAPL", {"verdict": "Neutral"},
                                   cuando="2026-08-07", alm=a)
        assert AR.lee_reporte(AR.ACCIONES, "AAPL", "2026-08-07", a)["recommendation"] == "Comprar"
        assert AR.lee_reporte(AR.OPCIONES, "AAPL", "2026-08-07", a)["verdict"] == "Neutral"

    def test_listar_uno_no_devuelve_al_otro(self, a):
        import vertex_archivo as AR

        AR.guarda_reporte_acciones("AAPL", {"recommendation": "Comprar"},
                                   cuando="2026-08-07", alm=a)
        AR.guarda_reporte_opciones("WULF", {"verdict": "Neutral"},
                                   cuando="2026-08-07", alm=a)
        assert [r["ticker"] for r in AR.lista_reportes(AR.ACCIONES, alm=a)] == ["AAPL"]
        assert [r["ticker"] for r in AR.lista_reportes(AR.OPCIONES, alm=a)] == ["WULF"]

    def test_cada_uno_tiene_SU_indice(self, a):
        import vertex_archivo as AR

        AR.guarda_reporte_acciones("AAPL", {"recommendation": "Comprar"},
                                   cuando="2026-08-07", alm=a)
        AR.guarda_reporte_opciones("WULF", {"verdict": "Neutral"},
                                   cuando="2026-08-07", alm=a)
        i_acc = (a.raiz / "Reportes/INDICE.md").read_text(encoding="utf-8")
        i_opc = (a.raiz / "Proyecciones/INDICE.md").read_text(encoding="utf-8")
        assert "ACCIONES" in i_acc and "AAPL" in i_acc and "WULF" not in i_acc
        assert "OPCIONES" in i_opc and "WULF" in i_opc and "AAPL" not in i_opc

    def test_el_indice_se_reconstruye_entero_y_no_miente(self, a):
        """Un índice que se edita por incrementos acaba citando reportes que ya
        no están, y entonces hay que abrir cada uno para saber cuáles existen —
        que es lo que un índice viene a evitar."""
        import vertex_archivo as AR

        AR.guarda_reporte_acciones("AAPL", {"recommendation": "Comprar"},
                                   cuando="2026-08-07", alm=a)
        AR.guarda_reporte_acciones("NVDA", {"recommendation": "Evitar"},
                                   cuando="2026-08-07", alm=a)
        (a.raiz / "Reportes/AAPL/2026-08-07/reporte.json").unlink()
        idx = AR.reconstruye_indice(AR.ACCIONES, alm=a)
        assert "NVDA" in idx and "AAPL" not in idx

    def test_el_resumen_es_legible_sin_abrir_el_json(self, a):
        import vertex_archivo as AR

        AR.guarda_reporte_opciones("WULF", {
            "verdict": "Oportunidad Moderada", "score": 61, "spot": 18.42,
            "scores": {"aggression": 7.0, "validation": None},
            "predictions": {"20": {"bear": {"target": 16.1}, "base": {"target": 19.3},
                                   "bull": {"target": 22.0}, "confidence": 48}},
        }, cuando="2026-08-07", alm=a)
        md = (a.raiz / "Proyecciones/WULF/2026-08-07/RESUMEN.md").read_text(encoding="utf-8")
        assert md.startswith("# WULF · agente de OPCIONES · 2026-08-07")
        assert "Agresividad" in md and "20 pts" in md
        # Los pesos salen del MOTOR, no escritos a mano en el resumen. Escritos
        # a mano estaban mal: llevaban los del agente de ACCIONES, así que
        # cuatro de los seis mentían.
        from wbj.tito.prediction import WEIGHTS

        for clave, peso in WEIGHTS.items():
            nombre = {"aggression": "Agresividad", "conviction": "Convicción",
                      "unusuality": "Inusualidad", "structure": "Estructura",
                      "iv_context": "Contexto IV",
                      "validation": "Confirmación de precio"}[clave]
            if clave in ("aggression", "validation"):   # los dos del payload
                assert f"| {nombre} |" in md
        assert sum(WEIGHTS.values()) == 100
        assert "$19.30" in md, "los escenarios tienen que verse"
        assert "no una orden de compra" in md

    def test_el_resumen_lleva_niveles_flujos_y_track_record(self, a):
        """Las tres cosas que el `scorecard.json` ya traía y el resumen callaba.

        Un `RESUMEN.md` es lo que se lee dentro de tres meses. Con el score y
        los escenarios solos no se puede decidir nada: falta dónde están los
        niveles, de qué contratos salió el flujo y —lo que más pesa— si este
        agente ha acertado antes. Los tres campos viajaban en el archivo desde
        hacía rondas; el resumen no los leía.
        """
        import vertex_archivo as AR

        AR.guarda_reporte_opciones("WULF", {
            "verdict": "Alcista", "score": 74, "spot": 18.42,
            # Los NOMBRES son los que sirve `/api/projection-targets`, no unos
            # parecidos — ese fue el fallo que este test existe para cazar. Y
            # `touch` es una FRACCIÓN 0-1, como la manda `prob_touch`.
            "levels": [{"price": 17.5, "kind": "soporte", "strength": 62,
                        "touch": 0.81, "why": "muro de puts", "flipped": False,
                        "distance_pct": -5.0},
                       {"price": 21.0, "kind": "resistencia", "strength": 38,
                        "touch": None, "why": "gamma flip", "flipped": False,
                        "distance_pct": 14.0}],
            "top_flows": [{"id": 1, "type": "call", "strike": 20, "dte": 160,
                           "expiration": "2027-01-15", "premium": 2_075_000,
                           "aggression": "ask", "alcista": True}],
            "memory": {"stats": {
                "predicciones_vencidas": 12, "dir_hit_rate": 58, "sesgo_pct": 6.1,
                "evals": [{"date": "2026-07-10", "horizon_days": 20, "spot": 16.4,
                           "base": 17.0, "actual_close": 18.1, "error_pct": -6.1,
                           "best": "bull", "direction_hit": True, "matured": True}],
            }},
        }, cuando="2026-08-07", alm=a)
        md = (a.raiz / "Proyecciones/WULF/2026-08-07/RESUMEN.md").read_text(encoding="utf-8")
        # Niveles, CON su probabilidad de toque: una fuerza de 62 al 81% y otra
        # al 38% no son el mismo dato, y sin la columna se leen igual.
        assert "Niveles importantes" in md
        assert "$17.50" in md and "muro de puts" in md
        assert "81%" in md, "`touch` es 0-1: sin el ×100 el resumen escribe «1%»"
        # Un nivel sin P(toque) dice «—», no un número inventado.
        assert "| 38 | — |" in md
        # Los flujos que sostienen el score.
        assert "WULF $20.00C" in md and "2027-01-15 (160d)" in md and "alcista" in md
        assert "$2,075,000" in md
        # Y el track record, que es lo que calibra la confianza en el resto.
        assert "Track record" in md and "58%" in md and "+6.1%" in md
        # La FILA entera, no sus trozos sueltos: así un nombre de clave
        # equivocado (`horizon` por `horizon_days`, `actual` por `actual_close`)
        # cambia la fila y esto falla, en vez de dejar un «—» que se ve normal.
        assert "| 2026-07-10 | 20d | $17.00 | $18.10 | -6.1% | bull |" in md

    def test_los_nombres_del_resumen_son_los_que_sirve_la_ruta(self):
        """El resumen lee del payload archivado por NOMBRE. Un nombre parecido
        pero distinto (`horizon` por `horizon_days`, `actual` por
        `actual_close`) no rompe nada: pinta «—» y el archivo se ve bien.

        Esto ata las claves que `_md_opciones` lee a las que `vertex_api`
        escribe, mirando el código de las dos partes. Si una de las dos cambia
        de nombre, esto falla en vez de dejar una columna muda para siempre.
        """
        import pathlib
        import re

        raiz = pathlib.Path(__file__).resolve().parents[1]
        arch = (raiz / "vertex_archivo.py").read_text(encoding="utf-8")
        api = (raiz / "vertex_api.py").read_text(encoding="utf-8")
        cuerpo = arch[arch.index("def _md_opciones("):arch.index("_RESUMEN[ACCIONES]")]
        # Las dos formas de citar la clave: `.get("x")` y `.get('x')`. Con solo
        # una, este test pasaba sin mirar nada — el resumen usa comillas simples
        # dentro de las f-strings.
        leidas = set(re.findall(r"""\.get\(["']([a-z_]+)["']""", cuerpo))
        assert len(leidas) >= 15, f"el escaneo solo vio {len(leidas)} claves"
        # `fecha` la pone el propio archivo al guardar, no la ruta.
        for clave in sorted(leidas - {"fecha"}):
            assert f'"{clave}"' in api, (
                f"`_md_opciones` lee «{clave}» y `vertex_api.py` no lo escribe "
                "en ningún sitio: la columna saldría muda para siempre")

    def test_el_resumen_sin_track_record_lo_dice_en_vez_de_callarse(self, a):
        """Un reporte del primer día no tiene predicciones vencidas. La tabla
        vacía sin frase se lee como «el agente no se mide»."""
        import vertex_archivo as AR

        AR.guarda_reporte_opciones("WULF", {"verdict": "Neutral", "score": 50},
                                   cuando="2026-08-07", alm=a)
        md = (a.raiz / "Proyecciones/WULF/2026-08-07/RESUMEN.md").read_text(encoding="utf-8")
        assert "todavía no hay predicciones vencidas" in md
        assert "sin niveles" in md and "sin flujos notables" in md

    @pytest.mark.parametrize("malo", ["", "   ", "!!!", "../etc", "a" * 40])
    def test_un_ticker_imposible_se_rechaza(self, a, malo):
        import vertex_archivo as AR

        with pytest.raises(ValueError):
            AR.guarda_reporte_acciones(malo, {"x": 1}, alm=a)

    def test_el_ticker_se_normaliza(self, a):
        """`aapl` y ` AAPL ` son el mismo símbolo: dos carpetas partirían el
        historial en dos sin que nadie lo notara."""
        import vertex_archivo as AR

        AR.guarda_reporte_acciones(" aapl ", {"recommendation": "Comprar"},
                                   cuando="2026-08-07", alm=a)
        assert (a.raiz / "Reportes/AAPL/2026-08-07/reporte.json").is_file()


class TestElArchivoNoPesaDeMas:
    def test_el_scorecard_archivado_NO_lleva_la_cadena(self):
        """1.500 filas por reporte son 372 KB; con 20 tickers, 1,8 GB al año.
        Es materia prima que Massive vuelve a servir, no evidencia del
        veredicto."""
        import vertex_api as V

        recortado = V._sin_derivado({
            "ok": True, "ticker": "WULF", "score": 61,
            "chain": [{"x": 1}] * 1500, "history": [{"t": 1}] * 70,
            "gex_heatmap": {"cells": [1] * 500},
            "scores": {"aggression": 7.0}, "warnings": ["algo"],
            "conviction_rows": [{"id": "t1"}],
        })
        for fuera in V._OPCIONES_NO_SE_ARCHIVA:
            assert fuera not in recortado
        # Y lo que sostiene el veredicto SÍ se queda.
        for dentro in ("scores", "warnings", "conviction_rows", "score"):
            assert dentro in recortado
        assert recortado["_no_archivado"] == list(V._OPCIONES_NO_SE_ARCHIVA), \
            "lo que falta se DECLARA; el archivo no miente por omisión"

    def test_el_reporte_de_acciones_va_ENTERO(self, tmp_path):
        """El tope de 2 MB era un límite de la COLUMNA de SQLite, no del dato."""
        import vertex_archivo as AR
        from vertex_almacen import Almacen

        a = Almacen(raiz=tmp_path / "almacen", token=""); a.restaura()
        gordo = {"recommendation": "Comprar", "thesis": "x" * 3_000_000}
        AR.guarda_reporte_acciones("AAPL", gordo, cuando="2026-08-07", alm=a)
        leido = AR.lee_reporte(AR.ACCIONES, "AAPL", "2026-08-07", a)
        assert len(leido["thesis"]) == 3_000_000
