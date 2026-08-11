"""Estimador del próximo reporte de resultados.

Port de `web/lib/earnings.ts`.

El plan de Massive **no trae calendario de earnings** — él lo verificó:
`/benzinga/v1/earnings` da 403 y `/v1/reference/earnings` da 404. Se usan dos
proxies, y la UI declara siempre que es una **estimación**:

1. La cadencia de `filing_date` de `/vX/reference/financials` (~91 días entre
   reportes).
2. El skew del frente que `ivcontext` ya calcula (>+10 pts = evento inminente).

La parte pura (`estimate_next_earnings`, `earnings_flag`) no toca red.

**Limitación declarada, suya y heredada:** el escaneo Wheel real llama siempre
con `front_skew=None`, porque ese escaneo no calcula `iv_context_score` por
ticker (no tiene el flujo de MarketSnack por símbolo). O sea que hoy
``dentro_confirmado`` es **inalcanzable** en producción y el flag efectivo es
solo la cadencia de filings: el "doble proxy" es, en la práctica, uno solo. El
parámetro se conserva para el día en que el skew esté disponible en el escaneo,
y los tests sí lo ejercitan pasando un valor > 10 a propósito.
"""

from __future__ import annotations

import json as _json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Sequence

from .wheel import EarningsFlag

__all__ = ["QUARTER_DAYS", "estimate_next_earnings", "earnings_flag",
           "fetch_filing_dates", "earnings_for_ticker"]

QUARTER_DAYS = 91


def _t(dia: str) -> float | None:
    """`new Date(`${d}T00:00:00Z`).getTime()` — UTC, no zona local."""
    try:
        return datetime.fromisoformat(f"{dia}T00:00:00+00:00").timestamp()
    except (ValueError, TypeError):
        return None


def estimate_next_earnings(filing_dates: Sequence[str], now: datetime) -> str | None:
    """Estima el próximo reporte desde los `filing_date` pasados.

    Toma el más reciente y avanza en saltos de ~91 días hasta pasar HOY.
    """
    tiempos = sorted(t for t in (_t(d) for d in (filing_dates or [])) if t is not None)
    if not tiempos:
        return None
    siguiente = tiempos[-1]
    ahora = now.timestamp()
    while siguiente <= ahora:
        siguiente += QUARTER_DAYS * 86_400
    return datetime.fromtimestamp(siguiente, tz=timezone.utc).date().isoformat()


def earnings_flag(next_earnings: str | None, expiration: str,
                  front_skew: float | None) -> EarningsFlag:
    if not next_earnings:
        return "no_aplica"
    e, x = _t(next_earnings), _t(expiration)
    # Una fecha ilegible NO da vía libre. Su `getTime()` devuelve `NaN` y toda
    # comparación con `NaN` es falsa, así que `earnings > exp` no se cumple y
    # cae en "dentro": el candidato pierde 7 de sus 10 puntos de reporte.
    #
    # El port devolvía "no_aplica", que son 10 de 10 — o sea que un dato
    # corrupto pasaba de penalizar a absolver, en la única guarda que existe
    # para que no te pille un reporte dentro del vencimiento. Lo cazó
    # `diff_wheel.sh`. Se adopta el suyo, que además es el prudente.
    if e is not None and x is not None and e > x:
        return "fuera"
    # Cae dentro del vencimiento. ¿Lo confirma el mercado?
    return "dentro_confirmado" if (front_skew or 0) > 10 else "dentro"


# ── Fetch (I/O) ──────────────────────────────────────────────────────────

def fetch_filing_dates(ticker: str, timeout: float = 12.0) -> list[str]:
    """Fechas de reporte pasadas. Devuelve `[]` si el ticker no reporta (ETF).

    Nunca lanza: un fallo aquí solo significa "no se pudo estimar el reporte",
    y `earnings_flag` lo traduce a `no_aplica`. Bloquear el escaneo entero de
    Wheel por el calendario de un ticker sería peor que no tenerlo.
    """
    key = os.environ.get("MASSIVE_API_KEY", "").strip()
    if not key:
        return []
    clean = (ticker or "").strip().upper()
    if not clean:
        return []
    url = (f"https://api.massive.com/vX/reference/financials"
           f"?ticker={urllib.parse.quote(clean)}"
           f"&timeframe=quarterly&limit=6&order=desc&sort=filing_date")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            data = _json.loads(res.read().decode("utf-8", errors="replace"))
    except Exception:                          # noqa: BLE001 — su `.catch(() => null)`
        return []
    if not isinstance(data, dict):
        return []
    return [r.get("filing_date") for r in (data.get("results") or [])
            if isinstance(r, dict) and r.get("filing_date")]


def earnings_for_ticker(ticker: str, expiration: str, front_skew: float | None,
                        now: datetime) -> EarningsFlag:
    filings = fetch_filing_dates(ticker)
    return earnings_flag(estimate_next_earnings(filings, now), expiration, front_skew)
