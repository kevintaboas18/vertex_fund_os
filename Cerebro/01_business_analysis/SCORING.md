# Business Analysis Agent - Scoring

## Dimension architecture

| Dimension | Max category points | Primary inputs | 0-3 | 4-6 | 7-10 | Gate / cap |
| --- | --- | --- | --- | --- | --- | --- |
| Moat and pricing power | 5 | ROIC-WACC persistence, margin stability, pricing/retention evidence | ROIC <= WACC, margins compress, no measurable retention/pricing evidence | Small positive spread or mixed stability; one defensible advantage | Spread >=5 pts in >=4/5 years, stable margins, multiple quantitative moat effects | Score capped at 6 without positive ROIC-WACC spread or a valid adapter. |
| Competitive position | 4 | Market-share trend, peer ROIC/margins, rank | Losing share and trailing peers | Stable share and near-peer economics | Gaining share while sustaining superior returns | Cannot score above 8 if market definition is low confidence. |
| Management and capital allocation | 4 | Incremental ROIC, allocation history, dilution, guidance accuracy | Incremental returns below WACC, material dilution, repeated poor M&A | Mixed allocation, returns near WACC | Incremental ROIC > WACC, disciplined buybacks/M&A, aligned incentives | Qualitative reputation alone cannot exceed 5. |
| Business durability | 4 | Recurring revenue, concentration, margin/ROIC persistence, cyclicality | One-off revenue, high concentration, fragile cycle economics | Moderate recurrence and stable but ordinary economics | High recurrence, diversified revenue, 5-10y persistence | Largest customer >30% caps at 6 unless contract protection is quantified. |
| Customer economics | 3 | NRR, GRR, churn, LTV/CAC, payback or repeat-purchase proxies | Deteriorating retention or uneconomic acquisition | Stable retention and acceptable economics | NRR/retention and payback show compounding customer value | If not applicable, use adapter metrics; do not impute. |

## Point calculation

```text
Dimension points = Dimension max points * Dimension score / 10
Category points = sum(Dimension points)
Category score_10 = 10 * Category points / 20
```

Use piecewise-linear interpolation or peer percentile only where the dimension rule explicitly says so. Missing evidence is never neutral.
