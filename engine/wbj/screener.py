"""Discovery screener: surface well-scoring companies the user may not know.

Two-stage pipeline (cheap prefilter first, so it stays fast):

1. PREFILTER — SEC XBRL "frames" API: revenue (this + prior year) and net
   income for EVERY calendar-year filer in ~4 requests. Filter to mid-size
   ($0.8B-$30B revenue: big enough to be real, small enough to be
   unfamiliar), profitable (net margin > 8%) and growing (> 5%).
2. SCORE — quick 6-agent scorecard (EDGAR companyfacts) only for the top
   candidates by prefilter strength, then rank by overall score.

Output is a research classification list — never a buy order.
Note: frames cover calendar-FY filers; companies with offset fiscal years
(e.g. AAPL) are simply not candidates here, which is fine for discovery.
"""

from __future__ import annotations

_FRAMES_URL = "https://data.sec.gov/api/xbrl/frames/us-gaap/{tag}/USD/CY{year}.json"
_REV_TAGS = ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues"]
_NI_TAG = "NetIncomeLoss"

# Prefilter bounds — tuned for "good but probably unfamiliar"
REV_MIN, REV_MAX = 0.8e9, 30e9
MARGIN_MIN = 0.08
GROWTH_MIN = 0.05
SHORTLIST = 40  # candidates that get the full scorecard


def _frame_rows(edgar, tag: str, year: int) -> dict[int, float]:
    """cik -> value for one us-gaap tag/year frame (cached via provider)."""
    from wbj.providers.edgar import _EDGAR_HEADERS, _GLOBAL_CACHE_TICKER

    payload = edgar.get_json(
        _FRAMES_URL.format(tag=tag, year=year), {},
        f"frame_{tag}_{year}", _GLOBAL_CACHE_TICKER,
        max_age_days=7, headers=_EDGAR_HEADERS,
    )
    if not isinstance(payload, dict):
        return {}
    out: dict[int, float] = {}
    for r in payload.get("data", []):
        cik, val = r.get("cik"), r.get("val")
        if isinstance(cik, int) and isinstance(val, (int, float)):
            out[cik] = float(val)  # frames are deduped per cik by the SEC
    return out


def _revenue_frame(edgar, year: int) -> dict[int, float]:
    """Merge revenue tags (taxonomy drift): first tag wins per cik."""
    merged: dict[int, float] = {}
    for tag in _REV_TAGS:
        for cik, val in _frame_rows(edgar, tag, year).items():
            merged.setdefault(cik, val)
    return merged


def prefilter(edgar, year: int) -> list[dict]:
    """Cheap fundamental filter over every calendar-year filer."""
    rev_now = _revenue_frame(edgar, year)
    if not rev_now:  # frame not published yet -> fall back one year
        year -= 1
        rev_now = _revenue_frame(edgar, year)
    rev_prev = _revenue_frame(edgar, year - 1)
    ni_now = _frame_rows(edgar, _NI_TAG, year)

    rows = []
    for cik, rev in rev_now.items():
        prev, ni = rev_prev.get(cik), ni_now.get(cik)
        if prev is None or ni is None or not (REV_MIN <= rev <= REV_MAX) or prev <= 0:
            continue
        growth, margin = rev / prev - 1, ni / rev
        if margin < MARGIN_MIN or growth < GROWTH_MIN:
            continue
        rows.append({
            "cik": cik, "revenue": rev, "growth": growth, "margin": margin,
            # prefilter strength: balanced growth + profitability
            "pre_score": min(growth, 0.40) + min(margin, 0.40),
        })
    rows.sort(key=lambda r: r["pre_score"], reverse=True)
    return rows


def screen(limit: int = 15, progress=None) -> list[dict]:
    """Full pipeline: prefilter -> quick scorecard on shortlist -> rank."""
    from datetime import date

    from wbj.cli import _build_packet, _providers
    from wbj.quick import quick_scorecard
    from wbj.targets import live_price, price_targets

    settings, edgar, _fmp = _providers()
    year = date.today().year - 1  # latest complete calendar year
    candidates = prefilter(edgar, year)

    # cik -> ticker map (exclude non-common listings without a ticker)
    from wbj.providers.edgar import _EDGAR_HEADERS, _GLOBAL_CACHE_TICKER, _MAX_AGE_TICKERS, TICKERS_URL
    tickers = edgar.get_json(TICKERS_URL, {}, "tickers", _GLOBAL_CACHE_TICKER,
                             max_age_days=_MAX_AGE_TICKERS, headers=_EDGAR_HEADERS) or {}
    by_cik = {e["cik_str"]: e for e in tickers.values() if isinstance(e, dict)}

    results = []
    scored = 0
    for cand in candidates:
        if scored >= SHORTLIST:
            break
        entry = by_cik.get(cand["cik"])
        if entry is None:
            continue
        ticker = entry["ticker"]
        try:
            packet = _build_packet(ticker)
            sc = quick_scorecard(packet)
        except Exception:
            continue  # unanalyzable filer -> skip, keep screening
        if sc["overall_10"] is None:
            continue
        scored += 1
        if progress:
            progress(scored, SHORTLIST, ticker)
        price = live_price(ticker, fmp_api_key=settings.fmp_api_key)
        targets = price_targets(packet, price)
        base = next((s for s in targets.get("scenarios", []) if s["key"] == "base"), None)
        results.append({
            "ticker": ticker,
            "name": entry.get("title", packet.get("entity", "")).title(),
            "revenue": cand["revenue"],
            "growth": round(cand["growth"], 4),
            "margin": round(cand["margin"], 4),
            "score10": sc["overall_10"],
            "evidence": sc["evidence_points_covered"],
            "price": targets.get("price"),
            "target_base": base["target"] if base else None,
            "upside_base": base["upside"] if base else None,
        })

    results.sort(key=lambda r: r["score10"], reverse=True)
    return results[:limit]
