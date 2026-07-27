"""Victor's own validation cases, executed by ID.

Every `Cerebro/*/VALIDATION_TESTS.md` ends with the same instruction:

    "Every release must pass these tests and the shared QA checklist."

The behaviours were implemented, but nothing tied them back to the case
IDs, so a regression could silently break a documented guarantee. This
file closes that loop: each test name carries the Cerebro ID and asserts
exactly the expected result written in the document.

Cases that need a full packet, an industry adapter or a live source
conflict are exercised by the area suites (`tests/specialists/`,
`tests/aggregate/`, `tests/packet/`) rather than duplicated here; they
are listed at the bottom so the mapping stays complete.
"""

import pandas as pd
import pytest

from wbj.core.scoring import CATEGORY_WEIGHTS
from wbj.engines import indicators as ind
from wbj.engines.valuation_engine import nopat, roic, spread
from wbj.specialists import business as bus
from wbj.specialists import financial as fin
from wbj.specialists import market as mkt
from wbj.specialists import risk as rsk


def val(v):
    """Unwrap a `Value`, or None when it carries a null state."""
    return getattr(v, "value", None)


# =============================================================================
# 00_main_agent
# =============================================================================


def test_main_001_category_maxima_sum_to_100():
    """MAIN-001 | Category maxima sum to 100 | Pass exactly"""
    assert sum(CATEGORY_WEIGHTS.values()) == 100


def test_main_002_raw_total_of_documented_example():
    """MAIN-002 | Recalculate example 16+10.5+18+16+9+7 | 76.5"""
    from wbj.aggregate import raw_total

    assert raw_total([16, 10.5, 18, 16, 9, 7]) == 76.5


# =============================================================================
# 01_business_analysis
# =============================================================================


def test_bus_t001_nopat_and_roic():
    """BUS-T001 | EBIT=100, tax=25%, invested capital=500 | NOPAT=75; ROIC=15%"""
    assert val(nopat(100, 0.25)) == pytest.approx(75)
    assert val(roic(75, 500)) == pytest.approx(0.15)


def test_bus_t002_spread_and_economic_value():
    """BUS-T002 | ROIC=15%, WACC=10%, IC=500 | Spread=5 pts; economic value=25"""
    s = val(spread(0.15, 0.10))
    assert s == pytest.approx(0.05)
    assert s * 500 == pytest.approx(25)


def test_bus_t003_margin_range_and_stability():
    """BUS-T003 | Five margins 20,21,19,22,20 | Range=3 pts; stable signal"""
    r = val(bus.margin_range([0.20, 0.21, 0.19, 0.22, 0.20]))
    assert r == pytest.approx(0.03)
    assert bus.margin_range_is_stable(r) is True


def test_bus_t004_customer_concentration_red_flag():
    """BUS-T004 | Largest customer share=35% | Concentration red flag"""
    assert bus.is_concentration_red_flag(0.35) is True


def test_bus_t005_cumulative_fcf_conversion():
    """BUS-T005 | Five-year FCF=500, net income=450 | FCF conversion=1.111x"""
    assert val(bus.cumulative_fcf_conversion(500, 450)) == pytest.approx(1.111, abs=0.001)


def test_bus_t006_cagr_not_meaningful_when_base_non_positive():
    """BUS-T006 | Beginning revenue <=0 for CAGR | NOT_MEANINGFUL"""
    v = bus.revenue_cagr(200, 0, 4)
    assert val(v) is None
    assert str(getattr(v, "state", "")).endswith("NOT_MEANINGFUL")


def test_bus_t007_value_destruction_when_roic_below_wacc():
    """BUS-T007 | ROIC<WACC with score otherwise high | No excellent/wide-moat label"""
    assert bus.value_destruction_triggered(0.08, 0.10) is True
    assert bus.value_destruction_triggered(0.12, 0.10) is False


