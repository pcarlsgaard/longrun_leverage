# TQQQ governed by the S&P 500 price SMA

TQQQ/QQQ or TQQQ/T-bill rotation using the S&P 500 price-index SMA as a common
equity-risk signal, compared against the otherwise identical rule driven by the
Nasdaq-100's own SMA. Investment returns remain total-return based. No parameters
were optimized.

## Primary 200-day / 50-bp financing results

| Strategy | Lag | Cost | CAGR | Max DD | Sharpe | Leveraged days | Switches/yr |
|---|---|---:|---:|---:|---:|---:|---:|
| TQQQ_ALWAYS | LAG1 | 0 bp | 13.46% | -99.98% | 0.52 | 100.00% | 0.00 |
| TQQQ_NDX_SMA_TO_NASDAQ | LAG1 | 0 bp | 22.79% | -97.86% | 0.59 | 75.45% | 6.84 |
| TQQQ_NDX_SMA_TO_TBILL | LAG1 | 0 bp | 20.76% | -95.34% | 0.56 | 75.45% | 6.84 |
| TQQQ_SP500_SMA_TO_NASDAQ | LAG1 | 0 bp | 24.26% | -97.75% | 0.61 | 75.74% | 6.54 |
| TQQQ_SP500_SMA_TO_TBILL | LAG1 | 0 bp | 22.56% | -94.56% | 0.59 | 75.74% | 6.54 |
| TQQQ_NDX_SMA_TO_NASDAQ | LAG2 | 0 bp | 22.53% | -96.25% | 0.59 | 75.44% | 6.84 |
| TQQQ_NDX_SMA_TO_TBILL | LAG2 | 0 bp | 20.65% | -87.88% | 0.56 | 75.44% | 6.84 |
| TQQQ_SP500_SMA_TO_NASDAQ | LAG2 | 0 bp | 26.21% | -96.19% | 0.64 | 75.73% | 6.54 |
| TQQQ_SP500_SMA_TO_TBILL | LAG2 | 0 bp | 25.54% | -86.36% | 0.64 | 75.73% | 6.54 |
| TQQQ_NDX_SMA_TO_NASDAQ | LAG1 | 25 bp | 20.71% | -98.29% | 0.56 | 75.45% | 6.84 |
| TQQQ_NDX_SMA_TO_TBILL | LAG1 | 25 bp | 18.71% | -96.03% | 0.53 | 75.45% | 6.84 |
| TQQQ_SP500_SMA_TO_NASDAQ | LAG1 | 25 bp | 22.25% | -97.88% | 0.59 | 75.74% | 6.54 |
| TQQQ_SP500_SMA_TO_TBILL | LAG1 | 25 bp | 20.57% | -95.33% | 0.56 | 75.74% | 6.54 |
| TQQQ_NDX_SMA_TO_NASDAQ | LAG2 | 25 bp | 20.46% | -96.46% | 0.56 | 75.44% | 6.84 |
| TQQQ_NDX_SMA_TO_TBILL | LAG2 | 25 bp | 18.60% | -88.87% | 0.53 | 75.44% | 6.84 |
| TQQQ_SP500_SMA_TO_NASDAQ | LAG2 | 25 bp | 24.16% | -96.43% | 0.61 | 75.73% | 6.54 |
| TQQQ_SP500_SMA_TO_TBILL | LAG2 | 25 bp | 23.50% | -87.31% | 0.61 | 75.73% | 6.54 |

## How many of these comparisons are independent?

Holding the off-sleeve, SMA length, financing spread, execution lag and switching
cost fixed and varying only the signal index gives 144 paired comparisons. The
S&P signal has the higher CAGR in 138/144 (95.83%) of them.

**That fraction is not 144 pieces of evidence.**
At 200 days the two signals hold the same state on 89.75% of sessions, so
these are one pair of highly correlated signal paths re-scored under nuisance
parameters, over one history. The effective sample is one comparison. A win rate
near 100% across a parameter grid tells you the result is insensitive to those
parameters; it says nothing about how often the conclusion would hold on data
this study has not seen.

