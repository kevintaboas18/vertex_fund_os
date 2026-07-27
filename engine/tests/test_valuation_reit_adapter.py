"""A REIT reaches the two secondary checks its matrix row names.

`DECISION_RULES.md`: "REIT | NAV, AFFO DCF, cap rates | **P/AFFO, dividend
model** | Standard EPS/FCFF without adapter". The primary three are names with
no entry in any FORMULAS.md and stay unbuilt. The secondary two are reachable:

- the **dividend model** runs on VAL-DDM-024/VAL-HDDM-025, registered formulas
  over dividends REITs tag in XBRL -- tier-1 data, no analyst input at all;
- **P/AFFO** needs AFFO, which is where the definitional problem lives. The
  ratio is unambiguous once AFFO is given; AFFO itself is not. So it is not
  computed: the issuer publishes one under a definition it discloses, and the
  analyst transcribes that with its source, which makes it evidence class R.

The size of what is being avoided: FFO for Realty Income lands between 3,405M
and 3,897M depending on whether real-estate impairments are added back and
which depreciation line counts as real-estate depreciation. 14% across three
defensible readings of one filing -- a spread that would flow into P/AFFO and
into the category score, which makes picking one a scoring decision rather than
an implementation detail.
"""

import json
from pathlib import Path

import pytest

from wbj.schemas.packet import Packet
from wbj.specialists import valuation as val

_FIXTURE = Path(__file__).parent / "fixtures" / "packet" / "NVDA_packet.json"

_DIVIDENDS = {"dividend_per_share": 3.12,
              "dividends_per_share_history": [2.7, 2.8, 2.9, 3.0, 3.05, 3.12],
              "dividend_tag": "CommonStockDividendsPerShareCashPaid",
              "risk_free_rate": 0.042, "beta": 0.85}
_REPORTED = {"affo_reported": 3_650_000_000.0, "ffo_reported": 3_876_000_000.0,
             "ffo_source": "Q4 2025 earnings release p.12, NAREIT 2018 definition"}


def _reit() -> Packet:
    data = json.loads(_FIXTURE.read_text())
    data["analysis"]["industry_adapter"] = "reits"
    return Packet.model_validate(data)


def _rows(out):
    return {m.metric_id: m for m in out.metrics}


def test_a_reit_is_no_longer_a_blank_refusal():
    out = val.run(_reit(), _DIVIDENDS)
    assert "ADAPTER_REIT_SECONDARY_CHECKS_ONLY" in out.mandatory_flags
    assert "ADAPTER_MODELS_NOT_REGISTERED" not in out.mandatory_flags


def test_the_dividend_model_runs_on_tier_one_data_alone():
    """No analyst input: REITs tag dividends in XBRL."""
    rows = _rows(val.run(_reit(), _DIVIDENDS))
    assert rows["VAL-DDM-024"].value is not None or rows["VAL-HDDM-025"].value is not None


def test_only_registered_formula_ids_are_emitted():
    """P/AFFO has no id in any FORMULAS.md, so it is disclosed rather than
    given a row with an invented one."""
    rows = _rows(val.run(_reit(), {**_DIVIDENDS, **_REPORTED}))
    for metric_id in rows:
        assert metric_id.startswith("VAL-")
    assert not any("AFFO" in metric_id for metric_id in rows)


def test_p_affo_is_disclosed_when_a_sourced_figure_is_supplied():
    out = val.run(_reit(), {**_DIVIDENDS, **_REPORTED})
    note = [a for a in out.assumptions if "P/AFFO" in a]
    assert note
    assert "evidence class R" in note[0].lower() or "Evidence class R" in note[0]
    assert _REPORTED["ffo_source"] in note[0]


def test_a_figure_without_its_source_is_ignored():
    """DATA_POLICY.md requires a stable locator, and a non-GAAP number with no
    stated definition is not reported evidence."""
    out = val.run(_reit(), {**_DIVIDENDS, "affo_reported": 3_650_000_000.0})
    # "P/AFFO" also appears in the closing note that lists the matrix row's
    # secondary checks, so this looks for the *computed* ratio specifically.
    assert not any("P/AFFO = " in a for a in out.assumptions)
    assert any("without `ffo_source`" in a for a in out.assumptions)


def test_the_module_never_computes_ffo_itself():
    """The whole point of the transcription route."""
    note = " ".join(val.run(_reit(), {**_DIVIDENDS, **_REPORTED}).assumptions)
    assert "does not compute" in note
    assert "the analyst's to state" in note


def test_the_category_is_still_incomplete():
    """A secondary check does not substitute for a primary model, and NAV,
    AFFO DCF and cap rates remain unregistered."""
    out = val.run(_reit(), {**_DIVIDENDS, **_REPORTED})
    assert out.status == "INCOMPLETE"
    assert out.coverage < 0.70
    assert "CATEGORY_INCOMPLETE_GATE_INELIGIBLE" in out.mandatory_flags


