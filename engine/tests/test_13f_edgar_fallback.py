"""13F holders from EDGAR, without the FMP endpoint that returns 402.

`CLAUDE.md`'s mandatory report item 4 asks for recognised funds holding the
company, sourced to 13F. The report recorded that "SEC EDGAR has no
per-company holdings endpoint", and on the paths it had tried that was true: a
13F is filed by the INVESTOR, so a company's own filing history never lists
one -- AAPL's submissions carry zero 13F-HR.

EDGAR full-text search inverts the lookup. It indexes filing CONTENTS, and
every information table names the CUSIP of each position, so searching 13F-HR
for the company's CUSIP returns its holders: free, and tier 1 in
SOURCE_HIERARCHY.md where the FMP endpoint is tier 5.
"""

import pytest

from wbj.providers.edgar import EdgarProvider


class _FakeCache:
    def get(self, *a, **k):
        return None

    def put(self, *a, **k):
        return None


def _hit(name, cik, date, adsh):
    return {"_source": {"display_names": [f"{name}  (CIK {cik})"],
                        "file_date": date, "adsh": adsh, "form": "13F-HR"}}


def _provider(pages):
    """`pages` is a list of hit-lists, served in order."""
    from types import SimpleNamespace

    class _P(EdgarProvider):
        def __init__(self):
            super().__init__(SimpleNamespace(reports_dir="."), _FakeCache())
            self._i = 0

        def get_json(self, url, params, *a, **k):
            page = pages[self._i] if self._i < len(pages) else []
            self._i += 1
            return {"hits": {"hits": page}}

    return _P()


# --- helpers para probar `_ownership` por COMPORTAMIENTO ---------------------
# Los tests que afirmaban sobre `inspect.getsource(...)` se rompian al reordenar
# el modulo aunque el reporte dijera exactamente lo mismo. Estos montan el
# escenario y comprueban lo que el lector acaba viendo.

def _hit_13dg(name, cik="0001104659", date="2024-02-13", adsh="0001104659-24-021596"):
    return {"name": name, "cik": cik, "filing_date": date,
            "accession": adsh, "form": "SC 13G/A"}


def _hit_13f(name, cik="0000046392", date="2026-07-30", adsh="0000046392-26-000004"):
    return {"name": name, "cik": cik, "filing_date": date,
            "accession": adsh, "form": "13F-HR"}


def _run_ownership(monkeypatch, tmp_path, dg, f13):
    """`_ownership` con FMP caido (402, el caso real) y EDGAR simulado."""
    from types import SimpleNamespace
    import wbj.report as rep

    class _FMP:
        def __init__(self, *a, **k): pass
        def institutional_holders(self, t): return None      # 402
        def key_executives(self, t): return []
        def profile(self, t): return [{"cusip": "67066G104"}]

    class _EDGAR:
        def __init__(self, *a, **k): pass
        def cik_for(self, t): return 1045810
        def major_holders_13d_g(self, *a, **k): return list(dg)
        def institutional_holders_13f(self, *a, **k): return list(f13)

    monkeypatch.setattr("wbj.providers.fmp.FMPProvider", _FMP)
    monkeypatch.setattr("wbj.providers.edgar.EdgarProvider", _EDGAR)
    monkeypatch.setattr("wbj.providers.cache.Cache", lambda *a, **k: _FakeCache())
    return rep._ownership("NVDA", SimpleNamespace(cache_dir=tmp_path, reports_dir=tmp_path))


def test_holders_are_extracted_with_name_cik_and_accession():
    p = _provider([[_hit("BERKSHIRE HATHAWAY INC", "0001067983", "2026-02-17", "acc-1")]])
    rows = p.institutional_holders_13f("037833100", "2026-01-01", "2026-12-31", pages=1)
    assert rows == [{"name": "BERKSHIRE HATHAWAY INC", "cik": "0001067983",
                     "filing_date": "2026-02-17", "accession": "acc-1", "form": "13F-HR"}]


def test_a_manager_that_filed_twice_keeps_its_newest():
    """A quarterly filer appears once per quarter; the position report that
    matters is the current one."""
    p = _provider([[_hit("BERKSHIRE HATHAWAY INC", "0001067983", "2026-02-17", "old"),
                    _hit("BERKSHIRE HATHAWAY INC", "0001067983", "2026-05-15", "new")]])
    rows = p.institutional_holders_13f("x", "2026-01-01", "2026-12-31", pages=1)
    assert len(rows) == 1
    assert rows[0]["accession"] == "new"


def test_results_are_newest_first():
    p = _provider([[_hit("A", "1", "2026-02-01", "a"), _hit("B", "2", "2026-07-01", "b")]])
    rows = p.institutional_holders_13f("x", "2026-01-01", "2026-12-31", pages=1)
    assert [r["name"] for r in rows] == ["B", "A"]


def test_paging_accumulates_across_pages():
    p = _provider([[_hit("A", "1", "2026-02-01", "a")],
                   [_hit("B", "2", "2026-03-01", "b")]])
    rows = p.institutional_holders_13f("x", "2026-01-01", "2026-12-31", pages=2)
    assert {r["name"] for r in rows} == {"A", "B"}


def test_an_empty_page_stops_the_scan():
    """No results means no more pages; continuing would spend requests on
    nothing."""
    p = _provider([[_hit("A", "1", "2026-02-01", "a")], [], [_hit("C", "3", "2026-04-01", "c")]])
    rows = p.institutional_holders_13f("x", "2026-01-01", "2026-12-31", pages=3)
    assert {r["name"] for r in rows} == {"A"}


