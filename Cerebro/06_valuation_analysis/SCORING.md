# Valuation Analysis Agent - Scoring

## Dimension architecture

| Dimension | Max category points | Primary inputs | 0-3 | 4-6 | 7-10 | Gate / cap |
| --- | --- | --- | --- | --- | --- | --- |
| Growth-adjusted multiples | 3 | VAL-PEG-028, JPE-032, JEVS-033, reverse DCF | Price implies growth/returns far above evidenced capacity | Multiples broadly match growth and returns | Price embeds conservative growth relative to quality | No score from PEG when earnings/growth are not meaningful. |
| Historical and peer comparison | 2 | VAL-REL-034, ZHIST-035, peer-adjusted fundamentals | Extreme premium after controlling for quality | Near fair peer/history range | Discount with no deterioration explanation | Minimum 8 valid peers or use history only. |
| Cash-flow and earnings yield | 2 | VAL-EY-029, FCFY-030, cost of capital | Negative/very low yields with weak conversion | Yields consistent with quality and rates | Attractive normalized yields with strong cash quality | Use normalized, not peak-cycle, cash flow. |
| Fair value by scenarios | 2 | VAL-FCFF-005..027, 036..044 | Price above bull/base values or reverse DCF implausible | Price within base range | Price below conservative base and near bear value support | WACC>g, diluted shares, and scenario audit required. |
| Margin of safety | 1 | VAL-MOS-040, model dispersion, confidence | No MOS or price above estimated value | 0-15% base-case MOS | >15% base MOS and downside protected across methods | Low-confidence valuation caps dimension at 5. |

## Point calculation

```text
Dimension points = Dimension max points * Dimension score / 10
Category points = sum(Dimension points)
Category score_10 = 10 * Category points / 10
```

Use piecewise-linear interpolation or peer percentile only where the dimension rule explicitly says so. Missing evidence is never neutral.
