# Risk and Resilience Analysis Agent - Formula Registry

| ID | Metric | Formula | Required inputs | Frequency | Rules / caveats |
| --- | --- | --- | --- | --- | --- |
| RSK-VOL-001 | Annualized volatility | stdev(daily log returns_N)*sqrt(252) | adjusted returns | 20/63/252d | Higher raw value means higher risk; invert for resilience score. |
| RSK-DOWN-002 | Downside deviation | sqrt(mean(min(Return-MAR,0)^2))*sqrt(252) | returns, minimum acceptable return | 63/252d | Default MAR=0 daily unless mandate specifies otherwise. |
| RSK-BETA-003 | Market beta | cov(Stock, Benchmark)/var(Benchmark) | aligned returns | 252/756d | Use weekly beta cross-check for thin trading. |
| RSK-DBETA-004 | Downside beta | cov(Stock,Benchmark \| Benchmark<0)/var(Benchmark \| Benchmark<0) | aligned returns | 252/756d | Minimum 30 down-market observations. |
| RSK-CORR-005 | Market correlation | corr(Stock, Benchmark) | aligned returns | 252d | Context for diversification and beta stability. |
| RSK-MDD-006 | Maximum drawdown | min(CumulativeValue_t / RunningPeak_t - 1) | total-return index | 1y/3y/5y | Report peak date, trough date, and recovery. |
| RSK-REC-007 | Drawdown recovery time | Sessions from trough until prior peak is regained | total-return index | event | If unrecovered, report open duration. |
| RSK-VAR-008 | Historical VaR | negative quantile(Return, 1-confidence) | daily returns | 95% and 99%, 1d/10d | 10-day scaling by simulation preferred; sqrt-time only as approximation. |
| RSK-CVAR-009 | Expected shortfall | negative mean(Return \| Return <= VaR quantile) | daily returns | 95% and 99% | Minimum 500 observations preferred for tail stability. |
| RSK-GAP-010 | Worst overnight gap | min(Open_t/PriorClose_t - 1) | adjusted opens/closes | 1y/3y | Corporate-action and event mapping required. |
| RSK-ICOV-011 | Interest coverage | Normalized EBIT / Cash interest expense | EBIT, interest | TTM / annual | Below 1.5x mandatory solvency warning. |
| RSK-FCC-012 | Fixed-charge coverage | (EBIT + lease/rent charge) / (Interest + lease/rent charge) | EBIT, interest, rent/lease | annual / TTM | Use when lease obligations are material. |
| RSK-ND-013 | Net debt to EBITDA | (Debt + lease debt - excess cash) / Normalized EBITDA | debt, cash, EBITDA | quarterly / TTM | Not meaningful for negative EBITDA. |
| RSK-DFC-014 | Net debt to FCF | Net debt / Normalized FCF | net debt, FCF | annual / TTM | Not meaningful for FCF<=0; negative cash position reported separately. |
| RSK-RUN-015 | Cash runway | (Cash + committed undrawn liquidity) / Average monthly cash burn | cash, facilities, burn | quarterly | Only for negative FCF/burn; exclude restricted cash. |
| RSK-MAT-016 | Maturity-wall coverage | (Cash + expected FCF before maturity + committed liquidity) / Debt due within horizon | liquidity and maturity schedule | 12/24/36m | Values <1 imply refinancing need. |
| RSK-CUST-017 | Customer HHI | sum(customer_share_i^2) | customer shares | annual | Partial disclosure produces lower-bound HHI. |
| RSK-PROD-018 | Product HHI | sum(product_share_i^2) | product/segment shares | annual | Higher is more concentrated. |
| RSK-GEO-019 | Geographic concentration HHI | sum(geography_share_i^2) | geographic shares | annual | Pair with FX/regulatory exposure. |
| RSK-ACCR-020 | Accrual ratio | (Net income - Operating cash flow) / Average total assets | NI, OCF, assets | annual | High positive accruals are a warning. |
| RSK-DSRI-021 | Beneish DSRI | (Receivables_t/Revenue_t)/(Receivables_t-1/Revenue_t-1) | receivables, revenue | annual | One component of M-score; forensic flag, not proof of manipulation. |
| RSK-GMI-022 | Beneish GMI | GrossMargin_t-1 / GrossMargin_t | gross margins | annual | Component of M-score. |
| RSK-AQI-023 | Beneish AQI | [1-(CurrentAssets+PPE)/Assets]_t / same_t-1 | assets/current assets/PPE | annual | Component of M-score; definition requires consistent fields. |
| RSK-SGI-024 | Beneish SGI | Revenue_t / Revenue_t-1 | revenue | annual | Component of M-score. |
| RSK-DEPI-025 | Beneish DEPI | DepRate_t-1 / DepRate_t; DepRate=Depreciation/(Depreciation+NetPPE) | depreciation, PPE | annual | Component of M-score. |
| RSK-SGAI-026 | Beneish SGAI | (SG&A_t/Revenue_t)/(SG&A_t-1/Revenue_t-1) | SG&A, revenue | annual | Component of M-score. |
| RSK-LVGI-027 | Beneish LVGI | Leverage_t / Leverage_t-1 | debt, assets | annual | Component of M-score. |
| RSK-TATA-028 | Beneish TATA | (Income from operations - OCF) / Total assets | operating income, OCF, assets | annual | Component of M-score. |
| RSK-MSCR-029 | Beneish M-score | -4.84 +0.920*DSRI +0.528*GMI +0.404*AQI +0.892*SGI +0.115*DEPI -0.172*SGAI +4.679*TATA -0.327*LVGI | eight Beneish components | annual | Classic threshold near -1.78 is a screening flag only; exclusions/adapters required. |
| RSK-ALT-030 | Altman Z double-prime | 6.56*WC/TA +3.26*RE/TA +6.72*EBIT/TA +1.05*BookEquity/TL | working capital, assets, retained earnings, EBIT, equity, liabilities | annual / TTM | Non-financial companies; use industry/country caveats. |
| RSK-PIO-031 | Piotroski F-score | Sum of 9 binary profitability, leverage/liquidity, and efficiency signals | two-year statements | annual | 0-9 diagnostic; define each signal in audit output. |
| RSK-DIL-032 | Diluted-share CAGR | (Shares_end/Shares_begin)^(1/n)-1 | diluted shares | 3y / 5y | Positive values are dilution risk. |
| RSK-SBC-033 | SBC to FCF | Stock-based compensation / max(abs(FCF), materiality_floor) | SBC, FCF | annual / TTM | Show sign and do not call negative-FCF denominator meaningful. |
| RSK-CYC-034 | Macro sensitivity beta | OLS coefficient of company metric changes or stock excess returns on macro-factor changes | company/market and macro series | monthly / quarterly | Require economic rationale, sufficient observations, and stability check. |
| RSK-THESIS-035 | Thesis-killer priority | Probability * Impact * (1-Detectability) * TimeUrgency | explicit 0-1 assumptions | event/risk | Custom prioritization. Report components separately; no false precision. |

## Formula execution rules

- Use the exact registered definition and sign convention.
- Keep raw and normalized variants side by side where applicable.
- Show calculation inputs in the output packet.
- Do not score a formula whose denominator is `NOT_MEANINGFUL`.
- Record any proxy in `warnings` and reduce model-fit confidence.
