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
#: Cuantos anos hacia atras puede tener la cifra de un TAM.
#:
#: 1 = el ano en curso o el anterior. Un estudio de mercado se publica con
#: retraso, asi que en agosto de 2026 la ultima cifra disponible puede ser
#: legitimamente de 2025; de 2024 ya no.
#:
#: `DATA_POLICY.md` da 18 meses para un "annual market-size study" antes de
#: exigir corroboracion. Esta regla es mas estricta y se prefiere: 18 meses
#: contados desde una fecha de publicacion que la fuente no siempre declara
#: se vuelve inauditable, mientras que el ANO del dato viene en la propia
#: respuesta.
#:
#: Medido: el barrido acepto hoy dos TAM citando 2023 --IDC para hardware y
#: para videojuegos-- o sea cifras de hace tres anos entrando como si fueran
#: del ultimo ano publicado. El campo `tam_anio` se le pedia al modelo desde
#: siempre y NO se leia en ninguna parte.
ANOS_MAXIMOS_DE_ANTIGUEDAD = 1

INTENTOS = 4

#: El tier gratuito de Gemini permite 20 peticiones por minuto y su propio
#: error dice cuanto falta. Esperarlo cuesta segundos y sale UNA vez cada 90
#: dias por industria; no esperarlo deja la industria sin TAM tres meses.
ESPERA_MAXIMA_S = 65.0
ESPERAS_MAXIMAS = 3

#: Hueco minimo entre peticiones al proveedor de busqueda. 20 por minuto es
#: una cada 3 segundos: respetarlo por adelantado es mas rapido que chocar con
#: el limite y dormir 65 segundos despues.
SEGUNDOS_ENTRE_LLAMADAS = 3.1

#: Industrias en vuelo a la vez. Cada busqueda con grounding tarda 15-30s, asi
#: que en serie se gastaban 4 peticiones por minuto de las 20 permitidas. Con
#: 8 en paralelo se llena la cuota que ya estaba pagada, y `_esperar_turno()`
#: impide pasarse por mucho que empujen los hilos.
HILOS_BARRIDO = 8

#: Cuantas pausas por cuota aguanta un barrido completo antes de rendirse. A
#: 20 peticiones por minuto, 132 industrias necesitan esperar muchas veces;
#: rendirse a la primera dejaba el barrido muerto en la industria 1.
PAUSAS_MAXIMAS_BARRIDO = 200

#: Para comprobar la cita contra su propia pagina.
_AGENTE = "Vertex Research vertexholgroup@gmail.com"
#: Un PDF cortado a la mitad es ILEGIBLE, no parcialmente legible: su tabla de
#: objetos vive al final del fichero, asi que truncarlo rompe el documento
#: entero. Medido con el informe `Electricity2025` de la IEA -- a 6 MB daba
#: "incorrect startxref pointer" y 0 caracteres extraidos.
_MAX_BYTES_CITA = 40_000_000

#: Paginas del PDF que se leen. La cifra de cabecera de un informe va en el
#: resumen ejecutivo; leer 300 paginas para encontrarla cuesta segundos y no
#: mejora nada.
_MAX_PAGINAS_PDF = 25

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
    # --- Organismos anadidos tras medir 40 industrias en tres sectores ---
    # El metodo ya se probo: al meter NAREIT y la EIA, `oil-gas-integrated`
    # resolvio tras llevar semanas marcada como imposible. Lo que fallaba no
    # era el mercado, era que su organismo no estaba en esta lista.
    #
    # Seguros: Swiss Re Institute publica el sigma, EL conjunto de datos
    # canonico de primas mundiales por ramo. Sin el, siete industrias de
    # seguros no tenian a quien preguntar.
    "swiss re", "sigma", "geneva association", "munich re",
    # Gestion de activos y mercados de capitales.
    "efama", "investment company institute", "ici ",
    "world federation of exchanges", "wfe ", "iosco", "isda", "afme",
    # Biotecnologia y dispositivos medicos. Biotechnology es la industria mas
    # grande del censo -- 118 acciones -- y no tenia organismo en la lista.
    "biotechnology innovation organization", "bio ", "ifpma",
    "medtech europe", "advamed", "cocir",
    # Industria y materiales.
    "world steel association", "international aluminium", "icmm",
    "international fertilizer", "cembureau",
    # Consumo y distribucion.
    "world federation of advertisers", "national retail federation", "nrf ",
    "food and agriculture organization", "fao ",
    # Energia y utilities.
    "world nuclear association", "global wind energy council", "gwec",
    "international hydropower", "eurelectric",
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

