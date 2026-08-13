"""La memoria entre sesiones: qué dijo el agente antes, y qué ha pasado desde.

Qué problema resuelve
---------------------
`CLAUDE.md` define un protocolo de memoria —leer la tesis anterior antes de
analizar, escribirla después, registrar la lección cuando el análisis nuevo
contradice al viejo— y decía, con todas sus letras, que **no es automático**.
No lo era: `Memoria/` ni siquiera existía en la rama de datos. Cada análisis
empezaba sin saber qué se había dicho del mismo ticker la semana pasada.

Eso no es un detalle de archivo. Un agente que no recuerda su propia tesis no
puede contradecirse a sí mismo, y un agente que no puede contradecirse no
aprende: repite. La calibración, el «cuándo revisitar» y el «esto contradice lo
que dijimos» dependen todos de que exista un antes.

Qué escribe
-----------
    Memoria/MEMORIA.md          el índice: una línea por ticker
    Memoria/tesis/<TICKER>.md   la tesis, con su historial de revisiones
    Memoria/errores.md          las veces que el agente se contradijo

Dos decisiones que importan
---------------------------
**La tesis vieja no se borra: se escribe encima.** Cada revisión se añade
arriba y la anterior queda debajo, con su fecha. `CLAUDE.md` lo pide explícito
—«nunca borres la tesis vieja»— y el motivo es el mismo por el que existe el
track record: si se borra lo que se dijo, no hay forma de saber si se acertó.

**Los dos agentes escriben en el MISMO archivo por ticker.** Uno mira la
empresa a 1-3 años y el otro el flujo de opciones a semanas, pero son el mismo
ticker y el lector es la misma persona. Separarlos obligaría a abrir dos
archivos para saber qué se piensa de NVDA.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

__all__ = [
    "DIR_TESIS", "RUTA_INDICE", "RUTA_ERRORES",
    "ruta_tesis", "resume_analisis", "escribe_tesis", "lee_tesis",
    "contexto_para_el_agente",
]

DIR_MEMORIA = "Memoria"
DIR_TESIS = f"{DIR_MEMORIA}/tesis"
RUTA_INDICE = f"{DIR_MEMORIA}/MEMORIA.md"
RUTA_ERRORES = f"{DIR_MEMORIA}/errores.md"

#: Cuántas revisiones se conservan por ticker. No es un límite de disco —un
#: `.md` de texto no pesa— sino de lectura: veinte revisiones hacen ilegible el
#: archivo que existe justo para leerse rápido antes de analizar.
MAX_REVISIONES = 12

_TICKER_OK = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


def _hoy() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _saneado(ticker: str) -> str:
    t = (ticker or "").strip().upper()
    if not _TICKER_OK.match(t):
        raise ValueError(f"ticker inválido: {ticker!r}")
    return t


def ruta_tesis(ticker: str) -> str:
    return f"{DIR_TESIS}/{_saneado(ticker)}.md"


def _num(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if v != v else v                  # descarta NaN


# ── De un reporte a lo que hay que recordar ─────────────────────────────────

def resume_analisis(agente: str, payload: dict) -> dict[str, Any]:
    """Lo que de un reporte merece recordarse. Nunca lanza.

    Se extrae aquí y no en cada llamador porque los dos agentes guardan formas
    distintas, y la memoria tiene que ser UNA. Lo que no venga queda en `None`:
    una memoria que se inventa lo que no sabe es peor que no tener memoria.
    """
    d = payload or {}
    fuera: dict[str, Any] = {"agente": agente, "fecha": d.get("fecha") or _hoy()}

    if agente == "acciones":
        a = d.get("analisis") or {}
        fuera["veredicto"] = a.get("recommendation") or a.get("veredicto")
        fuera["puntaje"] = _num(a.get("total_score") if a.get("total_score") is not None
                                else a.get("conviccion_score"))
        fuera["precio"] = _num(d.get("precio_actual"))
        t = ((d.get("targets") or {}).get("12m") or {})
        fuera["targets"] = {k: _num(t.get(k)) for k in ("bear", "base", "bull")
                            if _num(t.get(k)) is not None} or None
        fuera["tesis"] = (a.get("in_simple_terms") or a.get("company_summary_simple")
                          or a.get("final_report") or "")
        fuera["invalida"] = a.get("thesis_killers") or a.get("coherence_flags")
    else:
        fuera["veredicto"] = (d.get("verdict") or (d.get("gex") or {}).get("regime"))
        fuera["puntaje"] = _num(d.get("score"))
        fuera["precio"] = _num((d.get("company") or {}).get("price"))
        niv = d.get("levels") or {}
        fuera["targets"] = {k: _num(niv.get(k)) for k in
                            ("key_support", "magnet", "key_resistance")
                            if _num(niv.get(k)) is not None} or None
        fuera["tesis"] = d.get("resumen") or ""
        fuera["invalida"] = None

    # La tesis se recorta a lo que se lee de un vistazo: este archivo existe
    # para consultarse ANTES de analizar, no para volver a contar el reporte.
    texto = re.sub(r"\s+", " ", str(fuera["tesis"] or "")).strip()
    fuera["tesis"] = (texto[:420] + "…") if len(texto) > 420 else texto
    return fuera


# ── Escribir ────────────────────────────────────────────────────────────────

def _bloque(r: dict[str, Any]) -> str:
    t = r.get("targets") or {}
    partes = [f"### {r['fecha']} · agente de {r['agente']}"]
    linea = []
    if r.get("veredicto"):
        linea.append(f"**{r['veredicto']}**")
    if r.get("puntaje") is not None:
        linea.append(f"puntaje {r['puntaje']:g}")
    if r.get("precio") is not None:
        linea.append(f"precio ${r['precio']:,.2f}")
    if linea:
        partes.append(" · ".join(linea))
    if t:
        partes.append("Niveles: " + " · ".join(f"{k} ${v:,.2f}" for k, v in t.items()))
    if r.get("tesis"):
        partes.append(str(r["tesis"]))
    inval = r.get("invalida")
    if inval:
        if isinstance(inval, (list, tuple)):
            inval = "; ".join(str(x) for x in inval[:4])
        partes.append(f"**Lo que la invalidaría:** {inval}")
    return "\n\n".join(partes)


def _revisiones(md: str) -> list[str]:
    """Los bloques que ya había, del más nuevo al más viejo."""
    if not md:
        return []
    trozos = re.split(r"(?m)^### ", md)
    return [("### " + t).rstrip() for t in trozos[1:]]


def escribe_tesis(agente: str, ticker: str, payload: dict, alm) -> dict[str, Any]:
    """Guarda la tesis de este análisis. Devuelve qué cambió respecto a la anterior.

    Es lo que convierte «se guardó un reporte» en «el agente lo recuerda».
    """
    tk = _saneado(ticker)
    nuevo = resume_analisis(agente, payload)
    previo = lee_tesis(tk, alm)

    ruta = ruta_tesis(tk)
    viejo_md = _texto(alm, ruta)
    cabecera = (f"# {tk} — memoria del agente\n\n"
                "_Lo que se dijo antes. La revisión más reciente arriba; las "
                "anteriores NO se borran, se quedan debajo con su fecha._\n")
    revs = ([_bloque(nuevo)] + _revisiones(viejo_md))[:MAX_REVISIONES]
    alm.guarda(ruta, cabecera + "\n" + "\n\n---\n\n".join(revs) + "\n")

    cambio = _compara(previo, nuevo)
    if cambio.get("se_contradice"):
        _apunta_error(alm, tk, previo, nuevo, cambio)
    _actualiza_indice(alm, tk, nuevo)
    return cambio


def _texto(alm, ruta: str) -> str:
    try:
        v = alm.lee(ruta)
    except Exception:                             # noqa: BLE001
        return ""
    if isinstance(v, (bytes, bytearray)):
        return v.decode("utf-8", "replace")
    return v if isinstance(v, str) else ""


def _compara(previo: dict | None, nuevo: dict) -> dict[str, Any]:
    """Qué cambió. `se_contradice` es lo que dispara la lección.

    Contradecirse no es cambiar de opinión con datos nuevos —eso es lo que se
    espera— sino dar la vuelta al veredicto. Se marca para que quede escrito y
    se pueda revisar si el cambio estuvo justificado.
    """
    if not previo:
        return {"primera_vez": True, "se_contradice": False}
    antes, ahora = previo.get("veredicto"), nuevo.get("veredicto")
    contra = bool(antes and ahora and str(antes).strip().upper()
                  != str(ahora).strip().upper())
    d = {"primera_vez": False, "se_contradice": contra,
         "veredicto_antes": antes, "veredicto_ahora": ahora,
         "fecha_antes": previo.get("fecha")}
    pa, pn = previo.get("precio"), nuevo.get("precio")
    if pa and pn:
        d["precio_desde_entonces_pct"] = round((pn - pa) / pa * 100, 2)
    return d


def _apunta_error(alm, tk: str, previo: dict, nuevo: dict, cambio: dict) -> None:
    """La lección, añadida al final. Nunca se reescribe lo ya apuntado."""
    mov = cambio.get("precio_desde_entonces_pct")
    linea = (f"- **{tk}** · {cambio.get('fecha_antes')} → {nuevo['fecha']}: "
             f"de **{cambio.get('veredicto_antes')}** a "
             f"**{cambio.get('veredicto_ahora')}**"
             + (f" · el precio se movió {mov:+.2f}% en ese tramo" if mov is not None
                else "")
             + ".\n")
    viejo = _texto(alm, RUTA_ERRORES)
    if not viejo:
        viejo = ("# Cuando el agente se contradijo\n\n"
                 "_Cada vuelta de veredicto sobre el mismo ticker queda aquí. No es "
                 "un castigo: es el único sitio donde se puede ver si cambiar de "
                 "opinión estuvo justificado._\n\n")
    alm.guarda(RUTA_ERRORES, viejo + linea)


def _actualiza_indice(alm, tk: str, nuevo: dict) -> None:
    """Una línea por ticker, la del ticker que se acaba de analizar al día."""
    viejo = _texto(alm, RUTA_INDICE)
    lineas = [l for l in viejo.splitlines()
              if l.strip().startswith("|") and not l.strip().startswith("| Ticker")
              and not set(l.strip()) <= set("|- ")]
    lineas = [l for l in lineas if not l.strip().startswith(f"| [{tk}]")]
    p = nuevo.get("puntaje")
    lineas.append(f"| [{tk}](tesis/{tk}.md) | {nuevo['fecha']} | "
                  f"{nuevo.get('veredicto') or '—'} | "
                  f"{'—' if p is None else format(p, 'g')} | {nuevo['agente']} |")
    lineas.sort()
    alm.guarda(RUTA_INDICE,
               "# Memoria del agente\n\n"
               "_Qué se dijo de cada ticker y cuándo. Se lee ANTES de analizar._\n\n"
               "| Ticker | Última revisión | Veredicto | Puntaje | Agente |\n"
               "|---|---|---|---|---|\n" + "\n".join(lineas) + "\n")


# ── Leer ────────────────────────────────────────────────────────────────────

def lee_tesis(ticker: str, alm) -> dict[str, Any] | None:
    """La última revisión guardada de este ticker, o `None` si es la primera vez.

    Se devuelve parseada —no el markdown crudo— porque quien la consume es el
    prompt del agente y la pantalla, y ninguno de los dos quiere un archivo.
    """
    try:
        tk = _saneado(ticker)
    except ValueError:
        return None
    md = _texto(alm, ruta_tesis(tk))
    revs = _revisiones(md)
    if not revs:
        return None
    cab = re.match(r"### (\d{4}-\d{2}-\d{2}) · agente de (\w+)", revs[0])
    d: dict[str, Any] = {"ticker": tk, "markdown": revs[0],
                         "revisiones": len(revs)}
    if cab:
        d["fecha"], d["agente"] = cab.group(1), cab.group(2)
    m = re.search(r"\*\*([^*]+)\*\*(?: · puntaje ([\d.]+))?"
                  r"(?: · precio \$([\d,.]+))?", revs[0])
    if m:
        d["veredicto"] = m.group(1).strip()
        d["puntaje"] = _num(m.group(2))
        d["precio"] = _num((m.group(3) or "").replace(",", ""))
    return d


def contexto_para_el_agente(ticker: str, alm, precio_hoy: float | None = None) -> str:
    """Lo que se le pone DELANTE al modelo antes de analizar.

    Sin esto la memoria sería un archivo bonito que no cambia ninguna decisión.
    El agente tiene que llegar al análisis sabiendo qué dijo la última vez y qué
    ha hecho el precio desde entonces — que es exactamente lo que `CLAUDE.md`
    manda leer antes de tocar un ticker.
    """
    t = lee_tesis(ticker, alm)
    if not t:
        return ""
    partes = [f"=== MEMORIA DE {t['ticker']} (lo que dijiste antes) ===",
              t.get("markdown", "")]
    p_antes, p_hoy = t.get("precio"), _num(precio_hoy)
    if p_antes and p_hoy:
        mov = (p_hoy - p_antes) / p_antes * 100
        partes.append(f"Desde entonces el precio se movió {mov:+.2f}% "
                      f"(de ${p_antes:,.2f} a ${p_hoy:,.2f}).")
    partes.append(
        "Si tu análisis de hoy contradice lo anterior, DILO y explica qué dato "
        "cambió. Si lo confirma, dilo también. No repitas la tesis vieja sin "
        "mirarla: se guardó para que la mires.")
    return "\n".join(x for x in partes if x)
