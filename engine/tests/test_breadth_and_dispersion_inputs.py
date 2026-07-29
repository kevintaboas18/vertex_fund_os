"""The last three partial dimensions, and the two keys that close them.

Each sat at 75% — three of four slots — missing exactly one metric:

    earnings_and_revenue_revisions              MKT-DISP-013
    operating_leverage_and_market_confirmation  MKT-SECB-023
    sector_breadth_and_volatility_quality       TECH-BREAD-039

`sector_breadth` closes two of them at once: MKT-SECB-023 and TECH-BREAD-039
read the same overlay key from different specialists. Victor requires the
membership be point-in-time — `04_technical_momentum/SCORING.md`: "Point-in-time
sector membership required for breadth" — because today's constituent list
applied to a past date is survivorship bias.

`individual_estimates` closes the third. MKT-DISP-013 is "stdev(analyst
estimates) / abs(consensus mean)" over the *individual* figures, and no
configured provider serves them: FMP publishes low/high/avg, and deriving a
standard deviation from a range assumes a distribution the data never stated.
"""

import json
from pathlib import Path

import pytest

from wbj.entradas import SKELETON_KEYS, VICTOR_DATASET_FIELD
from wbj.schemas.packet import Packet
import wbj.specialists.market as mkt
import wbj.specialists.technical as tech

_FIXTURE = Path(__file__).parent / "fixtures" / "packet" / "NVDA_packet.json"
_BREADTH = {"sector_breadth": {"above_50dma_count": 38, "valid_members": 65}}


def _packet():
    return Packet.model_validate(json.loads(_FIXTURE.read_text()))


def _dim(out, needle):
    d = next(x for x in out.dimensions if needle in x.name)
    aw = d.applicable_weight()
    return (d.valid_weight() / aw) if aw else 0.0


# --- one key, two specialists ----------------------------------------------

def test_sector_breadth_feeds_the_market_metric():
    rows = {r.metric_id: r for r in mkt.run(_packet(), _BREADTH).metrics}
    assert rows["MKT-SECB-023"].value == pytest.approx(38 / 65)


def test_sector_breadth_feeds_the_technical_metric():
    rows = {r.metric_id: r for r in tech.run(_packet(), _BREADTH).metrics}
    assert rows["TECH-BREAD-039"].value is not None


def test_both_spellings_of_the_count_are_accepted():
    """market.py reads `above_50dma_count`, technical.py `above_50dma`. One
    key, two spellings: an analyst filling one must not satisfy one
    specialist's metric while silently leaving the other's MISSING."""
    for spelling in ("above_50dma_count", "above_50dma"):
        overlay = {"sector_breadth": {spelling: 38, "valid_members": 65}}
        m = {r.metric_id: r for r in mkt.run(_packet(), overlay).metrics}
        t = {r.metric_id: r for r in tech.run(_packet(), overlay).metrics}
        assert m["MKT-SECB-023"].value is not None, spelling
        assert t["TECH-BREAD-039"].value is not None, spelling


def test_a_count_without_the_member_total_is_refused():
    """A numerator with no denominator is not a percentage. Guessing the
    member count would invent the very figure Victor wants controlled."""
    overlay = {"sector_breadth": {"above_50dma_count": 38}}
    rows = {r.metric_id: r for r in mkt.run(_packet(), overlay).metrics}
    assert rows["MKT-SECB-023"].value is None


def test_both_dimensions_close_on_that_one_key():
    m_before, m_after = mkt.run(_packet()), mkt.run(_packet(), _BREADTH)
    t_before, t_after = tech.run(_packet()), tech.run(_packet(), _BREADTH)
    assert _dim(m_after, "operating") > _dim(m_before, "operating")
    assert _dim(t_after, "breadth") > _dim(t_before, "breadth")


# --- dispersion -------------------------------------------------------------

def test_individual_estimates_score_the_dispersion():
    overlay = {"individual_estimates": [4.10, 4.25, 4.40, 4.55, 4.30, 4.35]}
    rows = {r.metric_id: r for r in mkt.run(_packet(), overlay).metrics}
    assert rows["MKT-DISP-013"].value is not None
    assert rows["MKT-DISP-013"].value > 0


def test_dispersion_is_relative_to_the_mean():
    """"stdev / abs(consensus mean)" — the same spread around a larger mean is
    a smaller dispersion. A plain stdev would call a $400 stock and a $4 stock
    equally uncertain."""
    tight = {"individual_estimates": [40.0, 41.0, 42.0]}
    wide = {"individual_estimates": [4.0, 4.1, 4.2]}
    a = {r.metric_id: r for r in mkt.run(_packet(), tight).metrics}["MKT-DISP-013"]
    b = {r.metric_id: r for r in mkt.run(_packet(), wide).metrics}["MKT-DISP-013"]
    assert a.value < b.value


def test_absence_names_the_remedy_and_says_why_no_provider_has_it():
    rows = {r.metric_id: r for r in mkt.run(_packet()).metrics}
    warned = " ".join(rows["MKT-DISP-013"].warnings or [])
    assert "individual_estimates" in warned
    assert "low/high/avg" in warned


# --- documented -------------------------------------------------------------

def test_both_keys_are_documented_with_their_dataset_field():
    for key in ("sector_breadth", "individual_estimates"):
        assert key in SKELETON_KEYS
        assert key in VICTOR_DATASET_FIELD
