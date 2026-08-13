"""Amplitud de sector: cuántos de sus miembros van por encima de su media móvil.

`MKT-SECB-023` = miembros sobre la media de 50 sesiones / miembros válidos del
sector, con frecuencia **diaria** y la salvedad "point-in-time constituent
control required". Alimenta la dimensión de apalancamiento operativo y
confirmación de mercado (3 puntos), donde `SCORING.md` la describe como
"healthy sector participation".

**Por qué antes no se calculaba, y por qué ahora sí.** El código la declinaba
razonando que el screener de FMP devuelve la composición de HOY, y que aplicarla
a una ventana de 252 sesiones mediría sólo a las empresas que sobrevivieron
hasta hoy — sesgo de supervivencia de manual. Ese razonamiento es correcto para
lo que describe, pero describe otra métrica: aquí no hay ventana de 252
sesiones. La amplitud es una foto de HOY, y para una foto de hoy la composición
de hoy *es* el point-in-time que la salvedad pide. Lo que estaría prohibido es
usar el roster de hoy para calcular la amplitud de hace un año, y eso no se
hace en ninguna parte.

**El universo se declara, no se supone.** "El sector" no es un conjunto
evidente: hay miles de cotizadas y la mitad no las negocia nadie. Aquí es
NASDAQ y NYSE, sin ETF ni fondos, capitalización sobre $2.000M. La cifra viaja
con ese universo escrito al lado, porque una amplitud del 52% significa cosas
distintas sobre 400 empresas líquidas que sobre 4.000 incluyendo microcaps.

**Miembro válido** es el que tiene media de 50 sesiones publicada. Uno recién
listado no la tiene, y contarlo en el denominador como "no está por encima"
diría que el sector está más débil de lo que está.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

BASE = "https://financialmodelingprep.com/stable"

#: El universo, declarado. Ver el docstring: sin declararlo, el número no
#: significa nada.
CAP_MINIMA = 2_000_000_000
BOLSAS = "NASDAQ,NYSE"

#: Cuántas cotizaciones se piden a la vez. Medido: 60 en 1,6 s con 12 hilos,
#: así que un sector de 405 miembros sale en ~11 s. Se paga una vez por sector
#: y por día — el resultado lo comparte todo ticker de ese sector.
HILOS = 4

#: Cotizaciones seguidas sin respuesta antes de abandonar el sector. Existe
#: porque esto corre por red durante el analisis: si el proveedor limita, cada
#: peticion perdida se resta de la cuota del ticker que se esta analizando.
FALLOS_SEGUIDOS_MAXIMOS = 3

#: Qué fracción del universo tiene que contestar para que el número valga.
#: Medido el 2026-08-06: corriendo cuatro sectores seguidos, Financial Services
#: devolvió 85 cotizaciones utiles de 458 miembros -- el resto se perdio contra
#: el limite de tasa de FMP. Ese 85/458 daba una amplitud del 85%, pero no era
#: la del sector: era la de quienes alcanzaron a contestar, y los que contestan
#: primero no son una muestra aleatoria. Sin este piso, un fallo de red se
#: convierte en un dato de aspecto perfectamente normal.
RESPUESTA_MINIMA = 0.60

#: Tope de miembros, y la razón por la que bajó de 600 a 120.
#:
#: Con 600, Technology disparaba 405 peticiones y agotaba el limite de FMP.
#: Eso no solo tiraba la amplitud -- medido en produccion, se descarto por
#: llegar al 57% de respuesta -- sino que ENVENENABA EL ANALISIS: las llamadas
#: propias del ticker, su estado de flujo de caja y su historico de precios,
#: salian 429 detras de la tormenta. Una metrica de contexto de 3 puntos
#: estaba tumbando las seis categorias.
#:
#: 120 mayores por capitalizacion no es un recorte por rendimiento: es como se
#: construye cualquier indice sectorial, con sus constituyentes grandes. Se
#: declara en la salida, que es lo que permite leer el numero.
TOPE_MIEMBROS = 120


def _slug(sector: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in (sector or "").lower()).strip("-")


def _pedir(fmp: Any, ruta: str, params: dict, clave: str, ticker: str,
           dias: int) -> Any:
    try:
        return fmp.get_json(f"{BASE}/{ruta}", params, clave, ticker, max_age_days=dias)
    except Exception:  # noqa: BLE001 — sin amplitud la métrica queda NOT_SCORABLE
        logger.info("FMP no respondio en %s", ruta, exc_info=True)
        return None


def _miembros(fmp: Any, sector: str) -> list[str]:
    filas = _pedir(fmp, "company-screener", {
        "sector": sector, "exchange": BOLSAS, "isEtf": "false", "isFund": "false",
        "marketCapMoreThan": CAP_MINIMA, "limit": 1000,
        "apikey": fmp.settings.fmp_api_key,
    }, f"screener_{_slug(sector)}", "_sector", 1)
    if not isinstance(filas, list):
        return []
    conocidas = [f for f in filas
                 if isinstance(f, dict) and f.get("symbol") and f.get("marketCap")]
    conocidas.sort(key=lambda f: -float(f["marketCap"]))
    return [f["symbol"] for f in conocidas[:TOPE_MIEMBROS]]


def amplitud_de_sector(fmp: Any, sector: str | None, hoy: date | None = None,
                       permitir_red: bool = True) -> dict[str, Any] | None:
    """Los conteos que `MKT-SECB-023` necesita, o `None` si no se pudieron medir.

    `permitir_red=False` devuelve lo que haya en cache y NADA MAS. Es como se
    llama desde el analisis, y la razon es la unica que importa: esta metrica
    vale 3 de los 100 puntos del sistema, y sus cientos de peticiones estaban
    agotando el limite de FMP con el que se pagan las otras 97. Un numero de
    contexto no puede competir por cuota con los datos del ticker que se esta
    analizando.

    Nunca levanta: una amplitud que no se pudo calcular deja la métrica
    NOT_SCORABLE, que es la respuesta honesta, no un análisis roto.
    """
    if not sector or not getattr(fmp, "available", False):
        return None
    hoy = hoy or datetime.now(timezone.utc).date()
    cache = getattr(fmp, "cache", None)
    clave = f"breadth_{_slug(sector)}_{hoy.isoformat()}"

    # Se cachea el RESULTADO, no sólo cada cotización: son cientos de
    # peticiones y el número es idéntico para todos los tickers del sector.
    if cache is not None:
        previo = cache.get("_sector", clave)
        if isinstance(previo, dict) and previo.get("valid_members"):
            return previo

    if not permitir_red:
        return None

    simbolos = _miembros(fmp, sector)
    if len(simbolos) < 20:
        # Un sector con un puñado de miembros no produce una amplitud que
        # signifique nada: el ruido de dos o tres empresas la mueve entera.
        logger.info("amplitud de %s no calculada: solo %d miembros",
                    sector, len(simbolos))
        return None

    def _quote(s: str) -> dict | None:
        r = _pedir(fmp, "quote", {"symbol": s, "apikey": fmp.settings.fmp_api_key},
                   f"quote_{s}", s, 1)
        return r[0] if isinstance(r, list) and r and isinstance(r[0], dict) else None

    # Cortacircuitos. Ahora que esto corre por red DURANTE el analisis, un
    # proveedor que empieza a limitar no puede seguir recibiendo 120
    # peticiones: las que fallan aqui gastan la cuota con la que se pagan las
    # del propio ticker, y ese fue exactamente el fallo que obligo a apagar la
    # red en su dia -- los 429 de la amplitud caian sobre NVDA.
    #
    # Tres fallos seguidos y se abandona. La amplitud queda NOT_SCORABLE, que
    # cuesta 3 puntos; seguir insistiendo costaba el analisis entero.
    _fallos = {"seguidos": 0}

    def _quote_con_reintento(s: str) -> dict | None:
        if _fallos["seguidos"] >= FALLOS_SEGUIDOS_MAXIMOS:
            return None
        # Un reintento, y espaciado: el limite de FMP es por minuto, asi que
        # insistir en el acto solo gasta la siguiente ranura.
        q = _quote(s)
        if q is None:
            time.sleep(0.4)
            q = _quote(s)
        if q is None:
            _fallos["seguidos"] += 1
            if _fallos["seguidos"] == FALLOS_SEGUIDOS_MAXIMOS:
                logger.warning("amplitud abandonada: %d cotizaciones seguidas "
                               "sin respuesta; no se gasta mas cuota",
                               FALLOS_SEGUIDOS_MAXIMOS)
        else:
            _fallos["seguidos"] = 0
        return q

    with ThreadPoolExecutor(max_workers=HILOS) as pool:
        quotes = list(pool.map(_quote_con_reintento, simbolos))

    sobre50 = sobre200 = validos = validos200 = 0
    for q in quotes:
        if not q:
            continue
        precio = q.get("price")
        m50, m200 = q.get("priceAvg50"), q.get("priceAvg200")
        if not isinstance(precio, (int, float)) or precio <= 0:
            continue
        # Válido = tiene la media publicada. Un recién listado no la tiene, y
        # contarlo abajo como "no supera su media" diría que el sector está
        # más débil de lo que está.
        if isinstance(m50, (int, float)) and m50 > 0:
            validos += 1
            sobre50 += 1 if precio > m50 else 0
        if isinstance(m200, (int, float)) and m200 > 0:
            validos200 += 1
            sobre200 += 1 if precio > m200 else 0

    if validos < 20:
        return None
    respondieron = validos / len(simbolos)
    if respondieron < RESPUESTA_MINIMA:
        logger.warning(
            "amplitud de %s descartada: solo %d de %d miembros (%.0f%%) "
            "devolvieron media de 50. Por debajo del %.0f%% el numero mide a "
            "quien contesto, no al sector.",
            sector, validos, len(simbolos), respondieron * 100,
            RESPUESTA_MINIMA * 100)
        return None

    salida = {
        "above_50dma_count": sobre50,
        "valid_members": validos,
        # `FORMULAS.md` MKT-SECB-023: "Also report members above 200DMA".
        "above_200dma_count": sobre200,
        "valid_members_200dma": validos200,
        "_universo": (f"{BOLSAS}, sin ETF ni fondos, capitalizacion sobre "
                      f"${CAP_MINIMA:,}. {len(simbolos)} miembros consultados, "
                      f"{validos} con media de 50 publicada."),
        "_fecha": hoy.isoformat(),
        "_point_in_time": ("Composicion de HOY para una medicion de HOY, que es "
                           "lo que pide la salvedad de la formula. Usar este "
                           "roster para calcular la amplitud de una fecha "
                           "pasada si seria sesgo de supervivencia, y no se hace."),
    }
    if len(simbolos) >= TOPE_MIEMBROS:
        salida["_recorte"] = (f"El sector supera los {TOPE_MIEMBROS} miembros del "
                              "tope; se midieron los mayores por capitalizacion.")
    if cache is not None:
        try:
            cache.put("_sector", clave, salida)
        except OSError:
            pass
    return salida
