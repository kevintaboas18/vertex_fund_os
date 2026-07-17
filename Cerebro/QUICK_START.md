# Quick Start

## 1. Create an analysis packet

Provide:

- ticker, exchange, security type, reporting currency, and analysis timestamp;
- the latest annual and quarterly filings plus at least five fiscal years of statements where available;
- split- and dividend-adjusted daily OHLCV history, preferably three years and at least 252 sessions;
- benchmark and sector-index history for the same dates;
- consensus-estimate history, revisions, earnings dates, sector peers, and market-size evidence;
- capital structure, debt maturities, option/convertible information, diluted shares, tax data, and valuation inputs.

## 2. Run the shared validation layer

Apply, in order:

1. `shared/SOURCE_HIERARCHY.md`
2. `shared/DATA_POLICY.md`
3. `shared/NORMALIZATION_AND_RESTATEMENTS.md`
4. `shared/MISSING_DATA_POLICY.md`
5. `shared/INDUSTRY_ADAPTERS.md`

The packet must be rejected or marked incomplete if timestamps, units, currencies, period definitions, or source references are missing.

## 3. Run the six subagents independently

Do not let one agent see or alter another agent's score until all six specialist outputs are frozen. This prevents valuation from contaminating technical scoring, or price momentum from hiding poor fundamentals.

## 4. Run the main agent

The main agent:

- validates each specialist output;
- computes weighted points;
- applies gates and overrides;
- reconciles technical and valuation reference levels;
- produces the final report and audit appendix.

## 5. Re-run on a defined event

Recompute after a new 10-K/10-Q, earnings release, material estimate revision, financing, acquisition, major legal event, confirmed technical break, or a stale-data threshold breach.
