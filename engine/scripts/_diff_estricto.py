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


por_campo, ejemplos = {}, {}
distintos = 0
for i, (c, v) in enumerate(zip(casos, vic)):
    if v["row"].get("ERROR"):
        continue
    r = to_row(c)
    dif = []
    for js, py in CAMPOS:
        suyo, mio = stringify(v["row"][js]), stringify(getattr(r, py))
        if isinstance(suyo, (int, float)) and isinstance(mio, (int, float)) \
                and not isinstance(suyo, bool) and not isinstance(mio, bool):
            igual = abs(float(suyo) - float(mio)) <= 1e-9 * max(1.0, abs(float(suyo)))
        else:
            igual = suyo == mio and type(suyo) is type(mio)
        if not igual:
            dif.append(js)
            por_campo[js] = por_campo.get(js, 0) + 1
            ejemplos.setdefault(js, (i, suyo, mio, c))
    if dif:
        distintos += 1

print(f"  {len(casos)} casos · comparación ESTRICTA (salida ya serializada)\n")
for k, n in sorted(por_campo.items(), key=lambda kv: -kv[1]):
    i, s, m, c = ejemplos[k]
    print(f"  ✗ {k:<16} {n:>5}   ej #{i}: víctor={s!r} port={m!r}")
    print(f"     {json.dumps(c)[:100]}")
print(f"\n  {len(casos)-distintos}/{len(casos)} filas idénticas · {distintos} distintas")
sys.exit(1 if distintos else 0)
