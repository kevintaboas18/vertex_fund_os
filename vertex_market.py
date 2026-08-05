"""Datos de mercado desde las fuentes PRINCIPALES del sistema.

Victor fijó cuatro fuentes: **FMP, FinnHub, FRED y EDGAR**. Su repo no
depende de yfinance en ninguna parte — sus proveedores son exactamente
`edgar`, `finnhub`, `fmp`, `fred`, y su `pyproject.toml` no lo declara.

Este módulo reemplaza `yfinance.Ticker` con la misma forma pero alimentado
por esas fuentes, para que los ~45 sitios de `vertex_api.py` que lo usaban
no tengan que reescribirse uno a uno — que en un archivo de 13.500 líneas es
como se rompen cosas.

**Por qué se sustituye y no se conserva.** yfinance raspa un endpoint que
nadie documenta: puede cambiar de forma sin aviso y sin versión. Un dato así
alimentando un panel de precio es un riesgo aceptable; alimentando un
análisis de inversión, no.

Cobertura verificada contra la clave de Victor el 2026-08-03:

| Necesidad | Endpoint | Estado |
|---|---|---|
| velas diarias | `historical-price-eod/full` | 200 |
| velas 1h / 5m | `historical-chart/{1hour,5min}` | 200 |
| cotización | `quote` | 200 |
| ficha de empresa | `profile` | 200 |
| múltiplos y márgenes | `ratios-ttm` | 200 |
| precio objetivo | `price-target-consensus` | 200 |
| recomendación | `grades-consensus` | 200 |
| noticias | `news/stock` | 200 |
| insiders | `insider-trading/search` | 200 |

Lo ÚNICO sin sustituto son las cadenas de opciones: `options-chain` y
`options/contracts` dan 404 en este plan. Por eso `Ticker` no expone
`option_chain` ni `options` — las rutas que dependían de ellas se
eliminaron en vez de fingir que hay datos.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

logger = logging.getLogger(__name__)

_BASE = "https://financialmodelingprep.com/stable"
_TIMEOUT = 20.0

#: Caché en memoria por (símbolo, tipo). El histórico diario y la ficha de
#: empresa cambian poco; la cotización, cada minuto. Sin esto, una pantalla
#: que pinta ocho paneles dispara ocho veces la misma llamada.
_TTL = {"quote": 60.0, "profile": 3600.0, "ratios": 3600.0, "targets": 3600.0,
        "grades": 3600.0, "news": 900.0, "insiders": 3600.0, "estimates": 3600.0}
_TTL_HISTORIA = 300.0

_CACHE: dict[tuple, tuple[float, object]] = {}
_LOCK = threading.Lock()

#: `period` de yfinance -> días naturales.
_DIAS = {"1d": 1, "5d": 5, "1mo": 31, "3mo": 93, "6mo": 186, "1y": 366,
         "2y": 731, "5y": 1827, "10y": 3653, "ytd": 366, "max": 3653}

#: `interval` de yfinance -> ruta intradía de FMP. `None` = velas diarias.
_INTRADIA = {"1m": "1min", "5m": "5min", "10m": "5min", "15m": "15min",
             "30m": "30min", "60m": "1hour", "1h": "1hour", "90m": "1hour"}


def _clave() -> str:
    return (os.environ.get("FMP_API_KEY") or "").strip()


def _get(ruta: str, params: dict, tipo: str, simbolo: str):
    """GET cacheado contra FMP. Devuelve None ante cualquier fallo.

    Nunca lanza: esto alimenta paneles, y un panel vacío es mejor que una
    pantalla en blanco por una excepción que nadie atrapó.
    """
    k = _clave()
    if not k:
        return None
    ck = (simbolo, tipo, ruta, tuple(sorted(params.items())))
    ahora = time.time()
    with _LOCK:
        ent = _CACHE.get(ck)
        if ent and ahora - ent[0] < _TTL.get(tipo, 300.0):
            return ent[1]
    try:
        r = requests.get(f"{_BASE}/{ruta}", params={**params, "apikey": k},
                         timeout=_TIMEOUT)
        if r.status_code >= 400:
            logger.info("FMP %s -> HTTP %d (%s)", ruta, r.status_code, simbolo)
            return None
        datos = r.json()
    except Exception:
        logger.warning("FMP %s falló para %s", ruta, simbolo, exc_info=True)
        return None
    with _LOCK:
        _CACHE[ck] = (ahora, datos)
        if len(_CACHE) > 4000:                      # cota de memoria
            for viejo in sorted(_CACHE, key=lambda c: _CACHE[c][0])[:800]:
                _CACHE.pop(viejo, None)
    return datos


_FINNHUB_BASE = "https://finnhub.io/api/v1"


def _finnhub(ruta: str, params: dict):
    """GET a FinnHub, el SEGUNDO escalón de la cadena. None ante cualquier
    fallo — quien llama tiene EDGAR detrás.

    Sin caché propia: hoy las rutas de propiedad institucional responden 403
    (tier de pago) y no llega a haber payload que guardar. Cuando el plan las
    cubra, se le añade el mismo `_TTL` que usa `_get`.
    """
    token = (os.environ.get("FINNHUB_API_KEY") or "").strip()
    if not token:
        return None
    try:
        r = requests.get(f"{_FINNHUB_BASE}/{ruta}",
                         params={**params, "token": token}, timeout=_TIMEOUT)
        if r.status_code >= 400:
            logger.info("FinnHub %s -> HTTP %d", ruta, r.status_code)
            return None
        return r.json()
    except Exception:
        logger.warning("FinnHub %s falló", ruta, exc_info=True)
        return None


def _primera(datos) -> dict:
    if isinstance(datos, list) and datos and isinstance(datos[0], dict):
        return datos[0]
    return datos if isinstance(datos, dict) else {}


def _num(d: dict, *claves):
    for c in claves:
        v = d.get(c)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return None


class _FastInfo(dict):
    """`fast_info` de yfinance se usa como dict Y por atributo."""

    def __getattr__(self, nombre):
        try:
            return self[nombre]
        except KeyError as exc:
            raise AttributeError(nombre) from exc


class Ticker:
    """Mismo contrato que `yfinance.Ticker`, con datos de FMP/FinnHub.

    No expone `option_chain` ni `options`: FMP no sirve cadenas de opciones
    en este plan (404) y fingir que sí produciría paneles con datos
    inventados. Los llamadores se eliminaron.
    """

    def __init__(self, symbol: str) -> None:
        self.symbol = str(symbol or "").upper().strip()
        self.ticker = self.symbol

    # ---------------------------------------------------------------- precios

    def history(self, period: str = "1y", interval: str = "1d",
                **_kw) -> pd.DataFrame:
        """OHLCV con la forma que espera el código: índice de fechas y
        columnas `Open/High/Low/Close/Volume`.

        Diario desde `historical-price-eod/full` (ajustado por splits y
        dividendos); intradía desde `historical-chart/<intervalo>`. Antes el
        intradía sólo lo daba Yahoo y la ruta se quedaba sin red — ahora las
        dos vías salen de la misma fuente.
        """
        dias = _DIAS.get(str(period), 366)
        ruta_intra = _INTRADIA.get(str(interval))
        ck = (self.symbol, "hist", period, interval)
        ahora = time.time()
        with _LOCK:
            ent = _CACHE.get(ck)
            if ent and ahora - ent[0] < _TTL_HISTORIA:
                return ent[1].copy()

        desde = (datetime.now(timezone.utc) - timedelta(days=max(dias, 1))).date()
        if ruta_intra:
            filas = _get(f"historical-chart/{ruta_intra}",
                         {"symbol": self.symbol, "from": desde.isoformat()},
                         "hist", self.symbol)
        else:
            filas = _get("historical-price-eod/full",
                         {"symbol": self.symbol, "from": desde.isoformat()},
                         "hist", self.symbol)
        df = _a_dataframe(filas)
        with _LOCK:
            _CACHE[ck] = (ahora, df)
        return df.copy()

    # ------------------------------------------------------------ ficha/quote

    @property
    def fast_info(self) -> _FastInfo:
        q = _primera(_get("quote", {"symbol": self.symbol}, "quote", self.symbol))
        precio = _num(q, "price")
        return _FastInfo({
            "last_price": precio, "lastPrice": precio, "price": precio,
            "previous_close": _num(q, "previousClose"),
            "previousClose": _num(q, "previousClose"),
            "day_high": _num(q, "dayHigh"), "dayHigh": _num(q, "dayHigh"),
            "day_low": _num(q, "dayLow"), "dayLow": _num(q, "dayLow"),
            "open": _num(q, "open"),
            "last_volume": _num(q, "volume"), "regularMarketVolume": _num(q, "volume"),
            "market_cap": _num(q, "marketCap"), "marketCap": _num(q, "marketCap"),
            "year_high": _num(q, "yearHigh"), "year_low": _num(q, "yearLow"),
            "currency": "USD", "exchange": q.get("exchange"),
        })

    @property
    def info(self) -> dict:
        """Las claves de `.info` que el código lee de verdad.

        Las que ninguna fuente principal publica (`marketState`,
        `preMarketPrice`, `postMarketPrice`, `vwap`) quedan en `None` a
        propósito: el llamador ya trata la ausencia, y rellenarlas con el
        cierre anterior sería inventar un dato de sesión.
        """
        q = _primera(_get("quote", {"symbol": self.symbol}, "quote", self.symbol))
        p = _primera(_get("profile", {"symbol": self.symbol}, "profile", self.symbol))
        r = _primera(_get("ratios-ttm", {"symbol": self.symbol}, "ratios", self.symbol))
        t = _primera(_get("price-target-consensus", {"symbol": self.symbol},
                          "targets", self.symbol))
        g = _primera(_get("grades-consensus", {"symbol": self.symbol},
                          "grades", self.symbol))
        precio = _num(q, "price") or _num(p, "price")
        eps_fwd = self._eps_forward()
        votos = [int(g.get(k) or 0) for k in
                 ("strongBuy", "buy", "hold", "sell", "strongSell")]

        return {
            "longName": p.get("companyName") or q.get("name") or self.symbol,
            "shortName": q.get("name") or p.get("companyName") or self.symbol,
            "website": p.get("website"),
            "sector": p.get("sector"),
            "industry": p.get("industry"),
            "country": p.get("country"),
            "currency": p.get("currency") or "USD",
            "exchange": q.get("exchange") or p.get("exchange"),
            "longBusinessSummary": p.get("description"),
            "beta": _num(p, "beta"),
            "marketCap": _num(q, "marketCap") or _num(p, "marketCap"),
            "currentPrice": precio,
            "regularMarketPrice": precio,
            "price": precio,
            "lastPrice": precio,
            "previousClose": _num(q, "previousClose"),
            "regularMarketPreviousClose": _num(q, "previousClose"),
            "dayHigh": _num(q, "dayHigh"),
            "dayLow": _num(q, "dayLow"),
            "regularMarketVolume": _num(q, "volume"),
            "fiftyTwoWeekHigh": _num(q, "yearHigh"),
            "fiftyTwoWeekLow": _num(q, "yearLow"),
            "trailingPE": _num(r, "priceToEarningsRatioTTM"),
            "trailingEps": _num(r, "netIncomePerShareTTM"),
            "forwardEps": eps_fwd,
            "forwardPE": (precio / eps_fwd) if (precio and eps_fwd and eps_fwd > 0) else None,
            "grossMargins": _num(r, "grossProfitMarginTTM"),
            "ebitdaMargins": _num(r, "ebitdaMarginTTM"),
            "operatingMargins": _num(r, "operatingProfitMarginTTM"),
            "targetMeanPrice": _num(t, "targetConsensus"),
            "targetMedianPrice": _num(t, "targetMedian"),
            "targetHighPrice": _num(t, "targetHigh"),
            "targetLowPrice": _num(t, "targetLow"),
            "numberOfAnalystOpinions": (sum(votos) or None),
            "recommendationKey": _recomendacion(votos),
            "companyOfficers": self._directivos(),
            # Sin fuente principal: la ausencia se declara, no se rellena.
            "marketState": None, "preMarketPrice": None,
            "postMarketPrice": None, "vwap": None,
        }

    def _eps_forward(self):
        filas = _get("analyst-estimates",
                     {"symbol": self.symbol, "period": "annual", "limit": 10},
                     "estimates", self.symbol)
        if not isinstance(filas, list):
            return None
        hoy = datetime.now(timezone.utc).date().isoformat()
        fwd = sorted((f for f in filas
                      if isinstance(f, dict) and f.get("date")
                      and f["date"] > hoy and f.get("epsAvg")),
                     key=lambda f: f["date"])
        return float(fwd[0]["epsAvg"]) if fwd else None

    def _directivos(self):
        filas = _get("key-executives", {"symbol": self.symbol}, "profile", self.symbol)
        if not isinstance(filas, list):
            return []
        return [{"name": f.get("name"), "title": f.get("title"),
                 "totalPay": f.get("pay"), "yearBorn": f.get("yearBorn")}
                for f in filas if isinstance(f, dict)]

    # ------------------------------------------------------------------ otros

    @property
    def news(self) -> list:
        filas = _get("news/stock", {"symbols": self.symbol, "limit": 30},
                     "news", self.symbol)
        if not isinstance(filas, list):
            return []
        salida = []
        for f in filas:
            if not isinstance(f, dict):
                continue
            salida.append({
                "title": f.get("title"),
                "link": f.get("url"),
                "publisher": f.get("publisher") or f.get("site"),
                "providerPublishTime": _epoch(f.get("publishedDate")),
                "summary": f.get("text"),
                # yfinance anida bajo "content"; hay llamadores que lo leen así.
                "content": {"title": f.get("title"),
                            "summary": f.get("text"),
                            "canonicalUrl": {"url": f.get("url")}},
            })
        return salida

    @property
    def insider_transactions(self):
        filas = _get("insider-trading/search", {"symbol": self.symbol, "limit": 100},
                     "insiders", self.symbol)
        if not isinstance(filas, list) or not filas:
            return None
        return pd.DataFrame([{
            "Insider": f.get("reportingName"),
            "Position": f.get("typeOfOwner"),
            "Start Date": f.get("transactionDate"),
            "Transaction": f.get("transactionType"),
            "Shares": f.get("securitiesTransacted"),
            "Value": (float(f.get("securitiesTransacted") or 0)
                      * float(f.get("price") or 0)) or None,
        } for f in filas if isinstance(f, dict)])

    @property
    def institutional_holders(self):
        """Tenedores 13F: **FMP → FinnHub**, y si ninguno, `None` para que
        el llamador caiga a EDGAR.

        La cadena es literal a propósito, aunque hoy los dos primeros fallen:
        el día que suba de plan cualquiera de los dos, esto empieza a
        funcionar sin tocar una línea.

        Estado medido contra las claves actuales:

        | Fuente | Endpoint | |
        |---|---|---|
        | FMP | `institutional-ownership/extract-analytics/holder` | **402** |
        | FinnHub | `stock/fund-ownership` | **403** |
        | EDGAR | conjunto trimestral 13F | **funciona** |

        El 402 es respuesta PROPIA de `financialmodelingprep.com` sobre el
        plan — verificado con una petición directa y cero módulos de
        yfinance cargados. No lo provoca ninguna otra librería.
        """
        filas = _get("institutional-ownership/extract-analytics/holder",
                     {"symbol": self.symbol}, "insiders", self.symbol)
        if isinstance(filas, list) and filas:
            return pd.DataFrame([{
                "Holder": f.get("investorName") or f.get("holder"),
                "Shares": f.get("sharesNumber") or f.get("shares"),
                "Value": f.get("marketValue") or f.get("value"),
                "pctHeld": f.get("weight"),
                "Date Reported": f.get("date") or f.get("dateReported"),
            } for f in filas if isinstance(f, dict)])

        fh = _finnhub("stock/fund-ownership", {"symbol": self.symbol, "limit": 20})
        propietarios = (fh or {}).get("ownership") if isinstance(fh, dict) else None
        if isinstance(propietarios, list) and propietarios:
            return pd.DataFrame([{
                "Holder": f.get("name"),
                "Shares": f.get("share"),
                "Value": None,          # FinnHub no publica el valor en dólares
                "pctHeld": f.get("portfolioPercent"),
                "Date Reported": f.get("filingDate"),
            } for f in propietarios if isinstance(f, dict)])

        # Ninguno de los dos: el llamador tiene EDGAR como tercer escalón.
        return None

    @property
    def major_holders(self):
        """El reparto insiders/instituciones no lo publica ninguna de las dos
        fuentes principales, y EDGAR sólo da posiciones absolutas. Se declara
        la ausencia en vez de derivarla de un total incompleto."""
        return None

    @property
    def calendar(self) -> dict:
        prox = self._proximos_resultados()
        return {"Earnings Date": [prox] if prox else []}

    def get_earnings_dates(self, limit: int = 8):
        filas = _get("earnings", {"symbol": self.symbol, "limit": max(int(limit), 1)},
                     "estimates", self.symbol)
        if not isinstance(filas, list) or not filas:
            return None
        df = pd.DataFrame([{
            "EPS Estimate": f.get("epsEstimated"),
            "Reported EPS": f.get("epsActual"),
        } for f in filas if isinstance(f, dict)])
        df.index = pd.to_datetime([f.get("date") for f in filas
                                   if isinstance(f, dict)], errors="coerce")
        return df

    def _proximos_resultados(self):
        filas = _get("earnings", {"symbol": self.symbol, "limit": 10},
                     "estimates", self.symbol)
        if not isinstance(filas, list):
            return None
        hoy = datetime.now(timezone.utc).date()
        futuras = sorted(
            (f["date"] for f in filas
             if isinstance(f, dict) and f.get("date")
             and _fecha(f["date"]) and _fecha(f["date"]) >= hoy))
        return _fecha(futuras[0]) if futuras else None


def _recomendacion(votos: list[int]) -> str | None:
    """La etiqueta de consenso, del reparto de recomendaciones de FMP."""
    sb, b, h, s, ss = votos
    total = sum(votos)
    if not total:
        return None
    compra, venta = sb + b, s + ss
    if compra / total >= 0.7:
        return "strong_buy" if sb >= b else "buy"
    if venta / total >= 0.4:
        return "sell"
    if compra > venta:
        return "buy"
    return "hold"


def _fecha(valor):
    try:
        return datetime.strptime(str(valor)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _epoch(valor):
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return int(datetime.strptime(str(valor)[:19], fmt)
                       .replace(tzinfo=timezone.utc).timestamp())
        except (TypeError, ValueError):
            continue
    return None


def _a_dataframe(filas) -> pd.DataFrame:
    """Filas de FMP -> el DataFrame que espera el código.

    FMP entrega de más nuevo a más viejo; aquí sale ascendente, que es el
    orden en que yfinance lo daba y del que dependen los cálculos de
    ventana móvil.
    """
    vacio = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
    if not isinstance(filas, list) or not filas:
        return vacio
    reg, idx = [], []
    for f in filas:
        if not isinstance(f, dict) or f.get("close") is None:
            continue
        try:
            cierre = float(f["close"])
        except (TypeError, ValueError):
            continue
        idx.append(str(f.get("date")))
        reg.append({
            "Open": _num(f, "open") if _num(f, "open") is not None else cierre,
            "High": _num(f, "high") if _num(f, "high") is not None else cierre,
            "Low": _num(f, "low") if _num(f, "low") is not None else cierre,
            "Close": cierre,
            "Volume": _num(f, "volume") or 0.0,
        })
    if not reg:
        return vacio
    df = pd.DataFrame(reg, index=pd.to_datetime(idx, errors="coerce"))
    df = df[df.index.notna()].sort_index()
    return df
