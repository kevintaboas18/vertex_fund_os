"""OUTPUT_CONTRACT.md's per-row requirements, and the secrets rule.

The contract names ten fields "every metric row must contain", and
HANDOFF_CONTRACT.md has the main agent reject a packet whose rows are
incomplete. Two of the ten -- `period` ("Explicit") and `source` ("Stable
source locator") -- arrived empty on all thirty rows: `MetricRow
.from_value` reads them off the `Value`, and nothing ever set them.
"""

import json

import pytest

from wbj.specialists import business as bus


ANNUAL = [
    {"fiscalYear": str(y), "filingDate": f"{y + 1}-02-20",
     "revenue": 1000.0 + y, "ebit": 200.0, "total_debt": 150.0,
     "total_equity": 600.0, "cash": 200.0, "total_assets": 1000.0,
     "total_liabilities": 400.0, "income_before_tax": 100.0,
     "income_tax_expense": 15.0}
    for y in range(2016, 2027)
]


def test_a_point_metric_carries_the_latest_fiscal_year():
    assert bus._period_label(ANNUAL) == "FY2026"


def test_a_window_metric_carries_its_span():
    """A reader has to be able to tell a one-year margin from a five-year
    range without opening the formula registry."""
    assert bus._period_label(ANNUAL, 5) == "FY2022-FY2026"
    assert bus._period_label(ANNUAL, 11) == "FY2016-FY2026"


def test_an_empty_history_says_so_rather_than_inventing_a_period():
    assert bus._period_label([]) == "UNKNOWN"


def test_the_source_locator_pins_the_filing_it_came_from():
    """"Stable source locator" means traceable to a document, not to
    "the packet"."""
    from types import SimpleNamespace

    packet = SimpleNamespace(security=SimpleNamespace(ticker="nvda"))
    source = bus._filings_source(packet, ANNUAL)
    assert "NVDA" in source
    assert "FY2026" in source
    assert "2027-02-20" in source          # the acceptance date
    assert source.startswith("fmp:")       # provider named


def test_the_source_locator_survives_an_empty_history():
    from types import SimpleNamespace

    packet = SimpleNamespace(security=SimpleNamespace(ticker="NVDA"))
    assert bus._filings_source(packet, []) == "fmp:annual-statements/NVDA"


def test_provenance_never_overwrites_lineage_a_formula_recorded():
    """A formula that already knows where its number came from keeps it."""
    from wbj.core.nullstates import Value

    own = Value.of(1.0, unit="pct", period="FY2020", source_name="10-K exhibit 13")
    kept = bus._with_provenance(own, period="FY2026", source="fmp:annual-statements/X")
    assert kept.period == "FY2020"
    assert kept.source_name == "10-K exhibit 13"

    # DATA_POLICY.md's lineage separates what the source *is* from which
    # document it is, so the locator lands in `source_locator` and the name
    # beside it. Both were collapsed into `source_name` before.
    bare = Value.of(1.0, unit="pct")
    filled = bus._with_provenance(bare, period="FY2026", source="fmp:x")
    assert filled.period == "FY2026"
    assert filled.source_locator == "fmp:x"
    assert filled.source_name == bus._FILINGS_SOURCE_NAME

    analyst = bus._with_provenance(Value.of(1.0, unit="pct"), period="FY2026",
                                   source="Entradas/TEST.json")
    assert analyst.source_locator == "Entradas/TEST.json"
    assert analyst.source_name == bus._ANALYST_SOURCE_NAME


def test_a_null_value_still_carries_its_period_and_source():
    """An absent metric has to say which period it is absent for —
    otherwise a reader cannot tell a gap from a stale row."""
    from wbj.core.nullstates import NullState, Value

    missing = Value.null(NullState.MISSING, unit="pct")
    filled = bus._with_provenance(missing, period="FY2026", source="fmp:x")
    assert filled.period == "FY2026"
    assert filled.source_locator == "fmp:x"
    assert filled.source_name == bus._FILINGS_SOURCE_NAME


def test_the_secrets_rule_holds_for_the_source_locator():
    """SECURITY_AND_DISCLAIMER.md: "Do not place API keys, credentials,
    or paid-data entitlements in output files. Store only provider names
    and dataset identifiers.\""""
    from types import SimpleNamespace

    packet = SimpleNamespace(security=SimpleNamespace(ticker="NVDA"))
    source = bus._filings_source(packet, ANNUAL)
    for forbidden in ("apikey", "api_key", "token", "secret", "&key="):
        assert forbidden not in source.lower()


def _emitted_rows():
    """The rows a real run produces, keyed by metric id.

    Attribution is a property of the emitted row, so read it off the
    output rather than off the call that wrote it.
    """
    from datetime import datetime, timezone

    from wbj.schemas.packet import AnalysisMeta, Packet, Security

    packet = Packet(
        security=Security(ticker="T", exchange="X",
                          security_type="operating_company",
                          reporting_currency="USD", valuation_currency="USD",
                          sector="Technology", industry="Semiconductors"),
        analysis=AnalysisMeta(
            knowledge_timestamp=datetime.now(timezone.utc).isoformat(),
            industry_adapter="default_nonfinancial"),
        fundamentals={"annual": list(reversed(ANNUAL))}, facts_table={},
        market_data={}, estimates={}, capital_structure={}, staleness={})
    return {r.metric_id: r for r in bus.run(packet, {"wacc": 0.09}).metrics}


def test_every_overlay_metric_names_the_analyst_file_not_the_provider():
    """A number an analyst wrote must not be attributed to a data feed."""
    rows = _emitted_rows()
    # The overlay-only metrics are the ones DATASET.md marks conditional.
    for metric in ("BUS-MIX-001", "BUS-CONC-003", "BUS-HHI-004",
                   "BUS-NRR-020", "BUS-GUIDE-027"):
        row = rows[metric]
        assert row.source_locator == "Entradas/T.json", \
            f"{metric} is attributed to the data feed: {row.source_locator}"
        assert row.source_type == "analyst input", metric


