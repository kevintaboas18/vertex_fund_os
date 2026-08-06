"""Compara `_js_number` y `_date_parse` contra el `Number()`/`Date.parse()` reales.

Diferencial a nivel de PRIMITIVA. Es el que pilla lo que el de fila no puede:
que `float("1_000")` valga 1000 y `Number("1_000")` sea NaN, o que
`float("infinity")` sea infinito y `Number("infinity")` sea NaN.

Se exceptúan dos cosas, las dos declaradas:
  · el parseo *legacy* de fechas, que ECMA-262 deja implementation-defined;
  · el timestamp naive, que aquí se lee en UTC a propósito (ver `_date_parse`).
"""
import json, math, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _diffprim_casos import LEGACY_DE_V8                      # noqa: E402
from wbj.tito.compute import _js_number                       # noqa: E402
from wbj.tito.stores import _date_parse                       # noqa: E402

js = json.load(open(os.environ["PRIM_OUT"]))


def desnum(x):
    return (math.nan if x == "NaN" else math.inf if x == "Infinity"
            else -math.inf if x == "-Infinity" else float(x))


def igual(a, b):
    return (a != a and b != b) or a == b


def fuera_de_rango_py(v):
    """Año fuera de lo que `datetime` puede representar (empieza en el 1).

    Muro del lenguaje, no una decisión de diseño: `Date.parse` acepta el año
    extendido negativo y `datetime` no llega ahí.
    """
    s = v.strip()
    if s[:1] not in "+-" or not s[1:7].isdigit():
        return False
    anio = int(s[1:7]) * (-1 if s[0] == "-" else 1)
    return not (1 <= anio <= 9999)


def es_naive(v):
    """Fecha-hora ISO sin offset: la divergencia declarada de `_date_parse`."""
    s = v.strip()
    if not (len(s) >= 11 and (s[10] in "T ")):
        return False
    return not (s.endswith(("Z", "z")) or "+" in s[10:] or "-" in s[10:])


mal_num, mal_dp, exentos = [], [], 0
for e in js:
    v = e["v"]
    mio = _js_number(v)
    if not igual(desnum(e["num"]), mio):
        mal_num.append((v, e["num"], mio))
    if isinstance(v, str):
        suyo, md = desnum(e["dp"]), _date_parse(v)
        if igual(suyo, md):
            continue
        if v in LEGACY_DE_V8 or es_naive(v) or fuera_de_rango_py(v):
            exentos += 1
        else:
            mal_dp.append((v, e["dp"], md))

print(f"  Number()      {len(js):>3} valores · {len(mal_num)} discrepancias")
for v, s, m in mal_num:
    print(f"     ✗ {v!r:<18} JS={s!r:<14} port={m!r}")
print(f"  Date.parse()  {sum(1 for e in js if isinstance(e['v'], str)):>3} cadenas · "
      f"{len(mal_dp)} discrepancias "
      f"({exentos} exentas: legacy de V8, naive declarado, año fuera de datetime)")
for v, s, m in mal_dp:
    print(f"     ✗ {v!r:<30} JS={s!r:<16} port={m!r}")

if not mal_num and not mal_dp:
    print("\n  las dos primitivas coinciden con V8 fuera de lo declarado")
sys.exit(1 if (mal_num or mal_dp) else 0)
