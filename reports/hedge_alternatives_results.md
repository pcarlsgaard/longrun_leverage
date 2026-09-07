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
| LEAPS_ATM_50_TREASURY | 19.27% | -75.47% | 41.52% | 2.68 | 6.91% | 14.77% |
| LEAPS_ATM_40_TREASURY | 17.75% | -63.71% | 33.66% | 2.18 | 7.90% | 14.45% |
| LEAPS_ATM_50_TBILL | 17.61% | -82.73% | 42.10% | 2.69 | 3.15% | 11.34% |
| UPRO_SMA_TO_SP500 | 17.15% | -75.41% | 37.70% | 2.51 | 6.81% | 13.10% |
| UPRO60_TMF40 | 17.03% | -70.69% | 31.95% | 1.80 | 10.87% | 15.01% |
| LEAPS_RESTRUCK_3X | 16.69% | -97.17% | 55.94% | 3.12 | 0.19% | 10.35% |
| UPRO55_TMF45 | 16.57% | -70.47% | 30.50% | 1.65 | 11.70% | 14.63% |
| LEAPS_ATM_40_TBILL | 15.91% | -70.97% | 33.89% | 2.19 | 3.86% | 10.65% |
| UPRO_SMA_TO_TBILL | 15.73% | -64.15% | 34.93% | 2.27 | 6.80% | 12.56% |
| LEAPS_ITM_30_TREASURY | 14.85% | -50.74% | 21.95% | 1.36 | 9.56% | 13.50% |
| UPRO50_LT50 | 14.48% | -67.25% | 24.99% | 1.50 | 7.85% | 12.54% |
| UPRO60_TMF40_SMA | 14.41% | -61.06% | 27.48% | 1.36 | 7.59% | 12.60% |
| SSO_ALWAYS_2X | 14.08% | -88.38% | 36.70% | 2.00 | 2.33% | 10.10% |
| UPRO_ALWAYS_3X | 13.71% | -98.20% | 55.05% | 3.00 | -3.19% | 7.75% |
| SP500_1X | 11.26% | -55.25% | 18.35% | 1.00 | 4.79% | 9.34% |

Exposure is delta-equivalent equity per dollar of portfolio, descriptive only:
equal exposure is not equal risk. Cohort columns are overlapping windows and
are historical outcomes, not independent draws.

## Concentration: is the advantage twenty days again?

| series | total_log_advantage | top1_day_share | top20_day_share | wealth_ratio_excluding_top_20_days | best_day |
|---|---:|---:|---:|---:|---|
| LEAPS_ATM_50_TREASURY | 1.908 | 18.0% | 214.5% | 0.11 | 2020-03-16 |
| LEAPS_ATM_40_TREASURY | 1.394 | 28.0% | 319.2% | 0.05 | 2020-03-16 |
| LEAPS_ATM_50_TBILL | 1.349 | 23.2% | 278.7% | 0.09 | 1987-10-19 |
| UPRO_SMA_TO_SP500 | 1.191 | 60.8% | 337.7% | 0.06 | 1987-10-19 |
| UPRO60_TMF40 | 1.149 | 49.4% | 401.3% | 0.03 | 1987-10-19 |
| LEAPS_RESTRUCK_3X | 1.034 | 25.0% | 300.1% | 0.13 | 1987-10-19 |
| UPRO55_TMF45 | 0.993 | 61.7% | 494.7% | 0.02 | 1987-10-19 |
| LEAPS_ATM_40_TBILL | 0.766 | 56.3% | 535.5% | 0.04 | 1987-10-19 |
| UPRO_SMA_TO_TBILL | 0.705 | 135.3% | 808.1% | 0.01 | 1987-10-19 |
| LEAPS_ITM_30_TREASURY | 0.400 | 149.1% | 1235.8% | 0.01 | 1987-10-19 |
| UPRO50_LT50 | 0.272 | 245.5% | 1576.3% | 0.02 | 1987-10-19 |
| UPRO60_TMF40_SMA | 0.247 | 292.6% | 1625.9% | 0.02 | 1987-10-19 |
| SSO_ALWAYS_2X | 0.131 | 327.2% | 1691.6% | 0.12 | 1987-10-19 |
| SP500_1X | -0.867 | 21.7% | 289.6% | 5.17 | 2008-10-13 |

Against always-on 3x leverage, **14 of 14**
structures still show a top-20 share above 100%. That is largely a property of
the benchmark rather than of the candidates: a strategy that falls
98% at its worst can only be beaten in the crash. The unleveraged
index is the control:

