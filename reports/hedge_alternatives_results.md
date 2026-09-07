# Buying crash protection while staying leveraged

`reports/signal_null_model_results.md` establishes that the trend rule's
advantage is not distinguishable from chance once the search is accounted for,
and that essentially all of it arrives in about twenty sessions. A structure
that loses to its benchmark on the other ~10,039 sessions and is repaid in
crashes is a synthetic put bought on instalments. This report asks the pricing
question that follows: is there a cheaper way to buy that convexity?

Window **1986-09-29** to **2026-09-02** (10,059 sessions), 50 bp financing
spread, 25 bp switching cost, 200-day price SMA at LAG2. Identical
assumptions across every structure, so the columns are comparable to each other
and to the null-model report.

## What is modelled rather than measured

Every number in this repository except the `LEAPS_` rows is arithmetic on
realized prices. The `LEAPS_` rows are not. This repository holds no option
prices and no implied-volatility history, and the environment that produced it
has no network access to obtain them, so premia are Black-Scholes values on an
**assumed** implied volatility: trailing exponentially-weighted realized
volatility plus a flat premium of 3 volatility points, averaging
**20.2%** over the window.

That assumption is doing real work, and two known biases in it both favour the
option structures. Trailing realized volatility rises only *after* a crash,
while implied volatility rises during one, so rolls executed near a market top
buy too cheaply here. And a single volatility ignores equity skew, so
in-the-money calls cost less here than they would have. Underpricing options
buys more contracts per dollar of budget, which inflates exposure and therefore
returns.

**Read the break-even table, not the option CAGRs.** It states how expensive
options would have had to be for each structure to lose, which is a claim a
reader can check against option prices they know.

## Every structure on one window

| series | cagr | max_drawdown | annualized_volatility | mean_delta_exposure | cohort_20y_min_cagr | cohort_30y_min_cagr |
|---|---:|---:|---:|---:|---:|---:|
| LEAPS_ATM_50_TREASURY | 20.07% | -73.78% | 40.70% | 2.68 | 7.36% | 15.76% |
| LEAPS_ATM_50_TBILL | 18.81% | -79.22% | 41.91% | 2.70 | 4.11% | 12.71% |
| LEAPS_ATM_40_TREASURY | 18.63% | -60.85% | 32.76% | 2.18 | 8.23% | 15.62% |
| UPRO_SMA_TO_SP500 | 17.15% | -75.41% | 37.70% | 2.51 | 6.81% | 13.10% |
| LEAPS_ATM_40_TBILL | 17.07% | -68.90% | 33.64% | 2.20 | 4.60% | 12.12% |
| UPRO60_TMF40 | 17.03% | -70.69% | 31.95% | 1.80 | 10.87% | 15.01% |
| UPRO55_TMF45 | 16.57% | -70.47% | 30.50% | 1.65 | 11.70% | 14.63% |
| UPRO_SMA_TO_TBILL | 15.73% | -64.15% | 34.93% | 2.27 | 6.80% | 12.56% |
| LEAPS_ITM_30_TREASURY | 15.23% | -50.79% | 21.25% | 1.36 | 9.47% | 13.15% |
| UPRO50_LT50 | 14.48% | -67.25% | 24.99% | 1.50 | 7.85% | 12.54% |
| UPRO60_TMF40_SMA | 14.41% | -61.06% | 27.48% | 1.36 | 7.59% | 12.60% |
| LEAPS_RESTRUCK_3X | 14.16% | -97.28% | 58.07% | 3.12 | -1.91% | 7.44% |
| SSO_ALWAYS_2X | 14.08% | -88.38% | 36.70% | 2.00 | 2.33% | 10.10% |
| UPRO_ALWAYS_3X | 13.71% | -98.20% | 55.05% | 3.00 | -3.19% | 7.75% |
| SP500_1X | 11.26% | -55.25% | 18.35% | 1.00 | 4.79% | 9.34% |

Exposure is delta-equivalent equity per dollar of portfolio, descriptive only:
equal exposure is not equal risk. Cohort columns are overlapping windows and
are historical outcomes, not independent draws.

## Concentration: is the advantage twenty days again?

