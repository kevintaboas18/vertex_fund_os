"""Comparador ESTRICTO del diferencial de `store.ts`.

Sin categorías ni indultos: o los dos lados devuelven lo mismo, o es una
diferencia. Es a propósito — el comparador de `compute.ts` clasifica
divergencias declaradas y eso esconde el número que de verdad importa aquí, que
es cuántos casos NO son idénticos.

Lo único que no se compara es el VALOR de `updatedAt` (hora de pared); sí se
compara su forma y su presencia como clave del archivo.
"""
import json, os, sys

v = json.load(open(os.environ["ST_OUT"]))
p = json.load(open(os.environ["ST_PY_OUT"]))
assert len(v) == len(p), f"{len(v)} casos de Víctor vs {len(p)} del port"

CAMPOS = ("res", "archivos", "claves")
malos = []
for a, b in zip(v, p):
    assert a["caso"] == b["caso"], (a["caso"], b["caso"])
    if all(a.get(k) == b.get(k) for k in CAMPOS):
        continue
    malos.append((a, b))

for a, b in malos:
    print("=" * 74)
    print("✗", a["caso"])
    for k in CAMPOS:
        if a.get(k) != b.get(k):
            print(f"  {k} víctor: {json.dumps(a.get(k), sort_keys=True)[:340]}")
            print(f"  {k} port  : {json.dumps(b.get(k), sort_keys=True)[:340]}")

ok = len(v) - len(malos)
print(f"\n  {ok}/{len(v)} idénticos · {len(malos)} diferencias")
sys.exit(1 if malos else 0)
