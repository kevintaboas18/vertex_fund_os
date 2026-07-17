"""Financial Modeling Prep (FMP) provider.

Wraps the FMP v3 REST API: company profile, financial statements
(income/balance/cash flow, annual + quarterly), adjusted daily OHLCV,
peers, analyst estimates, insider trades (Form 4), institutional
holders (13F), and the earnings calendar.

`FMPProvider` is disabled (`available == False`) when no API key is
configured; every public method then returns `None` immediately
without touching the cache or the network. Requests and caching are
delegated to `wbj.providers.base.Provider.get_json` — this module only
builds URLs/params and picks cache keys / max_age_days per data type.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from wbj.providers.base import Provider

BASE_URL = "https://financialmodelingprep.com/api/v3"

# max_age_days per cache key, per task brief:
#   ohlcv_daily/quote 1, analyst_estimates 7, statements 30,
#   profile/peers/holders/insiders 7.
_MAX_AGE_OHLCV = 1
_MAX_AGE_ESTIMATES = 7
_MAX_AGE_STATEMENT = 30
_MAX_AGE_REFERENCE = 7


def _years_ago(d: date, years: int) -> date:
    """Return the date `years` years before `d`, handling Feb 29 safely."""
    try:
        return d.replace(year=d.year - years)
    except ValueError:
        # d is Feb 29 and target year isn't a leap year.
        return d.replace(month=2, day=28, year=d.year - years)


class FMPProvider(Provider):
    """Financial Modeling Prep data provider."""

    @property
    def available(self) -> bool:
        """True iff an FMP API key is configured."""
        return bool(self.settings and getattr(self.settings, "fmp_api_key", None))

    def _params(self, **extra: Any) -> dict[str, Any]:
        params = {"apikey": self.settings.fmp_api_key}
        params.update(extra)
        return params

    def profile(self, t: str) -> list | dict | None:
        """Company profile: name, sector, industry, market cap, etc."""
        if not self.available:
            return None
        return self.get_json(
            f"{BASE_URL}/profile/{t}",
            self._params(),
            "profile",
            t,
            max_age_days=_MAX_AGE_REFERENCE,
        )

    def income_annual(self, t: str, limit: int = 6) -> list | dict | None:
        """Annual income statements, most recent `limit` fiscal years."""
        if not self.available:
            return None
        return self.get_json(
            f"{BASE_URL}/income-statement/{t}",
            self._params(period="annual", limit=limit),
            "income_annual",
            t,
            max_age_days=_MAX_AGE_STATEMENT,
        )

    def income_quarterly(self, t: str, limit: int = 21) -> list | dict | None:
        """Quarterly income statements, most recent `limit` quarters."""
        if not self.available:
            return None
        return self.get_json(
            f"{BASE_URL}/income-statement/{t}",
            self._params(period="quarter", limit=limit),
            "income_quarterly",
            t,
            max_age_days=_MAX_AGE_STATEMENT,
        )

    def balance_annual(self, t: str, limit: int = 6) -> list | dict | None:
        """Annual balance sheet statements, most recent `limit` fiscal years."""
        if not self.available:
            return None
        return self.get_json(
            f"{BASE_URL}/balance-sheet-statement/{t}",
            self._params(period="annual", limit=limit),
            "balance_annual",
            t,
            max_age_days=_MAX_AGE_STATEMENT,
        )

    def balance_quarterly(self, t: str, limit: int = 21) -> list | dict | None:
        """Quarterly balance sheet statements, most recent `limit` quarters."""
        if not self.available:
            return None
        return self.get_json(
            f"{BASE_URL}/balance-sheet-statement/{t}",
            self._params(period="quarter", limit=limit),
            "balance_quarterly",
            t,
            max_age_days=_MAX_AGE_STATEMENT,
        )

    def cashflow_annual(self, t: str, limit: int = 6) -> list | dict | None:
        """Annual cash flow statements, most recent `limit` fiscal years."""
        if not self.available:
            return None
        return self.get_json(
            f"{BASE_URL}/cash-flow-statement/{t}",
            self._params(period="annual", limit=limit),
            "cashflow_annual",
            t,
            max_age_days=_MAX_AGE_STATEMENT,
        )

    def cashflow_quarterly(self, t: str, limit: int = 21) -> list | dict | None:
        """Quarterly cash flow statements, most recent `limit` quarters."""
        if not self.available:
            return None
        return self.get_json(
            f"{BASE_URL}/cash-flow-statement/{t}",
            self._params(period="quarter", limit=limit),
            "cashflow_quarterly",
            t,
            max_age_days=_MAX_AGE_STATEMENT,
        )

    def ohlcv_daily(
        self, t: str, years: int = 3, today: date | None = None
    ) -> list | None:
        """Split/dividend-adjusted daily OHLCV for the past `years` years.

        `today` anchors the `from`/`to` window and must be supplied by the
        caller (e.g. the CLI passes `date.today()`) so this stays
        deterministic under test. Returns the `historical` list from the
        FMP response, or None if unavailable/missing.
        """
        if not self.available:
            return None
        if today is None:
            today = date.today()
        from_date = _years_ago(today, years)
        payload = self.get_json(
            f"{BASE_URL}/historical-price-full/{t}",
            self._params(
                **{"from": from_date.isoformat(), "to": today.isoformat()}
            ),
            "ohlcv_daily",
            t,
            max_age_days=_MAX_AGE_OHLCV,
        )
        if not isinstance(payload, dict):
            return None
        return payload.get("historical")

    def peers(self, t: str) -> list | dict | None:
        """Peer tickers for `t`."""
        if not self.available:
            return None
        return self.get_json(
            f"{BASE_URL}/stock_peers",
            self._params(symbol=t),
            "peers",
            t,
            max_age_days=_MAX_AGE_REFERENCE,
        )

    def analyst_estimates(self, t: str) -> list | dict | None:
        """Analyst revenue/EPS estimates."""
        if not self.available:
            return None
        return self.get_json(
            f"{BASE_URL}/analyst-estimates/{t}",
            self._params(),
            "analyst_estimates",
            t,
            max_age_days=_MAX_AGE_ESTIMATES,
        )

    def insider_trades(self, t: str) -> list | dict | None:
        """SEC Form 4 insider trades, most recent 200."""
        if not self.available:
            return None
        return self.get_json(
            f"{BASE_URL}/insider-trading",
            self._params(symbol=t, limit=200),
            "insider_trades",
            t,
            max_age_days=_MAX_AGE_REFERENCE,
        )

    def institutional_holders(self, t: str) -> list | dict | None:
        """13F institutional holders."""
        if not self.available:
            return None
        return self.get_json(
            f"{BASE_URL}/institutional-holder/{t}",
            self._params(),
            "institutional_holders",
            t,
            max_age_days=_MAX_AGE_REFERENCE,
        )

    def earnings_calendar(self, t: str) -> list | dict | None:
        """Historical earnings calendar (actual vs. estimated EPS/revenue)."""
        if not self.available:
            return None
        return self.get_json(
            f"{BASE_URL}/historical/earning_calendar/{t}",
            self._params(),
            "earnings_calendar",
            t,
            max_age_days=_MAX_AGE_REFERENCE,
        )
