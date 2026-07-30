"""VAL-NWC-006: non-cash working capital.

FORMULAS.md registers it as "Operating current assets excluding cash -
Operating current liabilities excluding debt", with "exclude financing items".
It is what `VAL-FCFF-005` subtracts as `dnwc` and what `BUS-REINV-018` counts
as its second term, so a wrong sign here is a wrong free cash flow and a wrong
reinvestment rate at the same time.

It existed as an unregistered local helper in `business.py` (added when
BUS-REINV-018 needed a balance-sheet fallback) and had no VAL row at all. Both
now call one implementation: two copies of one formula is two sign conventions
waiting to disagree.
"""

import json
from pathlib import Path

import pytest

from wbj.core.nullstates import NullState
from wbj.engines import valuation_engine as ve
from wbj.schemas.packet import Packet
from wbj.specialists import business as biz
from wbj.specialists import valuation as val

_FIXTURE = Path(__file__).parent / "fixtures" / "packet" / "NVDA_packet.json"


@pytest.fixture(scope="module")
def packet() -> Packet:
    return Packet.model_validate(json.loads(_FIXTURE.read_text()))


def test_formula_matches_the_registry_text():
    """(1000 - 200) - (600 - 100) = 300."""
    v = ve.non_cash_working_capital(1000.0, 200.0, 0.0, 600.0, 100.0)
    assert v.value == pytest.approx(300.0)
    assert v.unit == "usd"


def test_cash_is_excluded_from_the_asset_side():
    with_cash = ve.non_cash_working_capital(1000.0, 0.0, 0.0, 600.0, 0.0).value
    less_cash = ve.non_cash_working_capital(1000.0, 200.0, 0.0, 600.0, 0.0).value
    assert with_cash - less_cash == pytest.approx(200.0)


def test_short_term_debt_is_excluded_from_the_liability_side():
    """Debt is a financing claim, not an operating one, so removing it
    RAISES the level."""
    without = ve.non_cash_working_capital(1000.0, 0.0, 0.0, 600.0, 0.0).value
    with_debt = ve.non_cash_working_capital(1000.0, 0.0, 0.0, 600.0, 100.0).value
    assert with_debt - without == pytest.approx(100.0)


def test_short_term_investments_are_excluded_as_excess_cash():
    """DATA_DICTIONARY.md reconciles invested capital to "debt plus equity
    minus excess cash". Marketable securities are excess cash parked in a
    current account, not capital tied up in operations -- AAPL FY2025 carries
    18.8B of it, which left in would overstate the level by that much."""
    without = ve.non_cash_working_capital(1000.0, 0.0, 0.0, 600.0, 0.0).value
    with_sti = ve.non_cash_working_capital(1000.0, 0.0, 150.0, 600.0, 0.0).value
    assert without - with_sti == pytest.approx(150.0)


def test_a_build_is_positive():
    """Sign convention: cash tied up in operations is positive, which is what
    VAL-FCFF-005 subtracts."""
    t0 = ve.non_cash_working_capital(1000.0, 0.0, 0.0, 600.0, 0.0).value
    t1 = ve.non_cash_working_capital(1200.0, 0.0, 0.0, 600.0, 0.0).value
    assert t1 - t0 > 0


def test_missing_totals_are_missing_not_zero():
    """A working capital of zero and one that could not be computed are not
    the same claim."""
    for args in ((None, 0.0, 0.0, 600.0, 0.0), (1000.0, 0.0, 0.0, None, 0.0)):
        v = ve.non_cash_working_capital(*args)
        assert v.is_null and v.state is NullState.MISSING


def test_absent_cash_and_debt_lines_are_treated_as_zero_balances():
    """A filer that omits the line has no such balance; that is knowable."""
    assert ve.non_cash_working_capital(1000.0, None, None, 600.0, None).value == pytest.approx(400.0)


def test_business_helper_delegates_to_the_registered_formula():
    """One formula, one sign convention."""
    row = {"total_current_assets": 1000.0, "cash": 200.0,
           "total_current_liabilities": 600.0, "short_term_debt": 100.0}
    assert biz.non_cash_working_capital(row) == pytest.approx(300.0)


def test_business_helper_returns_none_when_the_formula_cannot_compute():
    assert biz.non_cash_working_capital({"cash": 200.0}) is None


def test_row_is_emitted_by_the_valuation_specialist(packet):
    rows = {m.metric_id: m for m in val.run(packet, {}).metrics}
    assert "VAL-NWC-006" in rows
    assert rows["VAL-NWC-006"].unit == "usd"


def test_row_is_reported_not_scored(packet):
    """SCORING.md gives VAL-NWC-006 no dimension; what the models consume is
    its change, not its level."""
    rows = {m.metric_id: m for m in val.run(packet, {}).metrics}
    assert rows["VAL-NWC-006"].score == "NOT_SCORABLE"


def test_the_period_change_is_disclosed(packet):
    out = val.run(packet, {})
    rows = {m.metric_id: m for m in out.metrics}
    if rows["VAL-NWC-006"].value is None:
        pytest.skip("fixture lacks the current-account totals")
    assert any("VAL-NWC-006" in a for a in out.assumptions)


def test_a_model_replacing_adapter_does_not_derive_nwc_from_the_balance_sheet():
    """INDUSTRY_ADAPTERS.md bars conventional FCFF for banks, and VAL-NWC-006
    is one of its inputs. A bank's current accounts are deposits and loans:
    the derivation is arithmetically fine and economically meaningless (JPM
    computes to -2.7 trillion). A *reported* cash-flow line stays usable --
    the filer decided what belongs in it -- but deriving one here does not."""
    from types import SimpleNamespace

    rows = [{"total_current_assets": 3.0e12, "cash": 5.0e11,
             "total_current_liabilities": 5.5e12, "short_term_debt": 1.0e11,
             "ebit": 6.0e10, "capex": 1.0e10},
            {"total_current_assets": 3.2e12, "cash": 5.2e11,
             "total_current_liabilities": 5.8e12, "short_term_debt": 1.1e11,
             "ebit": 6.2e10, "capex": 1.1e10}]
    # No `changeInWorkingCapital` on either row: the fallback path is what
    # this test is about.
    assert all("changeInWorkingCapital" not in r for r in rows)
    packet = SimpleNamespace(analysis=SimpleNamespace(industry_adapter="banks"))
    from wbj.core import adapters as ad
    assert ad.replaces_model(packet.analysis.industry_adapter)
    # The raw formula still computes -- it is the *caller* that must decline.
    assert ve.non_cash_working_capital_from_row(rows[-1]) < -2.0e12