| series | total_log_advantage | top1_day_share | top20_day_share | wealth_ratio_excluding_top_20_days | best_day |
|---|---:|---:|---:|---:|---|
| LEAPS_ATM_50_TREASURY | 2.174 | 30.1% | 184.8% | 0.16 | 1987-10-19 |
| LEAPS_ATM_50_TBILL | 1.753 | 37.8% | 209.2% | 0.15 | 1987-10-19 |
| LEAPS_ATM_40_TREASURY | 1.693 | 43.6% | 267.8% | 0.06 | 1987-10-19 |
| UPRO_SMA_TO_SP500 | 1.191 | 60.8% | 337.7% | 0.06 | 1987-10-19 |
| LEAPS_ATM_40_TBILL | 1.164 | 64.0% | 355.4% | 0.05 | 1987-10-19 |
| UPRO60_TMF40 | 1.149 | 49.4% | 401.3% | 0.03 | 1987-10-19 |
| UPRO55_TMF45 | 0.993 | 61.7% | 494.7% | 0.02 | 1987-10-19 |
| UPRO_SMA_TO_TBILL | 0.705 | 135.3% | 808.1% | 0.01 | 1987-10-19 |
| LEAPS_ITM_30_TREASURY | 0.530 | 151.4% | 946.2% | 0.01 | 1987-10-19 |
| UPRO50_LT50 | 0.272 | 245.5% | 1576.3% | 0.02 | 1987-10-19 |
| UPRO60_TMF40_SMA | 0.247 | 292.6% | 1625.9% | 0.02 | 1987-10-19 |
| LEAPS_RESTRUCK_3X | 0.159 | 261.4% | 1938.3% | 0.05 | 1987-10-19 |
| SSO_ALWAYS_2X | 0.131 | 327.2% | 1691.6% | 0.12 | 1987-10-19 |
| SP500_1X | -0.867 | 21.7% | 289.6% | 5.17 | 2008-10-13 |

Against always-on 3x leverage, **14 of 14**
structures still show a top-20 share above 100%. That is largely a property of
the benchmark rather than of the candidates: a strategy that falls
98% at its worst can only be beaten in the crash. The unleveraged
index is the control:

| series | total_log_advantage | top1_day_share | top20_day_share | wealth_ratio_excluding_top_20_days |
|---|---:|---:|---:|---:|
| LEAPS_ATM_50_TREASURY | 3.041 | 6.5% | 54.7% | 3.97 |
| LEAPS_ATM_50_TBILL | 2.620 | 7.3% | 67.5% | 2.34 |
| LEAPS_ATM_40_TREASURY | 2.560 | 5.0% | 50.0% | 3.59 |
| UPRO_SMA_TO_SP500 | 2.058 | 4.5% | 56.2% | 2.46 |
| LEAPS_ATM_40_TBILL | 2.031 | 6.0% | 62.1% | 2.16 |
| UPRO60_TMF40 | 2.016 | 7.9% | 81.5% | 1.45 |
| UPRO55_TMF45 | 1.860 | 9.2% | 97.0% | 1.06 |
| UPRO_SMA_TO_TBILL | 1.572 | 14.4% | 111.4% | 0.84 |
| LEAPS_ITM_30_TREASURY | 1.397 | 6.7% | 86.8% | 1.20 |
| UPRO50_LT50 | 1.140 | 6.2% | 68.2% | 1.44 |
| UPRO60_TMF40_SMA | 1.114 | 7.2% | 71.9% | 1.37 |
| LEAPS_RESTRUCK_3X | 1.027 | 32.6% | 331.3% | 0.09 |
| SSO_ALWAYS_2X | 0.998 | 9.9% | 129.9% | 0.74 |
| UPRO_ALWAYS_3X | 0.867 | 21.7% | 289.6% | 0.19 |

Against the index, **4 of 14**
exceed 100%. Concentration is therefore a statement about a pair, not about a
strategy, and the original finding should be read that way too: the trend rule's
edge over *always-on leverage* is twenty days; its edge over the *index* is not.

## Where each structure fails

Subperiod CAGR:

