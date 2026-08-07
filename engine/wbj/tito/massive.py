"""Cliente de Massive (massive.com — antes Polygon.io). Solo servidor.

Port de `web/lib/massive.ts`.

Es la fuente de cadena que usa Víctor. Vertex puede funcionar sin ella
(`yfinance` da los mismos campos y no pide key), así que este módulo es
**opcional**: `_tito_chain_and_bars` lo usa cuando `MASSIVE_API_KEY` está en el
entorno y cae a yfinance cuando no.

Lo que Víctor midió sobre el plan (jul 2026) y conviene tener presente:

- **Sí** devuelve `last_quote` (bid/ask) en el Option Chain Snapshot.
- **No** devuelve `greeks` ni `implied_volatility`.

Por eso Massive **no sustituye al tape**: los griegos por trade y el lado
ask/bid siguen viniendo de MarketSnack (o de Quant Data). Massive cubre la
cadena y las barras, nada más.

La API key nunca se imprime ni se registra: los errores citan el HTTP y un
extracto del cuerpo, jamás la credencial.
"""

from __future__ import annotations

import json as _json
import math
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from .jsmath import js_number
from .compute import (contract_price, count_expirations,
                      sort_by_open_interest_desc, to_row)
from .levels import LvlBar
from .structure import ChainRow

__all__ = [
    "BASE_URL",
    "MassiveError",
    "ChainResult",
    "DailyBar",
    "fetch_company",
    "fetch_wheel_chain",
    "WheelChainQuote",
    "WheelChainResult",
    "fetch_option_chain",
    "fetch_daily_bars",
]

BASE_URL = "https://api.massive.com"


class MassiveError(RuntimeError):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def _api_key() -> str:
    # Su `apiKey()`, literal:
    #
    #     const key = process.env.MASSIVE_API_KEY;
    #     if (!key) throw new MassiveError("Falta MASSIVE_API_KEY…");
    #
    # `if (!key)` en JavaScript es solo la cadena VACÍA: una clave con espacios
    # alrededor pasa y Massive responde 401. Estuvo recortada aquí —lo que era
    # más útil— y se revirtió: la instrucción es que lo único distinto sea el
    # perfil y la Wheel, así que esto vuelve a ser suyo aunque su versión
    # falle más tarde y peor.
    key = os.environ.get("MASSIVE_API_KEY", "")
    if not key:
        raise MassiveError("Falta MASSIVE_API_KEY en el entorno.")
    return key


def _max_pages() -> int:
    """Su `maxPages()`, con la MISMA lectura del entorno.

        const n = Number(process.env.MASSIVE_MAX_PAGES);
        return Number.isFinite(n) && n > 0 ? Math.floor(n) : 40;

    `int()` no es `Number()`: rechaza la notación científica y el hexadecimal
    que JavaScript sí acepta. Con `MASSIVE_MAX_PAGES=1e2` él paginaba hasta 100
    páginas y aquí se cortaba en 40 — media cadena de opciones de menos, sin
    que nada avisara. `js_number` es el port de `Number()` y ya estaba escrito.
    """
    n = js_number(os.environ.get("MASSIVE_MAX_PAGES", ""))
    if n != n or n in (float("inf"), float("-inf")) or n <= 0:   # NaN/±∞/≤0
        return 40
    return int(math.floor(n))


def _describe(status: int, ticker: str, body: str, ruta: str = "") -> str:
    """El motivo, en palabras y con la ACCIÓN que toca.

    401 y 403 iban en la misma rama y son dos problemas distintos que se
    arreglan de forma distinta:

    - **401** — la credencial no vale: falta, está mal pegada o fue revocada.
      Se arregla cambiando la key.
    - **403** — la credencial vale, pero **el plan no cubre ese endpoint**. La
      key es correcta y cambiarla no arregla nada; hay que mirar QUÉ se pidió.
      Massive hereda el modelo de planes de Polygon, donde el snapshot de
      acciones, el de opciones y los aggregates se contratan por separado.

    Por eso el mensaje lleva la RUTA. Sin ella, "Massive rechazó la API key"
    manda a revisar una credencial que puede estar perfecta, mientras el
    problema real es que ese endpoint concreto no está en el plan.
    """
    donde = f" al pedir {ruta}" if ruta else ""
    if status == 401:
        return ("Massive no aceptó la credencial" + donde +
                ": la key falta, está mal pegada o fue revocada (MASSIVE_API_KEY).")
    if status == 403:
        return ("Massive aceptó la key pero no autoriza este endpoint" + donde +
                ": el plan no lo cubre. Cambiar la key no lo arregla.")
    if status == 404:
        return f"Massive no tiene datos para {ticker}{donde}."
    if status == 429:
        return "Límite de tasa de Massive; reintenta en unos segundos."
    return f"Massive respondió {status}{donde}. {body[:200]}".strip()


