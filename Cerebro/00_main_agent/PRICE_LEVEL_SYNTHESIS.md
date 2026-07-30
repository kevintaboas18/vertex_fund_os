# Price-Level Synthesis

## Objective

Present the most decision-relevant price references from two independent lenses:

- intrinsic-value references from the Valuation Agent;
- market-behavior zones from the Technical Agent.

They are not interchangeable and must not be averaged mechanically.

## Required level classes

1. Current adjusted close.
2. Nearest confirmed support zone.
3. Nearest confirmed resistance zone.
4. Confirmed breakout trigger and failed-breakout level.
5. Role-reversal retest zone, when validated.
6. 20-, 50-, 100-, and 200-session moving averages.
7. Selected anchored VWAP levels.
8. Material earnings-gap boundaries and midpoint.
9. Weekly support/resistance zones.
10. Bear, base, and bull intrinsic-value ranges.
11. Reverse-DCF current-price implied assumptions.
12. Margin-of-safety reference bands.

## Confluence rules

A `confluence zone` exists when two independent references overlap within:

```text
max(0.50 * ATR14, 0.75% of current price)
```

At least one reference must be technical and one may be valuation. Confluence increases analytical relevance but is not a guarantee that price will react.

## Distance calculations

```text
Distance_percent = (Level - CurrentPrice) / CurrentPrice
Distance_ATR = (Level - CurrentPrice) / ATR14
```

Use zone-low for approaching resistance and zone-high for approaching support as specified in the technical engine.

## Final table schema

| Rank | Type | Zone / value | Distance % | Distance ATR | Strength / confidence | Status | Confirmation | Invalidation |
|---:|---|---:|---:|---:|---:|---|---|---|

## Language rules

Use `reference`, `zone`, `confirmation`, `invalidation`, and `scenario value`. Do not use `guaranteed target`, `must hold`, or `certain floor`.