| series | 1987_1999 | 2000_2009 | 2010_2019 | 2020_2021 | 2022 | 2023_latest |
|---|---:|---:|---:|---:|---:|---:|
| SP500_1X | 17.4% | -0.9% | 13.6% | 23.5% | -18.3% | 22.5% |
| UPRO_ALWAYS_3X | 28.5% | -22.7% | 32.9% | 48.2% | -56.7% | 53.8% |
| SSO_ALWAYS_2X | 24.4% | -10.6% | 23.7% | 39.9% | -39.0% | 38.1% |
| UPRO_SMA_TO_SP500 | 27.2% | -4.6% | 23.4% | 64.7% | -46.6% | 36.0% |
| UPRO_SMA_TO_TBILL | 24.2% | 0.5% | 17.9% | 62.3% | -44.6% | 27.1% |
| UPRO60_TMF40 | 25.4% | -2.0% | 32.9% | 54.9% | -63.5% | 23.1% |
| UPRO55_TMF45 | 24.4% | -0.2% | 32.0% | 52.4% | -64.4% | 19.4% |
| UPRO50_LT50 | 22.3% | -3.0% | 23.3% | 40.5% | -43.8% | 25.6% |
| UPRO60_TMF40_SMA | 21.5% | -2.1% | 24.1% | 51.6% | -40.2% | 15.8% |
| LEAPS_ATM_50_TBILL | 36.3% | -8.2% | 23.5% | 46.7% | -42.4% | 42.7% |
| LEAPS_ATM_40_TBILL | 32.1% | -5.0% | 19.4% | 38.0% | -33.3% | 35.3% |
| LEAPS_ATM_50_TREASURY | 37.7% | -4.9% | 27.6% | 51.3% | -59.9% | 39.3% |
| LEAPS_ATM_40_TREASURY | 33.8% | -1.4% | 24.1% | 43.2% | -54.2% | 31.3% |
| LEAPS_ITM_30_TREASURY | 24.5% | 2.7% | 20.5% | 30.8% | -46.4% | 21.1% |
| LEAPS_RESTRUCK_3X | 31.1% | -19.7% | 28.4% | 67.9% | -73.3% | 58.1% |

Total return through each crash:

| series | 1987_crash | 2000_2002_bust | 2008_2009_gfc | 2020_covid | 2022_rates |
|---|---:|---:|---:|---:|---:|
| SP500_1X | -32.8% | -47.4% | -54.9% | -33.5% | -24.0% |
| UPRO_ALWAYS_3X | -80.5% | -92.5% | -95.5% | -76.3% | -63.0% |
| SSO_ALWAYS_2X | -60.6% | -78.9% | -84.3% | -58.8% | -45.9% |
| UPRO_SMA_TO_SP500 | -54.7% | -67.3% | -65.0% | -48.0% | -46.2% |
| UPRO_SMA_TO_TBILL | -42.6% | -42.5% | -29.0% | -29.8% | -38.3% |
| UPRO60_TMF40 | -48.6% | -61.5% | -70.3% | -29.2% | -66.2% |
| UPRO55_TMF45 | -44.6% | -54.6% | -64.8% | -23.5% | -66.6% |
| UPRO50_LT50 | -41.7% | -59.3% | -66.9% | -32.2% | -48.1% |
| UPRO60_TMF40_SMA | -48.1% | -58.4% | -56.8% | -27.7% | -42.6% |
| LEAPS_ATM_50_TBILL | -36.2% | -76.5% | -70.5% | -50.4% | -47.3% |
| LEAPS_ATM_40_TBILL | -29.8% | -66.0% | -60.1% | -41.9% | -37.8% |
| LEAPS_ATM_50_TREASURY | -34.6% | -70.9% | -65.2% | -43.8% | -62.7% |
| LEAPS_ATM_40_TREASURY | -28.1% | -57.5% | -52.5% | -33.9% | -56.5% |
| LEAPS_ITM_30_TREASURY | -20.6% | -35.7% | -34.9% | -21.4% | -48.6% |
| LEAPS_RESTRUCK_3X | -48.9% | -95.9% | -87.2% | -60.4% | -80.0% |

Best and worst leveraged structure in each crash, from the table above:

| crash | best | best_return | worst | worst_return |
|---|---|---:|---|---:|
| 1987_crash | LEAPS_ITM_30_TREASURY | -20.6% | UPRO_ALWAYS_3X | -80.5% |
| 2000_2002_bust | LEAPS_ITM_30_TREASURY | -35.7% | LEAPS_RESTRUCK_3X | -95.9% |
| 2008_2009_gfc | UPRO_SMA_TO_TBILL | -29.0% | UPRO_ALWAYS_3X | -95.5% |
| 2020_covid | LEAPS_ITM_30_TREASURY | -21.4% | UPRO_ALWAYS_3X | -76.3% |
| 2022_rates | LEAPS_ATM_40_TBILL | -37.8% | LEAPS_RESTRUCK_3X | -80.0% |

The hedges fail in different regimes, and that is the most useful thing here.
No structure is best in more than 3 of the 5 crashes, and the one
that cushions a liquidity crash is not the one that cushions a rates shock: in
2022 duration and equity fell together and the bond mixes did worse than holding
no hedge at all (-66.2% for `UPRO60_TMF40` against
-63.0% for always-on 3x). Neither family is a
general-purpose hedge, and the whole window is one secular declining-rate
regime, so the bond leg's contribution is itself a single-regime bet in exactly
the way October 1987 is for the trend rule.

