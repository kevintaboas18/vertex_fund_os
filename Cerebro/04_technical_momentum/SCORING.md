# Technical and Momentum Analysis Agent - Scoring

## Dimension architecture

| Dimension | Max category points | Primary inputs | 0-3 | 4-6 | 7-10 | Gate / cap |
| --- | --- | --- | --- | --- | --- | --- |
| Primary price trend | 4 | TECH-SMA-002, SLOPE-004, DMI-009, 52W-036 | Close<SMA50<SMA200, SMA200 falling, weak lows | Near SMA200 or mixed/flat stack | Close>SMA50>SMA200, positive slopes, ADX confirms | Minimum 200 sessions; score capped 6 without valid SMA200. |
| Relative strength | 4 | TECH-RS-011..013 | Composite percentile <30 and negative sector/broad RS | Percentile 30-70 or mixed windows | Percentile >70 with positive 63/126/252d RS | Point-in-time universe required for percentile. |
| Volume and institutional demand | 3 | TECH-VR-014..017 | Down-volume dominance, CMF< -0.10, weak OBV slope | Mixed volume and neutral CMF | Up/down >1.2, CMF>0.10, positive OBV slope, breakout volume | Score capped 5 when volume is missing/unadjusted. |
| Earnings-gap behavior | 3 | TECH-GAP-020, GHOLD-021 | Positive gaps repeatedly fail or negative gaps persist | Mixed one/five/twenty-day holds | Positive gaps hold >70% at day 5 and 20 with volume confirmation | At least 4 valid earnings events. |
| Breakout and base quality | 3 | TECH-VCP-019, PIV-022..033, BASE-037, TIGHT-038 | Repeated failed breakouts, loose/deep base, overhead resistance nearby | Constructive base but no confirmation | Orderly contraction, confirmed breakout, successful retest, strong level score | No score from a drawn line without touch records. |
| Sector breadth and volatility quality | 3 | TECH-VOL-018, VCP-019, BREAD-039, LIQ-040 | Weak breadth, rising unstable volatility, poor liquidity | Neutral breadth/volatility | Healthy breadth, controlled volatility, sufficient liquidity | Point-in-time sector membership required for breadth. |

## Point calculation

```text
Dimension points = Dimension max points * Dimension score / 10
Category points = sum(Dimension points)
Category score_10 = 10 * Category points / 20
```

Use piecewise-linear interpolation or peer percentile only where the dimension rule explicitly says so. Missing evidence is never neutral.
