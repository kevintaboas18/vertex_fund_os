"""El TAM se resuelve para TODAS las industrias, no de una en una.

Hasta aquí una industria sólo intentaba resolverse cuando alguien analizaba un
ticker suyo. Eso deja el mercado cubierto en el orden en que Kevin escribe
tickers, y las 132 industrias operativas del mercado mundial no se llenan nunca.

Dos cosas que este barrido tiene que hacer bien, y las dos son economía de
cuota:

**Ordenar por cobertura.** Cada intento cuesta peticiones al proveedor de
búsqueda y el tier gratuito da 20 por minuto. Resolver `Semiconductors` (51
empresas) antes que una industria de dos deja más mercado cubierto por
petición gastada.

**Cortarse cuando la cuota se acaba.** Seguir preguntando contra un contador
agotado no resuelve nada, y con 132 industrias × 4 intentos el desperdicio
deja de ser teórico.

Y una que tiene que hacer bien por honestidad: **lo que no verifique contra la
página de su fuente no se guarda como TAM**. Eso es lo único que separa este
barrido de la versión anterior, que llenaba archivos con cifras que nadie podía
abrir — nueve de diez citas eran redirects caducados de Google.
"""

from __future__ import annotations

import pytest

from wbj.overlay import tam_mundial as tm


class _FMP:
    def __init__(self, filas):
        self._filas = filas

    def screener_universo(self, **kw):
        return self._filas


def _empresa(industria, **extra):
    return {"industry": industria, "isEtf": False, "isFund": False,
            "isActivelyTrading": True, **extra}


# --- que industrias cuentan ------------------------------------------------

def test_funds_and_etfs_are_not_an_industry():
    """Sin este filtro «Asset Management» salía con 1.110 entradas y ninguna
    es una empresa operativa con un mercado que medir: son vehículos que
    cotizan, y su «industria» es una etiqueta del proveedor."""
    fmp = _FMP([_empresa("Semiconductors"), _empresa("Semiconductors"),
                _empresa("Asset Management", isEtf=True),
                _empresa("Asset Management", isFund=True)])
    assert tm.industrias_del_mercado(fmp) == [("Semiconductors", 2)]


def test_delisted_companies_do_not_count():
    fmp = _FMP([_empresa("Semiconductors"), _empresa("Semiconductors"),
                _empresa("Semiconductors", isActivelyTrading=False)])
    assert tm.industrias_del_mercado(fmp) == [("Semiconductors", 2)]


def test_the_biggest_industries_come_first():
    """El orden ES la economía de cuota: más mercado cubierto por petición."""
    fmp = _FMP([_empresa("Chica"), _empresa("Chica")]
               + [_empresa("Grande")] * 9)
    assert [n for n, _ in tm.industrias_del_mercado(fmp)] == ["Grande", "Chica"]


def test_a_one_company_industry_is_skipped_by_default():
    """Gastar cuatro peticiones en una industria de una sola empresa es
    exactamente lo que el orden por cobertura existe para evitar."""
    fmp = _FMP([_empresa("Grande"), _empresa("Grande"), _empresa("Unica")])
    assert [n for n, _ in tm.industrias_del_mercado(fmp)] == ["Grande"]


def test_an_unreadable_universe_yields_nothing_not_a_crash():
    assert tm.industrias_del_mercado(_FMP(None)) == []


# --- el barrido ------------------------------------------------------------

def _asegura(monkeypatch, respuestas: dict):
    vistas = []

    def _falso(settings, industria, ticker, **kw):
        vistas.append(industria)
        return respuestas.get(industria, "sin TAM (ninguna asociacion)")

    monkeypatch.setattr(tm, "asegurar_tam_industria", _falso)
    return vistas


def test_the_sweep_reports_what_happened_per_industry(monkeypatch):
    fmp = _FMP([_empresa("Semiconductors")] * 3 + [_empresa("Bebidas")] * 2)
    _asegura(monkeypatch, {
        "Semiconductors": "semiconductors: TAM mundial $1,510,000,000,000 de WSTS (tier 2)"})
    filas = tm.resolver_todas_las_industrias(fmp=fmp, settings=None)
    por = {f["industria"]: f["estado"] for f in filas}
    assert por["Semiconductors"] == "resuelto"
    assert por["Bebidas"] == "sin fuente"


