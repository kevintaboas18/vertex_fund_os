"""Builds `NVDA_packet.json` — THE golden fixture every wbj specialist task
(14-19) tests against — from the provider fixtures under `engine/tests/
fixtures/{fmp,edgar,finnhub,fred}/` plus a programmatically generated
600-session OHLCV history.

Also the canonical home of the fake provider objects used by
`engine/tests/packet/conftest.py`: simple classes matching the public
method surface of `wbj.providers.{fmp,edgar,finnhub,fred}` that return
canned fixture payloads instead of hitting the network. No MockTransport
needed at the packet-builder layer (Task 10 design decision) — the real
`Provider.get_json` transport plumbing is already covered by the task 7-9
provider test suites.

Run directly to (re)write `NVDA_packet.json`:

    cd engine && .venv/bin/python tests/fixtures/packet/make_packet_fixture.py

Golden-stability: this script pins `FIXED_NOW` so the committed JSON is
reproducible — rerunning it against unchanged fixtures must reproduce an
identical `packet_hash`.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

_THIS_DIR = Path(__file__).parent
_FIXTURES_DIR = _THIS_DIR.parent  # engine/tests/fixtures/
_ENGINE_DIR = _FIXTURES_DIR.parent.parent  # engine/

# So `import wbj...` and `from tests...` work when this file is executed
# directly (`python make_packet_fixture.py`) as well as when imported by
# conftest.py under pytest.
if str(_ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(_ENGINE_DIR))

from wbj.core.nullstates import Value  # noqa: E402
from wbj.packet.builder import Providers, build_packet  # noqa: E402

TICKER = "NVDA"

# Frozen analysis clock for the golden fixture and for hash-stability tests
# that need a deterministic `now`. Chosen to sit one day after the
# synthetic OHLCV history's last session so `daily_market` staleness reads
# FRESH, while the (real, historical) fundamentals/estimates/holders
# fixture dates are naturally well past their staleness thresholds —
# see the builder's staleness derivation for why that's expected rather
# than a bug.
FIXED_NOW = datetime(2026, 7, 16, 21, 0, 0, tzinfo=timezone.utc)

DEFAULT_OHLCV_SESSIONS = 600


def _load_fixture(provider: str, name: str):
    return json.loads((_FIXTURES_DIR / provider / f"{name}.json").read_text())


def generate_ohlcv_sessions(
    end: date, sessions: int = DEFAULT_OHLCV_SESSIONS, start_price: float = 45.0
) -> list[dict]:
    """Synthesize `sessions` weekday OHLCV bars ending on or before `end`.

    Deterministic (no randomness): a fixed, mildly-oscillating upward drift
    produces a plausible-looking daily bar series without hand-writing
    hundreds of JSON rows. Returned newest-first, matching
    `FMPProvider.ohlcv_daily`'s `historical` list ordering.
    """
    dates: list[date] = []
    cursor = end
    while len(dates) < sessions:
        if cursor.weekday() < 5:  # Mon-Fri
            dates.append(cursor)
        cursor -= timedelta(days=1)
    dates.reverse()  # oldest first, to build a monotonic session index

    bars: list[dict] = []
    price = start_price
    for i, d in enumerate(dates):
        # Deterministic wiggle (period-7 zigzag) around a gentle uptrend.
        drift = 0.0009 + 0.00025 * ((i % 7) - 3)
        price = max(1.0, price * (1 + drift))
        close = round(price, 2)
        open_ = round(close * 0.996, 2)
        high = round(max(open_, close) * 1.008, 2)
        low = round(min(open_, close) * 0.992, 2)
        volume = 150_000_000 + (i % 11) * 1_000_000
        bars.append(
            {
                "date": d.isoformat(),
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "adjClose": close,
                "volume": volume,
            }
        )
    bars.reverse()  # newest first
    return bars


class FakeFMPProvider:
    """Fake `wbj.providers.fmp.FMPProvider`: same method surface, reads
    fixture JSON (or a per-test `overrides` payload) instead of the network.
    """

    def __init__(self, ohlcv: list[dict] | None = None, overrides: dict | None = None):
        self._ohlcv = ohlcv if ohlcv is not None else []
        self._overrides = overrides or {}

    def _get(self, name: str):
        if name in self._overrides:
            return self._overrides[name]
        return _load_fixture("fmp", name)

    def profile(self, t):
        return self._get("profile")

    def income_annual(self, t, limit=6):
        return self._get("income_annual")

    def income_quarterly(self, t, limit=21):
        return self._get("income_quarterly")

    def balance_annual(self, t, limit=6):
        return self._get("balance_annual")

    def balance_quarterly(self, t, limit=21):
        return self._get("balance_quarterly")

    def cashflow_annual(self, t, limit=6):
        return self._get("cashflow_annual")

    def cashflow_quarterly(self, t, limit=21):
        return self._get("cashflow_quarterly")

    def ohlcv_daily(self, t, years=3, today=None):
        return self._overrides.get("ohlcv_daily", self._ohlcv)

    def peers(self, t):
        return self._get("peers")

    def analyst_estimates(self, t):
        return self._get("analyst_estimates")

    def insider_trades(self, t):
        return self._get("insider_trades")

    def institutional_holders(self, t):
        return self._get("institutional_holders")

    def earnings_calendar(self, t):
        return self._get("earnings_calendar")


class FakeEdgarProvider:
    """Fake `wbj.providers.edgar.EdgarProvider`."""

    def __init__(self, companyfacts: dict | None = None, cik: int | None = 1045810):
        self._companyfacts = (
            companyfacts if companyfacts is not None else _load_fixture("edgar", "companyfacts_sample")
        )
        self._cik = cik

    def cik_for(self, ticker):
        return self._cik

    def companyfacts(self, cik):
        return self._companyfacts

    def filing_acceptance_times(self, cik):
        return []


class FakeFinnhubProvider:
    """Fake `wbj.providers.finnhub.FinnhubProvider`."""

    def __init__(self, overrides: dict | None = None):
        self._overrides = overrides or {}

    def _get(self, name):
        if name in self._overrides:
            return self._overrides[name]
        return _load_fixture("finnhub", name)

    def estimates(self, t):
        return self._get("eps_estimate")

    def revenue_estimates(self, t):
        return self._get("revenue_estimate")

    def earnings_calendar(self, t):
        return self._get("earnings_calendar")

    def quote(self, t):
        return self._get("quote")


class FakeFredProvider:
    """Fake `wbj.providers.fred.FredProvider`."""

    def __init__(self, rate: float | None = 0.0425):
        self._rate = rate

    def series(self, series_id, limit=120):
        return _load_fixture("fred", "dgs10")

    def risk_free_rate(self) -> Value:
        if self._rate is None:
            from wbj.core.nullstates import NullState

            return Value.null(NullState.MISSING, unit="ratio", source_name="FRED")
        return Value.of(self._rate, unit="ratio", source_name="FRED", source_locator="DGS10")


def make_default_providers(now: datetime = FIXED_NOW) -> Providers:
    """The full, coherent NVDA scenario used for the golden fixture and for
    the packet-builder happy-path tests."""
    ohlcv = generate_ohlcv_sessions(end=(now - timedelta(days=1)).date())
    return Providers(
        fmp=FakeFMPProvider(ohlcv=ohlcv),
        edgar=FakeEdgarProvider(),
        finnhub=FakeFinnhubProvider(),
        fred=FakeFredProvider(),
    )


def build_golden_packet():
    providers = make_default_providers(FIXED_NOW)
    return build_packet(TICKER, providers, FIXED_NOW)


def main() -> None:
    packet = build_golden_packet()
    out_path = _THIS_DIR / "NVDA_packet.json"
    out_path.write_text(json.dumps(packet.model_dump(mode="json"), indent=2, sort_keys=True) + "\n")
    print(f"wrote {out_path} (packet_hash={packet.packet_hash})")


if __name__ == "__main__":
    main()
