"""Cuentas, perfiles por usuario y el cuestionario del inversionista.

Tres cosas que antes no existían o estaban mal:

1. **Cuentas de verdad.** El login vivía entero en `localStorage`, con las
   contraseñas **en texto plano** y una base de datos por navegador. Nadie podía
   entrar desde otro dispositivo porque su cuenta no existía fuera de ese
   Chrome. Aquí las cuentas viven en SQLite, la contraseña se guarda como hash
   PBKDF2-HMAC-SHA256 con sal por usuario, y la sesión es un token aleatorio
   cuyo **hash** es lo único que toca el disco: filtrar la base no entrega
   sesiones vivas.

2. **Un perfil por usuario.** Antes había un solo `perfil.json` global. Ahora
   cada cuenta tiene el suyo, y `Perfil Inversionista/usuarios/<id>.md` es el
   archivo que el agente de acciones lee para ESA persona.

3. **El cuestionario.** `Perfil Inversionista/Kevin.md` no era un formulario:
   era una lista de preguntas que Kevin contestó en prosa. Aquí esas mismas
   preguntas se hacen una a una, y **la respuesta de Kevin es el valor por
   defecto de cada una**. Quien no conteste, hereda la suya.

Nada de este módulo importa `vertex_api`: se le pasa la conexión a la base. Así
se puede probar solo, y así el import no se vuelve circular.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import time
import unicodedata
from datetime import datetime, timezone

__all__ = [
    "PREGUNTAS", "TOLERANCIAS", "perfil_por_defecto", "perfil_desde_respuestas",
    "perfil_a_markdown", "preguntas_sin_contestar", "perfil_efectivo",
    "MODOS", "OBLIGATORIAS", "hash_password",
    "verificar_password", "crear_tablas", "crear_usuario", "buscar_usuario",
    "autenticar", "abrir_sesion", "usuario_de_sesion", "cerrar_sesion",
    "guardar_perfil", "leer_perfil", "ruta_md_de", "registrar_contribucion",
    "ErrorDeCuenta", "UsuarioExiste", "CredencialInvalida",
]


# ═══════════════════════════════════════════════════════════════════════════
#  EL CUESTIONARIO
#
#  Sale, sección por sección, de `Perfil Inversionista/Kevin.md`. No es una
#  lista inventada: cada pregunta corresponde a un apartado que él contestó, y
#  su respuesta es el `defecto`.
#
#  `campo` dice a qué campo estructurado alimenta la respuesta. Ese campo es lo
#  que consume el motor (sizing, universo, horizonte); el texto libre es
#  contexto para la tesis y **nunca** se convierte en un score.
# ═══════════════════════════════════════════════════════════════════════════

#: Bandas de tolerancia. El % es del capital por operación y es el techo que
#: usa `risk.size_flow`. Los nombres son los del perfil de Kevin.
TOLERANCIAS = {
    "conservador":  {"label": "Conservador", "riesgo_pct": 2.0,
                     "que_significa": "Priorizas no perder. Posiciones pequeñas y muy filtradas."},
    "moderado":     {"label": "Moderado", "riesgo_pct": 5.0,
                     "que_significa": "Aceptas volatilidad a cambio de crecimiento."},
    "agresivo":     {"label": "Agresivo", "riesgo_pct": 15.0,
                     "que_significa": "Buscas crecimiento de capital y toleras drawdowns fuertes."},
    "especulativo": {"label": "Especulativo", "riesgo_pct": 30.0,
                     "que_significa": "Asumes riesgo de ruina real a cambio de asimetría."},
}

PREGUNTAS = [
    {
        "id": "objetivos",
        "seccion": "Objetivos",
        "pregunta": "¿Qué buscas con este dinero?",
        "ayuda": "Puedes marcar más de uno. Es lo primero que el agente mira para "
                 "decidir si una acción encaja contigo o no.",
        "tipo": "multi",
        "campo": "objetivos",
        "opciones": [
            {"valor": "crecimiento", "label": "Crecimiento de capital",
             "detalle": "Que la cuenta crezca, aunque el camino sea volátil."},
            {"valor": "timing", "label": "Trades de opciones de corto plazo",
             "detalle": "Semanas a meses, aprovechando el momento."},
            {"valor": "ingresos", "label": "Generación de ingresos",
             "detalle": "Flujo constante en el largo plazo (5+ años)."},
            {"valor": "preservacion", "label": "Preservar lo que tengo",
             "detalle": "No perder pesa más que ganar."},
        ],
        "defecto": ["crecimiento", "timing", "ingresos"],
    },
    {
        "id": "horizonte",
        "seccion": "Objetivos",
        "pregunta": "¿En cuánto tiempo esperas usar este dinero?",
        "ayuda": "El horizonte decide cuánta quema de theta aguanta una posición "
                 "y qué tan lejos puede estar un objetivo de precio.",
        "tipo": "opcion",
        "campo": "horizonte",
        # `anios` es el rango EXPLÍCITO que se escribe en el `.md`.
        #
        # Estuvo derivado de `horizonte_dias` y era una mentira silenciosa:
        # «5+ años» acababa impreso como «1 a 3 años» porque la derivación
        # redondeaba 90 días a 0. El especialista de riesgo reportaba entonces
        # un horizonte que el inversionista nunca eligió. Declarado a mano, no
        # puede derivar mal.
        "opciones": [
            {"valor": "días", "label": "Días", "detalle": "Muy corto plazo.",
             "anios": [0, 1]},
            {"valor": "semanas a meses", "label": "Semanas a meses",
             "detalle": "Swing trading, opciones de vencimiento cercano.",
             "anios": [0, 1]},
            {"valor": "1-3 años", "label": "1 a 3 años",
             "detalle": "El horizonte principal de Kevin.", "anios": [1, 3]},
            {"valor": "5+ años", "label": "5 años o más",
             "detalle": "Largo plazo, generación de ingresos.", "anios": [5, 10]},
        ],
        "defecto": "1-3 años",
    },
    {
        "id": "tolerancia",
        "seccion": "Tolerancia al riesgo",
        "pregunta": "¿Cuánto puedes perder en UNA operación sin perder el sueño?",
        "ayuda": "No es una preferencia: es el techo que decide cuántos contratos "
                 "te caben. Un número alto aquí no te hace ganar más, te hace "
                 "arriesgar más.",
        "tipo": "opcion",
        "campo": "tolerancia",
        "opciones": [
            {"valor": "conservador", "label": "Conservador · 2%",
             "detalle": TOLERANCIAS["conservador"]["que_significa"]},
            {"valor": "moderado", "label": "Moderado · 5%",
             "detalle": TOLERANCIAS["moderado"]["que_significa"]},
            {"valor": "agresivo", "label": "Agresivo · 15%",
             "detalle": TOLERANCIAS["agresivo"]["que_significa"]},
            {"valor": "especulativo", "label": "Especulativo · 30%",
             "detalle": TOLERANCIAS["especulativo"]["que_significa"]},
        ],
        "defecto": "agresivo",
    },
    {
        "id": "capital",
        "seccion": "Capital",
        "pregunta": "¿Con cuánto capital vas a operar? (USD)",
        "ayuda": "Solo el dinero destinado a esto. Es lo que decide si un "
                 "contrato te cabe siquiera.",
        "tipo": "numero",
        "campo": "capital",
        "defecto": 1000,
    },
    {
        "id": "instrumentos",
        "seccion": "Instrumentos",
        "pregunta": "¿Qué instrumentos usas?",
        "ayuda": "El agente no te propondrá nada fuera de lo que marques aquí.",
        "tipo": "multi",
        "campo": "instrumentos",
        "opciones": [
            {"valor": "acciones", "label": "Acciones individuales", "detalle": ""},
            {"valor": "etf", "label": "ETFs", "detalle": ""},
            {"valor": "opciones", "label": "Opciones", "detalle": "Calls, puts y estrategias."},
            {"valor": "bonos", "label": "Bonos / renta fija", "detalle": ""},
        ],
        "defecto": ["acciones", "etf", "opciones"],
    },
    {
        "id": "excluir",
        "seccion": "Instrumentos",
        "pregunta": "¿Qué NO tocas, pase lo que pase?",
        "ayuda": "Un veto es más fuerte que una preferencia: el agente lo respeta "
                 "aunque el análisis salga favorable.",
        "tipo": "multi",
        "campo": "excluir",
        "opciones": [
            {"valor": "forex", "label": "Forex", "detalle": ""},
            {"valor": "cripto", "label": "Cripto", "detalle": ""},
            {"valor": "penny", "label": "Penny stocks", "detalle": ""},
            {"valor": "apalancados", "label": "ETFs apalancados", "detalle": ""},
        ],
        "defecto": ["forex", "cripto"],
    },
    {
        "id": "mercados",
        "seccion": "Universo",
        "pregunta": "¿En qué mercados inviertes?",
        "ayuda": "El chequeo del universo es determinista: si una acción cotiza "
                 "fuera de lo que marques, sale del universo sin discusión.",
        "tipo": "multi",
        "campo": "mercados",
        "opciones": [
            {"valor": "EE.UU.", "label": "Estados Unidos", "detalle": "NYSE, NASDAQ, AMEX."},
            {"valor": "Europa", "label": "Europa", "detalle": ""},
            {"valor": "Latinoamérica", "label": "Latinoamérica", "detalle": ""},
            {"valor": "Asia", "label": "Asia", "detalle": ""},
        ],
        "defecto": ["EE.UU."],
    },
    {
        "id": "max_posicion_pct",
        "seccion": "Reglas de dimensionamiento",
        "pregunta": "¿Cuánto capital puede ocupar UNA sola posición?",
        "ayuda": "Distinto de lo anterior: aquel dice cuánto puedes PERDER, este "
                 "cuánto puedes DESPLEGAR. Con un tope del 30%, tres pérdidas "
                 "totales seguidas se llevan casi la cuenta entera.",
        "tipo": "rango_pct",
        "campo": "max_posicion_pct",
        "defecto": [20, 30],
    },
    {
        "id": "prioridad",
        "seccion": "Reglas de dimensionamiento",
        "pregunta": "Cuando hay que elegir, ¿qué pesa más?",
        "ayuda": "Cambia el orden en que se te presentan las oportunidades, no la "
                 "matemática con la que se calculan.",
        "tipo": "opcion",
        "campo": "prioridad",
        "opciones": [
            {"valor": "probabilidad", "label": "Probabilidad de éxito y timing",
             "detalle": "Prefiero acertar seguido aunque cada acierto sea menor."},
            {"valor": "magnitud", "label": "Magnitud del retorno",
             "detalle": "Prefiero pocas grandes aunque falle más veces."},
            {"valor": "equilibrio", "label": "Un equilibrio de los dos", "detalle": ""},
        ],
        "defecto": "probabilidad",
    },
    {
        "id": "experiencia",
        "seccion": "Sobre ti",
        "pregunta": "¿Cuánta experiencia tienes invirtiendo?",
        "ayuda": "No cambia ningún cálculo. Cambia cuánto se explica cada número.",
        "tipo": "opcion",
        "campo": "experiencia",
        "opciones": [
            {"valor": "principiante", "label": "Estoy empezando",
             "detalle": "Explícame cada término."},
            {"valor": "intermedio", "label": "Ya he operado",
             "detalle": "Entiendo lo básico de opciones."},
            {"valor": "avanzado", "label": "Con experiencia",
             "detalle": "Ve al grano con los números."},
        ],
        "defecto": "intermedio",
    },
    {
        "id": "texto",
        "seccion": "En mis palabras",
        "pregunta": "¿Algo más que el agente deba saber de ti?",
        "ayuda": "Escríbelo como se lo contarías a un asesor. Esto es CONTEXTO "
                 "para la tesis — nunca se convierte en un score.",
        "tipo": "texto_largo",
        "campo": "texto",
        "defecto": "",
        # **OPCIONAL.** Las demás preguntas tienen una respuesta correcta para
        # cada persona y dejarla en blanco significa heredar la de Kevin. Esta
        # no: en blanco es una respuesta válida —no tienes nada más que añadir—
        # y no hay nada que heredar, porque el contexto de otra persona no es
        # contexto tuyo. Por eso no cuenta para el progreso ni se marca como
        # pendiente: un perfil sin texto libre está COMPLETO.
        "opcional": True,
    },
]

# La pregunta «¿Qué esperas que el sistema haga por ti?» estuvo aquí y se quitó.
#
# No era una pregunta: era el contrato del sistema disfrazado de preferencia. La
# matemática es determinista y el LLM solo explica — eso no cambia porque alguien
# conteste otra cosa, así que preguntarlo insinuaba una elección que no existe.
# El contrato sigue estando en el `.md` que leen los agentes, pero como lo que
# es: una constante, igual para todos.

#: Índice por id, para no recorrer la lista en cada acceso.
_POR_ID = {p["id"]: p for p in PREGUNTAS}


#: Los dos modos del perfil.
#:
#:  · `default`       — usas el perfil de referencia (el de Kevin) tal cual. No
#:                      hay preguntas que contestar, y eso es una ELECCIÓN, no
#:                      una tarea pendiente.
#:  · `personalizado` — contestas el cuestionario y el sistema recomienda con
#:                      tus números.
#:
#: Cambiar de modo NO borra lo contestado: volver a `personalizado` recupera tus
#: respuestas tal como las dejaste. Borrarlas al cambiar castigaría la
#: curiosidad de quien solo quiso ver cómo era el otro modo.
MODOS = ("default", "personalizado")


def perfil_por_defecto() -> dict:
    """El perfil de Kevin, que es el que hereda quien no conteste.

    Se construye DESDE `PREGUNTAS`, no como una copia aparte. Una segunda copia
    se desincronizaría con el primer cambio de pregunta, y entonces el default
    que se enseña en pantalla y el que se guarda serían distintos.
    """
    d = {p["campo"]: (list(p["defecto"]) if isinstance(p["defecto"], list) else p["defecto"])
         for p in PREGUNTAS}
    d.update({"nombre": "", "email": "", "respondidas": [], "actualizado": None,
              "modo": "default"})
    return d


def perfil_efectivo(perfil: dict) -> dict:
    """El perfil con el que de verdad se recomienda, aplicado el modo.

    En `default` los campos del cuestionario vuelven a los de Kevin **sin tocar
    lo guardado**: las respuestas siguen en el diccionario para cuando la
    persona vuelva a `personalizado`. Lo que cambia es lo que sale por la
    puerta — el sizing, el `.md` y el especialista de riesgo.
    """
    if (perfil or {}).get("modo") != "default":
        return dict(perfil or {})
    fuera = dict(perfil or {})
    base = {p["campo"]: (list(p["defecto"]) if isinstance(p["defecto"], list)
                         else p["defecto"]) for p in PREGUNTAS}
    fuera.update(base)
    return fuera


#: Las que hay que contestar para que el perfil sea tuyo. Las opcionales no
#: entran: en blanco son una respuesta válida, no un valor heredado.
OBLIGATORIAS = [p["id"] for p in PREGUNTAS if not p.get("opcional")]


def preguntas_sin_contestar(perfil: dict) -> list[str]:
    """Qué preguntas siguen con el valor de Kevin y no con el del usuario.

    Es lo que permite decir en pantalla «4 de 11 contestadas» en vez de fingir
    que un perfil heredado es un perfil propio.

    Las OPCIONALES nunca aparecen aquí. Dejarlas en blanco no es heredar nada:
    el contexto personal de otra persona no es contexto tuyo, así que no hay
    valor por defecto que heredar y el perfil está completo sin ellas.
    """
    # En modo `default` no hay nada pendiente: usar el perfil de referencia es
    # una decisión tomada, no un formulario a medias. Marcarlo como incompleto
    # sería regañar a alguien por haber elegido.
    if (perfil or {}).get("modo") == "default":
        return []
    respondidas = set(perfil.get("respondidas") or [])
    return [pid for pid in OBLIGATORIAS if pid not in respondidas]


def _valida_respuesta(preg: dict, valor):
    """Devuelve `(valor_limpio, None)` o `(None, motivo)`.

    Validar aquí y no en la ruta permite probarlo sin levantar la API, y evita
    que una pregunta nueva se cuele sin validación por olvido.
    """
    tipo = preg["tipo"]
    if tipo == "numero":
        try:
            v = float(valor)
        except (TypeError, ValueError):
            return None, f"«{preg['pregunta']}» espera un número."
        if not 0 <= v <= 1e12:
            return None, "Capital fuera de rango."
        return v, None
    if tipo == "opcion":
        validos = {o["valor"] for o in preg["opciones"]}
        if valor not in validos:
            return None, f"Respuesta desconocida para «{preg['id']}»: {valor!r}"
        return valor, None
    if tipo == "multi":
        if not isinstance(valor, list):
            return None, f"«{preg['id']}» espera una lista."
        validos = {o["valor"] for o in preg["opciones"]}
        fuera = [x for x in valor if x not in validos]
        if fuera:
            return None, f"Opciones desconocidas en «{preg['id']}»: {fuera}"
        return list(dict.fromkeys(valor)), None
    if tipo == "rango_pct":
        try:
            lo, hi = int(valor[0]), int(valor[1])
        except (TypeError, ValueError, IndexError, KeyError):
            return None, f"«{preg['id']}» espera un par [min, max]."
        if not 0 < lo <= hi <= 100:
            return None, "El tope por posición va entre 1% y 100%, con min ≤ max."
        return [lo, hi], None
    if tipo in ("texto", "texto_largo"):
        return str(valor or "")[:8000], None
    return None, f"Tipo de pregunta desconocido: {tipo}"


def perfil_desde_respuestas(respuestas: dict, base: dict | None = None):
    """Aplica las respuestas sobre un perfil, dejando el default donde no hay.

    Devuelve `(perfil, error)`. Una sola respuesta inválida aborta el guardado
    entero: un perfil a medias es peor que uno viejo, porque nadie sabría qué
    parte es suya.

    **Contestar es un acto explícito.** Solo los ids presentes en `respuestas`
    entran en `respondidas`. Mandar el formulario sin tocar una pregunta la deja
    heredada, y la pantalla lo puede decir.
    """
    perfil = dict(base or perfil_por_defecto())
    perfil.setdefault("respondidas", [])
    respondidas = set(perfil.get("respondidas") or [])

    for pid, valor in (respuestas or {}).items():
        preg = _POR_ID.get(pid)
        if preg is None:
            return None, f"Pregunta desconocida: {pid!r}"
        limpio, motivo = _valida_respuesta(preg, valor)
        if motivo:
            return None, motivo
        perfil[preg["campo"]] = limpio
        respondidas.add(pid)

    perfil["respondidas"] = sorted(respondidas)
    return perfil, None


def derivados(perfil: dict) -> dict:
    """Los números que salen del perfil y que el motor consume directamente.

    Van aparte de los campos contestados para que quede claro qué escribió una
    persona y qué calculó el sistema.
    """
    tol = TOLERANCIAS.get(perfil.get("tolerancia"), TOLERANCIAS["agresivo"])
    try:
        capital = float(perfil.get("capital") or 0)
    except (TypeError, ValueError):
        capital = 0.0
    return {
        "riesgo_pct": tol["riesgo_pct"],
        "riesgo_por_trade": round(capital * tol["riesgo_pct"] / 100, 2),
        "capital": capital,
    }


def horizonte_dias(perfil: dict) -> int:
    """El horizonte en días, para la quema de theta de `size_flow`.

    Se usa el extremo CORTO del rango: un horizonte corto quema menos theta y
    deja un techo más alto, así que el largo sería el lado conservador… pero
    también el que esconde operaciones que sí caben. Se usa el corto y se dice.
    """
    t = (perfil.get("horizonte") or "").lower()
    if "día" in t or "dia" in t:
        return 5
    if "semana" in t:
        return 21
    if "mes" in t:
        return 45
    if "año" in t or "ano" in t:
        return 90        # tope: `size_flow` no mira más allá del vencimiento
    return 30


# ═══════════════════════════════════════════════════════════════════════════
#  EL MARKDOWN QUE LEEN LOS AGENTES
# ═══════════════════════════════════════════════════════════════════════════

def _anios_de_horizonte(valor) -> list[int]:
    """El rango en años de una opción de horizonte, declarado en `PREGUNTAS`.

    Para un horizonte que no esté en la lista se devuelve el de Kevin, que es lo
    que hereda quien no contesta.
    """
    for o in _POR_ID["horizonte"]["opciones"]:
        if o["valor"] == valor:
            return list(o["anios"])
    return [1, 3]


def perfil_a_markdown(perfil: dict) -> str:
    """El `.md` que consume el agente de acciones.

    El formato importa por DOS motivos, y el segundo es el que muerde:

    1. `_load_investor_profile()` pega el archivo entero en el prompt. Tiene que
       leerse como un perfil escrito por una persona.
    2. `engine/wbj/specialists/risk.py::_load_profile` **lo parsea con tres
       regex**: el primer importe en dólares (capital), un rango «N a M años»
       (horizonte) y un rango «N% – M%» (tope por posición). Si alguno deja de
       casar, ese especialista cae a su valor por defecto **en silencio** y el
       reporte pasa a hablar del perfil de otra persona.
    """
    d = perfil_efectivo(perfil)
    d.update(derivados(d))
    tol = TOLERANCIAS.get(d.get("tolerancia"), TOLERANCIAS["agresivo"])
    pos = d.get("max_posicion_pct") or [20, 30]

    def _lista(campo, vacio="sin especificar"):
        v = d.get(campo) or []
        return ", ".join(str(x) for x in v) or vacio

    def _etiqueta(pid, vacio="sin especificar"):
        """La etiqueta legible de una opción, no su valor interno.

        El `.md` acaba dentro de un prompt: «probabilidad» no dice nada, «Probabilidad
        de éxito y timing» sí."""
        preg = _POR_ID[pid]
        return {o["valor"]: o["label"]
                for o in preg["opciones"]}.get(d.get(preg["campo"]), vacio)

    # El regex del engine quiere «N a M años». Si el horizonte se eligió con
    # palabras («semanas a meses»), se añade detrás el rango DECLARADO de esa
    # opción — no uno derivado, que ya se equivocó una vez.
    horiz = d.get("horizonte") or "sin especificar"
    if not re.search(r"\d+\s*(?:a|-|–|—)\s*\d+\s*años", horiz, re.I):
        anios = _anios_de_horizonte(d.get("horizonte"))
        horiz = f"{horiz} ({anios[0]} a {anios[1]} años)"

    sin_contestar = preguntas_sin_contestar(d)
    lineas = [
        f"# Perfil del Inversionista — {d.get('nombre') or 'sin nombre'}",
        "",
        "> Generado desde el cuestionario de Vertex. Es la ÚNICA fuente del perfil:",
        "> lo contesta el inversionista y lo leen los tres agentes.",
    ]
    if d.get("modo") == "default":
        # Se declara. El agente tiene que poder distinguir «este es su capital»
        # de «este es el capital de referencia porque no eligió personalizar».
        lineas += [
            ">",
            "> **Perfil por defecto.** Esta persona NO ha personalizado su perfil:",
            "> estos valores son los de referencia, no los suyos. Trátalos como una",
            "> base razonable, no como una declaración personal.",
        ]
    if sin_contestar:
        # Se declara, no se disimula. Un perfil heredado que se presenta como
        # propio hace que el reporte hable con una confianza que no tiene.
        lineas += [
            ">",
            f"> **{len(sin_contestar)} de {len(PREGUNTAS)} preguntas siguen sin contestar** "
            f"y usan el valor por defecto: {', '.join(sin_contestar)}.",
        ]
    lineas += [
        "",
        "## Objetivos",
        "",
        f"- **Busca**: {_lista('objetivos')}.",
        f"- **Horizonte**: {horiz}.",
        "",
        # ── EL ORDEN DE ESTAS DOS SECCIONES NO ES ESTÉTICO ──────────────
        # `risk._load_profile` toma el PRIMER importe en dólares del documento
        # como el capital. Con «Tolerancia» delante, el primer `$` era el riesgo
        # por operación ($150) y el especialista concluía que la cuenta entera
        # eran $150. Capital va primero, y por eso va primero.
        "## Capital",
        "",
        f"- **Capital disponible**: ${d['capital']:,.0f}.",
        "",
        "## Tolerancia al riesgo",
        "",
        f"- **{tol['label']}.** {tol['que_significa']}",
        f"- **Riesgo máximo por operación**: {d['riesgo_pct']:.0f}% del capital "
        f"(${d['riesgo_por_trade']:,.0f}).",
        "",
        "## Instrumentos",
        "",
        f"- **Usa**: {_lista('instrumentos')}.",
        f"- **No toca**: {_lista('excluir', 'nada excluido a propósito')}.",
        "",
        "## Universo",
        "",
        f"- {_lista('mercados')}.",
        "",
        "## Reglas de dimensionamiento (guía, no altera el scoring)",
        "",
        f"- **Máximo por posición individual: {pos[0]}% – {pos[1]}% del capital.**",
        f"- Prioriza: {_etiqueta('prioridad')}.",
        "- El sistema entrega **clasificación de research** con niveles de confirmación",
        "  e invalidación — nunca una orden automática de compra/venta. La ejecución",
        "  es siempre manual y del inversionista.",
        "",
        "## Experiencia",
        "",
        f"- {_etiqueta('experiencia')}.",
        "",
        "## En mis palabras",
        "",
        (d.get("texto") or "_Sin nada más que añadir._").strip(),
        "",
        # Constante, no respuesta. Estuvo como pregunta y era el contrato del
        # sistema disfrazado de preferencia: no cambia porque alguien conteste
        # otra cosa, así que preguntarlo insinuaba una elección que no existe.
        "## Cómo trabaja este sistema",
        "",
        "- La matemática y el scoring se calculan **exactamente** con la metodología",
        "  del Cerebro (framework WBJ). El LLM solo **explica** en palabras simples",
        "  qué significa cada número, gate, override y nivel — sin cambiar ni reducir",
        "  ningún cálculo.",
        "- La salida es **clasificación de research** con niveles de confirmación e",
        "  invalidación. Nunca una orden de compra o venta: la ejecución es siempre",
        "  manual y del inversionista.",
        "",
        "---",
        "",
        "**Cómo usar esto.** Los datos duros son restricciones: el sizing y el",
        "universo salen de ahí. El texto es contexto para la tesis y **nunca** se",
        "convierte en un score — sin evidencia no hay número.",
        "",
    ]
    return "\n".join(lineas)


# ═══════════════════════════════════════════════════════════════════════════
#  CONTRASEÑAS
#
#  PBKDF2-HMAC-SHA256 de la biblioteca estándar: sin dependencias nuevas y sin
#  inventar criptografía. 600.000 iteraciones es la recomendación de OWASP para
#  SHA-256; la sal es de 16 bytes y es por usuario, así que dos personas con la
#  misma contraseña no comparten hash.
# ═══════════════════════════════════════════════════════════════════════════

_ITERACIONES = 600_000
_MIN_PASSWORD = 8


def hash_password(password: str, *, iteraciones: int = _ITERACIONES) -> str:
    sal = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), sal, iteraciones)
    return f"pbkdf2_sha256${iteraciones}${sal.hex()}${dk.hex()}"


def verificar_password(password: str, guardado: str) -> bool:
    """Comparación en tiempo constante. Nunca lanza: un hash corrupto es un
    `False`, no un 500 que le diga al atacante que dio con algo raro."""
    try:
        algo, its, sal_hex, dk_hex = str(guardado).split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 bytes.fromhex(sal_hex), int(its))
    except Exception:                            # noqa: BLE001
        return False
    return hmac.compare_digest(dk.hex(), dk_hex)


def _valida_password(p: str) -> str | None:
    if len(p or "") < _MIN_PASSWORD:
        return f"La contraseña necesita al menos {_MIN_PASSWORD} caracteres."
    return None


_RE_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")


def normaliza_email(email: str) -> str:
    return (email or "").strip().lower()


# ═══════════════════════════════════════════════════════════════════════════
#  CUENTAS Y SESIONES
# ═══════════════════════════════════════════════════════════════════════════

class ErrorDeCuenta(Exception):
    """Excepción cuyo mensaje está ESCRITO para que lo lea una persona.

    La regla del proyecto es que una excepción cruda no llega al navegador:
    puede llevar rutas del servidor, SQL o una URL con la clave dentro. Estas
    no — su texto se redacta aquí mismo, en el `raise`, y no contiene nada del
    sistema.

    Existe la clase para poder DECIRLO. `return str(e)` es indistinguible de
    filtrar una excepción cualquiera, tanto para quien lee el código como para
    el test que lo vigila; `return e.publico` dice que ese mensaje se escribió
    a propósito para salir.
    """

    @property
    def publico(self) -> str:
        return str(self)


class UsuarioExiste(ErrorDeCuenta):
    pass


class CredencialInvalida(ErrorDeCuenta):
    pass


#: Duración de la sesión. 30 días, como la cookie que ya existía.
_SESION_DIAS = 30


def crear_tablas(conn):
    """Idempotente: se llama en cada arranque, como el resto del esquema."""
    conn.execute("""CREATE TABLE IF NOT EXISTS usuarios (
        id          TEXT PRIMARY KEY,
        email       TEXT NOT NULL UNIQUE,
        nombre      TEXT NOT NULL,
        pass_hash   TEXT NOT NULL,
        creado_ts   REAL NOT NULL,
        perfil      TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS sesiones (
        token_hash  TEXT PRIMARY KEY,
        usuario_id  TEXT NOT NULL,
        creado_ts   REAL NOT NULL,
        expira_ts   REAL NOT NULL
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sesiones_usuario ON sesiones(usuario_id)")
    # El registro de lo que cada análisis aportó al pool común. `usuario_id` es
    # para poder decirle a alguien cuánto ha aportado, no para exponerlo: las
    # vistas compartidas agregan y nunca devuelven quién analizó qué.
    conn.execute("""CREATE TABLE IF NOT EXISTS contribuciones (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        agente      TEXT NOT NULL,
        ticker      TEXT NOT NULL,
        usuario_id  TEXT,
        creado_ts   REAL NOT NULL
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_contrib_agente ON contribuciones(agente, creado_ts)")
    conn.commit()


def _fila_a_usuario(row, *, con_perfil=True):
    if row is None:
        return None
    u = {"id": row["id"], "email": row["email"], "nombre": row["nombre"],
         "creado_ts": row["creado_ts"]}
    if con_perfil:
        try:
            u["perfil"] = json.loads(row["perfil"]) if row["perfil"] else None
        except Exception:                        # noqa: BLE001
            u["perfil"] = None
    return u


def crear_usuario(conn, email: str, nombre: str, password: str) -> dict:
    email = normaliza_email(email)
    if not _RE_EMAIL.match(email):
        raise CredencialInvalida("Escribe un email válido.")
    if not (nombre or "").strip():
        raise CredencialInvalida("Escribe tu nombre.")
    motivo = _valida_password(password)
    if motivo:
        raise CredencialInvalida(motivo)
    if conn.execute("SELECT 1 FROM usuarios WHERE email=?", (email,)).fetchone():
        raise UsuarioExiste("Ya existe una cuenta con ese email.")

    uid = secrets.token_hex(12)
    #: ¿Es la PRIMERA cuenta de este despliegue? Se mira antes de insertar.
    primera = conn.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0] == 0
    perfil = perfil_por_defecto()
    perfil.update({"nombre": nombre.strip(), "email": email})
    conn.execute(
        "INSERT INTO usuarios (id, email, nombre, pass_hash, creado_ts, perfil) "
        "VALUES (?,?,?,?,?,?)",
        (uid, email, nombre.strip(), hash_password(password), time.time(),
         json.dumps(perfil, ensure_ascii=False)))
    # ── ADOPCIÓN DEL ARCHIVO HUÉRFANO ──────────────────────────────────
    #
    # Los reportes anteriores a que existieran cuentas tienen `usuario_id`
    # NULL. Sin esto, la primera persona que se registra —que es justamente
    # quien los generó, porque era el único usuario que había— abre su archivo
    # y lo ve VACÍO. Su historial entero desaparece de la vista sin que nada
    # falle ni avise.
    #
    # Solo la PRIMERA cuenta los adopta. La segunda ya no puede: para entonces
    # los huérfanos tienen dueño, y regalárselos a cualquiera que se registre
    # sería entregarle el archivo de otro.
    adoptados = 0
    if primera:
        try:
            cur = conn.execute("UPDATE reports SET usuario_id=? WHERE usuario_id IS NULL",
                               (uid,))
            adoptados = cur.rowcount or 0
        except Exception:                        # noqa: BLE001 — sin tabla aún, se sigue
            adoptados = 0
    conn.commit()
    return {"id": uid, "email": email, "nombre": nombre.strip(), "perfil": perfil,
            "primera_cuenta": primera, "reportes_adoptados": adoptados}


def buscar_usuario(conn, *, uid=None, email=None):
    if uid:
        row = conn.execute("SELECT * FROM usuarios WHERE id=?", (uid,)).fetchone()
    else:
        row = conn.execute("SELECT * FROM usuarios WHERE email=?",
                           (normaliza_email(email),)).fetchone()
    return _fila_a_usuario(row)


def autenticar(conn, email: str, password: str) -> dict:
    """Devuelve el usuario o lanza `CredencialInvalida`.

    El mensaje es el MISMO para «no existe» y para «contraseña mala». Distinguir
    los dos convierte el login en un directorio: cualquiera podría averiguar qué
    emails tienen cuenta probando de uno en uno.
    """
    row = conn.execute("SELECT * FROM usuarios WHERE email=?",
                       (normaliza_email(email),)).fetchone()
    if row is None or not verificar_password(password, row["pass_hash"]):
        raise CredencialInvalida("Email o contraseña incorrectos.")
    return _fila_a_usuario(row)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def abrir_sesion(conn, usuario_id: str) -> str:
    """Crea la sesión y devuelve el token EN CLARO, que solo existe aquí.

    En disco va únicamente su SHA-256: si alguien se lleva la base de datos, no
    se lleva sesiones vivas — no puede reconstruir el token desde el hash.
    """
    token = secrets.token_urlsafe(32)
    ahora = time.time()
    conn.execute("INSERT INTO sesiones (token_hash, usuario_id, creado_ts, expira_ts) "
                 "VALUES (?,?,?,?)",
                 (_hash_token(token), usuario_id, ahora,
                  ahora + _SESION_DIAS * 86400))
    # Barrido de caducadas, aprovechando que ya estamos escribiendo.
    conn.execute("DELETE FROM sesiones WHERE expira_ts < ?", (ahora,))
    conn.commit()
    return token


def usuario_de_sesion(conn, token: str):
    """El usuario detrás de una cookie, o `None`. Nunca lanza."""
    if not token:
        return None
    try:
        row = conn.execute(
            "SELECT u.* FROM sesiones s JOIN usuarios u ON u.id = s.usuario_id "
            "WHERE s.token_hash=? AND s.expira_ts > ?",
            (_hash_token(token), time.time())).fetchone()
    except Exception:                            # noqa: BLE001
        return None
    return _fila_a_usuario(row)


def cerrar_sesion(conn, token: str) -> None:
    if not token:
        return
    try:
        conn.execute("DELETE FROM sesiones WHERE token_hash=?", (_hash_token(token),))
        conn.commit()
    except Exception:                            # noqa: BLE001
        pass


# ═══════════════════════════════════════════════════════════════════════════
#  PERFIL POR USUARIO
# ═══════════════════════════════════════════════════════════════════════════

def _slug(texto: str) -> str:
    """Nombre de archivo seguro a partir del nombre de la persona.

    Sin esto, un nombre con `../` o con barras escribiría fuera del directorio.
    Se acepta solo lo que sobrevive a la normalización.
    """
    base = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    base = re.sub(r"[^A-Za-z0-9 _-]", "", base).strip().replace(" ", "_")
    return base[:40] or "inversionista"


def ruta_md_de(dir_perfiles: str, usuario: dict) -> str:
    """El `.md` de ESTE usuario. Es el archivo que el agente de acciones lee.

    Va bajo `usuarios/` y lleva el id en el nombre: dos personas llamadas Kevin
    no pueden pisarse el perfil.
    """
    return os.path.join(dir_perfiles, "usuarios",
                        f"{_slug(usuario.get('nombre'))}-{usuario['id']}.md")


def guardar_perfil(conn, dir_perfiles: str, usuario: dict, perfil: dict) -> dict:
    """Escribe el perfil en la base y REGENERA el `.md` que leen los agentes."""
    # Solo los campos que son del perfil: lo derivado (`riesgo_pct`,
    # `sin_contestar`, `respuestas`) se recalcula al leer y guardarlo dejaría
    # copias que envejecen y contradicen al original.
    perfil = {k: v for k, v in dict(perfil).items() if k in perfil_por_defecto()}
    perfil["actualizado"] = datetime.now(timezone.utc).isoformat()
    perfil.setdefault("nombre", usuario.get("nombre", ""))
    perfil.setdefault("email", usuario.get("email", ""))
    conn.execute("UPDATE usuarios SET perfil=? WHERE id=?",
                 (json.dumps(perfil, ensure_ascii=False), usuario["id"]))
    conn.commit()

    ruta = ruta_md_de(dir_perfiles, usuario)
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    tmp = ruta + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(perfil_a_markdown(perfil))
    os.replace(tmp, ruta)                        # atómico: nunca un .md a medias
    return perfil


def leer_perfil(conn, usuario_id: str) -> dict:
    """El perfil del usuario, completado con los defaults de Kevin.

    Completar aquí y no al guardar es deliberado: si mañana se añade una
    pregunta, los perfiles ya guardados heredan su default sin migración, y
    `preguntas_sin_contestar` la marca como pendiente.
    """
    base = perfil_por_defecto()
    row = conn.execute("SELECT perfil, nombre, email FROM usuarios WHERE id=?",
                       (usuario_id,)).fetchone()
    if row is not None:
        try:
            guardado = json.loads(row["perfil"]) if row["perfil"] else {}
        except Exception:                        # noqa: BLE001
            guardado = {}
        base.update({k: v for k, v in (guardado or {}).items() if k in base})
        base["nombre"] = base.get("nombre") or row["nombre"]
        base["email"] = base.get("email") or row["email"]
    if base.get("modo") not in MODOS:
        base["modo"] = "default"
    # Se devuelven las DOS caras: `respuestas` es lo que la persona escribió y
    # el resto es lo EFECTIVO (con el modo aplicado). Sin separarlas, entrar en
    # modo `default` enseñaría el formulario con los valores de Kevin como si
    # fueran los tuyos, y guardarlos los convertiría en tuyos sin quererlo.
    respuestas = {p["campo"]: base.get(p["campo"]) for p in PREGUNTAS}
    efectivo = perfil_efectivo(base)
    efectivo["respuestas"] = respuestas
    efectivo.update(derivados(efectivo))
    efectivo["sin_contestar"] = preguntas_sin_contestar(base)
    return efectivo


# ═══════════════════════════════════════════════════════════════════════════
#  APRENDIZAJE COMPARTIDO
# ═══════════════════════════════════════════════════════════════════════════

#: Los dos agentes aprenden de forma DISTINTA, y por eso se cuentan aparte.
#:
#:  · `acciones` (Analyze/Explore) aprende por CALIBRACIÓN: cada reporte guarda
#:    convicción y objetivos, y el tiempo dice si acertó. Más reportes de más
#:    gente = una curva de fiabilidad con más puntos.
#:
#:  · `opciones` (Proyecciones) aprende por ACUMULACIÓN HACIA ADELANTE: la IV
#:    histórica, las cadenas y el flujo no se pueden comprar, se juntan una foto
#:    por día de mercado. Más tickers mirados por más gente = más serie.
AGENTES = ("acciones", "opciones")


def registrar_contribucion(conn, agente: str, ticker: str, usuario_id=None) -> None:
    """Anota que un análisis alimentó al pool. Nunca lanza: un fallo aquí no
    puede tumbar el análisis que acaba de terminar bien."""
    if agente not in AGENTES:
        return
    try:
        conn.execute("INSERT INTO contribuciones (agente, ticker, usuario_id, creado_ts) "
                     "VALUES (?,?,?,?)",
                     (agente, (ticker or "").upper()[:12], usuario_id, time.time()))
        conn.commit()
    except Exception:                            # noqa: BLE001
        pass
