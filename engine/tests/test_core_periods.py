"""One reading of a statement row's fiscal year (`wbj.core.periods`).

Three modules read this independently and disagreed. business.py and
from_packet.py both asked for `fiscalYear`, a field no builder and no FMP
response carries: every metric's `period` degraded to "FY?", and every peer
recession drawdown came back None, so DECISION_RULES.md's wide-moat
condition-2 alternative ("peer-relative resilience is in the top quartile
through a cycle") could never engage.
"""

import pytest

from wbj.core.periods import fiscal_year
from wbj.overlay.from_packet import _recession_margin_drawdown as peer_drawdown
from wbj.specialists.business import recession_margin_drawdown as own_drawdown


@pytest.mark.parametrize("row,expected", [
    ({"fiscalYear": "2025"}, "2025"),                    # hand-built rows
    ({"calendarYear": "2025"}, "2025"),                  # the builder and raw FMP
    ({"calendarYear": 2025}, "2025"),                    # FMP sometimes sends an int
    ({"date": "2025-01-26"}, "2025"),                    # last resort
    ({"fiscalYear": "2025", "calendarYear": "2024"}, "2025"),  # explicit wins
    ({}, None),
    ({"calendarYear": "n/a", "date": "n/a"}, None),
])
def test_the_year_is_read_from_whichever_field_the_row_carries(row, expected):
    assert fiscal_year(row) == expected


def test_a_peer_row_from_fmp_produces_a_drawdown():
    """Raw FMP rows carry `calendarYear` and `operatingIncome`. Asking for
    `fiscalYear` left `by_year` empty, so every peer returned None and the
    eight comparables SCORING_ENGINE.md requires never accumulated."""
    fmp_rows = [{"calendarYear": "2007", "revenue": 1000, "operatingIncome": 200},
                {"calendarYear": "2008", "revenue": 1000, "operatingIncome": 120},
                {"calendarYear": "2009", "revenue": 1000, "operatingIncome": 100}]
    assert peer_drawdown(fmp_rows, [2008, 2009]) == pytest.approx(0.08)


def test_the_company_and_its_peers_are_ranked_on_the_same_quantity():
    """`_recession_margin_drawdown`'s docstring promises the same
    construction the business specialist applies to the security itself."""
    fmp_rows = [{"calendarYear": "2007", "revenue": 1000, "operatingIncome": 200},
                {"calendarYear": "2008", "revenue": 1000, "operatingIncome": 120},
                {"calendarYear": "2009", "revenue": 1000, "operatingIncome": 100}]
    own = own_drawdown([(2007, 0.20), (2008, 0.12), (2009, 0.10)], [2008, 2009])
    assert peer_drawdown(fmp_rows, [2008, 2009]) == pytest.approx(own.value)
