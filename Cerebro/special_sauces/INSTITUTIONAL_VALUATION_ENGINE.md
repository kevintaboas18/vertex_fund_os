# Ruta 2030 Institutional Valuation and Price-Level Engine

## Public, audit-ready methods for a non-speculative stock-analysis agent

**Version 2.0**

This document collects publicly documented institutional-grade valuation, normalization, risk, and price-level techniques. It does not claim access to proprietary formulas or secret models from any investment bank, hedge fund, or asset manager. The advantage comes from disciplined implementation, explicit assumptions, model selection, cross-checks, and a complete audit trail.

---

# 1. The operating principle: no hidden assumptions

Every valuation conclusion must be decomposable into:

1. reported facts;
2. mechanical calculations;
3. external estimates with timestamps;
4. explicit model assumptions;
5. model-selection rules;
6. sensitivity and failure conditions.

The agent must never say a stock is "cheap" or "expensive" without identifying the model, normalized cash flow, discount rate, growth path, capital intensity, diluted shares, and range of outcomes.

## 1.1 Evidence labels

- `R`: reported;
- `C`: calculated;
- `E`: external estimate;
- `A`: model assumption;
- `Q`: qualitative context, not scored by itself.

## 1.2 The valuation range, not the magic target

The output is a distribution or range:

```text
Bear value
Base value
Bull value
Scenario-weighted value
Monte Carlo p10 / p25 / median / p75 / p90
Current-price implied assumptions
```

A single target is allowed only as a labeled midpoint of a disclosed range.

---

# 2. Select the correct model before calculating anything

| Business type | Preferred primary methods | Critical cross-check |
|---|---|---|
| Mature non-financial company | FCFF DCF; economic profit | FCFE, justified multiples, reverse DCF |
| Stable debt policy | FCFE | FCFF reconciliation |
| Rapidly changing leverage | APV | FCFF with explicit debt path |
| Bank | Residual income / excess return / DDM | P/B versus sustainable ROE |
| Insurer | Excess return / DDM | Combined ratio, reserve quality, book value |
| REIT | NAV and AFFO DCF | Cap rates, P/AFFO, dividend capacity |
| Conglomerate | SOTP | Consolidated DCF |
| Commodity producer | NAV and mid-cycle DCF | Cost curve, reserve life, replacement value |
| Pre-revenue biotech | Risk-adjusted NPV / decision tree | Cash runway and dilution |
| High-growth pre-profit company | Scenario DCF with explicit path to margin | Reverse DCF and unit economics |

A sophisticated formula used on the wrong business is still a bad model.

---

# 3. Clean the accounting before valuing the business

## 3.1 Normalize operating income

Start with reported EBIT and reconcile every adjustment:

```text
Normalized EBIT
= Reported EBIT
- unusual operating gains
+ truly non-recurring operating charges removed from the base case
+ recurring costs incorrectly labeled non-recurring
+ lease/R&D adjustments when the selected model requires them
```

An item that appears in three of five years is usually recurring for base-case purposes.

## 3.2 Capitalize R&D when it behaves like an investment

For a useful life of `L` years:

```text
R&D asset = sum(R&D_(t-j) * (1 - j/L)), j=0...L-1
R&D amortization = sum(R&D historical cohorts / L)
Adjusted EBIT = Reported EBIT + Current R&D - R&D amortization
Adjusted invested capital = Reported invested capital + R&D asset
```

The useful life is an assumption. Software may use a shorter life than pharmaceuticals or aerospace research. Show sensitivity.

## 3.3 Treat operating leases consistently

```text
Lease debt = present value of future lease commitments at pre-tax debt rate
Adjusted operating income = Reported operating income + lease expense - imputed depreciation
Imputed lease interest = Lease debt_begin * pre-tax cost of debt
```

If leases are added to debt, the operating earnings and cash-flow treatment must also be adjusted. Partial adjustment creates an inconsistent WACC/FCFF model.

## 3.4 Stock-based compensation

Do not silently add SBC back as if it were free. Show:

- FCF including SBC as an expense;
- SBC/revenue and SBC/FCF;
- expected future dilution;
- share-count bridge under treasury-stock or if-converted methods.

## 3.5 Cyclical normalization

