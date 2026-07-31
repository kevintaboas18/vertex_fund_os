"""Tarea 7 del Proceso Principal — monitoreo de noticias.

Port de `web/lib/news.ts`. Fuentes en `RSS Feed.md`.

**Dos capas, porque ninguna sola alcanza:**

1. **Macro** — los feeds RSS del documento (CNBC, Investing.com). Son generales
   de mercado, no por empresa: buscando TSLA lo normal es que ninguno la
   mencione. Dan el contexto que afecta a todos los tickers por igual (Fed,
   tarifas, inflación) y se cachean 15 min porque el resultado es idéntico para
   cada búsqueda.
2. **Empresa** — `GET api.massive.com/v2/reference/news?ticker=X`, que además
   devuelve **sentimiento por ticker con su razonamiento**. Cache de 5 min.

**Puente entre ambas:** si un titular macro menciona a la empresa (por nombre
limpio o por ticker en mayúsculas), se promueve a la capa de empresa.

**Bandera de contradicción.** Las noticias **NO alteran los 100 pts del
scorecard** — las 6 categorías ya suman 100%. Entran como una bandera que
confronta la dirección del flujo contra el sesgo de las noticias:

===========  ==========  ==========================================
Flujo        Noticias    Bandera
===========  ==========  ==========================================
Alcista      Negativas   ⚠ Conflicto — "compran contra el pánico"
Bajista      Positivas   ⚠ Conflicto — "venden contra la euforia"
Misma dir.   —           ✓ Confirmación — reacciona, no anticipa
Neutro       —           Sin bandera
===========  ==========  ==========================================

El sesgo sale **solo de la capa de empresa**, que es la única con sentimiento
por ticker, y se pondera por frescura.
"""

from __future__ import annotations

import html
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Literal, Sequence

__all__ = [
    "MACRO_FEEDS",
    "Sentiment",
    "Bias",
    "FlagKind",
    "NewsItem",
    "NewsBias",
    "ContradictionFlag",
    "NewsReport",
    "decode_entities",
    "parse_feed_date",
    "parse_rss",
    "company_aliases",
    "mentions_company",
    "recency_weight",
    "news_bias",
    "flow_bias",
    "contradiction_flag",
    "fetch_ticker_news",
    "fetch_macro_feeds",
    "build_news_report",
]

Sentiment = Literal["positive", "negative", "neutral"]
Bias = Literal["bullish", "bearish", "mixed", "neutral"]
FlagKind = Literal["confirm", "conflict", "none"]

