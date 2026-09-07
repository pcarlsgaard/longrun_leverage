# Regime signals for leverage management

The **absolute 20-day volatility rule and the existing 200-day SMA remain the only
signals with positive full-window timing value under all four lag/cost combinations**,
including the stricter control with equal total fee spending. No new indicator clearly
outperforms SMA across execution assumptions and historical subperiods. This supports a
broader leverage-regime mechanism, but not the proposition that any sensible trend or
path-quality indicator will capture it.

## Design and scope

- Primary and only invested family: archived UPRO BASE / S&P 500 1×; favorable = 3×,
  unfavorable = 1×. No cash variants, fitted thresholds, parameter search or new histories.
- Full **common comparison window**: entry close 1986-09-26; returns 1986-09-29 through
  2026-09-02 (10,059 sessions). This preserves the earlier falsification comparison window,
  whose shared warm-up was constrained by other families; it is not all available S&P
  history back to 1980. Earlier archived S&P observations warm up signals only.
- SMA retains the existing total-return-level convention. Efficiency, regression and
  sign flips use frozen S&P price closes. Absolute/relative volatility use S&P total
  returns, sample standard deviation and 252-session annualization. This intentional
  preservation of the SMA baseline means indicator comparisons are not a pure
  price-versus-total-return-controlled experiment.
- VOL_BINARY reuses the existing 20-return rule: volatility <20% = favorable, otherwise
  unfavorable. Its historical performance is reproduced, not extended into a new grid.
- Relative volatility: SD20/SD120 <=1. Efficiency: 60 changes, SER>0 and ER>=0.25;
  flat paths have ER=SER=0. Regression: 120 log-price observations, positive slope and
  OLS slope t>=2 (118 residual degrees of freedom). The t-score is descriptive: serial
  dependence in log-price levels makes an IID significance interpretation inappropriate.
- Churn: 59 adjacent pairs within 60 daily price returns; zero returns are not opposite
  signs. The expanding median begins with the first valid flip statistic and excludes
  the current close's statistic. Equality is unfavorable. It is a sign-alternation proxy,
  not a magnitude-aware measure of volatility or total path length.
- AO included: complete positive high/low observations in verified frozen S&P archive; no downloads.
- Close-t features first affect the return ending t+1 (LAG1) or t+2 (LAG2). Features are
  lagged exactly once; warm-up uses no future values. LAG1 retains the previous idealized
  signal-close execution assumption, not an observed executable intraday fill.
- Costs of 0 or 25 bp apply once per sleeve transition, for selling and buying combined;
  establishment is excluded. Subperiods retain live positions and boundary transition
  costs. All strategies share dates. State statistics describe the returns following
  the lagged state, before switching costs.

## Full-window results

25 bp per switch. Last two columns are strategy minus matched-control CAGR in percentage
points; other risk metrics refer to LAG2. Terminal wealth and all four scenarios are in
`regime_signal_metrics.csv` (wealth is both a multiple and dollars per $10,000).

| Signal | CAGR LAG1 | CAGR LAG2 | Vol LAG2 | Max DD LAG2 | Sharpe | Mean exposure | Switches | Timing Δ pp | Equal-fee Δ pp |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| SMA 200 | 16.29% | 17.14% | 37.70% | -75.41% | 0.53 | 2.51× | 261 | +2.72 | +2.47 |
| Absolute vol 20 | 15.34% | 15.01% | 38.90% | -88.95% | 0.48 | 2.61× | 211 | +0.64 | +0.44 |
| Relative vol | 9.63% | 11.96% | 37.66% | -96.81% | 0.41 | 2.22× | 495 | -2.38 | -2.78 |
| Efficiency 60 | 6.92% | 8.69% | 22.35% | -55.73% | 0.35 | 1.28× | 370 | -3.03 | -3.88 |
| Trend quality 120 | 12.99% | 13.49% | 40.00% | -78.74% | 0.45 | 2.37× | 73 | -0.94 | -1.26 |
| Low churn 60 | 7.82% | 8.14% | 35.51% | -86.91% | 0.32 | 1.74× | 542 | -5.31 | -5.95 |
| AO 5/34 | 9.15% | 11.80% | 36.74% | -80.38% | 0.40 | 2.30× | 423 | -2.61 | -2.96 |
| UPRO always | 13.70% | 13.70% | 55.05% | -98.20% | 0.46 | 3.00× | 0 | +0.00 | +0.00 |
| S&P 1× | 11.26% | 11.26% | 18.35% | -55.25% | 0.50 | 1.00× | 0 | +0.99 | +0.00 |

