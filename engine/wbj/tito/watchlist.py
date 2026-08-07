"""Watchlist de contratos — port literal de `web/lib/watchlist.ts`.

Es el watchlist SUYO, y sustituye al que tenía Vertex. La diferencia no es
cosmética: el de Vertex guardaba **tickers** y les colgaba alertas de precio;
este guarda el **contrato entero** —strike, vencimiento, griegos y tu sizing
del momento— porque lo que se juzga después no es la idea, es la decisión.

Todo lo de aquí son funciones puras. La persistencia del buzón vive en
`outbox_store.py`, la del watchlist en el navegador (`watchlistLocal.ts`, que
en este port es JavaScript dentro del panel, igual que en su app).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

__all__ = [
    "BrokerAdapter",
    "BROKERS",
    "broker_by_id",
    "BrokerSync",
    "WatchlistEntry",
    "EntrySource",
    "build_entry",
    "upsert",
    "remove",
    "mark_synced",
    "payload_for",
    "ContractRef",
    "contract_query",
    "contract_ref_label",
    "underlyings",
    "sort_entries",
    "quote_link",
    "ticker_list",
    "ROBINHOOD_MCP_COMMAND",
    "OutboxItem",
    "OutboxTarget",
    "outbox_key",
    "outbox_label",
    "add_to_outbox",
    "pending_outbox",
    "mark_outbox_synced",
    "mark_outbox_failed",
    "failed_outbox",
    "remove_from_outbox",
]

#: `BrokerGranularity`: `contracts` guarda el contrato exacto,
#: `underlying_only` solo el ticker, `none` no sale de aquí.
Granularidad = Literal["contracts", "underlying_only", "none"]

#: `SyncKind`: cómo llega la idea al broker.
#: `mcp` escritura real por MCP · `link` abre su página · `copy` al
#: portapapeles · `none` se queda en el watchlist.
TipoSync = Literal["mcp", "link", "copy", "none"]


@dataclass(frozen=True)
class BrokerAdapter:
    id: str
    name: str
    kind: TipoSync
    granularity: Granularidad
    #: Página de cotización del broker. Solo la tienen los `link`.
    quote_url: Any = None
    #: Nota para la UI cuando no puede guardar el contrato completo.
    caveat: str | None = None


def _url(plantilla: str):
    from urllib.parse import quote

    return lambda t: plantilla.replace("{t}", quote(t, safe=""))


#: Brokers conocidos, uno a uno como en su archivo.
#:
#: Robinhood es el único `mcp`: tiene MCP oficial (`agent.robinhood.com/mcp/
#: trading`, OAuth) y granularidad `contracts`, verificado contra la API real.
#: Lo que sigue en beta solo-acciones es la EJECUCIÓN de órdenes, no el
#: watchlist — y aquí nunca se coloca una orden, así que no limita.
#:
#: Webull e IBKR son `copy` a propósito: Webull exige el prefijo de bolsa en la
#: ruta (`nasdaq-wulf` responde, `nyse-wulf` da 404) y el feed no dice en qué
#: bolsa cotiza cada ticker; la página de IBKR responde 200 pero es genérica.
#: Mandar a alguien a un 404 es peor que darle el ticker para pegar.
BROKERS: list[BrokerAdapter] = [
    BrokerAdapter(
        id="robinhood",
        name="Robinhood",
        kind="mcp",
        granularity="contracts",
        quote_url=_url("https://robinhood.com/stocks/{t}"),
        caveat=(
            "Se sincroniza el contrato completo a tu watchlist de opciones. "
            "Robinhood no acepta estrategias de varias patas por API: si tienes "
            "un spread, sigue viéndose solo en su app."
        ),
    ),
    BrokerAdapter(
        id="schwab",
        name="Schwab / thinkorswim",
        kind="link",
        granularity="underlying_only",
        quote_url=_url(
            "https://www.schwab.com/research/stocks/quotes/summary/{t}"
        ),
    ),
    BrokerAdapter(
        id="fidelity",
        name="Fidelity",
        kind="link",
        granularity="underlying_only",
        quote_url=_url(
            "https://digital.fidelity.com/prgw/digital/research/quote/"
            "dashboard/summary?symbol={t}"
        ),
    ),
    BrokerAdapter(
        id="tastytrade",
        name="Tastytrade",
        kind="link",
        granularity="underlying_only",
        quote_url=_url("https://my.tastytrade.com/app.html#/trade/{t}"),
        caveat=(
            "El enlace abre la app de Tastytrade en el ticker. Es una SPA, así "
            "que si la sesión está cerrada caerás en el login y tendrás que "
            "volver a pulsar."
        ),
    ),
    BrokerAdapter(
        id="webull",
        name="Webull",
        kind="copy",
        granularity="underlying_only",
        caveat=(
            "Webull necesita la bolsa en la URL (nasdaq-WULF vs nyse-WULF) y el "
            "feed no la trae, así que en vez de mandarte a un 404 te copiamos "
            "los tickers para pegarlos en su buscador."
        ),
    ),
    BrokerAdapter(
        id="ibkr",
        name="Interactive Brokers",
        kind="copy",
        granularity="underlying_only",
        caveat=(
            "La web de IBKR no abre una página por símbolo, así que te copiamos "
            "los tickers para pegarlos en el buscador de TWS o del portal."
        ),
    ),
    BrokerAdapter(id="none", name="Solo en Tito", kind="none", granularity="none"),
]


def broker_by_id(id_: str) -> BrokerAdapter | None:
    for b in BROKERS:
        if b.id == id_:
            return b
    return None


#: `SyncStatus`
EstadoSync = Literal["pendiente", "sincronizado", "no_soportado", "error"]


@dataclass
class BrokerSync:
    broker: str
    status: EstadoSync
    #: Qué se mandó realmente: `contract`, `underlying` o nada.
    sent: str | None
    at: str | None
    detail: str | None = None


@dataclass
class WatchlistEntry:
    #: El símbolo OCC identifica el contrato: un contrato entra una sola vez.
    symbol: str
    ticker: str
    type: str            # "call" | "put"
    strike: float | None
    expiration: str | None
    addedAt: str

    #: Foto del momento de marcarla — es contra esto que se mide después.
    entrySpot: float = 0.0
    entryPrice: float = 0.0
    entryDte: int | None = None
    entryPremium: float = 0.0
    entryThetaPctDaily: float | None = None

    #: Tu sizing en ese momento, para juzgar la decisión y no solo la idea.
    maxContracts: int = 0
    binding: str | None = None
    accountSizeAtEntry: float = 0.0
    tolerancePctAtEntry: float = 0.0

    brokerSync: BrokerSync | None = None


@dataclass(frozen=True)
class EntrySource:
    """Lo mínimo que necesita `build_entry` — evita acoplar el watchlist al
    tipo Idea completo."""

    symbol: str
    ticker: str
    type: str
    strike: float | None
    expiration: str | None
    dte: int | None
    price: float
    assetPrice: float
    premium: float
    thetaPctDaily: float | None


def build_entry(
    source: EntrySource,
    sizing: dict[str, Any],
    profile: dict[str, Any],
    now: datetime,
) -> WatchlistEntry:
    return WatchlistEntry(
        symbol=source.symbol,
        ticker=source.ticker,
        type=source.type,
        strike=source.strike,
        expiration=source.expiration,
        addedAt=_iso(now),
        entrySpot=source.assetPrice,
        entryPrice=source.price,
        entryDte=source.dte,
        entryPremium=source.premium,
        entryThetaPctDaily=source.thetaPctDaily,
        maxContracts=sizing.get("maxContracts", 0),
        binding=sizing.get("binding"),
        accountSizeAtEntry=profile.get("accountSize", 0),
        tolerancePctAtEntry=profile.get("tolerancePct", 0),
        brokerSync=None,
    )


def _iso(d: datetime) -> str:
    """`Date.toISOString()`: siempre UTC, milisegundos y `Z`."""
    from datetime import timezone

    u = d.astimezone(timezone.utc) if d.tzinfo else d.replace(tzinfo=timezone.utc)
    return u.strftime("%Y-%m-%dT%H:%M:%S.") + f"{u.microsecond // 1000:03d}Z"


def upsert(
    entries: list[WatchlistEntry], entry: WatchlistEntry
) -> list[WatchlistEntry]:
    """Añade o reemplaza por símbolo.

    Volver a marcar un contrato NO pisa la foto original —esa es la que da
    valor al histórico— pero conserva el estado de sincronización.
    """
    existing = next((e for e in entries if e.symbol == entry.symbol), None)
    if existing is not None:
        return [
            _con_sync(existing, e.brokerSync) if e.symbol == entry.symbol else e
            for e in entries
        ]
    return [entry, *entries]


def _con_sync(base: WatchlistEntry, sync: BrokerSync | None) -> WatchlistEntry:
    """`{ ...existing, brokerSync: e.brokerSync }` — copia, no muta."""
    from dataclasses import replace

    return replace(base, brokerSync=sync)


def remove(entries: list[WatchlistEntry], symbol: str) -> list[WatchlistEntry]:
    return [e for e in entries if e.symbol != symbol]


def mark_synced(
    entries: list[WatchlistEntry], symbol: str, sync: BrokerSync
) -> list[WatchlistEntry]:
    from dataclasses import replace

    return [replace(e, brokerSync=sync) if e.symbol == symbol else e for e in entries]


def payload_for(
    entry: WatchlistEntry, broker: BrokerAdapter
) -> dict[str, str] | None:
    """Qué mandar a un broker dada su granularidad.

    Devuelve ``None`` cuando el broker no puede recibir nada — así la UI dice
    la verdad en vez de fingir que sincronizó.

    Ojo: ``value`` es el símbolo OCC, que es como se identifica el contrato
    aquí. Ningún broker lo acepta tal cual; el agente lo traduce a su id
    interno con `contract_query`.
    """
    if broker.granularity == "contracts":
        return {"sent": "contract", "value": entry.symbol}
    if broker.granularity == "underlying_only":
        return {"sent": "underlying", "value": entry.ticker}
    return None


@dataclass(frozen=True)
class ContractRef:
    """Los cuatro campos que identifican un contrato, con o sin foto."""

    ticker: str
    type: str
    strike: float | None
    expiration: str | None


def contract_query(c: ContractRef) -> dict[str, str] | None:
    """Traduce un contrato nuestro a la consulta que resuelve su id.

    Los brokers no direccionan por símbolo OCC: Robinhood pide un UUID de
    instrumento, que solo se obtiene buscando por subyacente + tipo + strike +
    vencimiento (`get_option_instruments`). Esta función arma esa búsqueda; el
    agente la ejecuta.

    El strike va con **4 decimales** porque es un filtro exacto sobre cadena:
    ``"20"`` no casa con ``"20.0000"`` y la búsqueda vuelve vacía sin decir por
    qué.

    Devuelve ``None`` si falta strike o vencimiento — sin ellos la búsqueda
    daría decenas de contratos y elegir uno sería adivinar. Quien recibe
    ``None`` cae al subyacente.
    """
    if c.strike is None or not c.expiration:
        return None
    return {
        "chain_symbol": c.ticker.upper(),
        "type": c.type,
        "strike_price": _to_fixed4(c.strike),
        "expiration_dates": c.expiration,
    }


def _to_fixed4(x: float) -> str:
    """`Number.prototype.toFixed(4)` — redondeo de JS, no el bancario."""
    from .jsmath import js_to_fixed

    return js_to_fixed(x, 4)


def contract_ref_label(c: ContractRef) -> str:
    """Etiqueta legible: ``WULF $20 CALL 2027-01-15``."""
    if c.strike is None or not c.expiration:
        return c.ticker
    return f"{c.ticker} ${_num_js(c.strike)} {c.type.upper()} {c.expiration}"


def _num_js(x: float) -> str:
    """`${strike}` en una plantilla: 20 se imprime `20`, no `20.0`."""
    if isinstance(x, float) and x.is_integer():
        return str(int(x))
    return str(x)


def underlyings(entries: list[WatchlistEntry]) -> list[str]:
    """Los tickers únicos a sincronizar cuando el broker solo acepta
    subyacentes."""
    return sorted({e.ticker for e in entries})


def sort_entries(entries: list[WatchlistEntry]) -> list[WatchlistEntry]:
    """Las más recientes primero."""
    return sorted(entries, key=lambda e: e.addedAt, reverse=True)


# ── Enlaces al broker ────────────────────────────────────────────────────────


def quote_link(ticker: str, broker: BrokerAdapter) -> str | None:
    """La URL del ticker en el broker, o ``None`` si ese broker no enruta por
    símbolo. Devolver ``None`` es parte del contrato: la UI enseña «copiar» en
    vez de un enlace roto."""
    return broker.quote_url(ticker) if broker.quote_url else None


def ticker_list(entries: list[WatchlistEntry]) -> str:
    """Los tickers listos para pegar en el buscador del broker."""
    return ", ".join(underlyings(entries))


#: El comando que conecta el MCP de Robinhood. El OAuth ocurre en el cliente
#: del agente, no en la web.
ROBINHOOD_MCP_COMMAND = (
    "claude mcp add robinhood-trading --transport http "
    "https://agent.robinhood.com/mcp/trading"
)


# ── Buzón de salida hacia el agente ──────────────────────────────────────────
#
# El watchlist completo vive en el navegador. El buzón es el único puente hacia
# el agente, y viaja **lo mínimo que el broker necesita para identificar el
# contrato**: ticker, y con granularidad `contracts` también tipo, strike y
# vencimiento. Lo que nunca cruza es lo tuyo: ni griegos, ni sizing, ni saldo.
#
# Con un broker `underlying_only` la entrada sigue siendo solo el ticker, así
# que elegir un broker menos capaz también manda menos datos.


@dataclass
class OutboxItem:
    ticker: str
    broker: str
    addedAt: str
    syncedAt: str | None = None

    #: Solo con granularidad `contracts`. Ausentes en la cola vieja de
    #: solo-tickers.
    symbol: str | None = None
    type: str | None = None
    strike: float | None = None
    expiration: str | None = None

    #: Estado terminal: el contrato no se puede resolver en el broker y no se
    #: reintenta. Sin contador de intentos a propósito — cuando
    #: `contract_query` devuelve `None` o el broker no encuentra el
    #: instrumento, ya se sabe que no va a resolverse nunca. Los fallos
    #: transitorios (red, servidor caído) no marcan nada y se reintentan solos.
    failedAt: str | None = None
    failReason: str | None = None


@dataclass(frozen=True)
class OutboxTarget:
    """Lo que se encola: un contrato del watchlist, recortado por la
    granularidad."""

    symbol: str
    ticker: str
    type: str
    strike: float | None
    expiration: str | None


def outbox_key(item: OutboxItem) -> str:
    """La clave de deduplicación.

    Con `contracts` es el contrato (dos strikes del mismo ticker son dos
    trabajos); con `underlying_only` es el ticker (son el mismo trabajo).
    """
    return item.symbol if item.symbol else item.ticker


def outbox_label(item: OutboxItem) -> str:
    """Cómo se anuncia una entrada de la cola en la UI."""
    if not item.symbol or item.type is None:
        return item.ticker
    return contract_ref_label(
        ContractRef(
            ticker=item.ticker,
            type=item.type,
            strike=item.strike,
            expiration=item.expiration,
        )
    )


def add_to_outbox(
    items: list[OutboxItem],
    target: OutboxTarget,
    broker: BrokerAdapter,
    now: datetime,
) -> list[OutboxItem]:
    """Encola un contrato con el detalle que ese broker sabe recibir.

    Si ya está pendiente no lo duplica; si ya se sincronizó tampoco lo
    reencola — marcar dos veces lo mismo no genera trabajo.
    """
    ticker = target.ticker.upper()
    full = broker.granularity == "contracts"
    entry = OutboxItem(
        ticker=ticker,
        broker=broker.id,
        addedAt=_iso(now),
        syncedAt=None,
        symbol=target.symbol if full else None,
        type=target.type if full else None,
        strike=target.strike if full else None,
        expiration=target.expiration if full else None,
    )
    key = outbox_key(entry)
    if any(i.broker == broker.id and outbox_key(i) == key for i in items):
        return items
    return [*items, entry]


def pending_outbox(items: list[OutboxItem], broker: str) -> list[OutboxItem]:
    """Lo que el agente aún no ha empujado, deduplicado y ordenado.

    Devuelve los ítems enteros —no solo el ticker— porque para resolver el
    contrato en el broker hacen falta strike y vencimiento (ver
    `contract_query`).
    """
    seen: set[str] = set()
    out: list[OutboxItem] = []
    for i in items:
        if i.broker != broker or i.syncedAt or i.failedAt:
            continue
        key = outbox_key(i)
        if key in seen:
            continue
        seen.add(key)
        out.append(i)
    return sorted(out, key=outbox_key)


def mark_outbox_synced(
    items: list[OutboxItem], keys: list[str], broker: str, now: datetime
) -> list[OutboxItem]:
    """El agente confirma lo que sí entró en el broker.

    Las claves son símbolos OCC con granularidad `contracts` y tickers con
    `underlying_only` — lo mismo que devolvió `pending_outbox` vía
    `outbox_key`.
    """
    from dataclasses import replace

    done = set(keys)
    return [
        replace(i, syncedAt=_iso(now))
        if i.broker == broker and outbox_key(i) in done and not i.syncedAt
        else i
        for i in items
    ]


def mark_outbox_failed(
    items: list[OutboxItem],
    keys: list[str],
    broker: str,
    reason: str,
    now: datetime,
) -> list[OutboxItem]:
    """El agente aparca lo que no se puede resolver en el broker: un contrato
    sin strike o sin vencimiento (la cola vieja de solo-tickers), o uno que el
    broker no encuentra.

    Es terminal por diseño: sin esto el drenador reintentaría lo mismo cada 15
    minutos para siempre. No pisa lo ya sincronizado — si entró, entró.
    """
    from dataclasses import replace

    bad = set(keys)
    return [
        replace(i, failedAt=_iso(now), failReason=reason)
        if i.broker == broker
        and outbox_key(i) in bad
        and not i.syncedAt
        and not i.failedAt
        else i
        for i in items
    ]


def failed_outbox(items: list[OutboxItem], broker: str) -> list[OutboxItem]:
    """Los aparcados, para que la UI ofrezca volver a marcar ⭐ con el contrato
    completo."""
    return sorted(
        (i for i in items if i.broker == broker and i.failedAt and not i.syncedAt),
        key=outbox_key,
    )


def remove_from_outbox(
    items: list[OutboxItem],
    target: dict[str, Any],
    broker: str,
) -> list[OutboxItem]:
    """Desencola al desmarcar ⭐.

    Las dos condiciones no son redundancia: `outbox_key` vale el **símbolo
    OCC** en las filas nuevas y el **ticker** en las viejas de solo-tickers,
    así que comparar solo contra `symbol` dejaba las filas legado imborrables
    para siempre (``"WULF270115C00020000" != "WULF"``) — pasó de verdad con
    SPXW y SPY. La segunda condición exige que la fila NO tenga contrato
    justamente para no arrastrarse los otros strikes del mismo subyacente: con
    granularidad `contracts` dos strikes de WULF son dos trabajos distintos.

    Sin `symbol` (broker `underlying_only`) se quita todo lo de esa empresa,
    que ahí sí es un solo trabajo.
    """
    symbol = target.get("symbol") or None
    t = target.get("ticker")
    ticker = t.upper() if t else None

    def sobrevive(i: OutboxItem) -> bool:
        if i.broker != broker:
            return True
        if symbol:
            if outbox_key(i) == symbol:
                return False
            return not (not i.symbol and ticker is not None and i.ticker == ticker)
        return not (ticker is not None and i.ticker == ticker)

    return [i for i in items if sobrevive(i)]