For commodity and cyclical businesses, estimate mid-cycle:

- realized price;
- volume/utilization;
- margin;
- sustaining capex;
- working capital;
- tax;
- normalized ROIC.

Do not apply a low multiple to peak earnings and call it value.

---

# 4. ROIC, WACC, and economic value creation

## 4.1 NOPAT

```text
NOPAT = Normalized EBIT * (1 - normalized cash tax rate)
```

## 4.2 Invested capital

Two equivalent reconciliations should approximately agree:

```text
Financing view:
Invested capital = Debt + Equity - Excess cash + Debt-like operating claims

Operating view:
Invested capital = Operating assets - Operating liabilities
```

Use average beginning/ending invested capital for annual ROIC.

## 4.3 ROIC

```text
ROIC = NOPAT / Average invested capital
```

## 4.4 Value-creation spread

```text
ROIC spread = ROIC - WACC
Economic value created = (ROIC - WACC) * Average invested capital
```

A company can report accounting profit while destroying economic value when ROIC is below WACC.

## 4.5 Incremental ROIC

```text
Incremental ROIC = change in NOPAT / change in invested capital
```

Use a three- to five-year change to reduce noise. Incremental ROIC is often more informative than a high legacy ROIC because it measures the returns on new capital.

## 4.6 Growth consistency

```text
Fundamental growth ~= Reinvestment rate * ROIC
Terminal reinvestment rate = Terminal growth / Terminal ROIC
```

The model cannot grant growth without paying for reinvestment.

---

# 5. Build the discount rate correctly

## 5.1 Weighted average cost of capital

```text
WACC = E/(D+E) * Cost of equity
     + D/(D+E) * Pre-tax cost of debt * (1 - tax rate)
```

Add preferred stock and minority-interest components when material.

## 5.2 Cost of equity

```text
Cost of equity = Risk-free rate + Bottom-up beta * Equity risk premium + justified country risk premium
```

Rules:

- use a risk-free rate in the same currency as cash flows;
- use an ERP appropriate to the valuation currency/market;
- avoid arbitrary company-specific premiums that double-count risks already in cash flows;
- use scenario cash flows for diversifiable operating risks when possible.

## 5.3 Bottom-up beta

For each comparable company:

```text
Unlevered beta = Levered beta / (1 + (1-tax rate)*Debt/Equity)
```

Then use the peer median and relever to the target capital structure:

```text
Relevered beta = Median unlevered beta * (1 + (1-tax rate)*Target Debt/Equity)
```

## 5.4 Cost of debt and synthetic rating

Use traded debt yield when reliable. Otherwise map interest coverage to a synthetic default spread:

```text
Interest coverage = EBIT / Cash interest expense
Pre-tax cost of debt = Risk-free rate + Default spread
```

The mapping table must be dated and sourced. Below 1.5x is a mandatory solvency warning in this system.

## 5.5 WACC sensitivity

At minimum show:

- base WACC;
- +/-100 basis points;
- alternate beta and debt-spread scenarios;
- effect on terminal value and per-share value.

---

# 6. FCFF discounted cash flow

## 6.1 FCFF

```text
FCFF = EBIT*(1-tax rate) + D&A - Capex - change in non-cash working capital
```

Alternatively:

```text
FCFF = NOPAT - Reinvestment
```

The two forms should reconcile.

## 6.2 Explicit forecast

Forecast the drivers, not just the output:

```text
Revenue = Units or users * price/ARPU * mix
Operating income = Revenue * operating margin
NOPAT = Operating income * (1-tax rate)
Reinvestment = Sales-to-capital or g/ROIC framework
FCFF = NOPAT - Reinvestment
```

## 6.3 Enterprise value

```text
Enterprise value
= sum(FCFF_t / (1+WACC)^t, t=1...N)
+ Terminal value / (1+WACC)^N
```

Mid-year convention may be used only when the cash-flow timing assumption is explicit.

## 6.4 Gordon terminal value

```text
Terminal value = FCFF_(N+1) / (WACC - g)
FCFF_(N+1) = FCFF_N * (1+g)
```

Required checks:

- `g < WACC`;
- terminal growth is consistent with long-run nominal economic growth in the valuation currency;
- terminal margin is economically defensible;
- terminal ROIC and reinvestment support g;
- terminal value does not hide an abrupt unexplained improvement.

