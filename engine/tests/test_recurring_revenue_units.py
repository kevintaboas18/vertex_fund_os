"""A share and an amount are not the same input.

`extract/filing.py` pulls `recurring_revenue_share` — its own field
description says "Recurring/subscription share of total revenue, 0-1 decimal"
— and used to emit it under the key `recurring_revenue`. BUS-REC-002's
consumer divides what it receives by total revenue, so a 0.45 share arriving
under that name became `0.45 / 400e9`: a company that is 45% recurring
reported as 0% recurring.

`DATA_POLICY.md` forbids exactly this: "Do not silently change sign
conventions, currency, tax treatment, or denominator definitions."

The defect was latent — it only fires when filing extraction runs, which was
blocked on API credits — so no score ever showed it. Both shapes are now
accepted under names that say which they are.
"""

import json
from pathlib import Path

import pytest

from wbj.schemas.packet import Packet
import wbj.specialists.business as bus

_FIXTURE = Path(__file__).parent / "fixtures" / "packet" / "NVDA_packet.json"


def _run(overlay):
    packet = Packet.model_validate(json.loads(_FIXTURE.read_text()))
    rows = {r.metric_id: r for r in bus.run(packet, overlay).metrics}
    return rows["BUS-REC-002"]


def _revenue():
    packet = Packet.model_validate(json.loads(_FIXTURE.read_text()))
    return float((packet.fundamentals or {}).get("annual", [{}])[0].get("revenue"))


def test_a_share_is_used_as_the_percentage_directly():
    """The extractor's shape. 0.45 means 45%, not 45 dollars."""
    assert _run({"recurring_revenue_share": 0.45}).value == pytest.approx(0.45)


def test_an_amount_is_still_divided_by_revenue():
    """The analyst's shape, unchanged: an absolute figure over total revenue."""
    rev = _revenue()
    got = _run({"recurring_revenue": rev * 0.30}).value
    assert got == pytest.approx(0.30, rel=1e-6)


def test_the_two_shapes_agree_on_the_same_company():
    """The whole point: whichever way the figure arrives, the answer matches."""
    rev = _revenue()
    assert _run({"recurring_revenue_share": 0.45}).value == pytest.approx(
        _run({"recurring_revenue": rev * 0.45}).value, rel=1e-6)


def test_the_share_wins_when_both_are_supplied():
    """The named share is unambiguous; the bare amount is the shape that was
    being misread. Prefer the one that says what it is."""
    assert _run({"recurring_revenue_share": 0.45,
                 "recurring_revenue": 1.0}).value == pytest.approx(0.45)


def test_a_share_outside_zero_to_one_is_refused_not_scored():
    """45 (meaning 45%) is not a 0-1 share. Accepting it would report 4500%."""
    row = _run({"recurring_revenue_share": 45.0})
    assert row.value is None
    assert "OUT_OF_RANGE" in " ".join(row.warnings or [])


def test_absence_still_propagates():
    row = _run({})
    assert row.value is None
    assert "RECURRING_REVENUE_UNAVAILABLE" in " ".join(row.warnings or [])


def test_the_extractor_emits_under_the_name_that_says_share():
    """The producer half of the fix, so the two cannot drift apart again."""
    source = (Path(__file__).parent.parent / "wbj" / "extract" / "filing.py"
              ).read_text(encoding="utf-8")
    assert 'out["recurring_revenue_share"]' in source
    assert 'out["recurring_revenue"]' not in source
