# Business Analysis Agent - Formula Registry

| ID | Metric | Formula | Required inputs | Frequency | Rules / caveats |
| --- | --- | --- | --- | --- | --- |
| BUS-MIX-001 | Segment revenue share | Revenue_i / Total revenue | segment revenue, total revenue | annual / quarterly | Shares must sum to approximately 100% after eliminations. |
| BUS-REC-002 | Recurring revenue percentage | Recurring revenue / Total revenue | recurring revenue, revenue | annual / quarterly | Use issuer definition only if reconciled and stable; disclose definition changes. |
| BUS-CONC-003 | Largest-customer concentration | Revenue from largest customer / Total revenue | customer revenue, total revenue | annual | Flag >30%; do not infer undisclosed concentration. |
| BUS-HHI-004 | Customer concentration HHI | sum(customer_share_i^2) | all customer shares or disclosed approximation | annual | Shares expressed as decimals; partial HHI must be labeled lower bound. |
| BUS-HHI-005 | Product/segment HHI | sum(segment_share_i^2) | segment revenue shares | annual | Higher means more concentration; compare definition through time. |
| BUS-CAGR-006 | Revenue CAGR | (Revenue_end / Revenue_begin)^(1/n) - 1 | revenue start/end, years | 3y and 5y | Not meaningful across non-positive base. |
| BUS-GM-007 | Gross margin | Gross profit / Revenue | gross profit, revenue | annual / quarterly | Compare to direct peers and own history. |
| BUS-OM-008 | Operating margin | Normalized EBIT / Revenue | normalized EBIT, revenue | annual / quarterly | Show reported and normalized. |
| BUS-STAB-009 | Margin stability | stdev(Operating margin over 5 years) | 5 annual operating margins | annual | Lower is better; also report range and recession-year drawdown. |
| BUS-RANGE-010 | Margin range | max(margin_5y) - min(margin_5y) | 5 annual margins | annual | A range <=3 percentage points is a positive moat signal in the source framework. |
| BUS-NOPAT-011 | NOPAT | Normalized EBIT * (1 - normalized cash tax rate) | EBIT, tax rate | annual / TTM | Keep tax normalization explicit. |
| BUS-IC-012 | Average invested capital | average((Debt + Equity - Excess cash)_begin, (Debt + Equity - Excess cash)_end) | debt, equity, excess cash | annual / TTM | Reconcile to operating assets minus operating liabilities. |
| BUS-ROIC-013 | ROIC | NOPAT / Average invested capital | NOPAT, invested capital | annual / TTM | Use ROE/ROA adapters for banks and insurers. |
| BUS-SPREAD-014 | ROIC-WACC spread | ROIC - WACC | ROIC, WACC | annual / current | Positive and persistent indicates value creation. |
| BUS-EVA-015 | Economic value created | (ROIC - WACC) * Average invested capital | spread, invested capital | annual | Currency value; negative means economic value destruction. |
| BUS-IROIC-016 | Incremental ROIC | change in NOPAT / change in invested capital | multi-year NOPAT and invested capital | 3y rolling | Use multi-year change to reduce noise; not meaningful for negative denominator change. |
| BUS-FCFC-017 | Cumulative FCF conversion | sum(FCF_5y) / sum(Net income_5y) | FCF and net income | 5y rolling | A ratio near or above 1.0 supports earnings quality; sign changes require review. |
| BUS-REINV-018 | Reinvestment rate | (Net capex + change in non-cash working capital + capitalized R&D adjustment) / NOPAT | capex, D&A, NWC, R&D, NOPAT | annual | Clamp only after explicit review; unstable when NOPAT is near zero. |
| BUS-SG-019 | Fundamental sustainable growth | Reinvestment rate * ROIC | reinvestment rate, ROIC | annual | Consistency check, not a guaranteed forecast. |
| BUS-NRR-020 | Net revenue retention | (Beginning cohort revenue + expansion - contraction - churn) / Beginning cohort revenue | cohort revenue bridge | quarterly / annual | Subscription/business-model adapter only. |
| BUS-GRR-021 | Gross revenue retention | (Beginning cohort revenue - contraction - churn) / Beginning cohort revenue | cohort bridge | quarterly / annual | Excludes expansion. |
| BUS-CHURN-022 | Logo churn | Customers lost during period / Customers at period start | lost and beginning customers | monthly / quarterly | Annualize only with stable seasonality. |
| BUS-LTV-023 | Customer LTV | ARPU * Gross margin * Expected customer life | ARPU, gross margin, retention/churn | quarterly | State survival-model or 1/churn approximation. |
| BUS-CAC-024 | Customer acquisition cost | Sales and marketing acquisition spend / New customers added | acquisition spend, new customers | quarterly | Exclude servicing/retention spend only when disclosed. |
| BUS-LTVCAC-025 | LTV/CAC | Customer LTV / CAC | LTV, CAC | quarterly | Model-specific; show sensitivity to churn and gross margin. |
| BUS-PAYBACK-026 | CAC payback months | CAC / (Monthly ARPU * Gross margin) | CAC, ARPU, margin | quarterly | Lower is better; subscription adapter. |
| BUS-GUIDE-027 | Guidance accuracy | 1 - abs(Actual - Guidance midpoint) / max(abs(Guidance midpoint), materiality_floor) | actual, guidance midpoint | quarterly | Clip to [0,1]; guidance is context, not reported actual. |
| BUS-DIL-028 | Diluted share CAGR | (Diluted shares_end / Diluted shares_begin)^(1/n) - 1 | diluted shares | 3y / 5y | Positive growth is dilution; include net effect of buybacks and SBC. |
| BUS-ALLOC-029 | Capital-allocation value spread | Incremental ROIC - WACC | incremental ROIC, WACC | 3y rolling | Primary quantitative test of reinvestment quality. |
| BUS-SBC-030 | SBC burden | Stock-based compensation / Revenue | SBC, revenue | annual / TTM | Also report SBC/FCF and diluted-share trend. |

## Formula execution rules

- Use the exact registered definition and sign convention.
- Keep raw and normalized variants side by side where applicable.
- Show calculation inputs in the output packet.
- Do not score a formula whose denominator is `NOT_MEANINGFUL`.
- Record any proxy in `warnings` and reduce model-fit confidence.
