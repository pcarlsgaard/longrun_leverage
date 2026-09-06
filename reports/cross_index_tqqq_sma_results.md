# TQQQ governed by the S&P 500 price SMA

This experiment tests whether a common S&P 500 price-index SMA can govern Nasdaq leverage. Investment returns remain total-return based. TQQQ rotates either to Nasdaq-100 1x or to 3-month T-bills when the S&P signal is unfavorable. The existing Nasdaq-100 price-SMA rules are retained as matched comparators. No parameters were optimized.

## Primary 200-day / 50-bp financing results

| Strategy | Lag | Cost | CAGR | Max DD | Sharpe | Leveraged days | Switches/yr |
|---|---|---:|---:|---:|---:|---:|---:|
| TQQQ always | LAG1 | 0 bp | 13.46% | -99.98% | 0.52 | 100.00% | 0.00 |
| Nasdaq SMA -> Nasdaq | LAG1 | 0 bp | 22.79% | -97.86% | 0.59 | 75.45% | 6.84 |
| Nasdaq SMA -> T-bill | LAG1 | 0 bp | 20.76% | -95.34% | 0.56 | 75.45% | 6.84 |
| **S&P SMA -> Nasdaq** | **LAG1** | **0 bp** | **24.26%** | **-97.75%** | **0.61** | **75.74%** | **6.54** |
| **S&P SMA -> T-bill** | **LAG1** | **0 bp** | **22.56%** | **-94.56%** | **0.59** | **75.74%** | **6.54** |
| Nasdaq SMA -> Nasdaq | LAG2 | 25 bp | 20.46% | -96.46% | 0.56 | 75.44% | 6.84 |
| Nasdaq SMA -> T-bill | LAG2 | 25 bp | 18.60% | -88.87% | 0.53 | 75.44% | 6.84 |
| **S&P SMA -> Nasdaq** | **LAG2** | **25 bp** | **24.16%** | **-96.43%** | **0.61** | **75.73%** | **6.54** |
| **S&P SMA -> T-bill** | **LAG2** | **25 bp** | **23.50%** | **-87.31%** | **0.61** | **75.73%** | **6.54** |

## Robustness across the prespecified grid

Matching signal source while holding the off-sleeve, SMA length, financing spread, execution lag, and switching cost fixed produces 144 pairwise comparisons. The S&P signal has higher CAGR than the Nasdaq signal in **138/144 (95.8%)** comparisons.

- 150-day SMA: S&P signal wins 87.5% of comparisons.
- 200-day SMA: S&P signal wins 100%.
- 250-day SMA: S&P signal wins 100%.

At 200 days the S&P and Nasdaq SMA states agree on about **89.75%** of sessions, so the result is driven by a relatively small set of disagreement dates rather than a radically different average leverage budget.

The 200-day rule is more execution-stable than the visually strongest 250-day full-history result. At 50-bp financing and 25-bp switching cost, S&P-SMA -> Nasdaq CAGR is 22.25%/24.16% under LAG1/LAG2, while the 250-day version is 24.61%/20.48%. This argues against selecting 250 days based on its LAG1 result.

## Historical subperiod caution

The S&P signal is not uniformly superior within every era. At 200 days it materially beats the Nasdaq signal in 1987-1999 and 2010-2019, is close in 2000-2009, but **loses substantially in 2020-latest**, when the Nasdaq's own trend contains useful asset-specific information. Thus the result supports a common market-risk regime signal but does not establish that the S&P signal dominates Nasdaq-specific information at all times.

## Long-horizon cohort distributions: LAG2 / 25 bp

### 20-year CAGR

| Strategy | P1 | P10 | P25 | P50 | P75 | P90 | P99 |
|---|---:|---:|---:|---:|---:|---:|---:|
| S&P SMA -> Nasdaq | 6.94% | 13.94% | 16.25% | 19.28% | 24.46% | 26.33% | 31.17% |
| **S&P SMA -> T-bill** | **10.42%** | **17.26%** | **19.19%** | **21.70%** | 23.23% | 26.08% | 28.77% |
| Nasdaq SMA -> Nasdaq | 3.87% | 10.09% | 12.57% | 15.32% | 20.61% | 25.02% | 30.83% |
| Nasdaq SMA -> T-bill | 5.77% | 11.50% | 14.70% | 17.02% | 19.13% | 22.41% | 27.23% |

### 30-year CAGR

| Strategy | P1 | P10 | P25 | P50 | P75 | P90 | P99 |
|---|---:|---:|---:|---:|---:|---:|---:|
| S&P SMA -> Nasdaq | 18.38% | 20.18% | 22.05% | 24.09% | 25.52% | 27.35% | 28.21% |
| **S&P SMA -> T-bill** | **19.37%** | **20.88%** | **22.72%** | **24.51%** | **26.18%** | **27.72%** | **28.52%** |
| Nasdaq SMA -> Nasdaq | 12.61% | 13.61% | 18.30% | 20.98% | 22.90% | 24.03% | 25.33% |
| Nasdaq SMA -> T-bill | 11.44% | 12.13% | 17.81% | 20.79% | 22.71% | 24.30% | 25.59% |

These cohorts overlap heavily and are descriptive historical outcomes, not independent probability draws. Early Nasdaq history also retains the existing proxy limitations.

## Interpretation

The experiment supports the idea that leverage intensity can reasonably be governed by a **common broad-equity risk regime** rather than requiring an asset-specific trend signal. The S&P 500 price SMA is particularly attractive because the same parsimonious signal can govern both S&P and Nasdaq leverage.

For TQQQ specifically, the off-sleeve choice is less clear from full-history CAGR alone. Staying in Nasdaq 1x preserves more upside, but T-bills materially improve drawdowns. Under the conservative LAG2/25-bp implementation, S&P-SMA -> T-bills has a much better 20-year lower tail and, unusually, slightly higher 30-year CAGR at every reported percentile than S&P-SMA -> Nasdaq. That makes the safe-asset version worthy of equal or greater attention for TQQQ, despite the earlier S&P-family finding that remaining in 1x equity was generally preferable.

The key falsification caveat is the 2020-latest reversal in favor of the Nasdaq-specific signal. A common S&P signal is therefore a strong parsimonious candidate, not a demonstrated universal optimum.
