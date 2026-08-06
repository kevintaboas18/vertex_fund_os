"""La amplitud de sector se mide, y se descarta cuando no se pudo medir bien.

`MKT-SECB-023` = miembros sobre su media de 50 sesiones / miembros válidos.
Estaba sin calcular porque el código razonaba que el screener de FMP devuelve
la composición de HOY y aplicarla a una ventana de 252 sesiones sería sesgo de
supervivencia. El razonamiento es correcto y describe otra métrica: aquí no hay
ventana. La amplitud es una foto de hoy, y para una foto de hoy la composición
de hoy es el point-in-time que la fórmula pide.

Lo que estos tests fijan es lo que salió mal al medirlo de verdad.
"""

from __future__ import annotations

from datetime import date

from wbj.overlay import amplitud_sector as amp


class _Cache:
    def __init__(self):
        self.datos = {}

    def get(self, ticker, key):
        return self.datos.get((ticker, key))

    def put(self, ticker, key, payload):
        self.datos[(ticker, key)] = payload


class _FMP:
    """FMP de mentira: un screener y un `quote` por símbolo."""

    available = True

    def __init__(self, miembros, quotes, cache=None):
        self._miembros = miembros
        self._quotes = quotes
        self.cache = cache or _Cache()
        self.settings = type("S", (), {"fmp_api_key": "x"})()
        self.llamadas = []

    def get_json(self, url, params, clave, ticker, max_age_days=1):
        self.llamadas.append(clave)
        if "company-screener" in url:
            return [{"symbol": s, "marketCap": 1e10 - i}
                    for i, s in enumerate(self._miembros)]
        q = self._quotes.get(params.get("symbol"))
        return [q] if q else []


def _quote(precio, m50, m200=None):
    return {"price": precio, "priceAvg50": m50,
            "priceAvg200": m200 if m200 is not None else m50}


def test_counts_members_above_their_fifty_day_average():
    miembros = [f"T{i}" for i in range(40)]
    # 30 por encima de su media, 10 por debajo.
    quotes = {s: _quote(110.0, 100.0) for s in miembros[:30]}
    quotes.update({s: _quote(90.0, 100.0) for s in miembros[30:]})
    fuera = amp.amplitud_de_sector(_FMP(miembros, quotes), "Technology")
    assert fuera["above_50dma_count"] == 30
    assert fuera["valid_members"] == 40


def test_the_two_hundred_day_average_is_reported_too():
    """`FORMULAS.md` MKT-SECB-023: "Also report members above 200DMA"."""
    miembros = [f"T{i}" for i in range(40)]
    quotes = {s: _quote(110.0, 100.0, 120.0) for s in miembros}
    fuera = amp.amplitud_de_sector(_FMP(miembros, quotes), "Technology")
    assert fuera["above_50dma_count"] == 40, "por encima de la de 50"
    assert fuera["above_200dma_count"] == 0, "y por debajo de la de 200"


def test_a_member_without_a_moving_average_is_not_counted_below_it():
    """Uno recién listado no tiene media de 50. Contarlo en el denominador
    como "no la supera" diría que el sector está más débil de lo que está."""
    miembros = [f"T{i}" for i in range(40)]
    quotes = {s: _quote(110.0, 100.0) for s in miembros[:30]}
    quotes.update({s: {"price": 50.0, "priceAvg50": None} for s in miembros[30:]})
    fuera = amp.amplitud_de_sector(_FMP(miembros, quotes), "Technology")
    assert fuera["valid_members"] == 30
    assert fuera["above_50dma_count"] == 30


def test_a_half_answered_sector_is_thrown_away():
    """El caso que motivó el piso. Medido: Financial Services devolvió 85
    cotizaciones de 458 miembros y daba una amplitud del 85% — pero no era la
    del sector, era la de quienes alcanzaron a contestar antes del límite de
    tasa, y esos no son una muestra aleatoria."""
    miembros = [f"T{i}" for i in range(100)]
    quotes = {s: _quote(110.0, 100.0) for s in miembros[:30]}  # 30% responde
    assert amp.amplitud_de_sector(_FMP(miembros, quotes), "Financial Services") is None


