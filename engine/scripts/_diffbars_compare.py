"""Comparador del diferencial de `barsStore.ts`.

No pregunta "¿es idéntico?" sino "¿difiere SOLO donde dijimos que difiere?".

Cada caso puede declarar una `divergencia` (la guarda de validación de
`load_bars`). La comprobación va en las dos direcciones:

- un caso que difiere SIN declararlo  → divergencia nueva sin querer;
- un caso que la declara y NO difiere → la guarda desapareció en silencio.

Cualquiera de las dos falla el script.
"""
import json, os, sys

v = json.load(open(os.environ["BS_OUT"]))
p = json.load(open(os.environ["BS_PY_OUT"]))
casos = json.load(open(os.environ["BS_CASOS"]))
assert len(v) == len(p) == len(casos), f"{len(v)} / {len(p)} / {len(casos)}"

iguales, declaradas, sin_declarar, mudas = 0, {}, [], []
for a, b, c in zip(v, p, casos):
    difiere = a["res"] != b["res"] or a.get("archivos") != b.get("archivos")
    esperada = c.get("divergencia")
    if not difiere:
        if esperada:
            mudas.append((a["caso"], esperada))
        else:
            iguales += 1
        continue
    if esperada:
        declaradas.setdefault(esperada, []).append(a["caso"])
    else:
        sin_declarar.append((a, b))

for a, b in sin_declarar:
    print(f"  ✗ DIVERGENCIA NO DECLARADA · {a['caso']}")
    print(f"     víctor = {a['res']}")
    print(f"     port   = {b['res']}")

if declaradas:
    print("\n  · DIVERGENCIAS DECLARADAS (las guardas del port, funcionando)")
    for g, cs in declaradas.items():
        print(f"      {g}   ·   {len(cs)} caso(s)")
        for x in cs:
            print(f"        · {x}")

for caso, g in mudas:
    print(f"\n  ✗ GUARDA MUDA · '{caso}' declara «{g}» pero NO difiere de su archivo")

print(f"\n  {iguales}/{len(casos)} idénticos a su barsStore.ts"
      f" · {len(casos) - iguales - len(mudas)} divergencias deliberadas"
      + (f" · {len(sin_declarar)} SIN DECLARAR" if sin_declarar else ""))
sys.exit(1 if (sin_declarar or mudas) else 0)
