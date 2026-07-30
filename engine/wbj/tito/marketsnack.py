"""Cliente del API interno de MarketSnack (app.marketsnack.com).

Port de `web/lib/marketsnack.ts`. **Solo servidor.**

Auth por **cookie de sesión** (`MARKETSNACK_COOKIE` en el entorno). Es la fuente
del tape que alimenta a los sub-agentes 1, 2, 3 y 5: entrega `side` (ask/bid/mid),
bid/ask reales, griegos completos e IV por trade — justo lo que la cadena de
opciones sola no da.

Este es el ÚNICO módulo del paquete que toca la red. Todo lo demás es puro y se
testea sin credenciales.

Nota operativa: la cookie **caduca sola**. Cuando lo hace, MarketSnack responde
401/403 o redirige al login (a veces con 200 y cuerpo HTML), y este cliente lo
convierte en `MarketSnackError` con un mensaje accionable en vez de dejar que un
JSON malformado se propague hacia el scorecard.
"""

from __future__ import annotations

import json as _json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

__all__ = ["BASE_URL", "MarketSnackError", "FlowResult", "fetch_flow", "fetch_market_flow"]

BASE_URL = "https://app.marketsnack.com"


class MarketSnackError(RuntimeError):
    """Fallo al hablar con MarketSnack. `status` es el HTTP cuando aplica."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def _cookie() -> str:
    c = os.environ.get("MARKETSNACK_COOKIE", "")
    if not c.strip():
        raise MarketSnackError(
            "Falta MARKETSNACK_COOKIE en el entorno. Copia tu cookie de sesión de "
            "app.marketsnack.com (DevTools → Network → /api/flow_feed → header Cookie)."
        )
    return c.strip()


@dataclass
class FlowResult:
    trades: list[dict[str, Any]] = field(default_factory=list)
    pages: int = 0
    truncated: bool = False


def _get(url: str, cookie_header: str, timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Cookie": cookie_header,
            "Cache-Control": "no-store",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            status = res.status
            body = res.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:  # type: ignore[attr-defined]
        status = e.code
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        if status in (401, 403) or 300 <= status < 400:
            raise MarketSnackError(
                "Sesión de MarketSnack inválida o expirada. Actualiza MARKETSNACK_COOKIE.",
                status,
            ) from e
        raise MarketSnackError(f"MarketSnack respondió {status}. {body[:200]}".strip(), status) from e
    except urllib.error.URLError as e:  # type: ignore[attr-defined]
        raise MarketSnackError(f"No se pudo conectar con MarketSnack: {e.reason}") from e

    # Sesión muerta con 200 + HTML del login: si no es JSON, no es una respuesta útil.
    if body.lstrip().startswith("<"):
        raise MarketSnackError(
            "MarketSnack devolvió HTML en vez de JSON: la sesión caducó y te está "
            "mandando al login. Actualiza MARKETSNACK_COOKIE.",
            status,
        )
    try:
        return _json.loads(body)
    except ValueError as e:
        raise MarketSnackError(f"Respuesta de MarketSnack no parseable: {body[:200]}") from e


def _paginate(
    symbol: str | None,
    period: str = "5d",
    max_pages: int = 10,
    min_premium: float | None = None,
    target_days: int | None = None,
    on_page: Callable[[int, int], None] | None = None,
    timeout: float = 25.0,
) -> FlowResult:
    """Cuerpo de paginación compartido. ``symbol is None`` → todo el mercado."""
    cookie_header = _cookie()
    trades: list[dict[str, Any]] = []
    token: str | None = None
    page = 0
    truncated = False

    # La paginación del feed camina hacia atrás en el tiempo; con target_days
    # paramos al cubrir la ventana pedida en vez de gastar todas las páginas.
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=target_days) if target_days else None
    )

    while True:
        page += 1
        params: list[tuple[str, str]] = [("filter[scope]", "all")]
        if symbol:
            params.append(("filter[symbol][]", symbol))
        params.append(("period", period))
        if min_premium and min_premium > 0:
            params.append(("filter[premium][gte]", str(int(min_premium))))
        if token:
            params.append(("next_page_token", token))
        url = f"{BASE_URL}/api/flow_feed?{urllib.parse.urlencode(params)}"

        data = _get(url, cookie_header, timeout)
        lst = data.get("list") or []
        trades.extend(lst)
        if on_page:
            on_page(page, len(trades))

        token = (data.get("meta") or {}).get("next_page_token")
        if not lst:
            break
        if cutoff is not None:
            oldest = lst[-1].get("timestamp")
            if oldest:
                try:
                    ts = datetime.fromisoformat(str(oldest).replace("Z", "+00:00"))
                    if ts < cutoff:
                        break  # ventana cubierta
                except (ValueError, TypeError):
                    pass
        if page >= max_pages:
            truncated = bool(token)
            break
        if not token:
            break

    return FlowResult(trades=trades, pages=page, truncated=truncated)


def fetch_flow(ticker: str, **opts: Any) -> FlowResult:
    """Descarga el flujo (Time & Sales) de un ticker, paginando por `next_page_token`."""
    clean = (ticker or "").strip().upper()
    if not clean:
        raise MarketSnackError("Ticker vacío.")
    return _paginate(clean, **opts)


def fetch_market_flow(**opts: Any) -> FlowResult:
    """Igual que `fetch_flow` pero SIN filtro de símbolo: todo el mercado.

    El piso de premium (`min_premium`) filtra **server-side**, así que el payload
    se mantiene chico aunque el universo sea el mercado entero.
    """
    return _paginate(None, **opts)
