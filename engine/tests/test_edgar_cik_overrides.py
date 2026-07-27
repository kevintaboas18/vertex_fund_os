"""A declared registrant succession, and the guards that keep it narrow.

When a company reorganises -- typically into a holding company -- the ticker
moves to the new registrant and the XBRL history stays under the predecessor.
SEC's own ticker map is tier 1 in SOURCE_HIERARCHY.md and points at the new
one, so the provider follows it correctly and finds nothing: XOM maps to
ExxonMobil Holdings Corp (CIK 2115436, zero us-gaap tags, no 10-K) while the
438-tag history and the 2026-02-18 10-K sit under Exxon Mobil Corporation
(CIK 34088). Every EDGAR-derived value disappears at once.

`Entradas/cik_overrides.json` declares the substitution. Three things make it a
fact rather than a guess, and each is tested here:

- an entry needs a `source_locator`, because DATA_POLICY.md requires one for
  any value and a substituted registrant without the filing that documents the
  succession is an inference wearing a fact's clothes (XOM's is the Form
  8-K12B, which is specifically the successor-issuer form under Rule 12g-3);
- an override may not shadow a CIK that already reports us-gaap facts;
- it must point at one that does.

Without those last two this is a way to silently redirect any ticker anywhere.
"""

import json

import pytest

from wbj.providers.edgar import EdgarProvider

_TICKERS = {"0": {"ticker": "ZZZ", "cik_str": 111}}


class _FakeCache:
    def get(self, *a, **k):
        return None

    def put(self, *a, **k):
        return None


def _provider(tmp_path, overrides, usgaap: dict[int, bool]):
    """An EdgarProvider whose network calls are stubbed."""
    (tmp_path / "Entradas").mkdir(exist_ok=True)
    if overrides is not None:
        (tmp_path / "Entradas" / "cik_overrides.json").write_text(
            json.dumps(overrides), encoding="utf-8")

    class _P(EdgarProvider):
        def get_json(self, *a, **k):
            return _TICKERS

        def companyfacts(self, cik):
            return {"facts": {"us-gaap": {"Revenues": {}}}} if usgaap.get(cik) else {"facts": {}}

    from types import SimpleNamespace
    settings = SimpleNamespace(reports_dir=str(tmp_path / "Reportes"), cache_dir=str(tmp_path))
    return _P(settings, _FakeCache())


def test_no_override_file_leaves_the_lookup_alone(tmp_path):
    p = _provider(tmp_path, None, {111: True})
    assert p.cik_for("ZZZ") == 111
    assert p.overrides_applied == {}


def test_an_override_applies_when_the_mapped_cik_has_no_usgaap(tmp_path):
    p = _provider(tmp_path, {"ZZZ": {"cik": 222, "source_locator": "8-K12B acc 1-2-3"}},
                  {111: False, 222: True})
    assert p.cik_for("ZZZ") == 222


def test_the_substitution_is_recorded_for_disclosure(tmp_path):
    """Silent redirection is the failure mode; the caller has to be able to
    say which registrant it read and why."""
    p = _provider(tmp_path, {"ZZZ": {"cik": 222, "source_locator": "8-K12B acc 1-2-3",
                                     "reason": "holdco reorganisation"}},
                  {111: False, 222: True})
    p.cik_for("ZZZ")
    applied = p.overrides_applied["ZZZ"]
    assert applied["cik"] == 222
    assert applied["replaced_cik"] == 111
    assert applied["source_locator"] == "8-K12B acc 1-2-3"


def test_an_override_may_not_shadow_a_working_registrant(tmp_path):
    """The guard that keeps this from being a general redirect: if the mapped
    CIK reports facts, the SEC's map stands."""
    p = _provider(tmp_path, {"ZZZ": {"cik": 222, "source_locator": "8-K12B acc 1-2-3"}},
                  {111: True, 222: True})
    assert p.cik_for("ZZZ") == 111
    assert p.overrides_applied == {}


def test_an_override_pointing_nowhere_useful_is_ignored(tmp_path):
    """Swapping one empty registrant for another buys nothing and hides the
    original diagnosis."""
    p = _provider(tmp_path, {"ZZZ": {"cik": 222, "source_locator": "8-K12B acc 1-2-3"}},
                  {111: False, 222: False})
    assert p.cik_for("ZZZ") == 111


def test_an_entry_without_a_source_locator_is_dropped(tmp_path):
    """DATA_POLICY.md requires a stable locator for any value."""
    p = _provider(tmp_path, {"ZZZ": {"cik": 222}}, {111: False, 222: True})
    assert p.cik_for("ZZZ") == 111
    assert p.overrides_applied == {}


def test_a_non_integer_cik_is_dropped(tmp_path):
    p = _provider(tmp_path, {"ZZZ": {"cik": "not a cik", "source_locator": "x"}},
                  {111: False, 222: True})
    assert p.cik_for("ZZZ") == 111


def test_comment_keys_are_not_treated_as_tickers(tmp_path):
    p = _provider(tmp_path, {"_comment_1": "documentation",
                             "ZZZ": {"cik": 222, "source_locator": "x"}},
                  {111: False, 222: True})
    assert p.cik_for("ZZZ") == 222


def test_a_malformed_file_does_not_break_the_lookup(tmp_path):
    (tmp_path / "Entradas").mkdir(exist_ok=True)
    (tmp_path / "Entradas" / "cik_overrides.json").write_text("{not json", encoding="utf-8")
    p = _provider(tmp_path, None, {111: True})
    assert p.cik_for("ZZZ") == 111


def test_an_unlisted_ticker_still_returns_none(tmp_path):
    p = _provider(tmp_path, {"ZZZ": {"cik": 222, "source_locator": "x"}},
                  {111: False, 222: True})
    assert p.cik_for("NOSUCH") is None


# --- the shipped file -------------------------------------------------------

def test_the_shipped_overrides_file_is_well_formed():
    from pathlib import Path

    path = Path(__file__).parent.parent.parent / "Entradas" / "cik_overrides.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = {k: v for k, v in data.items() if not k.startswith("_comment")}
    assert entries, "the file ships with no override at all"
    for ticker, entry in entries.items():
        assert isinstance(entry.get("cik"), int), f"{ticker}: cik must be an int"
        assert entry.get("source_locator"), f"{ticker}: needs the filing that documents it"
        assert entry.get("reason"), f"{ticker}: needs a reason a reader can check"
        assert entry.get("verified"), f"{ticker}: needs a verification date"


def test_the_xom_entry_cites_the_successor_issuer_form():
    """Form 8-K12B is specifically the successor-issuer filing under Rule
    12g-3. A same-ticker name match is not evidence of succession; that form
    is."""
    from pathlib import Path

    path = Path(__file__).parent.parent.parent / "Entradas" / "cik_overrides.json"
    xom = json.loads(path.read_text(encoding="utf-8"))["XOM"]
    assert xom["cik"] == 34088
    assert "8-K12B" in xom["source_locator"]
    assert "0001193125-26-291990" in xom["source_locator"]
