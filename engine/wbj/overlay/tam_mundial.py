"""El TAM es MUNDIAL y viene del organismo que mide el mercado.

Tres intentos hicieron falta para llegar aquí, y los dos primeros enseñaron
algo que este archivo tiene escrito en las reglas:

1. **Buscar en Google y aceptar lo que salga.** Devolvía el *comunicado de
   prensa* sobre el dato en vez del dato, porque IDC, Omdia y Gartner venden
   sus informes. Se citaba la nota que resume un número que nadie puede abrir.
2. **Descargar del Census vía FRED.** Oficial y tier 1, pero **sólo EE.UU.** —
   y `market.py::sam()` estrecha el TAM por geografía, o sea que espera uno
   mundial. Un denominador doméstico bajo un numerador global le daba a AAPL
   un 1.900% de participación.
3. **Sumar los ingresos de todas las cotizadas del sector.** Mundial y
   automático, pero apila capas de la cadena: NVDA factura $216.000M que ya
   incluyen lo que le pagó a TSMC, y TSMC vuelve a entrar con $119.000M.
   Medido en semiconductores: $921.000M contra los ~$790.000M reales, un 17%
   de aire, con un sesgo que cambia de industria en industria.

Lo que sí funciona es preguntarle **a quien mide el mercado**, en este orden:

- **Asociación de industria** (tier 2, confianza 85). WSTS publica las ventas
  mundiales de semiconductores —$630.500M en 2024, $791.700M en 2025— gratis y
  en su propia web. Es el ORIGEN del dato, no un resumen. Y mide UNA capa:
  facturación de chips, sin fabricantes de equipos ni fundiciones encima. Cada
  industria tiene la suya: IFPI para música grabada, IATA para aerolíneas,
  ACEA para automoción, SIFMA para valores.
- **Casa de análisis** (tier 3, confianza 70) sólo si no hay asociación.

Máximo dos fuentes, por decisión de Victor: una cifra con dos orígenes
verificables vale más que cinco a medio comprobar.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

VIGENCIA_DIAS = 90

#: Cada cuantos dias se vuelve a leer un TAM en la pagina de su fuente. WSTS
#: revisa sus ventas mundiales cada trimestre y la IEA su demanda cada mes: un
#: numero correcto en agosto puede estar viejo en noviembre.
REVISION_DIAS = 90

#: Cuántas veces se pregunta antes de concluir que nadie publica ese mercado.
#: Medido: "Consumer Electronics" acertó en el 2º de 4 intentos y falló en los
#: otros 3. Un solo `null` no es prueba de que el mercado no exista.
INTENTOS = 4

#: El tier gratuito de Gemini permite 20 peticiones por minuto y su propio
#: error dice cuanto falta. Esperarlo cuesta segundos y sale UNA vez cada 90
#: dias por industria; no esperarlo deja la industria sin TAM tres meses.
ESPERA_MAXIMA_S = 65.0
ESPERAS_MAXIMAS = 3

#: Cuantas pausas por cuota aguanta un barrido completo antes de rendirse. A
#: 20 peticiones por minuto, 132 industrias necesitan esperar muchas veces;
#: rendirse a la primera dejaba el barrido muerto en la industria 1.
PAUSAS_MAXIMAS_BARRIDO = 200

#: Para comprobar la cita contra su propia pagina.
_AGENTE = "Vertex Research vertexholgroup@gmail.com"
_MAX_BYTES_CITA = 600_000

# Asociaciones de industria: miden su propio mercado, publican mundial y gratis,
# y por construcción cubren UNA capa de la cadena. Es el tier más alto que un
# mercado privado puede tener — por encima sólo hay estadística de gobierno, que
# no existe a escala mundial por industria.
ASOCIACIONES = (
    "wsts", "world semiconductor trade statistics", "semiconductor industry association",
    "sia ", "semi ", "sematech", "ifpi", "riaa", "iata", "acea", "oica", "sifma",
    "swift", "gsma", "ctia", "iea ", "wind europe", "solarpower europe",
    "world steel", "worldsteel", "icca", "cropLife", "ifa ", "phrma", "efpia",
    "world gold council", "world bank", "oecd", "unctad", "wto", "who ",
    "world travel", "wttc", "unwto", "insurance information institute",
    "american banking association", "aba ", "fdic", "bis ",
    # Energía: la IEA ya estaba, pero el mercado del petróleo lo publican
    # tres cuerpos y sólo uno estaba en la lista. La EIA es una agencia
    # estadística del gobierno de EE.UU. y su International Energy Outlook es
    # mundial; la OPEP publica su Monthly Oil Market Report; y el Energy
    # Institute heredó de BP el Statistical Review of World Energy, que es EL
    # conjunto de datos canónico de energía mundial.
    "opec", "eia ", "energy information administration", "energy institute",
    "statistical review of world energy", "irena",
    # Inmobiliario: NAREIT y EPRA son las asociaciones del sector cotizado, y
    # MSCI Real Assets publica el tamaño del mercado profesional mundial.
    # Sin ellas ninguna industria REIT podía resolver su mercado.
    "nareit", "epra", "inrev", "anrev", "msci real assets", "msci real estate",
    # Salud: la base de gasto sanitario mundial de la OMS y las estadísticas
    # de la OCDE ya cubrían el gasto; faltaba quien publica el seguro.
    "global health expenditure", "ahip", "iais",
)

# Casas de análisis, sólo cuando no hay asociación que cubra el mercado.
CASAS = (
    "idc", "omdia", "gartner", "mercury research", "counterpoint", "trendforce",
    "dell'oro", "delloro", "canalys", "yole", "forrester", "ibisworld",
    "euromonitor", "nielsen", "circana", "npd", "s&p global", "wood mackenzie",
    "rystad", "bloombergnef", "bnef", "iqvia", "evaluate pharma", "gfk",
    "frost & sullivan", "abi research", "techinsights", "strategy analytics",
    "mckinsey", "boston consulting", "bain & company", "deloitte", "pwc",
    "ernst & young", "kpmg", "accenture", "oliver wyman",
    # Inmobiliario comercial: las cuatro casas que miden ese mercado y lo
    # publican. Ninguna estaba, y sin ellas un REIT no tenía denominador ni
    # por asociación ni por casa.
    "jll", "jones lang lasalle", "cbre", "cushman", "colliers", "savills",
    "green street", "real capital analytics",
    # Electrónica de consumo: CTA mide el mercado y Circana/GfK/Counterpoint
    # ya estaban, pero faltaba quien publica el agregado mundial de la
    # categoría entera en vez de una línea de producto.
    "consumer technology association", "cta ", "canalys", "idc worldwide",
)

# Lo que NO cuenta como fuente. Recopilan cifras de terceros sin firmar la
# metodología: tier 5, no puntuable. Se nombran en el prompt para que el modelo
# no gaste una búsqueda entera en una respuesta que va a ser rechazada.
AGREGADORES = ("mordor", "grand view", "precedence", "marketsandmarkets",
               "fortune business insights", "verified market", "allied market",
               "zion market", "polaris market", "straits research",
               "imarc", "technavio", "researchandmarkets", "statista")


def _tier_de_la_fuente(fuente: str) -> int | None:
    """El tier sale de QUIÉN firma. `None` cuando no se reconoce, y ésa es la
    respuesta correcta: sin casa identificable la cifra es tier 5."""
    f = (fuente or "").lower()
    if any(a in f for a in AGREGADORES):
        return None
    if any(a in f for a in ASOCIACIONES):
        return 2
    if any(c in f for c in CASAS):
        return 3
    return None


def _fuentes_aceptadas() -> str:
    """La lista, en texto, para meterla EN el prompt.

    Va desde las mismas constantes que valida `_tier_de_la_fuente`, así que
    prompt y validación no pueden separarse con el tiempo. Se filtra por el
    espacio final, no por longitud: filtrar por longitud dejaba fuera a IDC,
    BCG y PwC por tener tres letras.
    """
    def _limpias(xs):
        return [x.strip().upper() if len(x.strip()) <= 4 else x.strip().title()
                for x in xs if not x.endswith(" ")]
    return ("ASOCIACIONES DE INDUSTRIA (preferidas): "
            + ", ".join(_limpias(ASOCIACIONES))
            + "\n   CASAS DE ANALISIS (solo si no hay asociacion): "
            + ", ".join(_limpias(CASAS)))


def _numero(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v) if v > 0 else None
    try:
        n = float(re.sub(r"[,\s$]", "", str(v)))
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _json_del_texto(texto: str) -> dict | None:
    """El JSON que hay dentro de la respuesta. Los modelos con búsqueda lo
    envuelven en prosa y vallas de código por mucho que se les pida lo
    contrario, así que se recorta al primer `{` y al último `}`."""
    if not texto:
        return None
    t = re.sub(r"```(?:json)?", "", texto.strip()) if "```" in texto else texto.strip()
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        d = json.loads(t[i:j + 1])
    except (json.JSONDecodeError, ValueError):
        return None
    return d if isinstance(d, dict) else None


PROMPT = """Eres un analista de research de inversiones. Necesito el tamaño
MUNDIAL del mercado en el que compite {ticker}, una empresa de la industria
"{industria}".

