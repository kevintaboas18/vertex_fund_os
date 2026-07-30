# Financial Analysis Agent - Formula Registry

| ID | Metric | Formula | Required inputs | Frequency | Rules / caveats |
| --- | --- | --- | --- | --- | --- |
| FIN-GR-001 | YoY revenue growth | (Revenue_t - Revenue_t-1) / Revenue_t-1 | current and prior revenue | annual / quarterly | BAD <0%; GOOD 0-10%; EXCELLENT >10%, then peer check. |
| FIN-GR-002 | Revenue-growth trend | OLS slope of annual growth rates over 3 years | three annual growth rates | annual | Negative=decelerating, near zero=stable, positive=accelerating; materiality threshold 1 percentage point/year. |
| FIN-GR-003 | Growth versus competitors | Company revenue growth - peer median growth | company and peer growth | annual / TTM | Below/in-line/above peers using +/-2 percentage-point band. |
| FIN-GR-004 | Organic growth quality | Organic revenue growth / Total reported revenue growth | organic and total growth | quarterly / annual | If total growth <=0, classify from bridge rather than ratio. |
| FIN-GR-005 | Market-share trend | OLS slope of company market share over 3 years | three annual share observations | annual | Losing/stable/gaining using +/-0.25 percentage point/year unless market-specific adapter. |
| FIN-PR-006 | Net profit status | Normalized net income | net income and adjustments | annual / TTM | Loss, small/volatile profit, or strong consistent profit. |
| FIN-PR-007 | Gross margin | (Revenue - COGS) / Revenue | revenue, COGS | annual / quarterly | Default bands <20%, 20-40%, >40%; peer-sensitive. |
| FIN-PR-008 | Operating margin | Normalized operating income / Revenue | EBIT, revenue | annual / quarterly | Default bands <10%, 10-20%, >20%; peer-sensitive. |
| FIN-PR-009 | Net margin | Normalized net income / Revenue | net income, revenue | annual / quarterly | Default bands <5%, 5-10%, >10%; peer-sensitive. |
| FIN-PR-010 | Margin trend | OLS slope of normalized margin over 3 years | three annual margins | annual | Falling/flat/rising with 0.5 percentage-point/year materiality threshold. |
| FIN-PR-011 | Profit versus revenue growth | Net-income growth - Revenue growth | net-income and revenue growth | annual / TTM | Profit slower / approximately equal / faster; not meaningful across loss-to-profit sign change. |
| FIN-CF-012 | Free cash flow | Operating cash flow - Capital expenditures | OCF, capex | annual / TTM | Use industry adapter for banks, insurers, and REITs. |
| FIN-CF-013 | FCF growth | (FCF_t - FCF_t-1) / abs(FCF_t-1) | current and prior FCF | annual / TTM | If sign changes, report transition rather than percentage. |
| FIN-CF-014 | FCF margin | FCF / Revenue | FCF, revenue | annual / TTM | BAD <0%; GOOD 0-10%; EXCELLENT >10%, peer check. |
| FIN-CF-015 | Cash versus earnings | Operating cash flow / Net income | OCF, net income | annual and 3y cumulative | OCF<earnings, approx equal, OCF>earnings; cumulative ratio preferred. |
| FIN-CF-016 | Capital dependence | External financing need = max(0, Cash uses - OCF - Beginning excess cash) | OCF, capex, debt/equity issuance, cash | annual / forward 12m | Classify external dependence, self-funded, or funds growth plus returns cash. |
| FIN-BS-017 | Current ratio | Current assets / Current liabilities | current assets/liabilities | quarterly | BAD <1.0; GOOD 1.0-1.5; EXCELLENT 1.5-3.0; >3 may indicate idle capital. |
| FIN-BS-018 | Quick ratio | (Current assets - Inventory) / Current liabilities | current assets, inventory, current liabilities | quarterly | BAD <0.7; GOOD 0.7-1.0; EXCELLENT >1.0. |
| FIN-BS-019 | Debt to equity | Total interest-bearing debt / Shareholders equity | debt, equity | quarterly | BAD >2.0; GOOD 1.0-2.0; EXCELLENT <1.0; sector-sensitive and not meaningful with negative equity. |
| FIN-BS-020 | Interest coverage | Normalized EBIT / Cash interest expense | EBIT, interest expense | annual / TTM | BAD <1.5x; GOOD 1.5-3x; EXCELLENT >3x; mandatory warning below 1.5x. |
| FIN-BS-021 | Debt versus revenue trend | Debt growth - Revenue growth | debt and revenue growth | annual | Debt faster / in line / debt flat or declining. |
| FIN-BS-022 | Liquidity trend | OLS slope of current and quick ratios over 3 years | three-year liquidity ratios | annual | Deteriorating/stable/improving; reconcile seasonal working capital. |
| FIN-EF-023 | ROE | Net income / Average shareholders equity | net income, average equity | annual / TTM | BAD <8%; GOOD 8-15%; EXCELLENT >15%; high leverage may distort. |
| FIN-EF-024 | ROIC | NOPAT / Average invested capital | NOPAT, invested capital | annual / TTM | BAD <8%; GOOD 8-15%; EXCELLENT >15%; use adapters for financials. |
| FIN-EF-025 | ROA | Net income / Average total assets | net income, average assets | annual / TTM | BAD <3%; GOOD 3-8%; EXCELLENT >8%; useful for financial-sector adapters. |
| FIN-EF-026 | ROIC versus cost of capital | ROIC - WACC | ROIC, WACC | annual / current | BAD below WACC; GOOD approximately WACC (+/-1 pt); EXCELLENT above WACC. |
| FIN-EF-027 | Return trend | OLS slope of ROIC or adapter return over 3 years | three annual returns | annual | Falling/flat/rising with 1 percentage-point/year threshold. |
| FIN-DX-028 | Net debt to EBITDA | (Debt + lease debt - excess cash) / Normalized EBITDA | debt, cash, EBITDA | quarterly / TTM | Diagnostic and risk-agent input; not meaningful for negative EBITDA. |
| FIN-DX-029 | Debt to FCF | Net debt / Normalized FCF | net debt, FCF | annual / TTM | Diagnostic; not meaningful when FCF <=0. |
| FIN-DX-030 | Accrual ratio | (Net income - Operating cash flow) / Average total assets | NI, OCF, assets | annual | Higher positive accruals indicate lower cash quality. |
| FIN-DX-031 | Cash-conversion cycle | DSO + DIO - DPO | receivables, inventory, payables, revenue, COGS | quarterly / annual | Use average balances; compare to peers and history. |
| FIN-DX-032 | SBC to revenue | Stock-based compensation / Revenue | SBC, revenue | annual / TTM | Report with diluted-share CAGR. |
| FIN-DX-033 | Diluted-share CAGR | (Diluted shares_end / Diluted shares_begin)^(1/n)-1 | share history | 3y / 5y | Positive is dilution. |

## Formula execution rules

- Use the exact registered definition and sign convention.
- Keep raw and normalized variants side by side where applicable.
- Show calculation inputs in the output packet.
- Do not score a formula whose denominator is `NOT_MEANINGFUL`.
- Record any proxy in `warnings` and reduce model-fit confidence.