| series | total_log_advantage | top1_day_share | top20_day_share | wealth_ratio_excluding_top_20_days |
|---|---:|---:|---:|---:|
| LEAPS_ATM_50_TREASURY | 2.775 | 9.7% | 69.0% | 2.36 |
| LEAPS_ATM_40_TREASURY | 2.261 | 9.1% | 70.4% | 1.95 |
| LEAPS_ATM_50_TBILL | 2.216 | 11.0% | 84.2% | 1.42 |
| UPRO_SMA_TO_SP500 | 2.058 | 4.5% | 56.2% | 2.46 |
| UPRO60_TMF40 | 2.016 | 7.9% | 81.5% | 1.45 |
| LEAPS_RESTRUCK_3X | 1.901 | 12.4% | 153.6% | 0.36 |
| UPRO55_TMF45 | 1.860 | 9.2% | 97.0% | 1.06 |
| LEAPS_ATM_40_TBILL | 1.633 | 11.2% | 86.5% | 1.25 |
| UPRO_SMA_TO_TBILL | 1.572 | 14.4% | 111.4% | 0.84 |
| LEAPS_ITM_30_TREASURY | 1.267 | 9.2% | 107.5% | 0.91 |
| UPRO50_LT50 | 1.140 | 6.2% | 68.2% | 1.44 |
| UPRO60_TMF40_SMA | 1.114 | 7.2% | 71.9% | 1.37 |
| SSO_ALWAYS_2X | 0.998 | 9.9% | 129.9% | 0.74 |
| UPRO_ALWAYS_3X | 0.867 | 21.7% | 289.6% | 0.19 |

Against the index, **5 of 14**
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
| LEAPS_ATM_50_TBILL | 33.3% | -9.5% | 23.7% | 50.8% | -45.1% | 43.5% |
| LEAPS_ATM_40_TBILL | 28.7% | -6.2% | 20.1% | 40.6% | -36.1% | 35.6% |
| LEAPS_ATM_50_TREASURY | 34.2% | -5.1% | 27.6% | 53.9% | -59.7% | 41.1% |
| LEAPS_ATM_40_TREASURY | 30.0% | -1.5% | 24.6% | 44.3% | -54.2% | 32.6% |
| LEAPS_ITM_30_TREASURY | 22.4% | 3.3% | 20.6% | 30.4% | -45.9% | 22.0% |
| LEAPS_RESTRUCK_3X | 36.7% | -18.3% | 28.8% | 69.0% | -71.6% | 59.4% |

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
| LEAPS_ATM_50_TBILL | -58.2% | -77.1% | -70.9% | -45.9% | -47.0% |
| LEAPS_ATM_40_TBILL | -52.6% | -66.6% | -60.8% | -37.4% | -37.6% |
| LEAPS_ATM_50_TREASURY | -59.0% | -71.4% | -64.3% | -37.9% | -62.7% |
| LEAPS_ATM_40_TREASURY | -53.6% | -58.0% | -52.1% | -28.0% | -56.6% |
| LEAPS_ITM_30_TREASURY | -40.2% | -35.8% | -33.8% | -16.9% | -48.6% |
| LEAPS_RESTRUCK_3X | -60.8% | -95.4% | -87.7% | -57.3% | -79.4% |

Best and worst leveraged structure in each crash, from the table above:

| crash | best | best_return | worst | worst_return |
|---|---|---:|---|---:|
| 1987_crash | LEAPS_ITM_30_TREASURY | -40.2% | UPRO_ALWAYS_3X | -80.5% |
| 2000_2002_bust | LEAPS_ITM_30_TREASURY | -35.8% | LEAPS_RESTRUCK_3X | -95.4% |
| 2008_2009_gfc | UPRO_SMA_TO_TBILL | -29.0% | UPRO_ALWAYS_3X | -95.5% |
| 2020_covid | LEAPS_ITM_30_TREASURY | -16.9% | UPRO_ALWAYS_3X | -76.3% |
| 2022_rates | LEAPS_ATM_40_TBILL | -37.6% | LEAPS_RESTRUCK_3X | -79.4% |

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
| LEAPS_ATM_50_TBILL | 17.61% | 20.5% | 20.6% | 22.9% |
| LEAPS_ATM_40_TBILL | 15.91% | 19.4% | 19.5% | 21.9% |
| LEAPS_ATM_50_TREASURY | 19.27% | 21.6% | 21.7% | 24.3% |
| LEAPS_ATM_40_TREASURY | 17.75% | 20.7% | 20.8% | 23.6% |
| LEAPS_ITM_30_TREASURY | 14.85% | 17.3% | 17.4% | 21.9% |