#: Feeds de `RSS Feed.md`. `siteContentMetadata` queda FUERA a propósito:
#: responde HTTP 200 pero devuelve cero artículos (es un módulo del estándar
#: RSS de CNBC, no un feed). Verificado el 23-jul-2026.
MACRO_FEEDS: list[dict[str, str]] = [
    {"name": "CNBC — Top News",
     "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114"},
    {"name": "CNBC — Economía",
     "url": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258"},
    {"name": "Investing.com — Earnings",
     "url": "https://www.investing.com/rss/news_1062.rss"},
    {"name": "Investing.com — Macro",
     "url": "https://www.investing.com/rss/news_14.rss"},
]


@dataclass
class NewsItem:
    id: str
    title: str
    url: str
    publisher: str
    published_utc: str
    description: str | None = None
    #: Sentimiento para ESTE ticker (solo lo da Massive).
    sentiment: Sentiment | None = None
    reasoning: str | None = None
    layer: Literal["company", "macro"] = "macro"
    #: Qué término hizo match cuando la noticia viene de un feed macro.
    matched_by: str | None = None


# ---------- parseo de RSS ----------

_ENTITIES = {"amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'",
             "#39": "'", "nbsp": " "}


def decode_entities(s: str) -> str:
    """Decodifica entidades HTML. Los feeds las traen mezcladas y sin decodificar
    un titular sale con `&amp;` en medio."""
    def rep(m: re.Match) -> str:
        code = m.group(1)
        named = _ENTITIES.get(code.lower())
        if named:
            return named
        if code.startswith("#"):
            try:
                return chr(int(code[1:]))
            except (ValueError, OverflowError):
                return m.group(0)
        return m.group(0)

    return re.sub(r"&(#?\w+);", rep, s)


def _tag(block: str, name: str) -> str | None:
    m = re.search(rf"<{name}[^>]*>([\s\S]*?)</{name}>", block, re.I)
    if not m:
        return None
    raw = re.sub(r"<!\[CDATA\[([\s\S]*?)\]\]>", r"\1", m.group(1)).strip()
    return decode_entities(raw) if raw else None


def parse_feed_date(raw: str | None) -> str | None:
    """Fecha del feed a ISO.

    CNBC usa RFC-822 (``Fri, 24 Jul 2026 03:15:46 GMT``); Investing.com usa
    ``2026-07-24 02:54:27`` **sin zona** — se asume UTC. Sin ese caso especial,
    las de Investing se leerían en la zona del servidor y la ponderación por
    frescura saldría movida.
    """
    if not raw:
        return None
    naive = re.match(r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})$", raw.strip())
    try:
        if naive:
            d = datetime.fromisoformat(f"{naive.group(1)}T{naive.group(2)}+00:00")
        else:
            from email.utils import parsedate_to_datetime
            d = parsedate_to_datetime(raw)
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError, IndexError):
        return None
    return d.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_rss(xml: str, publisher: str) -> list[NewsItem]:
    """Extrae los ``<item>`` de un XML de RSS.

    Tolerante a propósito: los feeds vienen en una sola línea y con CDATA.
    """
    items: list[NewsItem] = []
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    for m in re.finditer(r"<item[^>]*>([\s\S]*?)</item>", xml, re.I):
        block = m.group(1)
        title = _tag(block, "title")
        link = _tag(block, "link")
        if not title or not link:
            continue
        items.append(NewsItem(
            id=_tag(block, "guid") or link,
            title=title,
            url=link,
            publisher=publisher,
            published_utc=parse_feed_date(_tag(block, "pubDate")) or epoch,
            description=_tag(block, "description"),
            layer="macro",
        ))
    return items


# ---------- match empresa ----------

_NAME_NOISE = re.compile(
    r"\b(inc|inc\.|incorporated|corp|corp\.|corporation|company|co|co\.|plc|ltd|"
    r"ltd\.|limited|holdings?|group|the|common|ordinary|class\s+[a-c]|shares?|"
    r"stock|capital|nv|sa|ag)\b",
    re.I,
)


def company_aliases(ticker: str, name: str | None) -> list[str]:
    """Nombres con los que buscar a la empresa en un titular.

    ``"Tesla, Inc. Common Stock"`` → ``["Tesla"]``; ``"NVIDIA Corporation"`` →
    ``["NVIDIA"]``. Los tickers de 1-2 letras **se descartan**: "A", "IT" u "ON"
    harían match con cualquier titular.
    """
    out: list[str] = []
    t = (ticker or "").strip().upper()
    if len(t) >= 3:
        out.append(t)
    if name:
        clean = re.sub(r"\s+", " ", _NAME_NOISE.sub(" ", re.sub(r"[,.]", " ", name))).strip()
        if len(clean) >= 3 and clean not in out:
            out.append(clean)
        first = clean.split(" ")[0] if clean else ""
        if len(first) >= 4 and first not in out:
            out.append(first)
    return out


def mentions_company(text: str, aliases: Sequence[str]) -> str | None:
    """Devuelve el alias que apareció en el texto, o ``None``.

    El **ticker exige mayúsculas**: sin eso, "tesla" en minúsculas dentro de una
    palabra cualquiera dispararía el match.
    """
    for a in aliases:
        is_ticker = a == a.upper() and " " not in a
        pattern = rf"(^|[^\w]){re.escape(a)}($|[^\w])"
        if re.search(pattern, text, 0 if is_ticker else re.I):
            return a
    return None


# ---------- sesgo y bandera ----------

_HOUR = 3600.0


