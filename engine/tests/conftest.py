"""Configuración de la suite del motor.

Lo único que hay aquí es la regla de los saltos: un test que se salta a sí mismo
es un fallo, salvo que falte una herramienta del entorno. El porqué, con los dos
casos que costó, está en `_saltos.py`.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

_RUTA = pathlib.Path(__file__).with_name("_saltos.py")
_spec = importlib.util.spec_from_file_location("wbj_tests_saltos", _RUTA)
_saltos = importlib.util.module_from_spec(_spec)
sys.modules["wbj_tests_saltos"] = _saltos
_spec.loader.exec_module(_saltos)

_saltos.instala(sys.modules[__name__])