REGLA 1 — QUIÉN LO FIRMA. Máximo DOS fuentes, de esta lista y de ninguna otra:
   {fuentes}
   Prefiere SIEMPRE la asociación de industria: mide su propio mercado, publica
   mundial y gratis, y es el origen del dato en vez de un resumen. Ejemplo: para
   semiconductores es WSTS / la Semiconductor Industry Association, que publica
   las ventas mundiales de chips. NO valen agregadores tipo {agregadores}:
   recopilan cifras de terceros sin firmar metodología.

REGLA 2 — MUNDIAL. La cifra tiene que ser del mercado MUNDIAL. Una cifra de
   EE.UU., de Europa o de un país suelto NO sirve: los ingresos de la empresa
   son mundiales y el cociente compararía ámbitos distintos.

REGLA 3 — UNA SOLA CAPA. Un mercado se mide en una capa concreta de la cadena
   de valor. "Ventas mundiales de semiconductores" es UNA capa: facturación de
   chips. Si sumaras además equipos de fabricación y fundiciones estarías
   contando el mismo dólar dos veces, porque el precio del chip ya incluye lo
   que su fabricante pagó por la máquina. La capa que des tiene que ser aquella
   en la que {ticker} FACTURA.

REGLA 4 — NO INTERPOLES. Sólo años que la fuente publique.

