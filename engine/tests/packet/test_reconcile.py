"""Tests for wbj.packet.reconcile.reconcile — FMP/EDGAR source-hierarchy
reconciliation, per Cerebro/shared/SOURCE_HIERARCHY.md.
"""

from wbj.core.nullstates import NullState, Value
from wbj.packet.reconcile import reconcile


def _fmp(x, **kw):
    return Value.of(x, unit="usd", source_name="FMP", **kw)


def _edgar(x, **kw):
    return Value.of(x, unit="usd", source_name="EDGAR", **kw)


# --- both valid, agreement (<=2%) -> EDGAR value ----------------------------


def test_agreement_within_2pct_returns_edgar_value():
    fmp = _fmp(100.0)
    edgar = _edgar(101.0)  # 1% diff (denominator = edgar)

    out = reconcile("revenue", fmp, edgar)

    assert out.is_valid
    assert out.value == 101.0


def test_agreement_exact_match_returns_edgar_value():
    fmp = _fmp(100.0)
    edgar = _edgar(100.0)

    out = reconcile("revenue", fmp, edgar)

    assert out.value == 100.0
    assert out.is_valid


def test_agreement_boundary_exactly_2pct_treated_as_agreement():
    edgar = _edgar(100.0)
    fmp = _fmp(102.0)  # exactly 2% diff

    out = reconcile("revenue", fmp, edgar)

    assert out.value == 100.0
    assert not out.is_null


def test_agreement_lineage_notes_both_sources():
    fmp = _fmp(100.0)
    edgar = _edgar(101.0)

    out = reconcile("revenue", fmp, edgar)

    combined = " ".join(out.warnings)
    assert "100" in combined or "FMP" in combined
    assert "101" in combined or "EDGAR" in combined


# --- both valid, 2-5% diff -> EDGAR value + warning -------------------------


def test_3pct_diff_returns_edgar_with_warning():
    edgar = _edgar(100.0)
    fmp = _fmp(103.0)  # 3% diff

    out = reconcile("shares_outstanding", fmp, edgar)

    assert out.is_valid
    assert out.value == 100.0
    assert len(out.warnings) >= 1
    combined = " ".join(out.warnings)
    assert "103" in combined
    assert "100" in combined


def test_boundary_just_above_2pct_is_escalated():
    edgar = _edgar(100.0)
    fmp = _fmp(102.5)  # 2.5% diff

    out = reconcile("revenue", fmp, edgar)

    assert out.value == 100.0
    assert len(out.warnings) >= 1


def test_boundary_exactly_5pct_is_escalated_not_conflicted():
    edgar = _edgar(100.0)
    fmp = _fmp(105.0)  # exactly 5% diff

    out = reconcile("revenue", fmp, edgar)

    assert out.is_valid
    assert out.value == 100.0


# --- both valid, >5% diff -> CONFLICTED -------------------------------------


def test_8pct_diff_returns_conflicted_null():
    edgar = _edgar(100.0)
    fmp = _fmp(108.0)  # 8% diff

    out = reconcile("cash", fmp, edgar)

    assert out.is_null
    assert out.state == NullState.CONFLICTED


def test_conflicted_warnings_contain_both_values():
    edgar = _edgar(100.0)
    fmp = _fmp(108.0)

    out = reconcile("cash", fmp, edgar)

    combined = " ".join(out.warnings)
    assert "100" in combined
    assert "108" in combined


# --- only one source valid ---------------------------------------------------


def test_only_edgar_valid_returns_edgar():
    edgar = _edgar(50.0)
    fmp = Value.null(NullState.MISSING, unit="usd")

    out = reconcile("debt", fmp, edgar)

    assert out.is_valid
    assert out.value == 50.0


def test_only_fmp_valid_returns_fmp_with_source_quality_note():
    fmp = _fmp(75.0)
    edgar = Value.null(NullState.MISSING, unit="usd")

    out = reconcile("debt", fmp, edgar)

    assert out.is_valid
    assert out.value == 75.0
    assert len(out.warnings) >= 1


def test_neither_valid_returns_null():
    fmp = Value.null(NullState.MISSING, unit="usd")
    edgar = Value.null(NullState.MISSING, unit="usd")

    out = reconcile("debt", fmp, edgar)

    assert out.is_null


# --- denominator / zero handling --------------------------------------------


def test_edgar_zero_falls_back_to_fmp_denominator():
    edgar = _edgar(0.0)
    fmp = _fmp(0.5)  # relative to fmp denom: 0.5/0.5 = 100% diff -> conflicted

    out = reconcile("buybacks", fmp, edgar)

    assert out.is_null
    assert out.state == NullState.CONFLICTED


def test_both_zero_treated_as_equal():
    edgar = _edgar(0.0)
    fmp = _fmp(0.0)

    out = reconcile("buybacks", fmp, edgar)

    assert out.is_valid
    assert out.value == 0.0
