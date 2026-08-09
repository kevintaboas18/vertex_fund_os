"""Un `null` no era prueba de que el mercado no existiera.

El resolutor preguntaba UNA vez a cada proveedor y se quedaba con la primera
respuesta legible. Con OpenAI sin créditos eso es un único intento, y el modelo
que queda lleva búsqueda web: no es determinista.

Medido, preguntando cuatro veces seguidas por la misma industria:

  - **Consumer Electronics** — marcada durante semanas como «ninguna asociación
    ni casa de la lista publica este mercado». El 2º intento devolvió
    $783.000M de Gartner (Worldwide Devices Market Spend) y pasó la validación
    entera. Los otros tres fueron `null`. Esa industria estaba declarada
    imposible por una sola tirada de dados.
  - **Beverages - Non-Alcoholic** — dos intentos, dos mercados distintos:
    $418.000M de NielsenIQ (tier 2) y $141.700M de Frost & Sullivan (tier 3).
    Los dos válidos por separado. Con «la primera que llegue», el TAM de una
    industria dependía del orden en que respondiera el modelo.

De ahí las dos reglas que fijan estos tests: se pregunta varias veces, y gana
el **tier más bajo**, no el primero — `DECISION_RULES.md` pone la asociación de
industria por encima de la casa de análisis porque mide su propio mercado en
vez de resumir el de otros.

Y una tercera que salió de un error propio: reintentar un límite de cuota lo
empeora. Las pruebas de este mismo reintento agotaron la cuota de Gemini, y las
tres industrias siguientes gastaron cuatro peticiones cada una para recibir
cuatro veces el mismo 429.
"""

from __future__ import annotations

import pytest

from wbj.overlay import tam_mundial as tm


class _S:
    inputs_dir = "."
    gemini_api_key = "x"
    openai_api_key = None


def _respuesta(fuente: str, tam: float) -> str:
    import json
    return json.dumps({
        "tam": tam, "tam_source": fuente, "ambito": "mundial",
        "cita": "https://ejemplo.org/informe",
        "capa": "ingresos anuales del mercado"})

@pytest.fixture(autouse=True)
def _sin_red(monkeypatch):
    """Los tests no salen a internet.

    `_validar` comprueba la cifra contra la pagina de su fuente -- ver
    `_verificar_en_la_fuente` y la regla del `judge.py` de Victor, "Nunca
    inventes cifras". Eso es una descarga, y una suite que la hace deja de ser
    determinista y tarda minuto y medio. Aqui se sustituye por un verificador
    que acepta, para que cada test siga midiendo lo suyo; la verificacion
    tiene sus propios tests en `test_tam_verificado_en_la_fuente.py`.
    """
    monkeypatch.setattr(tm, "_verificar_en_la_fuente",
                        lambda cita, tam, fuente: (True, cita))



def _guion(monkeypatch, respuestas: list[tuple[str, str | None]]):
    """`respuestas` es lo que devuelve Gemini en cada llamada sucesiva."""
    turnos = iter(respuestas)

    def _falso(settings, prompt):
        try:
            return next(turnos)
        except StopIteration:
            return "", "gemini: sin mas respuestas"

    monkeypatch.setattr(tm, "_preguntar_gemini", _falso)
    monkeypatch.setattr(tm, "_preguntar_openai",
                        lambda s, p: ("", "openai: sin clave"))


# --- un null no es prueba --------------------------------------------------

def test_a_single_null_does_not_condemn_the_market(monkeypatch):
    """El caso de Consumer Electronics: falla, falla, acierta. Con un intento
    la industria se quedaba sin TAM para siempre."""
    _guion(monkeypatch, [
        ('{"tam": null}', None),
        ('{"tam": null}', None),
        (_respuesta("Gartner", 783_000_000_000), None),
    ])
    datos, _ = tm._investigar(_S(), "Consumer Electronics", "AAPL")
    assert datos is not None and datos["tam"] == 783_000_000_000