def test_every_filings_metric_keeps_the_provider_locator():
    """The converse: a number derived from the statements must not be
    attributed to a file the analyst wrote."""
    rows = _emitted_rows()
    for metric in ("BUS-GM-007", "BUS-OM-008", "BUS-ROIC-013", "BUS-SBC-030"):
        row = rows[metric]
        assert not row.source_locator.startswith("Entradas/"), \
            f"{metric} is attributed to the analyst file"
        assert row.source_type == "filing", metric


# --- QA_CHECKLIST.md line 1: "Security ... currency, and exchange are correct"


def _packet_from_profile(**profile_overrides):
    """A packet built from the shared fake providers, with the profile
    payload's field names rewritten as the test asks.

    The fixture profile carries FMP's *legacy* spellings, so a test that
    wants the current ones swaps the keys and rebuilds.
    """
    import sys
    from pathlib import Path

    fixtures = Path(__file__).parent / "fixtures" / "packet"
    if str(fixtures) not in sys.path:
        sys.path.insert(0, str(fixtures))

    from make_packet_fixture import (FIXED_NOW, FakeFMPProvider,
                                     make_default_providers)
    from wbj.packet.builder import Providers, build_packet

    base = make_default_providers(FIXED_NOW)
    profile = dict(base.fmp.profile("NVDA")[0])
    for key, value in profile_overrides.items():
        if value is None:
            profile.pop(key, None)
        else:
            profile[key] = value

    providers = Providers(
        fmp=FakeFMPProvider(ohlcv=base.fmp._ohlcv,
                            benchmark_ohlcv=base.fmp._benchmark_ohlcv,
                            overrides={"profile": [profile]}),
        edgar=base.edgar, finnhub=base.finnhub, fred=base.fred)
    return build_packet("NVDA", providers, FIXED_NOW)


def test_the_exchange_is_read_from_the_field_the_provider_returns():
    """FMP's stable endpoint returns `exchange` and `exchangeFullName`.
    The builder read only the legacy `exchangeShortName`, so the key was
    never present and every packet carried an empty exchange — a field
    OUTPUT_SCHEMA.md declares and QA_CHECKLIST.md's first line requires."""
    packet = _packet_from_profile(exchangeShortName=None, exchange="NYSE")
    assert packet.security.exchange == "NYSE"


def test_market_cap_is_read_from_the_field_the_provider_returns():
    """The same renamed-field trap: FMP returns `marketCap`, not the
    legacy `mktCap`. Market cap is the equity weight in WACC, which the
    ROIC-WACC spread — and the wide-moat gate reading it — rests on."""
    packet = _packet_from_profile(mktCap=None, marketCap=1_234_000_000.0)
    assert packet.capital_structure["market_cap"] == 1_234_000_000.0


@pytest.mark.parametrize("legacy,current,read", [
    ("exchangeShortName", "exchange", lambda p: p.security.exchange),
    ("mktCap", "marketCap", lambda p: p.capital_structure["market_cap"]),
])
def test_a_renamed_provider_field_reads_both_names(legacy, current, read):
    """Neither read may depend on one spelling: the legacy name is what
    older cached payloads carry, the current one is what the API serves.
    Both have to land on the same packet field."""
    value = "AMEX" if legacy == "exchangeShortName" else 987_000_000.0

    old = _packet_from_profile(**{legacy: value, current: None})
    new = _packet_from_profile(**{legacy: None, current: value})
    assert read(old) == value, f"the legacy {legacy} stopped being read"
    assert read(new) == value, f"the current {current} is not read"


# --- DATA_POLICY.md "Required lineage fields" ------------------------------


def test_every_row_carries_the_lineage_data_policy_requires():
    """That block opens with `metric_id` and `value` and shares five fields
    with OUTPUT_CONTRACT.md's row contract, so the two describe one object.
    The row carried six of the fifteen: `as_of`, `source_locator`,
    `period_start/end`, `knowledge_timestamp`, `source_type`,
    `calculation_inputs` and `currency` were all absent, and `Value` was
    already recording three of them for the row to drop."""
    from pathlib import Path

    from wbj.schemas.packet import Packet

    fixture = Path(__file__).parent / "fixtures" / "packet" / "NVDA_packet.json"
    out = bus.run(Packet.model_validate_json(fixture.read_text(encoding="utf-8")),
                  {"wacc": 0.09})
    for row in out.metrics:
        assert row.knowledge_timestamp, row.metric_id
        assert row.as_of, row.metric_id
        assert row.currency == "USD", row.metric_id
        assert row.source_type in {"filing", "analyst input"}, row.metric_id
        assert row.source_name and row.source_locator, row.metric_id
        assert row.calculation_inputs, row.metric_id
        assert row.period_end, row.metric_id


def test_the_source_type_follows_the_locator_not_the_name():
    """A metric the analyst supplies is not a filing. Reading `source_name`
    to decide labelled every analyst-supplied row "filing" once the name
    became a human label rather than the locator."""
    from pathlib import Path

    from wbj.schemas.packet import Packet

    fixture = Path(__file__).parent / "fixtures" / "packet" / "NVDA_packet.json"
    out = bus.run(Packet.model_validate_json(fixture.read_text(encoding="utf-8")),
                  {"wacc": 0.09})
    by_id = {r.metric_id: r for r in out.metrics}
    assert by_id["BUS-ROIC-013"].source_type == "filing"
    assert by_id["BUS-NRR-020"].source_type == "analyst input"
    assert by_id["BUS-NRR-020"].source_locator.startswith("Entradas/")