## 6.5 Exit-multiple terminal value

```text
Terminal value = Terminal EBITDA or revenue * justified terminal multiple
```

Use only as a cross-check. The multiple must match terminal growth, margins, ROIC, and risk. A current-cycle multiple cannot be copied blindly into perpetuity.

## 6.6 Terminal-value share

```text
Terminal share = PV(Terminal value) / Enterprise value
```

A share above 75% triggers a high-sensitivity warning in this system.

## 6.7 Enterprise-to-equity bridge

```text
Equity value
= Enterprise value
+ Cash and non-operating assets
- Debt
- Lease debt
- Preferred stock
- Minority interest
- Underfunded pensions and other debt-like claims
```

## 6.8 Fully diluted per-share value

```text
Per-share value = Equity value / Fully diluted shares
```

Model options, RSUs, warrants, and convertibles consistently with price scenarios.

---

# 7. FCFE valuation

```text
FCFE = Net income + D&A - Capex - change in NWC + Net borrowing
Equity value = PV(FCFE at cost of equity) + PV(terminal FCFE)
```

Use FCFE when leverage policy is stable and equity cash flow is meaningful. Avoid combining FCFE with WACC.

---

# 8. Adjusted present value

```text
APV = Value of unlevered operations
    + PV(interest tax shields)
    - PV(expected financial-distress costs)
    + other financing side effects
```

APV is valuable when debt changes materially, such as leveraged transactions, recapitalizations, or large project financing.

---

# 9. Economic-profit valuation

```text
Economic profit_t = NOPAT_t - WACC_t * Invested capital_(t-1)
                   = (ROIC_t - WACC_t) * Invested capital_(t-1)

Enterprise value = Invested capital_0 + PV(future economic profits)
```

Under consistent assumptions, this should reconcile to FCFF DCF. A mismatch exposes a modeling error in capital, reinvestment, timing, or terminal assumptions.

---

# 10. Residual-income and excess-return valuation

For banks and other firms where book equity is economically meaningful:

```text
Residual income_t = Net income_t - Cost of equity * Beginning book equity_t
Equity value = Book equity_0 + PV(future residual income)
```

A stable-growth justified price-to-book relationship:

```text
Justified P/B = (ROE - g) / (Cost of equity - g)
```

This explains why high sustainable ROE relative to the cost of equity deserves a premium to book.

---

# 11. Dividend models

## 11.1 Gordon growth

```text
Value = Dividend_1 / (Cost of equity - g)
```

Use only when payout and growth are stable.

## 11.2 H-model transition

```text
Value = D0*(1+g_long)/(Ke-g_long)
      + D0*H*(g_short-g_long)/(Ke-g_long)
```

`H` is half the expected transition period. Use for a gradual decline from high to stable growth.

---

# 12. Sum-of-the-parts

```text
SOTP value = sum(segment values)
           + corporate non-operating assets
           - corporate debt and other claims
           - justified holding-company discount, if any
```

Each segment may use a different model. Avoid double counting central costs, cash, debt, or intersegment eliminations.

---

# 13. Reverse DCF: let the price reveal the market's assumptions

Set modeled per-share value equal to current price and solve for one or more unknowns:

```text
Implied revenue CAGR
Implied terminal operating margin
Implied years of high growth
Implied terminal ROIC
Implied market share
```

Then compare the implied assumptions with:

- historical performance;
- consensus forecasts;
- TAM and share capacity;
- reinvestment and ROIC;
- peer economics;
- current technical validation.

Reverse DCF is one of the best antidotes to the vague statement "the stock is expensive." It says exactly what the price requires.

---

# 14. Fundamental multiples

## 14.1 Justified P/E

```text
Justified P/E = (1 - g/ROE) / (Cost of equity - g)
```

This is a stable-growth relationship. It requires positive sustainable ROE and `g < Cost of equity`.

## 14.2 Justified EV/Sales

```text
Justified EV/Sales
= After-tax operating margin * (1 - g/ROIC) / (WACC - g)
```

This formula exposes why fast growth with poor returns may not deserve a premium multiple.

## 14.3 Cash-flow and earnings yields

