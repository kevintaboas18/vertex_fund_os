"""The overlay must enrich honestly or stay silent.

Business came back N/S on every ticker because nothing built an overlay
and `overlay["wacc"]` gates every ROIC/spread/EVA metric. These lock in
both halves of the fix: the keys get built from reported data, and a key
that cannot be sourced is omitted rather than guessed — otherwise the
N/S rule would quietly become "score a fabricated number".
"""

from types import SimpleNamespace

import pytest

from wbj.overlay.from_packet import (_consensus_eps_growth, _segment_shares,
                                      _wacc_from_packet)


class _Bar:
    def __init__(self, date, close):
        self.date, self.close = date, close


def _packet(rf=0.045, beta=1.2, market_cap=1_000_000.0, daily=None, annual=None):
    return SimpleNamespace(
        estimates={"risk_free_rate": rf} if rf is not None else {},
        capital_structure={"beta": beta, "market_cap": market_cap,
                           "total_debt": 0.0, "diluted_shares": 1000},
        fundamentals={"annual": annual if annual is not None
                      else [{"ebit": 100.0, "interest_expense": 10.0,
                             "income_before_tax": 90.0, "income_tax_expense": 18.0}]},
        market_data=SimpleNamespace(daily=daily or []),
        security=SimpleNamespace(ticker="TEST"),
    )


def test_wacc_is_computed_from_reported_inputs():
    w = _wacc_from_packet(_packet())
    assert w is not None and 0.0 < w < 1.0


def test_wacc_is_omitted_without_a_risk_free_rate():
    """No rate, no CAPM. Returning a default here would hand every
    ROIC/spread metric a fabricated cost of capital."""
    assert _wacc_from_packet(_packet(rf=None)) is None


def test_wacc_is_omitted_without_beta():
    assert _wacc_from_packet(_packet(beta=None)) is None


def test_market_cap_is_rebuilt_from_the_newest_bar():
    """FMP returns bars newest-first, so the newest close is the max by
    date, not the last row — getting this backwards priced the company
    off a three-year-old quote."""
    daily = [_Bar("2026-07-17", 200.0), _Bar("2023-07-18", 50.0)]
    w = _wacc_from_packet(_packet(market_cap=None, daily=daily))
    assert w is not None


def test_segment_shares_are_fractions_of_reported_revenue():
    class _FMP:
        def revenue_product_segmentation(self, t):
            return [{"data": {"A": 75.0, "B": 25.0}}]

    shares = _segment_shares(_FMP(), "TEST")
    assert shares == [0.75, 0.25]
    assert sum(shares) == 1.0


def test_single_segment_is_not_a_mix():
    """One line item is not a revenue split; reporting it as a 100% share
    would fake diversification evidence the filing never gave."""
    class _FMP:
        def revenue_product_segmentation(self, t):
            return [{"data": {"Everything": 100.0}}]

    assert _segment_shares(_FMP(), "TEST") is None


def test_missing_segmentation_yields_no_key():
    class _FMP:
        def revenue_product_segmentation(self, t):
            return None

    assert _segment_shares(_FMP(), "TEST") is None


def test_income_statement_keys_come_from_the_packet():
    """risk.py reads interest expense and SG&A from the overlay, but they are
    plain income-statement lines the packet already carries."""
    from wbj.overlay.from_packet import _income_statement_keys

    p = _packet(annual=[
        {"interest_expense": 10.0, "sga": 50.0, "depreciation_and_amortization": 20.0},
        {"sga": 45.0, "depreciation_and_amortization": 18.0},
    ])
    out = _income_statement_keys(p)
    assert out["interest_expense"] == 10.0
    assert out["sga"] == 50.0 and out["sga_prior"] == 45.0


def test_depreciation_is_not_filled_from_d_and_a():
    """This asserted the opposite, and that is what hid the substitution.

    Beneish DEPI is defined on depreciation alone. risk.py falls back to the
    packet's D&A field on its own and raises DA_PROXY_FOR_DEPRECIATION to say it
    did -- but that flag is `overlay.get("depreciation") is None`, so filling
    the key here made it permanently False. The proxy then applied to every
    ticker with nothing said, which is the silent denominator change
    DATA_POLICY.md prohibits. The true standalone tag arrives via
    `Packet.xbrl_inputs` when the company files it.
    """
    from wbj.overlay.from_packet import _income_statement_keys

    out = _income_statement_keys(_packet(annual=[
        {"sga": 50.0, "depreciation_and_amortization": 20.0},
        {"sga": 45.0, "depreciation_and_amortization": 18.0},
    ]))
    assert "depreciation" not in out
    assert "depreciation_prior" not in out


