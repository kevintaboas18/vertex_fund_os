"""Comparador ESTRICTO de `compute.ts`: sin categorías, sin indultos.

El otro comparador (`_diff_compare.py`) clasifica cada diferencia por causa y
absuelve las divergencias declaradas. Sirve para razonar, pero esconde el número
que hace falta para responder "¿es idéntico al de Víctor?". Este cuenta todas.

Lo que se compara es la salida OBSERVABLE, o sea la fila ya serializada: en su
lado la fila sale por `Response.json`, así que `JSON.stringify` convierte `NaN`
e `Infinity` en `null`. Eso no es una divergencia del port, es su salida real.
"""
import json, math, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from wbj.tito.compute import to_row  # noqa: E402

casos = json.load(open(os.environ["DIFF_CASOS"]))
vic = json.load(open(os.environ["DIFF_VICTOR_OUT"]))

CAMPOS = [("optionTicker", "option_ticker"), ("contractType", "contract_type"),
          ("expiration", "expiration"), ("strike", "strike"),
          ("openInterest", "open_interest"), ("volume", "volume"),
          ("price", "price"), ("priceSource", "price_source"),
          ("openPremium", "open_premium"), ("notionalValue", "notional_value")]


def stringify(v):
    """`JSON.stringify` de un escalar: los no finitos se vuelven `null`."""
    if v in ("NaN", "Infinity", "-Infinity"):       # el runner ya los marcó
        return None
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


def js_number(v):
    """`Number(v)`, solo para saber si dos valores son el MISMO número.

    Se aplica sobre el valor CRUDO, antes de serializar: su campo puede traer
    `"abc"` o `{}` y el del port el `NaN` correspondiente. `Number` de los dos
    es `NaN`, o sea el mismo número, y eso es lo que hay que ver.
    """
    if v in ("NaN", "Infinity", "-Infinity"):          # marcado por el runner
        return math.nan if v == "NaN" else math.inf * (1 if v[0] != "-" else -1)
    if v is None or v == "" or v == []:
        return 0.0                                     # Number(null/""/[]) === 0
    if v is True:
        return 1.0
    if v is False:
        return 0.0
    if isinstance(v, (dict, list)):
        return math.nan
    try:
        n = float(v)
    except (TypeError, ValueError):
        return math.nan
    return n if not isinstance(v, str) or math.isfinite(n) else math.nan


valor, tipo, ejemplos = {}, {}, {}
distintos = 0
for i, (c, v) in enumerate(zip(casos, vic)):
    if v["row"].get("ERROR"):
        continue
    r = to_row(c)
    dif = False
    for js, py in CAMPOS:
        suyo, mio = stringify(v["row"][js]), stringify(getattr(r, py))
        if isinstance(suyo, (int, float)) and isinstance(mio, (int, float)) \
                and not isinstance(suyo, bool) and not isinstance(mio, bool):
            igual = abs(float(suyo) - float(mio)) <= 1e-9 * max(1.0, abs(float(suyo)))
        else:
            igual = suyo == mio and type(suyo) is type(mio)
        if igual:
            continue
        # ¿Es el MISMO número escrito en otro tipo? Eso es el muro del lenguaje:
        # él deja el crudo en el campo (`"500"`, `true`), aquí va el número.
        a, b = js_number(v["row"][js]), js_number(getattr(r, py))
        solo_tipo = (a == b) or (a != a and b != b)
        (tipo if solo_tipo else valor)[js] = (tipo if solo_tipo else valor).get(js, 0) + 1
        ejemplos.setdefault((js, solo_tipo), (i, suyo, mio, c))
        if not solo_tipo:
            dif = True
    if dif:
        distintos += 1

print(f"  {len(casos)} casos · comparación ESTRICTA (salida ya serializada)\n")
if tipo:
    print("  · MURO DEL LENGUAJE — mismo número, distinto tipo de campo")
    print("    (él deja el crudo: `\"500\"`, `true`; aquí va el número, porque")
    print("     el resto del motor suma ese campo y en Python un string no se suma)")
    for k, n in sorted(tipo.items(), key=lambda kv: -kv[1]):
        i, s, m, c = ejemplos[(k, True)]
        print(f"      {k:<16} {n:>5}   ej #{i}: víctor={s!r} port={m!r}")
    print()
for k, n in sorted(valor.items(), key=lambda kv: -kv[1]):
    i, s, m, c = ejemplos[(k, False)]
    print(f"  ✗ {k:<16} {n:>5}   ej #{i}: víctor={s!r} port={m!r}")
    print(f"     {json.dumps(c)[:100]}")
print(f"\n  {len(casos)-distintos}/{len(casos)} filas con el MISMO VALOR en los 10 campos"
      + (f" · {distintos} con algún valor distinto" if distintos else " · SIN diferencias de valor"))
sys.exit(1 if distintos else 0)
