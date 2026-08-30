"""Páginas de Massive tal y como llegan por el cable, para comparar SU cliente.

`massive.ts` era uno de los cinco módulos suyos sin diferencial: portado y
leído, pero nunca EJECUTADO al lado del original. El motivo era razonable —es
el módulo que habla con la red— y por eso se quedó fuera tanto tiempo. Pero lo
que decide `fetchOptionChain` no es la red: es qué contrato entra, cuál es el
precio del subyacente, cuántas páginas se piden y cuándo se declara truncada la
cadena. Todo eso es puro en cuanto la respuesta ya está en la mano.

Y no es un módulo cualquiera: de aquí sale el SPOT y salen los contratos. El
spot ancla el cono, los niveles y los tres objetivos; los contratos son el GEX.
Un desacuerdo aquí no se ve en ninguna fórmula —todas siguen siendo las suyas—
y sin embargo mueve todos los números.

Las páginas van en el mismo espíritu que los otros corpus: primero bien
formadas, luego las formas que manda una fuente cuando cambia de esquema.
"""
import json

# El contrato mínimo que `compute.to_row` sabe convertir.
def c(strike=100, exp="2026-09-18", tipo="call", precio=None, **extra):
    d = {"details": {"strike_price": strike, "expiration_date": exp,
                     "contract_type": tipo},
         "open_interest": 10, "day": {"volume": 5, "close": 1.25},
         "greeks": {"gamma": 0.01, "delta": 0.5},
         "implied_volatility": 0.35}
    if precio is not None:
        d["underlying_asset"] = {"price": precio}
    d.update(extra)
    return d


def casos():
    out = []

    def caso(nombre, paginas, max_pages=None):
        out.append({"nombre": nombre, "paginas": paginas, "maxPages": max_pages})

    # ── Bien formadas ────────────────────────────────────────────────────
    caso("una página", [{"results": [c(100, precio=205.5), c(105), c(95)]}])
    caso("tres páginas encadenadas", [
        {"results": [c(100, precio=205.5)], "next_url": "u2"},
        {"results": [c(105), c(110)], "next_url": "u3"},
        {"results": [c(95)]},
    ])
    caso("sin resultados", [{"results": []}])
    caso("results ausente", [{}])
    caso("varios vencimientos", [{"results": [
        c(100, "2026-09-18", precio=205.5), c(100, "2026-10-16"),
        c(105, "2026-11-20"), c(105, "2026-09-18")]}])

    # ── El tope de páginas y `truncated` ─────────────────────────────────
    #
    # `truncated` solo se pone cuando se ALCANZA el tope Y queda `next_url`.
    # Terminar porque no hay `next_url` no es truncar, y confundir las dos
    # cosas hace que el panel avise de una cadena incompleta que está entera.
    caso("tope alcanzado con más páginas detrás", [
        {"results": [c(100, precio=205.5)], "next_url": "u2"},
        {"results": [c(105)], "next_url": "u3"},
        {"results": [c(110)], "next_url": "u4"},
    ], max_pages=2)
    caso("tope alcanzado justo al acabar", [
        {"results": [c(100, precio=205.5)], "next_url": "u2"},
        {"results": [c(105)]},
    ], max_pages=2)
    caso("tope de una sola página", [
        {"results": [c(100, precio=205.5)], "next_url": "u2"},
    ], max_pages=1)
    for mp in ("0", "-3", "2.9", "abc", "", "1e3", "Infinity", "NaN"):
        caso(f"MASSIVE_MAX_PAGES={mp!r}", [
            {"results": [c(100, precio=205.5)], "next_url": "u2"},
            {"results": [c(105)]},
        ], max_pages=mp)

    # ── El precio del subyacente ─────────────────────────────────────────
    #
    # Su condición es `typeof price === "number"`: se queda con el PRIMERO que
    # lo cumpla. Aquí se exige además `> 0` (divergencia declarada: es el spot).
    # Estas filas son las que separan las dos reglas, una por una.
    for px in (205.5, 0, -12.0, "205.5", None, True, False, [], {},
               1e308, -0.0, 0.5):
        caso(f"precio={px!r}", [{"results": [c(100, precio=px), c(105, precio=99.0)]}])
    caso("precio solo en la 2ª página", [
        {"results": [c(100)], "next_url": "u2"},
        {"results": [c(105, precio=205.5)]},
    ])
    caso("underlying_asset no es objeto", [
        {"results": [dict(c(100), underlying_asset="205.5"), c(105, precio=99.0)]}])
    caso("underlying_asset nulo", [
        {"results": [dict(c(100), underlying_asset=None), c(105, precio=99.0)]}])

    # ── Filas que el port descarta (divergencia declarada) ───────────────
    for strike in (0, -5, "abc", None):
        caso(f"strike={strike!r}", [{"results": [c(strike, precio=205.5), c(105)]}])
    for exp in ("", None, "basura"):
        caso(f"vencimiento={exp!r}", [{"results": [c(100, exp, precio=205.5), c(105)]}])

    # ── Formas rotas ─────────────────────────────────────────────────────
    caso("contrato que no es objeto", [{"results": ["x", 5, None, c(100, precio=205.5)]}])
    caso("details ausente", [{"results": [{"open_interest": 3}, c(100, precio=205.5)]}])
    caso("next_url vacío", [{"results": [c(100, precio=205.5)], "next_url": ""}])
    caso("next_url nulo", [{"results": [c(100, precio=205.5)], "next_url": None}])
    return out


#: Páginas de `/v2/aggs/...` para las dos rutas de barras. Vive AQUÍ y no en el
#: runner de Node porque los dos lados tienen que leer exactamente la misma:
#: dos copias del mismo corpus se separan a la primera edición y el diferencial
#: pasaría a comparar cosas distintas sin decirlo.
BARRAS = [
    {"results": [{"t": 1_756_339_200_000, "o": 1, "h": 2, "l": 0.5, "c": 1.5}]},
    {"results": [{"t": 1_756_400_000_000, "o": 1, "h": 2, "l": 0.5, "c": 1.5},
                 {"t": 1_756_486_400_000, "o": 2, "h": 3, "l": 1.5, "c": 2.5}]},
    {"results": []},
    {},
    {"results": [{"t": "1756339200000", "o": 1, "h": 2, "l": 0.5, "c": 1.5}]},
    {"results": [{"o": 1, "h": 2, "l": 0.5, "c": 1.5}]},
    {"results": [{"t": 1_756_339_200_000, "o": "1", "h": True, "l": None, "c": 1.5}]},
    # 23:59:59.999 UTC y 00:00:00.000 UTC: el borde del día que su `toISOString`
    # resuelve en UTC y una conversión local resolvería en otro día.
    {"results": [{"t": 1_756_339_199_999, "o": 1, "h": 2, "l": 0.5, "c": 1.5},
                 {"t": 1_756_339_200_000, "o": 1, "h": 2, "l": 0.5, "c": 1.5}]},
    {"results": [{"t": -1, "o": 1, "h": 2, "l": 0.5, "c": 1.5}]},
    {"results": [{"t": 1_756_339_200_123.7, "o": 1, "h": 2, "l": 0.5, "c": 1.5}]},
]


if __name__ == "__main__":
    print(json.dumps({"cadenas": casos(), "barras": BARRAS}))
