#!/usr/bin/env bash
# Diferencial de SU `marketsnack.ts`: el cliente del flujo (Time & Sales).
#
#     engine/scripts/diff_flujo.sh
#     TITO_ROOT=/ruta/a/agente-tito-metralleta engine/scripts/diff_flujo.sh
#
# El otro módulo suyo que hablaba con la red y por eso no tenía diferencial. Lo
# que decide no es la red: es la URL que pide y CUÁNDO PARA —lista vacía,
# ventana cubierta, tope de páginas, sin token—, y de ahí sale cuánto flujo ven
# los sub-agentes 1, 2 y 3. Parar una página antes no rompe nada visible: solo
# calcula la Agresividad y la Inusualidad sobre menos operaciones.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
S="$ROOT/engine/scripts"
BASE="https://raw.githubusercontent.com/infusionvictor/agente-tito-metralleta/main/web/lib"
command -v node >/dev/null || { echo "  · saltado: hace falta node"; exit 0; }
node --experimental-strip-types -e '' 2>/dev/null || {
  echo "  · saltado: este node no quita tipos (hace falta 22+)"; exit 0; }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
for f in marketsnack types flow conditions occ; do
  if [[ -n "${TITO_ROOT:-}" && -f "$TITO_ROOT/web/lib/$f.ts" ]]; then
    cp "$TITO_ROOT/web/lib/$f.ts" "$TMP/$f.ts"
  elif ! curl -fsS "$BASE/$f.ts" -o "$TMP/$f.ts"; then
    echo "  · saltado: no se pudo bajar $f.ts"; exit 0
  fi
done
sed -i -E 's|from "\./([A-Za-z]+)"|from "./\1.ts"|g' "$TMP"/*.ts
echo "  fuente: ${TITO_ROOT:-GitHub (main)}"

cp "$S/_diffflujo_run.mts" "$TMP/run.mts"
python3 "$S/_diffflujo_casos.py" > "$TMP/casos.json"
export FLUJO_CASOS="$TMP/casos.json" FLUJO_OUT="$TMP/victor_out.json"
export MARKETSNACK_COOKIE="sesion=abc"
( cd "$TMP" && node --experimental-strip-types run.mts )
python3 "$S/_diffflujo_compara.py"
