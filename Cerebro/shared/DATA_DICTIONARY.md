# Canonical Data Dictionary

## Financial statement conventions

| Canonical field | Definition |
|---|---|
| `revenue` | Net revenue from continuing operations |
| `cogs` | Cost directly associated with reported revenue |
| `ebit` | Operating income before interest and taxes, normalized where specified |
| `nopat` | Normalized EBIT multiplied by one minus the normalized cash tax rate |
| `operating_cash_flow` | Net cash from operating activities |
| `capex` | Cash purchases of property, plant, equipment, and capitalized internal-use assets as defined by the adapter |
| `fcf` | Operating cash flow minus capex unless an industry adapter specifies another cash-flow measure |
| `net_debt` | Interest-bearing debt plus debt-like obligations minus excess cash |
| `invested_capital` | Operating assets minus operating liabilities, reconciled to debt plus equity minus excess cash |
| `diluted_shares` | Weighted diluted shares plus in-the-money and as-converted claims where required |

## Market conventions

| Canonical field | Definition |
|---|---|
| `adj_open/high/low/close` | OHLC adjusted consistently for splits and cash distributions |
| `adj_volume` | Volume adjusted for splits where the provider supports it |
| `total_return` | Price return plus distributions, aligned to benchmark dates |
| `atr14` | Wilder 14-session average true range |
| `benchmark_return` | Total return of the selected broad benchmark |
| `sector_return` | Total return of the selected sector or industry benchmark |

## Null states

Use only these null states:

- `MISSING` - expected but absent;
- `NOT_APPLICABLE` - formula does not apply to the security type;
- `NOT_MEANINGFUL` - denominator or economics make the ratio misleading;
- `CONFLICTED` - material source conflict is unresolved;
- `STALE` - value exists but exceeds the permitted age;
- `NOT_SCORABLE` - evidence does not support a score.
