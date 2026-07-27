"""Which of Cerebro's three sources each judgment slot draws on.

`01_business_analysis/AGENT.md` and `03_market_analysis/AGENT.md`, verbatim:
"a claim that cannot be tied to a formula result, reported evidence, or
explicitly disclosed assumption is context only and receives no score."

That is the rule that decides whether to write an answer or pay for one, and
it was only ever applied by a human reading a table. It is now computed from
the same citations, so the split cannot drift from the methodology.
"""

import json
from pathlib import Path

import pytest

from wbj.deep import JUDGMENT_SOURCE, judgment_source, pending_judgments
from wbj.schemas.packet import Packet
from wbj.specialists import business as biz
from wbj.specialists import market as mkt
from wbj.specialists import risk as rsk

_FIXTURE = Path(__file__).parent / "fixtures" / "packet" / "NVDA_packet.json"


@pytest.fixture(scope="module")
def packet() -> Packet:
    return Packet.model_validate(json.loads(_FIXTURE.read_text()))


@pytest.mark.parametrize("metric_id", [
    "moat_classification", "three_thesis_killers",
    "three_growth_thesis_killers", "thesis_killers",
])
def test_the_assumption_slots_belong_to_the_analyst(metric_id):
    """Nobody can be accountable for an assumption they did not make."""
    kind, _ = judgment_source(metric_id)
    assert kind == "assumption"


@pytest.mark.parametrize("metric_id", [
    "moat_quantitative_effects_count", "business_in_one_sentence",
    "regulatory_legal_exposure", "FIN-GR-004",
])
def test_the_filing_slots_belong_to_the_judge(metric_id):
    kind, _ = judgment_source(metric_id)
    assert kind == "filing"


def test_catalyst_slots_are_matched_by_prefix():
    """They are numbered per catalyst, so the id is not fixed."""
    for i in (0, 3, 17):
        kind, why = judgment_source(f"catalyst_{i}_probability_impact_evidence")
        assert kind == "assumption"
        assert "model assumptions and must be labeled" in why


def test_a_share_series_is_neither_judgment_nor_assumption():
    """FIN-GR-005 needs three years on one market definition. Not in a filing,
    and not something to assume -- sending it to either channel wastes the
    request."""
    kind, why = judgment_source("FIN-GR-005")
    assert kind == "external"
    assert "supply it as data" in why


def test_every_classification_cites_the_line_that_assigns_it():
    """A routing rule with no citation is a preference."""
    for metric_id, (kind, why) in JUDGMENT_SOURCE.items():
        assert kind in ("assumption", "filing", "external"), metric_id
        assert why.strip(), metric_id


def test_an_unknown_slot_defaults_to_the_judge():
    """The judge cites what it reads; guessing an assumption on the analyst's
    behalf is the failure to avoid."""
    kind, _ = judgment_source("some_new_metric")
    assert kind == "filing"


def test_pending_splits_the_open_slots(packet):
    groups = pending_judgments([biz.run(packet, {}), rsk.run(packet, {})])
    assumption = {r["metric_id"] for r in groups["assumption"]}
    assert "moat_classification" in assumption
    assert "thesis_killers" in assumption
    assert "business_in_one_sentence" in {r["metric_id"] for r in groups["filing"]}


def test_an_answered_slot_drops_out(packet):
    """The list shrinks as Entradas/ fills, so it always shows what is left."""
    outputs = [biz.run(packet, {})]
    before = {r["metric_id"] for r in pending_judgments(outputs)["assumption"]}
    after = {r["metric_id"] for r in pending_judgments(
        outputs, {"judgments": {"moat_classification": "Wide"}})["assumption"]}
    assert "moat_classification" in before
    assert "moat_classification" not in after


def test_catalyst_requests_route_as_assumptions(packet):
    overlay = {"catalysts": [{"event": "launch", "months_to_event": 6}] * 3}
    groups = pending_judgments([mkt.run(packet, overlay)], overlay)
    assert any(r["metric_id"].startswith("catalyst_") for r in groups["assumption"])
