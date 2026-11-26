# Matched delta comparison — November 26, 2026

Indicative December 15, 2028 QQQ call quotes, not synchronized or executable. Reference QQQ close 633.93 from [Optionistics](https://www.optionistics.com/quotes/option-prices?optsym=QQQ+++281215C00710000). Bid/ask for 510 and 575 from indexed [WhaleQuant](https://whalequant.io/en/stocks/QQQ/options/2028-12-15) result; 540 from indexed [Finviz](https://finviz.com/stock?bd=bd-piechart&e=2028-12-15&ov=list_date&p=w&t=QQQ&ta=2&tt=tt-table&ty=oc). Source snapshots may differ and direct page views did not consistently expose the quoted rows. Refresh all inputs together from broker.

Assumptions: 751/365.25 years, 4.61% continuous option rate, 0.6% dividend yield, 4.34% annual bills, European approximation, 1% exit haircut, fractional contracts, initial delta 1.0, no interim rolls or rebalancing.

| Strike | Bid | Ask | Ask IV | Delta | Budget for 1x | Original budget | Original delta |
|---|---:|---:|---:|---:|---:|---:|---:|
| 510 | 198 | 201 | 32.45% | .800 | 39.64% | 25% | .631 |
| 540 | 176 | 180.5 | 31.28% | .769 | 37.05% | 30% | .810 |
| 575 | 155 | 158 | 30.10% | .728 | 34.26% | 40% | 1.168 |

One-year returns with bills reserve and matched 1x initial delta:

| QQQ move | Rate shock | IV shock | 510 | 540 | 575 |
|---|---:|---:|---:|---:|---:|
| -50% | +1pp | +5pp | -35.03% | -32.92% | -30.49% |
| -35% | +1pp | +5pp | -29.59% | -28.38% | -26.88% |
| -20% | 0 | 0 | -22.37% | -22.14% | -21.73% |
| 0 | 0 | 0 | -4.42% | -4.91% | -5.50% |
| +10% | 0 | 0 | 6.18% | 5.67% | 5.00% |
| +20% | 0 | 0 | 17.40% | 17.05% | 16.55% |
| +50% | 0 | 0 | 52.99% | 53.68% | 54.60% |

Higher strike uses less capital and has greater convexity, but more sensitivity to IV and higher modeled flat-market cost. One-year portfolio breakeven QQQ moves under unchanged rates and IV: 4.26%, 4.75%, 5.38%; moves to beat bills: 8.32%, 8.79%, 9.40%. These are deterministic marks, not expected returns or worst drawdowns.

The entry module now accepts --target-delta 1.0 to size at matched initial exposure and reports call delta, elasticity and required capital. The fixed-budget simulations are still distinct; the long-horizon matched-delta comparison is not yet implemented or run. Fractional positions may be infeasible in an actual account.

Candidate: compare a 90%-strike call with 30–35% premium and bill-heavy reserves against 85/30. This is not the previously simulated 90/40 policy, and it is not a validated optimal allocation. Match the intended initial exposure and use actual broker Greeks, executable bid/ask and account sizing before decision.
