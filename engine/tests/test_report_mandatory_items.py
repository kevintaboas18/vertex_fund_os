"""CLAUDE.md's mandatory report items 2 and 5.

Both were specified from the start and neither was rendered: the report
had no insider section at all, and an "avoid" classification named no
concrete revisit event. These lock them in.
"""

from wbj.report import _INSIDER_MATERIAL_USD, _insiders, _next_earnings


class _FakeFMP:
    def __init__(self, trades=None, earnings=None):
        self._t, self._e = trades, earnings

    def insider_trades(self, ticker):
        return self._t

    def earnings_calendar(self, ticker):
        return self._e


def _patch(monkeypatch, fake):
    import wbj.providers.fmp as fmp_mod
    monkeypatch.setattr(fmp_mod, "FMPProvider", lambda *a, **k: fake)


def _trade(name, shares, price, ad="D", form="4", when="2026-06-17"):
    return {"reportingName": name, "securitiesTransacted": shares, "price": price,
            "acquisitionOrDisposition": ad, "formType": form,
            "transactionDate": when, "typeOfOwner": "officer"}


def test_small_trades_are_not_material(monkeypatch, tmp_path):
    """Below $1M in total the SEC still reports it, but CLAUDE.md says it
    does not count as important — it must not reach the report."""
    _patch(monkeypatch, _FakeFMP(trades=[_trade("Small Fry", 10, 100.0)]))
    out = _insiders("X", type("S", (), {"cache_dir": tmp_path})())
    assert out["trades"] == []


def test_repeated_small_sales_aggregate_past_the_threshold(monkeypatch, tmp_path):
    """"Que excedan $1M en total" — six chunks under the line still make a
    material seller, so grouping must happen before thresholding."""
    trades = [_trade("Serial Seller", 3000, 100.0) for _ in range(6)]
    _patch(monkeypatch, _FakeFMP(trades=trades))
    out = _insiders("X", type("S", (), {"cache_dir": tmp_path})())
    assert len(out["trades"]) == 1
    assert out["trades"][0]["value"] == 1_800_000.0
    assert out["sold"] > _INSIDER_MATERIAL_USD


def test_form_3_and_zero_price_rows_are_ignored(monkeypatch, tmp_path):
    """Form 3 is an initial holdings statement, not a transaction, and a
    priceless row carries no dollar value to threshold on."""
    _patch(monkeypatch, _FakeFMP(trades=[
        _trade("Newcomer", 999_999, 500.0, form="3"),
        _trade("Grantee", 999_999, 0.0),
    ]))
    out = _insiders("X", type("S", (), {"cache_dir": tmp_path})())
    assert out["trades"] == []


def test_buys_and_sells_are_reported_separately(monkeypatch, tmp_path):
    _patch(monkeypatch, _FakeFMP(trades=[
        _trade("Buyer", 30_000, 100.0, ad="A"),
        _trade("Seller", 50_000, 100.0, ad="D"),
    ]))
    out = _insiders("X", type("S", (), {"cache_dir": tmp_path})())
    assert out["bought"] == 3_000_000.0
    assert out["sold"] == 5_000_000.0


def test_next_earnings_skips_already_reported_quarters(monkeypatch, tmp_path):
    """The revisit event must be in the future — a quarter with an actual
    EPS has already happened."""
    _patch(monkeypatch, _FakeFMP(earnings=[
        {"date": "2020-01-01", "epsActual": 1.0},
        {"date": "2099-08-26", "epsActual": None},
        {"date": "2099-11-20", "epsActual": None},
    ]))
    assert _next_earnings("X", type("S", (), {"cache_dir": tmp_path})()) == "2099-08-26"


def test_missing_form4_data_is_reported_as_unavailable(monkeypatch, tmp_path):
    _patch(monkeypatch, _FakeFMP(trades=None))
    out = _insiders("X", type("S", (), {"cache_dir": tmp_path})())
    assert out["available"] is False
