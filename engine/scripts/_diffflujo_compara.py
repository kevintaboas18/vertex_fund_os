"""Corre el port del cliente de flujo contra las MISMAS páginas que él."""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import wbj.tito.marketsnack as MS   # noqa: E402
from wbj.tito.marketsnack import MarketSnackError, fetch_flow, fetch_market_flow  # noqa: E402

VERDE, ROJO, FIN = "\033[32m", "\033[31m", "\033[0m"

# El MISMO instante que clava el lado de Node, derivado de la misma constante
# del corpus. Tecleado a mano ya se desincronizó una vez.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _diffflujo_casos import AHORA_MS   # noqa: E402

AHORA = datetime.fromtimestamp(AHORA_MS / 1000, tz=timezone.utc)

#: Lo que el port hace a propósito distinto, con el motivo.
DECLARADAS = {
    "marca": "`Date.parse` traga formatos que `datetime.fromisoformat` rechaza "
             "(y las marcas SIN zona las lee como hora local); el port, cuando "
             "no la sabe leer, sigue paginando en vez de cortar la ventana",
}


def _paginas(paginas, urls):
    estado = {"i": 0}

    def falso(url, cookie_header, timeout):
        urls.append(url)
        p = paginas[min(estado["i"], len(paginas) - 1)]
        estado["i"] += 1
        return p

    return falso


def _norm(u: str) -> str:
    """La query como lista ordenada de pares: el orden de los parámetros lo fija
    cada lenguaje y lo que importa es QUÉ se pide, no en qué orden se serializa.
    Aun así se compara el orden, que en los dos debería ser el de construcción."""
    p = urllib.parse.urlsplit(u)
    return p.path + "?" + "&".join(
        f"{k}={v}" for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True))


def main() -> int:
    suyo = json.load(open(os.environ["FLUJO_OUT"], encoding="utf-8"))
    casos = json.load(open(os.environ["FLUJO_CASOS"], encoding="utf-8"))
    os.environ["MARKETSNACK_COOKIE"] = "sesion=abc"

    # El reloj clavado en el mismo instante que el lado de Node.
    class _Reloj(datetime):
        @classmethod
        def now(cls, tz=None):
            return AHORA if tz else AHORA.replace(tzinfo=None)

    MS.datetime = _Reloj

    difs, decl, iguales = [], [], 0
    for caso, s in zip(casos, suyo):
        nombre, o = caso["nombre"], caso.get("opts") or {}
        urls: list[str] = []
        MS._get = _paginas(caso["paginas"], urls)
        vistas: list[list[int]] = []
        kw = {"on_page": lambda p, n: vistas.append([p, n])}
        for js, py in (("period", "period"), ("maxPages", "max_pages"),
                       ("minPremium", "min_premium"), ("targetDays", "target_days")):
            if o.get(js) is not None:
                kw[py] = o[js]
        try:
            r = (fetch_flow(o["symbol"], **kw) if o.get("symbol")
                 else fetch_market_flow(**kw))
        except MarketSnackError as e:
            if "ERROR" not in s:
                difs.append(f"[{nombre}] el port lanzó ({e}) y el suyo no")
            continue
        if "ERROR" in s:
            difs.append(f"[{nombre}] el suyo lanzó ({s['mensaje']}) y el port no")
            continue

        # ¿Se separaron por una marca de tiempo que él sabe leer y el port no?
        marca_rara = any(
            isinstance(x, dict) and x.get("timestamp") is not None
            and not _legible(x.get("timestamp"))
            for pag in caso["paginas"] for x in (pag.get("list") or []))

        for campo, nuestro, suyo_v in (("páginas", r.pages, s["pages"]),
                                       ("truncated", r.truncated, s["truncated"]),
                                       ("trades", len(r.trades), s["trades"])):
            if nuestro == suyo_v:
                iguales += 1
            elif marca_rara and o.get("targetDays"):
                decl.append(f"[marca] {nombre} {campo}: suyo {suyo_v} · port {nuestro}")
            else:
                difs.append(f"[{nombre}] {campo}: suyo {suyo_v} · port {nuestro}")

        nuestras, suyas = [_norm(u) for u in urls], [_norm(u) for u in s["urls"]]
        if nuestras == suyas:
            iguales += 1
        elif marca_rara and o.get("targetDays") and nuestras[:len(suyas)] == suyas:
            decl.append(f"[marca] {nombre}: el port pidió {len(nuestras)} páginas "
                        f"y él {len(suyas)}")
        else:
            difs.append(f"[{nombre}] URLs:\n      suyo {suyas}\n      port {nuestras}")

        if [list(v) for v in vistas] == [list(v) for v in s["progreso"]]:
            iguales += 1
        elif not (marca_rara and o.get("targetDays")):
            difs.append(f"[{nombre}] progreso: suyo {s['progreso']} · port {vistas}")

    print()
    if decl:
        print(f"{len(decl)} divergencia(s) DECLARADAS (esperadas, con motivo en `DECLARADAS`):")
        for d in sorted(set(decl))[:12]:
            print(f"  · {d}")
        if len(set(decl)) > 12:
            print(f"  · … y {len(set(decl)) - 12} más del mismo tipo")
        print()
    if difs:
        print(f"{ROJO}diff_flujo: {len(difs)} divergencia(s) SIN DECLARAR{FIN}")
        for d in difs[:20]:
            print(f"  · {d}")
        return 1
    print(f"  {iguales} comprobaciones · {len(casos)} casos de paginación de flujo")
    print(f"{VERDE}diff_flujo: 0 divergencias sin declarar con su marketsnack.ts{FIN}")
    return 0


def _legible(v) -> bool:
    """¿La sabe leer `fromisoformat`, que es lo que usa el port?"""
    if not isinstance(v, str) or not v:
        return False
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return False
    return d.tzinfo is not None      # una marca naive no se puede comparar


if __name__ == "__main__":
    raise SystemExit(main())
