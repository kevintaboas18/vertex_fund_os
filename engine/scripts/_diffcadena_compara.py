"""Corre el port contra las MISMAS páginas y compara con lo que sacó él.

Lo que se compara no son fórmulas —ésas ya las miden `diff_compute` y los tres
`diff_motor`— sino las decisiones del cliente: qué contrato entra, cuál es el
precio del subyacente, cuántas páginas se piden, cuándo se declara truncada la
cadena y con qué fecha se etiqueta cada barra.
"""
from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import wbj.tito.massive as MASS   # noqa: E402
from wbj.tito.massive import (    # noqa: E402
    MassiveError, fetch_bars, fetch_daily_bars, fetch_option_chain,
)

VERDE, ROJO, AMAR, FIN = "\033[32m", "\033[31m", "\033[33m", "\033[0m"


def r6(x):
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return x
    if x != x:
        return "NaN"
    if x in (float("inf"), float("-inf")):
        return "Inf" if x > 0 else "-Inf"
    y = x * 1e6
    # Mismo desbordamiento que su `r6`: 1e308 * 1e6 se sale del double. En JS da
    # `Infinity` y allí está guardado; aquí `round()` sobre infinito revienta.
    if y in (float("inf"), float("-inf")):
        return "Inf" if y > 0 else "-Inf"
    return round(y) / 1e6


#: Lo que el port hace a propósito distinto, con el motivo. Cada entrada dice
#: QUÉ campo y POR QUÉ; si aparece una diferencia fuera de esta lista, falla.
DECLARADAS = {
    "filtro": "el port descarta strike<=0 y vencimiento vacío antes de puntuar "
              "(su destino es GEX, no una tabla)",
    "spot": "el port exige `> 0` además de `typeof number` (NaN/0/negativo no "
            "son un spot utilizable)",
    "ohlc": "el port coacciona el OHLC con `typeof number ? v : 0`; el suyo lo "
            "pasa crudo porque su destino es una gráfica",
    "contrato nulo": "un contrato `null` dentro de `results` LANZA en su "
                     "archivo (`c.underlying_asset` sobre null); el port lo "
                     "salta y sigue con los demás",
    "t intradía": "en `fetchBars` un `t` que no es número da una barra con "
                  "tiempo `NaN` en su archivo (JS coacciona `\"175…\"/1000` y "
                  "`undefined/1000` es NaN); el port se salta esa barra, porque "
                  "una vela sin instante no se puede colocar en el eje",
    "barras": "un `t` que no es número LANZA en su archivo (`new Date(\"175…\")"
              ".toISOString()` tira RangeError y se pierde la descarga entera); "
              "el port se salta esa barra y devuelve las demás",
}


def _sin_fechas(u: str) -> str:
    return re.sub(r"\d{4}-\d{2}-\d{2}/\d{4}-\d{2}-\d{2}", "<desde>/<hasta>", u)


def _paginas(paginas):
    """Sustituye `_get` por las páginas del caso, en orden. Igual que el stub
    de `fetch` del lado de Node: nadie toca la red y los dos leen lo mismo."""
    estado = {"i": 0}
    urls: list[str] = []

    def falso(url, key, ticker, timeout):
        urls.append(url)
        p = paginas[min(estado["i"], len(paginas) - 1)]
        estado["i"] += 1
        return p

    return falso, urls


def _util(px):
    """El precio que SU regla habría elegido, con la nuestra encima."""
    return isinstance(px, (int, float)) and not isinstance(px, bool)