def test_the_unregistered_primaries_are_still_named():
    note = " ".join(val.run(_reit(), {**_DIVIDENDS, **_REPORTED}).assumptions)
    for model in ("NAV", "AFFO DCF", "cap rates"):
        assert model in note


def test_cap_rates_are_distinguished_from_the_ffo_problem():
    """They are unreachable for a different reason -- no filing states one --
    so an analyst should not be sent looking."""
    note = " ".join(val.run(_reit(), {**_DIVIDENDS, **_REPORTED}).assumptions)
    assert "a cap rate is in no filing at all" in note


def test_the_barred_models_are_rejected():
    rejected = val.run(_reit(), _DIVIDENDS).model_selection.rejected
    for barred in ("FCFF_DCF", "STANDARD_EPS", "EV_EBITDA"):
        assert barred in rejected


def test_no_fcff_row_is_emitted():
    rows = _rows(val.run(_reit(), {**_DIVIDENDS, **_REPORTED}))
    for fcff_id in ("VAL-FCFF-005", "VAL-JEVS-033", "VAL-RDCF-027"):
        assert fcff_id not in rows


def test_the_margin_of_safety_runs_off_the_dividend_models():
    """The only permitted values a REIT has here."""
    rows = _rows(val.run(_reit(), _DIVIDENDS))
    assert "VAL-MOS-040" in rows


# --- NAV inputs: loaded, cited, tiered -- and deliberately not combined -----

_NAV = {"noi_reported": 3_000_000_000.0,
        "noi_source": "Q1 2026 supplement p.21, Annualized Pro Forma Cash NOI",
        "cap_rate": 0.062,
        "cap_rate_source": "CBRE US Cap Rate Survey H2 2025, net lease retail",
        "cap_rate_source_tier": 3}


def test_a_cap_rate_without_source_and_tier_is_rejected():
    """The rule is Victor's own, lifted from the TAM: "a `tam` without both
    `tam_source` and a 1-4 `tam_source_tier` is REJECTED"."""
    out = val.run(_reit(), {**_DIVIDENDS, "cap_rate": 0.062})
    note = " ".join(out.assumptions)
    assert "therefore rejected" in note
    assert not any("Cap rate 6.20%" in a for a in out.assumptions)


def test_tier_five_is_rejected_like_an_unattributed_tam():
    """DECISION_RULES.md rates an unattributed source 0 and not scorable."""
    out = val.run(_reit(), {**_DIVIDENDS, **{**_NAV, "cap_rate_source_tier": 5}})
    assert "therefore rejected" in " ".join(out.assumptions)


def test_a_sourced_and_tiered_cap_rate_is_accepted_with_its_tier():
    out = val.run(_reit(), {**_DIVIDENDS, **_NAV})
    note = [a for a in out.assumptions if a.startswith("Cap rate")]
    assert note
    assert "tier 3" in note[0]
    assert _NAV["cap_rate_source"] in note[0]


def test_the_transacted_versus_portfolio_caveat_is_stated():
    """An issuer's published cap rate is on what it transacted. Realty
    Income's dispositions closed at 7.2% with 78 of 97 properties vacant --
    applying that to a portfolio is a judgement, not a reported fact."""
    note = " ".join(val.run(_reit(), {**_DIVIDENDS, **_NAV}).assumptions)
    assert "on what it TRANSACTED" in note
    assert "analyst judgement about comparability" in note


def test_nav_is_not_computed_even_with_every_input_present():
    """No FORMULAS.md registers it, and the arithmetic hides three unmade
    choices. Complete inputs are not a licence to invent the formula."""
    out = val.run(_reit(), {**_DIVIDENDS, **_NAV})
    rows = _rows(out)
    assert not any("NAV" in metric_id for metric_id in rows)
    note = " ".join(out.assumptions)
    assert "NAV is NOT computed" in note
    assert "three unmade choices" in note


def test_the_supplement_is_located_and_its_tier_stated():
    """SOURCE_HIERARCHY.md puts an issuer filing exhibit at tier 2 and an
    investor presentation at tier 4 -- not tier 1."""
    supplement = {"reit_supplement": {
        "accession": "0000726728-26-000028", "filing_date": "2026-05-06",
        "candidates": [{"name": "realtyincomeq12026supple.htm",
                        "match": "supplement-name", "url": "https://sec.gov/x"}]}}
    note = " ".join(val.run(_reit(), {**_DIVIDENDS, **supplement}).assumptions)
    assert "0000726728-26-000028" in note
    assert "realtyincomeq12026supple.htm" in note
    assert "not tier 1" in note
    assert "transcribed from it, not parsed" in note