| SMA length | S&P signal wins | Comparisons |
|---|---:|---:|
| 150 days | 87.50% | 48 |
| 200 days | 100.00% | 48 |
| 250 days | 100.00% | 48 |

## The subperiod evidence, which is what actually varies

LAG2, 25 bp, rotating to Nasdaq 1x:

| Period | Nasdaq signal | S&P signal | Difference |
|---|---:|---:|---:|
| 1987_1999 | 39.41% | 53.52% | +14.11 pp |
| 2000_2009 | -15.61% | -16.43% | -0.82 pp |
| 2010_2019 | 27.70% | 35.35% | +7.64 pp |
| 2020_latest | 43.06% | 32.22% | -10.84 pp |

The S&P signal is not uniformly superior. It wins strongly in the earliest and
the 2010s blocks, is roughly a tie in 2000-2009, and loses substantially in
2020-latest, when the Nasdaq trend carried useful asset-specific information.
Four blocks, split two-one-one, is the honest sample size behind the headline
win rate above.

## Long-horizon cohort distributions: LAG2 / 25 bp

### 20-year CAGR

| Strategy | P1 | P10 | P25 | P50 | P75 | P90 | P99 |
|---|---:|---:|---:|---:|---:|---:|---:|
| TQQQ_NDX_SMA_TO_NASDAQ | 3.87% | 10.09% | 12.57% | 15.32% | 20.61% | 25.02% | 30.83% |
| TQQQ_NDX_SMA_TO_TBILL | 5.77% | 11.50% | 14.70% | 17.02% | 19.13% | 22.41% | 27.23% |
| TQQQ_SP500_SMA_TO_NASDAQ | 6.94% | 13.94% | 16.25% | 19.28% | 24.46% | 26.33% | 31.17% |
| TQQQ_SP500_SMA_TO_TBILL | 10.42% | 17.26% | 19.19% | 21.70% | 23.23% | 26.08% | 28.77% |

### 30-year CAGR

| Strategy | P1 | P10 | P25 | P50 | P75 | P90 | P99 |
|---|---:|---:|---:|---:|---:|---:|---:|
| TQQQ_NDX_SMA_TO_NASDAQ | 12.61% | 13.61% | 18.30% | 20.98% | 22.90% | 24.03% | 25.33% |
| TQQQ_NDX_SMA_TO_TBILL | 11.44% | 12.13% | 17.81% | 20.79% | 22.71% | 24.30% | 25.59% |
| TQQQ_SP500_SMA_TO_NASDAQ | 18.38% | 20.18% | 22.05% | 24.09% | 25.52% | 27.35% | 28.21% |
| TQQQ_SP500_SMA_TO_TBILL | 19.37% | 20.88% | 22.72% | 24.51% | 26.18% | 27.72% | 28.52% |

These cohorts overlap heavily. They are descriptive historical outcomes, not
independent probability draws, and early Nasdaq history retains the existing
proxy limitations.

## Interpretation

The experiment is consistent with leverage intensity being governable by a common
broad-equity risk regime rather than requiring an asset-specific trend signal, and
the same parsimonious signal governing both S&P and Nasdaq leverage is an
attractive property if it holds. It is not established here.

Two things stop that conclusion from being a finding. The 2020-latest block
reverses in favor of the Nasdaq-specific signal, which is the falsification
caveat. And the grid win rate is one comparison, not a hundred and forty-four.
`letf.null_model` tests the underlying strategies against a matched-exposure
random-timing null; read that before treating any CAGR gap here as an edge.

For TQQQ specifically the off-sleeve choice is genuinely unresolved by full-history
CAGR. Staying in Nasdaq 1x preserves more upside; T-bills materially improve
drawdowns and the long-horizon lower tail. That makes the safe-asset version worth
equal attention for TQQQ, despite the earlier S&P-family finding that remaining in
1x equity was generally preferable.

