# Market and Growth Analysis Agent - Dataset Contract

## Required fields

| Field | Definition | Type | Unit | Minimum history | Preferred source | Requirement |
| --- | --- | --- | --- | --- | --- | --- |
| tam_sam_som_sources | Market size, scope, geography, product definition, and forecast | evidence + series | currency or units | current + 3 forecast years | government / industry primary source | required |
| industry_revenue_history | Historical market revenue/units under same definition | series | currency or units | 5 years | industry source | required |
| company_relevant_revenue | Company revenue mapped to TAM definition | series | currency | 3-5 years | segment filings | required |
| peer_market_shares | Company and peer shares under one denominator | panel | percent | 3 years | industry source | conditional |
| consensus_estimates_history | Timestamped revenue/EPS/EBITDA estimates and revisions | panel | currency/per share | 90 days + 8 quarters | consensus provider | required |
| actual_surprises | Reported actual versus pre-release consensus | series | percent | 8 quarters | consensus snapshot + filing | required |
| backlog_rpo_bookings | Backlog, remaining performance obligations, bookings, or orders | series | currency | 8 quarters | filings / issuer KPI | conditional |
| unit_economics | Customer growth, ARPU, churn, NRR, CAC, gross margin | series | mixed | 8 quarters | issuer KPI / validated dataset | conditional |
| catalyst_registry | Product, capacity, regulatory, contract, pricing, and launch events | event array | mixed | forward 24 months | official issuer/regulatory evidence | required |
| sector_breadth_and_relative_strength | Sector member breadth and returns | market series | percent | 252 sessions | market data | conditional |
| roic_reinvestment | ROIC and reinvestment rate from Business/Financial packets | series | percent | 3-5 years | validated specialist packet | required |

## Dataset rules

- All time series must be sorted ascending and free of duplicate timestamps.
- Currency, unit scale, fiscal period, and source locator are mandatory.
- Reported values and estimates must remain separate.
- Minimum history is a requirement for a score, not a suggestion.
- Use `shared/MISSING_DATA_POLICY.md` for unavailable fields.