def test_ppe_is_not_filled_from_the_net_balance():
    """Same defect, same reason: Beneish AQI is defined on GROSS PP&E, and
    filling the key from the net balance kept PPE_NET_PROXY_FOR_GROSS_PPE from
    ever firing."""
    from wbj.overlay.from_packet import _balance_sheet_keys

    class _FMP:
        def balance_annual(self, ticker, limit=2):
            return [{"propertyPlantEquipmentNet": 100.0, "retainedEarnings": 7.0},
                    {"propertyPlantEquipmentNet": 90.0}]

    out = _balance_sheet_keys(_FMP(), "TEST")
    assert "ppe" not in out and "ppe_prior" not in out
    assert out["retained_earnings"] == 7.0        # esta sí es del balance


def test_income_statement_keys_omit_absent_lines():
    """An absent line must not become 0.0 — zero interest expense makes
    coverage NOT_MEANINGFUL, which is a different claim from 'unknown'."""
    from wbj.overlay.from_packet import _income_statement_keys

    out = _income_statement_keys(_packet(annual=[{"revenue": 100.0}]))
    assert "interest_expense" not in out
    assert "sga" not in out


def test_geographic_and_product_shares_share_one_parser():
    """Both are the same reported-split shape; risk reads product_shares
    and geographic_shares, business reads segment_shares."""
    from wbj.overlay.from_packet import _shares_from_segmentation

    out = _shares_from_segmentation([{"data": {"US": 60.0, "CN": 40.0}}])
    assert out == [0.6, 0.4]


def test_debt_due_comes_from_reported_short_term_debt():
    """PROHIBITED_IMPUTATION bars *inferring* maturity amounts. The
    balance sheet's short-term debt line is the reported figure, which
    is what the maturity-wall metric asks for."""
    from wbj.overlay.from_packet import _income_statement_keys

    out = _income_statement_keys(_packet(annual=[{"short_term_debt": 1234.0}]))
    assert out["debt_due"] == 1234.0


def test_no_short_term_debt_line_means_no_debt_due_key():
    """Absent must stay absent: defaulting to 0.0 would assert the
    company has no maturities, which the filing never said."""
    from wbj.overlay.from_packet import _income_statement_keys

    assert "debt_due" not in _income_statement_keys(_packet(annual=[{"revenue": 1.0}]))


class _S:
    """Minimal settings: `_backlog_history` caches per filing."""

    def __init__(self, tmp_path):
        self.cache_dir = tmp_path


def _doc(period, text="x"):
    return {"period": period, "text": text, "accession": period, "url": "u"}


def test_backlog_history_is_ordered_by_report_period(monkeypatch, tmp_path):
    """A 10-K filed after a 10-Q can still cover an earlier period.
    Growth computed on a mis-ordered series would invent a trend, so the
    sort key is the report date, not the filing date."""
    import wbj.extract.filing as fx
    from wbj.overlay.from_packet import _backlog_history

    values = {"2025-04-30": 1.0, "2025-07-31": 2.0, "2025-10-31": 3.0}
    monkeypatch.setattr(fx, "extract_backlog",
                        lambda doc, settings, client=None: values.get(doc["period"]))

    class _Edgar:
        def companyfacts(self, cik):
            return {}  # nothing tagged, so the prose path runs

        def recent_filings_text(self, cik, form="10-Q", limit=8):
            # Deliberately out of order.
            return [_doc("2025-10-31"), _doc("2025-04-30"), _doc("2025-07-31")]

    assert _backlog_history(_Edgar(), _S(tmp_path), 1, None) == [1.0, 2.0, 3.0]


def test_backlog_history_deduplicates_periods(monkeypatch, tmp_path):
    """The 10-K and a 10-Q can report the same period; counting it twice
    would show a quarter of zero growth that never happened."""
    import wbj.extract.filing as fx
    from wbj.overlay.from_packet import _backlog_history

    monkeypatch.setattr(fx, "extract_backlog",
                        lambda doc, settings, client=None: 5.0)

    class _Edgar:
        def companyfacts(self, cik):
            return {}  # nothing tagged, so the prose path runs

        def recent_filings_text(self, cik, form="10-Q", limit=8):
            return [_doc("2025-10-31"), _doc("2025-07-31")]

    out = _backlog_history(_Edgar(), _S(tmp_path), 1, _doc("2025-10-31"))
    assert out == [5.0, 5.0]


