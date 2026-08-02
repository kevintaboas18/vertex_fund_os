#!/usr/bin/env bash
# Diferencial del RELOJ: las 5 funciones del motor que cuentan tiempo, contra
# SUS archivos ejecutados en Node.
#
#     engine/scripts/diff_reloj.sh
#
#   occ.daysToExpiration · gexHeatmap.dteOf · validation.daysBetween
#   news.recencyWeight   · el `Math.floor(Date.parse/1000)` de flow.detectClusters
#
# Por qué existe: las cinco usaban `datetime.fromisoformat` de Python donde él
# usa `Date.parse` de JS, y no son la misma función. Ningún diferencial las
# alcanzaba — `diff_motor.sh` compara los sub-agentes con timestamps bien
# formados, que es justo donde las dos coinciden.
#
# Medido cuando se encontró: 17 de 78 valores divergían. El caso caro es el
# vencimiento CON hora (`"2026-09-18T00:00:00Z"`): su `Date.parse` de
# `"…T00:00:00ZT00:00:00Z"` da NaN y el port devolvía 49 días. Es alcanzable
# desde que `compute` es literal y ya no recorta el vencimiento a 10 caracteres.
#
# Las funciones se EXTRAEN de sus .ts, no se transcriben: una transcripción a
# mano ya metió aquí un `0.15` donde su archivo dice `0.1`.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BASE="https://raw.githubusercontent.com/infusionvictor/agente-tito-metralleta/main/web/lib"
command -v node >/dev/null || { echo "  · saltado: hace falta node"; exit 0; }
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

for f in occ validation gexHeatmap news; do
  if [[ -n "${TITO_ROOT:-}" && -f "$TITO_ROOT/web/lib/$f.ts" ]]; then
    cp "$TITO_ROOT/web/lib/$f.ts" "$TMP/$f.ts"
  elif ! curl -fsS "$BASE/$f.ts" -o "$TMP/$f.ts"; then
    echo "  · saltado: no se pudo bajar su $f.ts"; exit 0
  fi
done
echo "  fuente: ${TITO_ROOT:-GitHub (main)}"

python3 - "$TMP" <<'PY'
import pathlib, re, sys
T = pathlib.Path(sys.argv[1])

def corta(archivo, ini, fin=None):
    t = (T / archivo).read_text()
    a = t.index(ini)
    return t[a:] if fin is None else t[a:t.index(fin, a)]

js = "const DAY = 86_400_000;\n" + "\n".join([
    corta("occ.ts", "export function marketDateStr"),
    corta("validation.ts", "function daysBetween", "\n/**\n * Sigue un flow"),
    corta("gexHeatmap.ts", "function dteOf", "\nexport function gexHeatmap"),
    corta("news.ts", "const HOUR", "\nexport interface NewsBias"),
])
js = re.sub(r"(\w+)\s*:\s*(string|Date|number)\b", r"\1", js)
js = re.sub(r"\)\s*:\s*(number|string)\s*\{", ") {", js)
js = js.replace("export function", "function")
js += """
function clusterSec(ts) {                 // el .map/.filter de detectClusters
  const s = Math.floor(Date.parse(ts) / 1000);
  return Number.isFinite(s) ? s : NaN;
}
export { daysToExpiration, daysBetween, dteOf, recencyWeight, clusterSec };
"""
# Que el transpilado no se haya comido nada de lo que se está midiendo.
for must in ("Math.round((exp - marketToday(now)) / 86_400_000)",
             "Math.floor(ms / 86_400_000)",
             "Math.max(0, Math.round(ms / 86_400_000))",
             "T21:00:00Z", "T00:00:00Z",
             "const HOUR = 3600_000", "return 0.1;"):
    assert must in js, f"el transpilado perdió: {must}"
(T / "reloj.mjs").write_text(js)
PY

export RJ_CASOS="$TMP/casos.json" RJ_PY_OUT="$TMP/py.json" RJ_OUT="$TMP/victor.json"
python3 "$ROOT/engine/scripts/_diffreloj_casos.py"
cp "$ROOT/engine/scripts/_diffreloj_run.mjs" "$TMP/run.mjs"
( cd "$TMP" && node run.mjs )

python3 - <<'PY'
import json, os, sys
casos = json.load(open(os.environ["RJ_CASOS"]))
suyo  = json.load(open(os.environ["RJ_OUT"]))
mio   = json.load(open(os.environ["RJ_PY_OUT"]))
NOM = {"dte":  "occ.days_to_expiration",
       "heat": "gex_heatmap._dte_of",
       "betw": "validation._days_between",
       "news": "news.recency_weight",
       "clus": "flow._epoch (detectClusters)"}
ETIQ = {"dte": casos["fechas"], "heat": casos["fechas"], "betw": casos["pares"],
        "news": casos["stamps"], "clus": casos["stamps"]}
total = malos = 0
for k in NOM:
    a, b, e = suyo[k], mio[k], ETIQ[k]
    assert len(a) == len(b) == len(e), (k, len(a), len(b), len(e))
    d = 0
    for et, x, y in zip(e, a, b):
        total += 1
        if x != y:
            d += 1; malos += 1
            if d <= 5:
                print(f"  ✗ {NOM[k]:<30} {str(et)[:44]:<46} víctor={x!r:<14} port={y!r}")
    if d > 5:
        print(f"     … y {d - 5} más en {NOM[k]}")
print(f"\n  {total - malos}/{total} valores idénticos"
      + (f" · {malos} DIVERGEN" if malos else " · las 5 funciones cuentan el tiempo como él"))
sys.exit(1 if malos else 0)
PY
