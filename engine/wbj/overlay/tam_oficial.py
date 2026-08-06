"""El TAM sale de fuentes oficiales, no de buscar en la web.

La versión anterior de este módulo le preguntaba a Gemini con búsqueda de
Google. Funcionaba, y estaba mal: Google no es una de las fuentes de este
sistema, y lo que devolvía no era el dato sino el *comunicado de prensa* sobre
el dato — porque IDC, Omdia y Gartner venden sus informes. Se estaba citando
la nota que resume un número que nadie de aquí puede abrir.

Aquí el tamaño del mercado se **descarga**, por una cadena en la que cada
eslabón es oficial y verificable:

    ticker → CIK → EDGAR (código SIC de la SEC) → NAICS → FRED (Census/BLS)

- **EDGAR** publica el SIC de cada emisor en `submissions/CIK*.json`. No es una
  clasificación de un proveedor: es la que la propia empresa declara ante la
  SEC.
- **FRED** sirve las encuestas del Census Bureau —Quarterly Services Survey,
  Annual Survey of Manufactures— y la producción sectorial del BLS. Son
  ingresos de industria medidos por el gobierno de EE.UU.: tier 1, la fuente
  más alta que reconoce `SOURCE_HIERARCHY.md`. Y a diferencia de un comunicado
  de prensa, tienen serie histórica, revisiones publicadas y API.

**La limitación, dicha de frente.** Las series del Census miden EE.UU. Los
ingresos de un emisor son mundiales. Dividir uno entre otro compara ámbitos
distintos, que es el mismo error de capa que tuvo NVDA con Gartner, sólo que
en el eje geográfico. Por eso:

  1. cada TAM se guarda con `_geografia` escrito,
  2. si la participación implícita pasa del 100%, se rechaza — el denominador
     no era el de esa empresa,
  3. y una industria sin serie oficial se queda SIN TAM, con el SIC anotado
     para poder añadirla. Un hueco se ve; un denominador equivocado no.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

FRED_BASE = "https://api.stlouisfed.org/fred"

# Cadencia trimestral, como los filings: el Census publica la Quarterly
# Services Survey por trimestre, así que preguntar más a menudo devuelve la
# misma cifra.
VIGENCIA_DIAS = 90

# SIC (lo que la empresa declara ante la SEC) → NAICS (con lo que el Census
# publica). Las dos son clasificaciones oficiales; esto es el puente entre
# ellas, y está escrito a mano a propósito: adivinarlo con coincidencia de
# texto es exactamente como se acaba dividiendo entre el mercado equivocado.
#
# Lo que no esté en esta tabla se queda sin TAM, con su SIC anotado en el
# archivo de industria para que añadirlo sea una línea.
SIC_A_NAICS: dict[str, tuple[str, str]] = {
    # -- manufactura --
    "3674": ("3344", "Semiconductor and Other Electronic Component Manufacturing"),
    "3663": ("3342", "Communications Equipment Manufacturing"),
    "3661": ("3342", "Communications Equipment Manufacturing"),
    "3571": ("3341", "Computer and Peripheral Equipment Manufacturing"),
    "3572": ("3341", "Computer and Peripheral Equipment Manufacturing"),
    "3576": ("3341", "Computer and Peripheral Equipment Manufacturing"),
    "3826": ("3345", "Navigational, Measuring, Electromedical and Control Instruments"),
    "3827": ("3345", "Navigational, Measuring, Electromedical and Control Instruments"),
    "2080": ("3121", "Beverage Manufacturing"),
    "2082": ("3121", "Beverage Manufacturing"),
    "2086": ("3121", "Beverage Manufacturing"),
    "2090": ("3119", "Other Food Manufacturing"),
    "2834": ("3254", "Pharmaceutical and Medicine Manufacturing"),
    "2836": ("3254", "Pharmaceutical and Medicine Manufacturing"),
    "2821": ("3252", "Resin, Synthetic Rubber, and Artificial Fibers"),
    "2911": ("3241", "Petroleum and Coal Products Manufacturing"),
    "3312": ("3311", "Iron and Steel Mills and Ferroalloy Manufacturing"),
    "3711": ("3361", "Motor Vehicle Manufacturing"),
    "3721": ("3364", "Aerospace Product and Parts Manufacturing"),
    "3841": ("3391", "Medical Equipment and Supplies Manufacturing"),
    "3845": ("3391", "Medical Equipment and Supplies Manufacturing"),
    # -- servicios (Quarterly Services Survey: la mejor cobertura de FRED) --
    "7372": ("5112", "Software Publishers"),
    "7370": ("5415", "Computer Systems Design and Related Services"),
    "7371": ("5415", "Computer Systems Design and Related Services"),
    "7373": ("5415", "Computer Systems Design and Related Services"),
    "7374": ("5182", "Data Processing, Hosting, and Related Services"),
    "4813": ("5173", "Wired and Wireless Telecommunications Carriers"),
    "4812": ("5173", "Wired and Wireless Telecommunications Carriers"),
    "4899": ("5174", "Satellite Telecommunications"),
    "7812": ("5121", "Motion Picture and Video Industries"),
    "7900": ("7139", "Other Amusement and Recreation Industries"),
    "8000": ("6221", "General Medical and Surgical Hospitals"),
    "8060": ("6221", "General Medical and Surgical Hospitals"),
    "8742": ("5416", "Management, Scientific, and Technical Consulting Services"),
    # -- finanzas --
    "6021": ("5221", "Depository Credit Intermediation"),
    "6022": ("5221", "Depository Credit Intermediation"),
    "6020": ("5221", "Depository Credit Intermediation"),
    "6141": ("5222", "Nondepository Credit Intermediation"),
    "6199": ("5223", "Activities Related to Credit Intermediation"),
    "6211": ("5231", "Securities and Commodity Contracts Intermediation"),
    "6282": ("5239", "Other Financial Investment Activities"),
    "6311": ("5241", "Insurance Carriers"),
    "6321": ("5241", "Insurance Carriers"),
    "6331": ("5241", "Insurance Carriers"),
    # -- comercio --
    "5411": ("4451", "Grocery Stores"),
    "5912": ("4461", "Health and Personal Care Stores"),
    "5311": ("4522", "Department Stores"),
    "5961": ("4541", "Electronic Shopping and Mail-Order Houses"),
    "5812": ("7225", "Restaurants and Other Eating Places"),
    # -- transporte y energía --
    "4011": ("4821", "Rail Transportation"),
    "4512": ("4811", "Scheduled Air Transportation"),
    "4213": ("4841", "General Freight Trucking"),
    "1311": ("2111", "Oil and Gas Extraction"),
    "4911": ("2211", "Electric Power Generation, Transmission and Distribution"),
}


def _numero(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    try:
        n = float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _pedir(url: str, params: dict) -> dict | None:
    """GET contra FRED. Devuelve `None` en vez de levantar: un TAM que no se
    pudo descargar deja la dimensión NOT_SCORABLE, no rompe el análisis."""
    import urllib.error
    import urllib.parse
    import urllib.request

    try:
        q = urllib.parse.urlencode(params)
        with urllib.request.urlopen(f"{url}?{q}", timeout=45) as r:
            return json.loads(r.read())
    except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError) as e:
        logger.info("FRED no respondio (%s): %s", url.rsplit("/", 1)[-1],
                    type(e).__name__)
        return None


def _dolares(units: str) -> float | None:
    """El multiplicador para llevar la serie a dólares.

    FRED publica la escala en el propio metadato (`Mil. of $`, `Bil. of $`).
    Ignorarla es la forma más silenciosa de equivocarse por mil: la serie de
    software publisher marca 169.800 y son $169.800 millones, no $169.800.
    """
    u = (units or "").lower()
    if "bil" in u:
        return 1e9
    if "mil" in u:
        return 1e6
    if "thous" in u:
        return 1e3
    if u.strip() in ("$", "dollars", "u.s. $", "us $"):
        return 1.0
    return None


def _serie_de_la_industria(key: str, naics: str, nombre: str) -> dict | None:
    """La serie de FRED que mide el tamaño de ESTA industria.

    La elección no se deja al azar del buscador: se exige que el código NAICS
    aparezca en el identificador o en el título de la serie. Sin esa comproba-
    ción, "Total Revenue for Software Publishers" y "Total Revenue for Other
    Information Services" son igual de plausibles para un buscador de texto, y
    una de las dos es el mercado de otra empresa.
    """
    for texto in (f"Total Revenue for {naics}", f"Total Revenue for {nombre}",
                  f"Sectoral Output for Manufacturing {nombre}"):
        datos = _pedir(f"{FRED_BASE}/series/search", {
            "search_text": texto, "api_key": key, "file_type": "json",
            "limit": 25, "order_by": "popularity", "sort_order": "desc"})
        if not datos:
            continue
        candidatas = []
        for s in datos.get("seriess") or []:
            escala = _dolares(s.get("units_short") or s.get("units") or "")
            if not escala:
                continue  # porcentajes e índices no son un tamaño de mercado
            texto_serie = f"{s.get('id', '')} {s.get('title', '')}"
            if naics not in texto_serie:
                continue
            # Ajustada estacionalmente cuando exista: el TAM se compara contra
            # un año fiscal completo, no contra un trimestre suelto.
            preferencia = (0 if s.get("frequency_short") == "Q" else 1,
                           0 if "SA" in (s.get("id") or "")[-3:] else 1,
                           -(len(s.get("observation_end") or "")))
            candidatas.append((preferencia, s, escala))
        if candidatas:
            candidatas.sort(key=lambda c: c[0])
            _, s, escala = candidatas[0]
            return {"serie": s, "escala": escala}
    return None


def _tamano(key: str, serie: dict, escala: float) -> tuple[float, float | None, str] | None:
    """El tamaño del mercado del último año y del anterior.

    Las series trimestrales se suman de cuatro en cuatro. Un trimestre suelto
    contra los ingresos anuales de un emisor daría una participación cuatro
    veces mayor de la real — el tipo de error que no se ve porque el número
    resultante sigue pareciendo razonable.
    """
    sid = serie.get("id")
    trimestral = serie.get("frequency_short") == "Q"
    datos = _pedir(f"{FRED_BASE}/series/observations", {
        "series_id": sid, "api_key": key, "file_type": "json",
        "limit": 12 if trimestral else 4, "sort_order": "desc"})
    if not datos:
        return None
    valores = [(o.get("date"), _numero(o.get("value")))
               for o in (datos.get("observations") or [])]
    valores = [(d, v) for d, v in valores if v is not None]

    if trimestral:
        if len(valores) < 4:
            return None
        actual = sum(v for _, v in valores[:4]) * escala
        previo = (sum(v for _, v in valores[4:8]) * escala
                  if len(valores) >= 8 else None)
    else:
        if not valores:
            return None
        actual = valores[0][1] * escala
        previo = valores[1][1] * escala if len(valores) >= 2 else None
    return actual, previo, valores[0][0]


def _escribir(path: Path, contenido: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_text(json.dumps(contenido, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _vigente(data: dict, hoy: date) -> bool:
    try:
        d = datetime.fromisoformat(str(data.get("_resuelto_en"))).date()
    except (ValueError, TypeError):
        return False
    return (hoy - d) < timedelta(days=VIGENCIA_DIAS)


def _sic_del_emisor(providers: Any, ticker: str) -> tuple[str, str] | None:
    """El SIC que la empresa declara ante la SEC, vía EDGAR."""
    edgar = getattr(providers, "edgar", None) if providers is not None else None
    if edgar is None and isinstance(providers, dict):
        edgar = providers.get("edgar")
    if edgar is None:
        return None
    try:
        cik = edgar.cik_for(ticker)
        return edgar.sic_for(cik) if cik else None
    except Exception:  # noqa: BLE001 — sin SIC la dimension queda NOT_SCORABLE
        logger.info("EDGAR no devolvio el SIC de %s", ticker, exc_info=True)
        return None


def asegurar_tam_industria(settings: Any, industria: str | None, ticker: str,
                           hoy: date | None = None, providers: Any = None,
                           sic: str | None = None) -> str:
    """Deja `Entradas/_industrias/<slug>.json` resuelto desde fuentes oficiales.

    Devuelve una frase con lo que pasó. Nunca levanta: un TAM que no se pudo
    descargar deja la dimensión NOT_SCORABLE, que es la respuesta correcta.

    Tres cosas que no hace, las tres a propósito: no toca un archivo escrito
    por un analista (sin `_generado_por` es suyo y gana siempre), no vuelve a
    preguntar mientras la respuesta siga vigente, y no borra un TAM que
    funcionaba porque la descarga de hoy falló.
    """
    from wbj.overlay.from_packet import _slug_industria

    slug = _slug_industria(industria)
    if not slug:
        return "el packet no trae industria: no hay TAM que resolver"

    hoy = hoy or datetime.now(timezone.utc).date()
    raiz = Path(getattr(settings, "inputs_dir", None)
                or Path(getattr(settings, "repo_root", ".")) / "Entradas")
    path = Path(raiz) / "_industrias" / f"{slug}.json"

    previo: dict = {}
    if path.exists():
        try:
            previo = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previo = {}
        if not previo.get("_generado_por"):
            return f"{slug}: TAM escrito por un analista, no se toca"
        if _vigente(previo, hoy):
            return f"{slug}: TAM vigente desde {previo.get('_resuelto_en')}"

    key = getattr(settings, "fred_api_key", None)
    if not key:
        return f"{slug}: sin FRED_API_KEY no hay fuente oficial de TAM"

    if not sic:
        par = _sic_del_emisor(providers, ticker)
        sic, sic_desc = par if par else (None, "")
    else:
        sic_desc = ""
    if not sic:
        return f"{slug}: EDGAR no devolvio el SIC de {ticker}"

    mapeo = SIC_A_NAICS.get(str(sic).zfill(4))
    if not mapeo:
        _guardar_hueco(path, hoy, ticker, sic, sic_desc, previo,
                       f"el SIC {sic} ({sic_desc}) no esta en la tabla SIC->NAICS")
        return f"{slug}: SIC {sic} sin equivalencia NAICS declarada"

    naics, nombre = mapeo
    hallada = _serie_de_la_industria(key, naics, nombre)
    if not hallada:
        _guardar_hueco(path, hoy, ticker, sic, sic_desc, previo,
                       f"FRED no publica una serie en dolares para NAICS {naics}")
        return f"{slug}: sin serie oficial para NAICS {naics}"

    medida = _tamano(key, hallada["serie"], hallada["escala"])
    if not medida:
        _guardar_hueco(path, hoy, ticker, sic, sic_desc, previo,
                       f"la serie {hallada['serie'].get('id')} no trae suficientes "
                       "observaciones para un año completo")
        return f"{slug}: serie {hallada['serie'].get('id')} sin año completo"

    actual, anterior, fecha = medida
    s = hallada["serie"]
    contenido = {
        "_generado_por": "vertex/tam_oficial",
        "_resuelto_en": hoy.isoformat(),
        "_cadena": f"{ticker} -> SEC EDGAR SIC {sic} ({sic_desc}) -> NAICS {naics} "
                   f"-> FRED {s.get('id')}",
        "_geografia": ("Estados Unidos. Las encuestas del Census miden el mercado "
                       "DOMESTICO; los ingresos de un emisor son mundiales. Lee la "
                       "participacion como captura sobre el mercado de EE.UU."),
        "_ambito": "US",
        "_serie_fred": s.get("id"),
        "_serie_titulo": s.get("title"),
        "_serie_frecuencia": s.get("frequency_short"),
        "_serie_ultima_observacion": fecha,
        "_unidades_origen": s.get("units_short") or s.get("units"),
        "_para_hacerlo_tuyo": ("Borra `_generado_por` y el sistema no vuelve a "
                               "tocar este archivo: pasa a ser un TAM de analista."),
        "tam": int(actual),
        "tam_source": f"U.S. Census Bureau / BLS via FRED — {s.get('title')} "
                      f"({s.get('id')}), observacion {fecha}",
        "tam_source_tier": 1,
    }
    if anterior:
        contenido["tam_history"] = [int(anterior), int(actual)]
    if previo.get("_aplica_a"):
        contenido["_aplica_a"] = previo["_aplica_a"]
    try:
        _escribir(path, contenido)
    except OSError as e:
        return f"{slug}: no se pudo guardar ({type(e).__name__})"
    return (f"{slug}: TAM ${int(actual):,} de FRED {s.get('id')} "
            f"(Census/BLS, tier 1, SIC {sic} -> NAICS {naics})")


def _guardar_hueco(path: Path, hoy: date, ticker: str, sic: str, sic_desc: str,
                   previo: dict, motivo: str) -> None:
    """Deja constancia del hueco para no repetir la descarga cada análisis.

    Nunca pisa un TAM que ya funcionaba: si la industria tenía cifra y hoy no
    se pudo refrescar, se queda la de antes.
    """
    if previo.get("tam"):
        return
    try:
        _escribir(path, {
            "_generado_por": "vertex/tam_oficial",
            "_resuelto_en": hoy.isoformat(),
            "_sin_tam": motivo,
            "_sic_visto": f"{sic} ({sic_desc}) — visto al analizar {ticker}",
            "_que_hacer": ("Dos caminos. (a) Anade el SIC a `SIC_A_NAICS` en "
                           "`engine/wbj/overlay/tam_oficial.py` si el Census "
                           "publica esa industria. (b) Escribe el TAM a mano en "
                           "este archivo y borra `_generado_por`: eso lo vuelve "
                           "tuyo y el sistema deja de tocarlo."),
        })
    except OSError:
        pass