Responde SOLO con este JSON, sin texto alrededor:
{{
  "tam": <mercado mundial del ultimo ano publicado, en USD, entero>,
  "tam_anio": <ano de esa cifra>,
  "tam_history": [<mercado del ano anterior, en USD>, <el mismo numero de "tam">],
  "tam_source": "<asociacion o casa + nombre del dato + ano>",
  "cita": "<URL exacta>",
  "cita_textual": "<la frase de la fuente con la cifra>",
  "segunda_fuente": "<opcional: otra de la lista que confirme la cifra, o null>",
  "ambito": "<tiene que decir mundial/worldwide/global>",
  "capa": "<que mide exactamente, ej. 'facturacion mundial de chips'>",
  "capa_coincide": "<por que es la capa en la que {ticker} factura>",
  "segmento_patrones": [<trozos de nombre en minuscula para reconocer, en la
     segmentacion de un 10-K, el segmento que compite en ESTE mercado. Ej.
     ["data center"]. Deben encajar con UN solo segmento por empresa>]
}}
Si ninguna fuente de la lista publica este mercado, responde {{"tam": null}}."""


def _error_legible(proveedor: str, e: Exception) -> str:
    """El fallo recortado, pero SIN perder cuánto hay que esperar.

    El recorte a 120 caracteres se comía justo el dato que decide qué hacer:
    el 429 de Gemini pone su `"Please retry in 16.57s"` al final de un mensaje
    largo, así que `_segundos_de_espera` no encontraba nada y una pausa de
    diecisiete segundos se convertía en una industria sin TAM durante 90 días.
    Se rescata la frase antes de recortar y se pega al final.
    """
    texto = str(e)
    corto = f"{proveedor}: {type(e).__name__} {texto[:120]}"
    m = re.search(r"retry in [0-9.]+\s*s", texto, re.I)
    return f"{corto} — Please {m.group(0)}" if m else corto


def _preguntar_gemini(settings: Any, prompt: str) -> tuple[str, str | None]:
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
            model="gemini-2.5-flash", contents=prompt,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())]))
        return (r.text or ""), None
    except Exception as e:  # noqa: BLE001 — el motivo se nombra, no se traga
        return "", _error_legible("gemini", e)


def _preguntar_openai(settings: Any, prompt: str) -> tuple[str, str | None]:
    key = getattr(settings, "openai_api_key", None)
    if not key:
        return "", "OPENAI_API_KEY no configurada"
    try:
        from openai import OpenAI
    except ImportError:
        return "", "el SDK openai no esta instalado"
    try:
        r = OpenAI(api_key=key).responses.create(
            model="gpt-4.1", tools=[{"type": "web_search"}], input=prompt)
        return (getattr(r, "output_text", "") or ""), None
    except Exception as e:  # noqa: BLE001
        return "", _error_legible("openai", e)


def _es_falta_de_cuota(error: str) -> bool:
    """¿El proveedor dijo "no" por cuota, o por no encontrar nada?

    Son fallos distintos y sólo uno mejora esperando.
    """
    e = (error or "").lower()
    return any(x in e for x in (
        "429", "resource_exhausted", "rate limit", "ratelimit", "quota",
        "no credits", "insufficient_quota", "too many requests"))


def _segundos_de_espera(error: str) -> float | None:
    """Cuánto pide esperar el proveedor, si lo dice y merece la pena.

    Hay dos clases de "cuota agotada" y confundirlas cuesta caro en los dos
    sentidos. El límite del tier gratuito de Gemini es de **20 peticiones por
    minuto** y su propio error trae `"Please retry in 16.57s"`: eso se arregla
    esperando. El de OpenAI es "You have no credits remaining", que no se
    arregla esperando ni un dia.

    Devuelve los segundos sólo cuando el proveedor los nombra y caben en
    `ESPERA_MAXIMA_S`. Sin cifra, `None` — no se inventa una pausa, que es
    justo lo que convierte un limite por minuto en un cuelgue.
    """
    m = re.search(r"retry in ([0-9.]+)\s*s", error or "", re.I)
    if not m:
        return None
    try:
        segundos = float(m.group(1))
    except ValueError:
        return None
    return segundos + 1.0 if 0 < segundos <= ESPERA_MAXIMA_S else None


def _investigar(settings: Any, industria: str, ticker: str,
                intentos: int = INTENTOS) -> tuple[dict | None, list[str]]:
    """La MEJOR respuesta de varios intentos, y TODOS los fallos por el camino.

    Antes preguntaba una vez a cada proveedor y se quedaba con la primera
    respuesta legible. Dos problemas medidos, los dos con evidencia:

    **Un `null` no era prueba.** El modelo lleva búsqueda web y no es
    determinista: preguntando cuatro veces por "Consumer Electronics" —una
    industria que llevaba semanas marcada "ninguna asociación publica este
    mercado"— el segundo intento devolvió $783.000M de Gartner y validó. Los
    otros tres fueron `null`. Con un solo intento, y con OpenAI sin créditos,
    esa industria se declaraba imposible por una sola tirada de dados.

    **La primera no es la mejor.** Preguntando dos veces por bebidas no
    alcohólicas salieron $418.000M de NielsenIQ (tier 2) y $141.700M de Frost
    & Sullivan (tier 3) — mercados distintos, ambos válidos por separado. Con
    "la primera que llegue" el TAM de una industria dependía del orden de las
    respuestas. Ahora gana el tier más bajo, que es el mejor: `DECISION_RULES.md`
    pone la asociación de industria por encima de la casa de análisis porque
    mide su propio mercado en vez de resumir el de otros.

    Se corta en cuanto aparece un tier 2: por encima no hay nada que buscar.
    """
    prompt = PROMPT.format(industria=industria, ticker=ticker,
                           fuentes=_fuentes_aceptadas(),
                           agregadores=", ".join(a.title() for a in AGREGADORES[:6]))
    fallos: list[str] = []
    mejor: dict | None = None
    mejor_tier = 99
    agotados: set = set()
    vueltas_reales = 0
    esperas_restantes = ESPERAS_MAXIMAS
    for vuelta in range(max(1, intentos)):
        proveedores = [f for f in (_preguntar_gemini, _preguntar_openai)
                       if f not in agotados]
        if not proveedores:
            break
        for preguntar in proveedores:
            texto, error = preguntar(settings, prompt)
            if error:
                # Un proveedor caído se nombra UNA vez, no una por vuelta: si
                # no, cuatro intentos dejan el motivo repetido cuatro veces y
                # el archivo dice cuatro veces lo mismo.
                if error not in fallos:
                    fallos.append(error)
                # Reintentar un límite de cuota lo empeora: cada vuelta gasta
                # otra petición contra el mismo contador que ya dijo que no.
                # Verificado en carne propia -- las pruebas de este reintento
                # agotaron la cuota de Gemini y las tres industrias siguientes
                # gastaron cuatro intentos cada una para recibir el mismo 429.
                # Un fallo de CUOTA corta la vuelta; uno de contenido no.
                if _es_falta_de_cuota(error):
                    espera = _segundos_de_espera(error)
                    if espera is not None and esperas_restantes > 0:
                        # Un limite POR MINUTO: vuelve solo. Esperar lo que el
                        # proveedor pide cuesta segundos y sale una vez cada
                        # 90 dias por industria, que es cuando se resuelve.
                        logger.info("cuota por minuto agotada; esperando %.0fs",
                                    espera)
                        time.sleep(espera)
                        esperas_restantes -= 1
                        continue
                    # Sin cifra de espera —"no credits remaining"— o ya se
                    # espero bastante: reintentar gasta peticiones contra el
                    # contador que acaba de rechazarte.
                    agotados.add(preguntar)
                continue
            datos = _json_del_texto(texto)
            if datos is None:
                continue
            vueltas_reales += 1
            candidato, _err = _validar(datos, industria)
            if candidato is None:
                continue
            tier = int(candidato.get("tam_source_tier") or 99)
            if tier < mejor_tier:
                mejor, mejor_tier = datos, tier
            if mejor_tier <= 2:
                return mejor, fallos
    if mejor is None and vueltas_reales:
        # Sólo si de verdad hubo intentos que evaluar. Si la cuota cortó en la
        # primera vuelta, decir "4 intentos" sería mentir sobre lo que pasó, y
        # este archivo existe para distinguir "nadie publica ese mercado" de
        # "no pude preguntar".
        fallos.append(f"{vueltas_reales} respuestas sin cifra atribuible")
    return mejor, fallos


_MUNDIAL = ("mundial", "worldwide", "global", "world")


def _validar(datos: dict, industria: str) -> tuple[dict | None, str]:
    """La respuesta convertida en overlay, o el motivo por el que no.

    Todo motivo se devuelve escrito y acaba en el archivo: un TAM ausente tiene
    que poder explicarse igual que uno presente.
    """
    tam = _numero(datos.get("tam"))
    if tam is None:
        return None, "ninguna asociacion ni casa de la lista publica este mercado"

    fuente = str(datos.get("tam_source") or "").strip()
    if not fuente:
        return None, "la cifra vino sin nombre de fuente"
    tier = _tier_de_la_fuente(fuente)
    if tier is None:
        return None, (f"fuente no aceptada: {fuente!r} — o es un agregador sin "
                      "metodologia firmada, o no esta en la lista")

    ambito = str(datos.get("ambito") or "").strip()
    if not any(m in ambito.lower() for m in _MUNDIAL):
        return None, (f"el ambito declarado es {ambito!r}, no mundial — un "
                      "denominador regional bajo ingresos globales le daba a "
                      "AAPL un 1.900% de participacion")

    cita = str(datos.get("cita") or "").strip()
    if not cita.startswith("http"):
        return None, f"la fuente {fuente!r} vino sin URL verificable"
    # Gemini envuelve sus citas en un redirect de grounding que caduca y no dice
    # de quién es la página. Sirve para llegar hoy, no para auditar en seis
    # meses: con el nombre de la fuente y la frase literal, la cifra se vuelve a
    # encontrar aunque el enlace muera.
    redirect = "vertexaisearch" in cita or "/grounding-api-" in cita
    textual = str(datos.get("cita_textual") or "").strip()
    if redirect and not textual:
        return None, (f"{fuente!r} sólo trajo un enlace de redirect que caduca, "
                      "y sin frase textual no hay forma de reencontrar la cifra")

    capa = str(datos.get("capa") or "").strip()
    if not capa:
        return None, ("no declaro QUE capa de la cadena mide — es lo que dejo a "
                      "NVDA con un TAM de gasto de usuario final durante semanas")

    # Un TAM es un FLUJO: cuánto factura ese mercado en un año. Lo que sigue
    # son ACERVOS —valor acumulado en un instante— y ninguno divide ingresos.
    #
    # Nareit contestó a REIT-Retail con "$252.000M, retail sector market
    # capitalization within the FTSE Nareit All Equity REITs index". Pasó los
    # cuatro filtros anteriores: cifra, fuente de tier 2, ámbito declarado
    # mundial y capa declarada. Y era la magnitud equivocada — capitalización
    # bursátil, no facturación. Realty Income factura $5.500M al año: contra
    # ese denominador su participación habría salido 2,2%, un número con
    # aspecto razonable y sin ningún significado.
    #
    # La capa no se declara sólo para que exista: se declara para poder
    # rechazarla cuando no es la que divide.
    _ACERVOS = ("market capitalization", "market cap", "capitalizacion",
                "capitalización", "enterprise value", "assets under management",
                "aum", "asset value", "net asset value", "installed base value",
                "total assets", "valor de los activos", "gdp",
                "producto interno bruto", "outstanding")
    _texto_capa = f"{capa} {fuente} {textual}".lower()
    for acervo in _ACERVOS:
        if acervo in _texto_capa:
            return None, (f"la cifra mide {acervo!r}, que es un acervo y no un "
                          "flujo anual: dividir ingresos de un año entre un "
                          "valor acumulado no da participacion de mercado")

    # La cifra se LEE de la fuente, no se acepta de memoria. Es la regla del
    # `judge.py` de Victor —"Nunca inventes cifras"— aplicada aqui, que era
    # donde faltaba. Ver `_verificar_en_la_fuente`.
    ok, detalle = _verificar_en_la_fuente(cita, tam, fuente)
    if not ok:
        return None, (f"la cifra no se pudo comprobar en su fuente: {detalle}. "
                      "Un TAM que nadie puede abrir no es evidencia, es un "
                      "recuerdo del modelo")

    fuera: dict[str, Any] = {
        "tam": int(tam),
        "_cita_verificada": detalle,
        "tam_source": fuente,
        "tam_source_tier": tier,
        "_ambito": "mundial",
        "_capa": capa,
        "_capa_coincide": str(datos.get("capa_coincide") or "").strip(),
        "_cita": cita,
        "_cita_textual": textual,
    }
    segunda = str(datos.get("segunda_fuente") or "").strip()
    if segunda and segunda.lower() not in ("null", "none", "-"):
        fuera["_segunda_fuente"] = segunda
    if redirect:
        fuera["_cita_es_redirect"] = ("Enlace de grounding: caduca. La cifra se "
                                      "reencuentra por fuente + frase textual.")

    historia = [n for n in (_numero(v) for v in (datos.get("tam_history") or []))
                if n is not None][-2:]
    if len(historia) == 2:
        # Medido: para JPM el modelo devolvió `[2024, 2025]` — los AÑOS donde
        # van los dólares. Nada aritmético lo delataba, y la participación
        # habría salido de dividir los ingresos entre 2024. Un mercado no
        # encoge a la décima parte ni se multiplica por diez en un año.
        plausible = all(tam / 10 <= n <= tam * 10 for n in historia)
        # El último punto ES el TAM: `_share_automatico` divide el año anterior
        # entre `historia[-2]` y el actual entre `tam`. Si no cerrara, las dos
        # mitades de la serie hablarían de mercados distintos.
        if plausible and abs(historia[-1] - tam) <= tam * 0.02:
            fuera["tam_history"] = [int(n) for n in historia]
        else:
            fuera["_sin_historia"] = (
                f"la serie {[int(n) for n in historia]} no acompana a un TAM "
                f"de {int(tam)}")

    patrones = [str(p).lower().strip() for p in (datos.get("segmento_patrones") or [])
                if str(p).strip()]
    if patrones:
        fuera["_segmento_patrones"] = patrones
    return fuera, ""


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


def _verificar_en_la_fuente(cita: str, tam: float, fuente: str) -> tuple[bool, str]:
    """Descarga la página citada y comprueba que la cifra ESTÁ ahí.

    Esto es la regla de Victor aplicada donde faltaba. Su `judge.py` usa el
    modelo para el TIER del TAM —clasificar la calidad de una fuente que ya
    existe— y su prompt lo dice sin rodeos: **"Nunca inventes cifras"**. Este
    módulo cruzaba esa línea: le pedía al modelo la cifra.

    Auditado sobre lo que había guardado, y no aguantó una comprobación:

      - `consumer-electronics` — $1,06 billones "de Omdia". El enlace daba 404
        a UN día de escrito. Nadie podía comprobar nada.
      - `credit-services` — la URL traía caracteres de control. Inservible.
      - `internet-content-information` — resolvía y la cifra aparecía, pero el
        destino era `bestmediainfo.com`, no PwC. Una nota de prensa *sobre* el
        dato, que es justo lo que el encabezado de este archivo dice que vino
        a evitar: "devolvía el comunicado de prensa sobre el dato en vez del
        dato".

    Nueve de diez citas eran redirects de grounding que caducan. Ni una
    apuntaba a la página de la fuente.

    Con esto, el modelo pasa a hacer lo que hace en el motor de Victor:
    ENCONTRAR la fuente. La cifra se lee del documento, y si el documento no
    la contiene, no hay número.
    """
    import urllib.request

    if not cita.startswith("http"):
        return False, "la cita no es una URL"
    try:
        req = urllib.request.Request(cita, headers={"User-Agent": _AGENTE})
        with urllib.request.urlopen(req, timeout=30) as r:
            destino = r.geturl()
            cuerpo = r.read(_MAX_BYTES_CITA).decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return False, f"la cita no se pudo abrir: {type(e).__name__}"

    texto = re.sub(r"<[^>]+>", " ", cuerpo)
    texto = re.sub(r"\s+", " ", texto)
    # La cifra se escribe de muchas formas: "$755.6 billion", "755,600",
    # "0.76 trillion". Se aceptan las representaciones habituales de ESTE
    # número, no un parecido cualquiera.
    formas = {f"{tam/1e9:,.0f}", f"{tam/1e9:.1f}", f"{tam/1e9:.2f}",
              f"{tam/1e12:.2f}", f"{tam/1e12:.1f}", f"{tam:,.0f}",
              f"{tam/1e6:,.0f}"}
    if not any(f in texto for f in formas if f and f != "0"):
        return False, f"la cifra no aparece en {destino[:70]}"

    # Y que la pagina sea de quien se dice. Sin esto, una nota de prensa de
    # tercero pasaba como si fuera el informe de PwC.
    # Y que la pagina sea DEL ORGANISMO, no de quien lo cuenta. Sin esta
    # linea las tres citas que sobrevivian apuntaban a prensa sectorial
    # -icaew.com, beveragedaily.com, bestmediainfo.com- y pasaban porque el
    # articulo mencionaba a Omdia, NielsenIQ o PwC. Eso es exactamente lo que
    # el encabezado de este archivo dice que vino a evitar: "el comunicado de
    # prensa SOBRE el dato en vez del dato".
    # Solo el nombre del ORGANISMO, no la descripcion del informe. La fuente
    # se registra como "PwC Global Entertainment & Media Outlook 2026-30": si
    # se buscan todas sus palabras, "media" encaja con `bestmediainfo.com` y
    # una nota de prensa pasa como si fuera PwC. Se corta en el primer
    # separador, que es donde acaba el nombre y empieza el titulo.
    organismo = re.split(r"[+(\-–—:,]", fuente)[0]
    partes = [w.lower() for w in re.split(r"[^A-Za-z]+", organismo) if len(w) > 2]
    dominio = re.sub(r"^https?://(www\.)?", "", destino.lower()).split("/")[0]
    # Solo la PRIMERA palabra: es el nombre del organismo. "PwC Global
    # Entertainment & Media Outlook" partido en palabras deja "media", que
    # encaja con `bestmediainfo.com` -- y una nota de prensa vuelve a colarse
    # como si fuera PwC. "pwc" no esta en ese dominio, y ahi acaba la duda.
    if not partes or partes[0] not in dominio:
        return False, (f"{dominio} no es el dominio de {fuente[:34]!r}: es "
                       "quien lo cuenta, no quien lo mide")
    return True, destino


def industrias_del_mercado(fmp: Any, minimo_empresas: int = 2) -> list[tuple[str, int]]:
    """Las industrias del mercado MUNDIAL, ordenadas por cuantas empresas
    cubren.

    Mundial y no solo EE.UU. a proposito: el TAM que este censo dispara
    tambien lo es. Con el universo limitado a NASDAQ+NYSE quedaban fuera
    industrias enteras -- automocion sin Toyota ni VW, lujo sin LVMH -- y por
    tanto sus TAM sin resolver, aunque un ticker americano de esa industria
    los necesitara igual.

    El orden importa porque cada TAM cuesta peticiones y el tier gratuito de
    Gemini da 20 por minuto: resolver primero `Semiconductors` (54 empresas)
    antes que una industria con dos deja mas cobertura por peticion gastada.

    Se excluyen ETF y fondos. Sin ese filtro, "Asset Management" salia con
    1.110 entradas y ninguna es una empresa operativa con un mercado que
    medir -- son vehiculos que cotizan, y su "industria" es una etiqueta del
    proveedor, no un mercado.
    """
    from collections import Counter

    filas = fmp.screener_universo()
    if not isinstance(filas, list):
        return []
    cuenta: Counter = Counter()
    for f in filas:
        if not isinstance(f, dict) or f.get("isEtf") or f.get("isFund"):
            continue
        if f.get("isActivelyTrading") is False:
            continue
        ind = (f.get("industry") or "").strip()
        if ind:
            cuenta[ind] += 1
    return [(n, c) for n, c in cuenta.most_common() if c >= minimo_empresas]


def resolver_todas_las_industrias(settings: Any, fmp: Any, *,
                                  limite: int = 0,
                                  minimo_empresas: int = 2) -> list[dict]:
    """Intenta resolver el TAM de cada industria del mercado que no lo tenga.

    Va en orden de cobertura -- primero las industrias con mas empresas --
    porque cada intento cuesta peticiones y la cuota es finita. Se corta sola
    en cuanto un proveedor dice que se acabo la cuota: seguir preguntando
    contra un contador agotado no resuelve nada y retrasa el resto.

    No repite lo ya resuelto ni toca lo de un analista. Lo que no verifique
    contra la pagina de su fuente NO se guarda como TAM -- eso es lo que
    distingue este barrido de la version que llenaba archivos con cifras que
    nadie podia abrir.
    """
    filas: list[dict] = []
    pausas = 0
    industrias = industrias_del_mercado(fmp, minimo_empresas)
    if limite:
        industrias = industrias[:limite]
    for nombre, empresas in industrias:
        mensaje = asegurar_tam_industria(settings, nombre, "")
        estado = ("resuelto" if "TAM mundial" in mensaje
                  else "ya estaba" if "vigente" in mensaje or "analista" in mensaje
                  else "sin fuente")
        filas.append({"industria": nombre, "empresas": empresas,
                      "estado": estado, "detalle": mensaje})
        # Que el mensaje NOMBRE una cuota no significa que el barrido este
        # bloqueado. Con dos proveedores, el fallo de uno viaja en el mismo
        # texto que las respuestas del otro.
        #
        # Medido: el barrido murio en la industria 8 de 137 por el "no credits
        # remaining" de OpenAI -- mientras Gemini contestaba, y el propio
        # mensaje lo decia: "3 respuestas sin cifra atribuible". Tres
        # respuestas son tres respuestas. Lo que faltaba era una fuente, no
        # cuota.
        respondio_alguien = "respuestas sin cifra atribuible" in mensaje
        if _es_falta_de_cuota(mensaje) and not respondio_alguien:
            # Un limite POR MINUTO no aborta un barrido de 132 industrias: le
            # marca el ritmo. El tier gratuito de Gemini da 20 peticiones por
            # minuto y su error dice cuanto falta, asi que se espera y se
            # sigue. Medido: sin esto la corrida entera moria en la PRIMERA
            # industria porque unas pruebas anteriores habian gastado la
            # cuota del minuto.
            #
            # Lo que si aborta es la cuota sin vuelta -- "no credits
            # remaining" no trae segundos porque no se arregla esperando.
            espera = _segundos_de_espera(mensaje)
            if espera is not None and pausas < PAUSAS_MAXIMAS_BARRIDO:
                logger.info("barrido en pausa %.0fs por cuota del minuto", espera)
                time.sleep(espera)
                pausas += 1
                continue
            filas.append({"industria": "(corte)", "empresas": 0,
                          "estado": "cuota agotada",
                          "detalle": "el resto queda para la proxima corrida"})
            break
    return filas


def revisar_tam_industrias(settings: Any, hoy: "date | None" = None,
                           forzar: bool = False) -> list[dict]:
    """Vuelve a comprobar cada TAM guardado contra la pagina de su fuente.

    Un TAM no es un hecho permanente: WSTS revisa sus ventas mundiales cada
    trimestre y la IEA su demanda cada mes. Un numero correcto en agosto puede
    estar viejo en noviembre, y el archivo no se entera solo.

    Esto es lo que hace posible que un TAM lo escriba el agente en vez del
    analista **sin volver a caer en el problema de origen**: la cifra no se
    recuerda, se vuelve a leer. Si sigue en la pagina, se refresca la fecha de
    revision. Si ya no esta, se dice -- y se dice fuerte, porque una cifra que
    su propia fuente ya no publica es exactamente lo que hay que revisar a
    mano.

    Tres cosas que NO hace, las tres a proposito:

    - **No borra un TAM porque la pagina fallara hoy.** Un timeout no es una
      correccion. Se anota el intento y se conserva el numero.
    - **No toca lo que escribio un analista sin verificar.** Un archivo sin
      `_verificado_por` es de su autor.
    - **No inventa el numero nuevo.** Si la cifra cambio, no adivina cual la
      sustituye: marca el archivo para revision y deja el anterior, que al
      menos se sabe de donde salio.

    Devuelve una fila por industria con lo que paso, para que la CLI y el
    reporte digan lo mismo.
    """
    from datetime import date as _date

    hoy = hoy or _date.today()
    raiz = Path(getattr(settings, "inputs_dir", None)
                or Path(getattr(settings, "repo_root", ".")) / "Entradas")
    carpeta = Path(raiz) / "_industrias"
    if not carpeta.is_dir():
        return []

    filas: list[dict] = []
    for path in sorted(carpeta.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            filas.append({"slug": path.stem, "estado": "ilegible"})
            continue
        if not isinstance(data, dict) or not data.get("tam"):
            continue
        # Solo lo que este motor verifico. Lo de un analista es suyo.
        if not data.get("_verificado_por"):
            filas.append({"slug": path.stem, "estado": "de un analista, no se toca"})
            continue
        if not forzar and not _toca_revisar(data, hoy):
            filas.append({"slug": path.stem, "estado": "vigente",
                          "desde": data.get("_verificado_en")})
            continue

        url = str(data.get("_cita_verificada") or data.get("_cita") or "")
        ok, detalle = _verificar_en_la_fuente(
            url, float(data["tam"]), str(data.get("tam_source") or ""))
        if ok:
            data["_verificado_en"] = hoy.isoformat()
            data.pop("_revisar_a_mano", None)
            filas.append({"slug": path.stem, "estado": "confirmado", "url": detalle})
        elif "no se pudo abrir" in detalle:
            # La fuente no respondio. Eso no corrige nada.
            data["_ultimo_intento"] = f"{hoy.isoformat()}: {detalle}"
            filas.append({"slug": path.stem, "estado": "fuente inaccesible",
                          "detalle": detalle})
        else:
            data["_revisar_a_mano"] = (
                f"{hoy.isoformat()}: {detalle}. La cifra guardada ya no aparece "
                "en la pagina de su fuente. Comprueba si el organismo la "
                "revisó y actualiza `tam` y `tam_history` a mano.")
            filas.append({"slug": path.stem, "estado": "CAMBIO", "detalle": detalle})
        _escribir(path, data)
    return filas


def _toca_revisar(data: dict, hoy: "date") -> bool:
    """Han pasado ya los dias que el archivo pide entre revisiones."""
    cada = int(data.get("_revisar_cada_dias") or REVISION_DIAS)
    try:
        ultima = datetime.fromisoformat(
            str(data.get("_verificado_en") or data.get("_resuelto_en"))).date()
    except (ValueError, TypeError):
        return True
    return (hoy - ultima) >= timedelta(days=cada)


def _vigente(data: dict, hoy: date) -> bool:
    try:
        d = datetime.fromisoformat(str(data.get("_resuelto_en"))).date()
    except (ValueError, TypeError):
        return False
    return (hoy - d) < timedelta(days=VIGENCIA_DIAS)


def asegurar_tam_industria(settings: Any, industria: str | None, ticker: str,
                           hoy: date | None = None, **_ignorado: Any) -> str:
    """Deja `Entradas/_industrias/<slug>.json` con el TAM mundial resuelto.

    Devuelve una frase con lo que pasó. Nunca levanta: un TAM que no se pudo
    resolver deja la dimensión NOT_SCORABLE, que es la respuesta honesta.

    Tres cosas que no hace, las tres a propósito: no toca un archivo escrito
    por un analista (sin `_generado_por` es suyo y gana siempre), no vuelve a
    preguntar mientras la respuesta siga vigente, y no borra un TAM que
    funcionaba porque la búsqueda de hoy falló.
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

    datos, fallos = _investigar(settings, industria or slug, ticker)
    if datos is None:
        motivo = "; ".join(fallos) or "sin respuesta de ningun proveedor"
        logger.warning("TAM de %s no resuelto: %s", slug, motivo)
        return f"{slug}: no se pudo resolver ({motivo})"

    overlay, error = _validar(datos, industria or slug)
    if overlay is None:
        logger.info("TAM de %s rechazado: %s", slug, error)
        if previo.get("tam"):
            return f"{slug}: rechazado ({error}), se conserva el anterior"
        try:
            _escribir(path, {
                "_generado_por": "vertex/tam_mundial",
                "_resuelto_en": hoy.isoformat(),
                "_sin_tam": error,
                "_visto_al_analizar": ticker,
                "_que_hacer": ("Escribe el TAM a mano en este archivo y borra "
                               "`_generado_por`: eso lo vuelve tuyo y el sistema "
                               "deja de tocarlo. Necesita `tam`, `tam_source` y "
                               "`tam_source_tier` (1-4)."),
            })
        except OSError:
            pass
        return f"{slug}: sin TAM ({error})"

    contenido = {
        "_generado_por": "vertex/tam_mundial",
        "_resuelto_en": hoy.isoformat(),
        "_como_se_obtuvo": (f"Resuelto al analizar {ticker}. Se revisa cada "
                            f"{VIGENCIA_DIAS} dias."),
        "_para_hacerlo_tuyo": ("Borra `_generado_por` y el sistema no vuelve a "
                               "tocar este archivo."),
        **overlay,
    }
    if previo.get("_aplica_a"):
        contenido["_aplica_a"] = previo["_aplica_a"]
    try:
        _escribir(path, contenido)
    except OSError as e:
        return f"{slug}: no se pudo guardar ({type(e).__name__})"
    return (f"{slug}: TAM mundial ${int(overlay['tam']):,} de "
            f"{overlay['tam_source']} (tier {overlay['tam_source_tier']})")
