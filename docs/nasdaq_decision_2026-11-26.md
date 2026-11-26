# Nasdaq allocation decision research — 2026-11-26

The 2,048-path-per-regime, 20-year run completed successfully: [workflow and raw artifacts](https://github.com/pcarlsgaard/longrun_leverage/actions/runs/45884506981). The aggregate job is 140136621389. This is a dated interpretation, not a generated report or recommendation to trade.

| Scenario | NDX 1x median real CAGR | 80/25 mixed | 85/30 mixed | 90/40 mixed | 90/40 long |
|---|---:|---:|---:|---:|---:|
| historical bootstrap | 12.42% | 10.05% | 11.66% | 14.14% | 14.97% |
| favorable | 10.98% | 9.38% | 10.85% | 13.10% | 14.08% |
| AI boom | 12.04% | 9.20% | 10.45% | 12.42% | 12.66% |
| AI expensive capital | 9.34% | 6.61% | 6.89% | 6.95% | 6.08% |
| normalization | 5.75% | 4.49% | 4.59% | 4.31% | 4.32% |
| slowdown | 3.76% | 2.78% | 2.46% | 1.37% | 1.12% |
| fiscal stress | 2.29% | 0.93% | 0.27% | -1.41% | -3.09% |

No regime probabilities are assigned. The simple adaptive policy does not consistently beat NDX 1x. 80/25 and 85/30 buy downside protection in many regimes but sacrifice median growth. 90/40 requires strong favorable assumptions; a mixed or bills reserve reduces exposure to duration shocks. These rankings are conditional on the reduced-form model, historical residuals and assumed IV. Review paired_decisions.csv and constrained_decisions.csv in the workflow artifact before choosing a risk budget.

## Dated external inputs

- [Treasury curve, November 25](https://home.treasury.gov/resource-center/data-chart-center/interest-rates/TextView?field_tdr_date_value_month=202611&type=daily_treasury_yield_curve): 3-month 4.34%, 2-year 4.61%, 10-year 4.85%, 30-year 5.24%.
- [Citadel Securities, November 3 observations](https://www.citadelsecurities.com/news-and-insights/global-market-intelligence/november-the-setup-improves/): NDX 19.1x forward earnings, forward earnings estimates up 14% since June. Not a current exact multiple or a ten-year growth forecast.
- [Public delayed QQQ option chain](https://public.com/stocks/qqq/options-chain?expiration=2028-12-15): QQQ 633.93, December 15 2028 575 call displayed ask 158.00 in indexed result; source says delayed and prior activity may be reflected. Refresh at broker before use. Not an executable quote.

For illustration, with assumed dividend yield 0.6%, European pricing, 2.053 years and a 4.61% rate, this near-90%-strike call has inferred IV about 30.1%, delta 0.727, and 1.17x initial portfolio delta at a 40% premium budget. Expiry break-even for call alone is 733, 15.6% above spot. Contract cost is $15,800; whole-contract sizing matters. Model ignores American exercise, taxes and changes in expected dividends.

At a 19.1x starting and terminal P/E, 4.34% cash carry, assumed 0.6% dividend yield, 23% realized volatility and 50 bp incremental financing spread, nominal EPS growth thresholds for 2x and 3x constant exposure versus 1x are 9.9% and 12.9%. At 28% volatility they are 12.7% and 17.3%. These are not LEAPS thresholds; they screen for general leverage economics. The true long-run earnings-growth rate remains unknown.

## Decision gate

Prioritize 80/25 and 85/30 with a bill-heavy reserve as research candidates, compare with unlevered Nasdaq, and require a separate favorable case before upgrading to 90/40 or adding long duration. No fixed allocation is endorsed from this study alone. Obtain contemporaneous broker bid/ask, option IV/delta, available expiries, user drawdown tolerance, and vintage-aware earnings forecasts. Test matched delta, option IV and duration sensitivity, and include dip-buying variants before promoting an adaptive policy. The current draft PR is not merged.
