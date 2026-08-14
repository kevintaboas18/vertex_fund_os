"""Financial Modeling Prep (FMP) provider — /stable/ API.

Wraps the current FMP `/stable/` REST API (the legacy `/api/v3/` endpoints
were retired): company profile, financial statements (income/balance/cash
flow, annual + quarterly), split/dividend-adjusted daily EOD prices, peers,
analyst estimates, insider trades (Form 4), institutional holders (13F),
and the earnings calendar. All `/stable/` endpoints take `symbol` as a
query parameter (not a path segment).

`FMPProvider` is disabled (`available == False`) when no API key is
configured; every public method then returns `None` immediately without
touching the cache or the network. Requests and caching are delegated to
`wbj.providers.base.Provider.get_json` — this module only builds
URLs/params and picks cache keys / max_age_days per data type. Endpoints
not included in the caller's plan return a non-JSON "Restricted Endpoint"
body, which `get_json` turns into `None` (graceful degradation).
"""

from __future__ import annotations

from datetime import date
from typing import Any

import logging

logger = logging.getLogger(__name__)

from wbj.providers.base import Provider

BASE_URL = "https://financialmodelingprep.com/stable"

# max_age_days per cache key:
#   ohlcv_daily/quote 1, analyst_estimates 7, statements 1/7,
#   profile/peers/holders/insiders 7.
_MAX_AGE_OHLCV = 1
_MAX_AGE_ESTIMATES = 7

# Un estado financiero es INMUTABLE una vez presentado. Lo que caduca no es
# la cifra: es nuestro conocimiento de si ya existe uno NUEVO. Confundir las
# dos cosas costaba un trimestre entero.
#
# Con 30 dias para todo, una empresa presentaba su Q2 y el motor seguia
# sirviendo el Q1 durante un mes -- justo en temporada de resultados, que es
# cuando mas importa. Medido el 2026-08-13: FMP tenia el Q2 de AMD
# (2026-06-27), PLTR y JPM (2026-06-30) disponible en vivo, y el packet
# entregaba el trimestre anterior. AMD a 138 dias, PLTR y JPM a 135, por
# encima del limite de 120 dias que DATA_POLICY.md fija para marcar el
# packet financiero rancio.
#
# Trimestral a 1 dia porque aparece uno nuevo cada ~90 y hay que verlo el
# dia que sale. Anual a 7 porque aparece uno al ano y una semana de retraso
# sobre ese ritmo no mueve ninguna metrica.
_MAX_AGE_STATEMENT_QUARTER = 1
_MAX_AGE_STATEMENT_ANNUAL = 7
_MAX_AGE_STATEMENT = _MAX_AGE_STATEMENT_ANNUAL   # compatibilidad
_MAX_AGE_REFERENCE = 7


def _years_ago(d: date, years: int) -> date:
    """Return the date `years` years before `d`, handling Feb 29 safely."""
    try:
        return d.replace(year=d.year - years)
    except ValueError:
        return d.replace(month=2, day=28, year=d.year - years)


