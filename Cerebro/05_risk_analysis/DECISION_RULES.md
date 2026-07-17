# Risk and Resilience Analysis Agent - Decision Rules


## Direction rule

A high category score means resilience and lower risk. Raw risk measures must be inverted before assigning 0-10 dimension scores.

## Quantitative resilience anchors

These are default non-financial-company anchors and require sector context:

| Measure | 0-3 resilience | 4-6 resilience | 7-10 resilience |
|---|---|---|---|
| Interest coverage | <1.5x | 1.5-3x | >3x; strongest >5x |
| Cash runway (cash burners) | <12 months | 12-24 months | >24 months |
| 3y max drawdown | Worse than -60% | -30% to -60% | Better than -30% |
| Downside beta | >1.5 | 0.8-1.5 | <0.8 |
| Customer concentration | Largest >30% | 10-30% | <10% / diversified |

Peer-percentile scoring should supplement these anchors for volatility and cyclicality.

## Mandatory solvency warning

If interest coverage is below 1.5x, display:

`SOLVENCY_WARNING: Operating earnings do not provide a comfortable interest buffer.`

This warning remains even when cash balances are high or the total score is strong.

## Forensic-screen rules

- Beneish, Altman, and Piotroski results are screening diagnostics.
- Never state fraud, insolvency, or manipulation solely from a score.
- Exclude financial companies and other inapplicable industries.
- Show all component values and data quality.

## Thesis killers

Always list at least three risks that could invalidate the thesis. Each entry must include:

```yaml
risk: null
probability_assumption: null
impact: low|medium|high|catastrophic
early_warning_metric: null
trigger_level: null
time_horizon: null
mitigant: null
```

## Risk override

Category points <=4/15 cap the main-agent profile at `Speculative` regardless of total score.