def test_a_sector_that_answers_well_is_kept():
    miembros = [f"T{i}" for i in range(100)]
    quotes = {s: _quote(110.0, 100.0) for s in miembros[:70]}  # 70%, sobre el piso
    fuera = amp.amplitud_de_sector(_FMP(miembros, quotes), "Technology")
    assert fuera is not None and fuera["valid_members"] == 70


def test_a_handful_of_members_is_not_a_sector():
    """Con cinco miembros, dos empresas mueven la amplitud entera."""
    miembros = [f"T{i}" for i in range(5)]
    quotes = {s: _quote(110.0, 100.0) for s in miembros}
    assert amp.amplitud_de_sector(_FMP(miembros, quotes), "Technology") is None


def test_the_universe_travels_with_the_number():
    """Una amplitud del 52% significa cosas distintas sobre 400 empresas
    líquidas que sobre 4.000 incluyendo microcaps. Sin el universo escrito al
    lado, el número no se puede leer."""
    miembros = [f"T{i}" for i in range(40)]
    quotes = {s: _quote(110.0, 100.0) for s in miembros}
    fuera = amp.amplitud_de_sector(_FMP(miembros, quotes), "Technology")
    assert "NASDAQ" in fuera["_universo"] and "2,000,000,000" in fuera["_universo"]
    assert "point_in_time" in " ".join(fuera.keys())


def test_it_is_computed_once_per_sector_and_day():
    """Son cientos de peticiones y el número es idéntico para todo ticker del
    sector: cobrarlo una vez por ticker sería pagarlo cinco veces por nada."""
    miembros = [f"T{i}" for i in range(40)]
    quotes = {s: _quote(110.0, 100.0) for s in miembros}
    fmp = _FMP(miembros, quotes)
    primera = amp.amplitud_de_sector(fmp, "Technology", hoy=date(2026, 8, 6))
    n = len(fmp.llamadas)
    segunda = amp.amplitud_de_sector(fmp, "Technology", hoy=date(2026, 8, 6))
    assert segunda == primera
    assert len(fmp.llamadas) == n, "la segunda vez volvio a pedir a la red"


def test_without_a_sector_or_a_provider_there_is_nothing_to_measure():
    fmp = _FMP([], {})
    assert amp.amplitud_de_sector(fmp, None) is None
    assert amp.amplitud_de_sector(fmp, "") is None
    fmp.available = False
    assert amp.amplitud_de_sector(fmp, "Technology") is None


def test_the_analysis_path_never_touches_the_network():
    """La regresion que esto arregla, y que se vio en produccion.

    Pedir la amplitud por red durante el analisis disparaba cientos de
    peticiones a FMP, agotaba su limite, y los 429 caian sobre las llamadas
    del PROPIO ticker: el estado de flujo de caja de NVDA y su historico de
    precios fallaron detras de esa tormenta. Una metrica de contexto que vale
    3 de los 100 puntos estaba tumbando las seis categorias.

    Desde el analisis se lee cache y nada mas. Sin cache, la metrica queda
    NOT_SCORABLE: cuesta 3 puntos, no el analisis entero.
    """
    miembros = [f"T{i}" for i in range(40)]
    quotes = {s: _quote(110.0, 100.0) for s in miembros}
    fmp = _FMP(miembros, quotes)
    assert amp.amplitud_de_sector(fmp, "Technology", permitir_red=False) is None
    assert fmp.llamadas == [], "toco la red desde la ruta del analisis"

    # Calentada aparte, la siguiente lectura sale de cache sin pedir nada.
    amp.amplitud_de_sector(fmp, "Technology")
    n = len(fmp.llamadas)
    assert amp.amplitud_de_sector(fmp, "Technology", permitir_red=False) is not None
    assert len(fmp.llamadas) == n


def test_the_universe_is_capped_where_an_index_would_cap_it():
    """405 peticiones por sector fue lo que agoto el limite. 120 mayores por
    capitalizacion es como se construye cualquier indice sectorial, y se
    declara en la salida para que el numero se pueda leer."""
    assert amp.TOPE_MIEMBROS == 120
