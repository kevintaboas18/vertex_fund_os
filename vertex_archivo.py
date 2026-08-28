"""Los reportes de los DOS agentes, cada uno en su carpeta.

Kevin lo pidió explícito: *«cada uno guarda reportes diferente y en lugar
diferente»*. No es capricho — son dos productos que no se parecen:

| | Agente de ACCIONES | Agente de OPCIONES |
|---|---|---|
| Dónde | `Reportes/` | `Proyecciones/` |
| Qué produce | tesis de inversión a 1-3 años | scorecard de flujo 0-100 |
| Con qué | 6 especialistas + valuación + gates | 6 sub-agentes sobre la cadena |
| Horizonte | 12 meses | 10 / 20 / 30 días |
| Track record | `prediccion.json` (`wbj track`) | `prediccion.json` por horizonte |

Compartir carpeta obligaría a abrir el archivo para saber de cuál es. Separados,
la ruta ya lo dice — y `wbj track` sigue leyendo `Reportes/` exactamente donde
`CLAUDE.md` lo define, sin migración ni sorpresas.

Cada reporte es **un archivo JSON legible** más un `.md` que lo resume, para que
se pueda abrir en GitHub sin descargar nada. El índice por agente es un
Markdown con una fila por análisis: es lo que se lee de un vistazo.

La base SQLite deja de ser la fuente de verdad y pasa a ser **caché de
consulta**: se sigue usando para ordenar y filtrar rápido, pero si se borra, se
reconstruye desde estos archivos. Al revés no: si se borra el archivo, el
reporte se perdió.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

import vertex_almacen
from vertex_almacen import DIR_ACCIONES, DIR_OPCIONES, Almacen

__all__ = [
    "ACCIONES",
    "OPCIONES",
    "guarda_reporte_acciones",
    "guarda_reporte_opciones",
    "lee_reporte",
    "lista_reportes",
    "reconstruye_indice",
    "carpeta_de",
]

#: Los dos agentes, por su nombre corto. Es lo que viaja en las rutas y en los
#: tests, para que «acciones» y «opciones» no se escriban a mano en 20 sitios.
ACCIONES = "acciones"
OPCIONES = "opciones"

_CARPETA = {ACCIONES: DIR_ACCIONES, OPCIONES: DIR_OPCIONES}

#: El nombre del archivo principal de cada agente. Distintos a propósito: si
#: algún día alguien mira una carpeta suelta, el nombre dice de qué agente es.
_ARCHIVO = {ACCIONES: "reporte.json", OPCIONES: "scorecard.json"}

def _por_defecto() -> Almacen:
    """El almacén vivo, resuelto AHORA.

    Se mira `vertex_almacen.almacen` en cada llamada en vez de guardarse una
    referencia al importar: reemplazar esa variable (un proceso nuevo, un test)
    tiene que cambiar dónde se escribe, y con una copia congelada no lo hacía —
    los reportes seguían cayendo en el directorio viejo, que ya no se respalda.
    """
    return vertex_almacen.almacen


#: Un ticker de verdad tiene 1-12 caracteres. Los más largos NO se recortan: dos
#: símbolos distintos que empiecen igual acabarían en la misma carpeta y
#: mezclarían dos historiales sin que nada avisara. Y no puede empezar por punto
#: —`..ETC` es un nombre de carpeta legal y confuso—.
_TICKER_OK = re.compile(r"^(?!\.)[A-Z0-9._-]{1,12}$")
_FECHA_OK = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def carpeta_de(agente: str) -> str:
    """`Reportes` o `Proyecciones`. Lanza con un agente que no existe: un typo
    no puede acabar escribiendo en una tercera carpeta fantasma."""
    try:
        return _CARPETA[agente]
    except KeyError:
        raise ValueError(f"agente desconocido: {agente!r} (usa {ACCIONES!r} u {OPCIONES!r})")


def _saneado(ticker: str) -> str:
    """El ticker, en mayúsculas y sin nada que pueda ser una ruta.

    El ticker viene de la caja de texto del usuario y acaba siendo un nombre de
    carpeta. `Almacen.ruta` ya cierra la travesía, pero aquí se normaliza
    ANTES para que el mismo símbolo escrito de dos formas (`aapl`, ` AAPL `) no
    produzca dos carpetas distintas y parta el historial en dos.
    """
    limpio = re.sub(r"[^A-Za-z0-9._-]", "", (ticker or "").strip()).upper()
    if not limpio or not _TICKER_OK.match(limpio):
        raise ValueError(f"ticker inválido: {ticker!r}")
    return limpio


def _fecha(cuando: datetime | str | None = None) -> str:
    if isinstance(cuando, str):
        if not _FECHA_OK.match(cuando):
            raise ValueError(f"fecha inválida: {cuando!r}")
        return cuando
    return (cuando or datetime.now(timezone.utc)).strftime("%Y-%m-%d")


def _base(agente: str, ticker: str, fecha: str) -> str:
    return f"{carpeta_de(agente)}/{_saneado(ticker)}/{fecha}"


# ── Guardar ─────────────────────────────────────────────────────────────────


#: Quién escribe el resumen legible de cada agente.
_RESUMEN = {}


def _guarda(agente: str, ticker: str, payload: dict,
            cuando=None, alm: Almacen | None = None) -> dict[str, str]:
    """El guardado común: el JSON entero + un `.md` legible + el índice.

    Nunca recorta el payload. El tope de 2 MB que había en la base era una
    limitación de la COLUMNA de SQLite, no del dato; en un archivo no aplica y
    por eso el reporte completo cabe entero.

    El resumen se genera del payload YA ENRIQUECIDO, no del que llegó: si no,
    la cabecera del `.md` sale sin fecha, porque la fecha se añade aquí. Es
    exactamente lo que pasaba antes de este comentario.
    """
    a = alm or _por_defecto()
    tk, f = _saneado(ticker), _fecha(cuando)
    base = _base(agente, tk, f)
    entero = dict(payload)
    entero.setdefault("ticker", tk)
    entero["fecha"] = f
    entero["agente"] = agente
    entero["guardado_en"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    a.guarda(f"{base}/{_ARCHIVO[agente]}", entero)
    a.guarda(f"{base}/RESUMEN.md", _RESUMEN[agente](tk, entero))
    reconstruye_indice(agente, alm=a)
    # Y la MEMORIA: qué se dijo de este ticker, para que el próximo análisis
    # llegue sabiéndolo. Sin esto, `Memoria/` no existía y cada análisis
    # empezaba sin saber qué se había dicho la semana pasada.
    #
    # Falla en silencio a propósito: perder la memoria de un ticker es malo,
    # perder el REPORTE por no poder escribirla sería peor.
    try:
        import vertex_memoria as _MEM
        entero["memoria_cambio"] = _MEM.escribe_tesis(agente, tk, entero, a)
    except Exception:                              # noqa: BLE001
        pass
    # Se sube AHORA, no dentro de veinte segundos.
    #
    # El hilo de fondo sincroniza cada `SEGUNDOS_RAPIDO`, y esa ventana es
    # exactamente el hueco por el que se pierden cosas: en Render, pulsar
    # «Deploy» mata el contenedor y borra el disco, así que un reporte escrito
    # hace quince segundos no llega a subir nunca. Un análisis tarda un minuto
    # largo en producirse; esperar un segundo más a que quede guardado no se
    # nota, y perderlo sí.
    try:
        a.sincroniza(mensaje=f"reporte de {agente}: {tk}")
    except Exception:                          # noqa: BLE001
        pass                                   # el hilo de fondo lo reintenta
    return {"json": f"{base}/{_ARCHIVO[agente]}", "md": f"{base}/RESUMEN.md"}


def guarda_reporte_acciones(ticker: str, payload: dict, *, cuando=None,
                            alm: Almacen | None = None) -> dict[str, str]:
    """Un análisis del agente de ACCIONES → `Reportes/<TICKER>/<fecha>/`.

    Convive con el `prediccion.json` que escribe `guarda_prediccion` en esa
    misma carpeta: uno es el reporte, el otro la predicción congelada que
    `wbj track` compara después contra el precio real. No se tocan.
    """
    return _guarda(ACCIONES, ticker, payload, cuando=cuando, alm=alm)


def guarda_prediccion(ticker: str, payload: dict, *, cuando=None,
                      alm: Almacen | None = None) -> str:
    """La predicción congelada → `Reportes/<TICKER>/<fecha>/prediccion.json`.

    **Esto es lo que hace que exista un track record.** `_wbj_write_prediccion`
    la escribía junto a `vertex_api.py`, o sea en el disco del repositorio, y
    Render en plan free **no tiene disco persistente**: cada redeploy y cada
    despertar tras dormir lo borra. Medido el 28/08/2026 sobre la rama `datos`:
    63 ficheros bajo `Reportes/` —los `reporte.json` y sus `RESUMEN.md`, que sí
    pasan por el almacén— y **cero `prediccion.json`**. Por eso
    `Memoria/calibracion.md` seguía diciendo «sin predicciones todavía»
    mientras el panel llevaba semanas produciendo análisis: no es que el
    horizonte no hubiera vencido, es que **la predicción no llegaba a existir**.

    No pasa por `_guarda` a propósito: aquél escribe el reporte, su `.md`, el
    índice y la memoria. Aquí sólo hay que dejar un JSON quieto en la carpeta
    que ya existe, sin tocar el índice ni reescribir la tesis.

    Dos análisis del mismo ticker el mismo día se pisan, igual que en el
    fichero local: la carpeta es por día, y el track record quiere UNA
    predicción por ticker y fecha. Las dos copias dicen lo mismo, que es lo
    que evita tener que decidir cuál manda.
    """
    a = alm or _por_defecto()
    tk, f = _saneado(ticker), _fecha(cuando)
    ruta = f"{_base(ACCIONES, tk, f)}/prediccion.json"
    a.guarda(ruta, payload)
    # Se sube AHORA, por el mismo motivo que el reporte: la ventana del hilo de
    # fondo es justo por donde se pierden las cosas cuando Render reinicia.
    try:
        a.sincroniza(mensaje=f"predicción de {tk}")
    except Exception:                          # noqa: BLE001
        pass                                   # el hilo de fondo lo reintenta
    return ruta


def guarda_reporte_opciones(ticker: str, payload: dict, *, cuando=None,
                            alm: Almacen | None = None) -> dict[str, str]:
    """Un scorecard del agente de OPCIONES → `Proyecciones/<TICKER>/<fecha>/`."""
    return _guarda(OPCIONES, ticker, payload, cuando=cuando, alm=alm)


# ── Leer ────────────────────────────────────────────────────────────────────


def lee_reporte(agente: str, ticker: str, fecha: str,
                alm: Almacen | None = None) -> dict | None:
    a = alm or _por_defecto()
    return a.lee_json(f"{_base(agente, ticker, fecha)}/{_ARCHIVO[agente]}")


def lista_reportes(agente: str, ticker: str = "",
                   alm: Almacen | None = None) -> list[dict[str, Any]]:
    """Todos los análisis de un agente, del más reciente al más viejo.

    Lee el DIRECTORIO, no la base: es la comprobación de que los archivos son
    la fuente de verdad. Si SQLite desapareciera, esto sigue contestando.
    """
    a = alm or _por_defecto()
    raiz = carpeta_de(agente) + (f"/{_saneado(ticker)}" if ticker else "")
    salida = []
    for p in a.lista(raiz, _ARCHIVO[agente]):
        try:
            partes = p.relative_to(a.raiz).parts
            tk, f = partes[1], partes[2]
        except (ValueError, IndexError):
            continue
        d = a.lee_json("/".join(p.relative_to(a.raiz).parts)) or {}
        salida.append({
            "agente": agente, "ticker": tk, "fecha": f,
            "ruta": "/".join(p.relative_to(a.raiz).parts),
            "titular": _titular(agente, d),
        })
    return sorted(salida, key=lambda x: (x["fecha"], x["ticker"]), reverse=True)


def _titular(agente: str, d: dict) -> str:
    """Una línea que resume el análisis, para el índice y la lista."""
    if agente == ACCIONES:
        rec = d.get("recommendation") or d.get("veredicto") or "—"
        total = d.get("total_score", d.get("score"))
        return f"{rec}" + (f" · {total}/100" if total is not None else "")
    ver = d.get("verdict") or "—"
    sc = d.get("score")
    return f"{ver}" + (f" · {sc}/100" if sc is not None else "")


# ── Índices legibles ────────────────────────────────────────────────────────


def reconstruye_indice(agente: str, alm: Almacen | None = None) -> str:
    """Reescribe `<carpeta>/INDICE.md` desde los archivos que hay.

    Se reconstruye entero en vez de añadir una línea: así el índice **no puede
    mentir**. Un índice que se edita por incrementos acaba citando reportes que
    ya no están, y entonces hay que abrir cada uno para saber cuáles existen de
    verdad — que es exactamente lo que un índice viene a evitar.
    """
    a = alm or _por_defecto()
    filas = lista_reportes(agente, alm=a)
    titulo = ("Reportes del agente de ACCIONES" if agente == ACCIONES
              else "Reportes del agente de OPCIONES")
    que = ("Tesis de inversión a 1-3 años: 6 especialistas, valuación y gates "
           "de perfil. El `prediccion.json` de cada carpeta es lo que "
           "`wbj track` compara después contra el precio real."
           if agente == ACCIONES else
           "Scorecard de flujo 0-100: 6 sub-agentes sobre la cadena de opciones "
           "y la cinta, con escenarios a 10 / 20 / 30 días.")
    lineas = [
        f"# {titulo}",
        "",
        que,
        "",
        f"Generado por `vertex_archivo.reconstruye_indice` · {len(filas)} análisis · "
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC",
        "",
        "| Fecha | Ticker | Veredicto | Archivo |",
        "|---|---|---|---|",
    ]
    for f in filas:
        lineas.append(f"| {f['fecha']} | **{f['ticker']}** | {f['titular']} | "
                      f"[`{f['ruta']}`]({f['ruta'].split('/', 1)[1]}) |")
    if not filas:
        lineas.append("| — | — | _todavía no hay análisis_ | — |")
    texto = "\n".join(lineas) + "\n"
    a.guarda(f"{carpeta_de(agente)}/INDICE.md", texto)
    return texto


# ── Los dos resúmenes en Markdown ───────────────────────────────────────────


def _n(x, dec=2, pre="", suf=""):
    if not isinstance(x, (int, float)) or isinstance(x, bool):
        return "—"
    return f"{pre}{x:,.{dec}f}{suf}"


def _md_acciones(ticker: str, d: dict) -> str:
    """El reporte de acciones, legible sin abrir el JSON.

    Lleva los seis pesos porque son la mitad del contrato del sistema: un score
    total sin el desglose por categoría es un número sin evidencia detrás, y la
    regla innegociable del proyecto lo prohíbe.
    """
    cat = d.get("categories") or d.get("categorias") or {}
    filas = "\n".join(
        f"| {k} | {(v or {}).get('score', v) if isinstance(v, dict) else v} |"
        for k, v in cat.items()) or "| — | _sin desglose_ |"
    return f"""# {ticker} · agente de ACCIONES · {d.get('fecha', '')}

