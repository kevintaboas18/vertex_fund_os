# Quick FMP Scoring — Light up Market, Technical & Valuation in the quick scorecard

**Date:** 2026-07-17
**Status:** Implemented (Phase 1 of a two-phase effort) — 2026-07-17

> Implementation note: live FMP `/stable/analyst-estimates` returns
> `revenueAvg` / `numAnalystsRevenue` (not the legacy `estimatedRevenueAvg` /
> `numberAnalystEstimatedRevenue` this spec's fixtures assumed). `quick.py`
> reads both spellings (live first); the fixture was updated to the `/stable/`
> shape. Verified end-to-end against live NVDA and AAPL — all six categories
> light up (100/100 evidence points).

## Problem

The web dashboard (`engine/scripts/webapp.py`) renders the **quick scorecard**
(`engine/wbj/quick.py`). Three of the six categories always show **N/S**:

- **Market & Growth**
- **Technical & Momentum**
- **Valuation**

This is not a bug — the quick packet (`_build_packet` in `engine/wbj/cli.py`) is
built **only from SEC EDGAR fundamentals**. The three categories are hard-coded
as `not_scorable` because the data they need (analyst consensus, price history,
market price) was never fetched:

```python
_NS_REASON = {
    "market":    "needs consensus estimates (engine pending)",
    "technical": "needs price history (engine pending)",
    "valuation": "needs market price (engine pending)",
}
```

The FMP API key **is** configured (`API/.env` → `FMP_API_KEY`) and `FMPProvider`
already exposes every endpoint required (`analyst_estimates`, `ohlcv_daily`,
`profile`). Nothing wires them into the quick packet yet.

## Cerebro rule (non-negotiable)

> Sin evidencia, no hay número.

When FMP is unavailable (no key, rate-limited, or no coverage for the ticker),
the affected category **stays N/S** — never an invented or imputed number.
Coverage and score remain separate concerns.

## Two-phase plan

- **Phase 1 (this spec):** Quick FMP scoring. Fetch FMP data into the packet and
  score the three categories with simple `anchor_score` bands, in the same
  honest "(quick)" style as the current Business/Financial/Risk quick scores.
  Fixes the screenshot; all six categories light up when data exists.
- **Phase 2 (later, separate spec):** Wire FMP into the full specialist engines
  (`market.py` / `technical.py` / `valuation.py`) via the full `Packet` schema
  (FCFF/DCF, anchored VWAP, consensus revisions, Monte Carlo). Out of scope here.

## Design (Phase 1)

### Data flow

`_build_packet(ticker)` currently fetches EDGAR company facts plus
`fmp.profile`. Add a `market_data` block, populated only when `fmp.available`:

```python
packet["market_data"] = {
    "price":     <profile price>,          # float | None
    "market_cap": <profile mktCap>,        # float | None
    "ohlcv":     fmp.ohlcv_daily(ticker, years=1, today=date.today()),  # list | None
    "estimates": fmp.analyst_estimates(ticker),                          # list | None
}
```

- `ohlcv_daily` requires an explicit `today` (deterministic under test) — the CLI
  passes `date.today()`.
- If FMP is off, `market_data` is absent (or its sub-keys are `None`). Each
  sub-key missing ⇒ the dependent category stays N/S.

### Scoring (added to `engine/wbj/quick.py`)

Same pattern as the existing quick categories: `Value` → `anchor_score` →
`Dimension` → `Category`. New anchor tables sit alongside the existing `_A_*`
constants.

| Category | Metrics (equal-weighted) | Source | Stays N/S when |
|---|---|---|---|
| **Market & Growth** | (1) forward revenue growth = next-FY consensus `estimatedRevenueAvg` vs latest EDGAR actual revenue; (2) estimate breadth = `numberAnalystEstimatedRevenue` | `estimates` + EDGAR `revenue` | `estimates` is empty/None, or no forward-dated row |
| **Technical & Momentum** | (1) price vs 50-day SMA; (2) price vs 200-day SMA; (3) 6-month price momentum; (4) % off 52-week high | `ohlcv` (adjusted close) | fewer than ~200 trading days of history |
| **Valuation** | (1) P/E = price ÷ diluted EPS; (2) P/FCF = market_cap ÷ FCF | `market_data.price` / `market_cap` + EDGAR (net income, diluted shares, OCF, capex) | no price, or EPS/FCF ≤ 0 (band-dependent) |

Notes:
- **Forward growth** uses the nearest future-dated estimate row
  (`date` > packet `as_of`); if only past estimates exist, Market stays N/S.
- **EPS** = latest net income ÷ latest diluted shares (both already in the
  packet's `annual` block). If EPS ≤ 0, P/E is not meaningful (a negative P/E
  says nothing about cheapness), so the P/E metric is **dropped** and Valuation
  scores on P/FCF alone. Likewise if FCF ≤ 0, P/FCF is dropped. If **both** are
  non-meaningful, Valuation is N/S. A valid price is required either way.
- **EV/EBIT is intentionally dropped** from the originally proposed set: it needs
  a cash-and-equivalents EDGAR tag the packet does not fetch. Deferred to
  Phase 2. P/E + P/FCF cover Valuation honestly without a new EDGAR tag.

### Anchor bands (initial, tunable)

Illustrative starting points — refined against real tickers during
implementation, kept in `quick.py` next to the existing `_A_*` tables:

- Forward rev growth: `[(-0.10, 0), (0.0, 3), (0.10, 6), (0.25, 9), (0.40, 10)]`
  (reuses the shape of `_A_REV_GROWTH`).
- Analyst breadth: `[(0, 0), (3, 4), (10, 7), (25, 10)]`.
- Price vs SMA (ratio price/SMA): `[(0.85, 2), (1.0, 5), (1.10, 8), (1.25, 10)]`.
- 6-mo momentum (return): `[(-0.20, 0), (0.0, 4), (0.15, 7), (0.40, 10)]`.
- % off 52-wk high (0 = at high): `[(-0.40, 2), (-0.20, 5), (-0.08, 8), (0.0, 10)]`.
- P/E (lower better): `[(10, 10), (18, 8), (28, 5), (45, 2), (70, 0)]`.
- P/FCF (lower better): `[(12, 10), (22, 8), (35, 5), (55, 2), (90, 0)]`.

### N/S reason strings

Replace the "(engine pending)" placeholders with truthful reasons for when data
is genuinely absent:

```python
_NS_REASON = {
    "market":    "sin cobertura de analistas (FMP)",
    "technical": "historial de precio insuficiente (FMP)",
    "valuation": "sin precio de mercado (FMP)",
}
```

### Labels

Stay honest — the three new scores are still *quick*, not the full methodology:

```
Market & Growth (quick)
Technical & Momentum (quick)
Valuation (quick)
```

### No webapp changes

`webapp.py` already renders each category as scored-vs-N/S based on the
scorecard rows. Once the rows carry real scores, the card fills in with no
frontend change.

## Components & boundaries

- **`_build_packet` (cli.py)** — the only place that touches the network; adds
  the `market_data` block. Pure data assembly, no scoring.
- **`quick.py`** — pure functions over the packet dict. New helpers:
  `_forward_growth(estimates, rev, as_of)`, `_sma(closes, n)`,
  `_momentum(closes, lookback)`, `_off_high(closes)`, plus the three new
  `Category` builders. No I/O, fully unit-testable from fixtures.
- **Interface between them** — the `market_data` dict shape above. `quick.py`
  reads it defensively (`packet.get("market_data") or {}`).

## Error handling

- Missing FMP key / disabled provider → `market_data` absent → three categories
  N/S with the truthful reason. Business/Financial/Risk unaffected.
- Malformed / partial FMP payloads → each metric guards its own inputs
  (`None`/empty → that metric is a null `Value`); a category with zero covered
  metrics reports `coverage == 0` and renders N/S via the existing
  `score10 is None` path.
- No exceptions bubble to the webapp: same defensive style as the current quick
  helpers (`_latest`, `_ratio` return `None` rather than raise).

## Testing

Extend `engine/tests/test_quick.py` using existing fixtures
(`tests/fixtures/fmp/analyst_estimates.json`, `ohlcv_daily.json`, `profile.json`):

For each of Market, Technical, Valuation:
1. **Scores with data** — packet including a `market_data` block built from the
   fixtures produces a numeric `score10` and `coverage > 0`.
2. **N/S without data** — packet with `market_data` absent (or the relevant
   sub-key `None`) keeps the category `status == "not_scorable"` with the new
   reason string, and does not contribute to `overall_10`.

Plus a regression test that Business/Financial/Risk scores are unchanged by the
new code path.

The `ohlcv_daily.json` fixture currently holds only 3 rows; the Technical test
needs ≥200 rows to exercise the SMA/momentum path. Implementation will add a
synthetic ≥200-row OHLCV fixture (or generate one in-test) rather than relying
on the 3-row sample.

## Out of scope

- Full specialist engines (Phase 2).
- EV/EBIT and any new EDGAR tags.
- FinnHub / FRED wiring.
- Caching changes (existing `Provider.get_json` max-age policy is reused as-is).