Each column is the average implied volatility at which that structure's return
falls to the named rival's. Blank means it never does across the bracket
searched.

At the assumed **20.2%**, 3 of the 5 option structures beat
`UPRO60_TMF40` and 5 beat always-on 3x. They stop beating the hedged
alternatives between **17.4%** and **21.7%** average implied volatility —
a margin over the assumption of only **-2.8** to
**+1.4** volatility points, which is smaller than the
uncertainty in the assumption itself. Long-dated index options plausibly traded
inside that band over this window, so **this report does not establish that
options beat the hedged alternatives.**

Against always-on 3x the margin is wider — break-even from **21.9%**
to **24.3%**, or **+1.7** to
**+4.0** points over the assumption. It is the
strongest claim the option family supports, and the next section tests the
mechanism behind it without pricing a single option.

The option grid searched 504 rows before these were selected. That is a
smaller search than the one that produced the trend result, but it is not zero,
and no permutation null is available to correct it: a roll schedule has no
timing to randomize. Treat the option rows as the weakest evidence in this
repository, not the strongest.

## The mechanism, measured: how often should leverage be restored?

The option family's edge over a daily-reset fund is usually explained by
variance drag — a daily reset sells into declines and buys into rallies, an
annual roll does not. That explanation is testable with no option in it, by
holding constant leverage on a margin loan and varying only how often it is
restored. Financing is recovered from the fund identity in `letf.model.simulate`,
so these rungs carry exactly the funding and spread every other result here uses;
they carry no fund expense, which is why the daily rung sits slightly above
`UPRO_ALWAYS_3X`.

| leverage | reset | cagr | max_drawdown | terminal_multiple | wiped_out |
|---:|---|---:|---:|---:|---|
| 2 | daily | 15.08% | -87.4% | 272.3 | False |
| 2 | weekly | 15.70% | -86.5% | 337.7 | False |
| 2 | monthly | 16.20% | -84.9% | 401.4 | False |
| 2 | quarterly | 15.73% | -87.4% | 340.9 | False |
| 2 | annual | 14.95% | -98.5% | 260.7 | False |
| 3 | daily | 14.72% | -98.1% | 240.9 | False |
| 3 | weekly | 16.59% | -97.7% | 459.1 | False |
| 3 | monthly | 17.91% | -96.8% | 719.2 | False |
| 3 | quarterly | -100.00% | -100.0% | 0.0 | True |
| 3 | annual | -100.00% | -100.0% | 0.0 | True |

**Half the explanation survives and half of it does not.**

Slowing the reset does pay. At 3x it is worth 3.19% a year going
from daily to monthly, measured on realized prices with nothing modelled. So
variance drag is real and it is roughly the size the option structures imply.

Read that as a statement about volatility, not about patience. What a daily
reset pays for is oscillation — it sells after falls and buys after rises — so
the saving only exists where there is volatility to harvest. On a smoothly
rising path a slow reset earns *less*, because a gain dilutes the leverage while
the loan stays put. Both directions are pinned by tests.

But the benefit is not monotone, and past the turn it is not a penalty, it is
ruin: at 3x the quarterly and annual rungs are **wiped out entirely**. A margin loan does
not shrink as its collateral falls, so a long enough gap between rebalances lets
the debt overtake the assets. At 2x no rung is destroyed, which is the same
point from the other side — the cliff is a function of leverage, not of patience.

That reframes what the options are doing. They are not merely a slow reset,
because a slow reset at 3x is fatal. They obtain the slow-reset benefit
*and survive it*, because a call's loss is capped at its premium while a loan's
is not. The best option structure reaches 19.27% against the best surviving
rung's 17.91%, and it never dies. **The convexity is not a bonus on top
of the drag saving; it is what makes the drag saving reachable at this
leverage.**

The modelled premium is still doing work in that 19.27%. What this section
establishes without any model is narrower and worth stating on its own: reset
frequency matters, it matters by percentage points a year, and the frequency
that would capture most of it cannot be held with borrowed money.

## Which option, and how much of it

Strike and budget do different jobs. The budget decides how much of the
portfolio is at risk and moves returns by percentage points a year; the strike
trims the shape of what is bought. Size the premium first.

**26 of the 63 cells beat the unleveraged index on return and
drawdown at once** (11.26% at -55.3%), which is a region
rather than a knife-edge. The best cell in each exposure band, ranked by worst
ten-year outcome:

| band | moneyness | premium_budget | total_exposure | cagr | max_drawdown | cohort_10y_min_cagr |
|---|---:|---:|---:|---:|---:|---:|
| 1.5-2.0x | 0.90 | 15% | 1.55 | 10.78% | -44.0% | 5.17% |
| 2.0-2.5x | 0.95 | 25% | 2.02 | 14.04% | -49.3% | 2.31% |
| 2.5-3.0x | 1.05 | 30% | 2.53 | 15.76% | -60.4% | -1.36% |
| 3.0-9.0x | 1.10 | 40% | 3.18 | 17.36% | -71.1% | -7.27% |

`total_exposure` adds the safe sleeve to the option delta, so it is comparable
to a leveraged fund holding the same notional. **The structure does not carry to
3x.** Past roughly 2.2x the worst ten-year cohort turns negative and the
2000-2002 loss returns to the level the leveraged funds suffered. What this buys
is about 2x held well, not 3x held safely.

## Rolling it in practice

Two years out an index option is not available on an arbitrary date. Only a few
expiries are listed that far ahead — for SPY today, January, June and December —
so the roll must land on one of them and the maturity actually bought drifts
around the target. The simulation uses that calendar, which costs a few tenths
of a percent a year against the fixed-maturity fiction it replaced. It settles
into one trade a year, every December, into the December listing two years out.

A once-a-year roll means a single session's prices set the whole year, so the
choice of month has to be shown not to matter:

| roll | rolls | mean_entry_years | entry_years_spread | cagr | max_drawdown |
|---|---|---:|---:|---:|---:|
| january | 39 | 2.01 | 0.32 | 12.99% | -47.8% |
| june | 40 | 1.99 | 0.29 | 13.04% | -46.3% |
| december | 40 | 2.00 | 0.23 | 13.06% | -47.1% |
| nearest_listed | 40 | 2.00 | 0.23 | 13.06% | -47.1% |

The spread across roll months is small enough that this is a strategy rather
than a calendar artifact.

The roll *interval* is a separate question, and this report does not answer it:
everything above rolls once a year because that is what was chosen, not because
anything tested it. `reports/leaps_roll_monte_carlo_results.md` varies it from
six to eighteen months, and resamples the whole history in blocks to ask whether
the ranking here survives a different ordering of the crashes.

Three implementation points the numbers here do not capture. Use a
**European, cash-settled** index option rather than an American one on an ETF:
a deep in-the-money American call is liable to early assignment around
ex-dividend dates, which would break the roll schedule this depends on. Bid-ask
on a two-year contract is wide, and the 100 bp of premium charged here
each way may be optimistic. And equity skew makes in-the-money calls dearer than
a single volatility charges, which lands hardest on exactly the strikes the
table above prefers.

## The bond sleeve is one asset, and one bet

Every hedged structure above buys its protection from the same place: long
Treasuries, the only bond series this repository has. What separates them is how
much duration they take — sleeve weight times sleeve leverage. Nothing else in
this report varies that axis, and it is the largest unhedged bet here.

| bond_weight | bond_leverage | duration_exposure | cagr | max_drawdown | cohort_10y_min_cagr | cohort_10y_min_cagr_from_2000 | dot_com_return | rates_shock_2022 |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0% | 0 | 0.00 | 14.72% | -98.1% | -27.21% | -23.23% | -92.3% | -56.3% |
| 20% | 0 | 0.00 | 15.23% | -93.3% | -19.12% | -15.65% | -85.1% | -46.1% |
| 30% | 0 | 0.00 | 14.80% | -88.9% | -15.50% | -12.36% | -79.9% | -40.7% |
| 40% | 0 | 0.00 | 14.00% | -82.3% | -12.15% | -9.38% | -73.3% | -35.2% |
| 50% | 0 | 0.00 | 12.87% | -73.1% | -9.03% | -6.69% | -65.1% | -29.4% |
| 20% | 1 | 0.20 | 16.41% | -92.0% | -17.60% | -13.67% | -83.8% | -51.2% |
| 30% | 1 | 0.30 | 16.38% | -85.7% | -13.36% | -9.58% | -77.4% | -48.6% |
| 20% | 2 | 0.40 | 17.37% | -90.5% | -16.19% | -11.79% | -82.5% | -55.7% |
| 40% | 1 | 0.40 | 15.89% | -76.2% | -9.44% | -5.90% | -69.3% | -46.0% |
| 50% | 1 | 0.50 | 15.00% | -67.1% | -5.81% | -2.59% | -58.9% | -43.5% |
| 30% | 2 | 0.60 | 17.55% | -82.2% | -11.49% | -7.06% | -75.0% | -55.5% |
| 20% | 3 | 0.60 | 18.25% | -88.8% | -14.76% | -9.90% | -81.1% | -59.8% |
| 40% | 2 | 0.80 | 17.16% | -73.3% | -7.20% | -2.92% | -65.1% | -55.4% |
| 30% | 3 | 0.90 | 18.52% | -79.5% | -9.68% | -4.72% | -72.3% | -61.5% |
| 50% | 2 | 1.00 | 16.26% | -62.8% | -3.30% | 0.15% | -52.5% | -55.3% |
| 40% | 3 | 1.20 | 18.08% | -70.3% | -5.13% | -0.99% | -60.6% | -63.2% |
| 50% | 3 | 1.50 | 17.02% | -71.7% | -1.08% | 2.12% | -45.5% | -64.9% |

