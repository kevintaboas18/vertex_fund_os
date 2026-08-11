#!/usr/bin/env bash
# Test diferencial de las SERIES: chainStore.ts, ivStore.ts y predictionStore.ts.
#
#     engine/scripts/diff_series.sh                       # baja sus .ts de GitHub
#     TITO_ROOT=/ruta/a/agente-tito-metralleta engine/scripts/diff_series.sh
#
# Los otros diferenciales comparan NÚMEROS. Este compara el ARCHIVO, que es lo
# único que importa en los tres stores: son la memoria del agente —el IV Rank
# real, el historial de cadena del sub-agente 4 y el diario que cierra el lazo
# de calibración— y esa memoria solo vale si el archivo es intercambiable con el
# de su app.
#
# El fallo que esto atrapa no se ve de ninguna otra forma: con un formato
# distinto NADA falla hoy. El rank se queda en el proxy de volatilidad
# realizada para siempre, la calibración nunca junta cinco muestras y el reporte
# sale igual de creíble. Por eso se comprueban tres cosas y no una:
#
#   1. los dos lados escriben el MISMO archivo;
#   2. SU TypeScript abre el archivo del port y ve los mismos días;
#   3. el port abre el archivo de SU app y ve los mismos días.
#
# Sus `DATA_DIR` son `path.join(process.cwd(), "data", …)` y se fijan al cargar
# el módulo, así que el lado Node se invoca dos veces con directorios de trabajo
# distintos. No es un rodeo: es la única forma de que el mismo código suyo, sin
# tocar, mire los dos árboles.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
S="$ROOT/engine/scripts"
BASE="https://raw.githubusercontent.com/infusionvictor/agente-tito-metralleta/main/web/lib"
command -v node >/dev/null || { echo "  · saltado: hace falta node"; exit 0; }
node --experimental-strip-types -e '' 2>/dev/null || {
  echo "  · saltado: este node no quita tipos (hace falta 22+)"; exit 0; }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

# `structure.ts` e `ivcontext.ts` producen la entrada de los stores: se corren
# los suyos para que la foto que se guarda sea la SUYA de punta a punta y no la
# del port disfrazada. `occ.ts` es `marketDateStr`, que decide la clave del día.
for f in chainStore ivStore predictionStore structure ivcontext occ; do
  if [[ -n "${TITO_ROOT:-}" && -f "$TITO_ROOT/web/lib/$f.ts" ]]; then
    cp "$TITO_ROOT/web/lib/$f.ts" "$TMP/$f.ts"
  elif ! curl -fsS "$BASE/$f.ts" -o "$TMP/$f.ts"; then
    echo "  · saltado: no se pudo bajar $f.ts"; exit 0
  fi
done
# Node exige la extensión explícita al quitar tipos; es lo único que se toca.
sed -i -E 's|from "\./([A-Za-z]+)"|from "./\1.ts"|g' "$TMP"/*.ts
echo "  fuente: ${TITO_ROOT:-GitHub (main)}"

# Comprobaciones de que la copia es la que se cree: si él cambia un tope o el
# criterio de recorte, el diferencial tiene que enterarse por aquí y no dar
# verde comparando dos cosas viejas.
python3 - "$TMP" <<'PY'
import pathlib, sys
d = pathlib.Path(sys.argv[1])
esperado = {
    "chainStore.ts": ["HISTORY_DAYS = 45", ".slice(0, HISTORY_DAYS)",
                      "Array.isArray(parsed.snapshots)", "b.date.localeCompare(a.date)"],
    "ivStore.ts": ["IV_HISTORY_DAYS = 365", ".slice(0, IV_HISTORY_DAYS)",
                   "s.iv.current == null", "avgIv: s.iv.current"],
    "predictionStore.ts": ["JOURNAL_DAYS = 120", ".slice(0, JOURNAL_DAYS)",
                           "byDate.set(date, snapshot)"],
}
for f, marcas in esperado.items():
    t = (d / f).read_text(encoding="utf-8")
    for m in marcas:
        assert m in t, f"{f}: se perdió «{m}» — su archivo cambió"
print("  contrato: topes y criterios de recorte, los suyos")
PY

cp "$S/_diffser_run.mts" "$TMP/run.mts"
python3 "$S/_diffser_casos.py" > "$TMP/casos.json"

mkdir -p "$TMP/vd" "$TMP/pd/data"
export SER_CASOS="$TMP/casos.json"

# 1) el port escribe en su propio árbol
SER_MODO=escribe SER_OUT="$TMP/py_esc.json" WBJ_TITO_DATA="$TMP/pd/data" \
  python3 "$S/_diffser_run_py.py"

# 2) SU código escribe en el suyo (cwd = $TMP/vd → $TMP/vd/data)
( cd "$TMP/vd" && SER_MODO=escribe SER_OUT="$TMP/v_esc.json" \
    node --experimental-strip-types "$TMP/run.mts" )

# 3) SU código lee el árbol del PORT (cwd = $TMP/pd → $TMP/pd/data)
( cd "$TMP/pd" && SER_MODO=lee SER_OUT="$TMP/v_lee.json" \
    node --experimental-strip-types "$TMP/run.mts" )

# 4) el port lee el árbol SUYO
SER_MODO=lee SER_OUT="$TMP/py_lee.json" WBJ_TITO_DATA="$TMP/vd/data" \
  python3 "$S/_diffser_run_py.py"

export SER_V_OUT="$TMP/v_esc.json" SER_PY_OUT="$TMP/py_esc.json"
export SER_V_LEE="$TMP/v_lee.json" SER_PY_LEE="$TMP/py_lee.json"
python3 "$S/_diffser_compara.py"
