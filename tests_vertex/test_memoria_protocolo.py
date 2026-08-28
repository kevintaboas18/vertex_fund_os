"""El protocolo de memoria de CLAUDE.md, fijado como tests.

La memoria entre sesiones no es automática: depende de que cada análisis
escriba su tesis y su línea de índice. Ese escritor estaba roto de dos
formas, y ninguna daba error — sólo iba degradando los archivos:

  - `tesis/<TICKER>.md` se reescribía anteponiendo el archivo ANTERIOR
    COMPLETO, título incluido, bajo un título nuevo. `NVDA.md` acabó con
    el encabezado `# Tesis — NVDA` repetido 32 veces y 32 bloques de los
    que sólo 15 eran análisis distintos.
  - `MEMORIA.md` se abría en modo `"a"`, así que cada corrida añadía una
    línea aunque el resultado fuese idéntico: NVDA aparecía 25 veces con
    el mismo texto, y el índice dejaba de servir para lo único que existe.

Ambos fallos sólo aparecen al repetir corridas, que es exactamente lo que
hace el uso normal. Los tests repiten.
"""

from __future__ import annotations

import re

import pytest

import vertex_api


_TARGETS = {"12m": {"bull": 323.21, "base": 281.05, "bear": 198.74}}


@pytest.fixture
def memoria(tmp_path, monkeypatch):
    """Una carpeta `Memoria/` de usar y tirar, con la estructura real."""
    (tmp_path / "Memoria" / "tesis").mkdir(parents=True)
    (tmp_path / "Memoria" / "MEMORIA.md").write_text(
        "# Memoria del Agente\n\n## Tesis activas\n\n"
        "*(el agente agrega una línea por ticker analizado)*\n",
        encoding="utf-8")
    # Se apunta la CONSTANTE, no `os.path.abspath`.
    #
    # Este aislamiento parcheaba `abspath`, y funcionaba de rebote: la ruta se
    # calculaba con `__file__` DENTRO de la función, en cada llamada. Al pasar
    # a `MEMORIA_LOCAL` —resuelta al importar— el parche dejó de tener efecto
    # y estos casos empezaron a escribir en la `Memoria/` DE VERDAD, pasando
    # en verde mientras corregían la tesis de tickers reales. Parchear el dato
    # en vez del mecanismo que lo calcula es lo que hace que no vuelva a pasar.
    monkeypatch.setattr(vertex_api, "MEMORIA_LOCAL", str(tmp_path / "Memoria"))
    return tmp_path / "Memoria"


def _escribir(ticker="NVDA", raw=47.9, fv=281.05, perfil="Avoid / Wait",
              tesis="Evitar / esperar."):
    vertex_api._wbj_write_thesis_md(ticker, 200.75, perfil, raw, fv, _TARGETS,
                                    tesis, "cierre confirmado bajo la zona")


def test_repeated_runs_never_duplicate_the_title(memoria):
    """El defecto original: un `# Tesis — X` por corrida."""
    for _ in range(6):
        _escribir()
    texto = (memoria / "tesis" / "NVDA.md").read_text(encoding="utf-8")
    assert texto.count("# Tesis — NVDA") == 1, "el título volvió a multiplicarse"


def test_an_unchanged_result_is_stamped_not_stacked(memoria):
    """Veinte bloques idénticos no dicen que la tesis se sostuvo: dicen que
    se apretó el botón veinte veces."""
    for _ in range(5):
        _escribir()
    texto = (memoria / "tesis" / "NVDA.md").read_text(encoding="utf-8")
    assert len(re.findall(r"^## ", texto, re.M)) == 1, "se apilaron duplicados"
    assert "sin cambios; revisado" in texto, "no quedó constancia de la revisión"


def test_a_changed_result_opens_a_new_block_and_keeps_the_old(memoria):
    """El historial es la señal de aprendizaje: corregir encima, nunca
    borrar."""
    _escribir(raw=47.9, fv=281.05, tesis="Evitar / esperar.")
    _escribir(raw=61.4, fv=290.00, perfil="Quality Opportunity",
              tesis="Mejoró tras resultados.")
    texto = (memoria / "tesis" / "NVDA.md").read_text(encoding="utf-8")
    assert len(re.findall(r"^## ", texto, re.M)) == 2
    assert "47.9/100" in texto and "61.4/100" in texto, "se perdió una tesis"
    # La más reciente va primero: es la que se lee de un vistazo.
    assert texto.index("61.4/100") < texto.index("47.9/100")


def test_each_block_records_when_the_conclusion_first_appeared(memoria):
    """Un bloque revisado conserva la fecha en que la conclusión apareció,
    no la de la última corrida: si no, se pierde cuánto tiempo se sostuvo."""
    _escribir()
    primero = re.search(r"desde: ([\d\- :]+)",
                        (memoria / "tesis" / "NVDA.md").read_text(encoding="utf-8")).group(1)
    for _ in range(3):
        _escribir()
    texto = (memoria / "tesis" / "NVDA.md").read_text(encoding="utf-8")
    assert re.search(r"desde: ([\d\- :]+)", texto).group(1) == primero


def test_the_index_keeps_one_line_per_ticker(memoria):
    """`MEMORIA.md` lo pide explícitamente: "una línea por ticker
    analizado". Se abría en modo append."""
    for _ in range(4):
        _escribir("NVDA", raw=47.9)
    _escribir("AAPL", raw=31.6)
    _escribir("NVDA", raw=48.2)          # cambia el score: sigue siendo UNA línea

    texto = (memoria / "MEMORIA.md").read_text(encoding="utf-8")
    tickers = re.findall(r"^- \[([A-Z]+)\]", texto, re.M)
    assert sorted(tickers) == ["AAPL", "NVDA"], f"índice con repetidos: {tickers}"
    assert "48.2/100" in texto and "47.9/100" not in texto, "no se actualizó al último"


def test_the_index_lines_are_sorted_and_link_to_the_thesis(memoria):
    for t in ("PLTR", "AAPL", "KO"):
        _escribir(t)
    lineas = [l for l in (memoria / "MEMORIA.md").read_text(encoding="utf-8").splitlines()
              if l.startswith("- [")]
    assert lineas == sorted(lineas), "el índice dejó de estar ordenado"
    for l in lineas:
        m = re.match(r"- \[([A-Z]+)\]\(tesis/([A-Z]+)\.md\)", l)
        assert m and m.group(1) == m.group(2), f"enlace roto: {l}"


def test_the_index_never_loses_its_own_header(memoria):
    """Reescribir la sección no puede llevarse por delante la explicación
    de para qué sirve el archivo."""
    _escribir()
    texto = (memoria / "MEMORIA.md").read_text(encoding="utf-8")
    assert "# Memoria del Agente" in texto
    assert "## Tesis activas" in texto
    assert "una línea por ticker" in texto


def test_a_broken_memory_folder_never_breaks_the_analysis(tmp_path, monkeypatch):
    """La memoria es best-effort: el análisis ya se hizo. Un fallo al
    escribirla no puede tumbar la respuesta al usuario."""
    monkeypatch.setattr(vertex_api, "MEMORIA_LOCAL",
                        str(tmp_path / "no" / "existe" / "Memoria"))
    _escribir()          # no debe lanzar
