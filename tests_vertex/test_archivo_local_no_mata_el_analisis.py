"""Que el navegador no pueda guardar un reporte no puede tumbar el análisis.

Victor vio esto en producción analizando JPM:

    No se pudo analizar JPM.
    Motivo: Failed to execute 'setItem' on 'Storage': Setting the value of
    'vertex_reports_v2' exceeded the quota.

El análisis había terminado BIEN. Los seis especialistas corrieron, el
servidor guardó el reporte de forma duradera, y aun así la pantalla dijo que
no se pudo analizar — porque `saveNewReport` recortaba el archivo local por
CANTIDAD (200 reportes) mientras `localStorage` limita por BYTES (~5 MB). Un
reporte completo lleva los seis especialistas con sus métricas, así que el
límite de bytes llega mucho antes que el de 200, y la excepción subía hasta el
manejador del análisis.

El archivo local es una caché: `syncReportsFromServer` baja de vuelta lo que
el servidor guarda. Perder el reporte más viejo de ahí no pierde nada. Abortar
el análisis sí.

Se prueba con node porque es el mismo motor que corre el navegador: un
`localStorage` de mentira con límite de bytes y las funciones reales sacadas
del HTML.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_HTML = Path(__file__).parent.parent / "vertex_fund_os_platform.html"

_ARNES = """
const LIMITE = 50000;
let almacen = {};
global.localStorage = {
  getItem: k => (k in almacen ? almacen[k] : null),
  setItem: (k, v) => {
    const otros = Object.entries(almacen)
      .reduce((n, [kk, vv]) => n + (kk === k ? 0 : vv.length), 0);
    if (otros + v.length > LIMITE) {
      const e = new Error(`Setting the value of '${k}' exceeded the quota.`);
      e.name = 'QuotaExceededError';
      throw e;
    }
    almacen[k] = v;
  },
  removeItem: k => { delete almacen[k]; },
};
const STORAGE_KEY = "vertex_reports_v2";
"""

_PRUEBA = """
const gordo = "z".repeat(9000);
let excepciones = 0;
for (let i = 0; i < 40; i++) {
  try { saveNewReport({ report_id: `r${i}`, relleno: gordo }); }
  catch (e) { excepciones++; }
}
const quedan = getAllReports();
console.log(JSON.stringify({
  excepciones,
  conservados: quedan.length,
  mas_nuevo: quedan[0] && quedan[0].report_id,
}));
"""


def _funciones_del_html() -> str:
    """Las funciones reales de archivo local, tal como las corre el navegador."""
    src = _HTML.read_text(encoding="utf-8", errors="replace")
    ini = src.index("function getAllReports()")
    fin = src.index("function deleteReport(")
    return src[ini:fin]


@pytest.mark.skipif(shutil.which("node") is None, reason="node no esta instalado")
def test_a_full_storage_never_kills_the_analysis(tmp_path):
    guion = tmp_path / "prueba.js"
    guion.write_text(_ARNES + _funciones_del_html() + _PRUEBA, encoding="utf-8")
    r = subprocess.run(["node", str(guion)], capture_output=True, text=True, timeout=90)
    assert r.returncode == 0, r.stderr[:500]
    fuera = json.loads(r.stdout.strip().splitlines()[-1])

    # Lo que importa: ni una sola excepción sube. Cada una de ellas era un
    # análisis completo tirado a la basura por no poder cachearlo.
    assert fuera["excepciones"] == 0, (
        f"{fuera['excepciones']} escrituras lanzaron: volverian a abortar el analisis")

    # Y el archivo sigue siendo útil: guarda lo que cabe, empezando por lo más
    # reciente. Vaciarlo entero también habría evitado la excepción, y habría
    # sido inútil.
    assert fuera["conservados"] > 0, "se quedo sin archivo local"
    assert fuera["mas_nuevo"] == "r39", (
        "se conservaron los viejos y se tiro el recien analizado")


def test_no_storage_write_bypasses_the_guard():
    """Cualquier `setItem` suelto sobre esta clave vuelve a abrir el agujero.

    Había tres sitios escribiendo el archivo —guardar, sincronizar del
    servidor y borrar— y sólo hacía falta que uno reventara.
    """
    src = _HTML.read_text(encoding="utf-8", errors="replace")
    sueltos = re.findall(r"localStorage\.setItem\(\s*STORAGE_KEY", src)
    assert len(sueltos) == 1, (
        f"hay {len(sueltos)} escrituras directas del archivo local; todas deben "
        "pasar por `guardarArchivoLocal`, que recorta y reintenta")
    assert "function guardarArchivoLocal" in src
