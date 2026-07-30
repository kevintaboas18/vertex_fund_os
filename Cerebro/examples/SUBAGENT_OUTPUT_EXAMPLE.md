# Example Technical Subagent Output - EXMPL

```yaml
agent_id: technical_momentum
version: 2.0.0
status: COMPLETE
category:
  max_points: 20
  awarded_points: 15.2
  score_10: 7.6
  confidence: 89
coverage: 0.96
important_levels:
  resistance:
    - zone_id: R-D-001
      lower: 84.20
      center: 85.00
      upper: 85.80
      timeframe: daily
      status: confirmed
      strength_0_100: 73
      touches:
        - {date: 2025-11-18, pivot_price: 84.70, rejection_atr: 1.1, volume_ratio: 1.2}
        - {date: 2026-01-09, pivot_price: 85.25, rejection_atr: 1.4, volume_ratio: 1.6}
      distance_percent: 6.25
      distance_atr: 2.0
      confirmation_rule: Close above 86.30 with volume ratio >=1.5 and follow-through
      invalidation_rule: Failed breakout if close returns inside zone within 3 sessions
```

Every number must carry formula and source objects in the complete machine output.
