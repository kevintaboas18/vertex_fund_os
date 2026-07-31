"""Every adapter branch, exercised.

The recurring failure in this engine has not been wrong arithmetic — it
has been branches that never ran. `model_fit`'s non-default arm sat dead
until the packet builder stopped hardcoding `default_nonfinancial`;
`customer_economics` looked correct for as long as it was only ever
checked against a chip maker. Both were latent from the day they were
written and surfaced only when the input space widened.

So this file walks the input space on purpose: one representative
classification per adapter, asserted for invariants that must hold
whatever the security is. It uses no network — the point is the branch
coverage, not the data.
"""

import pytest

from wbj.core.nullstates import NullState, Value
from wbj.core.scoring import Category, Dimension
from wbj.packet.builder import _industry_adapter_for
from wbj.specialists.business import _subscription_business

# One real classification per adapter, as FMP reports them.
CLASSIFICATIONS = [
    ("NVDA", "Technology", "Semiconductors", "default_nonfinancial"),
    ("KO", "Consumer Defensive", "Beverages - Non-Alcoholic", "default_nonfinancial"),
    ("NFLX", "Communication Services", "Entertainment", "default_nonfinancial"),
    ("CRM", "Technology", "Software - Application", "saas_subscriptions"),
    ("JPM", "Financial Services", "Banks - Diversified", "banks"),
    ("PGR", "Financial Services", "Insurance - Property & Casualty", "insurers"),
    ("V", "Financial Services", "Financial - Credit Services", "default_nonfinancial"),
    ("O", "Real Estate", "REIT - Retail", "reits"),
    ("MRNA", "Healthcare", "Biotechnology", "biotech"),
    ("XOM", "Energy", "Oil & Gas Integrated", "commodities_cyclicals"),
]


@pytest.mark.parametrize("ticker,sector,industry,expected", CLASSIFICATIONS)
def test_every_classification_maps_to_its_documented_adapter(
    ticker, sector, industry, expected
):
    assert _industry_adapter_for({"sector": sector, "industry": industry}) == expected


def test_every_adapter_in_the_map_is_reachable():
    """A mapping nobody can reach is a branch that will never be tested.
    Each adapter this module can assign must be produced by at least one
    real classification above."""
    from wbj.packet.builder import _ADAPTER_BY_INDUSTRY, _ADAPTER_BY_SECTOR

    declared = {a for _, a in (*_ADAPTER_BY_INDUSTRY, *_ADAPTER_BY_SECTOR)}
    reached = {_industry_adapter_for({"sector": s, "industry": i})
               for _, s, i, _ in CLASSIFICATIONS}
    assert declared - reached == set(), f"unreachable adapters: {declared - reached}"


def test_every_adapter_has_a_model_fit():
    """Every adapter the builder can assign must land on one of
    INDUSTRY_ADAPTERS.md's three documented treatments, so none falls
    through to an unintended default — the failure that once told a
    subscription business its analysis was a poor fit."""
    from wbj.core import adapters as ad
    from wbj.core import confidence_inputs as ci
    from wbj.packet.builder import _ADAPTER_BY_INDUSTRY, _ADAPTER_BY_SECTOR

    documented = {ci.MODEL_FIT_REPLACED, ci.MODEL_FIT_NORMALIZED, ci.MODEL_FIT_ADDITIVE}
    adapters = {a for _, a in (*_ADAPTER_BY_INDUSTRY, *_ADAPTER_BY_SECTOR)}
    adapters.add("default_nonfinancial")

    for adapter in adapters:
        fit = ci.model_fit(replaces_model=ad.replaces_model(adapter),
                           normalizes_inputs=ad.normalizes_inputs(adapter))
        assert fit in documented, adapter


@pytest.mark.parametrize("ticker,sector,industry,adapter", CLASSIFICATIONS)
def test_customer_economics_scope_is_decided_for_every_classification(
    ticker, sector, industry, adapter
):
    """Relief must be an affirmative call, never a default. Industrials
    and commodity producers get it; everything with returning customers
    does not."""
    from types import SimpleNamespace

    packet = SimpleNamespace(
        analysis=SimpleNamespace(industry_adapter=adapter),
        security=SimpleNamespace(sector=sector, industry=industry),
    )
    in_scope = _subscription_business(packet, {})
    # FORMULAS.md gates BUS-NRR-020..BUS-PAYBACK-026 on the business
    # model: "Subscription/business-model adapter only". Only a model
    # that runs on recurring contracts produces net revenue retention,
    # logo churn and CAC payback. Coca-Cola sells concentrate to
    # bottlers; Visa clears transactions; the bank, insurer and REIT
    # adapters replace these with their own retention analogues
    # (combined ratio, occupancy, lease maturities).
    subscription_models = {"NFLX", "CRM"}
    assert in_scope is (ticker in subscription_models), ticker


