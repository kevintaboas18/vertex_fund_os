"""The confidence components, against what Cerebro actually states.

CONFIDENCE_ENGINE.md fixes the formula and its weights and then gives
each component a one-line description with no numbers. Three arrived as
flat constants — `source_quality = 85.0`, `consistency = 80.0`, and a
per-adapter `model_fit` of 90/65/50/40. None appears in any of the
eighty-three documents, and the first two measured nothing at all: the
same figures came back whether a metric rested on an audited filing or
an analyst's estimate, and whether the two data sources agreed or
contradicted each other.

Each description names a signal Cerebro defines elsewhere.
"""

import pytest

from wbj.core import adapters as ad
from wbj.core import confidence_inputs as ci


# --- source_quality --------------------------------------------------------


def test_the_class_order_is_data_policys_own():
    """DATA_POLICY.md lists R, C, E, A, Q in that order, and
    CONFIDENCE_ENGINE.md's one explicit comparison agrees with it:
    "regulatory/official evidence scores higher than secondary
    estimates" — R is "reported directly in a filing or official
    release", E is "external estimate"."""
    assert ci.EVIDENCE_CLASS_ORDER == ("R", "C", "E", "A", "Q")
    q = ci.EVIDENCE_CLASS_QUALITY
    assert q["R"] > q["C"] > q["E"] > q["A"] > q["Q"]


def test_the_classes_are_evenly_spaced_across_the_stated_range():
    """Cerebro gives the ordering and no magnitudes. Even spacing over
    the component's stated 0-100 range is the only mapping that respects
    the order without asserting that some gaps are wider than others."""
    q = ci.EVIDENCE_CLASS_QUALITY
    assert q["R"] == pytest.approx(100.0)
    assert q["Q"] == pytest.approx(0.0)
    gaps = [q[a] - q[b] for a, b in zip(ci.EVIDENCE_CLASS_ORDER,
                                        ci.EVIDENCE_CLASS_ORDER[1:])]
    assert all(g == pytest.approx(gaps[0]) for g in gaps)


def test_source_quality_averages_the_evidence_actually_used():
    """A flat 85 said nothing about what backed the numbers."""
    assert ci.source_quality(["R", "R"]) == pytest.approx(100.0)
    assert ci.source_quality(["C", "C"]) == pytest.approx(75.0)
    assert ci.source_quality(["R", "E"]) == pytest.approx(75.0)
    assert ci.source_quality(["R", "Q"]) == pytest.approx(50.0)


def test_filing_backed_evidence_outscores_estimates():
    """The one comparison CONFIDENCE_ENGINE.md makes explicitly."""
    assert ci.source_quality(["R"]) > ci.source_quality(["E"])


def test_no_evidence_is_not_poor_evidence():
    """Returning a floor would let `coverage` and `source_quality` both
    charge for the same absence."""
    assert ci.source_quality([]) is None
    assert ci.source_quality(["not-a-class", None]) is None


def test_an_evidence_class_enum_is_accepted():
    """Callers hold `EvidenceClass`, not bare strings."""
    from wbj.core.nullstates import EvidenceClass

    assert ci.source_quality([EvidenceClass.R]) == pytest.approx(100.0)
    assert ci.source_quality([EvidenceClass.C]) == pytest.approx(75.0)


# --- consistency -----------------------------------------------------------


def test_consistency_is_the_share_of_checks_that_agreed():
    """"agreement across periods and reconciled sources" — measurable
    from the packet's own FMP-vs-EDGAR verdicts and the per-period
    invested-capital reconciliation."""
    assert ci.consistency(4, 4) == pytest.approx(100.0)
    assert ci.consistency(3, 4) == pytest.approx(75.0)
    assert ci.consistency(0, 4) == pytest.approx(0.0)


def test_no_comparable_source_is_not_a_disagreement():
    """A fact only one source reports cannot agree or disagree, so it is
    excluded rather than counted either way — and when nothing at all can
    be compared, there is no measurement to report."""
    assert ci.consistency(0, 0) is None


def test_consistency_stays_inside_the_stated_range():
    assert ci.consistency(9, 4) == pytest.approx(100.0)
    assert ci.consistency(-1, 4) == pytest.approx(0.0)


# --- model_fit -------------------------------------------------------------


def test_the_three_treatments_are_evenly_spaced():
    """INDUSTRY_ADAPTERS.md sorts adapters into ones that replace the
    model, ones that normalize its inputs, and ones that only add to it.
    Three levels, no magnitudes stated."""
    assert ci.MODEL_FIT_ADDITIVE == pytest.approx(100.0)
    assert ci.MODEL_FIT_NORMALIZED == pytest.approx(50.0)
    assert ci.MODEL_FIT_REPLACED == pytest.approx(0.0)


