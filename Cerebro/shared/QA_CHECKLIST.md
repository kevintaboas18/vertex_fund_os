# Quality-Assurance Checklist

## Data

- [ ] Security, share class, currency, and exchange are correct.
- [ ] Filing dates use acceptance timestamps.
- [ ] Historical statements use latest valid restatement for current analysis.
- [ ] OHLCV is corporate-action adjusted.
- [ ] Benchmark and sector dates are aligned.
- [ ] Peer set is economically comparable.

## Formulas

- [ ] Formula IDs and versions are present.
- [ ] Units and signs reproduce.
- [ ] Average balance-sheet denominators are used where required.
- [ ] No division-by-zero or sign-change CAGR is hidden.
- [ ] WACC and terminal growth use one currency and inflation regime.
- [ ] Terminal growth is below WACC.

## Scores

- [ ] Dimension points reproduce.
- [ ] Coverage and confidence are separate.
- [ ] Missing values are not scored as neutral.
- [ ] Risk direction is inverted correctly.
- [ ] Gates and overrides are applied after raw scoring.

## Technical levels

- [ ] Every touch has a date and is independently separated.
- [ ] Zone width follows the registered ATR/percentage rule.
- [ ] Breakout status uses close, buffer, volume, and follow-through rules.
- [ ] Levels show distance in both percent and ATR.

## Final report

- [ ] No unsupported adjective.
- [ ] No single point estimate presented as certainty.
- [ ] Three thesis-killing risks are listed.
- [ ] Important levels are labeled as analytical references, not promises.
