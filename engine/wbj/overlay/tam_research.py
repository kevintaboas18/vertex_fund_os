"""El TAM se investiga solo, para cualquier industria.

Hasta aquí el tamaño del mercado lo tecleaba una persona. Alguien buscaba el
comunicado de Omdia, comprobaba la cifra, la escribía en un JSON y sólo
entonces `MKT-TAM-005`, `MKT-SHARE-006` y `MKT-SHDELTA-007` podían puntuar.
Analizar un ticker de una industria nueva significaba parar y hacer deberes.

Este módulo hace esos deberes. Es el mismo patrón que ya usa la extracción del
10-K: se dispara solo al analizar, se cachea, y se refresca por trimestre.

**Por qué no hay un endpoint y sí un buscador.** IDC, Omdia, Gartner, Mercury
Research y Dell'Oro venden sus informes; no publican API. Lo que sí publican
son comunicados de prensa con la cifra de cabecera, y eso es lo que se busca y
se cita. No hay atajo: o se lee un comunicado, o no hay TAM.

**Por qué esto no viola "sin evidencia, no hay número".** Nada se acepta sin
firma. La respuesta se rechaza entera si no trae casa de análisis reconocida,
URL y añada. Una casa que no esté en la lista de abajo se rechaza en vez de
degradarse a tier 4: preferir el hueco es justo la regla de la casa.

**El error que este archivo tiene que evitar.** El TAM anterior de NVDA era
Gartner Data Center Systems, $489.500M — gasto del USUARIO FINAL en servidores
y almacenamiento. NVDA vende aguas arriba, en componentes. Numerador y
denominador estaban en capas distintas de la cadena de valor y el cociente no
significaba nada, pero *parecía* razonable: daba 39,6%. Ningún chequeo
aritmético lo habría cazado. Por eso `capa` es obligatoria y se guarda escrita
en el archivo: si la cifra vuelve a ser de la capa equivocada, al menos queda
a la vista de quien audite, que es la única defensa real que existe.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Cada cuánto se vuelve a preguntar. Trimestral, como los filings: las casas de
# análisis revisan sus pronósticos por trimestre, y preguntar más a menudo
# gasta cuota para recibir la misma cifra.
VIGENCIA_DIAS = 90

# Quién puede firmar un TAM. La lista no es decorativa: es la diferencia entre
# tier 3 (puntuable) y tier 5 (no puntuable). Una página que recopila cifras de
# otros sin firmar la metodología no entra — y sin firma reconocida se rechaza
# la respuesta entera en vez de rebajarla, porque un tier inventado convierte
# un desconocido en un score favorable.
CASAS_TIER_1 = ("census bureau", "bureau of labor", "bureau of economic",
                "eurostat", "fdic", "federal reserve", "oecd", "world bank",
                "eia", "usda", "cms.gov", "centers for medicare")
CASAS_TIER_2 = ("semi ", "sia ", "semiconductor industry association",
                "world semiconductor trade statistics", "wsts", "ifpi",
                "iata", "acea", "swift", "bis ", "sifma")
CASAS_TIER_3 = ("idc", "omdia", "gartner", "mercury research", "counterpoint",
                "trendforce", "dell'oro", "delloro", "canalys", "yole",
                "forrester", "ibisworld", "euromonitor", "nielsen",
                "circana", "npd", "s&p global", "wood mackenzie", "rystad",
                "bloombergnef", "bnef", "iqvia", "evaluate pharma",
                "frost & sullivan", "abi research", "techinsights",
                "strategy analytics", "gfk", "statista market insights",
                # Consultoras con publicación de industria y metodología
                # declarada. El Global Banking Annual Review de McKinsey es el
                # caso que descubrió el hueco: research legítimo, rechazado por
                # una lista pensada sólo para casas de datos tecnológicos.
                "mckinsey", "boston consulting", "bcg", "bain & company",
                "deloitte", "pwc", "pricewaterhouse", "ernst & young",
                "kpmg", "accenture", "oliver wyman", "capgemini")


def _casas_aceptadas() -> str:
    """Las casas de la lista blanca, en texto, para meterlas EN el prompt.

    Antes el modelo elegía casa y la validación la rechazaba después: gastaba
    una búsqueda entera para descubrir que Mordor Intelligence no cuenta.
    Diciéndoselo antes, busca directamente donde sí se le va a aceptar. La
    lista sale de las mismas constantes que valida `_tier_de_la_fuente`, así
    que prompt y validación no pueden separarse con el tiempo.

    Se filtra por el espacio final, no por longitud. Filtrar por longitud
    parecía razonable y dejaba fuera a IDC, BCG, PwC y NPD por tener tres
    letras: casas de primera línea excluidas del prompt por cortas. Lo que sí
    hay que excluir son los fragmentos que sólo funcionan pegados a un espacio
    —"semi ", "sia ", "bis "— porque sueltos casan con media lengua inglesa.
    """
    nombres = [c.strip().title() for c in (CASAS_TIER_3 + CASAS_TIER_2 + CASAS_TIER_1)
               if not c.endswith(" ")]
    return ", ".join(nombres)


def _tier_de_la_fuente(fuente: str) -> int | None:
    """El tier sale de QUIÉN firma, no de lo que el modelo diga que vale.

    Devuelve `None` cuando la firma no se reconoce, y esa es la respuesta
    correcta: `DECISION_RULES.md` pone en tier 5 —no puntuable— cualquier
    afirmación de tamaño de mercado sin casa identificable detrás.
    """
    f = (fuente or "").lower()
    for tier, casas in ((1, CASAS_TIER_1), (2, CASAS_TIER_2), (3, CASAS_TIER_3)):
        if any(c in f for c in casas):
            return tier
    return None


def _numero(v: Any) -> float | None:
    """Un número positivo, o nada. Acepta el texto que devuelve un LLM."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v) if v > 0 else None
    if isinstance(v, str):
        limpio = re.sub(r"[,\s$]", "", v)
        try:
            n = float(limpio)
        except ValueError:
            return None
        return n if n > 0 else None
    return None


