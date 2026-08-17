#!/usr/bin/env python3
"""Preflight EN VIVO de las dos cajas del Dashboard: resultados y macro.

    FMP_API_KEY=... FRED_API_KEY=... python engine/scripts/preflight_calendario.py

Hermano de `preflight_vivo.py`, y por el mismo motivo. Las dos cajas están
cubiertas por 10 casos de servidor y 7 de navegador, pero **ninguno toca la
red**: el contenedor de desarrollo bloquea `financialmodelingprep.com` y
`api.stlouisfed.org` con 403 de política. Lo que queda abierto no es la lógica
—que se pinten, que se coloquen, que fallen por separado, que el IPC salga
interanual— sino la **forma real** de la respuesta: que el calendario de FMP
acepte un rango de fechas, que los campos se llamen como el código cree y que
las series de FRED traigan los meses que hace falta.

Esto cierra ese hueco. Se corre UNA vez, con las claves puestas, el día que se
quiera confirmar. No es un test: no corre en CI y no tiene mocks.

Y no comprueba una reimplementación: llama a las **funciones de producción**
(`_resultados_calcula`, `_macro_calcula`), así que lo que imprime es
literalmente lo que verían las dos cajas.

No imprime ninguna credencial: solo si está puesta y su longitud.
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from pathlib import Path

_RAIZ = Path(__file__).resolve().parents[2]
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

V, R, A, Z = "\033[32m", "\033[31m", "\033[33m", "\033[0m"
_fallos: list[str] = []


def ok(msg):
    print(f"  {V}✓{Z} {msg}")


def mal(msg):
    _fallos.append(msg)
    print(f"  {R}✗{Z} {msg}")


def aviso(msg):
    print(f"  {A}!{Z} {msg}")


def titulo(t):
    print(f"\n\033[1m── {t} " + "─" * max(0, 58 - len(t)) + f"{Z}")


def _clave(nombre):
    """Si está puesta y cuánto mide. NUNCA el valor."""
    v = (os.environ.get(nombre) or "").strip()
    if v:
        ok(f"{nombre} puesta ({len(v)} caracteres)")
    else:
        mal(f"{nombre} NO está puesta: la caja saldrá con su motivo, no con datos")
    return bool(v)


def main() -> int:
    print("\033[1mPreflight EN VIVO — resultados y macroeconómico\033[0m")

    titulo("1. Las credenciales")
    hay_fmp = _clave("FMP_API_KEY")
    hay_fred = _clave("FRED_API_KEY")

    import requests

    import vertex_api as VA

    # ── 2. FMP: el calendario por RANGO de fechas ────────────────────────────
    titulo("2. FMP · el calendario de resultados por rango")
    if not hay_fmp:
        aviso("sin clave no se puede preguntar; se salta")
    else:
        hoy = date.today()
        hasta = hoy + timedelta(days=VA._CALENDARIO_DIAS)
        try:
            r = requests.get(
                "https://financialmodelingprep.com/stable/earnings-calendar",
                params={"from": hoy.isoformat(), "to": hasta.isoformat(),
                        "apikey": os.environ["FMP_API_KEY"]}, timeout=15)
            if r.status_code != 200:
                mal(f"contesta HTTP {r.status_code} — el endpoint de rango no "
                    "está en este plan, o cambió de nombre")
            else:
                crudo = r.json()
                if not isinstance(crudo, list):
                    mal(f"no devuelve una lista sino {type(crudo).__name__}: "
                        "el filtro de abajo no encontraría nada")
                elif not crudo:
                    aviso("la lista viene VACÍA para los próximos "
                          f"{VA._CALENDARIO_DIAS} días — puede ser real, pero "
                          "revísalo si estamos en plena temporada")
                else:
                    ok(f"{len(crudo)} filas en el rango")
                    fila = crudo[0]
                    for campo, para_que in (
                            ("symbol", "sin él no se puede filtrar por sector"),
                            ("date", "sin ella no hay día que agrupar")):
                        if campo in fila:
                            ok(f"trae `{campo}`")
                        else:
                            mal(f"NO trae `{campo}` — {para_que}. "
                                f"Campos reales: {sorted(fila)[:12]}")
                    if "when" in fila:
                        ok("trae `when` (antes de abrir / tras el cierre)")
                    else:
                        aviso("no trae `when`: las filas saldrán sin decir si "
                              "reporta antes de abrir o tras el cierre")
        except Exception as e:                   # noqa: BLE001
            mal(f"no se pudo preguntar: {e}")

    # ── 2-bis. FMP: el CALENDARIO ECONÓMICO ──────────────────────────────────
    titulo("2-bis. FMP · el calendario económico (salió · esperado · anterior)")
    if not hay_fmp:
        aviso("sin clave no se puede preguntar; se salta")
    else:
        hoy = date.today()
        try:
            r = requests.get(
                "https://financialmodelingprep.com/stable/economic-calendar",
                params={"from": (hoy - timedelta(days=VA._MACRO_DIAS_ATRAS)).isoformat(),
                        "to": (hoy + timedelta(days=VA._MACRO_DIAS_ADELANTE)).isoformat(),
                        "apikey": os.environ["FMP_API_KEY"]}, timeout=15)
            if r.status_code != 200:
                mal(f"contesta HTTP {r.status_code} — sin este endpoint no hay "
                    "ni consenso ni fechas futuras, y la caja cae al respaldo "
                    "de FRED, que no trae ninguna de las dos")
            else:
                crudo = r.json()
                if not isinstance(crudo, list) or not crudo:
                    mal("no devuelve una lista con eventos")
                else:
                    ok(f"{len(crudo)} eventos en la ventana")
                    ej = crudo[0]
                    for campo, para_que in (
                            ("country", "sin él entran los datos de todo el mundo"),
                            ("event", "sin él no se puede filtrar ni enseñar"),
                            ("date", "sin ella no hay ni pasado ni futuro"),
                            ("actual", "sin él no se sabe qué salió"),
                            ("previous", "sin él falta la columna «anterior»")):
                        if campo in ej:
                            ok(f"trae `{campo}`")
                        else:
                            mal(f"NO trae `{campo}` — {para_que}. "
                                f"Campos reales: {sorted(ej)[:12]}")
                    if "estimate" in ej or "consensus" in ej:
                        ok("trae el CONSENSO (`estimate`/`consensus`)")
                    else:
                        mal("NO trae el consenso: sin él la columna «esperado» "
                            "sale vacía y la explicación pierde la sorpresa, "
                            "que es la noticia")
        except Exception as e:                   # noqa: BLE001
            mal(f"no se pudo preguntar el calendario económico: {e}")

    # ── 3. FRED: el respaldo ─────────────────────────────────────────────────
    titulo("3. FRED · el respaldo (niveles, sin consenso ni futuro)")
    if not hay_fred:
        aviso("sin clave no se puede preguntar; se salta")
    else:
        for serie, nombre, pct_directo in VA._MACRO_SERIES:
            obs = VA._fred_observaciones(serie, limite=14)
            if not obs:
                mal(f"{serie} ({nombre}): sin observaciones utilizables")
                continue
            necesita = 13 if not pct_directo else 2
            if len(obs) < necesita:
                mal(f"{serie} ({nombre}): {len(obs)} observaciones y hacen "
                    f"falta {necesita}"
                    + (" para la variación interanual" if not pct_directo
                       else " para saber la dirección"))
            else:
                ok(f"{serie} ({nombre}): {len(obs)} observaciones, "
                   f"la última del {obs[0]['fecha']}")

    # ── 4. Y AHORA LAS FUNCIONES DE PRODUCCIÓN ───────────────────────────────
    #
    # Esto es lo que de verdad cierra el hueco: no una reimplementación, sino
    # el mismo código que corre en el servidor. Si aquí sale, la caja sale.
    titulo("4. Las dos cajas, con el código que corre en el servidor")

    res = VA._resultados_calcula()
    if res["filas"]:
        ok(f"Resultados: {len(res['filas'])} empresas de los once sectores")
        for f in res["filas"][:6]:
            cuando = f" ({f['cuando']})" if f.get("cuando") else ""
            print(f"      {f['fecha']}  {f['ticker']:6} {f['sector']}{cuando}")
        if len(res["filas"]) > 6:
            print(f"      … y {len(res['filas']) - 6} más")
    else:
        (aviso if not hay_fmp else mal)(f"Resultados vacío — motivo: {res['motivo']}")

    mac = VA._macro_calcula()
    if mac.get("publicados") or mac.get("proximos"):
        ok(f"Macro por CALENDARIO: {len(mac['publicados'])} publicados, "
           f"{len(mac['proximos'])} por venir")
        for f in mac["publicados"][:5]:
            def _n(x):
                return "—" if x is None else f"{x:g}"
            print(f"      {f['fecha'][:10]}  {f['evento'][:38]:38} "
                  f"salió {_n(f['salio']):>8} · esperado {_n(f['esperado']):>8} "
                  f"· anterior {_n(f['anterior']):>8}")
        for f in mac["proximos"][:5]:
            print(f"      {f['fecha'][:10]}  {f['evento'][:38]:38} (por venir)")
        con_consenso = [f for f in mac["publicados"] if f["esperado"] is not None]
        if mac["publicados"] and not con_consenso:
            mal("NINGÚN publicado trae consenso: la columna «esperado» saldrá "
                "vacía entera y la explicación se queda sin la sorpresa")
        elif con_consenso:
            ok(f"{len(con_consenso)} de {len(mac['publicados'])} traen consenso")
    elif mac.get("filas"):
        aviso("Macro cayó al RESPALDO de FRED: hay niveles pero no habrá ni "
              "consenso ni próximas fechas")
        for f in mac["filas"]:
            print(f"      {f['nombre']:32} {f['valor']:+7.2f}%  {f['fecha']}")
        ipc = next((f for f in mac["filas"] if f["serie"] == "CPIAUCSL"), None)
        if ipc and abs(ipc["valor"]) > 25:
            mal(f"el IPC sale {ipc['valor']}%: eso es el ÍNDICE crudo, no la "
                "variación interanual")
    else:
        (aviso if not hay_fmp else mal)(f"Macro vacío — motivo: {mac['motivo']}")

    print("\n" + "=" * 62)
    if _fallos:
        print(f"  {R}{len(_fallos)} problema(s).{Z} Pásaselos tal cual al agente:")
        for f in _fallos:
            print(f"    · {f}")
        return 1
    print(f"  {V}Todo verde.{Z} Las dos cajas del Dashboard traen datos reales.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
