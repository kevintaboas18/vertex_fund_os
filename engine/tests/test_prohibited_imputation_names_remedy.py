"""A PROHIBITED_IMPUTATION null must say what to do about it.

`MISSING_DATA_POLICY.md` bars inferring customer concentration and market
share, so the engine correctly refuses to invent them. But the null carried
only a bare token -- `CUSTOMER_CONCENTRATION_UNAVAILABLE_PROHIBITED_IMPUTATION`
-- which tells a reader the number is absent and nothing else: not that
`Entradas/<TICKER>.json` is where a disclosed figure goes, and not that
"absent" has two different causes. An issuer that discloses a >=10%-of-revenue
customer is an unread gap; one that discloses none (Apple's 2025 10-K states a
12% share of *trade receivables* and no revenue concentration at all) has
nothing to supply, and MISSING is its correct final state.

The refusal is right. Leaving the reader unable to tell those apart, or to act
on the first, is what these tests pin down.

Risk's largest-customer-share is deliberately scored without a row (its anchor
is on the share, the registered row is the HHI), so its message is carried on
the scored `Value` and is not assertable here.
"""

import json
from pathlib import Path

import pytest

from wbj.schemas.packet import Packet
from wbj.specialists import business as biz
from wbj.specialists import market as mkt

_FIXTURE = Path(__file__).parent / "fixtures" / "packet" / "NVDA_packet.json"


@pytest.fixture(scope="module")
def packet() -> Packet:
    return Packet.model_validate(json.loads(_FIXTURE.read_text()))


def _warning_for(module, metric_id, packet, overlay):
    rows = {m.metric_id: m for m in module.run(packet, overlay).metrics}
    assert metric_id in rows, f"{metric_id} not emitted"
    return " ".join(rows[metric_id].warnings)


def test_concentration_null_names_the_file_and_the_key(packet):
    w = _warning_for(biz, "BUS-CONC-003", packet, {})
    assert "PROHIBITED_IMPUTATION" in w, "the refusal itself must stay legible"
    assert "Entradas/<TICKER>.json" in w
    assert "largest_customer_share" in w


def test_concentration_null_says_a_non_disclosing_filing_is_correct(packet):
    """Otherwise a permanently-correct MISSING reads as an unfinished task."""
    w = _warning_for(biz, "BUS-CONC-003", packet, {})
    assert "MISSING is the correct final state" in w


def test_market_share_null_names_both_halves_of_the_ratio(packet):
    """Market share is never in the filing: it needs a sized market too."""
    w = _warning_for(mkt, "MKT-SHARE-006", packet, {})
    assert "Entradas/<TICKER>.json" in w
    assert "total_market_sales" in w


def test_a_supplied_figure_still_scores(packet):
    """The remedy has to actually work, not just be described."""
    rows = {m.metric_id: m for m in
            biz.run(packet, {"largest_customer_share": 0.22}).metrics}
    assert rows["BUS-CONC-003"].value == pytest.approx(0.22)
    assert rows["BUS-CONC-003"].state is None


def test_the_refusal_is_unchanged_when_no_figure_is_supplied(packet):
    """Naming the remedy must not become a licence to guess."""
    rows = {m.metric_id: m for m in biz.run(packet, {}).metrics}
    assert rows["BUS-CONC-003"].value is None
    assert rows["BUS-CONC-003"].score == "NOT_SCORABLE"
