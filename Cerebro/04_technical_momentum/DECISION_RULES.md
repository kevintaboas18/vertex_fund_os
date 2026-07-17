# Technical and Momentum Analysis Agent - Decision Rules


## Exact primary-trend anchors

Use piecewise scoring from these anchors:

| Condition | Trend score |
|---|---:|
| Close below SMA50 below SMA200; SMA200 slope < -1 ATR over 50 sessions | 0-2 |
| Close below SMA200 but stack mixed | 3 |
| Close within +/-1 ATR of SMA200 and SMA200 slope between -0.25 and +0.25 ATR | 4-5 |
| Close above SMA200; SMA50 mixed or flat | 6 |
| Close > SMA50 > SMA200; both slopes positive | 8 |
| Same as above, ADX>=25 and 52-week position>=0.80 | 9-10 |

RSI is a secondary condition, never a standalone trend verdict. RSI >80 is `extended`, not automatically bearish; RSI <30 is `oversold`, not automatically bullish.

## Deterministic support/resistance engine

### Step 1 - Confirm swings

Run both:

- symmetric daily pivots with `k=3` and weekly pivots with `k=2`;
- ATR zigzag with 1.5 ATR reversal threshold.

A pivot is usable only after the required future bars have closed. Historical backtests must honor this confirmation delay.

### Step 2 - Build volatility-scaled zones

For each pivot:

```text
tolerance_i = max(0.50 * ATR14_at_pivot, 0.0075 * pivot_price)
```

Sort pivots by price and cluster overlapping intervals `[pivot-tolerance, pivot+tolerance]`. Separate pivot highs and lows initially. Zone center is the recency-weighted median; half-width is the weighted median tolerance.

### Step 3 - Count independent touches

- Daily touches must be separated by at least 5 trading sessions.
- Weekly touches must be separated by at least 2 completed weeks.
- Multiple bars within the same swing count once.
- A resistance touch requires a pivot high inside the zone; support requires a pivot low.
- A valid rejection must move at least 0.5 ATR away from the zone within the next 3 sessions.

### Step 4 - Confirm a level

- `Candidate`: one valid touch.
- `Confirmed`: at least two independent valid touches.
- `Strong`: at least three touches, or two touches plus median reaction >=1 ATR and one touch with volume ratio >=1.5.

### Step 5 - Score level strength

```text
N_eff = sum(exp(-ln(2)*age_sessions/126))
Touch points = 30*min(N_eff/4,1)
Reaction points = 20*min(median_reaction_ATR/2,1)
Volume points = 15*min(median_volume_ratio/1.5,1)
Recency points = 15*exp(-ln(2)*age_latest/126)
Timeframe points = 10 weekly or 5 daily
Confluence points = 10*min(confluence_count/3,1)
Level strength = min(100, sum(points))
```

Confluence may include weekly zone, SMA50/SMA200, anchored VWAP, earnings-gap boundary, or volume-profile POC/HVN within the zone tolerance.

### Step 6 - Define nearby levels

For resistance above current price:

```text
Distance_ATR = (zone_low - current_close) / ATR14
Nearby = 0 <= Distance_ATR <= 1.0
Approaching = 1.0 < Distance_ATR <= 2.0
```

For support below current price:

```text
Distance_ATR = (current_close - zone_high) / ATR14
Nearby = 0 <= Distance_ATR <= 1.0
Approaching = 1.0 < Distance_ATR <= 2.0
```

This directly addresses repeated hits: two independent rejected highs create confirmed resistance; three or more raise strength under the formula.

### Step 7 - Confirm breakout or breakdown

Resistance breakout requires all:

1. close above `zone_high + 0.25*ATR14`;
2. volume ratio >=1.5;
3. either two consecutive closes above the buffered level, or one close followed by three sessions without a close back inside the zone.

A failed breakout occurs when price closes back inside or below the zone within three sessions. Role reversal requires a later retest and >=0.5 ATR rejection in the new direction.

## Technical profile bands

- 17-20: Powerful leadership.
- 13-16.99: Healthy uptrend.
- 9-12.99: Neutral / transition.
- 0-8.99: Weak or broken.

## Mandatory output

Return the nearest three support and resistance zones, every touch date, touch type, rejection magnitude, volume ratio, zone bounds, strength score, current distance, breakout state, confirmation condition, and invalidation condition.

Technical analysis is timing evidence, not proof of business value. It cannot override weak fundamentals by itself.