def test_a_single_observation_is_not_a_history(monkeypatch, tmp_path):
    """MKT-BACK-015 compares two periods; one point cannot yield growth."""
    import wbj.extract.filing as fx
    from wbj.overlay.from_packet import _backlog_history

    monkeypatch.setattr(fx, "extract_backlog",
                        lambda doc, settings, client=None: 5.0)

    class _Edgar:
        def companyfacts(self, cik):
            return {}  # nothing tagged, so the prose path runs

        def recent_filings_text(self, cik, form="10-Q", limit=8):
            return [_doc("2025-10-31")]

    assert _backlog_history(_Edgar(), _S(tmp_path), 1, None) is None


class _Cache:
    def __init__(self):
        self.store, self.writes = {}, 0

    def get(self, ticker, key):
        return self.store.get((ticker, key))

    def put(self, ticker, key, value):
        self.store[(ticker, key)] = value
        self.writes += 1


def test_a_filing_is_extracted_once_and_reused():
    """A filing is immutable, so reading it twice and getting different
    answers is a defect. Re-extraction made the same ticker score 4.71
    then 4.67 on the same day."""
    from wbj.overlay.from_packet import _cached_extract

    cache, calls = _Cache(), []

    def compute():
        calls.append(1)
        return {"catalysts": [{"event": "Launch"}]}

    filing = {"accession": "0001045810-26-000021"}
    first = _cached_extract(cache, "NVDA", filing, "disclosures", compute)
    second = _cached_extract(cache, "NVDA", filing, "disclosures", compute)
    assert first == second
    assert len(calls) == 1


def test_a_cached_empty_result_is_not_recomputed():
    """"The filing says nothing" is an answer worth keeping. Treating it
    as a cache miss would re-bill every run for the same silence."""
    from wbj.overlay.from_packet import _cached_extract

    cache, calls = _Cache(), []

    def compute():
        calls.append(1)
        return None

    filing = {"accession": "acc-1"}
    _cached_extract(cache, "X", filing, "backlog", compute)
    _cached_extract(cache, "X", filing, "backlog", compute)
    assert len(calls) == 1


def test_different_filings_do_not_share_an_entry():
    from wbj.overlay.from_packet import _cached_extract

    cache = _Cache()
    _cached_extract(cache, "X", {"accession": "a"}, "backlog", lambda: 1.0)
    assert _cached_extract(cache, "X", {"accession": "b"}, "backlog", lambda: 2.0) == 2.0


class _Settings:
    def __init__(self, tmp_path):
        self.inputs_dir = tmp_path
        self.cache_dir = tmp_path


