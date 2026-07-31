"""BUS-CAGR-006 and BUS-DIL-028 over the window FORMULAS.md registers.

The registry gives both a frequency of "3y and 5y", not the full packet
history. `revenues[0]` and `valid_diluted[0]` measured the whole span,
which became a 10-year CAGR once the packet carried eleven years, and the
period label claimed five years while the computation used ten.
"""

from datetime import datetime, timezone

import pytest

from wbj.schemas.packet import AnalysisMeta, Packet, Security
from wbj.specialists import business as bus


def _row(y, *, revenue=1000.0, diluted=100.0):
    return dict(fiscalYear=str(y), filingDate=f"{y + 1}-02-20", revenue=revenue,
                gross_profit=revenue * 0.6, ebit=revenue * 0.2, net_income=150.0,
                operating_cash_flow=250.0, capex=-40.0, fcf=210.0,
                total_debt=150.0, total_equity=600.0, cash=200.0,
                total_assets=1000.0, total_liabilities=400.0,
                income_before_tax=190.0, income_tax_expense=40.0,
                diluted_shares=diluted, stock_based_compensation=10.0,
                changeInWorkingCapital=-15.0)


def _packet(rows_newest_first):
    return Packet(
        security=Security(ticker="T", exchange="X", security_type="operating_company",
                          reporting_currency="USD", valuation_currency="USD",
                          sector="Technology", industry="Semiconductors"),
        analysis=AnalysisMeta(
            knowledge_timestamp=datetime.now(timezone.utc).isoformat(),
            industry_adapter="default_nonfinancial"),
        fundamentals={"annual": rows_newest_first}, facts_table={},
        market_data={}, estimates={}, capital_structure={}, staleness={})


def _metric(out, mid):
    return next(m for m in out.metrics if m.metric_id == mid)


# --- the window ------------------------------------------------------------


def test_the_window_is_the_registered_five_years():
    assert bus.CAGR_WINDOW_YEARS == 5


def test_the_helper_spans_five_years_over_six_points():
    """A five-year CAGR compares a year to the one five years before it,
    so the window is six annual points and n is five."""
    annual = [{"fiscalYear": str(y)} for y in range(2015, 2026)]   # ascending
    values = list(range(11))
    begin, end, years, period = bus._cagr_over_window(annual, values, 5)
    assert years == pytest.approx(5.0)
    assert begin == 5 and end == 10          # last six points
    assert period == "FY2020-FY2025"


def test_a_deeper_packet_does_not_change_the_cagr():
    """The regression: the same recent trajectory must give the same CAGR
    whether the packet holds six years or eleven."""
    recent = [_row(2025, revenue=2000.0), _row(2024, revenue=1800.0),
              _row(2023, revenue=1600.0), _row(2022, revenue=1400.0),
              _row(2021, revenue=1200.0), _row(2020, revenue=1000.0)]
    padded = recent + [_row(y, revenue=500.0) for y in range(2019, 2014, -1)]
    assert _metric(bus.run(_packet(recent), {"wacc": 0.09}), "BUS-CAGR-006").value == \
        pytest.approx(_metric(bus.run(_packet(padded), {"wacc": 0.09}), "BUS-CAGR-006").value)


def test_the_period_matches_the_window_used():
    """The other half of the defect: the label claimed five years while
    the computation used ten. The period is now read from the window
    itself, so it cannot disagree."""
    rows = [_row(y, revenue=1000.0 + (y - 2015) * 100) for y in range(2025, 2014, -1)]
    m = _metric(bus.run(_packet(rows), {"wacc": 0.09}), "BUS-CAGR-006")
    assert m.period == "FY2020-FY2025"       # a six-point, five-year span


def test_dilution_uses_the_same_window():
    recent = [_row(2025, diluted=130.0), _row(2024, diluted=124.0),
              _row(2023, diluted=118.0), _row(2022, diluted=112.0),
              _row(2021, diluted=106.0), _row(2020, diluted=100.0)]
    padded = recent + [_row(y, diluted=100.0) for y in range(2019, 2014, -1)]
    assert _metric(bus.run(_packet(recent), {"wacc": 0.09}), "BUS-DIL-028").value == \
        pytest.approx(_metric(bus.run(_packet(padded), {"wacc": 0.09}), "BUS-DIL-028").value)


def test_a_short_history_uses_what_it_has():
    two = [_row(2025, revenue=1200.0), _row(2024, revenue=1000.0)]
    assert _metric(bus.run(_packet(two), {"wacc": 0.09}), "BUS-CAGR-006").value is not None


# --- both registered windows are reported ---------------------------------


def _accelerating():
    """Revenue that curves, so a 3-year CAGR and a 5-year CAGR differ.
    Newest first, as the packet carries them."""
    rev = {2020: 1000.0, 2021: 1050.0, 2022: 1100.0,
           2023: 1300.0, 2024: 1600.0, 2025: 2000.0}
    return _packet([_row(y, revenue=rev[y]) for y in range(2025, 2019, -1)])


