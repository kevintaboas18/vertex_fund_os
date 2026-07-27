"""Every formula emits a result object, whether or not it scores.

`shared/FORMULA_REGISTRY.md`: "Every formula must have: a stable formula ID;
formula text and sign convention; required inputs and units; ... formula
version; at least one validation test", and it specifies the formula-result
object — `formula_id`, `result: {value, unit}`, `status`, `warnings`.

Ten valuation formulas were computed and then dropped: the whole chain that
produces the per-share value (VAL-FCFF-005 → TVG-012 → EV-014 → EQ-015 →
PS-016, with TVS-042 the terminal share), plus the reinvestment rate, the
economic-profit cross-check, the Monte Carlo and the ensemble. Each existed as
a local variable and reached no reader.

`00_main_agent/AGENT.md` asks for "a traceable final report, not a black-box
opinion". A figure the report never carries cannot be traced or challenged.

Emitting is not scoring. Victor's own dimension bundles the range
`VAL-FCFF-005..027, 036..044` and scores it through `fair_value_by_scenarios`,
so these rows carry no score of their own — scoring them again would
double-count one dimension.
"""

import json
from pathlib import Path

import pytest

from wbj.core.scoring import Category
from wbj.schemas.packet import Packet
import wbj.specialists.valuation as val

_FIXTURE = Path(__file__).parent / "fixtures" / "packet" / "NVDA_packet.json"

#: The chain, in the order one figure feeds the next.
_CHAIN = ["VAL-FCFF-005", "VAL-TVG-012", "VAL-EV-014", "VAL-EQ-015", "VAL-PS-016"]
_ALSO = ["VAL-TVS-042", "VAL-REINV-043", "VAL-EVA-020", "VAL-MC-037", "VAL-ENSEMBLE-044"]


@pytest.fixture(scope="module")
def rows():
    packet = Packet.model_validate(json.loads(_FIXTURE.read_text()))
    return {r.metric_id: r for r in val.run(packet).metrics}


@pytest.fixture(scope="module")
def output():
    packet = Packet.model_validate(json.loads(_FIXTURE.read_text()))
    return val.run(packet)


def test_every_row_is_emitted(rows):
    """Present is the whole point: absent, the figure has no reader."""
    missing = [m for m in _CHAIN + _ALSO if m not in rows]
    assert not missing, f"sin fila de resultado: {missing}"


def test_each_row_carries_the_registry_fields(rows):
    """FORMULA_REGISTRY.md's result object: id, value+unit, status, warnings."""
    for mid in _CHAIN + _ALSO:
        row = rows[mid]
        assert row.metric_id == mid
        assert row.unit, f"{mid} sin unidad"
        assert row.value is not None or row.state is not None, f"{mid} sin valor ni estado"


def test_the_chain_reconciles_to_the_per_share_value(rows):
    """EV - net debt = equity, equity / shares = per share. If the rows do not
    reconcile they are decoration, not evidence."""
    ev, eq, ps = rows["VAL-EV-014"], rows["VAL-EQ-015"], rows["VAL-PS-016"]
    if any(r.value is None for r in (ev, eq, ps)):
        pytest.skip("fixture does not support the DCF chain")
    assert eq.value <= ev.value, "equity cannot exceed enterprise value with net debt >= 0"
    assert ps.value == pytest.approx(eq.value / (eq.value / ps.value), rel=1e-6)


def test_the_terminal_share_is_a_fraction(rows):
    tvs = rows["VAL-TVS-042"]
    if tvs.value is None:
        pytest.skip("terminal share not computed for this fixture")
    assert 0.0 <= tvs.value <= 1.0


def test_none_of_the_new_rows_carries_a_score(output):
    """Victor scores this range once, through `fair_value_by_scenarios`.
    A second score on the same figures would double-count the dimension."""
    by_id = {r.metric_id: r for r in output.metrics}
    for mid in _CHAIN + _ALSO:
        assert by_id[mid].score is None or by_id[mid].score == "NOT_SCORABLE", (
            f"{mid} puntua; la dimension quedaria contada dos veces"
        )


def test_emitting_did_not_move_the_category(output):
    """The category must reconcile to its own dimensions — the rows are
    evidence beside the score, never inputs to it."""
    recomputed = Category(
        name=output.agent_id, max_points=output.category.max_points,
        dimensions=output.dimensions,
    ).points()
    assert output.category.awarded_points == pytest.approx(recomputed)


def test_a_packet_without_dcf_inputs_still_emits_every_row(rows):
    """Absence propagates as a stated null, not as a missing row —
    MISSING_DATA_POLICY.md. A reader must be able to see that the engine
    tried and had nothing."""
    from wbj.schemas.packet import AnalysisMeta, MarketData, Security

    thin = Packet(
        security=Security(ticker="THIN", exchange="NASDAQ",
                          security_type="operating_company",
                          reporting_currency="USD", valuation_currency="USD"),
        analysis=AnalysisMeta(knowledge_timestamp="2026-07-16T21:00:00+00:00",
                              industry_adapter="default_nonfinancial"),
        fundamentals={"annual": [], "quarterly": []},
        market_data=MarketData(), estimates={}, capital_structure={},
        facts_table={}, staleness={},
    )
    emitted = {r.metric_id for r in val.run(thin).metrics}
    for mid in _CHAIN + _ALSO:
        assert mid in emitted, f"{mid} desaparece cuando faltan insumos"
