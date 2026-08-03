"""Reporte del diferencial de geometría. La comparación la hace el runner en
Node —los dos lados son JavaScript—; aquí solo se agrupa y se declara."""
import collections
import json
import os
import pathlib
import sys

R = json.load(open(os.environ["GEO_OUT"]))
fallos, n, faltan = R["fallos"], R["n"], R["faltan"]

#: Por qué está declarada cada omisión. Sin razón escrita, el check falla.
_RAZONES = {
    "buildScales.xOfIndex":
        "su `PriceChart` lo usa para el crosshair, que el panel de Vertex no "
        "tiene. La x de cada vela ya viaja en `candles[i].cx`.",
    "buildScales.priceOfY":
        "idem: es la inversa que solo necesita el crosshair.",
    "buildScales.visibleBars":
        "las barras recortadas ya viajan dentro de `candles`; el panel no "
        "necesita la lista aparte.",
}

print(f"  {n} casos · smartDomain + buildScales + packLabels")

if fallos:
    por_bloque = collections.Counter(f["caso"].split("#")[0] for f in fallos)
    print("\n  ✗ LA GRÁFICA NO CALCULA LO MISMO QUE SU ARCHIVO")
    for k, c in por_bloque.most_common():
        print(f"      {k:<12} {c:>4} caso(s)")
    for f in fallos[:6]:
        print(f"\n      {f['caso']}\n        víctor={f['victor'][:150]}"
              f"\n        panel ={f['panel'][:150]}")

sin_razon = [k for k in faltan if k not in _RAZONES]
if faltan:
    print("\n  · CAMPOS DE SU GEOMETRÍA QUE EL PANEL NO DEVUELVE")
    for k in faltan:
        print(f"      {k:<32} {_RAZONES.get(k, '✗ SIN RAZÓN ESCRITA')[:90]}")

if sin_razon:
    print(f"\n  ✗ {len(sin_razon)} omisión(es) sin declarar: {sin_razon}")

print(f"\n  {n - len(fallos)}/{n} casos idénticos"
      + (f" · {len(fallos)} DIVERGENCIAS" if fallos else " · sin diferencias"))
sys.exit(1 if (fallos or sin_razon) else 0)
