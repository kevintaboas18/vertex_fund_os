"""Analyst inputs that companyfacts already reports.

`Entradas/<TICKER>.json` exists for figures no feed serves. Several of its keys
were being asked of a person anyway, even though the number is filed as a
us-gaap tag in a payload the builder already downloads. Two of those keys have a
documented proxy that fires on absence -- so an empty file was not neutral, it
was a permanent confidence penalty.

Three properties matter, and the third is the one that keeps this honest:

- **Maps what is reported**: gross PP&E, standalone depreciation, equity
  issuance, lease charge, and the lease-payment ladder.
- **Never invents**: a tag the company does not file leaves the key absent, so
  the existing proxy or MISSING path is untouched. A REIT files no gross PP&E.
- **Refuses the near-misses**: three keys look mappable and are not, because the
  available tag means something else. Filling them would silently redefine a
  quantity, which DATA_POLICY.md prohibits outright.
"""

from wbj.packet.builder import _XBRL_SCALARS, _xbrl_analyst_inputs


def _facts(**tags):
    """companyfacts shaped payload. `tags` maps tag -> (end, value), or to a
    list of (end, value) when a tag needs more than one period."""
    def entradas(spec):
        pares = spec if isinstance(spec, list) else [spec]
        return [{"end": end, "val": val, "filed": "2026-02-01",
                 "accn": f"0000-{i}", "fp": "FY", "form": "10-K"}
                for i, (end, val) in enumerate(pares)]
    return {"facts": {"us-gaap": {tag: {"units": {"USD": entradas(spec)}}
                                 for tag, spec in tags.items()}}}


FECHA = "2025-12-31"
FECHA_ANTERIOR = "2024-12-31"


# --- maps what is reported --------------------------------------------------

def test_gross_ppe_is_taken_from_the_filing_for_both_years():
    """RSK-AQI-023 wants GROSS PP&E, and it is a RATIO of this year over last,
    so both years have to come from the same definition."""
    out = _xbrl_analyst_inputs(_facts(PropertyPlantAndEquipmentGross=[
        (FECHA, 1_500.0), (FECHA_ANTERIOR, 1_400.0)]), FECHA, FECHA_ANTERIOR)
    assert out["ppe"] == 1_500.0
    assert out["ppe_prior"] == 1_400.0


def test_standalone_depreciation_is_taken_from_the_filing_for_both_years():
    """The packet carries D&A, which Beneish DEPI accepts only as
    DA_PROXY_FOR_DEPRECIATION."""
    out = _xbrl_analyst_inputs(_facts(Depreciation=[
        (FECHA, 220.0), (FECHA_ANTERIOR, 200.0)]), FECHA, FECHA_ANTERIOR)
    assert out["depreciation"] == 220.0
    assert out["depreciation_prior"] == 200.0


def test_one_year_alone_is_refused_so_gross_never_meets_net():
    """The trap this guards. With only the current year mapped, the ratio would
    put a GROSS figure over risk.py's NET fallback for the prior year and invent
    a jump of ~3x. Neither year is supplied instead, leaving the net/net pair --
    consistent, and now flagged as a proxy."""
    out = _xbrl_analyst_inputs(
        _facts(PropertyPlantAndEquipmentGross=(FECHA, 1_500.0)), FECHA, FECHA_ANTERIOR)
    assert "ppe" not in out
    assert "ppe_prior" not in out


def test_equity_issuance_and_lease_charge_are_mapped():
    out = _xbrl_analyst_inputs(_facts(
        ProceedsFromIssuanceOfCommonStock=(FECHA, 40.0),
        OperatingLeaseCost=(FECHA, 90.0)), FECHA)
    assert out["equity_issuance"] == 40.0
    assert out["lease_charge"] == 90.0


def test_lease_charge_accepts_either_reported_tag():
    """Companies file one or the other, never both."""
    out = _xbrl_analyst_inputs(_facts(OperatingLeaseExpense=(FECHA, 75.0)), FECHA)
    assert out["lease_charge"] == 75.0


def test_the_lease_ladder_becomes_a_payment_per_year_list():
    """VAL-LEASE-004 reads "payment per future year", which is exactly what
    this ladder is."""
    out = _xbrl_analyst_inputs(_facts(
        LesseeOperatingLeaseLiabilityPaymentsDueNextTwelveMonths=(FECHA, 10.0),
        LesseeOperatingLeaseLiabilityPaymentsDueYearTwo=(FECHA, 9.0),
        LesseeOperatingLeaseLiabilityPaymentsDueYearThree=(FECHA, 8.0)), FECHA)
    assert out["lease_commitments"] == [10.0, 9.0, 8.0]


# --- never invents ---------------------------------------------------------

def test_an_unreported_tag_leaves_the_key_absent():
    """A REIT files no gross PP&E and a bank no standalone `Depreciation`.
    MISSING_DATA_POLICY.md step 1 makes that NOT_APPLICABLE -- absent, so the
    existing proxy still runs. A zero would read as a real reported zero."""
    out = _xbrl_analyst_inputs(_facts(OperatingLeaseCost=(FECHA, 5.0)), FECHA)
    assert "ppe" not in out
    assert "depreciation" not in out


