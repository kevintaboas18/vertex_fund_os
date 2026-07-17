# System Architecture

## Separation of duties

| Layer | Responsibility | Forbidden action |
|---|---|---|
| Data layer | Collect, timestamp, normalize, reconcile, and label evidence | Interpret the investment thesis |
| Formula layer | Calculate registered metrics exactly as specified | Change a formula silently |
| Specialist layer | Score one analytical lens | Override another specialist |
| Main agent | Aggregate, gate, reconcile, and report | Invent missing specialist evidence |
| Audit layer | Verify formulas, units, lineage, and tests | Rewrite a result to fit a narrative |

## Handoff sequence

```text
Raw evidence
  -> source validation
  -> normalization and restatement policy
  -> formula registry
  -> six independent specialist packets
  -> schema validation
  -> weighted score
  -> profile gates and mandatory overrides
  -> valuation/technical level synthesis
  -> final report + audit trail
```

## Canonical analysis clocks

- `knowledge_timestamp`: last moment evidence was known.
- `market_timestamp`: close time of the latest market bar.
- `filing_timestamp`: filing acceptance time, not fiscal period end.
- `estimate_timestamp`: timestamp of the consensus snapshot.
- `calculation_timestamp`: when the metric was calculated.

No agent may use information published after `knowledge_timestamp` in a historical backtest.

## Core design choice

Score and confidence are separate. A company can have a high metric score with low confidence if evidence is stale or sparse. Confidence never converts an unknown into a favorable score.