def _write(tmp_path, ticker, payload):
    import json
    (tmp_path / f"{ticker}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_unattributed_tam_is_refused(tmp_path):
    """AGENT.md: "Do not score an unattributed market-size claim", and
    DECISION_RULES.md scores tier 5 at 0. A TAM with no source must not
    reach the specialist at all."""
    from wbj.overlay.from_packet import _manual_overlay

    _write(tmp_path, "X", {"tam": 1e12})
    assert _manual_overlay(_Settings(tmp_path), "X") == {}


def test_tier_five_tam_is_refused(tmp_path):
    """Tier 5 *is* the unattributed tier — naming it does not attribute
    anything."""
    from wbj.overlay.from_packet import _manual_overlay

    _write(tmp_path, "X", {"tam": 1e12, "tam_source": "someone said so",
                           "tam_source_tier": 5})
    assert _manual_overlay(_Settings(tmp_path), "X") == {}


def test_sourced_tam_is_accepted(tmp_path):
    """A tier 1-4 TAM with a named source is an "explicitly disclosed
    assumption", which AGENT.md's no-speculation rule permits."""
    from wbj.overlay.from_packet import _manual_overlay

    _write(tmp_path, "X", {"tam": 4e11, "tam_source": "IDC 2026 study",
                           "tam_source_tier": 3})
    out = _manual_overlay(_Settings(tmp_path), "X")
    assert out["tam"] == 4e11
    assert out["tam_source_tier"] == 3


def test_refusing_a_tam_leaves_other_inputs_intact(tmp_path):
    """Only the market-size claim is in question; unrelated analyst
    inputs must survive."""
    from wbj.overlay.from_packet import _manual_overlay

    _write(tmp_path, "X", {"tam": 1e12,
                           "adoption": {"current_units": 1, "eventual_units": 2}})
    out = _manual_overlay(_Settings(tmp_path), "X")
    assert "tam" not in out
    assert out["adoption"] == {"current_units": 1, "eventual_units": 2}


def test_no_input_file_is_not_an_error(tmp_path):
    from wbj.overlay.from_packet import _manual_overlay

    assert _manual_overlay(_Settings(tmp_path), "NOPE") == {}


def test_malformed_input_file_is_not_fatal(tmp_path):
    """A typo in a hand-edited file must not take the analysis down."""
    from wbj.overlay.from_packet import _manual_overlay

    (tmp_path / "X.json").write_text("{not json", encoding="utf-8")
    assert _manual_overlay(_Settings(tmp_path), "X") == {}


class _FMPEst:
    def __init__(self, rows):
        self._rows = rows

    def analyst_estimates(self, ticker):
        return self._rows


def _est(date, eps):
    return {"date": date, "epsAvg": eps}


def test_no_revision_on_the_first_run(tmp_path):
    """The series starts empty, so there is nothing to compare against.
    Returning a revision of zero would assert estimates held steady,
    which nobody observed."""
    from wbj.overlay.from_packet import _consensus_history

    fmp = _FMPEst([_est("2027-01-25", 9.0)])
    assert _consensus_history(fmp, _Settings(tmp_path), "X",
                              today="2026-07-19") is None


def test_revision_is_measured_once_the_window_has_passed(tmp_path):
    from wbj.overlay.from_packet import _consensus_history

    s = _Settings(tmp_path)
    _consensus_history(_FMPEst([_est("2027-01-25", 8.0)]), s, "X", today="2026-07-19")
    out = _consensus_history(_FMPEst([_est("2027-01-25", 9.0)]), s, "X",
                             today="2026-09-17")
    assert out == {"current_consensus": 9.0, "prior_consensus": 8.0,
                   "prior_as_of": "2026-07-19"}


def test_a_snapshot_inside_the_window_is_not_a_revision(tmp_path):
    """A day-old estimate move is noise. DATASET.md asks for 90 days of
    revision history; comparing across two days would report it as a
    revision anyway."""
    from wbj.overlay.from_packet import _consensus_history

    s = _Settings(tmp_path)
    _consensus_history(_FMPEst([_est("2027-01-25", 8.0)]), s, "X", today="2026-07-19")
    assert _consensus_history(_FMPEst([_est("2027-01-25", 9.0)]), s, "X",
                              today="2026-07-21") is None


def test_a_change_of_fiscal_year_is_not_a_revision(tmp_path):
    """Once the estimate rolls to the next fiscal year, comparing it to
    the old year's number would read a change of subject as a revision."""
    from wbj.overlay.from_packet import _consensus_history

    s = _Settings(tmp_path)
    _consensus_history(_FMPEst([_est("2027-01-25", 8.0)]), s, "X", today="2026-07-19")
    assert _consensus_history(_FMPEst([_est("2028-01-25", 12.0)]), s, "X",
                              today="2026-09-17") is None


def test_wacc_is_omitted_without_reported_tax_figures():
    """A 21% statutory substitute would put an invented number under
    every ROIC, spread and EVA metric in Business. The absence has to
    propagate instead."""
    from wbj.overlay.from_packet import _wacc_from_packet

    p = _packet(annual=[{"ebit": 100.0, "interest_expense": 10.0}])
    assert _wacc_from_packet(p) is None


def test_wacc_uses_the_filed_debt_when_capital_structure_omits_it():
    """capital_structure.total_debt is unset on some tickers (NVDA among
    them) while the balance sheet reports it. Reading the gap as zero
    debt would understate WACC on a levered company."""
    from wbj.overlay.from_packet import _wacc_from_packet

    p = _packet(annual=[{"ebit": 100.0, "interest_expense": 10.0,
                         "income_before_tax": 90.0, "income_tax_expense": 18.0,
                         "total_debt": 5000.0}])
    p.capital_structure["total_debt"] = None
    assert _wacc_from_packet(p) is not None


def test_wacc_is_omitted_when_debt_is_reported_nowhere():
    from wbj.overlay.from_packet import _wacc_from_packet

    p = _packet(annual=[{"ebit": 100.0, "interest_expense": 10.0,
                         "income_before_tax": 90.0, "income_tax_expense": 18.0}])
    p.capital_structure["total_debt"] = None
    assert _wacc_from_packet(p) is None


class _EdgarFacts:
    def __init__(self, facts):
        self._facts = facts

    def companyfacts(self, cik):
        return {"facts": {"us-gaap": self._facts}}


def _tag(rows, unit="USD"):
    return {"units": {unit: rows}}


def test_xbrl_series_prefers_the_latest_filed_value_for_a_period():
    """A 10-K restates what a 10-Q first reported for the same period;
    the later filing wins."""
    from wbj.overlay.from_packet import _xbrl_series

    e = _EdgarFacts({"T": _tag([
        {"end": "2026-01-25", "val": 2.0e9, "filed": "2026-02-25"},
        {"end": "2026-01-25", "val": 1.9e9, "filed": "2025-11-19"},
    ])})
    assert _xbrl_series(e, 1, "T") == [("2026-01-25", 2.0e9)]


def test_backlog_prefers_xbrl_over_reading_prose(tmp_path):
    """Companies tag RevenueRemainingPerformanceObligation, so the series
    is free, deterministic and needs no model in the loop."""
    from wbj.overlay.from_packet import _backlog_history

    class _E(_EdgarFacts):
        def recent_filings_text(self, cik, form="10-Q", limit=8):
            raise AssertionError("must not read prose when XBRL has the tag")

    e = _E({"RevenueRemainingPerformanceObligation": _tag([
        {"end": "2025-10-26", "val": 2.5e9, "filed": "2025-11-19"},
        {"end": "2026-01-25", "val": 2.3e9, "filed": "2026-02-25"},
    ])})
    assert _backlog_history(e, _Settings(tmp_path), 1, None) == [2.5e9, 2.3e9]


def test_a_single_tagged_period_falls_back_to_prose(tmp_path, monkeypatch):
    """One observation is not a history, so the prose path still gets a
    chance to find more."""
    import wbj.extract.filing as fx
    from wbj.overlay.from_packet import _backlog_history

    monkeypatch.setattr(fx, "extract_backlog",
                        lambda doc, settings, client=None: 7.0)

    class _E(_EdgarFacts):
        def recent_filings_text(self, cik, form="10-Q", limit=8):
            return [_doc("2025-07-31"), _doc("2025-10-31")]

    e = _E({"RevenueRemainingPerformanceObligation": _tag([
        {"end": "2026-01-25", "val": 2.3e9, "filed": "2026-02-25"}])})
    assert _backlog_history(e, _Settings(tmp_path), 1, None) == [7.0, 7.0]


class _FMPEstimates:
    """Stand-in de FMPProvider para `analyst-estimates`, con el orden real:
    FMP devuelve las filas DESCENDENTES por fecha."""

    def __init__(self, rows):
        self._rows = rows

    def analyst_estimates(self, ticker, limit=10):
        return self._rows


def test_the_growth_rate_comes_from_fmp_not_yahoo():
    """VAL-PEG-028 necesita el crecimiento del año fiscal EN CURSO. Lo daba
    Yahoo; Yahoo queda fuera porque raspa un endpoint no documentado y no es
    una de las fuentes del sistema. FMP publica el mismo panel por año, así
    que el crecimiento sale del primer año FORWARD contra el último pasado.

    Con las cifras reales de NVDA (2026-08-03): 9.001/4.694 - 1 = +91.8%,
    que es la misma magnitud que devolvía Yahoo (+88.6%) sobre un panel
    ligeramente distinto."""
    fmp = _FMPEstimates([
        {"date": "2028-01-25", "epsAvg": 12.752},
        {"date": "2027-01-25", "epsAvg": 9.001},
        {"date": "2026-01-25", "epsAvg": 4.694},
        {"date": "2025-01-26", "epsAvg": 2.952},
    ])
    g = _consensus_eps_growth(fmp, "NVDA", today="2026-08-03")
    assert g == pytest.approx(9.001 / 4.694 - 1, rel=1e-9)
    assert 0.90 < g < 0.94


def test_the_growth_is_a_decimal_fraction_not_percentage_points():
    """`valuation.py` multiplica por 100 al formar el PEG. Devolver 91.8 en
    vez de 0.918 daría un PEG 100 veces menor — una acción carísima
    pareciendo una ganga."""
    fmp = _FMPEstimates([{"date": "2027-01-25", "epsAvg": 2.0},
                         {"date": "2026-01-25", "epsAvg": 1.0}])
    assert _consensus_eps_growth(fmp, "NVDA", today="2026-08-03") == pytest.approx(1.0)


def test_a_non_positive_base_yields_no_growth():
    """Crecer contra una base negativa o cero no significa nada, y el PEG lo
    trataría como una cifra buena."""
    for base in (0.0, -0.5):
        fmp = _FMPEstimates([{"date": "2027-01-25", "epsAvg": 3.0},
                             {"date": "2026-01-25", "epsAvg": base}])
        assert _consensus_eps_growth(fmp, "NVDA", today="2026-08-03") is None


def test_without_a_past_or_a_forward_year_there_is_no_growth():
    """Hace falta un año a cada lado de hoy: si no, no hay dos consensos que
    comparar y cualquier número sería inventado."""
    solo_futuro = _FMPEstimates([{"date": "2027-01-25", "epsAvg": 9.0}])
    solo_pasado = _FMPEstimates([{"date": "2026-01-25", "epsAvg": 4.7}])
    for fmp in (solo_futuro, solo_pasado):
        assert _consensus_eps_growth(fmp, "NVDA", today="2026-08-03") is None


def test_a_provider_failure_is_never_fatal():
    """El overlay es enriquecimiento: si FMP no responde, el resto de las
    métricas siguen puntuando."""
    class _Roto:
        def analyst_estimates(self, ticker, limit=10):
            return None

    assert _consensus_eps_growth(_Roto(), "NVDA", today="2026-08-03") is None


def test_the_engine_no_longer_imports_yahoo():
    """Victor fijó las fuentes: FMP, FinnHub, FRED y EDGAR. yfinance raspa
    un endpoint sin documentar que puede cambiar sin aviso, y un score que
    depende de eso puede moverse sin que nadie sepa por qué."""
    import pathlib as _pl

    motor = _pl.Path(__file__).resolve().parents[1] / "wbj"
    culpables = []
    for f in motor.rglob("*.py"):
        texto = f.read_text(encoding="utf-8")
        for linea in texto.splitlines():
            despojada = linea.strip()
            if despojada.startswith("#") or despojada.startswith('"'):
                continue
            if "import yfinance" in despojada or "yf.Ticker" in despojada:
                culpables.append(f"{f.name}: {despojada[:60]}")
    assert not culpables, f"el motor volvió a depender de Yahoo: {culpables}"


class _Bar2:
    def __init__(self, date, close):
        self.date, self.close = date, close


def _pkt_with_prices(bars, annual):
    from types import SimpleNamespace
    return SimpleNamespace(
        market_data=SimpleNamespace(daily=bars),
        fundamentals={"annual": annual},
    )


def test_historical_multiples_price_each_session_on_earnings_then_known():
    """VAL-ZHIST-035 z-scores today's P/E against its own history. Pricing
    a 2024 session with 2026 earnings would score the multiple against
    results the market could not yet have seen."""
    from wbj.overlay.from_packet import _historical_multiples

    bars = [_Bar2(f"2025-01-{d:02d}", 100.0) for d in range(1, 32)]
    bars += [_Bar2(f"2025-02-{d:02d}", 100.0) for d in range(1, 30)]
    annual = [{"date": "2024-12-31", "net_income": 100.0, "diluted_shares": 10.0},
              {"date": "2025-01-31", "net_income": 200.0, "diluted_shares": 10.0}]
    out = _historical_multiples(_pkt_with_prices(bars, annual))
    # January sessions use the 2024 EPS of 10; February the 2025 EPS of 20.
    assert out[0] == 10.0
    assert out[-1] == 5.0


def test_negative_earnings_are_excluded_from_the_multiple_history():
    """A negative P/E carries no valuation signal and would distort the
    z-score it is compared against."""
    from wbj.overlay.from_packet import _historical_multiples

    bars = [_Bar2(f"2025-03-{d:02d}", 100.0) for d in range(1, 32)]
    bars += [_Bar2(f"2025-04-{d:02d}", 100.0) for d in range(1, 31)]
    annual = [{"date": "2024-12-31", "net_income": -50.0, "diluted_shares": 10.0},
              {"date": "2025-03-31", "net_income": 100.0, "diluted_shares": 10.0}]
    out = _historical_multiples(_pkt_with_prices(bars, annual))
    assert all(m > 0 for m in out)
    # 31 March already sits on the positive fiscal year, plus all 30 of April.
    assert len(out) == 31


def test_too_little_price_history_yields_nothing():
    from wbj.overlay.from_packet import _historical_multiples

    bars = [_Bar2("2025-01-01", 100.0)]
    annual = [{"date": "2024-12-31", "net_income": 100.0, "diluted_shares": 10.0}]
    assert _historical_multiples(_pkt_with_prices(bars, annual)) is None