```text
Earnings yield = Normalized EPS / Price
Equity FCF yield = Equity FCF / Market capitalization
Enterprise FCF yield = FCFF / Enterprise value
```

Use the correct denominator and compare with required returns, history, and peers.

## 14.4 Peer regression

Instead of comparing a multiple with a simple peer average:

```text
Multiple = a + b1*Growth + b2*Margin + b3*ROIC + b4*Risk + error
```

Use the fitted value as a quality-adjusted relative benchmark. Require enough peers, robust diagnostics, and disclosure of outliers.

---

# 15. Historical valuation with robust statistics

```text
Robust z-score = (Current multiple - Historical median) / (1.4826 * MAD)
```

Historical comparison is valid only if business mix, accounting, margins, growth, rates, and capital structure are economically comparable.

---

# 16. Scenario trees and Monte Carlo

## 16.1 Discrete scenarios

At minimum:

| Driver | Bear | Base | Bull |
|---|---|---|---|
| Revenue growth | Lower | Evidence-based | Higher but capacity-tested |
| Margin | Compression | Normalization | Operating leverage |
| ROIC | Erodes | Stable | Improves |
| WACC | Higher | Base | Lower only with justification |
| Dilution | Higher | Expected | Lower |
| Terminal growth | Conservative | Long-run consistent | Still below WACC |

```text
Scenario-weighted value = sum(probability_i * value_i)
```

## 16.2 Monte Carlo

Simulate correlated drivers, not independent fantasy distributions. Typical drivers:

- revenue growth;
- margin;
- sales-to-capital or ROIC;
- WACC;
- terminal growth;
- dilution;
- event success probability.

Use bounded distributions and report the random seed, trial count, correlations, and percentiles.

---

# 17. Real options and staged investments

Use real-option logic only when management can defer, expand, contract, or abandon a separable project.

Potential cases:

- drug pipeline programs;
- natural-resource reserves;
- undeveloped land;
- technology platforms with staged commercialization;
- patents with uncertain future markets.

Avoid double counting: if the project cash flows already include the option value, do not add a separate option.

---

# 18. Distress, financing, and dilution adjustments

## 18.1 Solvency

```text
Interest coverage = EBIT / Interest expense
Fixed-charge coverage = (EBIT + lease charge)/(interest + lease charge)
Maturity coverage = (cash + expected pre-maturity FCF + committed liquidity)/debt due
```

## 18.2 Cash runway

```text
Cash runway months = (Cash + committed liquidity) / Monthly cash burn
```

## 18.3 Expected distress adjustment

For high-risk cases:

```text
Expected equity value
= Probability of survival * Going-concern equity value
+ Probability of distress * Recovery equity value
```

Do not bury failure probability inside an arbitrary WACC premium alone.

## 18.4 Dilution

Model:

- stock-based compensation;
- employee options and RSUs;
- warrants;
- convertible debt;
- expected capital raises;
- earn-outs and contingent consideration.

Per-share value can fall even when enterprise value rises.

---

# 19. Reliability-weighted model ensemble

Do not average models equally. Score each method on:

1. business-model fit;
2. data quality;
3. forecast visibility;
4. terminal-value dependence;
5. capital-structure consistency;
6. reconciliation with alternative methods;
7. model dispersion.

```text
Ensemble value = sum(Model value_i * Reliability weight_i) / sum(Reliability weights)
```

Show every model value and weight. If dispersion is wide, report low confidence rather than forcing precision.

---

# 20. Technical price-level engine

Valuation explains what the business may be worth. Technical levels explain where supply, demand, and confirmation have appeared. They remain independent.

## 20.1 Repeated-touch support and resistance

```text
Daily pivot k=3; weekly pivot k=2
ATR zigzag reversal = 1.5*ATR14
Zone tolerance = max(0.50*ATR14_at_pivot, 0.75%*pivot_price)
Independent daily touches >=5 sessions apart
Valid rejection >=0.5 ATR within 3 sessions
Confirmed level >=2 independent rejected touches
Strong level >=3 touches, or 2 touches + reaction/volume confirmation
```

## 20.2 Strength score