def test_all_nulls_still_mean_no(monkeypatch):
    """Reintentar no es insistir hasta que salga algo: si las cuatro vueltas
    vienen vacías, la respuesta sigue siendo que nadie lo publica."""
    _guion(monkeypatch, [('{"tam": null}', None)] * 6)
    datos, fallos = tm._investigar(_S(), "Oil & Gas Integrated", "XOM")
    assert datos is None
    assert any("sin cifra atribuible" in f for f in fallos)


# --- gana el mejor tier, no el primero -------------------------------------

def test_an_association_beats_a_research_house_whatever_the_order(monkeypatch):
    """El caso de las bebidas. Si ganara la primera, el TAM de una industria
    dependería del orden de las respuestas."""
    _guion(monkeypatch, [
        (_respuesta("Frost & Sullivan", 141_700_000_000), None),   # tier 3
        (_respuesta("NielsenIQ", 418_000_000_000), None),          # tier 3
        (_respuesta("WSTS", 630_500_000_000), None),               # tier 2
    ])
    datos, _ = tm._investigar(_S(), "Semiconductors", "NVDA")
    assert datos["tam_source"] == "WSTS", (
        "gano una casa de analisis teniendo la asociacion de industria detras")


def test_it_stops_as_soon_as_an_association_answers(monkeypatch):
    """Por encima del tier 2 no hay nada que buscar: seguir preguntando gasta
    cuota para no mejorar."""
    llamadas = {"n": 0}

    def _falso(settings, prompt):
        llamadas["n"] += 1
        return _respuesta("WSTS", 630_500_000_000), None

    monkeypatch.setattr(tm, "_preguntar_gemini", _falso)
    monkeypatch.setattr(tm, "_preguntar_openai", lambda s, p: ("", "openai: sin clave"))
    tm._investigar(_S(), "Semiconductors", "NVDA")
    assert llamadas["n"] == 1


def test_a_rejected_answer_never_wins(monkeypatch):
    """El reintento no relaja la validación. Una capitalización bursátil sigue
    siendo un acervo aunque llegue primera y aunque sea de un tier 2."""
    _guion(monkeypatch, [
        ('{"tam": 252000000000, "tam_source": "Nareit", "ambito": "mundial",'
         ' "cita": "https://reit.com", "capa": "market capitalization"}', None),
        (_respuesta("JLL", 1_800_000_000_000), None),
    ])
    datos, _ = tm._investigar(_S(), "REIT - Retail", "O")
    assert datos is not None and datos["tam_source"] == "JLL"


# --- la cuota corta, el contenido no ---------------------------------------

@pytest.mark.parametrize("error", [
    "gemini: ClientError 429 RESOURCE_EXHAUSTED",
    "openai: RateLimitError You have no credits remaining",
    "gemini: quota exceeded",
])
def test_a_quota_error_stops_the_retries(monkeypatch, error):
    """Reintentar contra el contador que acaba de rechazarte lo empeora."""
    llamadas = {"n": 0}

    def _falso(settings, prompt):
        llamadas["n"] += 1
        return "", error

    monkeypatch.setattr(tm, "_preguntar_gemini", _falso)
    monkeypatch.setattr(tm, "_preguntar_openai", lambda s, p: ("", "openai: sin clave"))
    tm._investigar(_S(), "Semiconductors", "NVDA")
    assert llamadas["n"] == 1, (
        f"con {error!r} se siguio preguntando {llamadas['n']} veces")


def test_a_content_failure_does_not_stop_the_retries(monkeypatch):
    """Una respuesta ilegible no es un límite de cuota: ahí sí vale insistir,
    y es exactamente el caso que rescató a Consumer Electronics."""
    _guion(monkeypatch, [
        ("no soy json", None),
        ("tampoco", None),
        (_respuesta("Omdia", 207_000_000_000), None),
    ])
    datos, _ = tm._investigar(_S(), "Semiconductors", "NVDA")
    assert datos is not None


