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
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable

from .levels import LvlBar
from .structure import ChainRow

__all__ = [
    "BASE_URL",
    "MassiveError",
    "ChainResult",
    "fetch_option_chain",
    "fetch_daily_bars",
]

BASE_URL = "https://api.massive.com"


class MassiveError(RuntimeError):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def _api_key() -> str:
    key = os.environ.get("MASSIVE_API_KEY", "").strip()
    if not key:
        raise MassiveError("Falta MASSIVE_API_KEY en el entorno.")
    return key


def _max_pages() -> int:
    try:
        n = int(os.environ.get("MASSIVE_MAX_PAGES", "40"))
    except ValueError:
        return 40
    return n if n > 0 else 40


def _describe(status: int, ticker: str, body: str) -> str:
    if status in (401, 403):
        return "Massive rechazó la API key (revisa MASSIVE_API_KEY)."
    if status == 404:
        return f"Massive no tiene datos para {ticker}."
    if status == 429:
        return "Límite de tasa de Massive; reintenta en unos segundos."
    return f"Massive respondió {status}. {body[:200]}".strip()


def _get(url: str, key: str, ticker: str, timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return _json.loads(res.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        raise MassiveError(_describe(e.code, ticker, body), e.code) from e
    except urllib.error.URLError as e:
        raise MassiveError(f"No se pudo conectar con Massive: {e.reason}") from e
    except ValueError as e:
        raise MassiveError("Respuesta de Massive no parseable.") from e


@dataclass
class ChainResult:
    rows: list[ChainRow] = field(default_factory=list)
    underlying_price: float | None = None
    pages: int = 0
    truncated: bool = False


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
        for c in data.get("results") or []:
            details = c.get("details") or {}
            day = c.get("day") or {}
            strike = _num(details.get("strike_price"))
            exp = str(details.get("expiration_date") or "")[:10]
            ct = str(details.get("contract_type") or "").lower()
            if strike <= 0 or not exp or ct not in ("call", "put"):
                continue
            oi = int(_num(c.get("open_interest")))
            rows.append(ChainRow(
                contract_type=ct,  # type: ignore[arg-type]
                expiration=exp,
                strike=strike,
                open_interest=oi,
                volume=int(_num(day.get("volume"))),
                notional_value=oi * 100 * strike,
            ))
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

    return ChainResult(rows=rows, underlying_price=underlying, pages=page, truncated=truncated)


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


def fetch_daily_bars(ticker: str, days: int = 365, timeout: float = 25.0) -> list[LvlBar]:
    """Barras diarias del subyacente (`/v2/aggs/...`), de más vieja a más nueva."""
    key = _api_key()
    clean = (ticker or "").strip().upper()
    if not clean:
        raise MassiveError("Ticker vacío.")
    end = date.today()
    start = end - timedelta(days=days)
    url = (
        f"{BASE_URL}/v2/aggs/ticker/{urllib.parse.quote(clean)}/range/1/day/"
        f"{start.isoformat()}/{end.isoformat()}?adjusted=true&sort=asc&limit=50000"
    )
    data = _get(url, key, clean, timeout)
    out: list[LvlBar] = []
    for b in data.get("results") or []:
        ts = b.get("t")
        if not isinstance(ts, (int, float)):
            continue
        day = date.fromtimestamp(ts / 1000).isoformat()
        low, high, close = _num(b.get("l")), _num(b.get("h")), _num(b.get("c"))
        if low <= 0 or high <= 0 or close <= 0:
            continue
        out.append(LvlBar(time=day, high=high, low=low, close=close))
    return out
