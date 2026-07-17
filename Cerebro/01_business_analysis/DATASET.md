# Business Analysis Agent - Dataset Contract

## Required fields

| Field | Definition | Type | Unit | Minimum history | Preferred source | Requirement |
| --- | --- | --- | --- | --- | --- | --- |
| revenue_by_segment_5y | Revenue by product, segment, geography, and major customer | series | currency | 5 fiscal years | 10-K / annual report notes | required |
| recurring_revenue_5y | Contractual or subscription revenue with recurring character | series | currency | 3-5 years | issuer filing / KPI reconciliation | conditional |
| customer_revenue_shares | Revenue share of disclosed major customers | array | percent | latest + history if disclosed | customer-concentration note | required when disclosed |
| gross_operating_margins_10y | Reported and normalized margins | series | percent | 5 years; preferred 10 | financial statements | required |
| ebit_tax_cash_debt_equity_cash_10y | Inputs for NOPAT, invested capital, and ROIC | series | currency | 5 years | filings | required |
| operating_cash_flow_capex_net_income_5y | Inputs for FCF and conversion | series | currency | 5 years | cash-flow statements | required |
| wacc_inputs | Risk-free rate, ERP, beta, debt cost, capital weights, tax rate | snapshot | mixed | as of analysis date | valuation packet | required for spread |
| market_share_company_industry_3y | Company and market sales under one consistent market definition | series | currency or units | 3 years | filing + industry source | conditional |
| retention_churn_cohorts | GRR, NRR, logo churn, repeat purchase, cohort revenue | series | percent | 8 quarters | issuer KPI / validated dataset | conditional |
| customer_economics | ARPU, gross margin, CAC, customer count, retention | series | mixed | 8 quarters | issuer KPI / cohort dataset | conditional |
| capital_allocation_10y | Reinvestment, M&A, dividends, buybacks, equity issuance, SBC | series | currency and shares | 5-10 years | filings | required |
| management_guidance_history | Guidance midpoint and realized result | series | metric-specific | 8 quarters | earnings releases | conditional |
| insider_ownership | Beneficial ownership of directors and executives | snapshot | percent | latest proxy | proxy statement | conditional |
| competitive_evidence | Patents, network scale, licenses, switching costs, cost position | evidence array | not numeric | current | filings / official data | context only |

## Dataset rules

- All time series must be sorted ascending and free of duplicate timestamps.
- Currency, unit scale, fiscal period, and source locator are mandatory.
- Reported values and estimates must remain separate.
- Minimum history is a requirement for a score, not a suggestion.
- Use `shared/MISSING_DATA_POLICY.md` for unavailable fields.