```text
N_eff = sum(exp(-ln(2)*age/126))
Strength =
  30*min(N_eff/4,1)
+ 20*min(median_reaction_ATR/2,1)
+ 15*min(median_volume_ratio/1.5,1)
+ 15*exp(-ln(2)*age_latest/126)
+ 10 weekly or 5 daily
+ 10*min(confluence_count/3,1)
```

## 20.3 Breakout confirmation

```text
Close > resistance_high + 0.25*ATR14
Volume ratio >=1.5
Two closes above, OR one close plus 3 sessions without closing inside zone
```

A close back inside/below the zone within three sessions is a failed breakout.

## 20.4 Important-level table

The final system reports:

- current price;
- nearest three support and resistance zones;
- exact touch dates;
- distance in percent and ATR;
- moving averages;
- anchored VWAPs;
- material earnings gaps;
- volume-profile nodes;
- Bear/Base/Bull valuation ranges;
- margin-of-safety references;
- confirmation and invalidation conditions.

It never averages technical resistance with intrinsic value.

---

# 21. Worked mini-example

Assume:

```text
Current price = 80
Normalized EBIT next year = 150
Tax rate = 25%
D&A = 20
Capex = 35
Change in NWC = 10
WACC = 9%
Five-year explicit FCFF growth = 8%
Terminal growth = 3%
Net debt and other claims = 300
Diluted shares = 50
```

Year-1 FCFF:

```text
NOPAT = 150*(1-0.25) = 112.5
FCFF = 112.5 + 20 - 35 - 10 = 87.5
```

Build years 2-5 from explicit drivers, then:

```text
Terminal FCFF = FCFF_5*(1+0.03)
Terminal value = Terminal FCFF/(0.09-0.03)
Enterprise value = PV(explicit FCFF) + PV(terminal value)
Equity value = Enterprise value - 300
Per-share value = Equity value/50
```

The correct output is a sensitivity table, not only one number:

| WACC / g | 2% | 3% | 4% |
|---|---:|---:|---:|
| 8% | Recalculate | Recalculate | Recalculate |
| 9% | Recalculate | Base | Recalculate |
| 10% | Recalculate | Recalculate | Recalculate |

Then run reverse DCF at the market price of 80 to calculate the revenue growth and margin path the market is requiring.

---

# 22. Final quality-control checklist

## Accounting

- Reported-to-normalized reconciliation completed.
- R&D, leases, SBC, one-offs, and cyclicality treated consistently.
- Historical and forecast capital reconcile.

## Discount rate

- Same currency across cash flows, risk-free rate, ERP, and terminal growth.
- Bottom-up beta and debt cost documented.
- Capital weights use market values.

## Forecast

- Growth, margins, reinvestment, and ROIC are internally consistent.
- Market share does not exceed a validated TAM without explanation.
- Dilution and financing are explicit.

## Terminal value

- `g < discount rate`.
- Terminal reinvestment supports g.
- Terminal margin/ROIC are defensible.
- Terminal-value share and implied exit multiple are shown.

## Cross-checks

- FCFF reconciles with economic profit.
- Residual income is used for financials where appropriate.
- Reverse DCF exposes current-price assumptions.
- Peer multiple is adjusted for growth, margin, return, and risk.
- Scenario and Monte Carlo ranges are displayed.

## Price levels

- Repeated touches are independently dated.
- Zone widths are ATR-scaled.
- Breakouts require price, volume, and follow-through.
- Valuation and technical levels remain distinct.

---

# 23. Research references

- Ruta 2030: *The Wall Street Agent Rulebook*.
- Ruta 2030: *Analisis de Negocio*.
- Ruta 2030: *Agente de Analisis Financiero - Framework de Scoring Aprobado*.
- Ruta 2030: *Market Opportunity Scorecard*.
- Aswath Damodaran, NYU Stern public valuation papers and spreadsheets.
- U.S. Securities and Exchange Commission EDGAR/XBRL APIs and filing data.
- Andrew W. Lo, Harry Mamaysky, and Jiang Wang, *Foundations of Technical Analysis*.

---

# 24. Final warning

The system reduces unsupported discretion; it does not eliminate uncertainty. The most important outputs are not the score or target alone. They are the assumptions, sensitivities, invalidation conditions, and evidence that allow another analyst to reproduce or challenge the result.
