#!/usr/bin/env bash
# Diferencial de GEOMETRÍA: la gráfica del panel contra su `chartGeometry.ts`.
#
#     engine/scripts/diff_geo.sh
#     TITO_ROOT=/ruta/a/agente-tito-metralleta engine/scripts/diff_geo.sh
#
# Es la única pieza del port escrita a mano en JavaScript —vive dentro de
# `vertex_fund_os_platform.html` porque la ejecuta el navegador, no el motor— y
# era la única que no se comparaba contra nada. Transcribir a mano es justo
# donde ya se coló un error antes (`news.ts`), así que aquí se EXTRAE el bloque
# del HTML y se corre al lado de su archivo, en el mismo Node.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
S="$ROOT/engine/scripts"
BASE="https://raw.githubusercontent.com/infusionvictor/agente-tito-metralleta/main/web/lib"
command -v node >/dev/null || { echo "  · saltado: hace falta node"; exit 0; }
node --experimental-strip-types -e '' 2>/dev/null || {
  echo "  · saltado: este node no quita tipos (hace falta 22+)"; exit 0; }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
for f in chartGeometry types; do
  if [[ -n "${TITO_ROOT:-}" && -f "$TITO_ROOT/web/lib/$f.ts" ]]; then
    cp "$TITO_ROOT/web/lib/$f.ts" "$TMP/$f.ts"
  elif ! curl -fsS "$BASE/$f.ts" -o "$TMP/$f.ts"; then
    echo "  · saltado: no se pudo bajar $f.ts"; exit 0
  fi
done
sed -i -E 's|from "\./([A-Za-z]+)"|from "./\1.ts"|g' "$TMP"/*.ts
echo "  fuente: ${TITO_ROOT:-GitHub (main)}"

python3 "$S/_diffgeo_extrae.py" "$TMP/panel.mjs"
cp "$S/_diffgeo_run.mts" "$TMP/run.mts"
python3 "$S/_diffgeo_casos.py" > "$TMP/casos.json"
export GEO_CASOS="$TMP/casos.json" GEO_OUT="$TMP/out.json"
( cd "$TMP" && node --experimental-strip-types run.mts )
python3 "$S/_diffgeo_compara.py"