def _json_del_texto(texto: str) -> dict | None:
    """El objeto JSON que hay dentro de la respuesta.

    Los modelos con búsqueda envuelven el JSON en prosa y en vallas de código
    por mucho que se les pida lo contrario, así que se recorta al primer `{`
    y al último `}` en vez de confiar en que la respuesta venga limpia.
    """
    if not texto:
        return None
    t = texto.strip()
    if "```" in t:
        t = re.sub(r"```(?:json)?", "", t)
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        d = json.loads(t[i:j + 1])
    except (json.JSONDecodeError, ValueError):
        return None
    return d if isinstance(d, dict) else None


PROMPT = """Eres un analista de research de inversiones. Busca en la web el
tamaño del mercado (TAM) de la industria "{industria}", el mercado en el que
compiten empresas como {ejemplos}.

Reglas innegociables:
1. La cifra TIENE que venir de una de estas casas, y de ninguna otra:
   {casas}
   Agregadores tipo Mordor Intelligence, Grand View, Precedence, MarketsAndMarkets
   o Statista (salvo "Statista Market Insights") NO valen: recopilan cifras de
   terceros sin firmar la metodología. Si la primera cifra que encuentres es de
   una casa que no está en la lista, BUSCA OTRA VEZ con el nombre de una que sí
   esté. Sólo si ninguna de la lista cubre esta industria responde {{"tam": null}}.
2. Da la URL exacta del comunicado o página donde está la cifra.
3. LA CAPA IMPORTA MÁS QUE LA CIFRA. Un mercado se puede medir en capas
   distintas de la cadena de valor: gasto del usuario final, ingresos de
   fabricantes de equipos, o ingresos de componentes. Tienen que ser la MISMA
   capa en la que las empresas de esta industria facturan. Si la industria
   vende componentes, NO sirve una cifra de gasto del usuario final.
4. No interpoles años que la fuente no publique.

Responde SOLO con este JSON, sin texto alrededor:
{{
  "tam": <tamaño del mercado del último año reportado, en USD, número entero>,
  "tam_anio": <año de esa cifra>,
  "tam_history": [<año anterior>, <último año>],
  "tam_source": "<casa de análisis + nombre del informe + añada>",
  "cita": "<URL exacta>",
  "cita_textual": "<la frase de la fuente con la cifra>",
  "capa": "<qué mide exactamente: gasto de usuario final / ingresos de
            fabricantes / ingresos de componentes / ...>",
  "capa_coincide": "<por qué esa capa es donde facturan estas empresas>",
  "segmento_patrones": [<trozos de nombre en minúscula con los que se reconoce,
     en la segmentacion de un 10-K, el segmento que compite en ESTE mercado.
     Ej. ["data center"]. Deben ser lo bastante específicos como para encajar
     con UN solo segmento por empresa>]
}}
Si no encuentras una cifra que cumpla las reglas, responde {{"tam": null}}."""