More duration monotonically raises the return and improves every tail column,
and monotonically worsens 2022. Both halves are the same fact seen twice: the
window is one long decline in yields. Unleveraged long Treasuries returned about
9% a year through 2011 and about -8% a year from 2021.

The from-2000 column is there to test whether the tail benefit is only the early
bond regime. It is not — duration still improves the worst decade for cohorts
entering from 2000. But that is weaker evidence than it looks. Duration
protected the *equity* crises of 2000, 2008 and 2020, when Treasuries rallied.
The one time the hedge failed was 2022, and by then equities had compounded far
enough through the 2010s that no ten-year window containing it was ever a worst
case. **The failure never landed in the statistic.** One rates shock, and it
arrived at a forgiving moment.

## Cutting duration at a fixed return is not de-risking

The option structures have the same axis, and an investor can dial it with two
ordinary funds by splitting the safe sleeve between long Treasuries and bills.
The best cell at each blend, among those reaching the return band, ranked by
worst twenty-year cohort:

| treasury_share | moneyness | premium_budget | duration_exposure | cagr | max_drawdown | cohort_20y_min_cagr | cohort_30y_min_cagr | dot_com_return | rates_shock_2022 |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0% | 0.90 | 65% | 0.00 | 19.19% | -88.6% | 4.50% | 13.18% | -84.6% | -52.3% |
| 25% | 0.85 | 65% | 0.09 | 19.04% | -84.5% | 6.15% | 14.01% | -81.6% | -51.6% |
| 50% | 0.90 | 55% | 0.22 | 18.81% | -77.5% | 7.34% | 14.34% | -74.7% | -51.4% |
| 75% | 0.80 | 65% | 0.26 | 18.93% | -78.7% | 8.27% | 15.08% | -77.0% | -53.0% |
| 100% | 0.85 | 55% | 0.45 | 18.92% | -72.0% | 9.68% | 15.82% | -69.1% | -54.8% |

Read the drawdown column against the treasury column. Holding return roughly
constant, going from an all-Treasury safe sleeve to an all-bills one costs
**17 points of drawdown** and
**5.2 points of worst
twenty-year cohort**, and buys **2.5 points**
of 2022 protection.

That is the opposite of the intended effect, and the mechanism is not subtle:
holding the return fixed while removing duration forces the option budget up,
and the budget is the dominant risk control in this family. At this return level
the marginal risk is not the bond sleeve, it is how much of the portfolio sits
in contracts that can expire worthless. An investor who distrusts long-dated
government debt enough to act on it should lower the return target rather than
swap Treasuries for bills at an unchanged one.

## Re-levering defeats the bounded loss

`LEAPS_RESTRUCK_3X` re-strikes to 3x of *current* wealth at each roll
instead of spending a fixed budget. It reaches 16.69% with a
-97.2% drawdown and a worst 20-year cohort of
0.19%.

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
3. **Options plausibly dominate daily-reset funds, and half the mechanism is
   measured.** The break-even against always-on 3x is the widest margin in the
   table (+4.0 volatility points at most). Slowing a
   reset really is worth 3.19% a year with no option
   involved — but only up to a point, and past it a margin position is destroyed
   outright. The option's contribution is surviving the frequency that kills the
   loan.
4. **Options do not clearly dominate the hedged alternatives.** Break-even sits
   -2.8 to +1.4 points from the
   assumption — inside its own error bar. This report cannot settle that and
   should not be read as settling it.
5. **A bounded loss per contract is not a bounded loss per portfolio.** The
   fixed-budget structures keep a floor; `LEAPS_RESTRUCK_3X` does not, and lands
   at a -97.2% drawdown and a 0.19%
   worst 20-year cohort — worse on both than the daily-reset fund it was meant
   to improve on.

## What would change any of this

Option price history, which would replace the modelled premia with measured
ones and make the break-even table unnecessary. Failing that, out-of-sample
data, or a rates regime that is not the forty-year bond bull market this window
consists of. Nothing here is established; these are candidates ranked by how
much has to be assumed to believe them.
