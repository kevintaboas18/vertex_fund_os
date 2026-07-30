# Financial Analysis Agent - Scoring

## Dimension architecture

| Dimension | Max category points | Primary inputs | 0-3 | 4-6 | 7-10 | Gate / cap |
| --- | --- | --- | --- | --- | --- | --- |
| Revenue quality and growth | 3 | FIN-GR-001..005 | Core growth metrics mostly BAD or share loss | Low/moderate growth, stable trend, in-line peers | Organic, accelerating, above-peer growth with share gains | At least 4 of 5 metrics valid. |
| EPS and free cash flow | 3 | FIN-PR-006,011 and FIN-CF-012..016 | Losses, negative FCF, poor conversion, external dependence | Positive but modest/volatile cash generation | Consistent profit and FCF, conversion >=1x, self-funds growth | Loss + negative FCF + external dependence triggers override. |
| Margins | 3 | FIN-PR-007..010 | Sub-peer and falling margins | Stable, acceptable margins | Top-quartile and rising/resilient margins | Industry comparison required. |
| Balance and liquidity | 3 | FIN-BS-017..022 + diagnostics | Liquidity stress or coverage <1.5x | Manageable obligations and stable liquidity | Strong liquidity, low leverage, improving coverage | Interest coverage <1.5x forces solvency warning. |
| Cash conversion and capital efficiency | 3 | FIN-CF-015 and FIN-EF-023..027 + dilution | ROIC<WACC, falling returns, poor cash backing, heavy dilution | Returns near/above hurdle and stable | ROIC materially above WACC, rising returns, strong conversion | ROIC<WACC prevents excellent category verdict. |

## Point calculation

```text
Dimension points = Dimension max points * Dimension score / 10
Category points = sum(Dimension points)
Category score_10 = 10 * Category points / 15
```

Use piecewise-linear interpolation or peer percentile only where the dimension rule explicitly says so. Missing evidence is never neutral.
