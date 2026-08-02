#!/usr/bin/env bash
# Diferencial del MOTOR (2/2): los cuatro módulos que `diff_motor.sh` no cubre.
#
#     engine/scripts/diff_motor2.sh
#     TITO_ROOT=/ruta/a/agente-tito-metralleta engine/scripts/diff_motor2.sh
#
#     ivcontext.ts   sub-agente 5 — IV Rank, régimen, front skew      10 pts
#     gex.ts         nodo imán, zona de flip, régimen gamma
#     prediction.ts  Prediction Pro — 3 escenarios + calibración
#     risk.ts        techo de contratos = min(prima, quema de theta)
#
# `diff_motor.sh` cubre los otros cuatro (flow, structure, validation, levels).
# Estos estaban probados SOLO con datos bien formados, que es exactamente el
# hueco que allí resultó caro: el corpus malformado destapó 104 casos donde el
# port lanzaba y su archivo no.
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
for f in ivcontext gex prediction risk types flow conditions occ \
         blackScholes expectedMove; do
  if [[ -n "${TITO_ROOT:-}" && -f "$TITO_ROOT/web/lib/$f.ts" ]]; then
    cp "$TITO_ROOT/web/lib/$f.ts" "$TMP/$f.ts"
  elif ! curl -fsS "$BASE/$f.ts" -o "$TMP/$f.ts"; then
    echo "  · saltado: no se pudo bajar $f.ts"; exit 0
  fi
done
# Node exige la extensión en los imports relativos; su bundler no. Se añade sin
# tocar nada más — es lo único que se modifica de sus archivos.
# Node exige la extensión en los imports relativos; su bundler no.
sed -i -E 's|from "\./([A-Za-z]+)"|from "./\1.ts"|g' "$TMP"/*.ts
echo "  fuente: ${TITO_ROOT:-GitHub (main)}"

cp "$S/_diffmotor2_run.mts" "$TMP/run.mts"
python3 "$S/_diffmotor2_casos.py" > "$TMP/casos.json"
export MOTOR2_CASOS="$TMP/casos.json" MOTOR2_OUT="$TMP/victor_out.json"
( cd "$TMP" && node --experimental-strip-types run.mts )
python3 "$S/_diffmotor2_compare.py"
