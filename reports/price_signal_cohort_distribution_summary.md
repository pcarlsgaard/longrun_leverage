# Price-signal cohort outcome distributions

Canonical price-only signal convention; strategy wealth uses total returns. The
primary view below uses the 200-day price SMA, 50 bp financing spread, LAG1
execution and 0 bp switching cost. Cohorts enter at month-end closes and exit on
exact calendar anniversaries.

**These percentiles are not a probability distribution.** The windows overlap
heavily — a 30-year percentile over a 40-year history is built from windows that
share almost all of their data — so they describe what happened once, not what is
likely to happen again. Read them alongside `signal_null_model_results.md`, which
tests whether the timing is distinguishable from chance at all.

## 10-year CAGR distribution (360 cohorts)

| Strategy | P1 | P10 | P25 | P50 | P75 | P90 | P99 |
|---|---:|---:|---:|---:|---:|---:|---:|
| SP500 1x | -2.3% | 2.9% | 7.4% | 10.9% | 14.1% | 17.1% | 19.1% |
| Nasdaq-100 1x | -6.8% | 4.1% | 10.6% | 14.2% | 19.2% | 22.4% | 34.5% |
| SSO always | -13.2% | -2.5% | 7.3% | 13.0% | 22.5% | 27.0% | 30.2% |
| SSO SMA to SP500 | -5.6% | 2.8% | 9.8% | 15.3% | 19.5% | 23.6% | 27.7% |
| SSO SMA to T-bill | -1.2% | 4.1% | 8.4% | 12.4% | 15.7% | 19.6% | 25.2% |
| UPRO always | -26.0% | -10.9% | 3.8% | 12.1% | 27.9% | 36.0% | 41.4% |
| UPRO SMA to SP500 | -9.1% | 1.9% | 11.3% | 18.6% | 24.4% | 30.0% | 36.2% |
| UPRO SMA to T-bill | -4.9% | 3.2% | 9.8% | 15.8% | 20.2% | 25.4% | 33.6% |
| TQQQ always | -47.3% | -25.4% | -12.5% | 16.7% | 38.7% | 51.2% | 75.4% |
| TQQQ SMA to Nasdaq | -18.7% | -1.2% | 9.4% | 20.5% | 37.5% | 42.8% | 60.5% |
| TQQQ SMA to T-bill | -12.2% | 0.2% | 8.7% | 19.6% | 31.2% | 37.5% | 52.1% |

## 20-year CAGR distribution (240 cohorts)

| Strategy | P1 | P10 | P25 | P50 | P75 | P90 | P99 |
|---|---:|---:|---:|---:|---:|---:|---:|
| SP500 1x | 5.6% | 6.4% | 7.7% | 8.8% | 10.0% | 10.9% | 11.8% |
| Nasdaq-100 1x | 5.1% | 8.9% | 10.5% | 11.8% | 13.3% | 14.8% | 16.8% |
| SSO always | 4.1% | 5.5% | 7.2% | 9.0% | 12.6% | 14.0% | 15.7% |
| SSO SMA to SP500 | 6.5% | 8.2% | 9.9% | 11.7% | 13.4% | 14.2% | 16.1% |
| SSO SMA to T-bill | 5.4% | 7.1% | 9.2% | 10.6% | 11.7% | 12.7% | 13.5% |
| UPRO always | -0.8% | 1.4% | 3.6% | 6.1% | 12.0% | 14.4% | 16.6% |
| UPRO SMA to SP500 | 6.6% | 8.9% | 11.2% | 13.8% | 16.1% | 17.2% | 20.1% |
| UPRO SMA to T-bill | 5.5% | 8.0% | 10.8% | 12.9% | 14.1% | 15.5% | 17.2% |
| TQQQ always | -12.7% | -8.0% | -4.1% | -1.7% | 17.0% | 24.0% | 29.7% |
| TQQQ SMA to Nasdaq | 2.4% | 9.7% | 11.4% | 13.6% | 21.3% | 26.8% | 32.4% |
| TQQQ SMA to T-bill | 2.3% | 9.8% | 11.8% | 13.6% | 17.4% | 22.9% | 28.4% |

## 30-year CAGR distribution (120 cohorts)

| Strategy | P1 | P10 | P25 | P50 | P75 | P90 | P99 |
|---|---:|---:|---:|---:|---:|---:|---:|
| SP500 1x | 9.4% | 9.7% | 9.9% | 10.3% | 10.6% | 10.7% | 11.0% |
| Nasdaq-100 1x | 12.3% | 12.9% | 13.4% | 13.9% | 14.3% | 14.9% | 15.5% |
| SSO always | 10.4% | 11.0% | 12.1% | 12.8% | 13.4% | 13.8% | 14.4% |
| SSO SMA to SP500 | 12.3% | 12.6% | 13.1% | 13.8% | 14.2% | 14.6% | 15.2% |
| SSO SMA to T-bill | 10.7% | 11.2% | 11.5% | 11.9% | 12.3% | 12.7% | 13.4% |
| UPRO always | 8.1% | 9.4% | 11.3% | 12.4% | 13.3% | 14.0% | 14.7% |
| UPRO SMA to SP500 | 14.3% | 14.8% | 15.6% | 16.4% | 17.1% | 17.8% | 18.7% |
| UPRO SMA to T-bill | 12.8% | 13.5% | 13.9% | 14.6% | 15.2% | 15.7% | 16.7% |
| TQQQ always | 4.0% | 5.7% | 8.6% | 9.9% | 11.3% | 12.7% | 14.6% |
| TQQQ SMA to Nasdaq | 14.8% | 16.0% | 18.4% | 20.2% | 22.4% | 23.4% | 24.6% |
| TQQQ SMA to T-bill | 13.4% | 14.2% | 16.6% | 18.4% | 20.6% | 22.0% | 23.3% |

## Interpretation

The distribution view shows state-dependent leverage acting mainly on the left
tail: at every horizon the SMA variants lift the low percentiles far more than
they lift the median, which is what a rule that sits out drawdowns should do.
TQQQ is the extreme case and the least trustworthy — its always-on 20-year
distribution is dominated by whether a cohort spans 2000-2002, and its early
history is proxy data.

What this view cannot show is whether the improvement is timing or simply lower
average exposure; the matched-exposure controls and the null model address that,
and neither is settled by the percentiles here.

The generated CSV also contains LAG2 and 25 bp switching-cost versions for the same
10/20/30-year horizons.

