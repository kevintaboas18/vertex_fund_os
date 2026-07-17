# Market and Growth Analysis Agent - Decision Rules


## Source-quality tiers for TAM

| Tier | Source | Confidence component |
|---|---|---:|
| 1 | Government/regulatory or audited industry volume | 100 |
| 2 | Reputable industry association with methodology | 85 |
| 3 | Independent research with disclosed methodology | 70 |
| 4 | Issuer estimate or secondary summary | 45 |
| 5 | Unattributed market-size claim | 0; not scorable |

## Forecast-consistency gate

For each forecast year:

```text
Company revenue <= TAM in same definition
Implied market share = Company revenue / TAM
Implied share change must be disclosed
Fundamental growth capacity = Reinvestment rate * ROIC
```

When forecast growth exceeds fundamental growth capacity, identify the financing source, margin change, asset-turn improvement, or external capital required.

## Market verdict

- 8.0-10.0: Large expanding opportunity, positive revisions, share gains, and funded runway.
- 6.0-7.99: Attractive but with one material uncertainty.
- 4.0-5.99: Mixed or mature opportunity.
- <4.0: Shrinking, saturated, poorly evidenced, or losing share.

## Mandatory output

Always list:

1. exact TAM definition and source tier;
2. current penetration and share trend;
3. revenue/EPS revision breadth and magnitude;
4. at least three catalysts with evidence class and date;
5. three events that would invalidate the growth thesis.