def test_a_dead_provider_is_named_once_not_once_per_round(monkeypatch):
    """Cuatro vueltas dejaban el mismo motivo repetido cuatro veces, y el
    archivo de la industria acababa diciendo cuatro veces lo mismo."""
    _guion(monkeypatch, [('{"tam": null}', None)] * 6)
    _, fallos = tm._investigar(_S(), "X", "Y")
    assert fallos.count("openai: sin clave") == 1


# --- esperar es distinto de rendirse ---------------------------------------

def test_the_retry_delay_survives_the_error_truncation():
    """El fallo se recorta a 120 caracteres, y el 429 de Gemini pone su
    `"Please retry in 16.57s"` al FINAL de un mensaje largo. El recorte se
    comía justo el dato que decide qué hacer, así que una pausa de diecisiete
    segundos se convertía en una industria sin TAM durante 90 días."""
    largo = ("ClientError 429 RESOURCE_EXHAUSTED. {'error': {'code': 429, "
             "'message': 'You exceeded your current quota, please check your "
             "plan and billing details. For more information on this error, "
             "head to: https://ai.google.dev/gemini-api/docs/rate-limits. "
             "Quota exceeded for metric: generate_content_free_tier_requests, "
             "limit: 20, model: gemini-2.5-flash. Please retry in 16.57s.'}}")
    mensaje = tm._error_legible("gemini", RuntimeError(largo))
    assert len(mensaje) < 200, "el recorte tiene que seguir recortando"
    assert tm._segundos_de_espera(mensaje) == pytest.approx(17.57, abs=0.01)


@pytest.mark.parametrize("error,espera", [
    # Limite POR MINUTO: vuelve solo, y el proveedor dice cuando.
    ("gemini: 429 — Please retry in 16.57s", 17.57),
    # Credito agotado: no se arregla esperando ni un dia.
    ("openai: RateLimitError You have no credits remaining", None),
    # Sin cifra no se inventa una pausa: eso convierte un limite por minuto
    # en un cuelgue.
    ("gemini: 429 RESOURCE_EXHAUSTED", None),
    # Una espera absurda tampoco: hay un tope.
    ("gemini: 429 — Please retry in 3600s", None),
])
def test_only_a_stated_and_bounded_delay_is_waited(error, espera):
    got = tm._segundos_de_espera(error)
    if espera is None:
        assert got is None
    else:
        assert got == pytest.approx(espera, abs=0.01)


def test_a_per_minute_limit_is_waited_out_not_abandoned(monkeypatch):
    """La prueba de fondo: tras esperar, se vuelve a preguntar y el TAM sale.
    Antes ese mismo 429 dejaba la industria marcada "nadie lo publica"."""
    dormido = []
    monkeypatch.setattr(tm.time, "sleep", lambda s: dormido.append(s))
    turnos = iter([
        ("", "gemini: 429 — Please retry in 16.57s"),
        (_respuesta("WSTS", 630_500_000_000), None),
    ])
    monkeypatch.setattr(tm, "_preguntar_gemini",
                        lambda s, p: next(turnos, ("", "gemini: fin")))
    monkeypatch.setattr(tm, "_preguntar_openai", lambda s, p: ("", "openai: sin clave"))
    datos, _ = tm._investigar(_S(), "Semiconductors", "NVDA")
    assert datos is not None and datos["tam"] == 630_500_000_000
    assert dormido and dormido[0] == pytest.approx(17.57, abs=0.01)


def test_the_waiting_is_bounded(monkeypatch):
    """Un proveedor que pide esperar en bucle no puede colgar un análisis."""
    dormido = []
    monkeypatch.setattr(tm.time, "sleep", lambda s: dormido.append(s))
    monkeypatch.setattr(tm, "_preguntar_gemini",
                        lambda s, p: ("", "gemini: 429 — Please retry in 20s"))
    monkeypatch.setattr(tm, "_preguntar_openai", lambda s, p: ("", "openai: sin clave"))
    tm._investigar(_S(), "Semiconductors", "NVDA")
    assert len(dormido) <= tm.ESPERAS_MAXIMAS


# --- el barrido tiene que poder observarse mientras corre -------------------