def main() -> int:
    suyo = json.load(open(os.environ["CADENA_OUT"], encoding="utf-8"))
    todo = json.load(open(os.environ["CADENA_CASOS"], encoding="utf-8"))
    casos, barras_corpus = todo["cadenas"], todo["barras"]
    os.environ["MASSIVE_API_KEY"] = "x" * 32

    difs, decl, iguales = [], [], 0

    for caso, s in zip(casos, suyo["cadena"]):
        nombre = caso["nombre"]
        falso, urls = _paginas(caso["paginas"])
        MASS._get = falso
        mp = caso.get("maxPages")
        if mp is None:
            os.environ.pop("MASSIVE_MAX_PAGES", None)
        else:
            os.environ["MASSIVE_MAX_PAGES"] = str(mp)
        vistas: list[list[int]] = []
        try:
            r = fetch_option_chain("AAPL", on_page=lambda p, n: vistas.append([p, n]))
        except MassiveError as e:
            if "ERROR" not in s:
                difs.append(f"[{nombre}] el port lanzó ({e}) y el suyo no")
            continue
        if "ERROR" in s:
            decl.append(f"[contrato nulo] {nombre}: suyo lanzó "
                        f"{s['ERROR']} · port devolvió {len(r.rows)} fila(s)")
            continue

        # ── páginas y truncado: tienen que ser IDÉNTICOS ──────────────────
        if r.pages != s["pages"]:
            difs.append(f"[{nombre}] páginas: suyo {s['pages']} · port {r.pages}")
        else:
            iguales += 1
        if r.truncated != s["truncated"]:
            difs.append(f"[{nombre}] truncated: suyo {s['truncated']} · port {r.truncated}")
        else:
            iguales += 1
        if [list(v) for v in vistas] != [list(v) for v in s["progreso"]]:
            # El progreso cuenta lo ACUMULADO, y el port filtra: solo se compara
            # el número de página, que es lo que decide el bucle.
            if [v[0] for v in vistas] != [v[0] for v in s["progreso"]]:
                difs.append(f"[{nombre}] progreso: suyo {s['progreso']} · port {vistas}")
            else:
                decl.append(f"[filtro] {nombre}: acumulado {s['progreso']} vs {vistas}")
        else:
            iguales += 1
        if [ _sin_fechas(u) for u in urls ] != [ _sin_fechas(u) for u in s["urls"] ]:
            difs.append(f"[{nombre}] URLs: suyo {s['urls']} · port {urls}")
        else:
            iguales += 1

        # ── el precio del subyacente ──────────────────────────────────────
        suyo_px = s["underlyingPrice"]
        nuestro_px = r6(r.underlying_price)
        if suyo_px == nuestro_px:
            iguales += 1
        else:
            crudo = None
            for pag in caso["paginas"]:
                for c in (pag.get("results") or []):
                    if isinstance(c, dict) and isinstance(c.get("underlying_asset"), dict):
                        crudo = c["underlying_asset"].get("price")
                        break
                if crudo is not None:
                    break
            # Solo es declarada si SU regla aceptó algo que la nuestra rechaza
            # por no ser utilizable (NaN, 0, negativo) o por ser un booleano.
            if _util(crudo) and not (isinstance(crudo, (int, float))
                                     and not isinstance(crudo, bool) and crudo > 0):
                decl.append(f"[spot] {nombre}: suyo {suyo_px} · port {nuestro_px}")
            elif isinstance(crudo, bool):
                decl.append(f"[spot] {nombre}: booleano · suyo {suyo_px} · port {nuestro_px}")
            else:
                difs.append(f"[{nombre}] spot: suyo {suyo_px} · port {nuestro_px}")

        # ── los contratos que sobrevivieron ───────────────────────────────
        suyos = [(k, v) for k, v in s["crudos"]]
        nuestros = [(r6(x.strike), x.expiration) for x in r.rows]
        esperado = [(k, v) for k, v in suyos
                    if isinstance(k, (int, float)) and not isinstance(k, bool)
                    and k > 0 and isinstance(v, str) and v]
        if nuestros == esperado:
            iguales += 1
            if len(esperado) != len(suyos):
                decl.append(f"[filtro] {nombre}: {len(suyos)} suyos → {len(esperado)} tras el filtro")
        else:
            difs.append(f"[{nombre}] contratos: esperado {esperado} · port {nuestros}")

    # ── las barras ────────────────────────────────────────────────────────
    for s in suyo["barras"]:
        pag = barras_corpus[s["i"]]
        falso, urls = _paginas([pag])
        MASS._get = falso
        d = fetch_daily_bars("AAPL", 365)
        falso2, urls2 = _paginas([pag])
        MASS._get = falso2
        t = fetch_bars("AAPL", 15, "minute", 10)

        nd = [[b.time, r6(b.open), r6(b.high), r6(b.low), r6(b.close)] for b in d]
        nt = [[r6(b.time), r6(b.open), r6(b.high), r6(b.low), r6(b.close)] for b in t]
        for etiqueta, nuestro, clave in (("diarias", nd, "diarias"),
                                         ("marco", nt, "marco")):
            if s.get(clave + "Error"):
                # Su archivo LANZÓ y el port no. Es la divergencia declarada
                # `barras`: un `t` que no es número mata su descarga entera
                # —`new Date("175…").toISOString()` tira `RangeError`— y aquí
                # esa barra se salta y las demás sobreviven.
                decl.append(f"[barras] {s['i']} {etiqueta}: suyo lanzó "
                            f"{s[clave + 'Error']} · port devolvió {len(nuestro)} barra(s)")
                continue
            suyo_l = s[clave]
            # La FECHA/hora es lo que no puede diferir: es el eje temporal.
            # Salvo cuando el `t` no era un número: ahí él fabrica una vela con
            # instante `NaN` (o coaccionado desde texto) y el port la salta.
            crudas = [b for b in (pag.get("results") or [])
                      if not isinstance((b or {}).get("t") if isinstance(b, dict) else None,
                                        (int, float))
                      or isinstance((b or {}).get("t"), bool)]
            if crudas and len(nuestro) != len(suyo_l):
                decl.append(f"[t intradía] barras {s['i']} {etiqueta}: "
                            f"suyo {suyo_l} · port {nuestro}")
                continue
            if [x[0] for x in nuestro] != [x[0] for x in suyo_l]:
                difs.append(f"[barras {s['i']} {etiqueta}] tiempos: "
                            f"suyo {[x[0] for x in suyo_l]} · port {[x[0] for x in nuestro]}")
            else:
                iguales += 1
            if nuestro != suyo_l:
                decl.append(f"[ohlc] barras {s['i']} {etiqueta}: "
                            f"suyo {suyo_l} · port {nuestro}")
            else:
                iguales += 1
        if _sin_fechas(urls[0]) != s["urlDiaria"]:
            difs.append(f"[barras {s['i']}] URL diaria: suyo {s['urlDiaria']} · port {_sin_fechas(urls[0])}")
        else:
            iguales += 1
        if _sin_fechas(urls2[0]) != s["urlMarco"]:
            difs.append(f"[barras {s['i']}] URL marco: suyo {s['urlMarco']} · port {_sin_fechas(urls2[0])}")
        else:
            iguales += 1

    print()
    if decl:
        print(f"{len(decl)} divergencia(s) DECLARADAS (esperadas, con motivo en `DECLARADAS`):")
        for d in sorted(set(decl))[:14]:
            print(f"  · {d}")
        if len(set(decl)) > 14:
            print(f"  · … y {len(set(decl)) - 14} más del mismo tipo")
        print()
    if difs:
        print(f"{ROJO}diff_cadena: {len(difs)} divergencia(s) SIN DECLARAR{FIN}")
        for d in difs[:25]:
            print(f"  · {d}")
        return 1
    print(f"  {iguales} comprobaciones · {len(casos)} cadenas + {len(suyo['barras'])} de barras")
    print(f"{VERDE}diff_cadena: 0 divergencias sin declarar con su massive.ts{FIN}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