The ranking by evidence is **SMA and absolute volatility as the leading pair**, then
trend quality as a weaker partial regime classifier; relative volatility and AO lack
cost/lag robustness, while efficiency and sign churn provide little support here.
SMA offers stronger drawdown protection and better worst long cohorts than absolute
volatility. Absolute volatility has much less sensitivity to an extra execution day:
at 25 bp, its CAGR is about 15.8% under both lags, while SMA falls from 17.9% to 15.1%.
These are different strengths, not a clean dominance result. Even these rules retain
severe drawdowns (approximately 78% and 88% under LAG2/25 bp).

## Matched leverage: is the allocation timing productive?

For each signal and lag, the primary control holds constant effective leverage
`L = mean(1 + 2 × favorable)` with daily resets. As in the capital-reserve experiment,
funding plus spread is inferred from the archived UPRO return identity:
`funding = (3*r_sp500 - r_upro - fee)/2`, then
`r_control = L*r_sp500 - (L-1)*funding - fee`.
The existing UPRO expense rate is charged in full. No underlying or LETF series is rebuilt.
Controls have zero regime switches and no switching cost; this is an idealized constant
leverage financing control, not a claimed available ETF.

Because the rotating strategy pays fund expenses only on UPRO days, the CSV also includes
an **equal-fee control**: its constant expense rate is reduced so total simple fee spending
equals the rotating strategy's. This prevents avoided fund fees from masquerading as timing.
For the 1× benchmark, the primary full-fee control mechanically loses an ETF fee; its
equal-fee timing difference is zero. Do not interpret that primary difference as skill.

Average exposure is calculated separately within every reported subperiod; full-window
controls are fitted only to the full-window mean. These are ex-post diagnostics, not
forecastable allocation rules. Equal average leverage does not equalize volatility,
and neither control proves causality or future out-of-sample profitability.

| Signal | Equal-fee full-window Δ range, pp | Positive full scenarios | Positive subperiod scenarios |
| --- | --- | --- | --- |
| SMA 200 | +1.62 to +4.41 | 4/4 | 11/16 |
| Absolute vol 20 | +0.44 to +2.30 | 4/4 | 7/16 |
| Relative vol | -5.12 to +0.75 | 1/4 | 6/16 |
| Efficiency 60 | -5.66 to -1.33 | 0/4 | 2/16 |
| Trend quality 120 | -1.77 to -0.74 | 0/4 | 8/16 |
| Low churn 60 | -6.27 to -2.21 | 0/4 | 1/16 |
| AO 5/34 | -5.61 to +0.04 | 1/4 | 6/16 |

The full-window SMA and absolute-volatility advantages survive equalizing fees and
25 bp costs. At LAG2/25 bp they are about **+0.52 and +1.19 CAGR points**, respectively.
However, neither wins in every era. Relative volatility has a positive full-window
result only at LAG2/zero cost; AO's corresponding advantage is negligible. More frequent
switching further weakens both. Trend quality has just 73 switches but still fails the
full-window matched-leverage test, so its shortfall cannot be blamed primarily on costs.

## Historical subperiods

LAG2, 25 bp; CAGR. The 1987 start omits the partial 1986 tail included in the full window.

| Signal | 1987_1999 | 2000_2009 | 2010_2019 | 2020_latest |
| --- | --- | --- | --- | --- |
| SMA 200 | 30.17% | -4.61% | 23.34% | 25.27% |
| Absolute vol 20 | 29.97% | -12.69% | 26.26% | 22.96% |
| Relative vol | 28.80% | -18.70% | 19.10% | 25.55% |
| Efficiency 60 | 17.49% | -1.35% | 7.57% | 10.40% |
| Trend quality 120 | 17.28% | -2.48% | 29.71% | 11.70% |
| Low churn 60 | 19.05% | -12.70% | 13.68% | 15.02% |
| AO 5/34 | 20.84% | -7.37% | 15.83% | 24.37% |
| UPRO always | 30.66% | -22.66% | 32.82% | 25.87% |
| S&P 1× | 17.97% | -0.95% | 13.56% | 15.54% |

