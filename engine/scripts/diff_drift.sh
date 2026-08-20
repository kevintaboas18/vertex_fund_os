#!/usr/bin/env bash
# Ejecuta SU `drift_sentiment/` y compara contra el port de Vertex.
#
#   DRIFT_ROOT=/ruta/a/drift-sentiment-agent engine/scripts/diff_drift.sh
#
# Sin DRIFT_ROOT se clona el repo público en un temporal.
set -euo pipefail
AQUI="$(cd "$(dirname "$0")" && pwd)"
if [ -z "${DRIFT_ROOT:-}" ]; then
  TMP="$(mktemp -d)"
  trap 'rm -rf "$TMP"' EXIT
  git clone --depth 1 -q https://github.com/infusionvictor/drift-sentiment-agent "$TMP/d"
  export DRIFT_ROOT="$TMP/d"
fi
exec python "$AQUI/_diffdrift_compara.py"