| | |
|---|---|
| Veredicto | **{d.get('recommendation') or d.get('veredicto') or '—'}** |
| Score total | {d.get('total_score', d.get('score', '—'))} / 100 |
| Perfil | {d.get('profile') or d.get('perfil') or '—'} |
| Precio al analizar | {_n(d.get('price_at_analysis'), 2, '$')} |
| Valor justo | {_n(d.get('fair_value'), 2, '$')} |
| Potencial | {_n(d.get('upside_pct'), 1, '', '%')} |
| Convicción | {d.get('conviction', '—')} |

## Las 6 categorías

| Categoría | Score |
|---|---|
{filas}

## Escenarios a 12 meses

| | Precio |
|---|---|
| Alcista | {_n((d.get('targets') or {}).get('bull') or d.get('target_bull'), 2, '$')} |
| Base | {_n((d.get('targets') or {}).get('base') or d.get('target_base'), 2, '$')} |
| Bajista | {_n((d.get('targets') or {}).get('bear') or d.get('target_bear'), 2, '$')} |

## Tesis

{d.get('thesis') or d.get('tesis') or '_sin tesis guardada_'}

---
*Clasificación de research, no una orden de compra o venta. El reporte completo,
con toda su evidencia, está en `{_ARCHIVO[ACCIONES]}`, en esta misma carpeta.*
"""


def _md_opciones(ticker: str, d: dict) -> str:
    """El scorecard de opciones, legible sin abrir el JSON."""
    sc = d.get("scores") or {}
    NOMBRE = {"aggression": "Agresividad", "conviction": "Convicción",
              "unusuality": "Inusualidad", "structure": "Estructura",
              "iv_context": "Contexto IV", "validation": "Confirmación de precio"}
    # Los pesos se LEEN DEL MOTOR, no se escriben aquí.
    #
    # Escritos a mano estaban mal: llevaban los del agente de ACCIONES
    # (20/15/20/20/15/10) en vez de los de opciones (20/20/20/15/10/15), así que
    # cuatro de los seis salían con el peso equivocado. Un resumen que dice qué
    # pesa cada categoría y se equivoca es peor que uno que no lo diga —
    # y leyéndolos de `prediction.WEIGHTS` no pueden volver a separarse.
    try:
        from wbj.tito.prediction import WEIGHTS as PESO
    except Exception:                            # noqa: BLE001 — el motor puede faltar
        PESO = {}
    filas = "\n".join(
        f"| {NOMBRE.get(k, k)} | {'—' if v is None else v}/10 | {PESO.get(k, '—')} pts |"
        for k, v in sc.items()) or "| — | — | _sin desglose_ |"

    pr = d.get("predictions") or {}
    esc = []
    for h in sorted(pr, key=lambda x: int(x)):
        p = pr[h] or {}
        esc.append(f"| {h} días | {_n((p.get('bear') or {}).get('target'), 2, '$')} | "
                   f"{_n((p.get('base') or {}).get('target'), 2, '$')} | "
                   f"{_n((p.get('bull') or {}).get('target'), 2, '$')} | "
                   f"{p.get('confidence', '—')} |")
    g = d.get("gex") or {}
    avisos = "\n".join(f"- ⚠ {w}" for w in (d.get("warnings") or [])) or "_ninguno_"

    # ── Lo que el JSON archivado ya traía y el resumen callaba ──────────────
    #
    # Un `RESUMEN.md` es lo que se lee dentro de tres meses para saber qué dijo
    # el agente. Tenía el score y los escenarios, y nada de las tres cosas que
    # convierten un escenario en algo con lo que decidir: dónde están los
    # niveles, qué dinero se movió y —sobre todo— si este agente ha acertado.
    # Estaban en el `scorecard.json` desde hace rondas; el resumen no las leía.

    # Niveles, con su probabilidad de toque. `touch` viaja como FRACCIÓN 0-1
    # (`prob_touch`), igual que la lee el panel — escribirlo sin el ×100 pinta
    # "1%" donde el motor dice 81%, que es peor que no ponerlo.
    def _lvl(l):
        f, toca = l.get("strength"), l.get("touch")
        return (f"| {_n(l.get('price'), 2, '$')} | {l.get('kind') or '—'} | "
                f"{'—' if f is None else round(f)} | "
                f"{'—' if toca is None else str(round(toca * 100)) + '%'} | "
                f"{l.get('why') or ''} |")
    # `levels` llega en DOS formas y hay que aguantar las dos:
    #
    #   · La que sirve `/api/projection-targets` HOY: un **diccionario** con
    #     `supports`, `resistances`, `tolerance_pct`, `key_support` y
    #     `key_resistance`.
    #   · Una **lista** plana de niveles, que es lo que guardan los reportes
    #     archivados de antes. Un resumen que no sepa leerlos convierte el
    #     archivo viejo en ilegible, y el archivo es justo lo que no se puede
    #     perder.
    #
    # Se cortaba con `[:12]` dando por hecho que era lista. Con el dict real
    # eso lanza `unhashable type: 'slice'`, y como `_archiva_opciones` traga la
    # excepción el fallo era INVISIBLE: `scorecard.json` se escribe antes y
    # sobrevivía; a partir de aquí se perdían el `RESUMEN.md`, el índice de
    # `Proyecciones/` y la tesis del ticker en `Memoria/`. Desde la ronda 14.
    _lv = d.get("levels")
    if isinstance(_lv, dict):
        # Los dos lados juntos y de más fuerte a menos: el resumen tiene que
        # enseñar el suelo Y el techo, no doce soportes seguidos.
        _todos = list(_lv.get("supports") or []) + list(_lv.get("resistances") or [])
        _todos.sort(key=lambda l: (l.get("strength") or 0), reverse=True)
    else:
        _todos = list(_lv or [])                 # la forma vieja, ya ordenada
    niveles = "\n".join(_lvl(l) for l in _todos[:12])

    # Los tres flujos más grandes del día. El score dice «convicción 8/10»; esto
    # dice de qué contratos salió ese 8. Sin `underlying`: son del ticker de la
    # carpeta, y el motor no lo repite en cada fila.
    def _flow(t):
        cp = "C" if t.get("type") == "call" else ("P" if t.get("type") == "put" else "?")
        dte = t.get("dte")
        vence = str(t.get("expiration") or "—")
        if dte is not None:
            vence += f" ({round(dte)}d)"
        return (f"| {ticker} {_n(t.get('strike'), 2, '$')}{cp} | {vence} | "
                f"{_n(t.get('premium'), 0, '$')} | "
                f"{'alcista' if t.get('alcista') else 'bajista'} |")
    flujos = "\n".join(_flow(t) for t in (d.get("top_flows") or [])[:3])

    # El track record. Es lo que separa «el agente dice 78» de «el agente dice
    # 78 y las últimas seis veces se pasó un 6% de largo». Los nombres son los
    # que sirve la ruta, no unos parecidos: `horizon_days`, `base` (el escenario
    # base que se predijo), `actual_close` y `error_pct`.
    st = (d.get("memory") or {}).get("stats") or {}

    def _ev(e):
        err = e.get("error_pct")
        return (f"| {e.get('date') or '—'} | {e.get('horizon_days', '—')}d | "
                f"{_n(e.get('base'), 2, '$')} | {_n(e.get('actual_close'), 2, '$')} | "
                f"{'—' if err is None else format(err, '+.1f') + '%'} | "
                f"{e.get('best') or '—'} |")
    ev_filas = "\n".join(_ev(e) for e in (st.get("evals") or [])[:8])
    _venc = st.get("predicciones_vencidas")
    if _venc:
        _dir, _ses = st.get("dir_hit_rate"), st.get("sesgo_pct")
        calib = (f"**{_venc}** predicciones vencidas · acierto de dirección "
                 f"**{'—' if _dir is None else str(round(_dir)) + '%'}** · sesgo medio "
                 f"**{'—' if _ses is None else format(_ses, '+.1f') + '%'}**")
        if _ses is not None and abs(_ses) > 10:
            calib += ("\n\n> ⚠ El sesgo medio pasa de ±10%: los targets de arriba "
                      "se leen con la confianza a la baja.")
    else:
        calib = "_todavía no hay predicciones vencidas: el track record se llena solo_"

    return f"""# {ticker} · agente de OPCIONES · {d.get('fecha', '')}