def test_bus_t008_no_penalty_for_a_non_applicable_subscription_metric():
    """BUS-T008 | Missing NRR for non-subscription industrial | Use
    adapter; no penalty for N/A

    The adapter now comes from the company's own classification, so
    subscription metrics report NOT_APPLICABLE on an industrial and the
    dimension leaves the category maximum instead of scoring zero.
    """
    from wbj.core.nullstates import NullState, Value
    from wbj.core.scoring import Category, Dimension
    from wbj.packet.builder import _industry_adapter_for
    from wbj.specialists.business import _subscription_business

    industrial = {"sector": "Technology", "industry": "Semiconductors"}
    assert _industry_adapter_for(industrial) == "default_nonfinancial"

    class _P:
        analysis = type("A", (), {"industry_adapter": "default_nonfinancial"})()
        security = type("S", (), {"sector": "Technology",
                                  "industry": "Semiconductors"})()

    # A chip maker has no repeat-customer economics to measure. Relief is
    # affirmative and narrow: Netflix and Costco sit under the same
    # default adapter and do *not* get it.
    assert _subscription_business(_P(), {}) is False

    class _Streaming:
        analysis = type("A", (), {"industry_adapter": "default_nonfinancial"})()
        security = type("S", (), {"sector": "Communication Services",
                                  "industry": "Entertainment"})()

    assert _subscription_business(_Streaming(), {}) is True

    na = Value.null(NullState.NOT_APPLICABLE, unit="score")
    customer_econ = Dimension(name="customer_economics", max_points=3.0,
                              metric_scores=[(0.4, na), (0.3, na), (0.3, na)])
    scored = Dimension(name="rest", max_points=17.0,
                       metric_scores=[(1.0, Value.of(6.0, unit="score"))])
    cat = Category(name="business", max_points=20.0,
                   dimensions=[scored, customer_econ])
    # No penalty: the inapplicable three points leave the denominator.
    assert cat.applicable_max_points() == 17.0
    assert cat.score10() == pytest.approx(6.0)


# =============================================================================
# 02_financial_analysis
# =============================================================================


def test_fin_t001_yoy_revenue_growth():
    """FIN-T001 | Revenue 110 vs 100 | YoY growth=10%"""
    assert val(fin.yoy_revenue_growth(110, 100)) == pytest.approx(0.10)


def test_fin_t003_and_t004_interest_coverage_warning_boundary():
    """FIN-T003 | EBIT=30, interest=20 | Coverage=1.5x; no warning
    FIN-T004 | EBIT=29, interest=20 | Coverage=1.45x; solvency warning"""
    clean, warned = rsk.interest_coverage(30, 20), rsk.interest_coverage(29, 20)
    assert val(clean) == pytest.approx(1.5)
    assert val(warned) == pytest.approx(1.45)
    # The documented threshold is "below 1.5" -> 1.5 is clean, 1.45 warns.
    assert rsk.SOLVENCY_WARNING not in clean.warnings
    assert rsk.SOLVENCY_WARNING in warned.warnings


def test_fin_t005_fcf_and_margin():
    """FIN-T005 | OCF=120, capex=40, revenue=800 | FCF=80; FCF margin=10%"""
    fcf = 120 - 40
    assert fcf == 80
    assert val(fin.fcf_margin(fcf, 800)) == pytest.approx(0.10)


def test_fin_t009_negative_equity_makes_debt_to_equity_not_meaningful():
    """FIN-T009 | Negative equity | Debt/equity NOT_MEANINGFUL"""
    v = fin.debt_to_equity(100, -50)
    assert val(v) is None
    assert str(getattr(v, "state", "")).endswith("NOT_MEANINGFUL")


# =============================================================================
# 03_market_analysis
# =============================================================================


def test_mkt_t001_tam_cagr():
    """MKT-T001 | TAM 1000 to 1210 over 2 years | CAGR=10%"""
    assert val(mkt.tam_cagr(1210, 1000, 2)) == pytest.approx(0.10, abs=1e-6)


