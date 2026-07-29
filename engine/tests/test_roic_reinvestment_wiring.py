"""`roic_reinvestment`: Market's required input, taken from the specialist that
computes it properly.

`03_market_analysis/DATASET.md` declares `roic_reinvestment` **required** and
sourced from a "validated specialist packet" -- Business/Financial. Nobody
passed it, so MKT-GCAP-009 fell back to re-deriving a reinvestment rate locally
as capex / NOPAT and flagged REINVESTMENT_RATE_PROXY_CAPEX_OVER_NOPAT on every
ticker.

That proxy drops three of the four terms in Victor's numerator. BUS-REINV-018 is
"(Net capex + change in non-cash working capital + capitalized R&D adjustment) /
NOPAT", and Business already computes it with the sign conventions and the
bank/insurer adapter carve-out the proxy has none of. Measured effect of wiring
it through: growth capacity for AAPL goes 0.0858 -> 0.2546, KO 0.0332 -> 0.1466,
NVDA 0.0505 -> 0.1839. On MKT-GCAP-009's own anchors that moves AAPL from about
6/10 to 10/10, so the proxy was not a rounding detail.
"""

from types import SimpleNamespace

from wbj.deep import _ROIC_REINVESTMENT_METRICS, _roic_reinvestment_from


def _out(*filas):
    """A specialist output carrying `metrics` rows."""
    return SimpleNamespace(metrics=[
        SimpleNamespace(metric_id=mid, value=val) for mid, val in filas])


def test_both_inputs_are_read_off_the_business_rows():
    got = _roic_reinvestment_from(_out(("BUS-ROIC-013", 0.758),
                                      ("BUS-REINV-018", 0.3359)))
    assert got == {"roic": 0.758, "reinvestment_rate": 0.3359}


def test_the_metric_ids_are_the_registered_ones():
    """If FORMULAS.md ever renumbers these, the wiring must fail loudly rather
    than silently hand Market nothing and revert to the proxy."""
    assert _ROIC_REINVESTMENT_METRICS == {"BUS-ROIC-013": "roic",
                                          "BUS-REINV-018": "reinvestment_rate"}


def test_other_metrics_are_ignored():
    got = _roic_reinvestment_from(_out(("BUS-NOPAT-011", 1000.0),
                                       ("BUS-ROIC-013", 0.2)))
    assert got == {"roic": 0.2}


# --- a hole must not travel as a figure ------------------------------------

def test_a_null_row_is_not_passed_on():
    """A MISSING or NOT_SCORABLE row has `value is None`. Passing it would make
    Market score against a hole; leaving it out keeps MKT-GCAP-009 on its
    documented proxy, which is the honest fallback."""
    got = _roic_reinvestment_from(_out(("BUS-ROIC-013", None),
                                       ("BUS-REINV-018", 0.5)))
    assert got == {"reinvestment_rate": 0.5}


def test_a_boolean_is_not_a_number():
    """`isinstance(True, int)` is True in Python, and a bool reaching a ratio
    would score as 1.0."""
    assert _roic_reinvestment_from(_out(("BUS-ROIC-013", True))) == {}


def test_an_output_without_metrics_yields_nothing():
    assert _roic_reinvestment_from(SimpleNamespace()) == {}
    assert _roic_reinvestment_from(SimpleNamespace(metrics=None)) == {}
    assert _roic_reinvestment_from(_out()) == {}


# --- precedence ------------------------------------------------------------

def test_the_analyst_file_still_wins_over_the_derivation():
    """deep.py merges `{**derivadas, **overlay}`: the analyst file is already
    inside `overlay`, so a hand-entered figure stays authoritative. Reversing
    the order would make the derivation silently overwrite it."""
    derivadas = {"roic": 0.20, "reinvestment_rate": 0.30}
    overlay = {"roic": 0.99}                      # lo que puso el analista
    enriquecido = {**derivadas, **overlay}
    assert enriquecido["roic"] == 0.99
    assert enriquecido["reinvestment_rate"] == 0.30