| | |
|---|---|
| Veredicto | **{d.get('verdict') or '—'}** |
| Score de flujo | {d.get('score', '—')} / 100 |
| Spot | {_n(d.get('spot'), 2, '$')} |
| Régimen de gamma | {g.get('regime') or '—'} |
| Nodo imán | {_n(g.get('king_strike'), 2, '$')} |
| Gamma flip | {_n(g.get('flip_strike'), 2, '$')} |
| Sub-agentes activos | {d.get('active', '—')} de 6 |

## Los 6 sub-agentes

| Sub-agente | Score | Peso |
|---|---|---|
{filas}

## Escenarios por horizonte

| Horizonte | Bajista | Base | Alcista | Confianza |
|---|---|---|---|---|
{chr(10).join(esc) or '| — | — | — | — | _sin escenarios_ |'}

## Niveles importantes

| Precio | Tipo | Fuerza | P(toque) | Por qué |
|---|---|---|---|---|
{niveles or '| — | — | — | — | _sin niveles_ |'}

## Los 3 flujos más grandes

| Contrato | Vence | Prima | Apuesta |
|---|---|---|---|
{flujos or '| — | — | — | _sin flujos notables_ |'}

## Track record de este agente

{calib}

| Fecha | Horizonte | Predijo (base) | Pasó | Error | Escenario que acertó |
|---|---|---|---|---|---|
{ev_filas or '| — | — | — | — | — | _sin predicciones vencidas_ |'}

## Advertencias

{avisos}

---
*Clasificación de research, no una orden de compra o venta. El scorecard
completo está en `{_ARCHIVO[OPCIONES]}`, en esta misma carpeta.*
"""


#: Se registran al final, cuando las dos funciones ya existen. Un `dict` y no un
#: `if`: añadir un tercer agente sería una línea aquí, no un caso más en cuatro
#: sitios distintos.
_RESUMEN[ACCIONES] = _md_acciones
_RESUMEN[OPCIONES] = _md_opciones