def test_a_hit_without_a_cik_is_dropped():
    """No CIK means no stable identity to dedupe or cite by."""
    p = _provider([[{"_source": {"display_names": ["SOME FUND"], "file_date": "2026-01-01",
                                 "adsh": "x", "form": "13F-HR"}}]])
    assert p.institutional_holders_13f("x", "2026-01-01", "2026-12-31", pages=1) == []


def test_no_hits_returns_empty_not_an_error():
    assert _provider([[]]).institutional_holders_13f(
        "x", "2026-01-01", "2026-12-31", pages=1) == []


# --- how the report uses it -------------------------------------------------

def test_the_report_states_that_the_order_is_not_by_size(monkeypatch, tmp_path):
    """CLAUDE.md asks for RECOGNISED holders. The search index names the filer
    but not the share count, so the list is by filing recency -- and recency is
    not recognition. Saying so es lo que mantiene el item honestamente cumplido.

    Comprueba el TEXTO QUE VE EL LECTOR, no el codigo fuente: la version
    anterior afirmaba `"SC 13D/G" in src` y se rompio al reordenar el modulo
    sin que cambiara nada de lo que el reporte dice."""
    out = _run_ownership(monkeypatch, tmp_path, dg=[], f13=[_hit_13f("Some Manager")])
    src_line = out["holders_source"] or ""
    assert "NOT by position size" in src_line
    assert "recency is not recognition" in src_line


def test_fmp_is_still_preferred_when_available():
    """It carries share counts and values the search index does not."""
    import inspect

    from wbj import report

    src = inspect.getsource(report._ownership)
    assert src.index("fmp.institutional_holders") < src.index("institutional_holders_13f")


# --- SC 13D/G: the "recognised" half of report item 4 -----------------------

def _dg_hit(subject_cik, holder, holder_cik, date, adsh, form="SC 13G/A"):
    return {"_source": {
        "display_names": [f"Subject Co  (CIK {subject_cik})", f"{holder}  (CIK {holder_cik})"],
        "ciks": [subject_cik, holder_cik],
        "file_date": date, "adsh": adsh, "form": form}}


def test_a_schedule_13g_names_the_holder_not_just_the_subject():
    """Unlike a 13F, these hits carry two parties. Taking the first would
    report the issuer as a holder of itself."""
    p = _provider([[_dg_hit("0000320193", "VANGUARD GROUP INC", "0000102909",
                            "2024-02-13", "acc-v")]])
    rows = p.major_holders_13d_g("037833100", 320193, "2020-01-01", "2026-12-31", pages=1)
    assert [r["name"] for r in rows] == ["VANGUARD GROUP INC"]


def test_the_issuer_is_excluded_by_its_own_cik():
    p = _provider([[_dg_hit("0000320193", "BlackRock Inc.", "0001086364",
                            "2024-02-12", "acc-b")]])
    rows = p.major_holders_13d_g("x", 320193, "2020-01-01", "2026-12-31", pages=1)
    assert all("Subject Co" not in r["name"] for r in rows)


def test_both_13d_and_13g_are_searched():
    """13D is the activist filing and 13G the passive one; a 5% holder is
    material either way."""
    import inspect

    src = inspect.getsource(EdgarProvider.major_holders_13d_g)
    for form in ("SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A"):
        assert form in src


def test_a_holder_that_amended_keeps_its_newest_filing():
    p = _provider([[_dg_hit("1", "FUND", "2", "2023-02-14", "old"),
                    _dg_hit("1", "FUND", "2", "2024-02-14", "new")]])
    rows = p.major_holders_13d_g("x", 1, "2020-01-01", "2026-12-31", pages=1)
    assert len(rows) == 1 and rows[0]["accession"] == "new"


def test_recognition_comes_from_the_filing_threshold_not_a_ranking():
    """The docstring has to say why this answers "recognised" where an
    ordered 13F list cannot: a 13D/G exists only above 5%."""
    import inspect

    src = inspect.getsource(EdgarProvider.major_holders_13d_g)
    assert "5%" in src
    assert "Recency is not recognition" in src


def test_the_report_prefers_the_five_percent_set():
    """It answers the requirement by definition; the 13F sweep is the
    fallback when no 13D/G exists."""
    import inspect

    from wbj import report

    src = inspect.getsource(report._ownership)
    assert src.index("major_holders_13d_g") < src.index("institutional_holders_13f")


def test_the_13f_source_line_is_not_credited_for_13dg_names(monkeypatch, tmp_path):
    """Cada bloque de nombres lo produjo un formulario distinto y con una
    definicion distinta. La nota del 13F se AÑADE a la del 13D/G, nunca la pisa:
    atribuir los tres nombres de >5% a un barrido de 13F seria acreditar al
    filing equivocado."""
    out = _run_ownership(monkeypatch, tmp_path,
                         dg=[_hit_13dg("VANGUARD GROUP INC")],
                         f13=[_hit_13f("Some Manager")])
    fuente = out["holders_source"] or ""
    assert "above 5%" in fuente, "la nota del 13D/G sigue presente"
    assert "13F-HR" in fuente, "y la del 13F se añadio"
    # Cada tenedor declara de que formulario salio.
    bases = {h["name"]: h.get("basis") for h in out["holders"]}
    assert bases["VANGUARD GROUP INC"] == "13D/G"
    assert bases["Some Manager"] == "13F"
