"""SEC EDGAR provider: ticker->CIK lookup, XBRL company facts, filing metadata.

Tier-1 per Cerebro/shared/SOURCE_HIERARCHY.md ("Regulatory filing and filing
acceptance metadata" ranks first). No API key is required — `EdgarProvider`
is always `available`. SEC's fair-access policy requires a descriptive
`User-Agent` identifying the requester on every request
(https://www.sec.gov/os/webmaster-faq#developers); `EDGAR_USER_AGENT` is
sent on every call via `wbj.providers.base.Provider.get_json`'s `headers`
pass-through.

Endpoints:
- `https://www.sec.gov/files/company_tickers.json` — ticker -> CIK map,
  one global payload (not per-ticker), refreshed roughly monthly by SEC,
  so cached for up to 30 days under a fixed global cache entry.
- `https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json` — all
  XBRL (dei/us-gaap/...) facts reported by the company across filings.
  Cached per-CIK for up to 1 day.
- `https://data.sec.gov/submissions/CIK{cik:010d}.json` — filing history
  including `acceptanceDateTime`, used to determine filing recency.
  Cached per-CIK for up to 1 day.
"""

from __future__ import annotations

from wbj.providers.base import Provider

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"

EDGAR_USER_AGENT = "warren-buffett-jr victor@infusioninvestments.com"
_EDGAR_HEADERS = {"User-Agent": EDGAR_USER_AGENT}

# The tickers map is one global, ticker-independent payload, so it is
# cached under a fixed pseudo-ticker rather than the caller's ticker —
# looking up a second ticker must reuse the same cache entry.
_GLOBAL_CACHE_TICKER = "_GLOBAL"

_MAX_AGE_TICKERS = 30
_MAX_AGE_COMPANYFACTS = 1
_MAX_AGE_SUBMISSIONS = 1


def _cik_cache_key(cik: int) -> str:
    return f"CIK{cik:010d}"


class EdgarProvider(Provider):
    """SEC EDGAR data provider (no API key required)."""

    @property
    def available(self) -> bool:
        """Always True — EDGAR requires no API key, only a User-Agent header."""
        return True

    def cik_for(self, ticker: str) -> int | None:
        """Look up the CIK for `ticker` via SEC's company_tickers.json map.

        Returns None if the ticker isn't found or the payload is malformed.
        """
        payload = self.get_json(
            TICKERS_URL,
            {},
            "tickers",
            _GLOBAL_CACHE_TICKER,
            max_age_days=_MAX_AGE_TICKERS,
            headers=_EDGAR_HEADERS,
        )
        if not isinstance(payload, dict):
            return None

        ticker_upper = ticker.upper()
        for entry in payload.values():
            if not isinstance(entry, dict):
                continue
            if str(entry.get("ticker", "")).upper() != ticker_upper:
                continue
            cik = entry.get("cik_str")
            try:
                return int(cik)
            except (TypeError, ValueError):
                return None
        return None

    def companyfacts(self, cik: int) -> dict | None:
        """Fetch all XBRL company facts (dei/us-gaap/...) for `cik`."""
        payload = self.get_json(
            COMPANYFACTS_URL.format(cik=cik),
            {},
            "companyfacts",
            _cik_cache_key(cik),
            max_age_days=_MAX_AGE_COMPANYFACTS,
            headers=_EDGAR_HEADERS,
        )
        return payload if isinstance(payload, dict) else None

    def filing_acceptance_times(self, cik: int) -> list[dict] | None:
        """Return recent filings' form/acceptanceDateTime/accessionNumber.

        Derived from `https://data.sec.gov/submissions/CIK{cik}.json`'s
        `filings.recent` arrays. Returns None if the payload is malformed
        or lacks the expected `filings.recent` structure.
        """
        payload = self.get_json(
            SUBMISSIONS_URL.format(cik=cik),
            {},
            "submissions",
            _cik_cache_key(cik),
            max_age_days=_MAX_AGE_SUBMISSIONS,
            headers=_EDGAR_HEADERS,
        )
        if not isinstance(payload, dict):
            return None

        recent = payload.get("filings", {}).get("recent")
        if not isinstance(recent, dict):
            return None

        forms = recent.get("form", [])
        accept_times = recent.get("acceptanceDateTime", [])
        accession_numbers = recent.get("accessionNumber", [])

        return [
            {
                "form": form,
                "acceptanceDateTime": accepted,
                "accessionNumber": accession,
            }
            for form, accepted, accession in zip(
                forms, accept_times, accession_numbers, strict=False
            )
        ]
