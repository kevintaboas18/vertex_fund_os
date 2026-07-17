# Market and Growth Analysis Agent - Formula Registry

| ID | Metric | Formula | Required inputs | Frequency | Rules / caveats |
| --- | --- | --- | --- | --- | --- |
| MKT-TAM-001 | TAM | sum(addressable customers_i * annual spend_i) or validated top-down market revenue | market units/spend or primary estimate | annual | State scope, geography, year, and source tier; never mix definitions. |
| MKT-SAM-002 | Serviceable available market | TAM * eligible geography share * eligible product share * reachable customer share | TAM and explicit eligibility factors | annual | Each factor is an assumption or sourced statistic. |
| MKT-SOM-003 | Serviceable obtainable market | SAM * defensible target share | SAM, target share assumption | scenario | Target share is an assumption and must be scenario-specific. |
| MKT-CAGR-004 | TAM CAGR | (TAM_end / TAM_begin)^(1/n) - 1 | market size start/end | 3y / 5y | Use constant currency where possible. |
| MKT-PEN-005 | Company penetration | Company relevant revenue / TAM under same definition | company revenue, TAM | annual | Do not divide total company revenue by a narrow TAM. |
| MKT-SHARE-006 | Market share | Company relevant sales / Total market sales | company and market sales | annual / quarterly | Use consistent units and geography. |
| MKT-SHDELTA-007 | Market-share change | Market share_t - Market share_t-1 | share history | annual | Report percentage points, not percent change. |
| MKT-HHI-008 | Industry HHI | sum(competitor_share_i^2) | competitor shares | annual | Shares as decimals; residual market must be represented or result is a lower bound. |
| MKT-GCAP-009 | Fundamental growth capacity | Reinvestment rate * ROIC | reinvestment rate, ROIC | annual | Consistency ceiling for internally financed growth, not a forecast guarantee. |
| MKT-RUN-010 | Runway years to target share | ln(Target revenue / Current revenue) / ln(1 + Assumed growth) | current/target revenue, growth assumption | scenario | Not meaningful when assumed growth <=0; cap target by TAM. |
| MKT-REVBR-011 | Positive revision breadth | Number of upward estimate revisions / Total revisions | analyst revision counts | 30d / 90d | Minimum 5 active estimates; otherwise low confidence. |
| MKT-REVMAG-012 | Net revision magnitude | (Current consensus - Prior consensus) / abs(Prior consensus) | timestamped consensus | 30d / 90d | Calculate separately for revenue and EPS; sign-change handling required. |
| MKT-DISP-013 | Estimate dispersion | stdev(analyst estimates) / abs(consensus mean) | individual estimates | current | Higher dispersion lowers forecast confidence. |
| MKT-SURP-014 | Earnings/revenue surprise | (Actual - Pre-release consensus) / abs(Pre-release consensus) | actual and frozen consensus | quarterly | Freeze consensus before release; handle sign changes. |
| MKT-BACK-015 | Backlog or RPO growth | (Backlog_t - Backlog_t-1) / Backlog_t-1 | backlog/RPO history | quarterly | Check cancellation, duration, and acquisition effects. |
| MKT-COVER-016 | Revenue coverage | Next-12m contracted backlog or RPO / Next-12m revenue estimate | contracted amount, NTM revenue | quarterly | Only use convertible/recognized portion; disclose duration. |
| MKT-OPLEV-017 | Operating leverage | Percent change in operating income / Percent change in revenue | operating income and revenue | annual / TTM | Not meaningful across loss sign change; use incremental margin instead. |
| MKT-INCM-018 | Incremental operating margin | change in operating income / change in revenue | operating income and revenue | annual / TTM | Use multi-period average to reduce noise. |
| MKT-CAT-019 | Catalyst expected financial impact | Probability * Estimated annual financial impact * Evidence quality * Time-decay factor | explicit scenario probability, impact, evidence score, timing | event | Probabilities and impacts are assumptions; never disguise as reported facts. |
| MKT-TDEC-020 | Catalyst time-decay factor | exp(-ln(2) * months_to_event / 12) | months to event | event | Custom prioritization parameter with 12-month half-life; calibration required. |
| MKT-ADOPT-021 | Adoption-curve penetration | Current users or units / Estimated eventual addressable users or units | current and eventual units | annual | Eventual market is an assumption; show scenarios. |
| MKT-ARPU-022 | ARPU growth | (ARPU_t - ARPU_t-1) / ARPU_t-1 | revenue and average users or issuer KPI | quarterly | Separate price, mix, and usage when disclosed. |
| MKT-SECB-023 | Sector breadth | Members above 50DMA / Valid sector members | sector constituent prices | daily | Also report members above 200DMA; point-in-time constituent control required. |
| MKT-RSG-024 | Sector relative strength | Sector total return - Broad benchmark total return | sector and benchmark returns | 21d/63d/126d | Context, not company technical score. |
| MKT-SCEN-025 | Scenario-weighted market outcome | sum(Scenario probability_i * Revenue or margin outcome_i) | scenario probabilities and outcomes | model | Probabilities sum to 1; report scenarios separately. |

## Formula execution rules

- Use the exact registered definition and sign convention.
- Keep raw and normalized variants side by side where applicable.
- Show calculation inputs in the output packet.
- Do not score a formula whose denominator is `NOT_MEANINGFUL`.
- Record any proxy in `warnings` and reduce model-fit confidence.
