"""Comparador del diferencial de `store.ts`.

No pregunta "¿es idéntico?" sino "¿difiere SOLO donde dijimos que difiere?".

Cada caso puede declarar una `divergencia` (una de las tres guardas del port).
La comprobación va en las dos direcciones, que es lo que la hace útil:

- un caso que difiere SIN declararlo  → divergencia nueva sin querer;
- un caso que la declara y NO difiere → la guarda desapareció en silencio.

Cualquiera de las dos falla el script. Lo que no se compara es el VALOR de
`updatedAt` (hora de pared); sí su forma y su presencia como clave del archivo.
"""
import json, os, sys

v = json.load(open(os.environ["ST_OUT"]))
p = json.load(open(os.environ["ST_PY_OUT"]))
casos = json.load(open(os.environ["ST_CASOS"]))
assert len(v) == len(p) == len(casos), f"{len(v)} / {len(p)} / {len(casos)}"

CAMPOS = ("res", "archivos", "claves")
iguales, declaradas, inesperadas, guardas_mudas = 0, {}, [], []

for a, b, c in zip(v, p, casos):
    assert a["caso"] == b["caso"] == c["nombre"], (a["caso"], b["caso"], c["nombre"])
    difiere = any(a.get(k) != b.get(k) for k in CAMPOS)
    esperada = c.get("divergencia")
    if not difiere:
        if esperada:
            guardas_mudas.append((a["caso"], esperada))
        else:
            iguales += 1
        continue
    if esperada:
        declaradas.setdefault(esperada, []).append((a, b))
    else:
        inesperadas.append((a, b))


def muestra(a, b, marca):
    print("=" * 74)
    print(marca, a["caso"])
    for k in CAMPOS:
        if a.get(k) != b.get(k):
            print(f"  {k} víctor: {json.dumps(a.get(k), sort_keys=True)[:300]}")
            print(f"  {k} port  : {json.dumps(b.get(k), sort_keys=True)[:300]}")


for a, b in inesperadas:
    muestra(a, b, "✗ DIVERGENCIA NO DECLARADA ·")

if declaradas:
    print("\n  · DIVERGENCIAS DECLARADAS (las guardas del port, funcionando)")
    for g in sorted(declaradas):
        print(f"      {g:<44} {len(declaradas[g]):>2} caso(s)")
        for a, b in declaradas[g]:
            print(f"        · {a['caso']}")

for caso, g in guardas_mudas:
    print(f"\n  ✗ GUARDA MUDA · '{caso}' declara «{g}» pero NO difiere de su store.ts")

total = len(casos)
print(f"\n  {iguales}/{total} idénticos a su store.ts"
      f" · {total - iguales - len(guardas_mudas)} divergencias deliberadas"
      f"{' · ' + str(len(inesperadas)) + ' SIN DECLARAR' if inesperadas else ''}")
sys.exit(1 if (inesperadas or guardas_mudas) else 0)