def test_an_already_resolved_industry_is_not_redone(monkeypatch):
    fmp = _FMP([_empresa("Semiconductors")] * 3)
    _asegura(monkeypatch, {"Semiconductors": "semiconductors: TAM vigente desde 2026-08-08"})
    assert tm.resolver_todas_las_industrias(fmp=fmp, settings=None)[0]["estado"] == "ya estaba"


def test_an_analyst_file_counts_as_done(monkeypatch):
    fmp = _FMP([_empresa("X")] * 3)
    _asegura(monkeypatch, {"X": "x: TAM escrito por un analista, no se toca"})
    assert tm.resolver_todas_las_industrias(fmp=fmp, settings=None)[0]["estado"] == "ya estaba"


def test_a_permanent_quota_stops_the_sweep(monkeypatch):
    """"No credits remaining" no trae segundos porque no se arregla
    esperando. Ahi si vale rendirse: seguir preguntando contra un contador
    muerto desperdicia la corrida entera."""
    fmp = _FMP([_empresa("Primera")] * 9 + [_empresa("Segunda")] * 5)
    vistas = _asegura(monkeypatch, {
        "Primera": "primera: no se pudo resolver (openai: You have no credits remaining)"})
    filas = tm.resolver_todas_las_industrias(fmp=fmp, settings=None)
    assert vistas == ["Primera"], f"siguio preguntando: {vistas}"
    assert filas[-1]["estado"] == "cuota agotada"


def test_a_per_minute_limit_paces_the_sweep_instead_of_killing_it(monkeypatch):
    """El fallo que costo una corrida entera: el tier gratuito de Gemini da 20
    peticiones por minuto, y unas pruebas previas habian gastado la del
    minuto. El barrido de 132 industrias murio en la PRIMERA.

    Un limite por minuto trae sus segundos en el propio error. Se espera y se
    sigue."""
    dormido = []
    monkeypatch.setattr(tm.time, "sleep", lambda s: dormido.append(s))
    fmp = _FMP([_empresa("Primera")] * 9 + [_empresa("Segunda")] * 5)
    intentos = {"n": 0}

    def _falso(settings, industria, ticker, **kw):
        intentos["n"] += 1
        if industria == "Primera" and intentos["n"] == 1:
            return ("primera: no se pudo resolver (gemini: 429 "
                    "RESOURCE_EXHAUSTED — Please retry in 16.5s)")
        return "sin TAM (ninguna asociacion)"

    monkeypatch.setattr(tm, "asegurar_tam_industria", _falso)
    filas = tm.resolver_todas_las_industrias(fmp=fmp, settings=None)
    assert dormido, "no espero: la cuota del minuto mato el barrido"
    assert {f["industria"] for f in filas} >= {"Primera", "Segunda"}, (
        "no llego a la segunda industria tras esperar")


def test_the_limit_bounds_a_run(monkeypatch):
    """Para poder correrlo por tandas sin agotar la cuota de una vez."""
    fmp = _FMP([_empresa(f"I{i}") for i in range(6) for _ in range(2)])
    vistas = _asegura(monkeypatch, {})
    tm.resolver_todas_las_industrias(fmp=fmp, settings=None, limite=2)
    assert len(vistas) == 2


def test_one_dead_provider_does_not_kill_the_sweep(monkeypatch):
    """El barrido murio en la industria 8 de 137 por el "no credits
    remaining" de OpenAI -- mientras Gemini contestaba, y el propio mensaje lo
    decia: "3 respuestas sin cifra atribuible".

    Tres respuestas son tres respuestas. Con dos proveedores, el fallo de uno
    viaja en el mismo texto que el trabajo del otro, y leer solo la palabra
    "cuota" confunde "no pude preguntar" con "pregunte y no hay fuente".
    """
    fmp = _FMP([_empresa("Primera")] * 9 + [_empresa("Segunda")] * 5
               + [_empresa("Tercera")] * 3)
    vistas = _asegura(monkeypatch, {
        "Primera": ("primera: no se pudo resolver (openai: RateLimitError You "
                    "have no credits remaining; 3 respuestas sin cifra atribuible)")})
    tm.resolver_todas_las_industrias(fmp=fmp, settings=None)
    assert vistas == ["Primera", "Segunda", "Tercera"], (
        f"un proveedor muerto corto el barrido: solo llego a {vistas}")
