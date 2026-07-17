# Business Analysis Agent - Output Schema

```yaml
agent_id: business_analysis
version: 2.0.0
status: COMPLETE
security: {ticker: null, exchange: null, currency: null}
knowledge_timestamp: null
category:
  max_points: 20
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
business_in_one_sentence: null
moat:
  classification: Wide|Narrow|None|NotScorable
  quantitative_evidence: []
roic_history: []
roic_wacc_spread_history: []
margin_stability: {}
customer_economics: {}
capital_allocation: {}
competitive_position: {}
three_thesis_killers: []
```