def test_revenue_reports_both_the_three_and_five_year_windows():
    """FORMULAS.md registers BUS-CAGR-006 at "3y and 5y" — both. The 5y
    scores; the 3y must not be dropped, so both reach the schema."""
    out = bus.run(_accelerating(), {"wacc": 0.09})
    cp = out.competitive_position
    assert cp["revenue_cagr"] is not None and cp["revenue_cagr_3y"] is not None
    # The curve makes the recent three years grow faster than the five.
    assert cp["revenue_cagr_3y"] > cp["revenue_cagr"]


def test_the_scored_revenue_metric_is_the_five_year_window():
    """The value the anchor scores stays the 5y — reporting the 3y must
    not move the score."""
    out = bus.run(_accelerating(), {"wacc": 0.09})
    assert _metric(out, "BUS-CAGR-006").value == pytest.approx(
        out.competitive_position["revenue_cagr"])


def test_the_three_year_revenue_window_spans_three_years():
    """(2000/1100) over three years, distinct from the packet's full span."""
    out = bus.run(_accelerating(), {"wacc": 0.09})
    assert out.competitive_position["revenue_cagr_3y"] == pytest.approx(
        (2000.0 / 1100.0) ** (1 / 3) - 1)


def test_dilution_reports_both_windows_too():
    """BUS-DIL-028 is registered at "3y / 5y"; both reach capital_allocation,
    and the 3y matches the flag's own three-year read."""
    rows = [_row(2025, diluted=130.0), _row(2024, diluted=124.0),
            _row(2023, diluted=118.0), _row(2022, diluted=112.0),
            _row(2021, diluted=106.0), _row(2020, diluted=100.0)]
    out = bus.run(_packet(rows), {"wacc": 0.09})
    ca = out.capital_allocation
    assert ca["diluted_share_cagr"] is not None
    assert ca["diluted_share_cagr_3y"] == pytest.approx(
        (130.0 / 112.0) ** (1 / 3) - 1)


# --- DILUTION_RED_FLAG: the three conditions -------------------------------


def _dilutive(cagr_over_three_years=0.08):
    """Six rows whose last four dilute at about `cagr`, older ones flat."""
    base = 100.0
    s = [base * (1 + cagr_over_three_years) ** k for k in range(4)]  # three-year run
    rows = [_row(2022 + k, diluted=s[k]) for k in range(4)]          # oldest..newest
    rows = list(reversed(rows))                                      # newest first
    rows += [_row(y, diluted=base) for y in (2021, 2020)]
    return _packet(rows)


def _shares(*ascending):
    """A packet whose diluted-share counts follow `ascending`, oldest first."""
    return _packet(list(reversed([_row(2020 + k, diluted=s)
                                  for k, s in enumerate(ascending)])))


def test_the_flag_uses_the_three_year_window_he_names():
    """DECISION_RULES.md: 'diluted shares grow >5% CAGR for three years'.
    The flag reads its own three-year CAGR, separate from the metric's
    registered 3y/5y — so the two windows have to be able to disagree.

    Both packets below span six years. The one that dilutes 8%/yr over the
    last three years averages only 4.7%/yr over five, and the one that
    diluted hard early and has been flat since averages 7.0%/yr over five
    but 0% over three. A flag reading the 5y window would get both
    backwards.
    """
    recent = _shares(100.0, 100.0, 100.0, 108.0, 116.64, 125.97)
    assert "DILUTION_RED_FLAG" in bus.run(recent, {"wacc": 0.09}).mandatory_flags

    old = _shares(100.0, 120.0, 140.0, 140.0, 140.0, 140.0)
    assert "DILUTION_RED_FLAG" not in bus.run(old, {"wacc": 0.09}).mandatory_flags


def test_the_flag_fires_on_more_than_five_percent_over_three_years():
    out = bus.run(_dilutive(0.08), {"wacc": 0.09})
    assert "DILUTION_RED_FLAG" in out.mandatory_flags


def test_no_flag_below_the_threshold():
    out = bus.run(_dilutive(0.02), {"wacc": 0.09})
    assert "DILUTION_RED_FLAG" not in out.mandatory_flags


# --- "for three years" is a duration condition ----------------------------


def test_no_flag_when_history_is_shorter_than_three_years():
    """DECISION_RULES.md: '>5% CAGR *for three years*'. Two annual points
    span a single year, which cannot establish the sustained three-year
    trend Victor names — a one-year 20% dilution (e.g. a stock-funded
    acquisition) must not trip the flag."""
    two = [_row(2025, diluted=120.0), _row(2024, diluted=100.0)]
    out = bus.run(_packet(two), {"wacc": 0.09})
    assert "DILUTION_RED_FLAG" not in out.mandatory_flags
    # No judgment is requested either, since the flag never fired.
    assert "dilution_commensurate_value" not in {
        r.metric_id for r in out.judgment_requests}


def test_no_flag_at_two_years_of_span():
    """Three annual points are a two-year span — still short of the three
    years the rule requires, even with heavy dilution throughout."""
    three = [_row(2025, diluted=150.0), _row(2024, diluted=125.0),
             _row(2023, diluted=100.0)]
    assert "DILUTION_RED_FLAG" not in bus.run(
        _packet(three), {"wacc": 0.09}).mandatory_flags