Same scenarios, **CAGR advantage over the period-specific equal-fee, matched-exposure
control**, percentage points:

| Signal | 1987_1999 | 2000_2009 | 2010_2019 | 2020_latest |
| --- | --- | --- | --- | --- |
| SMA 200 | +0.45 | +6.68 | -7.15 | -0.15 |
| Absolute vol 20 | -0.15 | +1.09 | -4.40 | -2.40 |
| Relative vol | +1.11 | -6.22 | -7.29 | +1.38 |
| Efficiency 60 | -3.73 | +0.69 | -9.37 | -8.23 |
| Trend quality 120 | -12.14 | +7.11 | +0.89 | -13.33 |
| Low churn 60 | -6.37 | -9.32 | -9.58 | -7.83 |
| AO 5/34 | -7.46 | +4.38 | -11.74 | -0.58 |

SMA's benefit is concentrated in adverse compounding eras, especially 2000–2009; it
sacrifices considerable wealth in some strong-market periods. Absolute volatility is
more execution-stable but has negative within-period timing differences in three of
these four LAG2/25 bp eras. Its positive full-window difference can coexist with this:
full-window matching also captures how much leverage is allocated *between* eras, while
period-specific matching removes that channel. Counts across scenarios are correlated
observations, not independent statistical replications.

## Conditional states and economic mechanism

LAG2 states. Log-return columns are **252 × mean daily log return**, not calendar CAGR
of a continuous investment in disconnected state dates. Volatility is annualized standard
deviation of the S&P daily returns assigned to that state. Drag is exact
`3*log(1+r_sp500) - log(1+3*r_sp500)`, excluding financing and expenses; daily arithmetic
and log means, approximate drag and financing drag are also in the state CSV.

| Signal | State | Day share | S&P log | UPRO log | S&P vol | Path drag |
| --- | --- | --- | --- | --- | --- | --- |
| SMA 200 | favorable | 75.73% | 11.56% | 20.53% | 13.38% | 5.46% |
| SMA 200 | unfavorable | 24.27% | 7.92% | -11.12% | 28.79% | 26.64% |
| Absolute vol 20 | favorable | 80.40% | 10.07% | 15.84% | 13.53% | 5.57% |
| Absolute vol 20 | unfavorable | 19.60% | 13.17% | 0.59% | 31.10% | 31.23% |
| Relative vol | favorable | 60.79% | 10.68% | 16.81% | 14.91% | 6.75% |
| Relative vol | unfavorable | 39.21% | 10.68% | 6.70% | 22.67% | 16.57% |
| Efficiency 60 | favorable | 14.07% | 6.95% | 6.84% | 12.01% | 4.36% |
| Efficiency 60 | unfavorable | 85.93% | 11.29% | 13.83% | 19.19% | 11.62% |
| Trend quality 120 | favorable | 68.36% | 10.00% | 13.57% | 15.20% | 7.62% |
| Trend quality 120 | unfavorable | 31.64% | 12.14% | 11.28% | 23.77% | 17.05% |
| Low churn 60 | favorable | 37.21% | 10.30% | 11.79% | 17.62% | 10.34% |
| Low churn 60 | unfavorable | 62.79% | 10.90% | 13.47% | 18.77% | 10.76% |
| AO 5/34 | favorable | 65.25% | 9.64% | 14.45% | 13.92% | 5.91% |
| AO 5/34 | unfavorable | 34.75% | 12.62% | 9.84% | 24.60% | 19.41% |

SMA and absolute volatility most clearly identify the requested mechanism: in their
unfavorable states, S&P log growth remains roughly 10.8–11.4% annualized while UPRO log
growth falls below zero. Unfavorable-state S&P volatility is roughly 29–31%, versus
13–14% when favorable, and exact path drag rises sharply. Financing further reduces
leveraged growth. Thus it is not necessary for equities themselves to have negative
expected log growth for high leverage to be poorly compensated.

Relative volatility separates risk somewhat, but its unfavorable-state UPRO log growth
remains positive. A relative ratio can label persistently high volatility as favorable
once the long-window denominator catches up; absolute volatility directly addresses the
level of the compounding penalty. The ratio does not explain more in this experiment.

