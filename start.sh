#!/usr/bin/env bash
# Arranca Vertex Fund OS en local. Uso: ./start.sh
set -e
cd "$(dirname "$0")"

echo "==> Vertex Fund OS — arranque local"

# 1) Python 3.11+
if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: falta Python 3. Instálalo desde https://www.python.org/downloads/"; exit 1
fi

# 2) Entorno virtual
if [ ! -d ".venv" ]; then
  echo "==> Creando entorno virtual (.venv)"
  python3 -m venv .venv
fi
source .venv/bin/activate

# 3) Dependencias (solo si faltan)
echo "==> Instalando dependencias"
pip install -q --upgrade pip
pip install -q -r requirements.txt

# 4) Claves
if [ ! -f "vertex.env" ]; then
  echo ""
  echo "⚠️  No existe vertex.env con tus claves."
  echo "    Crea uno a partir del ejemplo y edítalo:"
  echo "        cp API/.env.example vertex.env   # y pon tus keys"
  echo "    (EDGAR funciona sin key; GEMINI_API_KEY y FMP_API_KEY son las importantes)"
  echo ""
fi

# 5) Servidor
PORT="${PORT:-8000}"
echo "==> Servidor en http://localhost:${PORT}   (Ctrl+C para detener)"
exec uvicorn vertex_api:app --host 0.0.0.0 --port "${PORT}" --reload
