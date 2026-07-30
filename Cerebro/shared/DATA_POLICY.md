# Data Policy

## Evidence classes

Every value must carry exactly one class:

- `R` - reported directly in a filing or official release;
- `C` - calculated mechanically from reported or market data;
- `E` - external estimate, such as consensus revenue or market size;
- `A` - explicit model assumption, such as terminal growth;
- `Q` - qualitative evidence that is not scored unless a conversion rule exists.

## Required lineage fields

```yaml
metric_id: FIN-FCF-001
value: 1250.0
unit: USD_millions
period_start: 2025-01-01
period_end: 2025-12-31
as_of: 2026-02-14T21:05:00Z
knowledge_timestamp: 2026-02-14T21:05:00Z
source_type: filing
source_name: SEC 10-K
source_locator: filing accession or stable identifier
evidence_class: C
formula_version: 2026.1
calculation_inputs: [operating_cash_flow, capital_expenditures]
restated: false
currency: USD
```

## Hard prohibitions

- Do not fill a missing value with an industry average unless the formula explicitly calls for a peer value.
- Do not use management guidance as a reported actual.
- Do not mix annual, trailing-twelve-month, and quarterly periods without labeling and reconciliation.
- Do not mix pre-split price history with post-split shares.
- Do not use unadjusted price series for return, moving-average, or level calculations unless the corporate action is intentionally modeled.
- Do not convert an unverified web statement into a score.
- Do not silently change sign conventions, currency, tax treatment, or denominator definitions.

## Staleness defaults

| Data type | Stale after | Action |
|---|---:|---|
| Daily market data | 1 completed trading session | Mark technical packet stale |
| Consensus estimates | 7 calendar days | Lower estimate confidence |
| Quarterly fundamentals | Next required filing or 120 days | Mark financial packet stale |
| Annual market-size study | 18 months | Require corroboration |
| Peer set | 90 days or material corporate event | Rebuild peer set |

Staleness affects confidence and may trigger `RECALC_REQUIRED`; it does not change a historical value.
