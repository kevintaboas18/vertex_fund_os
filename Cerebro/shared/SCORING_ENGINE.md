# Scoring Engine

## Category architecture

```text
Business 20 + Financial 15 + Market 20 + Technical 20 + Risk 15 + Valuation 10 = 100
```

Each category contains dimensions with maximum category points. Each dimension receives a score from 0 to 10 under its own explicit rules.

```text
Dimension points = Dimension max points * Dimension score / 10
Category points = sum(Dimension points)
Category score_10 = 10 * Category points / Category max points
Raw total = sum(Category points)
```

## Continuous scoring

When an absolute band is defined, use piecewise-linear interpolation between registered anchors. Do not jump from 4 to 8 merely because a value crossed a label boundary.

Example for a positive metric with anchors `[(0,0), (5%,3), (10%,5), (15%,7), (25%,10)]`:

```text
score(x) = linear_interpolation(x, anchors), clamped to [0,10]
```

## Peer-relative scoring

When the metric is explicitly peer-relative:

```text
peer_score_positive = 10 * percentile_rank(x)
peer_score_negative = 10 * (1 - percentile_rank(x))
```

Use a minimum of 8 valid peers. If fewer than 8 peers exist, use absolute rules or mark the peer component `NOT_SCORABLE`.

## Hybrid score

Only when a metric definition calls for it:

```text
hybrid_score = w_abs * absolute_score + w_peer * peer_score
w_abs + w_peer = 1
```

Weights must be registered in that metric's scoring file.

## Missing metrics

- Do not assign 5/10 to missing evidence.
- Reweight only within a dimension when at least 70% of its metric weight is valid.
- A category is `INCOMPLETE` when valid dimension weight is below 70%.
- An incomplete category may be displayed but cannot pass a profile gate.

## Risk direction

For Risk and Resilience, a higher score always means lower risk. Raw risk measures must be inverted before aggregation where necessary.
