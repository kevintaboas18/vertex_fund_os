# Technical and Momentum Analysis Agent - Formula Registry

| ID | Metric | Formula | Required inputs | Frequency | Rules / caveats |
| --- | --- | --- | --- | --- | --- |
| TECH-RET-001 | Log return | ln(Adjusted close_t / Adjusted close_t-1) | adjusted closes | daily | Align non-trading dates before benchmark comparisons. |
| TECH-SMA-002 | Simple moving average | mean(Close over N sessions) | adjusted close | daily; N=20,50,100,200 | Require N valid observations. |
| TECH-EMA-003 | Exponential moving average | EMA_t = alpha*Close_t + (1-alpha)*EMA_t-1; alpha=2/(N+1) | adjusted close | daily; N=12,20,26,50 | Initialize with SMA_N. |
| TECH-SLOPE-004 | ATR-normalized trend slope | OLS slope(Close over N) * N / ATR14_latest | close series, ATR14 | daily; N=20,50,200 | Expresses N-session trend change in ATR units. |
| TECH-TR-005 | True range | max(High-Low, abs(High-PriorClose), abs(Low-PriorClose)) | high, low, prior close | daily | Use adjusted series consistently. |
| TECH-ATR-006 | Wilder ATR14 | ATR_t = (13*ATR_t-1 + TR_t)/14 | true range | daily | Initialize with mean first 14 TR values. |
| TECH-RSI-007 | Wilder RSI14 | 100 - 100/(1 + AvgGain14/AvgLoss14) | close changes | daily | Use Wilder smoothing; handle zero losses as RSI=100. |
| TECH-MACD-008 | MACD | EMA12 - EMA26; Signal=EMA9(MACD); Histogram=MACD-Signal | adjusted close | daily | Score direction and acceleration, not magnitude alone. |
| TECH-DMI-009 | ADX14 | Wilder average of DX=100*abs(+DI--DI)/(+DI+-DI) | high, low, close | daily | ADX measures trend strength, not direction. |
| TECH-ROC-010 | Rate of change | Close_t / Close_t-N - 1 | adjusted close | N=21,63,126,252 | Total-return series preferred when distributions are material. |
| TECH-RS-011 | Benchmark relative return | Stock total return_N - Benchmark total return_N | stock and benchmark total returns | 21/63/126/252d | Percentage-point excess return. |
| TECH-RSS-012 | Sector relative return | Stock total return_N - Sector total return_N | stock and sector returns | 21/63/126/252d | Separates stock leadership from sector beta. |
| TECH-RSC-013 | Composite relative-strength percentile | 0.35*PctRank(RS21)+0.25*PctRank(RS63)+0.25*PctRank(RS126)+0.15*PctRank(RS252) | point-in-time universe RS values | daily | Universe and survivorship controls required. |
| TECH-VR-014 | Volume ratio | Volume_t / median(Volume over prior 50 sessions) | adjusted volume | daily | Median reduces outlier distortion. |
| TECH-UDV-015 | Up/down volume ratio | sum(Volume on up closes,50d) / sum(Volume on down closes,50d) | close, volume | daily | If denominator is zero, mark not meaningful. |
| TECH-OBV-016 | On-balance volume | OBV_t = OBV_t-1 + sign(Close_t-Close_t-1)*Volume_t | close, volume | daily | Use slope/divergence, not absolute level. |
| TECH-CMF-017 | Chaikin money flow 20 | sum(((2C-H-L)/(H-L))*Volume,20) / sum(Volume,20) | OHLCV | daily | If H=L, multiplier=0 for that bar. |
| TECH-VOL-018 | Annualized realized volatility | stdev(log returns_N)*sqrt(252) | returns | 20/63/252d | Risk-agent input and volatility-quality context. |
| TECH-VCP-019 | Volatility contraction ratio | ATR14/Close divided by median(ATR14/Close over prior 126 sessions) | ATR, close | daily | Below 1 indicates contraction. |
| TECH-GAP-020 | Earnings gap percentage | (Session open_after_event - Prior close) / Prior close | event-mapped open/prior close | event | Use first regular session after after-hours release. |
| TECH-GHOLD-021 | Gap hold ratio | (Close_k - Prior close) / (Gap open - Prior close) | prior close, gap open, close at k | k=1,5,20 sessions | For negative gaps, preserve sign and interpret symmetrically. |
| TECH-PIV-022 | Symmetric pivot high/low | High_t is pivot high if High_t=max(High[t-k:t+k]); low analog | high/low series | daily k=3; weekly k=2 | Ties require one canonical pivot; future bars mean pivots are confirmed with delay. |
| TECH-ZIG-023 | ATR zigzag swing | Confirm reversal when price moves >=1.5*ATR14 from candidate extreme | OHLC, ATR | daily / weekly | Custom deterministic alternative to pivot windows; parameter must be logged. |
| TECH-ZTOL-024 | Level-zone tolerance | tol_i=max(0.50*ATR14_i, 0.0075*PivotPrice_i) | ATR at pivot, pivot price | per pivot | Ruta 2030 custom parameter; backtest by liquidity/volatility regime. |
| TECH-ZONE-025 | Zone center and bounds | Center=weighted median(pivot prices); half_width=weighted median(tol_i); bounds=center +/- half_width | clustered pivots, tolerances | daily / weekly | Recency weight uses 126-session half-life. |
| TECH-NEFF-026 | Effective touch count | N_eff=sum(exp(-ln(2)*AgeSessions_i/126)) | touch ages | per zone | Independent touches only; daily touches separated by >=5 sessions, weekly by >=2 weeks. |
| TECH-REJ-027 | Rejection magnitude | Resistance: (ZoneCenter - min(Low next 3 sessions))/ATR_at_touch; support analog=(max(High next 3)-ZoneCenter)/ATR | zone, post-touch highs/lows, ATR | per touch | A valid rejection requires >=0.5 ATR reaction within 3 sessions. |
| TECH-LSTR-028 | Level strength score | 30*min(N_eff/4,1)+20*min(MedianReactionATR/2,1)+15*min(MedianVolumeRatio/1.5,1)+15*exp(-ln(2)*AgeLatest/126)+TimeframePts+10*min(ConfluenceCount/3,1) | touches, reactions, volume, age, timeframe, confluence | per zone | TimeframePts=10 weekly or 5 daily; total capped 100. Custom model. |
| TECH-DATR-029 | Distance to level in ATR | (ReferencePrice - CurrentClose) / ATR14 | level boundary/center, close, ATR | daily | Use nearest zone boundary for approach status. |
| TECH-BRK-030 | Breakout buffer | Close > ResistanceHigh + 0.25*ATR14 | close, zone high, ATR | daily | Must also pass volume/follow-through confirmation. |
| TECH-BCONF-031 | Breakout confirmation | Buffer pass AND VolumeRatio>=1.5 AND (two closes above OR one close followed by 3 sessions with no close inside zone) | close sequence, zone, ATR, volume ratio | event | All conditions deterministic. |
| TECH-FBRK-032 | Failed breakout | Confirmed/attempted breakout followed by close back inside/below zone within 3 sessions | post-breakout closes | event | Record failure date and volume. |
| TECH-ROLE-033 | Role reversal | Confirmed breakout plus retest entering former zone and closing back above it with >=0.5 ATR reaction | zone, closes, lows, ATR | event | Former resistance becomes support only after validated retest. |
| TECH-AVWAP-034 | Anchored VWAP | sum(TypicalPrice_i*Volume_i)/sum(Volume_i) from anchor; TypicalPrice=(H+L+C)/3 | OHLCV, anchor date | daily | Valid anchors: earnings gap, major high/low, breakout, capital event; anchor reason required. |
| TECH-VP-035 | Approximate volume profile | Aggregate volume by price bin using typical price; POC=max-volume bin | OHLCV, bin width | lookback-specific | Bin width=max(0.5*ATR14,0.5%*price); daily-bar approximation, not tick-level profile. |
| TECH-52W-036 | 52-week range position | (Close - 252d low)/(252d high - 252d low) | close, 252d high/low | daily | Not meaningful if range is zero. |
| TECH-BASE-037 | Base depth | (Base high - Base low)/Base high | detected base bounds | event | Use only after deterministic base segmentation. |
| TECH-TIGHT-038 | Tight-close ratio | stdev(log returns last 10d)/stdev(log returns prior 50d) | returns | daily | Lower ratio indicates contraction; not sufficient for breakout. |
| TECH-BREAD-039 | Sector breadth | Sector members above SMA50 / Valid point-in-time members | constituent prices | daily | Also calculate SMA200 breadth. |
| TECH-LIQ-040 | Median dollar volume | median(Close*Volume over 63 sessions) | close, volume | daily | Low liquidity lowers level reliability and execution confidence. |

## Formula execution rules

- Use the exact registered definition and sign convention.
- Keep raw and normalized variants side by side where applicable.
- Show calculation inputs in the output packet.
- Do not score a formula whose denominator is `NOT_MEANINGFUL`.
- Record any proxy in `warnings` and reduce model-fit confidence.
