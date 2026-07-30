# Specialist-to-Main-Agent Handoff Contract

Each specialist returns one YAML-compatible object.

```yaml
agent_id: technical_momentum
version: 2.0.0
ticker: EXAMPLE
knowledge_timestamp: 2026-02-14T21:00:00Z
status: COMPLETE
category:
  max_points: 20
  awarded_points: 15.4
  score_10: 7.7
  confidence: 88
coverage: 0.94
dimensions: []
metrics: []
mandatory_flags: []
important_levels: []
assumptions: []
source_lineage: []
validation_tests:
  passed: 24
  failed: 0
  warnings: 2
```

## Handoff validation

The main agent rejects the packet if:

- category points do not reproduce from dimension scores;
- a score lacks a formula ID or scoring rule;
- the knowledge timestamp is absent;
- confidence and coverage are absent;
- a required override flag is omitted;
- price levels lack touch dates, zone bounds, or ATR distance;
- valuation lacks scenario assumptions and diluted share count.
