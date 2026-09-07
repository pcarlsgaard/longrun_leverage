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

## Revised regime-signal ranking (LAG2, 25 bp)

| Signal | CAGR | Equal-fee matched-leverage timing Δ |
|---|---:|---:|
| UPRO_SMA_TO_SP500 | 17.14% | +2.47 pp |
| VOL_BINARY | 15.01% | +0.44 pp |
| SP500_1X | 11.26% | +0.00 pp |
| UPRO_ALWAYS | 13.70% | +0.00 pp |

## Where the difference comes from

Price and legacy total-return SMA states differ on only 2.85% of sessions,
so the aggregate gap is produced by a small set of disagreement dates rather than
by a different average leverage budget. The stress table above names them.

The single most important is 1987. Under LAG2 the legacy total-return signal is
still leveraged into the 1987-10-19 crash while the price signal is already out,
because the price index crossed its moving average one session earlier. That one
session, not a broad improvement in timing, is what separates the two LAG2 CAGRs
in the table above (16.82% legacy versus 19.08% revised).

### Concentration of the revised advantage over always-on UPRO

| Lag | Total log advantage | Top 1 day | Top 5 days | Top 20 days | Largest month | Month share |
|---|---:|---:|---:|---:|---|---:|
| LAG1 | 0.8973 | 80.96% | 191.70% | 449.02% | 1987-10 | 76.59% |
| LAG2 | 1.1906 | 60.80% | 144.25% | 337.74% | 1987-10 | 47.96% |

These shares are fractions of the entire multi-decade advantage, ranked by
contribution to it. Both top-20 shares exceed 100%, which means those twenty
sessions produced more than the whole gap and the remaining ~10,000 sessions were
net negative: over almost all of its history this rule did not beat always-on
leverage. Read that before quoting the CAGR difference. `letf.null_model` tests
the same strategies against a matched-exposure random-timing null, and none of
them survives a correction for the size of the grid searched here.

## Interpretation

This is a methodological correction, not an optimized model, and not evidence that
the corrected rule works. The prior qualitative conclusion survives the correction:
price-only SMA timing remains the strongest parsimonious regime trigger in this
comparison, the 20-day absolute-volatility rule remains a weaker positive
comparator, and relative volatility, efficiency, trend-quality, low-churn and
Awesome Oscillator rules do not establish robust matched-leverage value.

The corrected SMA result is broad rather than knife-edge: 150/200/250-day price
SMAs all identify useful leverage states in parts of the grid, but their execution
sensitivity differs materially, and the 250-day rule can be stronger under LAG1
while weaker under LAG2. That is consistent with a slow regime phenomenon rather
than a precisely optimized cutoff — and equally consistent with a grid large
enough to contain favorable cells by chance.

So the correction leaves the central thesis directionally unchanged but
quantitatively revised: distributions belong in investment wealth, not in the
signal. Signal-source choice can materially affect individual transition dates,
especially around extreme events, even though the states disagree on only a small
fraction of days. The structured CSVs and `src/letf/price_signal_revision.py`
reproduce the corrected battery.

AO note: AO included: complete positive high/low observations in verified frozen S&P archive; no downloads.
