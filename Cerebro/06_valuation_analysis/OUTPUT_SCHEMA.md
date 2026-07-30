# Valuation Analysis Agent - Output Schema

```yaml
agent_id: valuation_analysis
version: 2.0.0
status: COMPLETE
security: {ticker: null, exchange: null, currency: null}
knowledge_timestamp: null
category:
  max_points: 10
  awarded_points: null
  score_10: null
  confidence: null
coverage: null
dimensions: []
metrics: []
mandatory_flags: []
assumptions: []
source_lineage: []
validation_tests: {passed: 0, failed: 0, warnings: 0}
```


## Additional required fields

```yaml
model_selection:
  primary: []
  rejected: []
normalization_reconciliation: []
wacc:
  value: null
  components: {}
  sensitivity: []
scenarios:
  - name: Bear
    probability: null
    assumptions: {}
    enterprise_value: null
    equity_value: null
    per_share_value: null
  - name: Base
  - name: Bull
reverse_dcf:
  current_price: null
  implied_revenue_cagr: null
  implied_margin: null
  implied_high_growth_years: null
model_cross_checks:
  fcff: null
  economic_profit: null
  residual_income: null
  relative: null
  dispersion: null
fair_value_distribution:
  p10: null
  p25: null
  median: null
  p75: null
  p90: null
reference_bands:
  bear: null
  base: null
  bull: null
  margin_of_safety_15pct: null
  margin_of_safety_25pct: null
```
