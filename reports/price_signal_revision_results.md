# Price-only signal revision

**Methodological correction:** all trend, SMA, realized-volatility, volatility-target, momentum, trend-quality and path/choppiness signals in this revision use unadjusted price-index data only. Strategy wealth, CAGR, drawdown, Sharpe and portfolio volatility continue to use distribution-inclusive total returns.

- S&P signal input: S&P 500 price index close.
- Nasdaq signal input: Nasdaq-100 price index close.
- `signal_price_return`: close-to-close price-index return, excluding distributions.
- `strategy_total_return`: actual simulated investment total return, including distributions.

## Primary UPRO 200-day SMA before/after audit

| Metric | Legacy total-return signal | Revised price-only signal |
|---|---:|---:|
| LAG1 CAGR | 19.59% | 18.20% |
| LAG1 terminal wealth | 1,266.8× | 795.5× |
| LAG1 max drawdown | -74.49% | -75.46% |
| LAG1 Sharpe | 0.579 | 0.552 |
| LAG1 fraction leveraged | 78.20% | 75.74% |
| LAG1 switches/year | 5.76 | 6.54 |
| LAG1 worst rolling 20y CAGR | 5.73% | 4.57% |
| LAG1 worst rolling 30y CAGR | 14.66% | 13.73% |
| LAG2 CAGR | 16.82% | 19.08% |
| LAG2 terminal wealth | 496.5× | 1,066.7× |

Price and legacy total-return SMA states differ on **2.85%** of matched trading days (LAG1); transition-state flags differ on **309** dates.

## Stress-event timing differences

- 1987 LAG1: legacy first risk-off 1987-10-16; price-only 1987-10-15.
- 1987 LAG2: legacy first risk-off 1987-10-16; price-only 1987-10-15.

The one-session earlier 1987 price-index cross is economically material: under LAG2 the revised price signal avoids the extreme Black Monday timing penalty that dominated the earlier total-return-signal delay result.

## Revised regime-signal ranking (LAG2, 25 bp)

| Signal | CAGR | Equal-fee matched-leverage timing Δ |
|---|---:|---:|
| UPRO_SMA_TO_SP500 | 17.14% | +2.47 pp |
| VOL_BINARY | 15.01% | +0.44 pp |
| SP500_1X | 11.26% | +0.00 pp |
| UPRO_ALWAYS | 13.70% | +0.00 pp |

## Interpretation

The prior qualitative conclusion **survives**, but important magnitudes change. Price-only SMA timing remains the strongest parsimonious regime trigger in the revised comparison, and the 20-day absolute-volatility rule remains a weaker positive comparator. Relative volatility, efficiency, trend-quality, low-churn and Awesome Oscillator rules do not establish robust matched-leverage value.

The corrected SMA result is still broad rather than knife-edge: 150/200/250-day price SMAs all identify useful leverage-management states in parts of the grid, but their execution sensitivity differs materially. The 200-day rule is not uniquely optimal, and the 250-day rule can be stronger under LAG1 while weaker under LAG2. This reinforces the interpretation of a slow trend/regime phenomenon rather than a precisely optimized cutoff.

The price-only correction therefore leaves the central thesis **directionally unchanged but quantitatively revised**: distributions belong in investment wealth, not in the signal. Signal-source choice can materially affect individual transition dates, especially around extreme events, even though price and total-return states disagree on only a small fraction of days.

This remains a methodological correction and falsification exercise, not an optimized model. The structured CSVs and `src/letf/price_signal_revision.py` reproduce the corrected battery.
