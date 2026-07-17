# Financial Analysis Agent - Dataset Contract

## Required fields

| Field | Definition | Type | Unit | Minimum history | Preferred source | Requirement |
| --- | --- | --- | --- | --- | --- | --- |
| income_statement_5y_quarterly | Revenue, gross profit, EBIT, pretax income, taxes, net income, EPS | statements | currency and per-share | 5 annual + 12 quarters | regulatory filings | required |
| balance_sheet_5y_quarterly | Cash, receivables, inventory, current assets/liabilities, debt, equity, assets | statements | currency | 5 annual + 12 quarters | regulatory filings | required |
| cash_flow_statement_5y_quarterly | OCF, capex, acquisitions, debt/equity issuance, dividends, buybacks, SBC | statements | currency | 5 annual + 12 quarters | regulatory filings | required |
| peer_growth_margin_returns | Comparable peer growth, margins, and returns | peer panel | percent | current + 3 years | filings / normalized dataset | required for peer rules |
| organic_growth_bridge | Organic, acquired, FX, and divestiture contributions | series | percent | 8 quarters | issuer reconciliation | conditional |
| market_share_series | Company share under consistent market definition | series | percent | 3 years | industry source | conditional |
| debt_schedule | Debt by instrument, coupon, maturity, secured status, covenants | schedule | currency and rate | current | debt footnote | required |
| lease_schedule | Operating and finance lease obligations | schedule | currency | current | lease footnote | conditional |
| diluted_share_history | Basic and diluted shares, options, RSUs, convertibles | series | shares | 5 years | filings | required |
| wacc | Current weighted average cost of capital or approved adapter hurdle rate | snapshot | percent | analysis date | valuation agent input packet | required for ROIC spread |

## Dataset rules

- All time series must be sorted ascending and free of duplicate timestamps.
- Currency, unit scale, fiscal period, and source locator are mandatory.
- Reported values and estimates must remain separate.
- Minimum history is a requirement for a score, not a suggestion.
- Use `shared/MISSING_DATA_POLICY.md` for unavailable fields.
