# Risk and Resilience Analysis Agent - Scoring

## Dimension architecture

| Dimension | Max category points | Primary inputs | 0-3 | 4-6 | 7-10 | Gate / cap |
| --- | --- | --- | --- | --- | --- | --- |
| Financing and balance-sheet risk | 3 | RSK-ICOV-011..016 | Coverage<1.5x, runway<12m, maturity coverage<1, high refinancing need | Manageable leverage and 12-24m runway | Low leverage, coverage>5x, strong liquidity and staggered maturities | Interest coverage <1.5x mandatory warning; higher score means lower risk. |
| Competition and concentration risk | 3 | RSK-CUST-017..019 plus business/market share evidence | Single-point concentration or rapid share loss | Moderate concentration with mitigants | Diversified revenue and resilient competitive position | Undisclosed concentration lowers confidence; do not assume diversification. |
| Execution and earnings-quality risk | 3 | RSK-ACCR-020..031, project/catalyst execution | High accruals, forensic flags, repeated misses or weak controls | Mixed but explainable quality | Strong cash backing, clean diagnostics, consistent execution | Forensic models are flags, not guilt determinations. |
| Regulatory, legal, and macro risk | 2 | event registry, RSK-CYC-034, geographic exposure | Material unresolved threat with high impact | Normal industry exposure with mitigants | Low exposure or well-capitalized resilience | Qualitative probability must be disclosed as assumption. |
| Valuation-compression risk | 2 | Valuation premium, duration, revision risk, reverse DCF | Extreme premium requiring optimistic assumptions | Moderate premium or fair value | Low embedded expectations / margin of safety | Use valuation-agent packet; do not duplicate valuation score. |
| Volatility and drawdown risk | 2 | RSK-VOL-001..010, liquidity | Extreme downside beta, drawdowns, gaps, illiquidity | Market-like risk | Controlled volatility and shallow/recovering drawdowns | Higher score always means lower risk. |

## Point calculation

```text
Dimension points = Dimension max points * Dimension score / 10
Category points = sum(Dimension points)
Category score_10 = 10 * Category points / 15
```

Use piecewise-linear interpolation or peer percentile only where the dimension rule explicitly says so. Missing evidence is never neutral.