## Options: the break-even volatility

| structure | cagr_at_base_premium | UPRO_SMA_TO_SP500_breakeven_mean_iv | UPRO60_TMF40_breakeven_mean_iv | UPRO_ALWAYS_3X_breakeven_mean_iv |
|---|---:|---:|---:|---:|
| LEAPS_ATM_50_TBILL | 18.81% | 21.2% | 21.3% | 23.6% |
| LEAPS_ATM_40_TBILL | 17.07% | 20.2% | 20.3% | 22.7% |
| LEAPS_ATM_50_TREASURY | 20.07% | 22.1% | 22.1% | 24.7% |
| LEAPS_ATM_40_TREASURY | 18.63% | 21.3% | 21.4% | 24.2% |
| LEAPS_ITM_30_TREASURY | 15.23% | 17.8% | 18.0% | 22.4% |

Each column is the average implied volatility at which that structure's return
falls to the named rival's. Blank means it never does across the bracket
searched.

At the assumed **20.2%**, 4 of the 5 option structures beat
`UPRO60_TMF40` and 5 beat always-on 3x. They stop beating the hedged
alternatives between **18.0%** and **22.1%** average implied volatility —
a margin over the assumption of only **-2.3** to
**+1.9** volatility points, which is smaller than the
uncertainty in the assumption itself. Long-dated index options plausibly traded
inside that band over this window, so **this report does not establish that
options beat the hedged alternatives.**

Against always-on 3x the margin is wider — break-even from **22.4%**
to **24.7%**, or **+2.2** to
**+4.5** points over the assumption. That
comparison is also the one resting on a mechanism rather than a coincidence: a
daily-reset fund pays variance drag continuously and an annually-rolled option
does not, which is arithmetic, not a historical accident. It is the strongest
claim the option family supports, and it is still a modelled one.

The option grid searched 192 rows before these were selected. That is a
smaller search than the one that produced the trend result, but it is not zero,
and no permutation null is available to correct it: a roll schedule has no
timing to randomize. Treat the option rows as the weakest evidence in this
repository, not the strongest.

## Re-levering defeats the bounded loss

`LEAPS_RESTRUCK_3X` re-strikes to 3x of *current* wealth at each roll
instead of spending a fixed budget. It reaches 14.16% with a
-97.3% drawdown and a worst 20-year cohort of
-1.91%.

An individual call cannot lose more than its premium. That does not bound the
portfolio, because re-striking to a constant multiple of reduced wealth
compounds the losses across rolls: three bad years in succession each take most
of a fresh premium. The bounded-loss property people expect from options is a
property of a **fixed budget**, not of options. `LEAPS_ATM_40_TREASURY` and its
siblings keep a constant safe weight and get the floor; this row does not.

## What this supports

1. **Less leverage is the hedge that cannot fail.** `SSO_ALWAYS_2X` returns
   14.08% against 13.71% for 3x, with a -88.4%
   drawdown against -98.2% and a worst 20-year cohort of
   2.33% against -3.19%. No signal, no
   counterparty, no regime assumption.
2. **The static mix matches the trend rule without a signal.** 17.03% against
   17.15%, with a better drawdown (-70.7% against
   -75.4%) and a better worst 20-year cohort
   (10.87% against 6.81%) — and zero
   switches. Its exposure to a 2022-style rates shock is the price.
3. **Options plausibly dominate daily-reset funds, on a mechanism.** The
   break-even against always-on 3x is the widest margin in the table
   (+4.5 volatility points at most), and variance
   drag is arithmetic rather than a historical accident.
4. **Options do not clearly dominate the hedged alternatives.** Break-even sits
   -2.3 to +1.9 points from the
   assumption — inside its own error bar. This report cannot settle that and
   should not be read as settling it.
5. **A bounded loss per contract is not a bounded loss per portfolio.** The
   fixed-budget structures keep a floor; `LEAPS_RESTRUCK_3X` does not, and lands
   at a -97.3% drawdown and a -1.91%
   worst 20-year cohort — worse on both than the daily-reset fund it was meant
   to improve on.

## What would change any of this

Option price history, which would replace the modelled premia with measured
ones and make the break-even table unnecessary. Failing that, out-of-sample
data, or a rates regime that is not the forty-year bond bull market this window
consists of. Nothing here is established; these are candidates ranked by how
much has to be assumed to believe them.