def recency_weight(published_utc: str, now: datetime) -> float:
    """Peso por frescura: una noticia de hoy pesa más que una de la semana pasada."""
    try:
        pub = datetime.fromisoformat(str(published_utc).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return 1.0
    if pub.tzinfo is None:
        pub = pub.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    age = (now - pub).total_seconds() / _HOUR
    if age < 0:
        return 1.0
    if age <= 24:
        return 1.0
    if age <= 72:
        return 0.6
    if age <= 24 * 7:
        return 0.3
    return 0.1


@dataclass(frozen=True)
class NewsBias:
    bias: Bias
    #: −1 (muy negativo) … +1 (muy positivo), ponderado por frescura.
    score: float
    positive: int
    negative: int
    neutral: int


def news_bias(items: Sequence[NewsItem], now: datetime) -> NewsBias:
    """Sesgo de las noticias, ponderado por frescura. Umbral ±0.25."""
    num = den = 0.0
    positive = negative = neutral = 0
    for it in items:
        if not it.sentiment:
            continue
        if it.sentiment == "positive":
            positive += 1
        elif it.sentiment == "negative":
            negative += 1
        else:
            neutral += 1
        w = recency_weight(it.published_utc, now)
        den += w
        if it.sentiment == "positive":
            num += w
        elif it.sentiment == "negative":
            num -= w

    score = (num / den) if den > 0 else 0.0
    bias: Bias = "neutral"
    if den == 0:
        bias = "neutral"
    elif score >= 0.25:
        bias = "bullish"
    elif score <= -0.25:
        bias = "bearish"
    elif positive > 0 and negative > 0:
        bias = "mixed"
    return NewsBias(bias=bias, score=score, positive=positive, negative=negative, neutral=neutral)


def flow_bias(call_pct: float) -> Bias:
    """Dirección del flujo a partir del % de premium en calls."""
    if call_pct >= 60:
        return "bullish"
    if call_pct <= 40:
        return "bearish"
    return "neutral"


@dataclass(frozen=True)
class ContradictionFlag:
    kind: FlagKind
    title: str
    detail: str


def contradiction_flag(flow: Bias, news: NewsBias) -> ContradictionFlag:
    """Confronta lo que apuesta el dinero contra lo que dicen las noticias.

    **NO toca los 100 pts del scorecard.** Es contexto, no puntuación.
    """
    f = flow if flow in ("bullish", "bearish") else None
    n = news.bias if news.bias in ("bullish", "bearish") else None

    if not f or not n:
        return ContradictionFlag(
            kind="none",
            title="Sin contradicción clara",
            detail=(
                "Las noticias no marcan una dirección definida, así que no hay nada "
                "que confrontar con el flujo."
                if not n
                else "El flujo está repartido entre calls y puts: no hay una apuesta "
                     "dominante que contrastar."
            ),
        )

    if f == n:
        return ContradictionFlag(
            kind="confirm",
            title=("Flujo alcista confirmado por las noticias" if f == "bullish"
                   else "Flujo bajista confirmado por las noticias"),
            detail=(
                "El dinero apuesta en la misma dirección que las noticias. Ojo: cuando "
                "la noticia ya salió, el flujo suele estar reaccionando y no anticipando."
            ),
        )

    return ContradictionFlag(
        kind="conflict",
        title=("Flujo alcista contra noticias negativas" if f == "bullish"
               else "Flujo bajista contra noticias positivas"),
        detail=(
            "Alguien está comprando contra el pánico: la noticia es mala pero el dinero "
            "grande apuesta al alza."
            if f == "bullish"
            else "Alguien está vendiendo contra la euforia: la noticia es buena pero el "
                 "dinero grande apuesta a la baja."
        ),
    )


# ---------- red (solo servidor) ----------

_MACRO_TTL = 15 * 60  # los feeds macro son idénticos para todos los tickers
_TICKER_TTL = 5 * 60

_macro_cache: dict[str, object] = {"at": 0.0, "value": None}
_ticker_cache: dict[str, tuple[float, list[NewsItem]]] = {}


def _http_get(url: str, headers: dict[str, str], timeout: float) -> str | None:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
        return None


def fetch_ticker_news(ticker: str, limit: int = 12, timeout: float = 12.0) -> list[NewsItem]:
    """Capa 2 — noticias del ticker, con el sentimiento que ya calcula Massive."""
    import json as _json

    clean = (ticker or "").strip().upper()
    hit = _ticker_cache.get(clean)
    if hit and time.time() - hit[0] < _TICKER_TTL:
        return hit[1]

    key = os.environ.get("MASSIVE_API_KEY", "").strip()
    if not key:
        return []

    url = (
        f"https://api.massive.com/v2/reference/news?ticker={urllib.parse.quote(clean)}"
        f"&order=desc&sort=published_utc&limit={int(limit)}"
    )
    body = _http_get(url, {"Authorization": f"Bearer {key}"}, timeout)
    if not body:
        return []
    try:
        results = (_json.loads(body) or {}).get("results") or []
    except ValueError:
        return []

    items: list[NewsItem] = []
    for r in results:
        title, art = r.get("title"), r.get("article_url")
        if not title or not art:
            continue
        # Un artículo cubre varios tickers; solo interesa el insight del nuestro.
        mine = next(
            (i for i in (r.get("insights") or [])
             if str(i.get("ticker") or "").upper() == clean),
            None,
        )
        s = str((mine or {}).get("sentiment") or "").lower()
        items.append(NewsItem(
            id=r.get("id") or art,
            title=title,
            url=art,
            publisher=(r.get("publisher") or {}).get("name") or "—",
            published_utc=r.get("published_utc")
                          or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            description=r.get("description"),
            sentiment=s if s in ("positive", "negative", "neutral") else None,  # type: ignore[arg-type]
            reasoning=(mine or {}).get("sentiment_reasoning"),
            layer="company",
        ))

    _ticker_cache[clean] = (time.time(), items)
    return items


def fetch_macro_feeds(timeout: float = 8.0) -> list[NewsItem]:
    """Capa 1 — los feeds RSS del documento.

    Un feed caído **no puede tumbar el panel**: cada uno se captura por separado
    y los que fallen simplemente no aportan.
    """
    cached = _macro_cache.get("value")
    if cached is not None and time.time() - float(_macro_cache["at"]) < _MACRO_TTL:  # type: ignore[arg-type]
        return cached  # type: ignore[return-value]

    collected: list[NewsItem] = []
    for f in MACRO_FEEDS:
        body = _http_get(
            f["url"],
            {"User-Agent": "Mozilla/5.0 (compatible; TitoMetralleta/1.0)"},
            timeout,
        )
        if body:
            collected.extend(parse_rss(body, f["name"]))

    seen: set[str] = set()
    items = []
    for it in collected:
        if it.url in seen:
            continue
        seen.add(it.url)
        items.append(it)
    items.sort(key=lambda i: i.published_utc, reverse=True)

    _macro_cache["at"] = time.time()
    _macro_cache["value"] = items
    return items


@dataclass
class NewsReport:
    ticker: str
    company: list[NewsItem]
    macro: list[NewsItem]
    #: Titulares de los feeds RSS que sí nombran a la empresa.
    promoted: list[NewsItem]
    bias: NewsBias
    feeds_ok: int
    feeds_total: int


def build_news_report(
    ticker: str,
    company_name: str | None,
    now: datetime,
    company_items: Sequence[NewsItem] | None = None,
    macro_items: Sequence[NewsItem] | None = None,
) -> NewsReport:
    """Junta las dos capas y calcula el sesgo de noticias del ticker.

    `company_items` y `macro_items` permiten inyectar los datos ya descargados
    (así la función es testeable sin red); si no se pasan, los baja.
    """
    company = list(company_items) if company_items is not None else fetch_ticker_news(ticker)
    macro_all = list(macro_items) if macro_items is not None else fetch_macro_feeds()

    aliases = company_aliases(ticker, company_name)
    promoted: list[NewsItem] = []
    macro: list[NewsItem] = []
    for it in macro_all:
        hit = mentions_company(f"{it.title} {it.description or ''}", aliases)
        if hit:
            promoted.append(replace(it, matched_by=hit))
        else:
            macro.append(it)

    return NewsReport(
        ticker=ticker,
        company=company,
        macro=macro[:6],
        promoted=promoted[:4],
        # El sesgo sale SOLO de la capa de empresa: es la única con sentimiento
        # por ticker. Los feeds macro no saben nada de este subyacente.
        bias=news_bias(company, now),
        feeds_ok=len({i.publisher for i in macro_all}),
        feeds_total=len(MACRO_FEEDS),
    )