class FMPProvider(Provider):
    """Financial Modeling Prep data provider (/stable/ API)."""

    @property
    def available(self) -> bool:
        """True iff an FMP API key is configured."""
        return bool(self.settings and getattr(self.settings, "fmp_api_key", None))

    def _params(self, **extra: Any) -> dict[str, Any]:
        params = {"apikey": self.settings.fmp_api_key}
        params.update(extra)
        return params

    def profile(self, t: str) -> list | dict | None:
        """Company profile: name, sector, industry, market cap, price, beta."""
        if not self.available:
            return None
        return self.get_json(
            f"{BASE_URL}/profile",
            self._params(symbol=t),
            "profile", t, max_age_days=_MAX_AGE_REFERENCE,
        )

    def screener_universo(self, min_market_cap: int = 2_000_000_000,
                          limit: int = 6000,
                          exchange: str | None = None,
                          sector: str | None = None) -> list | dict | None:
        """El universo cotizado MUNDIAL, con la industria de cada empresa.

        Lo usa el barrido de TAM para saber QUE industrias existen y cuantas
        empresas cubre cada una. El orden importa: cada TAM cuesta peticiones
        al proveedor de busqueda y su cuota es finita, asi que resolver
        primero una industria de 54 empresas rinde mas que una de dos.

        Se cachea un dia: el censo de industrias no cambia de una hora a otra
        y bajar 3.000 filas por corrida seria gratuito solo en apariencia.
        """
        if not self.available:
            return None
        # Sin filtro de bolsa: el TAM que resuelve este censo es MUNDIAL, asi
        # que la industria de una empresa de Tokio o de Frankfurt cuenta igual
        # que la de Nueva York. Limitarlo a NASDAQ+NYSE dejaba fuera
        # industrias enteras -- automocion sin Toyota ni VW, lujo sin LVMH --
        # y por tanto sus TAM sin resolver.
        extra: dict[str, Any] = {}
        if exchange:
            extra["exchange"] = exchange
        # Filtrar por SECTOR permite resolver el TAM por tandas -- las 14
        # industrias de Technology de una vez -- en vez de un barrido de 149
        # que la cuota corta a la mitad.
        if sector:
            extra["sector"] = sector
        return self.get_json(
            f"{BASE_URL}/company-screener",
            self._params(marketCapMoreThan=min_market_cap, limit=limit, **extra),
            "screener_universo",
            f"_universo_{sector or 'todo'}_{exchange or 'mundial'}",
            max_age_days=1,
        )

    def income_annual(self, t: str, limit: int = 6) -> list | dict | None:
        """Annual income statements, most recent `limit` fiscal years."""
        if not self.available:
            return None
        return self.get_json(
            f"{BASE_URL}/income-statement",
            self._params(symbol=t, period="annual", limit=limit),
            "income_annual", t, max_age_days=_MAX_AGE_STATEMENT_ANNUAL,
        )

    def income_quarterly(self, t: str, limit: int = 21) -> list | dict | None:
        """Quarterly income statements, most recent `limit` quarters."""
        if not self.available:
            return None
        return self.get_json(
            f"{BASE_URL}/income-statement",
            self._params(symbol=t, period="quarter", limit=limit),
            "income_quarterly", t, max_age_days=_MAX_AGE_STATEMENT_QUARTER,
        )

    def balance_annual(self, t: str, limit: int = 6) -> list | dict | None:
        """Annual balance sheet statements, most recent `limit` fiscal years."""
        if not self.available:
            return None
        return self.get_json(
            f"{BASE_URL}/balance-sheet-statement",
            self._params(symbol=t, period="annual", limit=limit),
            "balance_annual", t, max_age_days=_MAX_AGE_STATEMENT_ANNUAL,
        )

    def balance_quarterly(self, t: str, limit: int = 21) -> list | dict | None:
        """Quarterly balance sheet statements, most recent `limit` quarters."""
        if not self.available:
            return None
        return self.get_json(
            f"{BASE_URL}/balance-sheet-statement",
            self._params(symbol=t, period="quarter", limit=limit),
            "balance_quarterly", t, max_age_days=_MAX_AGE_STATEMENT_QUARTER,
        )

    def cashflow_annual(self, t: str, limit: int = 6) -> list | dict | None:
        """Annual cash flow statements, most recent `limit` fiscal years."""
        if not self.available:
            return None
        return self.get_json(
            f"{BASE_URL}/cash-flow-statement",
            self._params(symbol=t, period="annual", limit=limit),
            "cashflow_annual", t, max_age_days=_MAX_AGE_STATEMENT_ANNUAL,
        )

    def cashflow_quarterly(self, t: str, limit: int = 21) -> list | dict | None:
        """Quarterly cash flow statements, most recent `limit` quarters."""
        if not self.available:
            return None
        return self.get_json(
            f"{BASE_URL}/cash-flow-statement",
            self._params(symbol=t, period="quarter", limit=limit),
            "cashflow_quarterly", t, max_age_days=_MAX_AGE_STATEMENT_QUARTER,
        )

    def ohlcv_daily(
        self, t: str, years: int = 3, today: date | None = None
    ) -> list | None:
        """Daily EOD bars for the past `years` years, ajustadas de verdad.

        `/stable/historical-price-eod/full` returns a flat list of
        `{symbol, date, open, high, low, close, volume, ...}` (newest
        first). `today` anchors the window and must be supplied by the
        caller so this stays deterministic under test.

        Ese endpoint NO devuelve `adjClose`, y el docstring decia
        "Split/dividend-adjusted" siendo falso a medias. Medido contra la
        respuesta real:

        - Splits: SI vienen ajustados en el cierre crudo. NVDA el
          2024-06-07, tres dias antes de su split 10:1, cotiza a 120,89 y no
          a los ~1.208 que valia. Nunca hubo roturas de serie.
        - Dividendos: NO. KO a un ano daba 70,46 crudo contra 68,53
          ajustado -- un 2,7%, exactamente su dividendo. Eso sesgaba momentum
          y fuerza relativa EN CONTRA de quien paga dividendo.

        Por eso se pide tambien `historical-price-eod/dividend-adjusted` y se
        fusiona su `adjClose` por fecha. Hacen falta las dos series y no
        vale quedarse con la segunda: trae el OHLC ajustado pero el motor
        necesita el CRUDO para gaps y ATR, que se miden sobre el precio al
        que la accion cotizo de verdad.

        Si la segunda peticion falla, se devuelven las barras crudas sin
        `adjClose` y se deja dicho en el log: el consumidor cae al cierre
        crudo, que es peor pero no falso -- lo falso seria callarlo.

        The cache key carries `years`. It used to be a bare
        "ohlcv_daily", so the 1-year MVP fetch (`cli.py`) and the 3-year
        packet fetch (`packet/builder.py`) collided: whichever ran first
        won, and when the short one won the packet was rejected for
        "fewer than 252 daily sessions" even though FMP had served the
        full history.
        """
        if not self.available:
            return None
        if today is None:
            today = date.today()
        from_date = _years_ago(today, years)
        payload = self.get_json(
            f"{BASE_URL}/historical-price-eod/full",
            self._params(symbol=t, **{"from": from_date.isoformat(), "to": today.isoformat()}),
            f"ohlcv_daily_{years}y", t, max_age_days=_MAX_AGE_OHLCV,
        )
        barras = None
        if isinstance(payload, list):
            barras = payload
        elif isinstance(payload, dict):
            # Some plans wrap the series; tolerate both shapes.
            barras = payload.get("historical")
        if not barras:
            return barras

        try:
            ajust = self.get_json(
                f"{BASE_URL}/historical-price-eod/dividend-adjusted",
                self._params(symbol=t, **{"from": from_date.isoformat(),
                                          "to": today.isoformat()}),
                f"ohlcv_adj_{years}y", t, max_age_days=_MAX_AGE_OHLCV,
            )
            filas = ajust if isinstance(ajust, list) else (
                (ajust or {}).get("historical") if isinstance(ajust, dict) else None)
            por_fecha = {r["date"]: r.get("adjClose") for r in (filas or [])
                         if isinstance(r, dict) and r.get("date") is not None}
            if por_fecha:
                puestos = 0
                for b in barras:
                    if not isinstance(b, dict):
                        continue
                    a = por_fecha.get(b.get("date"))
                    if isinstance(a, (int, float)):
                        b["adjClose"] = float(a)
                        puestos += 1
                logger.debug("%s: adjClose fusionado en %d/%d barras",
                             t, puestos, len(barras))
            else:
                logger.warning("%s: sin serie ajustada por dividendos; "
                               "adj_close caera al cierre crudo", t)
        except Exception:                                     # noqa: BLE001
            # Una serie ajustada que no llega no puede costar las barras
            # crudas: se sigue con lo que hay y se dice.
            logger.warning("%s: no se pudo fusionar adjClose", t, exc_info=True)
        return barras

    def peers(self, t: str) -> list | dict | None:
        """Peer tickers for `t`."""
        if not self.available:
            return None
        return self.get_json(
            f"{BASE_URL}/stock-peers",
            self._params(symbol=t),
            "peers", t, max_age_days=_MAX_AGE_REFERENCE,
        )

    def analyst_estimates(self, t: str, limit: int = 10) -> list | dict | None:
        """Analyst revenue/EPS estimates (annual)."""
        if not self.available:
            return None
        return self.get_json(
            f"{BASE_URL}/analyst-estimates",
            self._params(symbol=t, period="annual", limit=limit),
            "analyst_estimates", t, max_age_days=_MAX_AGE_ESTIMATES,
        )

    def insider_trades(self, t: str) -> list | dict | None:
        """SEC Form 4 insider trades, most recent 200."""
        if not self.available:
            return None
        return self.get_json(
            f"{BASE_URL}/insider-trading/search",
            self._params(symbol=t, limit=200),
            "insider_trades", t, max_age_days=_MAX_AGE_REFERENCE,
        )

    def institutional_holders(self, t: str) -> list | dict | None:
        """13F institutional holders (may be plan-restricted → None)."""
        if not self.available:
            return None
        return self.get_json(
            f"{BASE_URL}/institutional-ownership/extract-analytics/holder",
            self._params(symbol=t),
            "institutional_holders", t, max_age_days=_MAX_AGE_REFERENCE,
        )

    def key_executives(self, t: str) -> list | dict | None:
        """Named officers with title and disclosed pay.

        Backs CLAUDE.md's mandatory report item 4 ("does management have a
        track record at other successful companies") — this supplies the
        *who*; the track-record judgement itself is qualitative.
        """
        if not self.available:
            return None
        return self.get_json(
            f"{BASE_URL}/key-executives",
            self._params(symbol=t),
            "key_executives",
            t,
            max_age_days=_MAX_AGE_REFERENCE,
        )

    def revenue_product_segmentation(self, t: str) -> list | dict | None:
        """Reported revenue split by product line, per fiscal year.

        Feeds `overlay["segment_shares"]` (BUS-MIX-001). This is the
        company's own segment disclosure, not an estimate.
        """
        if not self.available:
            return None
        return self.get_json(
            f"{BASE_URL}/revenue-product-segmentation",
            self._params(symbol=t),
            "revenue_product_segmentation", t, max_age_days=_MAX_AGE_STATEMENT,
        )

    def revenue_geographic_segmentation(self, t: str) -> list | dict | None:
        """Reported revenue split by geography, per fiscal year.

        Feeds `overlay["geographic_shares"]` (risk's concentration
        checks). Company disclosure, not an estimate.
        """
        if not self.available:
            return None
        return self.get_json(
            f"{BASE_URL}/revenue-geographic-segmentation",
            self._params(symbol=t),
            "revenue_geographic_segmentation", t, max_age_days=_MAX_AGE_STATEMENT,
        )

    def earnings_calendar(self, t: str) -> list | dict | None:
        """Earnings calendar (actual vs. estimated EPS/revenue)."""
        if not self.available:
            return None
        return self.get_json(
            f"{BASE_URL}/earnings",
            self._params(symbol=t, limit=40),
            "earnings_calendar", t, max_age_days=_MAX_AGE_REFERENCE,
        )
