# Ruta 2030 Important Levels Engine

## Purpose

Convert repeated market behavior and valuation outputs into a ranked table of analytical price references. The engine is deterministic: it never draws a line because it "looks important."

## Required inputs

- at least 252 split/dividend-adjusted daily OHLCV sessions; 756 preferred;
- weekly bars derived from the adjusted daily series;
- ATR14, moving averages, volume history, earnings dates, and event anchors;
- Bear/Base/Bull valuation ranges and current-price reverse-DCF assumptions.

## A. Technical zone engine

### A1. Swing detection

Use two independent swing methods:

```text
Daily pivot high: High_t = max(High[t-3:t+3])
Daily pivot low:  Low_t  = min(Low[t-3:t+3])
Weekly pivot: same with k=2
ATR zigzag reversal threshold: 1.5 * ATR14
```

A symmetric pivot is known only after the future confirmation bars close. Backtests must delay its availability.

### A2. Zone width

```text
tolerance_i = max(0.50 * ATR14_i, 0.0075 * pivot_price_i)
interval_i = [pivot_price_i - tolerance_i, pivot_price_i + tolerance_i]
```

Cluster overlapping intervals. Start with highs and lows separately to avoid declaring a level from opposite swing types prematurely.

### A3. Zone center

```text
recency_weight_i = exp(-ln(2) * age_sessions_i / 126)
zone_center = weighted_median(pivot_price_i, recency_weight_i)
zone_half_width = weighted_median(tolerance_i, recency_weight_i)
zone_low = zone_center - zone_half_width
zone_high = zone_center + zone_half_width
```

### A4. Independent touches

A touch counts only once per swing:

- daily touches separated by at least five trading sessions;
- weekly touches separated by at least two completed weeks;
- resistance touch = confirmed pivot high inside zone;
- support touch = confirmed pivot low inside zone;
- valid rejection = at least 0.5 ATR movement away within three sessions.

Repeated bars at the same local high are one touch, not several.

### A5. Rejection magnitude

```text
Resistance reaction_ATR = (zone_center - min(low over next 3 sessions)) / ATR_at_touch
Support reaction_ATR    = (max(high over next 3 sessions) - zone_center) / ATR_at_touch
```

### A6. Level labels

| State | Deterministic requirement |
|---|---|
| Candidate | One valid touch |
| Confirmed | At least two independent valid touches |
| Strong | At least three touches, or two touches plus median reaction >=1 ATR and one volume ratio >=1.5 |
| Broken | Confirmed close through the buffered zone in the adverse direction |
| Role reversed | Confirmed break, later retest, and >=0.5 ATR rejection from the former zone |

### A7. Strength score

```text
N_eff = sum(exp(-ln(2) * age_sessions_i / 126))
Touch = 30 * min(N_eff / 4, 1)
Reaction = 20 * min(median_reaction_ATR / 2, 1)
Volume = 15 * min(median_touch_volume_ratio / 1.5, 1)
Recency = 15 * exp(-ln(2) * age_latest_touch / 126)
Timeframe = 10 if weekly else 5
Confluence = 10 * min(confluence_count / 3, 1)
Strength = min(100, Touch + Reaction + Volume + Recency + Timeframe + Confluence)
```

This is a disclosed Ruta 2030 model parameterization, not a universal market law. It requires calibration by liquidity, volatility, and timeframe.

### A8. Confluence

Count at most one point from each independent class within zone tolerance:

1. weekly pivot zone;
2. SMA50 or SMA200;
3. event-anchored VWAP;
4. material earnings-gap boundary or midpoint;
5. volume-profile point of control or high-volume node.

Do not count three nearby moving averages as three independent confirmations.

## B. Nearby resistance and support

For resistance above price:

```text
distance_ATR = (zone_low - current_close) / ATR14
nearby: 0 <= distance_ATR <= 1
approaching: 1 < distance_ATR <= 2
```

For support below price:

```text
distance_ATR = (current_close - zone_high) / ATR14
nearby: 0 <= distance_ATR <= 1
approaching: 1 < distance_ATR <= 2
```

Always report percent and ATR distance.

## C. Breakout and failed-breakout logic

### Confirmed upside breakout

All conditions:

```text
Close > zone_high + 0.25*ATR14
Volume / median(volume prior 50d) >= 1.5
AND (
  two consecutive closes above the buffered level
  OR one close above followed by three sessions with no close inside the zone
)
```

### Failed breakout

A breakout attempt fails when price closes back inside or below the zone within three sessions. The failure is a separate event and may strengthen the resistance classification.

### Role reversal

Former resistance becomes support only after:

1. confirmed breakout;
2. later retest into the old zone;
3. close back above the zone;
4. upward reaction of at least 0.5 ATR within three sessions.

## D. Anchored VWAP

```text
TypicalPrice_i = (High_i + Low_i + Close_i) / 3
AVWAP = sum(TypicalPrice_i * Volume_i) / sum(Volume_i), from anchor to current date
```

Approved anchors:

- material earnings gap;
- confirmed breakout;
- major 52-week or multi-year swing high/low;
- material financing/acquisition event;
- beginning of a verified trend regime.

Every AVWAP must include the anchor date and rationale.

## E. Volume profile

With daily data, approximate volume at price:

```text
bin_width = max(0.50*ATR14, 0.005*current_price)
assign each bar's volume to its typical-price bin
POC = bin with maximum volume
HVN = contiguous bins above the 75th percentile of bin volume
LVN = contiguous bins below the 25th percentile
```

Label this an approximation; tick-level data are preferable.

## F. Earnings-gap levels

For each material earnings gap, retain:

- prior close;
- gap-session open;
- gap high/low;
- gap midpoint;
- day-1, day-5, and day-20 hold ratios;
- whether the gap is open, partially filled, fully filled, or reclaimed.

A gap is material when its absolute size is at least `max(1.0 ATR, 3%)` unless calibrated otherwise.

## G. Valuation reference levels

The valuation agent supplies:

- Bear scenario range;
- Base scenario range;
- Bull scenario range;
- p10/p25/median/p75/p90 Monte Carlo values;
- 15% and 25% discounts to Base value as margin-of-safety references;
- current-price reverse-DCF implied growth/margin/duration.

These are scenario references, not technical support.

## H. Cross-lens confluence

A valuation/technical confluence exists when a technical zone overlaps a valuation reference band within:

```text
max(0.50*ATR14, 0.0075*current_price)
```

The final report must show both source types. It must not average them into a synthetic target.

## I. Final ranking

Rank levels by:

```text
Relevance = 0.45*TechnicalStrength
          + 0.25*RecencyScore
          + 0.20*CrossLensConfluence
          + 0.10*LiquidityConfidence
```

All components are 0-100. For pure valuation references, use valuation confidence in place of technical strength and keep the type explicit.

## J. Required output

| Rank | Level type | Lower | Center | Upper | Distance % | Distance ATR | Strength/confidence | Status | Confirmation | Invalidation |
|---:|---|---:|---:|---:|---:|---:|---:|---|---|---|

The engine returns the nearest three support zones, nearest three resistance zones, major moving averages, key AVWAPs, material gaps, and Bear/Base/Bull valuation references.