def test_the_flag_fires_at_exactly_a_three_year_span():
    """Four annual points are a genuine three-year span: the shortest
    history that satisfies 'for three years'."""
    four = [_row(2025, diluted=130.0), _row(2024, diluted=120.0),
            _row(2023, diluted=110.0), _row(2022, diluted=100.0)]
    assert "DILUTION_RED_FLAG" in bus.run(
        _packet(four), {"wacc": 0.09}).mandatory_flags


def test_the_short_history_still_reports_the_cagr_it_has():
    """The duration gate governs the flag, not the reported metric: the
    3y companion still carries the best-available CAGR, the same
    report-what-you-have convention the 5y follows."""
    two = [_row(2025, diluted=120.0), _row(2024, diluted=100.0)]
    ca = bus.run(_packet(two), {"wacc": 0.09}).capital_allocation
    assert ca["diluted_share_cagr_3y"] == pytest.approx(0.20)


def test_the_flag_requests_the_commensurate_value_judgment():
    out = bus.run(_dilutive(0.08), {"wacc": 0.09})
    ids = {r.metric_id for r in out.judgment_requests}
    assert "dilution_commensurate_value" in ids


def test_no_judgment_is_requested_when_the_flag_does_not_fire():
    out = bus.run(_dilutive(0.02), {"wacc": 0.09})
    ids = {r.metric_id for r in out.judgment_requests}
    assert "dilution_commensurate_value" not in ids


def _judge(out, answer):
    from wbj.deep import _apply_judgments_to_output_fields
    from wbj.judge import Judgment
    from wbj.specialists.common import JudgmentRequest

    req = JudgmentRequest(
        request_id="business_analysis:dilution_commensurate_value",
        agent_id=bus.AGENT_ID, metric_id="dilution_commensurate_value",
        question="?", schema_hint="yes|no")
    j = Judgment(request_id=req.request_id, answer=answer,
                 evidence_class="C", source="analyst")
    return _apply_judgments_to_output_fields([("Business", out)], [j], [req])[0][1]


def test_confirming_commensurate_value_clears_the_flag():
    """DECISION_RULES.md's third condition: the flag fires 'without
    commensurate per-share value creation', so confirming that value kept
    pace removes it."""
    out = bus.run(_dilutive(0.08), {"wacc": 0.09})
    cleared = _judge(out, "yes")
    assert "DILUTION_RED_FLAG" not in cleared.mandatory_flags
    assert cleared.capital_allocation["commensurate_per_share_value_creation"] is True


def test_denying_it_leaves_the_flag_standing():
    out = bus.run(_dilutive(0.08), {"wacc": 0.09})
    kept = _judge(out, "no")
    assert "DILUTION_RED_FLAG" in kept.mandatory_flags


def test_an_unanswered_judgment_leaves_the_flag_standing():
    """A red flag's conservative posture: unconfirmed dilution warns."""
    out = bus.run(_dilutive(0.08), {"wacc": 0.09})
    assert "DILUTION_RED_FLAG" in out.mandatory_flags


@pytest.mark.parametrize("answer", ["maybe", "", "42"])
def test_an_ambiguous_answer_does_not_clear_the_flag(answer):
    """Strict, like the moat vocabulary: only a clear yes clears it. An
    off-vocabulary answer leaves the red flag standing."""
    out = bus.run(_dilutive(0.08), {"wacc": 0.09})
    assert "DILUTION_RED_FLAG" in _judge(out, answer).mandatory_flags


def test_the_coercion_rejects_everything_but_a_clear_yes_or_no():
    """The unit level, including shapes a Judgment cannot even carry."""
    from wbj.deep import _coerce_yes_no

    for clears in ("yes", "y", "true", "YES", " True ", True):
        assert _coerce_yes_no(clears) is True
    for stays in ("no", "n", "false", " No ", False):
        assert _coerce_yes_no(stays) is False
    for ambiguous in ("maybe", "", "42", None, ["yes"], {"a": 1}, 42):
        assert _coerce_yes_no(ambiguous) is None


# --- BUS-FCFC-017: the registered five-year window -------------------------


def test_fcf_conversion_scores_at_the_registered_five_years():
    """FORMULAS.md registers BUS-FCFC-017 at "5y rolling" and DATASET.md
    requires five years of its inputs. Five annual points score."""
    rows = [_row(y) for y in range(2025, 2020, -1)]   # five rows
    assert _metric(bus.run(_packet(rows), {"wacc": 0.09}),
                   "BUS-FCFC-017").value is not None


def test_fcf_conversion_is_missing_below_five_years():
    """'Minimum history is a requirement for a score, not a suggestion'
    (DATASET.md). Four years cannot be scored under a period label that
    claims five — the same floor STAB-009/RANGE-010 enforce."""
    rows = [_row(y) for y in range(2025, 2021, -1)]   # four rows
    assert _metric(bus.run(_packet(rows), {"wacc": 0.09}),
                   "BUS-FCFC-017").value is None
