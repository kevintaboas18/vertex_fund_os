@echo off
REM Arranca Vertex Fund OS en local (Windows). Doble clic o: start.bat
cd /d "%~dp0"
echo ==> Vertex Fund OS - arranque local

where python >nul 2>nul
if errorlevel 1 (
  echo ERROR: falta Python 3. Instalalo desde https://www.python.org/downloads/
  pause
  exit /b 1
)

if not exist ".venv" (
  echo ==> Creando entorno virtual (.venv)
  python -m venv .venv
)
call .venv\Scripts\activate.bat

echo ==> Instalando dependencias
python -m pip install -q --upgrade pip
python -m pip install -q -r requirements.txt

if not exist "vertex.env" (
  echo.
  echo [!] No existe vertex.env con tus claves.
  echo     Copia API\.env.example a vertex.env y pon tus keys.
  echo.
)

if "%PORT%"=="" set PORT=8000
echo ==> Servidor en http://localhost:%PORT%   (Ctrl+C para detener)
uvicorn vertex_api:app --host 0.0.0.0 --port %PORT% --reload
pause