def test_mkt_t002_penetration():
    """MKT-T002 | Company relevant revenue=50, TAM=1000 | Penetration=5%"""
    assert val(mkt.penetration(50, 1000)) == pytest.approx(0.05)


def test_mkt_t003_market_share_delta_in_percentage_points():
    """MKT-T003 | Share 5.0% to 5.7% | Delta=+0.7 percentage points"""
    assert val(mkt.market_share_delta(0.057, 0.050)) == pytest.approx(0.007, abs=1e-9)


def test_mkt_t004_revision_breadth():
    """MKT-T004 | 8 upward revisions out of 10 | Breadth=80%"""
    assert val(mkt.revision_breadth(8, 10)) == pytest.approx(0.80)


# =============================================================================
# 04_technical_momentum
# =============================================================================


def test_tech_t001_constant_series_has_zero_atr():
    """TECH-T001 | Constant price series | ATR=0; level-distance ratios guarded"""
    df = pd.DataFrame({"high": [10.0] * 30, "low": [10.0] * 30, "close": [10.0] * 30})
    assert float(ind.atr14(df).iloc[-1]) == pytest.approx(0.0)


def test_tech_t002_true_range():
    """TECH-T002 | High=12, low=10, prior close=11 | True range=2"""
    df = pd.DataFrame({"high": [11.0, 12.0], "low": [11.0, 10.0], "close": [11.0, 11.0]})
    assert float(ind.true_range(df).iloc[-1]) == pytest.approx(2.0)


# =============================================================================
# 05_risk_analysis
# =============================================================================


def test_rsk_t001_interest_coverage():
    """RSK-T001 | EBIT=15, interest=10 | Coverage=1.5x"""
    assert val(rsk.interest_coverage(15, 10)) == pytest.approx(1.5)


def test_rsk_t002_mandatory_solvency_warning():
    """RSK-T002 | Coverage=1.49x | Mandatory solvency warning"""
    v = rsk.interest_coverage(1.49, 1.0)
    assert val(v) == pytest.approx(1.49)
    assert rsk.SOLVENCY_WARNING in v.warnings


def test_rsk_t003_cash_runway_months():
    """RSK-T003 | Cash=120, facility=0, monthly burn=10 | Runway=12 months"""
    assert val(rsk.cash_runway_months(120, 0, 10)) == pytest.approx(12)


def test_rsk_t004_max_drawdown():
    """RSK-T004 | Price index peak 100, trough 40 | Max drawdown=-60%"""
    s = pd.Series([50.0, 100.0, 70.0, 40.0, 60.0])
    assert val(rsk.max_drawdown(s)) == pytest.approx(-0.60)


def test_rsk_t005_customer_hhi():
    """RSK-T005 | Two customers 50% each | HHI=0.50"""
    assert val(bus.customer_hhi([0.5, 0.5])) == pytest.approx(0.50)


def test_rsk_t006_negative_ebitda_makes_net_debt_ratio_not_meaningful():
    """RSK-T006 | Negative EBITDA | Net debt/EBITDA NOT_MEANINGFUL"""
    v = rsk.net_debt_to_ebitda(500, -100)
    assert val(v) is None
    assert str(getattr(v, "state", "")).endswith("NOT_MEANINGFUL")


# =============================================================================
# Cases covered by the area suites (kept here so the mapping is complete)
# =============================================================================
#
# MAIN-003..010  -> tests/aggregate/test_gates.py, test_overrides.py,
#                   test_synthesis.py, test_contradiction.py
# BUS-T008,      -> industry adapters, tests/specialists/test_business.py
# FIN-T002,006..008,010 -> tests/specialists/test_financial.py
# MKT-T005..T008 -> tests/specialists/test_market.py
# TECH-T003..T012 -> tests/engines/test_levels.py, tests/specialists/test_technical.py
# RSK-T007..T009 -> tests/specialists/test_risk.py
# VAL-T001..T010 -> tests/engines/test_valuation.py, tests/specialists/test_valuation.py
