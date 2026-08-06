#!/usr/bin/env bash
# Diferencial del MOTOR (3/3): los dos módulos que nadie más toca.
#
#     engine/scripts/diff_motor3.sh
#     TITO_ROOT=/ruta/a/agente-tito-metralleta engine/scripts/diff_motor3.sh
#
#     gexHeatmap.ts  malla strike × vencimiento con el GEX por celda
#     news.ts        RSS, alias, sesgo de noticias y bandera de contradicción
#
# `diff_motor.sh` cubre flow/validation/levels/structure y `diff_motor2.sh`
# ivcontext/gex/prediction/risk. Los otros cuatro módulos de su `web/lib`
# —occ, conditions, expectedMove, blackScholes— los llama el motor en CADA caso
# de esos dos, corpus malformado incluido, así que ya quedan medidos ahí. Estos
# dos cuelgan de rutas propias y eran el único hueco sin diferencial.
#
# Mismo montaje: quitado de tipos NATIVO de Node, así que solo desaparecen los
# tipos y ni una línea de lógica cambia.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
S="$ROOT/engine/scripts"
BASE="https://raw.githubusercontent.com/infusionvictor/agente-tito-metralleta/main/web/lib"
command -v node >/dev/null || { echo "  · saltado: hace falta node"; exit 0; }
node --experimental-strip-types -e '' 2>/dev/null || {
  echo "  · saltado: este node no quita tipos (hace falta 22+)"; exit 0; }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
for f in gexHeatmap news types occ blackScholes gex conditions flow expectedMove; do
  if [[ -n "${TITO_ROOT:-}" && -f "$TITO_ROOT/web/lib/$f.ts" ]]; then
    cp "$TITO_ROOT/web/lib/$f.ts" "$TMP/$f.ts"
  elif ! curl -fsS "$BASE/$f.ts" -o "$TMP/$f.ts"; then
    echo "  · saltado: no se pudo bajar $f.ts"; exit 0
  fi
done
# Node exige la extensión en los imports relativos; su bundler no. Es lo único
# que se modifica de sus archivos.
sed -i -E 's|from "\./([A-Za-z]+)"|from "./\1.ts"|g' "$TMP"/*.ts
echo "  fuente: ${TITO_ROOT:-GitHub (main)}"

cp "$S/_diffmotor3_run.mts" "$TMP/run.mts"
python3 "$S/_diffmotor3_casos.py" > "$TMP/casos.json"
export MOTOR3_CASOS="$TMP/casos.json" MOTOR3_OUT="$TMP/victor_out.json"
( cd "$TMP" && node --experimental-strip-types run.mts )
python3 "$S/_diffmotor3_compare.py"
