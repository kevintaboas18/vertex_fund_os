# Output Contract

Every metric row must contain:

| Field | Requirement |
|---|---|
| `metric_id` | Stable ID |
| `value` | Numeric or approved null state |
| `unit` | Explicit |
| `period` | Explicit |
| `formula` | Formula ID and version |
| `score` | 0-10 or `NOT_SCORABLE` |
| `evidence_class` | R, C, E, A, or Q |
| `source` | Stable source locator |
| `confidence` | 0-100 |
| `warnings` | Array |

## Narrative rule

Narrative must follow the numbers, not lead them. Every material adjective in the executive summary must point to a metric, gate, or rule. Examples:

- Allowed: `FCF conversion is strong because the five-year cumulative ratio is 1.08x.`
- Forbidden: `Cash generation appears excellent` without a number and threshold.