class _SS:
    """Settings con una raiz de disco real, para ver los sellos."""

    def __init__(self, raiz):
        self.inputs_dir = str(raiz)
        self.gemini_api_key = "x"
        self.openai_api_key = None


def _sello(tmp_path, slug="semiconductors"):
    import json
    f = tmp_path / "_industrias" / f"{slug}.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def test_an_industry_with_no_answer_leaves_its_stamp(tmp_path, monkeypatch):
    """El barrido de 137 industrias tiene que ser observable MIENTRAS corre.

    Esta rama no escribia nada: una industria que no conseguia respuesta no
    dejaba rastro en disco, solo en el JSON final que se escribe al terminar.
    Medido: nueve minutos de barrido sin un solo archivo nuevo, sin forma de
    saber si avanzaba o estaba colgado.
    """
    monkeypatch.setattr(tm, "_investigar",
                        lambda *a, **k: (None, ["4 respuestas sin cifra atribuible"]))
    tm.asegurar_tam_industria(_SS(tmp_path), "Semiconductors", "NVDA")
    d = _sello(tmp_path)
    assert d is not None, "la industria no dejo rastro"
    assert "sin cifra atribuible" in d["_sin_tam"]
    assert d["_que_hacer"], "un sello mudo no dice como cerrarlo a mano"


def test_a_quota_failure_leaves_no_stamp(tmp_path, monkeypatch):
    """Y esta es la mitad que importa mas. Un sello lleva fecha, y la fecha
    hace que `_vigente` salte esa industria 90 dias.

    Sellar un 429 seria tres meses sin TAM por un limite de veinte peticiones
    por minuto que se pasa en diecisiete segundos -- y el barrido entero se
    autoenvenenaria en su primera corrida contra una cuota agotada.
    """
    monkeypatch.setattr(tm, "_investigar", lambda *a, **k: (
        None, ["gemini: ClientError 429 RESOURCE_EXHAUSTED"]))
    tm.asegurar_tam_industria(_SS(tmp_path), "Semiconductors", "NVDA")
    assert _sello(tmp_path) is None, (
        "sello un fallo de cuota: esa industria queda saltada 90 dias por un "
        "limite que se pasa en segundos")


def test_an_existing_file_is_not_replaced_by_a_stamp(tmp_path, monkeypatch):
    """Un TAM que funcionaba no se pierde porque la busqueda de hoy fallara."""
    import json
    d = tmp_path / "_industrias"
    d.mkdir(parents=True)
    (d / "semiconductors.json").write_text(json.dumps({
        "_generado_por": "vertex/tam_mundial", "_resuelto_en": "2020-01-01",
        "tam": 1_655_000_000_000, "tam_source": "WSTS", "tam_source_tier": 2}),
        encoding="utf-8")
    monkeypatch.setattr(tm, "_investigar", lambda *a, **k: (None, ["sin fuente"]))
    tm.asegurar_tam_industria(_SS(tmp_path), "Semiconductors", "NVDA")
    assert _sello(tmp_path)["tam"] == 1_655_000_000_000


def test_a_stamp_survives_a_dead_second_provider(tmp_path, monkeypatch):
    """El sello no llegaba a escribirse NUNCA.

    Con OpenAI sin creditos, todos los motivos llevan su "no credits
    remaining" dentro, asi que `_es_falta_de_cuota(motivo)` daba True siempre
    y la condicion del sello nunca se cumplia. Medido: el barrido resolvio 8
    industrias, 5 de ellas sin fuente, y el contador de archivos no se movio.

    Lo que decide es si ALGUIEN respondio, no si el texto nombra una cuota.
    """
    monkeypatch.setattr(tm, "_investigar", lambda *a, **k: (None, [
        "openai: RateLimitError You have no credits remaining",
        "4 respuestas sin cifra atribuible"]))
    tm.asegurar_tam_industria(_SS(tmp_path), "Biotechnology", "")
    f = tmp_path / "_industrias" / "biotechnology.json"
    assert f.exists(), (
        "el proveedor muerto de OpenAI impidio sellar una industria que "
        "Gemini si contesto cuatro veces")
