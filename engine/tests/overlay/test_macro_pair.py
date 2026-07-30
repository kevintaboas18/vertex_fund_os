"""RSK-CYC-034's macro pair: revenue growth vs nominal-GDP growth.

FORMULAS.md defines it as the "OLS coefficient of company metric changes ...
on macro-factor changes" and requires an "economic rationale" plus
"sufficient observations". The risk specialist's regulatory/macro dimension
has no other mechanically scorable member, so without this pair the whole
2-point dimension was NOT_SCORABLE on every company.
"""

from __future__ import annotations

from types import SimpleNamespace

from wbj.overlay.from_packet import _MACRO_MIN_OBSERVATIONS, _macro_pair


class _FakeFred:
    available = True

    def __init__(self, observations=None, raises=False):
        self._observations = observations
        self._raises = raises

    def series(self, series_id, limit=200):
        if self._raises:
            raise RuntimeError("unreachable")
        return {"observations": self._observations}


def _packet(years_and_revenue):
    return SimpleNamespace(fundamentals={
        "annual": [{"calendarYear": str(y), "revenue": r}
                   for y, r in reversed(years_and_revenue)]
    })


def _gdp(years, start=100.0, growth=0.05):
    """One quarterly observation per year, growing at `growth`."""
    return [{"date": f"{y}-01-01", "value": str(start * (1 + growth) ** i)}
            for i, y in enumerate(years)]


def test_the_pair_is_growth_rates_not_levels():
    """A regression of levels on levels measures shared trend, not
    sensitivity, and would report a large beta for any growing company."""
    years = list(range(2016, 2026))
    packet = _packet([(y, 100.0 * (1.10 ** i)) for i, y in enumerate(years)])
    pair = _macro_pair(packet, _FakeFred(_gdp(years)))
    assert pair is not None
    company, macro = pair
    # Growth rates, so every observation is ~0.10 and ~0.05 -- not 100+.
    assert all(abs(c - 0.10) < 1e-6 for c in company)
    assert all(abs(m - 0.05) < 1e-6 for m in macro)
    assert len(company) == len(macro)


def test_too_little_history_abstains():
    """DATASET.md asks for 5 years; below the floor the pair is withheld
    rather than regressed on a handful of points."""
    years = list(range(2023, 2026))  # 3 years
    packet = _packet([(y, 100.0) for y in years])
    assert _macro_pair(packet, _FakeFred(_gdp(years))) is None


def test_an_unreachable_series_abstains_rather_than_guessing():
    years = list(range(2016, 2026))
    packet = _packet([(y, 100.0) for y in years])
    assert _macro_pair(packet, _FakeFred(raises=True)) is None


def test_no_fred_abstains():
    years = list(range(2016, 2026))
    packet = _packet([(y, 100.0) for y in years])
    assert _macro_pair(packet, None) is None
    assert _macro_pair(packet, SimpleNamespace(available=False)) is None


def test_years_without_a_macro_observation_are_dropped_not_imputed():
    years = list(range(2016, 2026))
    packet = _packet([(y, 100.0 * (1.10 ** i)) for i, y in enumerate(years)])
    # GDP only covers the second half of the window.
    pair = _macro_pair(packet, _FakeFred(_gdp(years[5:])))
    if pair is not None:
        company, macro = pair
        assert len(company) == len(macro)
        assert len(company) >= _MACRO_MIN_OBSERVATIONS
