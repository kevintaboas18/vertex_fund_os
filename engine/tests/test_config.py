"""Como `load_settings` lee las claves, aislado del entorno de la maquina.

`_key()` mira PRIMERO el archivo y DESPUES el entorno real -- a proposito, para
que Render, donde no existe `API/.env`, siga funcionando. Eso significa que un
test que afirma "sin archivo no hay clave" solo dice la verdad en una maquina
sin claves puestas.

Y esa condicion se rompia sola: `tests_vertex` importa `vertex_api`, que hace
`load_dotenv(vertex.env)` al importarse y puebla `os.environ`. Corriendo las
dos suites juntas, estos tests veian las claves reales de Victor y fallaban;
por separado pasaban. Peor todavia, el aserto imprimia el valor: `assert
'Wx8Tr...' is None`. El `__repr__` de Settings enmascara las claves con
cuidado, y un aserto fallido lo saltaba entero.

La fixture las borra del entorno para que lo que se mida sea la lectura del
ARCHIVO, que es lo que estos tests dicen medir.
"""

from pathlib import Path

import pytest

from wbj.config import load_settings

_CLAVES = ("FMP_API_KEY", "FINNHUB_API_KEY", "FRED_API_KEY", "ANTHROPIC_API_KEY",
           "GEMINI_API_KEY", "OPENAI_API_KEY", "EDGAR_USER_AGENT", "JUDGE_MODEL")


@pytest.fixture(autouse=True)
def _sin_claves_del_entorno(monkeypatch):
    """Un entorno limpio, como el de un clon recien hecho."""
    for nombre in _CLAVES:
        monkeypatch.delenv(nombre, raising=False)


def test_loads_keys_from_env_file(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("FMP_API_KEY=abc123\nFINNHUB_API_KEY=\n")
    s = load_settings(env_file=env)
    assert s.fmp_api_key == "abc123"
    assert s.finnhub_api_key is None  # empty string → None (key absent)


def test_missing_env_file_is_not_fatal(tmp_path: Path):
    s = load_settings(env_file=tmp_path / "nope.env")
    assert s.fmp_api_key is None


def test_settings_never_repr_keys(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("FMP_API_KEY=SECRETVALUE\n")
    s = load_settings(env_file=env)
    assert "SECRETVALUE" not in repr(s)
