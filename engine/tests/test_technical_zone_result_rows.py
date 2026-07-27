"""The zone pipeline emits result rows, not just levels.

`shared/FORMULA_REGISTRY.md`: "Every formula must have: a stable formula ID;
formula text and sign convention; required inputs and units; ... formula
version; at least one validation test", plus a formula-result object carrying
`formula_id`, `result: {value, unit}`, `status` and `warnings`.

Twelve technical formulas — the pivot/zigzag/tolerance/zone/touch/rejection
chain, the break statuses, the anchored VWAP and the volume profile — were
computed inside `levels_engine` on the way to `important_levels` and emitted
no row of their own. `00_main_agent/AGENT.md` asks for "a traceable final
report, not a black-box opinion", and `04_technical_momentum/AGENT.md` forbids
"subjective support/resistance lines" in favour of "the registered pivot,
ATR-zone, touch, and breakout algorithms" — registered means it reports.

They carry no score. SCORING.md's breakout dimension names the `PIV-022..033`
range and is already scored through TECH-LSTR-028 (zone strength) and
TECH-BCONF-031 (breakout confirmation); these are the evidence behind those
two, so scoring them again would count one dimension twice.
"""

import json
from pathlib import Path

import pytest

from wbj.core.scoring import Category
from wbj.schemas.packet import Packet
import wbj.specialists.technical as tech

_FIXTURE = Path(__file__).parent / "fixtures" / "packet" / "NVDA_packet.json"

_ZONE_CHAIN = ["TECH-PIV-022", "TECH-ZIG-023", "TECH-ZTOL-024", "TECH-ZONE-025",
               "TECH-NEFF-026", "TECH-REJ-027", "TECH-DATR-029"]
_BREAK_STATUS = ["TECH-BRK-030", "TECH-FBRK-032", "TECH-ROLE-033"]
_PRICE_LEVELS = ["TECH-AVWAP-034", "TECH-VP-035"]
_ALL = _ZONE_CHAIN + _BREAK_STATUS + _PRICE_LEVELS


@pytest.fixture(scope="module")
def output():
    return tech.run(Packet.model_validate(json.loads(_FIXTURE.read_text())))


@pytest.fixture(scope="module")
def rows(output):
    return {r.metric_id: r for r in output.metrics}


def test_every_row_is_emitted(rows):
    missing = [m for m in _ALL if m not in rows]
    assert not missing, f"sin fila de resultado: {missing}"


def test_each_row_carries_a_unit_and_a_state_or_value(rows):
    for mid in _ALL:
        row = rows[mid]
        assert row.unit, f"{mid} sin unidad"
        assert row.value is not None or row.state is not None


def test_counts_are_non_negative_whole_numbers(rows):
    """Pivots, swings, zones and touches are counts. A fractional or negative
    count would mean the row is reporting something other than what it says."""
    for mid in ("TECH-PIV-022", "TECH-ZIG-023", "TECH-ZONE-025", "TECH-NEFF-026"):
        v = rows[mid].value
        if v is None:
            continue
        assert v >= 0 and float(v).is_integer(), f"{mid} = {v} no es un conteo"


def test_the_break_statuses_are_mutually_exclusive(rows):
    """A zone is broken, failed, or role-reversed — never two at once. They
    read off one `status` field, so more than one true means the mapping
    drifted."""
    flags = [rows[m].value for m in _BREAK_STATUS if rows[m].value is not None]
    assert sum(flags) <= 1, "mas de un estado de ruptura activo a la vez"


def test_the_break_statuses_are_booleans(rows):
    for mid in _BREAK_STATUS:
        v = rows[mid].value
        assert v is None or v in (0.0, 1.0), f"{mid} = {v} no es booleano"


def test_price_levels_are_positive(rows):
    """The anchored VWAP and the volume-profile point of control are prices.
    A non-positive one would be a unit or wiring error, not a market fact."""
    for mid in _PRICE_LEVELS:
        v = rows[mid].value
        assert v is None or v > 0, f"{mid} = {v} no es un precio"


def test_the_zone_width_is_positive_when_present(rows):
    v = rows["TECH-ZTOL-024"].value
    assert v is None or v > 0, "una zona no puede tener ancho <= 0"


def test_none_of_these_rows_carries_a_score(output):
    """Scored once already, through LSTR-028 and BCONF-031."""
    by_id = {r.metric_id: r for r in output.metrics}
    for mid in _ALL:
        assert by_id[mid].score is None or by_id[mid].score == "NOT_SCORABLE", (
            f"{mid} puntua; la dimension de ruptura quedaria contada dos veces"
        )


def test_emitting_did_not_move_the_category(output):
    recomputed = Category(
        name=output.agent_id, max_points=output.category.max_points,
        dimensions=output.dimensions,
    ).points()
    assert output.category.awarded_points == pytest.approx(recomputed)


def test_no_price_history_stops_the_whole_specialist_and_says_so():
    """The rows only exist when the specialist runs. With no price history it
    does not run at all -- it emits nothing and raises
    INSUFFICIENT_PRICE_HISTORY, which is the honest answer and predates this
    change. What must never happen is a silent partial run: either every row
    is there, or the flag explains why none is.
    """
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
    out = tech.run(thin)
    assert out.metrics == []
    assert "INSUFFICIENT_PRICE_HISTORY" in out.mandatory_flags


def test_when_the_specialist_runs_every_row_is_present(rows):
    """The other half of the contract, on the real fixture: a run that
    produces any metric produces all twelve of these."""
    assert all(mid in rows for mid in _ALL)