@pytest.mark.parametrize("adapter,expected", [
    ("default_nonfinancial", ci.MODEL_FIT_ADDITIVE),
    ("saas_subscriptions", ci.MODEL_FIT_ADDITIVE),      # "Add ARR growth, ..."
    ("commodities_cyclicals", ci.MODEL_FIT_NORMALIZED),  # "Normalize price/deck ..."
    ("banks", ci.MODEL_FIT_REPLACED),                    # "Replace ROIC with ROE ..."
    ("insurers", ci.MODEL_FIT_REPLACED),
    ("reits", ci.MODEL_FIT_REPLACED),                    # "Replace EPS with FFO/AFFO"
    ("biotech", ci.MODEL_FIT_REPLACED),                  # "Use risk-adjusted NPV"
])
def test_each_adapter_lands_on_its_documented_treatment(adapter, expected):
    fit = ci.model_fit(replaces_model=ad.replaces_model(adapter),
                       normalizes_inputs=ad.normalizes_inputs(adapter))
    assert fit == pytest.approx(expected)


def test_an_additive_adapter_fits_as_well_as_no_adapter():
    """SaaS only adds metrics; the core formulas are untouched, so the
    security type suits them exactly as well as a default one does."""
    assert ci.model_fit(replaces_model=False, normalizes_inputs=False) == \
           pytest.approx(ci.MODEL_FIT_ADDITIVE)


def test_replacement_outranks_normalization_when_both_are_claimed():
    """A defensive ordering: a caller that somehow reports both must get
    the worse fit, not the better one."""
    assert ci.model_fit(replaces_model=True, normalizes_inputs=True) == \
           pytest.approx(ci.MODEL_FIT_REPLACED)


# --- business actually wires them -----------------------------------------


def test_business_no_longer_hardcodes_the_three_components():
    """The point of the module is that these stop being constants."""
    import inspect
    import re

    from wbj.specialists import business as bus

    src = inspect.getsource(bus._category_confidence)
    # A bare `x = 12.3` is a constant. `x = 12.3 if measured is None else
    # measured` is a fallback for the case where nothing could be
    # measured, which is a different thing and is documented as such.
    constant = re.compile(
        r"^(source_quality|consistency|model_fit)\s*=\s*[\d.]+\s*(#.*)?$")
    for line in src.splitlines():
        stripped = line.strip()
        assert not constant.match(stripped), f"component hardcoded again: {stripped}"
    assert "_confidence_inputs.source_quality" in src
    assert "_confidence_inputs.consistency" in src
    assert "_confidence_inputs.model_fit" in src


def test_the_proxy_floor_cannot_raise_a_replaced_model():
    """The floor exists so a proxy cannot push a fit below the documented
    bottom. It was 40, chosen when a replaced-model adapter also scored
    40; once that bottom became 0 the same floor did the opposite of its
    job and *raised* every bank, insurer, REIT and biotech to 40."""
    from wbj.specialists import business as bus

    assert bus.MODEL_FIT_PROXY_FLOOR == pytest.approx(ci.MODEL_FIT_REPLACED)
    replaced = ci.model_fit(replaces_model=True, normalizes_inputs=False)
    assert max(bus.MODEL_FIT_PROXY_FLOOR, replaced) == pytest.approx(replaced)


def test_a_conflicted_source_costs_confidence():
    """End to end: two packets alike but for one contested fact must not
    report the same confidence."""
    from datetime import datetime, timezone

    from wbj.core.nullstates import NullState, Value
    from wbj.schemas.packet import AnalysisMeta, Packet, Security
    from wbj.specialists import business as bus

    row = dict(fiscalYear="2025", filingDate="2026-02-20", revenue=1000.0,
               gross_profit=600.0, ebit=200.0, net_income=150.0,
               operating_cash_flow=250.0, capex=-40.0, fcf=210.0,
               total_debt=150.0, total_equity=600.0, cash=200.0,
               total_assets=1000.0, total_liabilities=400.0,
               income_before_tax=190.0, income_tax_expense=40.0,
               diluted_shares=100.0, stock_based_compensation=10.0,
               changeInWorkingCapital=-15.0)

    def packet(facts):
        return Packet(
            security=Security(ticker="T", exchange="X",
                              security_type="operating_company",
                              reporting_currency="USD", valuation_currency="USD",
                              sector="Technology", industry="Semiconductors"),
            analysis=AnalysisMeta(
                knowledge_timestamp=datetime.now(timezone.utc).isoformat(),
                industry_adapter="default_nonfinancial"),
            fundamentals={"annual": [dict(row) for _ in range(5)]},
            facts_table=facts, market_data={}, estimates={},
            capital_structure={}, staleness={})

    agree = packet({"revenue": Value.of(1000.0, unit="USD"),
                    "cash": Value.of(200.0, unit="USD")})
    conflict = packet({"revenue": Value.of(1000.0, unit="USD"),
                       "cash": Value.null(NullState.CONFLICTED, unit="USD")})

    assert bus.run(agree, {}).category.confidence > \
           bus.run(conflict, {}).category.confidence