def test_an_output_with_no_dimensions_reads_as_unscored():
    """Valuation returns an empty output when its adapter replaces the
    model — a bank, a REIT. Treating that as "scored" charged the
    category its full maximum as a zero earned on merit."""
    from wbj.deep import is_unscored

    class _Declined:
        dimensions: list = []

    assert is_unscored(_Declined()) is True


def test_a_declined_category_does_not_count_as_a_zero_on_merit():
    """The companion invariant on the scoring side: no dimensions means
    no points, and the caller must be able to tell that apart from a
    category that genuinely scored nothing."""
    empty = Category(name="valuation", max_points=10.0, dimensions=[])
    scored_zero = Category(
        name="valuation", max_points=10.0,
        dimensions=[Dimension(name="d", max_points=10.0,
                              metric_scores=[(1.0, Value.of(0.0, unit="score"))])],
    )
    assert empty.points() == 0.0
    assert scored_zero.points() == 0.0  # same number, different meaning

    from wbj.deep import is_unscored

    class _O:
        def __init__(self, c):
            self.dimensions = c.dimensions

    assert is_unscored(_O(empty)) is True
    assert is_unscored(_O(scored_zero)) is False


def test_not_applicable_and_not_scorable_stay_distinguishable():
    """The distinction the whole missing-data policy rests on: one leaves
    the denominator, the other keeps costing."""
    na = Value.null(NullState.NOT_APPLICABLE, unit="score")
    ns = Value.null(NullState.NOT_SCORABLE, unit="score")
    scored = Dimension(name="a", max_points=17.0,
                       metric_scores=[(1.0, Value.of(6.0, unit="score"))])

    inapplicable = Category(
        name="business", max_points=20.0,
        dimensions=[scored, Dimension(name="b", max_points=3.0,
                                      metric_scores=[(1.0, na)])])
    unsourced = Category(
        name="business", max_points=20.0,
        dimensions=[scored, Dimension(name="b", max_points=3.0,
                                      metric_scores=[(1.0, ns)])])

    assert inapplicable.applicable_max_points() == 17.0
    assert unsourced.applicable_max_points() == 20.0
    assert inapplicable.points() > unsourced.points()


def test_valuation_declines_only_the_adapters_that_replace_its_model():
    """INDUSTRY_ADAPTERS.md names a different primary model for banks,
    insurers, REITs and biotech. It does not for SaaS ("Add ARR growth,
    ...") or commodities ("Normalize price/deck and margins through a
    cycle") — those keep the DCF. Gating on `!= default_nonfinancial`
    refused a valuation the document asks for."""
    from wbj.specialists.valuation import (
        CYCLE_NORMALIZED_ADAPTERS,
        MODEL_REPLACING_ADAPTERS,
    )

    assert MODEL_REPLACING_ADAPTERS == {"banks", "insurers", "reits", "biotech"}
    assert "saas_subscriptions" not in MODEL_REPLACING_ADAPTERS
    assert "commodities_cyclicals" not in MODEL_REPLACING_ADAPTERS
    assert CYCLE_NORMALIZED_ADAPTERS.isdisjoint(MODEL_REPLACING_ADAPTERS)


@pytest.mark.parametrize("ticker,sector,industry,adapter", CLASSIFICATIONS)
def test_valuation_model_fit_is_graded_not_binary(ticker, sector, industry, adapter):
    """Same defect business carried: a 90/40 split told
    every non-default adapter its model barely fit."""
    from types import SimpleNamespace

    from wbj.specialists.valuation import _category_confidence

    packet = SimpleNamespace(
        analysis=SimpleNamespace(industry_adapter=adapter),
        staleness={"consensus": "FRESH"},
    )
    conf = _category_confidence(1.0, packet)
    assert 0.0 < conf <= 100.0
    if adapter == "commodities_cyclicals":
        default = SimpleNamespace(
            analysis=SimpleNamespace(industry_adapter="default_nonfinancial"),
            staleness={"consensus": "FRESH"},
        )
        # Reduced against the default, but not down at the floor used for
        # a company whose model is replaced outright.
        replaced = SimpleNamespace(
            analysis=SimpleNamespace(industry_adapter="banks"),
            staleness={"consensus": "FRESH"},
        )
        assert (_category_confidence(1.0, replaced)
                < conf
                < _category_confidence(1.0, default))