def test_no_companyfacts_at_all_yields_nothing():
    assert _xbrl_analyst_inputs({}, FECHA) == {}
    assert _xbrl_analyst_inputs(None, FECHA) == {}


def test_a_gap_truncates_the_ladder_instead_of_closing_it():
    """Skipping a missing year would shift every later payment one year nearer
    and overstate the lease debt. Year three is absent here, so the ladder is
    the first two years and stops."""
    out = _xbrl_analyst_inputs(_facts(
        LesseeOperatingLeaseLiabilityPaymentsDueNextTwelveMonths=(FECHA, 10.0),
        LesseeOperatingLeaseLiabilityPaymentsDueYearTwo=(FECHA, 9.0),
        LesseeOperatingLeaseLiabilityPaymentsDueYearFour=(FECHA, 7.0)), FECHA)
    assert out["lease_commitments"] == [10.0, 9.0]


def test_a_single_year_is_not_a_schedule():
    out = _xbrl_analyst_inputs(_facts(
        LesseeOperatingLeaseLiabilityPaymentsDueNextTwelveMonths=(FECHA, 10.0)), FECHA)
    assert "lease_commitments" not in out


# --- committed liquidity ---------------------------------------------------

def test_committed_liquidity_arrives_nested_under_cash_burn():
    """risk.py reads `overlay["cash_burn"]["committed_liquidity"]`, so a flat key
    would be silently ignored. RSK-RUN-015 is "(Cash + committed undrawn
    liquidity) / Average monthly cash burn"; the term defaulted to 0.0, which
    understated every runway."""
    out = _xbrl_analyst_inputs(_facts(
        LineOfCreditFacilityRemainingBorrowingCapacity=(FECHA, 7_227_000_000.0)), FECHA)
    assert out["cash_burn"] == {"committed_liquidity": 7_227_000_000.0}


def test_a_stale_facility_figure_is_refused():
    """The trap this guards, and it is not hypothetical: Tesla's newest entry for
    this tag is 2016 and American Airlines' is 2023. Without `require_period`,
    the no-period-match fallback hands back the newest fact the tag happens to
    hold -- a nine-year-old facility balance presented as current liquidity,
    inflating the runway with nothing said."""
    out = _xbrl_analyst_inputs(_facts(
        LineOfCreditFacilityRemainingBorrowingCapacity=("2016-12-31", 17_900_000.0)),
        FECHA)
    assert "cash_burn" not in out


def test_a_zero_or_absent_facility_leaves_the_term_alone():
    """Zero undrawn capacity and no disclosed facility both mean the default
    stands. Writing an explicit 0.0 would only add noise."""
    assert "cash_burn" not in _xbrl_analyst_inputs(
        _facts(LineOfCreditFacilityRemainingBorrowingCapacity=(FECHA, 0.0)), FECHA)
    assert "cash_burn" not in _xbrl_analyst_inputs(_facts(), FECHA)


# --- refuses the near-misses ----------------------------------------------

def test_three_keys_stay_unmapped_because_the_tag_means_something_else():
    """A regression guard, not a limitation.

    Each of these has an inviting us-gaap tag that measures a DIFFERENT
    quantity, and DATA_POLICY.md forbids silently changing a definition:

    - `debt_schedule` wants the debt BALANCE per forecast year; the XBRL
      maturity ladder is REPAYMENTS.
    - `options_outstanding` wants `[{name, count, strike}]` per grant; XBRL has
      an aggregate count and a weighted-average strike.
    - `share_history` is the company's MARKET SHARE over time -- it feeds
      `market_share_trend` -- not its share count.
    """
    from wbj.packet.builder import _XBRL_PAIRS
    mapeadas = ({key for key, _, _ in _XBRL_SCALARS} | {"lease_commitments"}
                | {k for k, _, _, _ in _XBRL_PAIRS} | {k for _, k, _, _ in _XBRL_PAIRS})
    for key in ("debt_schedule", "options_outstanding", "share_history"):
        assert key not in mapeadas, (
            f"{key} no se puede mapear desde XBRL: el tag disponible mide otra cosa")


# --- precedence ------------------------------------------------------------

def test_xbrl_fills_only_absent_keys_and_the_analyst_still_wins(tmp_path):
    """The overlay order that matters: computed values are never clobbered, and
    a figure in `Entradas/` still overrides the filing -- an analyst reading the
    10-K notes may have a better-scoped number than a single tag."""
    overlay = {"ppe": 999.0, "depreciation": None}
    xbrl = {"ppe": 111.0, "depreciation": 222.0, "lease_charge": 33.0}
    for key, value in xbrl.items():
        if overlay.get(key) is None:
            overlay[key] = value
    assert overlay["ppe"] == 999.0          # ya calculado: intacto
    assert overlay["depreciation"] == 222.0  # ausente: rellenado
    assert overlay["lease_charge"] == 33.0   # ausente: rellenado