def _preguntar_gemini(settings: Any, prompt: str) -> tuple[str, str | None]:
    """Gemini con búsqueda de Google. Primera opción por tener la búsqueda
    integrada y ser la cuota que este proyecto sí tiene."""
    key = getattr(settings, "gemini_api_key", None)
    if not key:
        return "", "GEMINI_API_KEY no configurada"
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        return "", "el SDK google-genai no esta instalado"
    try:
        cliente = genai.Client(api_key=key)
        r = cliente.models.generate_content(
            model=getattr(settings, "tam_model", None) or "gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())]))
        return (r.text or ""), None
    except Exception as e:  # noqa: BLE001 — el motivo se nombra, no se traga
        return "", f"gemini: {type(e).__name__} {str(e)[:120]}"


def _preguntar_openai(settings: Any, prompt: str) -> tuple[str, str | None]:
    """OpenAI con `web_search`. Suplente, no sustituto: se usa cuando Gemini
    devuelve cuota agotada."""
    key = getattr(settings, "openai_api_key", None)
    if not key:
        return "", "OPENAI_API_KEY no configurada"
    try:
        from openai import OpenAI
    except ImportError:
        return "", "el SDK openai no esta instalado"
    try:
        cliente = OpenAI(api_key=key)
        r = cliente.responses.create(
            model="gpt-4.1", tools=[{"type": "web_search"}], input=prompt)
        return (getattr(r, "output_text", "") or ""), None
    except Exception as e:  # noqa: BLE001
        return "", f"openai: {type(e).__name__} {str(e)[:120]}"


def _investigar(settings: Any, industria: str, ejemplos: str) -> tuple[dict | None, list[str]]:
    """Pregunta a los proveedores en orden y devuelve el primer JSON válido.

    Los fallos se acumulan y se devuelven TODOS. Un TAM que no aparece porque
    se acabó la cuota y uno que no aparece porque la industria no tiene
    estudios publicados son problemas distintos, y el reporte tiene que poder
    distinguirlos.
    """
    prompt = PROMPT.format(industria=industria, ejemplos=ejemplos,
                           casas=_casas_aceptadas())
    fallos: list[str] = []
    for preguntar in (_preguntar_gemini, _preguntar_openai):
        texto, error = preguntar(settings, prompt)
        if error:
            fallos.append(error)
            continue
        datos = _json_del_texto(texto)
        if datos is None:
            fallos.append(f"{preguntar.__name__}: respuesta sin JSON legible")
            continue
        return datos, fallos
    return None, fallos


def _validar(datos: dict, industria: str) -> tuple[dict | None, str]:
    """Convierte la respuesta del modelo en overlay, o explica por qué no.

    Todo lo que no cuadre devuelve `None` y un motivo escrito. El motivo va al
    archivo y al log: un TAM ausente tiene que poder explicarse.
    """
    tam = _numero(datos.get("tam"))
    if tam is None:
        return None, "la busqueda no encontro una cifra con fuente firmada"

    fuente = str(datos.get("tam_source") or "").strip()
    if not fuente:
        return None, "la cifra vino sin nombre de fuente"
    tier = _tier_de_la_fuente(fuente)
    if tier is None:
        return None, (f"fuente no reconocida como casa de analisis: {fuente!r} "
                      "— tier 5, no puntuable")

    cita = str(datos.get("cita") or "").strip()
    if not cita.startswith("http"):
        return None, f"la fuente {fuente!r} vino sin URL verificable"
    # Gemini devuelve sus citas envueltas en un redirect de grounding que
    # caduca y no dice de quién es la página. Sirve para llegar hoy, no para
    # auditar dentro de seis meses, así que se exige además la frase textual:
    # con el nombre de la casa y la cita literal, la cifra se vuelve a
    # encontrar aunque el enlace muera.
    redirect = "vertexaisearch.cloud.google.com" in cita or "/grounding-api-" in cita
    textual = str(datos.get("cita_textual") or "").strip()
    if redirect and not textual:
        return None, (f"la fuente {fuente!r} sólo trajo un enlace de redirect "
                      "que caduca, y sin frase textual no queda forma de "
                      "reencontrar la cifra")

    capa = str(datos.get("capa") or "").strip()
    if not capa:
        return None, ("la respuesta no declaro QUE capa de la cadena de valor "
                      "mide — es el error que dejo a NVDA con un TAM de gasto "
                      "de usuario final durante semanas")

    fuera: dict[str, Any] = {
        "tam": int(tam),
        "tam_source": fuente,
        "tam_source_tier": tier,
    }

    historia = [n for n in (_numero(v) for v in (datos.get("tam_history") or []))
                if n is not None]
    # Dos años o ninguno: `MKT-SHDELTA-007` compara periodos, y una serie de un
    # solo punto no es una serie.
    if len(historia) >= 2:
        historia = historia[-2:]
        # Medido: para JPM el modelo devolvió `[2024, 2025]` — los AÑOS en el
        # sitio de los dólares. Nada aritmético lo delataba, y la participación
        # habría salido de dividir los ingresos de JPM entre 2024. Un mercado
        # no encoge a la décima parte ni se multiplica por diez en un año, así
        # que cualquier valor fuera de esa horquilla no es un tamaño de mercado.
        plausible = all(tam / 10 <= n <= tam * 10 for n in historia)
        # El último punto ES el TAM actual: `_share_automatico` divide el año
        # anterior entre `historia[-2]` y el actual entre `tam`. Si el último
        # punto fuera otra cifra, las dos mitades de la serie hablarían de
        # mercados distintos y la variación no significaría nada.
        cierra = abs(historia[-1] - tam) <= tam * 0.02
        if plausible and cierra:
            fuera["tam_history"] = [int(n) for n in historia]
        else:
            logger.info("serie de TAM descartada para %s: %s (tam=%s)",
                        industria, historia, tam)
            fuera["_sin_historia"] = (
                f"la serie devuelta {[int(n) for n in historia]} no es una serie "
                f"de tamanos de mercado junto a un TAM de {int(tam)}")

    patrones = [str(p).lower().strip() for p in (datos.get("segmento_patrones") or [])
                if str(p).strip()]
    if patrones:
        fuera["_segmento_patrones"] = patrones

    fuera["_capa"] = capa
    fuera["_capa_coincide"] = str(datos.get("capa_coincide") or "").strip()
    fuera["_cita"] = cita
    if redirect:
        fuera["_cita_es_redirect"] = ("Enlace de grounding de Gemini: caduca. "
                                      "La cifra se reencuentra por el nombre de "
                                      "la casa mas la frase textual de abajo.")
    fuera["_cita_textual"] = str(datos.get("cita_textual") or "").strip()
    return fuera, ""


def _escribir(path: Path, contenido: dict) -> None:
    """Escritura atómica: el archivo lo lee el siguiente análisis, que puede
    estar corriendo ya."""
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
    resuelto = data.get("_resuelto_en")
    if not resuelto:
        return False
    try:
        d = datetime.fromisoformat(str(resuelto)).date()
    except ValueError:
        return False
    return (hoy - d) < timedelta(days=VIGENCIA_DIAS)


def asegurar_tam_industria(settings: Any, industria: str | None, ticker: str,
                           hoy: date | None = None) -> str:
    """Deja `Entradas/_industrias/<slug>.json` listo para esta industria.

    Devuelve una frase con lo que pasó, para el log y la auditoría. Nunca
    levanta: un TAM que no se pudo investigar deja la dimensión NOT_SCORABLE,
    que es exactamente lo que debe pasar, no un análisis roto.

    Tres cosas que NO hace, y las tres a propósito:

    - **No toca un archivo escrito por una persona.** Sin `_generado_por`, el
      archivo es de un analista y gana siempre. Quien leyó el estudio sabe
      algo que ninguna búsqueda sabe.
    - **No vuelve a preguntar si la respuesta sigue vigente.** Trimestral, como
      los filings.
    - **No borra lo bueno cuando falla.** Si la búsqueda de hoy no encuentra
      nada, el TAM de hace dos meses se queda donde está.
    """
    from wbj.overlay.from_packet import _slug_industria  # circular sólo al usar

    slug = _slug_industria(industria)
    if not slug:
        return "el packet no trae industria: no hay TAM que investigar"

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

    datos, fallos = _investigar(settings, industria or slug, ticker)
    if datos is None:
        motivo = "; ".join(fallos) or "sin respuesta de ningun proveedor"
        logger.warning("TAM de %s no investigado: %s", slug, motivo)
        return f"{slug}: no se pudo investigar ({motivo})"

    overlay, error = _validar(datos, industria or slug)
    if overlay is None:
        logger.info("TAM de %s rechazado: %s", slug, error)
        if previo:
            return f"{slug}: busqueda rechazada ({error}), se conserva el anterior"
        # Se deja constancia del intento para no repetirlo cada análisis.
        try:
            _escribir(path, {
                "_generado_por": "vertex/tam_research",
                "_resuelto_en": hoy.isoformat(),
                "_sin_tam": error,
                "_que_hacer": ("Si conoces un estudio que cumpla, escribe el TAM "
                               "a mano en este archivo y borra `_generado_por`: "
                               "eso lo vuelve tuyo y el sistema deja de tocarlo."),
            })
        except OSError:
            pass
        return f"{slug}: sin TAM ({error})"

    contenido = {
        "_generado_por": "vertex/tam_research",
        "_resuelto_en": hoy.isoformat(),
        "_como_se_obtuvo": ("Buscado automaticamente al analizar " + ticker +
                            ". Se revisa cada " + str(VIGENCIA_DIAS) + " dias."),
        "_para_hacerlo_tuyo": ("Borra `_generado_por` y el sistema no vuelve a "
                               "tocar este archivo: pasa a ser un TAM de analista."),
        **overlay,
    }
    if previo.get("_aplica_a"):
        contenido["_aplica_a"] = previo["_aplica_a"]
    try:
        _escribir(path, contenido)
    except OSError as e:
        return f"{slug}: no se pudo guardar el TAM ({type(e).__name__})"
    return (f"{slug}: TAM ${int(overlay['tam']):,} de {overlay['tam_source']} "
            f"(tier {overlay['tam_source_tier']})")
