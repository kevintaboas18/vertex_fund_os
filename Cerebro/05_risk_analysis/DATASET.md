# Risk and Resilience Analysis Agent - Dataset Contract

## Required fields

| Field | Definition | Type | Unit | Minimum history | Preferred source | Requirement |
| --- | --- | --- | --- | --- | --- | --- |
| adjusted_returns | Daily stock, benchmark, and sector total returns | time series | decimal | 756 sessions preferred | validated market data | required |
| financial_risk_inputs | Debt, cash, EBITDA, EBIT, interest, FCF, leases, covenants | series/schedule | currency | 5 years + current maturities | filings | required |
| cash_burn_and_commitments | Cash burn, capex commitments, purchase obligations, available facilities | series/schedule | currency | 8 quarters + forward schedule | filings | conditional |
| concentration_data | Customer, product, geography, supplier, channel concentrations | arrays | percent | 3 years | filing notes | required when disclosed |
| earnings_quality_inputs | Receivables, inventory, depreciation, SG&A, leverage, accruals | series | currency | 2-5 years | filings | required for forensic diagnostics |
| regulatory_legal_events | Material litigation, investigations, approvals, compliance obligations | event registry | mixed | current | regulatory filings / court records | required |
| macro_sensitivity_series | Revenue/margin/return exposure to rates, FX, commodities, GDP or other drivers | series | mixed | 5 years | filings + market data | conditional |
| short_interest_borrow_options | Short interest, borrow cost, options-implied volatility/skew | series | percent | 63 sessions | market data | optional context |

## Dataset rules

- All time series must be sorted ascending and free of duplicate timestamps.
- Currency, unit scale, fiscal period, and source locator are mandatory.
- Reported values and estimates must remain separate.
- Minimum history is a requirement for a score, not a suggestion.
- Use `shared/MISSING_DATA_POLICY.md` for unavailable fields.