REGLA 5 — LA CITA ES DE LA FUENTE, NO DE QUIEN LA CUENTA. El enlace tiene que
   ser una página del PROPIO organismo: wsts.org para WSTS, iea.org para la
   IEA, pwc.com para PwC. Una nota de prensa de un medio que menciona la cifra
   NO sirve, aunque la cifra sea correcta.

   Esto no es una preferencia: el motor DESCARGA esa página y comprueba dos
   cosas antes de aceptar el número —que la cifra aparezca en el texto y que
   el dominio sea el del organismo—. Una cita de un tercero se rechaza
   automáticamente y el trabajo se pierde.

   Si sólo encuentras la cifra en prensa y no en la web del organismo,
   responde {{"tam": null}}: es preferible a una atribución que nadie puede
   comprobar.

Responde SOLO con este JSON, sin texto alrededor:
{{
  "tam": <mercado mundial del ultimo ano publicado, en USD, entero>,
  "tam_anio": <ano de esa cifra>,
  "tam_history": [<mercado del ano anterior, en USD>, <el mismo numero de "tam">],
  "tam_source": "<asociacion o casa + nombre del dato + ano>",
  "cita": "<URL de una pagina DEL PROPIO organismo, no de prensa>",
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


#: Reloj compartido del ritmo. Una sola llamada en vuelo a la vez y un hueco
#: minimo entre ellas: es lo que convierte el limite en una cadencia en vez de
#: en un choque.
_RITMO = threading.Lock()
_ULTIMA_LLAMADA = [0.0]


def _esperar_turno() -> None:
    """Espacia las llamadas para NO chocar con el limite, en vez de chocar y
    dormir despues.

    El tier gratuito da 20 peticiones por minuto = una cada 3 segundos. El
    codigo llamaba a toda velocidad, se comia el 429 y entonces dormia hasta
    65 segundos -- y esas esperas se componen, 3 por intento y 4 intentos por
    industria.

    Medido: el barrido de 146 industrias pasaba nueve minutos sin resolver
    UNA. Su coste teorico a 20/min es de unos 30 minutos en total.

    Esperar 3 segundos ANTES de llamar cuesta lo mismo que la cuota permite y
    no gasta peticiones en respuestas que van a fallar.
    """
    with _RITMO:
        falta = SEGUNDOS_ENTRE_LLAMADAS - (time.monotonic() - _ULTIMA_LLAMADA[0])
        if falta > 0:
            time.sleep(falta)
        _ULTIMA_LLAMADA[0] = time.monotonic()


def _preguntar_gemini(settings: Any, prompt: str) -> tuple[str, str | None]:
    key = getattr(settings, "gemini_api_key", None)
    if not key:
        return "", "GEMINI_API_KEY no configurada"
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        return "", "el SDK google-genai no esta instalado"
    _esperar_turno()
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


PROMPT_CAPA = """Eres un analista de research. Tengo un tamano de mercado y
necesito saber si mide LA CAPA en la que {ticker} factura.

Cifra:      {tam}
Fuente:     {fuente}
Capa que declara medir: {capa}
Ingresos anuales reportados por {ticker}: {ingresos}

Una capa es un eslabon de la cadena de valor. El error que buscamos es este:

  - Gartner dio "gasto mundial en dispositivos" para NVIDIA. Cifra correcta,
    pero mide lo que paga el USUARIO FINAL, y NVIDIA vende chips a fabricantes.
  - NielsenIQ dio "valor de venta al publico de bebidas" para Coca-Cola.
    Correcta, pero KO factura CONCENTRADO a embotelladores.
  - SIPRI dio "gasto militar mundial" para una empresa de defensa. Correcta,
    pero incluye salarios y operaciones; la empresa factura ARMAMENTO.

En los tres la cifra era buena y la capa estaba equivocada, y el denominador
salia varias veces mas grande de lo que corresponde.

Responde SOLO con este JSON:
{{
  "veredicto": "<COINCIDE | CAPA_DISTINTA | NO_SE_PUEDE_SABER>",
  "porque": "<una frase: que mide la cifra y donde factura la empresa>"
}}

COINCIDE       si la cifra mide el eslabon donde la empresa cobra.
CAPA_DISTINTA  si mide otro eslabon -- gasto del usuario final cuando la
               empresa vende a fabricantes, o al reves, o un agregado mucho
               mas ancho que lo que la empresa vende.
NO_SE_PUEDE_SABER si la descripcion no basta. No adivines."""


def _ingresos_del_ticker(settings: Any, ticker: str) -> float | None:
    """Los ingresos anuales del ticker, para contrastarlos con la cifra.

    Sin ellos el juez decide sobre descripciones; con ellos ve que una empresa
    que factura $5.500M contra un mercado de $252.000M o bien tiene el 2% o
    bien esta comparando dos cosas distintas.
    """
    if not ticker:
        return None
    try:
        from wbj.providers.cache import Cache
        from wbj.providers.fmp import FMPProvider

        filas = FMPProvider(settings, Cache(settings.cache_dir)).income_annual(ticker)
        if isinstance(filas, list) and filas:
            v = (filas[0] or {}).get("revenue")
            return float(v) if v else None
    except Exception:  # noqa: BLE001 -- sin ingresos el juez sigue pudiendo opinar
        logger.info("sin ingresos de %s para contrastar la capa", ticker)
    return None


def _juzgar_capa(settings: Any, datos: dict, ticker: str,
                 ingresos: float | None) -> tuple[bool, str]:
    """Pregunta si la cifra mide la capa donde la empresa factura.

    Es lo que ninguna comprobacion mecanica alcanza. Los cuatro errores de
    capa que ha tenido este modulo -- Gartner con NVDA, NAREIT con O,
    NielsenIQ con KO, SIPRI con defensa -- tenian TODOS la cifra correcta,
    verificada en la pagina de su organismo, con ambito mundial y capa
    declarada. Lo que fallaba era si esa capa es donde la empresa cobra, y eso
    es una pregunta cualitativa.

    La lista de acervos prohibidos (`market capitalization`, `AUM`, `GDP`...)
    cubre los casos que ya conocemos y no habria atrapado el de SIPRI: "gasto
    militar mundial" no es un acervo, es un flujo -- pero de otra capa.

    Ante la duda NO se rechaza. Un `NO_SE_PUEDE_SABER` deja pasar la cifra con
    su capa declarada a la vista, que es como estaba antes de existir esto:
    el juez solo puede quitar TAM malos, nunca bloquear buenos por timidez.
    """
    if not ticker:
        return True, "sin ticker de referencia, no hay capa que contrastar"
    prompt = PROMPT_CAPA.format(
        ticker=ticker, tam=f"${int(datos.get('tam') or 0):,}",
        fuente=datos.get("tam_source"), capa=datos.get("capa"),
        ingresos=f"${int(ingresos):,}" if ingresos else "no disponibles")
    texto, error = _preguntar_gemini(settings, prompt)
    if error:
        return True, f"no se pudo consultar la capa ({error[:40]})"
    r = _json_del_texto(texto) or {}
    veredicto = str(r.get("veredicto") or "").strip().upper()
    porque = str(r.get("porque") or "").strip()
    if veredicto == "CAPA_DISTINTA":
        return False, porque or "el juez leyo otra capa"
    return True, porque


def _anio_del_tam(datos: dict) -> int | None:
    """El ano de la cifra, del campo o del nombre de la fuente.

    `tam_anio` se le pide al modelo en el PROMPT desde siempre y no se leia en
    ningun sitio. Cuando no lo devuelve, el ano suele venir dentro de
    `tam_source` --"IDC + Worldwide Server Market Revenue + 2023"-- porque el
    prompt pide "asociacion o casa + nombre del dato + ano".

    Se toma el MAYOR ano plausible del nombre: una fuente puede citar un rango
    ("2024-2025") y lo que interesa es a que ano corresponde la cifra.
    """
    import re as _re_anio

    bruto = datos.get("tam_anio")
    try:
        n = int(str(bruto).strip()[:4])
        if 1990 <= n <= date.today().year + 1:
            return n
    except (TypeError, ValueError):
        pass
    anos = [int(a) for a in _re_anio.findall(r"(19\d{2}|20\d{2})",
                                             str(datos.get("tam_source") or ""))]
    anos = [a for a in anos if a <= date.today().year + 1]
    return max(anos) if anos else None


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

    anio = _anio_del_tam(datos)
    if anio is None:
        return None, ("la cifra vino sin ano: sin saber de cuando es no se "
                      "puede juzgar si sigue vigente")
    _limite = date.today().year - ANOS_MAXIMOS_DE_ANTIGUEDAD
    if anio < _limite:
        return None, (f"la cifra es de {anio} y el limite es {_limite}: un TAM "
                      "de hace mas de un ano no describe el mercado de hoy")

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
        "tam_anio": anio,
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


def _sin_contradiccion(contenido: dict) -> dict:
    """Una empresa no puede competir ENTERA y a la vez tener patrones que
    eligen un trozo de ella.

    Si `_ingreso_relevante` es `total`, el numerador es toda la facturacion y
    los patrones no eligen nada: sobran. Y si eligen algo, entonces la empresa
    no compite entera. Declarar las dos cosas deja el numerador ambiguo, que es
    justo lo que no puede quedar a interpretacion.

    Podia pasar porque los juicios de la capa se CONSERVAN de la resolucion
    anterior —para que revisar la cifra no borre el numerador— mientras
    `_validar` anade los patrones nuevos que devuelve el modelo. Las dos cosas
    son correctas por separado y juntas se contradicen. Paso el 29/08/2026 con
    `drug-manufacturers-general`: el archivo decia «la facturacion de una
    farmaceutica general son ventas de medicamentos, que es la capa del TAM» y
    ademas traia cuatro patrones. Lo cazo el guardian del motor.

    Manda `_ingreso_relevante`, que es el juicio explicito y viene con su
    `_porque` escrito; los patrones son el mecanismo, y un mecanismo que no
    elige nada es ruido.
    """
    if contenido.get("_ingreso_relevante") == "total" and contenido.get("_segmento_patrones"):
        contenido = dict(contenido)
        contenido.pop("_segmento_patrones", None)
    return contenido


def _escribir(path: Path, contenido: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    contenido = _sin_contradiccion(contenido)
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


def _texto_del_documento(crudo: bytes, url: str) -> str:
    """El texto de la pagina, sea HTML o PDF.

    Los organismos publican sus cifras en PDF mas de lo que parece: la IEA
    enlaza `Electricity2025.pdf`, EFAMA su Fact Book, IQVIA sus informes del
    Institute. Sin leerlos, "la cifra no aparece en la pagina" era verdad y
    enganaba -- estaba en el documento que la pagina enlaza.

    Se detecta por la cabecera del propio archivo (`%PDF-`) y no por la
    extension de la URL, porque muchos CDN sirven PDFs desde rutas sin `.pdf`.
    """
    if crudo[:5] == b"%PDF-":
        try:
            import io

            from pypdf import PdfReader

            lector = PdfReader(io.BytesIO(crudo))
            partes = []
            for pagina in lector.pages[:_MAX_PAGINAS_PDF]:
                try:
                    partes.append(pagina.extract_text() or "")
                except Exception:  # noqa: BLE001 -- una pagina rota no anula el resto
                    continue
            texto = " ".join(partes)
        except Exception as e:  # noqa: BLE001
            logger.info("PDF ilegible en %s: %s", url[:60], type(e).__name__)
            return ""
    else:
        texto = re.sub(r"<[^>]+>", " ", crudo.decode("utf-8", "replace"))
    return re.sub(r"\s+", " ", texto).strip()


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
            cuerpo = r.read(_MAX_BYTES_CITA)
    except Exception as e:  # noqa: BLE001
        return False, f"la cita no se pudo abrir: {type(e).__name__}"

    texto = _texto_del_documento(cuerpo, destino)
    if not texto:
        return False, f"no se pudo leer el documento en {destino[:60]}"
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
    # El CDN del organismo cuenta como suyo. La IEA sirve su informe desde
    # `iea.blob.core.windows.net` y EFAMA desde su propio almacen: la cifra es
    # de ellos, el fichero es su publicacion oficial, y rechazarla por donde
    # esta alojada habria tirado el dato correcto. Se admite cuando el nombre
    # del organismo aparece en CUALQUIER parte del enlace -- ruta incluida --
    # y no solo en el dominio.
    #
    # Sigue sin colar una nota de prensa: `bestmediainfo.com/insights/...` no
    # lleva "pwc" ni en el dominio ni en la ruta.
    _enlace = destino.lower()
    if not partes or (partes[0] not in dominio and partes[0] not in _enlace):
        return False, (f"{dominio} no es el dominio de {fuente[:34]!r}: es "
                       "quien lo cuenta, no quien lo mide")
    return True, destino


def industrias_del_mercado(fmp: Any, minimo_empresas: int = 2,
                           sector: str | None = None) -> list[tuple[str, int]]:
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

    filas = fmp.screener_universo(sector=sector)
    if not isinstance(filas, list):
        return []
    cuenta: Counter = Counter()
    # El ticker MAYOR de cada industria, para que el juez de capa tenga contra
    # que contrastar. Sin el respondia "sin ticker de referencia" y no opinaba
    # -- justo en el barrido, que es donde mas se usa. `consumer-electronics`
    # resolvio asi con "ventas minoristas mundiales", que es valor al publico
    # cuando Apple factura a canal: la misma forma del error de Coca-Cola que
    # el juez SI atrapa cuando se le da la empresa.
    mayor: dict[str, tuple[float, str]] = {}
    for f in filas:
        if not isinstance(f, dict) or f.get("isEtf") or f.get("isFund"):
            continue
        if f.get("isActivelyTrading") is False:
            continue
        ind = (f.get("industry") or "").strip()
        if not ind:
            continue
        cuenta[ind] += 1
        cap = float(f.get("marketCap") or 0)
        sim = str(f.get("symbol") or "")
        if sim and cap > mayor.get(ind, (0.0, ""))[0]:
            mayor[ind] = (cap, sim)
    return [(n, c, mayor.get(n, (0.0, ""))[1])
            for n, c in cuenta.most_common() if c >= minimo_empresas]


def _clasificar(mensaje: str) -> str:
    """El estado que resume lo que `asegurar_tam_industria` acaba de decir."""
    if "TAM mundial" in mensaje:
        return "resuelto"
    # "sin TAM" gana sobre "vigente": un sello vigente SIN denominador no es
    # una industria resuelta, y llamarla "ya estaba" es lo que inflaba los
    # conteos.
    if "sin TAM" in mensaje:
        return "sin fuente"
    if "vigente" in mensaje or "analista" in mensaje:
        return "ya estaba"
    return "sin fuente"


def resolver_todas_las_industrias(settings: Any, fmp: Any, *,
                                  limite: int = 0,
                                  minimo_empresas: int = 2,
                                  hilos: int = 0,
                                  sector: str | None = None) -> list[dict]:
    """Resuelve el TAM de cada industria del mercado que no lo tenga.

    Va en orden de cobertura -- primero las industrias con mas empresas --
    porque `Banks - Regional` cubre 115 acciones y una industria de dos cubre
    dos. Lo ya resuelto no se repite y lo de un analista no se toca.

    **En paralelo, y esa es la diferencia entre media hora y dos horas y
    media.** Medido: dos industrias tardaron 113 segundos, o sea ~1 minuto
    cada una... gastando 4 peticiones por minuto de las 20 que el proveedor
    permite. El cuello no era la cuota sino la LATENCIA: cada busqueda con
    grounding tarda entre 15 y 30 segundos, y se hacian de una en una.

    Con varias en vuelo se usa la cuota que ya estaba pagada. Lo que impide
    pasarse es `_esperar_turno()`, que serializa la salida de cada peticion
    con un hueco de `SEGUNDOS_ENTRE_LLAMADAS` bajo un lock compartido: da
    igual cuantos hilos empujen, por el cuello sale una cada 3,1 segundos.

    Lo que no se pierde al paralelizar: el orden de cobertura se conserva en
    el resultado, y una cuota SIN VUELTA -- "no credits remaining" -- sigue
    abortando, ahora con una bandera compartida para que los hilos que quedan
    no gasten peticiones contra un contador muerto.
    """
    from concurrent.futures import ThreadPoolExecutor

    industrias = industrias_del_mercado(fmp, minimo_empresas, sector)
    if limite:
        industrias = industrias[:limite]
    if not industrias:
        return []

    n = hilos or HILOS_BARRIDO
    abortado = threading.Event()

    def _una(par: tuple) -> dict:
        nombre, empresas = par[0], par[1]
        referencia = par[2] if len(par) > 2 else ""
        if abortado.is_set():
            return {"industria": nombre, "empresas": empresas,
                    "estado": "no intentada", "detalle": "corte por cuota"}
        mensaje = asegurar_tam_industria(settings, nombre, referencia)
        # Que el mensaje NOMBRE una cuota no significa que el barrido este
        # bloqueado: con dos proveedores, el fallo de uno viaja en el mismo
        # texto que las respuestas del otro. Solo aborta si NADIE respondio.
        if (_es_falta_de_cuota(mensaje)
                and "respuestas sin cifra atribuible" not in mensaje
                and _segundos_de_espera(mensaje) is None):
            abortado.set()
        return {"industria": nombre, "empresas": empresas,
                "estado": _clasificar(mensaje), "detalle": mensaje}

    with ThreadPoolExecutor(max_workers=n) as pool:
        filas = list(pool.map(_una, industrias))

    if abortado.is_set():
        filas.append({"industria": "(corte)", "empresas": 0,
                      "estado": "cuota agotada",
                      "detalle": "el resto queda para la proxima corrida"})
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


def _version_de_fuentes() -> int:
    """Cuantos organismos conoce el resolutor ahora mismo.

    Va en cada sello para que un "no hay fuente" caduque cuando la lista
    crece. Es el numero de nombres, no un hash del contenido: lo que invalida
    un veredicto es que haya MAS sitios donde preguntar, y quitar uno no
    resucita una industria que ya fallo con el.
    """
    return len(ASOCIACIONES) + len(CASAS)


def _vigente(data: dict, hoy: date) -> bool:
    """Sigue fresco, mirando la fecha mas reciente que el archivo tenga.

    Antes solo leia `_resuelto_en`. Un archivo escrito a mano y verificado
    lleva `_verificado_en` en su lugar, asi que salia "no vigente" y el
    barrido lo re-resolvia -- medido: sobrescribio el TAM de WSTS que se habia
    puesto y leido de su fuente esa misma tarde.
    """
    # Un "sin fuente" caduca en cuanto la lista de organismos crece. Sin
    # esto, anadir Swiss Re, BIO o EFAMA no servia de nada durante 90 dias:
    # las industrias que ya habian fallado seguian diciendo "ya lo intente"
    # contra una lista que ya no era la misma, y habia que borrar los
    # archivos a mano para que se reintentaran.
    #
    # Solo afecta a los sellos SIN TAM. Uno que resolvio no se reabre porque
    # aparezca otro organismo: la cifra que tiene sigue siendo buena, y para
    # eso esta `revisar_tam_industrias`.
    if not data.get("tam"):
        antes = data.get("_fuentes_conocidas")
        if not isinstance(antes, int) or antes < _version_de_fuentes():
            return False

    fechas = []
    for clave in ("_verificado_en", "_resuelto_en"):
        try:
            fechas.append(datetime.fromisoformat(str(data.get(clave))).date())
        except (ValueError, TypeError):
            continue
    if not fechas:
        return False
    return (hoy - max(fechas)) < timedelta(days=VIGENCIA_DIAS)


def asegurar_tam_industria(settings: Any, industria: str | None, ticker: str,
                           hoy: date | None = None, *, investigar: bool = True,
                           **_ignorado: Any) -> str:
    """Deja `Entradas/_industrias/<slug>.json` con el TAM mundial resuelto.

    Devuelve una frase con lo que pasó. Nunca levanta: un TAM que no se pudo
    resolver deja la dimensión NOT_SCORABLE, que es la respuesta honesta.

    Tres cosas que no hace, las tres a propósito: no toca un archivo escrito
    por un analista (sin `_generado_por` es suyo y gana siempre), no vuelve a
    preguntar mientras la respuesta siga vigente, y no borra un TAM que
    funcionaba porque la búsqueda de hoy falló.

    `investigar=False` LEE lo que haya en disco y no sale a buscar. Es lo que
    usa un análisis interactivo, y la razón está medida: resolver un TAM tarda
    **147 segundos** —cuatro intentos, y sobre todo el verificador abriendo la
    página de cada fuente candidata para comprobar que la cifra está ahí— y eso
    era el 96% de lo que tardaba analizar una acción. Las llamadas a Gemini y
    OpenAI son sólo 7,6 s de esos 147; el resto es descarga.

    Resolver el TAM es trabajo de INDUSTRIA, no de ticker: el barrido
    (`wbj tam-todas`) existe justo para eso y lo hace una vez para las 145.
    Hacerlo dentro de un análisis se lo cobra a quien pulsó Analyze, y se lo
    vuelve a cobrar a cada compañera de industria que llegue después.
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
            # Distinguir un TAM vigente de un SELLO vigente. Los dos se saltan
            # -- no se vuelve a preguntar -- pero significan cosas opuestas:
            # uno tiene denominador y el otro dice que no se encontro.
            #
            # Decir "vigente" a los dos inflo TODOS los conteos de esta
            # sesion. `Software - Application`, `Software - Services` y `Solar`
            # se reportaron como "ya estaba" sin tener TAM, y hubo que
            # corregir el numero a la baja tres veces -- en Healthcare, en
            # Technology y en el total.
            if previo.get("tam"):
                return f"{slug}: TAM vigente desde {previo.get('_resuelto_en')}"
            return (f"{slug}: sin TAM, intentado el {previo.get('_resuelto_en')} "
                    f"({str(previo.get('_sin_tam'))[:60]})")

    if not investigar:
        # No hay archivo (o caduco) y no se puede salir a buscar: se dice, y la
        # dimension queda sin denominador. Es la respuesta honesta y cuesta 0s.
        return (f"{slug}: sin TAM en disco; no se investiga durante un analisis "
                f"(corre `wbj tam-todas` para resolver las industrias)")

    datos, fallos = _investigar(settings, industria or slug, ticker)
    if datos is None:
        motivo = "; ".join(fallos) or "sin respuesta de ningun proveedor"
        logger.warning("TAM de %s no resuelto: %s", slug, motivo)
        # Sellar el intento, para que el barrido de 137 industrias sea
        # OBSERVABLE mientras corre. Antes esta rama no escribia nada: una
        # industria que no conseguia respuesta no dejaba rastro en disco, solo
        # en el JSON final. Medido: nueve minutos de barrido sin un solo
        # archivo nuevo, sin forma de saber si avanzaba o estaba colgado.
        #
        # PERO no se sella un fallo de CUOTA. Un sello lleva fecha, y la fecha
        # hace que `_vigente` salte esa industria 90 dias -- tres meses sin
        # TAM por un limite de veinte peticiones por minuto que se pasa en
        # diecisiete segundos. Se distingue con el mismo `_es_falta_de_cuota`
        # que usa el barrido para decidir si esperar o rendirse.
        # "Nadie respondio" es lo que decide, no que el texto NOMBRE una
        # cuota. Con dos proveedores el fallo de uno viaja en el mismo mensaje
        # que el trabajo del otro: con OpenAI sin creditos, TODOS los motivos
        # llevan su "no credits remaining" dentro, asi que
        # `_es_falta_de_cuota(motivo)` daba True siempre y no se sellaba nunca
        # nada.
        #
        # Medido: el barrido resolvio 8 industrias -- 5 de ellas "sin fuente"
        # -- y el contador de archivos no se movio de 13. El sello que se
        # anadio para poder observar el barrido no llego a escribirse ni una
        # vez.
        #
        # Es el mismo error que ya se corrigio en el corte del barrido, y se
        # arregla con la misma frase: si hubo respuestas, hubo pregunta.
        # ...pero "hubo respuestas" no es lo mismo que "hubo un intento
        # SERIO", y esa diferencia sellaba industrias por 90 dias sobre nada.
        #
        # Medido sobre los 43 archivos: 27 llevan un motivo MIXTO --cuota mas
        # alguna respuesta-- y el numero de respuestas reales es 1, 2 o 3 de
        # los 8 intentos posibles (INTENTOS=4 por dos proveedores).
        # `aerospace-defense` quedo marcada "sin fuente" hasta noviembre con
        # UNA respuesta de ocho; las otras siete murieron en 429.
        #
        # Lo que eso contradice es el razonamiento de este mismo modulo, tres
        # docstrings mas arriba: "Un `null` no era prueba... preguntando
        # cuatro veces por Consumer Electronics el SEGUNDO intento devolvio
        # $783.000M de Gartner y valido. Los otros tres fueron `null`". Si
        # cuatro intentos existen porque uno no basta, un sello de 90 dias
        # no puede firmarse con uno.
        #
        # Asi que se exige que hayan corrido al menos `INTENTOS` respuestas de
        # verdad. Por debajo de eso la corrida no probo nada: no se sella y se
        # reintenta la proxima vez, que es lo unico honesto que se puede hacer
        # con una tirada que la cuota interrumpio.
        _respondieron = "respuestas sin cifra atribuible" in motivo
        _n_respuestas = 0
        if _respondieron:
            import re as _re_resp
            _m = _re_resp.search(r"(\d+) respuestas sin cifra", motivo)
            _n_respuestas = int(_m.group(1)) if _m else 0
        _intento_serio = _n_respuestas >= INTENTOS
        if not previo and (_intento_serio or not _es_falta_de_cuota(motivo)):
            try:
                _escribir(path, {
                    "_generado_por": "vertex/tam_mundial",
                    "_resuelto_en": hoy.isoformat(),
                    "_sin_tam": motivo,
                    "_fuentes_conocidas": _version_de_fuentes(),
                    "_visto_al_analizar": ticker,
                    "_que_hacer": ("Escribe el TAM a mano en este archivo y borra "
                                   "`_generado_por`: eso lo vuelve tuyo y el sistema "
                                   "deja de tocarlo. Necesita `tam`, `tam_source` y "
                                   "`tam_source_tier` (1-4)."),
                })
            except OSError:
                pass
        return f"{slug}: no se pudo resolver ({motivo})"

    # Los juicios sobre la CAPA sobreviven a un cambio de cifra. Que WSTS
    # revise sus ventas no cambia que un fabricante de chips compita entero en
    # ese mercado, ni a quien cubre el archivo. Sin esto, el barrido borro
    # `_ingreso_relevante` al re-resolver y AMD perdio su numerador: su
    # cobertura de Market cayo de 0,71 a 0,46 sin que nadie tocara una formula.
    _juicios = {k: previo[k] for k in
                ("_ingreso_relevante", "_ingreso_relevante_porque",
                 "_aplica_a", "_segmento_patrones", "_revisar_cada_dias")
                if previo.get(k) is not None}

    overlay, error = _validar(datos, industria or slug)

    # El juez de la capa va DESPUES de la validacion mecanica y solo sobre el
    # candidato que la paso: es una llamada mas al modelo, y gastarla en cifras
    # que ya se van a rechazar seria tirar cuota. Ver `_juzgar_capa`.
    if overlay is not None:
        _ok_capa, _porque = _juzgar_capa(settings, datos, ticker,
                                         _ingresos_del_ticker(settings, ticker))
        if not _ok_capa:
            logger.info("TAM de %s rechazado por capa: %s", slug, _porque)
            if previo.get("tam"):
                return (f"{slug}: rechazado por capa ({_porque[:70]}), "
                        "se conserva el anterior")
            overlay, error = None, f"capa distinta: {_porque}"
        elif _porque:
            overlay["_capa_juzgada"] = _porque
    if overlay is None:
        logger.info("TAM de %s rechazado: %s", slug, error)
        if previo.get("tam"):
            return f"{slug}: rechazado ({error}), se conserva el anterior"
        try:
            _escribir(path, {
                "_generado_por": "vertex/tam_mundial",
                "_resuelto_en": hoy.isoformat(),
                "_sin_tam": error,
                "_fuentes_conocidas": _version_de_fuentes(),
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
        # Los juicios sobre la capa van DESPUES del overlay: son del archivo,
        # no de la respuesta, y una cifra nueva no los revoca.
        **_juicios,
    }
    try:
        _escribir(path, contenido)
    except OSError as e:
        return f"{slug}: no se pudo guardar ({type(e).__name__})"
    return (f"{slug}: TAM mundial ${int(overlay['tam']):,} de "
            f"{overlay['tam_source']} (tier {overlay['tam_source_tier']})")