def _ruta(url: str) -> str:
    """El path de la URL, sin host ni query. La query lleva parámetros y jamás
    la credencial —va en la cabecera— pero se recorta igual: lo que hace falta
    para saber qué endpoint falló es el path."""
    try:
        return urllib.parse.urlsplit(url).path or ""
    except Exception:
        return ""


def _get(url: str, key: str, ticker: str, timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return _json.loads(res.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        raise MassiveError(_describe(e.code, ticker, body, _ruta(url)), e.code) from e
    except urllib.error.URLError as e:
        raise MassiveError(f"No se pudo conectar con Massive: {e.reason}") from e
    except ValueError as e:
        raise MassiveError("Respuesta de Massive no parseable.") from e


@dataclass
class ChainResult:
    #: Ordenadas por open interest de mayor a menor, como las deja su ruta de
    #: cadena antes de puntuarlas.
    rows: list[ChainRow] = field(default_factory=list)
    underlying_price: float | None = None
    pages: int = 0
    truncated: bool = False
    #: `expirationCount` de su `ChainMeta`: cuántos vencimientos distintos trae.
    expiration_count: int = 0


def _num(v: Any) -> float:
    return float(v) if isinstance(v, (int, float)) else 0.0


def fetch_option_chain(
    ticker: str,
    on_page: Callable[[int, int], None] | None = None,
    timeout: float = 25.0,
) -> ChainResult:
    """Cadena completa siguiendo la paginación por `next_url`.

    Corta en `MASSIVE_MAX_PAGES` (40 ≈ 10k contratos) como salvaguarda: sin
    tope, un subyacente con miles de strikes agota la cuota en una consulta.
    """
    key = _api_key()
    clean = (ticker or "").strip().upper()
    if not clean:
        raise MassiveError("Ticker vacío.")

    rows: list[ChainRow] = []
    underlying: float | None = None
    url: str | None = (
        f"{BASE_URL}/v3/snapshot/options/{urllib.parse.quote(clean)}?limit=250"
    )
    page = 0
    truncated = False
    limit = _max_pages()

    while url:
        page += 1
        data = _get(url, key, clean, timeout)
        # La respuesta tiene que ser un objeto con `results` en lista. Si Massive
        # devuelve otra cosa —un array suelto, un `results` que es texto o un
        # número— sin esta guarda salía un `AttributeError`/`TypeError` crudo en
        # vez del `MassiveError` que el resto del módulo produce y que
        # `_tito_memory` sabe reportar con su motivo.
        if not isinstance(data, dict):
            raise MassiveError(
                f"Massive devolvió {type(data).__name__} donde se esperaba un objeto.")
        results = data.get("results")
        if results is None:
            results = []
        if not isinstance(results, list):
            raise MassiveError(
                f"Massive devolvió `results` como {type(results).__name__}, no una lista.")
        for c in results:
            # La conversión la hace compute.to_row, como en el original: este
            # módulo trae páginas, las fórmulas viven aparte y se prueban solas.
            row = to_row(c)
            # DIVERGENCIA declarada: Víctor no filtra (su ruta hace
            # `contracts.map(toRow)` a secas) porque su destino es una tabla,
            # donde una fila rara solo se ve fea. Aquí el destino son GEX,
            # niveles y Estructura: un strike 0 mete un nodo imán en cero, y un
            # vencimiento vacío crea un grupo fantasma en el sub-agente 4.
            if row.strike <= 0 or not row.expiration:
                continue
            rows.append(row)
            if underlying is None:
                px = _num((c.get("underlying_asset") or {}).get("price"))
                if px > 0:
                    underlying = px
        if on_page:
            on_page(page, len(rows))

        url = data.get("next_url")
        if url and page >= limit:
            truncated = True
            break

    # Las dos últimas piezas de compute.ts, en el mismo sitio donde las usa su
    # `/api/chain`: ordenar por OI antes de puntuar, y contar los vencimientos
    # para la meta. Estaban portadas pero sin llamar desde ningún sitio.
    rows = sort_by_open_interest_desc(rows)
    return ChainResult(rows=rows, underlying_price=underlying, pages=page,
                       truncated=truncated, expiration_count=count_expirations(rows))


def fetch_ticker_name(ticker: str, timeout: float = 12.0) -> str | None:
    """Nombre de la empresa (`/v3/reference/tickers/{ticker}`).

    Lo usa `news.company_aliases` para reconocer a la empresa en un titular
    macro: con solo el ticker, "TSLA" en un titular hace match pero "Tesla" no.
    Devuelve ``None`` si falla — el match degrada al ticker, no revienta.
    """
    try:
        key = _api_key()
    except MassiveError:
        return None
    clean = (ticker or "").strip().upper()
    if not clean:
        return None
    url = f"{BASE_URL}/v3/reference/tickers/{urllib.parse.quote(clean)}"
    try:
        data = _get(url, key, clean, timeout)
    except MassiveError:
        return None
    name = (data.get("results") or {}).get("name")
    return name if isinstance(name, str) and name.strip() else None


def fetch_company(ticker: str, timeout: float = 12.0) -> dict | None:
    """Port de su `fetchCompany` — la ficha del subyacente **con su precio**.

    Existe por UNA razón que no es cosmética: su `page.tsx` elige el spot así,
    y en este orden::

        company?.price ?? chainMeta?.underlyingPrice ?? bars[bars.length - 1].close

    El port se saltaba el primero e iba directo al segundo. No es lo mismo:
    ``chainMeta.underlyingPrice`` viene dentro de la respuesta de la CADENA y
    es el precio con el que Massive calculó esa cadena; ``company.price`` es el
    snapshot del subyacente —última operación, `day.c ?? min.c ?? prevDay.c`—.
    Cuando la cadena se sirve de caché o el subyacente se movió después de
    calcularla, los dos no coinciden. Y el spot no es un adorno: ancla los
    nodos del GEX, la ventana de ±`NEAR_SPOT_PCT` que decide qué strikes
    entran, los niveles, el cono y los tres targets. Un spot viejo mueve el
    panel entero sin que nada avise.

    Se piden los dos endpoints como él, y `None` en cualquiera no revienta:
    devuelve lo que haya. Si falla del todo, `None` y el llamador cae al
    siguiente eslabón de SU cadena de respaldo, no a otra fuente.
    """
    try:
        key = _api_key()
    except MassiveError:
        return None
    clean = (ticker or "").strip().upper()
    if not clean:
        return None
    q = urllib.parse.quote(clean)

    def _quiza(url):
        try:
            return _get(url, key, clean, timeout)
        except MassiveError:
            return None

    det = _quiza(f"{BASE_URL}/v3/reference/tickers/{q}")
    snap = _quiza(f"{BASE_URL}/v2/snapshot/locale/us/markets/stocks/tickers/{q}")
    if det is None and snap is None:
        return None
    d = (det or {}).get("results") or {}
    t = (snap or {}).get("ticker") or {}

    def _c(*caminos):
        """`t.day?.c ?? t.min?.c ?? t.prevDay?.c` — el `??` suyo: solo salta el
        nulo, así que un cierre de 0 se queda en 0 y no cae al siguiente."""
        for bloque, campo in caminos:
            v = (t.get(bloque) or {}).get(campo)
            if v is not None:
                return v
        return None

    return {
        "ticker": clean,
        "name": d.get("name"),
        "market_cap": d.get("market_cap"),
        "sector": d.get("sic_description"),
        "price": _c(("day", "c"), ("min", "c"), ("prevDay", "c")),
        "change": t.get("todaysChange"),
        "change_percent": t.get("todaysChangePerc"),
        "day_open": (t.get("day") or {}).get("o"),
        "day_high": (t.get("day") or {}).get("h"),
        "day_low": (t.get("day") or {}).get("l"),
        "day_volume": (t.get("day") or {}).get("v"),
        "prev_close": (t.get("prevDay") or {}).get("c"),
    }


@dataclass(frozen=True)
class DailyBar:
    """`DailyBar` de su `types.ts`. Es un `LvlBar` **más la apertura**.

    El port devolvía `LvlBar` y tiraba el `open`, con dos consecuencias que solo
    se ven al dibujar: la gráfica de Proyecciones pintaba TODAS las velas como
    doji —cuerpo cero, y `close >= open` siempre cierto, o sea todas verdes— y
    el cache en disco guardaba una serie mutilada que ya no se puede completar
    sin volver a pedirla.

    Los campos van en el orden de su interfaz. `levels`, `validation` y
    `estimate_iv` solo leen `time/high/low/close`, así que sirve igual donde
    antes iba un `LvlBar`.
    """

    time: str          # YYYY-MM-DD
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class WheelChainQuote:
    """Una fila de la cadena de puts, ya normalizada. Es su `WheelChainQuote`."""

    strike: float
    expiration: str
    dte: int
    bid: float | None
    ask: float | None
    last_trade: float | None
    open_interest: int


@dataclass
class WheelChainResult:
    spot: float | None = None
    quotes: list[WheelChainQuote] = field(default_factory=list)


def fetch_wheel_chain(ticker: str, dte_min: int, dte_max: int,
                      now: datetime | None = None,
                      timeout: float = 25.0) -> WheelChainResult:
    """Cadena de PUTS acotada al rango de DTE del preset — su `fetchWheelChain`.

    Dos cosas suyas que no son detalle:

    - El "hoy" se ancla al día de mercado **ET**, no UTC. Después de las ~8 PM
      ET el día UTC ya saltó, y el `dte` y el rango de vencimientos saldrían
      desfasados un día (el aviso vive en `market_date_str`, en `occ.py`).
    - Solo se devuelven **puts OTM** (`strike <= spot`). Un put ITM no es un
      cash-secured put de Wheel: es otra cosa.
    """
    from .occ import market_date_str

    key = _api_key()
    clean = (ticker or "").strip().upper()
    if not clean:
        raise MassiveError("Ticker vacío.")
    hoy_et = market_date_str(now or datetime.now(timezone.utc))
    base = date.fromisoformat(hoy_et)
    desde = (base + timedelta(days=dte_min)).isoformat()
    hasta = (base + timedelta(days=dte_max)).isoformat()

    url = (f"{BASE_URL}/v3/snapshot/options/{urllib.parse.quote(clean)}"
           f"?contract_type=put&expiration_date.gte={desde}"
           f"&expiration_date.lte={hasta}&limit=250")
    data = _get(url, key, clean, timeout)
    if not isinstance(data, dict):
        raise MassiveError(
            f"Massive devolvió {type(data).__name__} donde se esperaba un objeto.")

    spot: float | None = None
    quotes: list[WheelChainQuote] = []
    for c in (data.get("results") or []):
        if not isinstance(c, dict):
            continue
        det = c.get("details") or {}
        strike, expiration = det.get("strike_price"), det.get("expiration_date")
        if not (isinstance(strike, (int, float)) and strike > 0) or not expiration:
            continue
        if spot is None:
            px = (c.get("underlying_asset") or {}).get("price")
            if px:
                spot = float(px)
        try:
            dte = (date.fromisoformat(str(expiration)) - base).days
        except ValueError:
            continue
        # El precio de respaldo sale de SU `contract_price`, la misma cascada
        # que usa para la tabla de la cadena: `last_trade` → `day.close` →
        # `day.vwap`. Aquí se leía solo el primer nivel, y su propio comentario
        # en `compute.py` explica por qué eso no basta:
        #
        #     "La fórmula del agente pide BID, pero el plan actual de Massive
        #      NO devuelve quotes, así que cae a last_trade → day.close →
        #      day.vwap."
        #
        # O sea: la cascada existe justamente para esto. Usar solo `last_trade`
        # dejaba sin precio cualquier contrato que no hubiera negociado hoy —
        # que fuera de sesión son todos.
        precio, _fuente = contract_price(c)
        quotes.append(WheelChainQuote(
            strike=float(strike), expiration=str(expiration), dte=dte,
            bid=(c.get("last_quote") or {}).get("bid"),
            ask=(c.get("last_quote") or {}).get("ask"),
            last_trade=precio,
            open_interest=c.get("open_interest") or 0))

    otm = [q for q in quotes if q.strike <= spot] if spot is not None else quotes
    return WheelChainResult(spot=spot, quotes=otm)


def fetch_daily_bars(ticker: str, days: int = 365, timeout: float = 25.0) -> list[DailyBar]:
    """Barras diarias del subyacente (`/v2/aggs/...`), de más vieja a más nueva."""
    key = _api_key()
    clean = (ticker or "").strip().upper()
    if not clean:
        raise MassiveError("Ticker vacío.")
    # `const to = new Date()` y `toDateStr` = **UTC**. `date.today()` usa la
    # zona LOCAL del servidor: al oeste de Greenwich pedía un día MENOS que él
    # y al este uno más, así que la ventana de barras no era la misma. Es el
    # mismo error que ya se corrigió al etiquetar cada barra, pero en el RANGO
    # que se pide, que nadie había mirado.
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days)
    url = (
        f"{BASE_URL}/v2/aggs/ticker/{urllib.parse.quote(clean)}/range/1/day/"
        f"{start.isoformat()}/{end.isoformat()}?adjusted=true&sort=asc&limit=500"
    )
    data = _get(url, key, clean, timeout)
    out: list[DailyBar] = []
    for b in data.get("results") or []:
        ts = b.get("t")
        if not isinstance(ts, (int, float)):
            continue
        # `new Date(ms).toISOString().slice(0, 10)` — su `toDateStr`, que es
        # **UTC**. `date.fromtimestamp` usa la zona LOCAL del servidor: con el
        # contenedor en una zona al oeste de Greenwich, la barra del cierre
        # (21:00 UTC) se etiquetaba con el día ANTERIOR y todo el eje temporal
        # se corría un día contra el suyo.
        day = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).date().isoformat()
        out.append(DailyBar(
            time=day, open=_num(b.get("o")), high=_num(b.get("h")),
            low=_num(b.get("l")), close=_num(b.get("c")),
        ))
    return out
