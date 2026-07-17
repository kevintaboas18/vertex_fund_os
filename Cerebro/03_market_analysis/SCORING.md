# Market and Growth Analysis Agent - Scoring

## Dimension architecture

| Dimension | Max category points | Primary inputs | 0-3 | 4-6 | 7-10 | Gate / cap |
| --- | --- | --- | --- | --- | --- | --- |
| TAM and industry tailwind | 5 | MKT-TAM-001..008,021 | Shrinking/small market, high penetration, weak source quality | Stable/decent market with moderate runway | Large, expanding, well-sourced market with low/moderate penetration | TAM confidence <60 caps score at 6. |
| Earnings and revenue revisions | 4 | MKT-REVBR-011..014 | Downward breadth/magnitude and repeated misses | Stable estimates and mixed surprises | Broad upward revisions, positive magnitude, controlled dispersion | Requires timestamped frozen consensus and >=5 estimates. |
| Product and business catalysts | 4 | MKT-BACK-015,016 and MKT-CAT-019,020 | No validated catalyst or deteriorating backlog | One measurable but uncertain catalyst | Multiple evidenced catalysts with quantified impact and timing | Narrative-only catalyst score capped at 3. |
| Growth runway and share capture | 4 | MKT-PEN-005..010,015,021,022 | Saturation, share loss, growth unsupported by capital | Several years of moderate growth and stable share | Long runway, share gains, growth capacity supports forecast | Forecast growth > growth capacity by >5 pts requires external-capital explanation. |
| Operating leverage and market confirmation | 3 | MKT-OPLEV-017,018,023,024 | Negative incremental economics and weak sector breadth | Mixed/neutral leverage and confirmation | Positive incremental margin with healthy sector participation | Sector breadth is context; company technical remains separate. |

## Point calculation

```text
Dimension points = Dimension max points * Dimension score / 10
Category points = sum(Dimension points)
Category score_10 = 10 * Category points / 20
```

Use piecewise-linear interpolation or peer percentile only where the dimension rule explicitly says so. Missing evidence is never neutral.
