"""El TAM es del MERCADO, no de la empresa que lo declaró primero.

El TAM de Omdia para chips de datacenter ($207.000M en 2025) cubre a NVIDIA,
a AMD con sus Instinct, a Broadcom y a Marvell — el propio comunicado los
nombra en el mismo denominador. Guardado sólo en `Entradas/NVDA.json`, el
resto salía con `tam=ninguno`:

    NVDA  market 4.87/20   tam $207B
    AMD   market 1.82/20   tam ninguno   <- vende al MISMO mercado

No faltaba el dato. Estaba escrito en el archivo de otra empresa.

`Entradas/_industrias/<slug>.json` lo comparte, y el archivo del ticker
sigue ganando en cualquier clave que repita: la industria son cimientos, no
una imposición.

El riesgo que esto introduce —y que estos tests fijan— es el contrario: una
industria de GICS es más ancha que un mercado. `Semiconductors` mete en la
misma bolsa a NVIDIA, que vende aceleradores, y a Micron, que vende memoria.
Sin la lista `_aplica_a`, MU heredaba un denominador que no es el suyo. Un
número equivocado es peor que un hueco, porque el hueco se ve.
"""

from __future__ import annotations

import json

import pytest

from wbj.overlay.from_packet import _overlay_industria, _slug_industria


class _Settings:
    def __init__(self, root):
        self.inputs_dir = str(root)


@pytest.fixture
def entradas(tmp_path):
    (tmp_path / "_industrias").mkdir()
    return tmp_path


def _escribir(root, slug, data):
    (root / "_industrias" / f"{slug}.json").write_text(
        json.dumps(data), encoding="utf-8")


def test_the_industry_tam_reaches_every_listed_ticker(entradas):
    """El caso que motivó todo: AMD compite en el mismo mercado que NVDA."""
    _escribir(entradas, "semiconductors", {
        "_aplica_a": ["NVDA", "AMD", "AVGO"],
        "tam": 207_000_000_000, "tam_source": "Omdia", "tam_source_tier": 3})
    s = _Settings(entradas)
    for tk in ("NVDA", "AMD", "AVGO"):
        assert _overlay_industria(s, "Semiconductors", tk)["tam"] == 207_000_000_000, tk


def test_a_ticker_outside_the_list_inherits_nothing(entradas):
    """Micron es 'Semiconductors' y NO vende aceleradores. Heredar el TAM de
    Omdia le daría una participación diminuta pero *puntuable*, que es peor
    que no tener el dato."""
    _escribir(entradas, "semiconductors", {
        "_aplica_a": ["NVDA", "AMD"],
        "tam": 207_000_000_000, "tam_source": "Omdia", "tam_source_tier": 3})
    assert _overlay_industria(_Settings(entradas), "Semiconductors", "MU") == {}


def test_without_a_list_the_file_covers_the_whole_industry(entradas):
    """`_aplica_a` es opcional: cuando el mercado SÍ coincide con la
    clasificación, exigir la lista sería burocracia."""
    _escribir(entradas, "banks-diversified", {
        "tam": 1_000, "tam_source": "FDIC", "tam_source_tier": 1})
    assert _overlay_industria(_Settings(entradas), "Banks - Diversified", "JPM")["tam"] == 1_000


def test_an_industry_tam_still_needs_its_attribution(entradas):
    """Compartir un dato no lo exime de estar atribuido. `DECISION_RULES.md`:
    una afirmación de tamaño de mercado sin fuente es tier 5 y puntúa 0."""
    _escribir(entradas, "semiconductors", {"tam": 207_000_000_000})
    fuera = _overlay_industria(_Settings(entradas), "Semiconductors", "NVDA")
    assert "tam" not in fuera, f"pasó un TAM sin fuente: {fuera}"

    _escribir(entradas, "semiconductors", {
        "tam": 1, "tam_source": "alguien", "tam_source_tier": 5})
    assert "tam" not in _overlay_industria(_Settings(entradas), "Semiconductors", "NVDA")


def test_a_missing_industry_file_is_simply_no_inheritance(entradas):
    s = _Settings(entradas)
    assert _overlay_industria(s, "Beverages - Non-Alcoholic", "KO") == {}
    assert _overlay_industria(s, None, "KO") == {}
    assert _overlay_industria(s, "", "KO") == {}


def test_the_slug_matches_the_file_name():
    """La industria llega del packet tal como la escribe FMP."""
    assert _slug_industria("Semiconductors") == "semiconductors"
    assert _slug_industria("Banks - Diversified") == "banks-diversified"
    assert _slug_industria("Beverages - Non-Alcoholic") == "beverages-non-alcoholic"
    assert _slug_industria(None) == ""
