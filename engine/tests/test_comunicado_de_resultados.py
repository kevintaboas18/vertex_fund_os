"""El comunicado de resultados es una fuente que el motor no iba a buscar.

`BUS-GUIDE-027` compara el punto medio del guidance contra lo reportado, y
`DATASET.md` nombra su fuente sin ambigüedad: "earnings releases". El motor
sólo leía el 10-K, así que la métrica quedaba `MISSING` para todo ticker salvo
el que un analista tecleara a mano — que era exactamente uno.

Lo que estos tests fijan es cómo se encuentra el documento, porque ahí está
todo el riesgo:

  - hay que filtrar por **item 2.02**, que es el que la SEC exige para
    publicar resultados. Un 8-K cualquiera no lleva comunicado;
  - el nombre del archivo NO está normalizado. KO presenta
    `a2026q2earningsreleaseex-9.htm`, WMT `earningsreleasefy27q1.htm` y PLTR
    `a2026q2ex991pressrelease.htm`. Buscar sólo `ex-99`, que es lo que dice la
    intuición, se deja fuera a dos de esos tres;
  - y hay emisores que sencillamente no lo presentan.
"""

from __future__ import annotations

import pytest

from wbj.providers.edgar import EdgarProvider


class _Cache:
    def get(self, ticker, key):
        return None

    def put(self, ticker, key, payload):
        pass


class _Edgar(EdgarProvider):
    """Sin red: las respuestas de EDGAR vienen preparadas."""

    def __init__(self, submissions, indices, textos):
        super().__init__(None, _Cache())
        self._sub, self._idx, self._txt = submissions, indices, textos

    def get_json(self, url, params, kind, cache_key, **kw):
        return self._idx.get(url) if "index.json" in url else self._sub

    def get_text(self, url, params, kind, cache_key, **kw):
        return self._txt.get(url)


_BASE = "https://www.sec.gov/Archives/edgar/data/21344"


def _monta(nombres, items="2.02", cuerpo="palabra " * 400):
    acc = "0001628280-26-049922"
    bare = acc.replace("-", "")
    sub = {"filings": {"recent": {
        "form": ["8-K"], "items": [items], "accessionNumber": [acc],
        "filingDate": ["2026-07-28"]}}}
    idx = {f"{_BASE}/{bare}/index.json":
           {"directory": {"item": [{"name": n} for n in nombres]}}}
    txt = {f"{_BASE}/{bare}/{n}": f"<html><body>{cuerpo}</body></html>"
           for n in nombres}
    return _Edgar(sub, idx, txt)


@pytest.mark.parametrize("nombre", [
    "a2026q2earningsreleaseex-9.htm",   # Coca-Cola
    "earningsreleasefy27q1.htm",        # Walmart
    "a2026q2ex991pressrelease.htm",     # Palantir
    "pld-ex99_1.htm",                   # Prologis
])
def test_every_naming_convention_is_found(nombre):
    """Los cuatro son el mismo documento con cuatro nombres. Buscar sólo
    `ex-99` encontraba uno de cada dos."""
    fuera = _monta([nombre]).latest_earnings_release(21344)
    assert fuera is not None, f"no encontro el comunicado llamado {nombre!r}"
    assert fuera["url"].endswith(nombre)


def test_an_eight_k_without_results_is_skipped():
    """Un 8-K de cambio de consejero no lleva comunicado de resultados. El
    item 2.02 es lo que separa uno de otro."""
    assert _monta(["a2026q2ex991pressrelease.htm"],
                  items="5.02").latest_earnings_release(21344) is None


def test_a_cover_page_is_not_a_press_release():
    """NVIDIA publica el suyo en su propia sala de prensa: su 8-K de
    resultados son 4.100 caracteres de caratula. Devolver eso como si fuera el
    comunicado mandaria al extractor a leer un documento que no dice nada."""
    assert _monta(["a2026q2ex991pressrelease.htm"],
                  cuerpo="corto").latest_earnings_release(21344) is None


def test_an_issuer_that_files_none_gets_none():
    """La respuesta correcta para NVDA y TSLA, verificado el 2026-08-06."""
    assert _monta(["nvda-20260520.htm", "nvda-20260520.xsd"]
                  ).latest_earnings_release(21344) is None


def test_the_text_arrives_stripped_of_markup():
    """Quien lo consume verifica con cita textual, y una cita no puede llevar
    etiquetas dentro."""
    fuera = _monta(["earningsreleasefy27q1.htm"]).latest_earnings_release(21344)
    assert "<" not in fuera["text"] and ">" not in fuera["text"]
    assert fuera["accession"] and fuera["filing_date"]
