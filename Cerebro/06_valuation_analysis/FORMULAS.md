# Valuation Analysis Agent - Formula Registry

| ID | Metric | Formula | Required inputs | Frequency | Rules / caveats |
| --- | --- | --- | --- | --- | --- |
| VAL-NORM-001 | Normalized EBIT | Reported EBIT - unusual gains + recurring/unusual expenses retained + normalization adjustments | reported EBIT and adjustment schedule | annual / TTM | Every adjustment reconciles to reported numbers. |
| VAL-RD-002 | Capitalized R&D asset | sum(R&D_t-j * (1 - j/UsefulLife), j=0..UsefulLife-1) | historical R&D, useful life | annual | Useful life is an industry assumption; adjust EBIT by adding current R&D and subtracting amortization. |
| VAL-RDA-003 | R&D amortization | sum(Historical R&D cohorts / UsefulLife) | R&D cohorts, useful life | annual | Straight-line default unless evidence supports another pattern. |
| VAL-LEASE-004 | Lease debt | PV of future operating lease commitments discounted at pre-tax debt rate | lease schedule, debt rate | annual / current | Add imputed interest and adjust operating expense consistently. |
| VAL-FCFF-005 | Free cash flow to firm | EBIT*(1-TaxRate) + D&A - Capex - Change in non-cash NWC | EBIT, tax, D&A, capex, NWC | annual forecast | Use normalized operating items and consistent lease/R&D treatment. |
| VAL-NWC-006 | Non-cash working capital | Operating current assets excluding cash - Operating current liabilities excluding debt | working-capital accounts | annual / quarterly | Exclude financing items; use revenue-linked drivers. |
| VAL-WACC-007 | WACC | E/(D+E)*CostEquity + D/(D+E)*PreTaxCostDebt*(1-TaxRate) | market equity, debt, costs, tax | current | Include lease debt and preferred/minority components where material. |
| VAL-KE-008 | Cost of equity | RiskFreeRate + BottomUpBeta*EquityRiskPremium + CountryRiskPremium_if_justified | risk-free, beta, ERP, CRP | current | All inputs in same currency/inflation regime. |
| VAL-UBETA-009 | Unlevered beta | LeveredBeta / (1 + (1-TaxRate)*Debt/Equity) | peer beta, debt/equity, tax | current | Use operating-company peers; adjust cash/business mix where practical. |
| VAL-LBETA-010 | Relevered bottom-up beta | MedianUnleveredBeta * (1 + (1-TaxRate)*TargetDebt/Equity) | peer unlevered beta, target leverage | current | Use target or sustainable leverage, not a temporary spike. |
| VAL-KD-011 | Pre-tax cost of debt | RiskFreeRate + DefaultSpread from market yield or synthetic rating | risk-free, spread/rating | current | Synthetic rating derived from interest coverage when market debt yield unavailable. |
| VAL-TVG-012 | Gordon terminal value | FCFF_N*(1+g)/(WACC-g) | terminal FCFF, g, WACC | model | Require g<WACC and long-run economic consistency. |
| VAL-TVE-013 | Exit-multiple terminal value | Terminal metric * Selected normalized exit multiple | terminal EBITDA/revenue/earnings, multiple | model | Cross-check only; multiple must be justified by terminal fundamentals. |
| VAL-EV-014 | FCFF enterprise value | sum(FCFF_t/(1+WACC)^t) + TerminalValue/(1+WACC)^N | FCFF forecast, WACC, terminal value | model | Use mid-year convention only if explicitly selected. |
| VAL-EQ-015 | Equity value bridge | EnterpriseValue + Cash + NonOperatingAssets - Debt - LeaseDebt - Preferred - MinorityInterest - OtherDebtLikeClaims | EV and capital structure | model | Avoid double counting operating cash and non-operating assets. |
| VAL-PS-016 | Value per diluted share | EquityValue / FullyDilutedShares | equity value, diluted shares | model | Options/convertibles treated consistently. |
| VAL-FCFE-017 | Free cash flow to equity | NetIncome + D&A - Capex - ChangeNWC + NetBorrowing | equity cash-flow drivers | annual forecast | Discount at cost of equity; leverage policy must be stable or modeled. |
| VAL-FCFEV-018 | FCFE equity value | sum(FCFE_t/(1+CostEquity)^t) + TerminalFCFE/(CostEquity-g)/(1+CostEquity)^N | FCFE, cost equity, g | model | Use when debt policy is meaningful and FCFE is stable. |
| VAL-APV-019 | Adjusted present value | Value of unlevered operations + PV(Tax shields) - PV(Expected distress costs) + Financing side effects | unlevered FCF, unlevered cost, debt schedule | model | Useful when leverage changes materially. |
| VAL-EVA-020 | Economic profit | (ROIC_t - WACC_t) * InvestedCapital_t-1 | ROIC, WACC, capital | annual forecast | Also equals NOPAT - WACC*beginning invested capital. |
| VAL-EVAEV-021 | Economic-profit enterprise value | InvestedCapital_0 + PV(Future economic profits) | capital and economic-profit forecast | model | Must reconcile to FCFF DCF under consistent assumptions. |
| VAL-RI-022 | Residual income | NetIncome_t - CostEquity*BeginningBookEquity_t | NI, cost equity, book equity | annual forecast | Useful for financials and firms where book value is meaningful. |
| VAL-RIV-023 | Residual-income equity value | BookEquity_0 + PV(Future residual income) | book equity, residual income, cost equity | model | Clean-surplus violations require adjustment. |
| VAL-DDM-024 | Gordon dividend value | Dividend_1/(CostEquity-g) | next dividend, cost equity, g | model | Only for stable payout and growth. |
| VAL-HDDM-025 | H-model dividend value | D0*(1+gL)/(Ke-gL) + D0*H*(gS-gL)/(Ke-gL) | dividend, short/long growth, half-life H, Ke | model | Mature transition companies only. |
| VAL-SOTP-026 | Sum-of-the-parts equity value | sum(Segment enterprise/equity values) + corporate assets - corporate claims - holding discount_if_justified | segment forecasts/peers, corporate items | model | Avoid applying a multiple to consolidated metrics twice. |
| VAL-RDCF-027 | Reverse DCF | Solve for revenue growth, margin, ROIC, or duration such that modeled per-share value = current price | current price and DCF model | current | Return implied assumptions, not a target. |
| VAL-PEG-028 | PEG | Forward P/E / Expected EPS growth percent | P/E, growth in percentage points | current | Not meaningful for negative earnings or unstable growth; definitions vary. |
| VAL-EY-029 | Earnings yield | Normalized EPS / Price | EPS, price | current | Compare to cost of equity and history; negative earnings not meaningful. |
| VAL-FCFY-030 | FCF yield | Equity FCF / Market capitalization or FCFF / Enterprise value | FCF, market value | current | State whether equity or enterprise basis. |
| VAL-JPB-031 | Justified price-to-book | (ROE - g)/(CostEquity - g) | ROE, g, cost equity | stable model | Requires stable ROE, payout, and g<Ke. |
| VAL-JPE-032 | Justified P/E | (1 - g/ROE)/(CostEquity - g) | g, ROE, cost equity | stable model | Derived from fundamental payout; not valid when ROE<=0 or g>=Ke. |
| VAL-JEVS-033 | Justified EV/Sales | AfterTaxOperatingMargin * (1 - g/ROIC)/(WACC - g) | margin, g, ROIC, WACC | stable model | Shows why growth without excess returns may not justify a high multiple. |
| VAL-REL-034 | Relative-valuation regression | Multiple = a + b1*Growth + b2*Margin + b3*ROIC + b4*Risk + error | peer multiples and fundamentals | current | Require sufficient peers, diagnostics, and out-of-sample caution. |
| VAL-ZHIST-035 | Historical valuation z-score | (Current multiple - Historical median)/RobustScale | current and historical multiple | current | RobustScale=1.4826*MAD; history must be economically comparable. |
| VAL-SCEN-036 | Scenario-weighted value | sum(Probability_i * Value_i) | scenario probabilities and values | model | Probabilities sum to 1 and remain disclosed. |
| VAL-MC-037 | Monte Carlo valuation | Simulate correlated drivers and recalculate value across trials | driver distributions/correlations | model | Distributions must be economically bounded; report percentiles and seed. |
| VAL-ROPT-038 | Real-option value | Option-pricing or decision-tree value of staged project after avoiding DCF double count | project PV, investment, volatility, time, risk-free, probabilities | project | Only for material, separable, discretionary projects. |
| VAL-CONV-039 | Convertible dilution | If-converted shares and debt/interest adjustment under scenario; use treasury method for options | convertible terms, price scenarios | model | Apply scenario-consistent dilution. |
| VAL-MOS-040 | Margin of safety | (Estimated value - Current price) / Estimated value | scenario value, price | current | Report by scenario; negative means price above value. |
| VAL-IRR-041 | Implied investor IRR | IRR of purchase price, forecast distributions/buybacks if modeled, and terminal per-share value | price and equity cash flows | scenario | Terminal value and holding period disclosed. |
| VAL-TVS-042 | Terminal-value share | PV(Terminal value) / Enterprise value | PV terminal value, EV | model | High share signals sensitivity; flag >75% by default. |
| VAL-REINV-043 | Terminal reinvestment consistency | Terminal reinvestment rate = g / Terminal ROIC | g, terminal ROIC | model | Use to derive terminal FCFF and prevent free growth. |
| VAL-ENSEMBLE-044 | Reliability-weighted valuation ensemble | sum(Model value_i * Reliability weight_i) / sum(Reliability weights) | model values and reliability scores | model | Weights based on model fit, data quality, terminal share, and dispersion; never arbitrary. |

## Formula execution rules

- Use the exact registered definition and sign convention.
- Keep raw and normalized variants side by side where applicable.
- Show calculation inputs in the output packet.
- Do not score a formula whose denominator is `NOT_MEANINGFUL`.
- Record any proxy in `warnings` and reduce model-fit confidence.
