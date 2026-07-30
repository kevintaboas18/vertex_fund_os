# Valuation Analysis Agent - Dataset Contract

## Required fields

| Field | Definition | Type | Unit | Minimum history | Preferred source | Requirement |
| --- | --- | --- | --- | --- | --- | --- |
| normalized_historical_financials | Revenue, margins, taxes, D&A, capex, NWC, FCF, ROIC after normalization | series | currency/percent | 5-10 years | validated filings | required |
| segment_financials | Segment revenue, margins, capital intensity, and growth | series | currency/percent | 3-5 years | filing segments | conditional for SOTP |
| capital_structure | Market equity, debt, leases, preferred, minority interest, cash, non-operating assets | snapshot/schedule | currency | analysis date | filings + market data | required |
| share_claims | Basic shares, options, RSUs, convertibles, warrants, contingencies | schedule | shares/currency | analysis date | filings | required |
| discount_rate_inputs | Risk-free rate, ERP, country risk, bottom-up beta, debt cost, tax rate | snapshot | percent | analysis date | official market + peer data | required |
| forecast_drivers | Units/users, pricing, share, revenue, margins, reinvestment, ROIC, taxes | scenario series | mixed | 5-10 forecast years | explicit model assumptions | required |
| peer_multiples_and_fundamentals | EV/revenue, EV/EBITDA, P/E, P/B, growth, margins, risk, ROIC | peer panel | multiples/percent | current + history | validated market/fundamental data | required |
| historical_valuation | Own valuation multiples and yields through a cycle | series | multiple/percent | 5-10 years | market + filing data | required |
| scenario_probabilities | Bear/base/bull or discrete outcome probabilities | assumption array | probability | current model | explicit assumptions | required |
| current_price | Adjusted current market price at market timestamp | snapshot | currency/share | current | validated market data | required |

## Dataset rules

- All time series must be sorted ascending and free of duplicate timestamps.
- Currency, unit scale, fiscal period, and source locator are mandatory.
- Reported values and estimates must remain separate.
- Minimum history is a requirement for a score, not a suggestion.
- Use `shared/MISSING_DATA_POLICY.md` for unavailable fields.