def test_no_test_fixture_invents_an_adapter_name():
    """`test_valuation.py` asserted valuation's refusal using the names
    "bank_adapter" and "reit_adapter", which the packet builder never
    emits. The assertions passed anyway, because the old gate rejected
    any string that was not the default — so the tests proved nothing
    about real banks, and the same fixtures would have gone on passing if
    the gate had broken. Every adapter literal in the suite must be a
    name the builder can actually produce."""
    import re
    from pathlib import Path

    from wbj.packet.builder import _ADAPTER_BY_INDUSTRY, _ADAPTER_BY_SECTOR

    real = {a for _, a in (*_ADAPTER_BY_INDUSTRY, *_ADAPTER_BY_SECTOR)}
    real.add("default_nonfinancial")

    offenders = []
    for path in Path(__file__).parent.rglob("test_*.py"):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for name in re.findall(r'industry_adapter\s*=\s*["\']([^"\']+)["\']', line):
                if name not in real:
                    offenders.append(f"{path.name}:{lineno} {name!r}")
    assert not offenders, "adapter names no builder emits: " + "; ".join(offenders)


def test_customer_economics_scores_when_the_overlay_supplies_its_inputs():
    """The dimension read 0/3 on every company checked, which looks like
    a wiring fault and was worth ruling out. It is not: given the inputs
    it asks for, all three slots score. The zeros are missing data."""
    scored = [(0.4, Value.of(8.0, unit="score")),
              (0.3, Value.of(7.0, unit="score")),
              (0.3, Value.of(6.0, unit="score"))]
    dim = Dimension(name="customer_economics", max_points=3.0, metric_scores=scored)
    assert dim.applicable_weight() == pytest.approx(1.0)
    assert dim.valid_weight() == pytest.approx(1.0)
    assert dim.score10() > 0.0

    empty = Dimension(name="customer_economics", max_points=3.0,
                      metric_scores=[(w, Value.null(NullState.NOT_SCORABLE, unit="score"))
                                     for w in (0.4, 0.3, 0.3)])
    # Still applicable — this is a subscription business, the metrics
    # simply were not sourced — so the weight stays in the denominator
    # and the missing evidence costs, exactly as MISSING_DATA_POLICY.md
    # requires.
    assert empty.applicable_weight() == pytest.approx(1.0)
    assert empty.valid_weight() == pytest.approx(0.0)


def test_a_misshapen_analyst_block_announces_itself():
    """NRR is computed from begin/expansion/contraction/churn, not read
    from an `nrr` key. An analyst writing the finished ratio got exactly
    the same result as writing nothing — a silent 0/3 — with no way to
    tell the two apart."""
    from wbj.overlay.from_packet import _analyst_block_warnings

    assert _analyst_block_warnings({}) == []
    assert _analyst_block_warnings(
        {"retention": {"begin": 1.0, "expansion": 1.0,
                       "contraction": 1.0, "churn": 1.0}}) == []

    warnings = _analyst_block_warnings({"retention": {"nrr": 1.08}})
    assert len(warnings) == 1
    assert "retention" in warnings[0]
    assert "expansion" in warnings[0]


def test_analyst_input_warnings_reach_the_report():
    """A warning only in the log is a warning the reader never sees.

    The overlay carries the warning; business has to surface it in the
    output the report is built from, so run it and read the output back.
    """
    from datetime import datetime, timezone

    from wbj.schemas.packet import AnalysisMeta, Packet, Security
    from wbj.specialists import business

    packet = Packet(
        security=Security(ticker="T", exchange="X", security_type="operating_company",
                          reporting_currency="USD", valuation_currency="USD",
                          sector="Technology", industry="Software"),
        analysis=AnalysisMeta(
            knowledge_timestamp=datetime.now(timezone.utc).isoformat(),
            industry_adapter="default_nonfinancial"),
        fundamentals={"annual": []}, facts_table={}, market_data={},
        estimates={}, capital_structure={}, staleness={})

    warning = "retention block: 'nrr' is computed, not read"
    out = business.run(packet, {"analyst_input_warnings": [warning]})
    assert warning in out.assumptions

    clean = business.run(packet, {})
    assert warning not in clean.assumptions
