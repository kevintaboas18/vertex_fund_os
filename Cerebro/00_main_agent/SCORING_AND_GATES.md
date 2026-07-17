# Scoring, Gates, and Overrides

## Fixed category maximums

| Category | Maximum points |
|---|---:|
| Business | 20 |
| Financial | 15 |
| Market and Growth | 20 |
| Technical and Momentum | 20 |
| Risk and Resilience | 15 |
| Valuation | 10 |
| **Total** | **100** |

## Raw-score descriptive bands

| Raw total | Descriptive band |
|---:|---|
| 90-100 | Elite raw score |
| 80-89.99 | Strong raw score |
| 70-79.99 | Conditional raw score |
| 60-69.99 | Mixed / wait |
| 50-59.99 | Weak |
| <50 | Avoid on raw score |

A raw band is not the final profile.

## Profile gates

### Momentum Candidate

All conditions must pass:

```text
Raw total >= 78
Technical >= 17/20
Market/Growth >= 16/20
Business + Financial >= 28/35
Risk >= 8/15
Technical confidence >= 70
```

Valuation may be low, but the report must show position-risk and breakdown levels.

### Quality Opportunity

```text
Raw total >= 80
Business >= 16/20
Financial >= 11/15
Risk >= 10/15
Valuation >= 5/10
Technical >= 12/20
```

### Value Opportunity

```text
Raw total >= 75
Valuation >= 8/10
Business >= 13/20
Risk >= 10/15
Technical >= 9/20
```

This gate reduces, but does not remove, value-trap risk.

### Conditional / Watch

Use when the raw score is at least 60 but no major profile gate passes. State the exact missing gate and confirmation condition.

### Speculative

Use when one or more of the following are true:

- Risk <= 4/15;
- total confidence < 60;
- a critical category is incomplete;
- pre-profit valuation depends on a low-confidence terminal value;
- financing runway is less than 12 months without committed funding.

### Avoid / Wait

Use when a mandatory override requires it or raw total is below 50.

## Mandatory overrides

1. **Capital dependence override:** net loss + negative FCF + dependence on external capital caps the final profile at `Avoid/Speculative`.
2. **Value-creation override:** ROIC below WACC prevents `Elite`, `Quality Opportunity`, or `Excellent business` classification.
3. **Solvency warning:** interest coverage below 1.5x always appears prominently.
4. **Risk override:** Risk 0-4/15 caps the profile at `Speculative`.
5. **Premium breakdown override:** Valuation 0-4/10 plus Technical 0-8/20 becomes `Wait/Avoid`.
6. **Coverage override:** any core category below 70% coverage cannot pass a profile gate.
7. **Data-conflict override:** unresolved material share-count, debt, cash, or price conflict prevents final valuation-per-share publication.

## Total confidence

```text
Total confidence = sum(category_max_points * category_confidence) / 100
```

Confidence is reported beside the score; it does not numerically alter the raw total.