Efficiency selects only about 14% of days for leverage, and its supposedly favorable
state has *lower* subsequent UPRO log growth. Regression quality identifies lower
volatility but insufficient improvement in subsequent leveraged growth. Low churn barely
separates realized volatility or path drag; alternating signs without return magnitudes
are a poor proxy for the quadratic compounding penalty. AO separates volatility but
fails to deliver robust net matched-leverage gains. The evidence supports positive equity
growth plus controlled volatility as the economic foundation; it does **not** establish
high observed efficiency, low sign churn, or faster momentum as additional useful triggers.

**Before quoting any advantage below:** `reports/signal_null_model_results.md`
tests these strategies against a null with the same switch count, episode
lengths and leveraged-day fraction but randomized timing. None is significant
at 5% after correcting for the 144 nuisance-parameter cells searched here, and
every one has a top-20-session share above 100% of its total advantage — the
remaining ~10,000 sessions are net negative.

## Signal overlap and long-horizon check

LAG1 agreement with the existing SMA; the compact CSV contains the complete pairwise
matrix in long form for both lags. Raw agreement depends on how often each rule is
favorable and does not establish incremental predictive information.

| Signal | Agreement with SMA |
| --- | --- |
| SMA 200 | 100.00% |
| Absolute vol 20 | 84.00% |
| Relative vol | 63.07% |
| Efficiency 60 | 38.30% |
| Trend quality 120 | 85.80% |
| Low churn 60 | 47.91% |
| AO 5/34 | 73.19% |

Absolute volatility agrees with SMA about 86% of the time, supporting substantial
shared regime information. Regression quality agrees about 85% yet has inferior
matched-leverage performance: broad classification similarity is not sufficient, and
the dates of disagreements matter. No optimized combined rule is warranted by this test.

Worst exact-anniversary monthly-entry cohort CAGR, LAG2/25 bp. Controls here keep the
full-window matched leverage fixed (not rematched within every cohort); the existing
cohort machinery is reused. There are 240 20-year and 120 30-year cohorts, with substantial
overlap, so these are descriptive historical checks rather than independent trials.

| Signal | 20y min | 30y min | Control 20y min | Control 30y min |
| --- | --- | --- | --- | --- |
| SMA 200 | 6.81% | 13.10% | -0.05% | 9.40% |
| Absolute vol 20 | 1.83% | 10.39% | -0.59% | 9.16% |
| Relative vol | -3.48% | 5.23% | 1.44% | 9.93% |
| Efficiency 60 | 1.28% | 6.30% | 3.83% | 9.25% |
| Trend quality 120 | 4.85% | 10.88% | 0.73% | 9.71% |
| Low churn 60 | -2.16% | 3.59% | 3.09% | 10.02% |
| AO 5/34 | 1.54% | 7.48% | 1.03% | 9.81% |
| UPRO always | -3.19% | 7.75% | -3.19% | 7.75% |
| S&P 1× | 4.79% | 9.34% | 3.86% | 8.37% |

## Reproduction and limitations

Run `PYTHONPATH=src python -m letf.regime_signals` from the repository root. It uses only
verified frozen bundles, leaves their bytes unchanged, and writes the five requested
compact reports. No daily-output files or summary figure are needed. The generator
checks 8 reused baseline rows against the prior committed results. Tests:
`PYTHONPATH=src python -m unittest discover -s tests -p 'test_regime_signals.py' -v`.

This is a prespecified historical falsification exercise on one equity family, not new
held-out validation. Earlier source-proxy, financing, gross-1×, synthetic-fund and
execution assumptions remain in force; taxes and actual market impact are not modeled.
There is no Nasdaq replication or combined-signal fit in this focused experiment.

Runtime: Python 3.11.15, NumPy 2.4.6, pandas 3.0.5.

Verified input SHA-256:

- `config.json`: `81650c61c509f432be9f9827d7f53109bbd3e8a016cf248edc0323cd3e85a715`
- `data/snapshots/portfolio_sma_inputs.zip`: `dbcc1d90710867480eb51d2133d4eb02fd312208c9a79ad6bdda234d8cffaea5`
- `data/snapshots/sma_price_inputs.zip`: `3cba13c65a48dc85160a2721b955485d2a1aa01b508d146325a90137eefa2e4c`
