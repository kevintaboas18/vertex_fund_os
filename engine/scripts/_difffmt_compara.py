"""Comparador del diferencial de `format.ts`.

Estricto salvo una divergencia, declarada por bloque y por entrada:

- **`money`/`money0` con `null`** — su `money.format(undefined)` pinta "$NaN";
  el panel pinta "—". Un "$NaN" en la columna de premium se lee como un dato
  roto del mercado y no como un dato que no llegó, y este panel tiene la regla
  de que sin evidencia no hay número.

Como en los demás comparadores, la comprobación va en las DOS direcciones: una
divergencia sin declarar falla, y una declarada que YA NO ocurre también —
significaría que la guarda desapareció en silencio.
"""
import json
import os
import sys
from collections import Counter

filas = json.load(open(os.environ["FMT_OUT"]))

#: (bloque, entrada) → por qué difieren a propósito.
#:
#: Solo el AUSENTE y el NaN. `null` y `NaN` son las dos formas en que llega "no
#: hay dato": `Number(null)` es 0, así que su `money` pinta un rotundo "$0.00"
#: para un premium que nunca llegó, y su `NaN` pinta "$NaN". El panel pinta "—"
#: en los dos casos.
#:
#: Los infinitos NO están: su `Intl` pinta "$∞" y el panel también, porque
#: `isNaN(Infinity)` es `false` y la guarda no lo atrapa. Declararlos habría
#: sido decir que hay una diferencia donde no la hay — y el comparador lo
#: denuncia como «guarda muda», que es justo para lo que sirve.
DECLARADAS = {
    ("money", "null"): "el ausente se pinta «—», no «$0.00»",
    ("money0", "null"): "el ausente se pinta «—», no «$0»",
    ("money", "NaN"): "el NaN se pinta «—», no «$NaN»",
    ("money0", "NaN"): "el NaN se pinta «—», no «$NaN»",
}

iguales, inesperadas, declaradas, mudas = 0, [], Counter(), []
por_bloque = Counter()

for f in filas:
    clave = (f["bloque"], f["entrada"])
    por_bloque[f["bloque"]] += 1
    difiere = f["suyo"] != f["mio"]
    esperada = DECLARADAS.get(clave)
    if not difiere:
        if esperada:
            mudas.append((clave, esperada))
        else:
            iguales += 1
    elif esperada:
        declaradas[esperada] += 1
    else:
        inesperadas.append(f)

for f in inesperadas[:40]:
    print(f"  ✗ {f['bloque']:<8} entrada={f['entrada']:<22}"
          f" víctor={f['suyo']!r:<18} panel={f['mio']!r}")
if len(inesperadas) > 40:
    print(f"  … y {len(inesperadas) - 40} más")

if declaradas:
    print("\n  · DIVERGENCIAS DECLARADAS")
    for g, n in declaradas.items():
        print(f"      {g:<44} {n:>3} caso(s)")

for clave, g in mudas:
    print(f"\n  ✗ GUARDA MUDA · {clave} declara «{g}» pero NO difiere")

total = len(filas)
print(f"\n  {iguales}/{total} idénticos a su format.ts"
      f" · {sum(declaradas.values())} deliberadas"
      + (f" · {len(inesperadas)} SIN DECLARAR" if inesperadas else ""))
print("      " + " · ".join(f"{b} {n}" for b, n in sorted(por_bloque.items())))

# Los diez formateadores tienen que haberse ejercitado. Un runner que se
# saltara un bloque saldría verde sin haber comparado nada de él.
FALTAN = {"money", "money0", "int", "px", "pct",
          "timeET", "dateET", "timeOf", "dateOf", "hmET"} - set(por_bloque)
if FALTAN:
    print(f"  ✗ bloques sin ejecutar: {sorted(FALTAN)}")

sys.exit(1 if (inesperadas or mudas or FALTAN) else 0)
