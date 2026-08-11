"""El correo pre-market nunca se envio. Tres cosas lo impedian a la vez.

Los tres se confirmaron leyendo los logs reales del workflow, no razonando
sobre el codigo:

1. **La puerta horaria no se abria nunca.** El guion exigia `now.hour == 8` ET
   porque un comentario decia que el workflow corria a las 12:00 y 13:00 UTC.
   No existia tal cosa: hay un solo cron, y GitHub lo dispara tarde y con
   deriva -- se le vio a las 12:08 y a las 13:52 UTC con el mismo `30 11`. El
   log del 7 de agosto lo dice entero: "Son las 09:52 ET, no las 8 - skip".
   Y como saltarse el envio devuelve 0, el workflow salia **en verde**. Meses
   de palomitas verdes sin un solo correo.

2. **La fuente devolvia 403 al runner.** stockanalysis.com responde a la IP de
   casa y bloquea las de GitHub Actions. Ademas no es una de las cuatro
   fuentes del proyecto. Ahora los movers salen de FMP.

3. **El destinatario llegaba vacio.** `EMAIL_TO: ${{ vars.EMAIL_TO }}` con la
   variable sin definir inyecta cadena VACIA, y `os.environ.get(k, default)`
   devuelve "" -- no el default -- cuando la clave existe vacia. Resend
   recibia `"to": [""]`.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

class _Respuesta:
    """Lo minimo que `urlopen` devuelve como gestor de contexto, apuntando el
    cuerpo enviado para poder mirarlo despues."""

    def __init__(self, apuntes, req):
        apuntes.append(json.loads(req.data.decode()))
        self.status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return b"{}"


RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))


def _modulo(monkeypatch, **entorno):
    """Recarga el modulo: EMAIL_TO y compania se leen al importar."""
    for k in ("EMAIL_TO", "EMAIL_FROM", "FMP_API_KEY", "RESEND_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    for k, v in entorno.items():
        monkeypatch.setenv(k, v)
    import premarket_email
    return importlib.reload(premarket_email)


def test_una_variable_vacia_no_borra_el_destinatario(monkeypatch):
    """Lo que hacia que Resend contestara 422."""
    pe = _modulo(monkeypatch, EMAIL_TO="")
    assert pe.EMAIL_TO == "kevintaboas02@gmail.com"


def test_el_destinatario_configurado_manda(monkeypatch):
    pe = _modulo(monkeypatch, EMAIL_TO="otro@ejemplo.com")
    assert pe.EMAIL_TO == "otro@ejemplo.com"


def test_la_ventana_absorbe_la_deriva_del_cron(monkeypatch):
    """8:00 ET en punto era la unica hora aceptada y GitHub no la da nunca.
    Las horas de las corridas reales que se saltaron el envio -- 9:52 y 8:08
    ET -- tienen que entrar ahora."""
    pe = _modulo(monkeypatch)
    for hora in (6, 7, 8, 9, 10):
        assert hora in pe.VENTANA_ET, f"{hora} ET quedaria fuera"
    for hora in (5, 11, 15, 23):
        assert hora not in pe.VENTANA_ET, f"{hora} ET no es pre-market"


def test_los_movers_salen_de_fmp_no_de_una_web_raspada(monkeypatch):
    """El raspado de stockanalysis.com daba 403 desde el runner. Si alguien lo
    devuelve, este test cae."""
    pe = _modulo(monkeypatch, FMP_API_KEY="x")
    assert "financialmodelingprep.com" in pe.FMP_BASE
    # La URL, no la palabra: el comentario que explica por que se abandono la
    # fuente la nombra, y esa prosa es justamente lo que hay que conservar.
    fuente = Path(pe.__file__).read_text(encoding="utf-8")
    assert "https://stockanalysis.com" not in fuente


def test_una_fila_rota_no_tumba_el_correo(monkeypatch):
    """FMP mete ETFs y tickers raros; una fila sin precio no puede costar el
    envio entero de las otras nueve."""
    pe = _modulo(monkeypatch, FMP_API_KEY="x")
    monkeypatch.setattr(pe, "fetch_json", lambda p: [
        {"symbol": "AAA", "name": "Buena", "price": 10.0, "changesPercentage": 5.0},
        {"symbol": "BBB", "name": "Rota", "price": None, "changesPercentage": None},
    ])
    monkeypatch.setattr(pe, "market_cap", lambda t: 2e10)
    filas = pe.movers("biggest-gainers")
    assert [f["ticker"] for f in filas] == ["AAA"]


def test_sin_clave_de_resend_el_error_dice_donde_ponerla(monkeypatch):
    """Antes era un KeyError pelado en el log del runner."""
    pe = _modulo(monkeypatch)
    with pytest.raises(RuntimeError, match="Secrets"):
        pe.send_resend("s", "t", "<p>h</p>", ["ana@ejemplo.com"])


# --- a quien se le manda ---------------------------------------------------

def test_cada_cuenta_recibe_en_su_propio_correo(monkeypatch):
    """Lo que pidio Victor: si tu cuenta es la tuya, el correo va a tu email."""
    pe = _modulo(monkeypatch)
    monkeypatch.setattr(pe, "_emails_de_las_cuentas",
                        lambda: ["ana@ejemplo.com", "bea@ejemplo.com"])
    assert pe.destinatarios() == ["ana@ejemplo.com", "bea@ejemplo.com"]


def test_sin_cuentas_legibles_se_cae_a_email_to(monkeypatch):
    """Sin VERTEX_DB_KEY, sin respaldo o sin `cryptography` no se pierde el
    correo: se manda a donde se mandaba siempre."""
    pe = _modulo(monkeypatch, EMAIL_TO="uno@ejemplo.com, dos@ejemplo.com")
    monkeypatch.setattr(pe, "_emails_de_las_cuentas", lambda: [])
    assert pe.destinatarios() == ["uno@ejemplo.com", "dos@ejemplo.com"]


def test_nadie_ve_el_correo_de_los_demas(monkeypatch):
    """Un solo envio con todos en el `to` le enseña a cada usuario los emails
    del resto. Son cuentas de desconocidos entre si: eso es una fuga."""
    pe = _modulo(monkeypatch, RESEND_API_KEY="x")
    envios = []
    monkeypatch.setattr(pe.urllib.request, "urlopen",
                        lambda req, timeout=0: _Respuesta(envios, req))
    n = pe.send_resend("s", "t", "<p>h</p>", ["ana@x.com", "bea@x.com"])
    assert n == 2
    for cuerpo in envios:
        assert len(cuerpo["to"]) == 1, f"varios destinatarios en un envio: {cuerpo['to']}"
    assert [c["to"][0] for c in envios] == ["ana@x.com", "bea@x.com"]


def test_un_destinatario_que_rebota_no_deja_sin_correo_a_los_demas(monkeypatch):
    """El remitente de pruebas de Resend solo puede escribirle al dueño de la
    cuenta, asi que los rebotes son el caso NORMAL hasta verificar un dominio.
    Uno no puede cancelar a los otros."""
    pe = _modulo(monkeypatch, RESEND_API_KEY="x")
    envios = []

    def _urlopen(req, timeout=0):
        cuerpo = json.loads(req.data.decode())
        if cuerpo["to"][0].startswith("mala"):
            raise RuntimeError("422 no puedes escribir a ese destinatario")
        return _Respuesta(envios, req)

    monkeypatch.setattr(pe.urllib.request, "urlopen", _urlopen)
    assert pe.send_resend("s", "t", "h", ["mala@x.com", "buena@x.com"]) == 1
    assert [c["to"][0] for c in envios] == ["buena@x.com"]


def test_si_no_sale_ni_uno_el_workflow_tiene_que_salir_en_rojo(monkeypatch):
    """Cero de N enviados en verde es el fallo que costo meses de silencio."""
    pe = _modulo(monkeypatch, RESEND_API_KEY="x", FMP_API_KEY="y")
    monkeypatch.setattr(pe, "movers", lambda cual, limit=10: [
        {"ticker": "AAA", "name": "A", "pct": 5.0, "price": "10.00", "mcap": 2e10}])
    monkeypatch.setattr(pe, "destinatarios", lambda: ["ana@x.com"])
    monkeypatch.setattr(pe, "send_resend", lambda *a, **k: 0)
    monkeypatch.setenv("FORCE", "1")
    monkeypatch.delenv("DRY_RUN", raising=False)
    assert pe.main() == 1


# --- el envio desde la app -------------------------------------------------
#
# GitHub Actions no ve las variables de Render. Tenerlas puestas en el
# dashboard no hacia nada por el workflow, y el arreglo no era duplicarlas en
# los secrets del repositorio para siempre: era mover el envio a donde ya
# viven. Estos tests cubren el endpoint que lo hace.

from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "engine"))
import vertex_api as V  # noqa: E402

_cliente = TestClient(V.app)
class _FalsaDB:
    """`_db()` entrega una conexion cuyo `execute` devuelve un CURSOR. Devolver
    la lista directamente hacia que `.fetchall()` reventara, el endpoint se
    cayera a EMAIL_TO por su `except`, y el test midiera el camino equivocado
    creyendo que medía el bueno."""

    def __init__(self, filas):
        self._filas = filas

    def execute(self, *a):
        return self

    def fetchall(self):
        return self._filas

    def close(self):
        pass


_FILA = {"ticker": "AAA", "name": "Alfa", "pct": 5.0, "price": "10.00", "mcap": 2e10}


@pytest.fixture
def app_lista(monkeypatch):
    """FMP y Resend sustituidos; la ventana horaria se salta con `forzar`."""
    import premarket_email as pm
    monkeypatch.setattr(pm, "movers", lambda cual, limit=10: [dict(_FILA)])
    enviados = []

    def _enviar(asunto, texto, html, para, motivos=None):
        enviados.extend(para)
        return len(para)

    monkeypatch.setattr(pm, "send_resend", _enviar)
    return enviados


def test_el_endpoint_manda_al_email_de_cada_cuenta(app_lista, monkeypatch):
    """La base esta VIVA aqui: los correos salen de `usuarios`, sin descifrar
    el respaldo ni bajar la rama `datos`."""
    _conn = _FalsaDB([("ana@ejemplo.com",), ("bea@ejemplo.com",)])

    monkeypatch.setattr(V, "_db", lambda: _conn)
    r = _cliente.post("/api/premarket/enviar?forzar=true")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["enviados"] == 2 and d["fuente"] == "cuentas"
    assert app_lista == ["ana@ejemplo.com", "bea@ejemplo.com"]


def test_sin_cuentas_todavia_se_cae_a_email_to(app_lista, monkeypatch):
    _conn = _FalsaDB([])

    monkeypatch.setattr(V, "_db", lambda: _conn)
    d = _cliente.post("/api/premarket/enviar?forzar=true").json()
    assert d["fuente"] == "EMAIL_TO" and d["enviados"] == 1


def test_en_seco_dice_a_quien_iria_sin_mandarlo(app_lista, monkeypatch):
    _conn = _FalsaDB([("ana@ejemplo.com",)])

    monkeypatch.setattr(V, "_db", lambda: _conn)
    d = _cliente.post("/api/premarket/enviar?forzar=true&seco=true").json()
    assert d["para"] == ["ana@ejemplo.com"]
    assert app_lista == [], "el modo seco mando el correo de verdad"


def test_cero_enviados_contesta_error_no_exito(monkeypatch):
    """Un 200 con cero correos deja el workflow en verde. Ese es el fallo
    original, y no puede volver por la puerta de atras."""
    import premarket_email as pm
    monkeypatch.setattr(pm, "movers", lambda cual, limit=10: [dict(_FILA)])
    monkeypatch.setattr(pm, "send_resend", lambda *a, **k: 0)

    _conn = _FalsaDB([("ana@ejemplo.com",)])

    monkeypatch.setattr(V, "_db", lambda: _conn)
    assert _cliente.post("/api/premarket/enviar?forzar=true").status_code == 502


def test_fuera_de_la_ventana_no_manda_pero_no_es_un_error(monkeypatch):
    """Que no toque no es un fallo: no debe pintar el workflow de rojo."""
    import premarket_email as pm
    monkeypatch.setattr(pm, "VENTANA_ET", range(3, 4))
    r = _cliente.post("/api/premarket/enviar")
    assert r.status_code == 200 and r.json()["enviados"] == 0
    assert "ventana" in r.json()["motivo"] or "cerrado" in r.json()["motivo"]


def test_el_motivo_del_rechazo_llega_a_quien_dispara(monkeypatch):
    """Resend explica el rechazo en el CUERPO del 4xx; la excepcion solo dice
    "HTTP Error 403: Forbidden". Sin leer el cuerpo, quien dispara el workflow
    ve "no se acepto" y tiene que irse a los logs del servidor a adivinar.

    Paso real: el envio salio 502 y el motivo -- que el remitente de pruebas
    solo escribe al dueño de la cuenta -- se quedo en Render.
    """
    import premarket_email as pm

    class _Http(Exception):
        def read(self):
            return json.dumps({"message": "You can only send testing emails "
                                          "to your own email address"}).encode()

    monkeypatch.setenv("RESEND_API_KEY", "x")
    monkeypatch.setattr(pm.urllib.request, "urlopen",
                        lambda req, timeout=0: (_ for _ in ()).throw(_Http()))
    motivos = []
    assert pm.send_resend("s", "t", "h", ["ana@x.com"], motivos) == 0
    assert "ana@x.com" in motivos[0]
    assert "your own email address" in motivos[0], (
        f"el motivo de Resend se perdio: {motivos}")


def test_el_codigo_http_sale_aunque_el_cuerpo_venga_vacio(monkeypatch):
    """Lo que dejo el diagnostico a ciegas: el log decia "HTTPError" a secas.

    Un 403 (no puedes escribir a ese destinatario), un 422 (remitente
    invalido) y un 429 (cuota) se arreglan de tres formas distintas, y sin el
    codigo son indistinguibles. El cuerpo puede fallar; el codigo siempre esta.
    """
    import premarket_email as pm

    class _SinCuerpo(Exception):
        code = 403
        reason = "Forbidden"

        def read(self):
            return b""

    monkeypatch.setenv("RESEND_API_KEY", "x")
    monkeypatch.setattr(pm.urllib.request, "urlopen",
                        lambda req, timeout=0: (_ for _ in ()).throw(_SinCuerpo()))
    motivos = []
    pm.send_resend("s", "t", "h", ["ana@x.com"], motivos)
    assert "403" in motivos[0], f"se perdio el codigo: {motivos}"
    assert "Forbidden" in motivos[0]


def test_un_cuerpo_que_no_es_json_tampoco_se_tira(monkeypatch):
    """Un proxy o un balanceador puede contestar HTML. Sirve igual: dice mas
    que el nombre de la clase de la excepcion."""
    import premarket_email as pm

    class _Html(Exception):
        code = 502
        reason = "Bad Gateway"

        def read(self):
            return b"<html>upstream timeout</html>"

    monkeypatch.setenv("RESEND_API_KEY", "x")
    monkeypatch.setattr(pm.urllib.request, "urlopen",
                        lambda req, timeout=0: (_ for _ in ()).throw(_Html()))
    motivos = []
    pm.send_resend("s", "t", "h", ["ana@x.com"], motivos)
    assert "502" in motivos[0] and "upstream timeout" in motivos[0]


def test_toda_peticion_saliente_se_identifica(monkeypatch):
    """Cloudflare atiende delante de la API de Resend y rechaza el
    `Python-urllib/3.11` que urllib manda por defecto: "403, error code: 1010",
    acceso bloqueado por la firma del cliente.

    Ni la clave, ni el destinatario, ni el remitente llegaban a evaluarse -- y
    el fallo se veia igual que "Resend no acepta tu correo", que mando a
    revisar tres cosas que estaban bien. A FMP y al almacen ya se les mandaba
    User-Agent; aqui faltaba.
    """
    import premarket_email as pm
    vistos = []

    class _Ok:
        def __init__(self, req):
            vistos.append(dict(req.headers))
            self.status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"{}"

    monkeypatch.setenv("RESEND_API_KEY", "x")
    monkeypatch.setattr(pm.urllib.request, "urlopen", lambda req, timeout=0: _Ok(req))
    pm.send_resend("s", "t", "h", ["ana@x.com"])
    # urllib normaliza las cabeceras a Capitalizado.
    assert any(k.lower() == "user-agent" for k in vistos[0]), (
        f"peticion sin identificar: {list(vistos[0])}")


def test_fmp_tambien_se_identifica(monkeypatch):
    """El mismo cuidado en la otra salida a internet, para que no se repita."""
    import premarket_email as pm
    vistos = []

    class _Ok:
        def __init__(self, req):
            vistos.append(dict(req.headers))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"[]"

    monkeypatch.setattr(pm, "FMP_API_KEY", "x")
    monkeypatch.setattr(pm.urllib.request, "urlopen", lambda req, timeout=0: _Ok(req))
    pm.fetch_json("biggest-gainers")
    assert any(k.lower() == "user-agent" for k in vistos[0])
