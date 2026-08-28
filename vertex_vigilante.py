"""El vigilante de las tesis: comprueba cada mañana si alguna se rompió.

La tesis siempre supo qué la invalidaría. `Memoria/tesis/NVDA.md` guardaba
esto, palabra por palabra:

    Broken by a confirmed close < zone_low - 0.25*ATR14 with
    volume/median(50d) >= 1.5 and follow-through: two consecutive closes
    beyond the buffer, or one close plus three sessions with no close back
    inside the zone (Cerebro IMPORTANT_LEVELS_ENGINE C).

Un nivel exacto, medible, con datos que el sistema se baja todos los días. Y
**nada lo comprobaba nunca**: una alarma escrita a mano y metida en un cajón.
Esto es el que abre el cajón.

Dos decisiones que lo sostienen:

**La regla no se reescribe aquí.** Se evalúa con la del motor —
`levels_engine.breakout_confirmed`, TECH-BCONF-031, la misma que produjo la
frase— reconstruyendo la zona a partir de los números guardados en
`prediccion.json`. Reimplementar la regla en el vigilante habría creado dos
versiones de la misma condición, y el día que una cambiara la otra seguiría
avisando de otra cosa.

**El ATR y el volumen se recalculan.** No se guardan con la tesis: la regla los
mide sobre las últimas sesiones, y congelar el ATR del día del análisis sería
medir hoy con una regla de hace tres semanas.

Lo que produce son AVISOS, no órdenes. «La tesis de NVDA se rompió por su
propio criterio» es una invitación a volver a mirarla, no a vender.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Callable

#: Cuántas sesiones necesita la regla para poder contestar.
#:
#: `atr14` necesita 14 y `volume_ratio` compara contra la mediana de 50, así
#: que con menos de eso la regla no dice «no se rompió»: dice que no se sabe.
#: Y las dos son cosas MUY distintas para quien lee el aviso.
MINIMO_SESIONES = 60


def niveles_guardados(pred: dict) -> dict | None:
    """El bloque `invalidacion` de una predicción, si lo lleva y está entero."""
    inv = (pred or {}).get("invalidacion")
    if not isinstance(inv, dict):
        return None
    bajo, alto = inv.get("zona_baja"), inv.get("zona_alta")
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool)
               for v in (bajo, alto)):
        return None
    if inv.get("lado") not in ("soporte", "resistencia"):
        return None
    return inv


def _zona(inv: dict):
    """Reconstruye la `ZoneState` que espera el motor.

    `touches` va vacía y `confluence_count` en cero porque
    `breakout_confirmed` no los mira: sólo usa `kind`, `lower` y `upper`. Se
    pasan por ser obligatorios en la estructura, no porque signifiquen algo
    aquí — y decirlo evita que alguien intente «arreglarlos» más adelante.
    """
    from wbj.engines.levels_engine import ZoneState

    bajo, alto = float(inv["zona_baja"]), float(inv["zona_alta"])
    return ZoneState(
        kind="low" if inv["lado"] == "soporte" else "high",
        center=(bajo + alto) / 2.0, lower=bajo, upper=alto,
        timeframe="daily", touches=[], confluence_count=0,
    )


def revisa(pred: dict, barras) -> dict[str, Any] | None:
    """¿Se rompió esta tesis por su propio criterio?

    `barras` es un DataFrame diario con `date/open/high/low/close/volume`, del
    más viejo al más nuevo — la misma forma que come el motor.

    Devuelve `None` cuando la predicción no lleva nivel guardado (las escritas
    antes de que esto existiera). Si lo lleva, devuelve siempre un diccionario,
    incluso para decir que **no se sabe**: un vigilante que calla cuando le
    faltan datos es indistinguible de uno que dice que todo va bien.
    """
    inv = niveles_guardados(pred)
    if inv is None:
        return None

    ticker = str(pred.get("ticker") or "").upper()
    base = {"ticker": ticker, "fecha_tesis": pred.get("date") or pred.get("fecha"),
            "lado": inv["lado"], "zona_baja": inv["zona_baja"],
            "zona_alta": inv["zona_alta"], "etiqueta": inv.get("etiqueta")}

    if barras is None or len(barras) < MINIMO_SESIONES:
        return {**base, "estado": "sin_datos", "roto": None,
                "cierre": None, "nivel": None,
                "detalle": (f"hacen falta {MINIMO_SESIONES} sesiones para medir la "
                            f"regla y hay {0 if barras is None else len(barras)}")}

    from wbj.engines import indicators
    from wbj.engines.levels_engine import breakout_confirmed

    zona = _zona(inv)
    atr = indicators.atr14(barras)
    roto = bool(breakout_confirmed(barras, zona, atr))

    cierre = float(barras["close"].iloc[-1])
    ultimo_atr = atr.iloc[-1]
    ultimo_atr = float(ultimo_atr) if ultimo_atr == ultimo_atr else None
    # El nivel EN DINERO, que es lo que se puede mirar en la pantalla. La
    # fórmula ya está en la tesis y no le dice nada a nadie; «$204,26» sí.
    nivel = None
    if ultimo_atr is not None:
        nivel = (inv["zona_baja"] - 0.25 * ultimo_atr if inv["lado"] == "soporte"
                 else inv["zona_alta"] + 0.25 * ultimo_atr)
        nivel = round(nivel, 2)
    return {**base, "estado": "roto" if roto else "en_pie", "roto": roto,
            "cierre": round(cierre, 2), "nivel": nivel,
            "detalle": None}


def predicciones_abiertas(alm, dias: int = 400, hoy: date | None = None) -> list[dict]:
    """Las predicciones del almacén que todavía tienen algo que vigilar.

    Se lee del almacén y no del disco del repositorio porque es el almacén el
    que sobrevive a un redeploy de Render. Una por ticker, la más reciente:
    vigilar la tesis de hace tres semanas cuando hay una de ayer avisaría de
    una tesis que ya se corrigió sola.
    """
    hoy = hoy or date.today()
    por_ticker: dict[str, dict] = {}
    for p in alm.lista("Reportes", "prediccion.json"):
        try:
            partes = p.relative_to(alm.raiz).parts
            tk, f = partes[1], partes[2]
        except (ValueError, IndexError):
            continue
        try:
            if (hoy - date.fromisoformat(f)).days > dias:
                continue
        except ValueError:
            continue
        try:
            d = json.loads(Path(p).read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(d, dict):
            continue
        # ── Manda la CARPETA, no el payload ─────────────────────────────
        #
        # `Reportes/<TICKER>/<fecha>/` es la ruta canónica del archivo: es de
        # donde `vertex_archivo.lista_reportes` saca las dos cosas. El payload
        # puede traer otras —una copia movida a mano, un `report_id`
        # reutilizado, un análisis relanzado— y creerle tiene dos consecuencias
        # feas, las dos silenciosas:
        #
        #   · con el TICKER equivocado se piden las barras de otra empresa y se
        #     mide la tesis contra el precio de quien no es;
        #   · con la FECHA equivocada gana la tesis vieja y se vigila una que
        #     ya se corrigió sola.
        d["ticker"], d["date"] = tk, d.get("date") or f
        d["_carpeta"] = f
        anterior = por_ticker.get(tk)
        if anterior is None or f > str(anterior.get("_carpeta") or ""):
            por_ticker[tk] = d
    return [por_ticker[k] for k in sorted(por_ticker)]


def revisa_todas(alm, barras_de: Callable[[str], Any],
                 hoy: date | None = None) -> dict[str, Any]:
    """Pasa el vigilante por todas las tesis abiertas.

    `barras_de(ticker)` devuelve el DataFrame diario o `None`. Se inyecta para
    que los casos de prueba no toquen la red.

    El resumen separa **rotas**, **en pie** y **sin datos**. Las terceras no se
    esconden: si el proveedor no contestó para tres tickers, eso se dice, en
    vez de dar por bueno lo que no se pudo medir.
    """
    rotas, en_pie, sin_datos, sin_nivel = [], [], [], []
    for pred in predicciones_abiertas(alm, hoy=hoy):
        tk = str(pred.get("ticker") or "").upper()
        if niveles_guardados(pred) is None:
            sin_nivel.append(tk)
            continue
        try:
            r = revisa(pred, barras_de(tk))
        except Exception as e:                    # noqa: BLE001
            sin_datos.append({"ticker": tk, "estado": "sin_datos", "roto": None,
                              "detalle": f"{type(e).__name__} al medir"})
            continue
        if r is None:
            sin_nivel.append(tk)
        elif r["estado"] == "sin_datos":
            sin_datos.append(r)
        elif r["roto"]:
            rotas.append(r)
        else:
            en_pie.append(r)
    return {"rotas": rotas, "en_pie": en_pie, "sin_datos": sin_datos,
            "sin_nivel": sorted(sin_nivel),
            "revisadas": len(rotas) + len(en_pie) + len(sin_datos)}


def en_palabras(r: dict) -> str:
    """El aviso de una tesis, en español y con el número delante.

    La regla dice «close < zone_low - 0.25*ATR14 with volume/median(50d) >=
    1.5». Eso es correcto y no le dice nada a nadie. Esto dice el precio.
    """
    tk = r.get("ticker") or "?"
    if r.get("estado") == "sin_datos":
        return f"{tk}: no se pudo medir ({r.get('detalle') or 'sin barras'})."
    nivel = r.get("nivel")
    donde = f" (nivel: ${nivel:,.2f})" if isinstance(nivel, (int, float)) else ""
    cierre = r.get("cierre")
    cerro = f" Cerró en ${cierre:,.2f}." if isinstance(cierre, (int, float)) else ""
    if r.get("roto"):
        lado = ("perdió su soporte" if r.get("lado") == "soporte"
                else "superó su resistencia")
        return (f"{tk}: la tesis {lado}{donde} con volumen y confirmación."
                f"{cerro} Toca volver a mirarla.")
    return f"{tk}: en pie{donde}.{cerro}"
