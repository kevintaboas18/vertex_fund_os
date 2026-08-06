#!/usr/bin/env bash
# Diferencial del MOTOR DE CALIBRACIÓN: `reviewPredictions` de predictionStore.ts.
#
#     engine/scripts/diff_calib.sh
#     TITO_ROOT=/ruta/a/agente-tito-metralleta engine/scripts/diff_calib.sh
#
# Es la función que decide cuánto se corrige el target base por el sesgo
# histórico del agente. Si diverge, el motor se auto-corrige hacia el lado
# equivocado y NADA en el reporte lo delata: el número sale igual de creíble.
# Estaba portada, probada contra sí misma y sin comparar contra su archivo.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
S="$ROOT/engine/scripts"
BASE="https://raw.githubusercontent.com/infusionvictor/agente-tito-metralleta/main/web/lib"
command -v node >/dev/null || { echo "  · saltado: hace falta node"; exit 0; }
node --experimental-strip-types -e '' 2>/dev/null || {
  echo "  · saltado: este node no quita tipos (hace falta 22+)"; exit 0; }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
for f in predictionStore occ; do
  if [[ -n "${TITO_ROOT:-}" && -f "$TITO_ROOT/web/lib/$f.ts" ]]; then
    cp "$TITO_ROOT/web/lib/$f.ts" "$TMP/$f.ts"
  elif ! curl -fsS "$BASE/$f.ts" -o "$TMP/$f.ts"; then
    echo "  · saltado: no se pudo bajar $f.ts"; exit 0
  fi
done
sed -i -E 's|from "\./([A-Za-z]+)"|from "./\1.ts"|g' "$TMP"/*.ts
echo "  fuente: ${TITO_ROOT:-GitHub (main)}"

cp "$S/_diffcalib_run.mts" "$TMP/run.mts"
python3 "$S/_diffcalib_casos.py" > "$TMP/casos.json"
export CALIB_CASOS="$TMP/casos.json" CALIB_OUT="$TMP/victor_out.json"
( cd "$TMP" && node --experimental-strip-types run.mts )
python3 "$S/_diffcalib_compara.py"
