import pytest

from wbj.core.nullstates import (
    PROHIBITED_IMPUTATION,
    EvidenceClass,
    NullState,
    Value,
)


def test_valid_value():
    v = Value.of(0.21, unit="ratio", source_name="10-K")
    assert v.is_valid and v.value == 0.21 and not v.is_null


def test_null_value_has_no_number():
    v = Value.null(NullState.NOT_SCORABLE)
    assert v.is_null and v.value is None and v.state == NullState.NOT_SCORABLE


def test_exactly_one_of_value_or_state():
    with pytest.raises(ValueError):
        Value(value=1.0, state=NullState.MISSING, unit="x", warnings=[])


def test_null_propagates_through_map2():
    a, b = Value.of(10, unit="usd"), Value.null(NullState.MISSING)
    out = Value.map2(a, b, lambda x, y: x / y)
    assert out.is_null and out.state == NullState.MISSING


def test_prohibited_list_contains_wacc_and_beta():
    assert "wacc" in PROHIBITED_IMPUTATION and "beta" in PROHIBITED_IMPUTATION


# --- supplementary coverage ---


def test_neither_value_nor_state_raises():
    with pytest.raises(ValueError):
        Value(value=None, state=None, unit="x", warnings=[])


def test_null_state_enum_members():
    assert {s.value for s in NullState} == {
        "MISSING",
        "NOT_APPLICABLE",
        "NOT_MEANINGFUL",
        "CONFLICTED",
        "STALE",
        "NOT_SCORABLE",
    }


def test_evidence_class_enum_members():
    assert {e.value for e in EvidenceClass} == {"R", "C", "E", "A", "Q"}


def test_of_carries_lineage_fields():
    v = Value.of(
        1250.0,
        unit="USD_millions",
        period="FY2025",
        as_of="2026-02-14T21:05:00Z",
        source_name="SEC 10-K",
        source_locator="accession-0001",
        evidence_class=EvidenceClass.C,
    )
    assert v.period == "FY2025"
    assert v.as_of == "2026-02-14T21:05:00Z"
    assert v.source_name == "SEC 10-K"
    assert v.source_locator == "accession-0001"
    assert v.evidence_class == EvidenceClass.C
    assert v.state is None


def test_null_default_unit_and_warnings():
    v = Value.null(NullState.MISSING)
    assert v.unit == ""
    assert v.warnings == []


def test_map2_returns_first_null_when_both_null():
    a = Value.null(NullState.CONFLICTED)
    b = Value.null(NullState.MISSING)
    out = Value.map2(a, b, lambda x, y: x + y)
    assert out.is_null and out.state == NullState.CONFLICTED


def test_map2_computes_when_both_valid():
    a, b = Value.of(4, unit="usd"), Value.of(2, unit="usd")
    out = Value.map2(a, b, lambda x, y: x / y)
    assert out.is_valid and out.value == 2.0


def test_value_is_frozen():
    v = Value.of(1.0, unit="x")
    with pytest.raises(Exception):
        v.value = 2.0  # type: ignore[misc]


def test_prohibited_imputation_full_list():
    assert PROHIBITED_IMPUTATION == frozenset(
        {
            "wacc",
            "beta",
            "customer_concentration",
            "organic_growth",
            "market_share",
            "debt_maturities",
            "earnings_revisions",
            "technical_volume",
            "terminal_growth",
            "support_resistance_touches",
        }
    )
