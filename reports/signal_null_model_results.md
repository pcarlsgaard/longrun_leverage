# Does the timing signal beat chance?

Every other battery in this repository varies a nuisance parameter — SMA
length, execution lag, financing spread, switching cost, subperiod. None of
them asks whether a rule with the same trading profile but **no timing
information** would have done as well. This report asks that.

## Method

For each strategy the realized position series is cut into episodes of constant
allocation. The episode lengths are reshuffled within each state, 2,000 times.
Every draw therefore holds the same number of switches, the same multiset of
episode lengths, and the same fraction of sessions spent leveraged as the real
rule. Only the dates move. A genuine timing signal should beat this null; a rule
whose benefit comes from simply holding less leverage should not.

`p_value` is the uncorrected one-sided permutation p-value. `p_sidak` corrects it
for the 144 nuisance-parameter cells each signal family was actually
evaluated over (SMA length × lag × spread × switching cost × off-asset). That
correction excludes the alternative regime signals and the cross-index variants,
so it is if anything too generous. **Read `p_sidak`, not `p_value`.**

## Results

| Strategy | Lag | Real CAGR | Always-on | Placebo median | Real percentile | p | p (Šidák) |
|---|---|---:|---:|---:|---:|---:|---:|
| SSO_SMA_TO_SP500 | LAG1 | 13.23% | 14.08% | 11.54% | 88.80% | 0.1124 | 1.000 |
| SSO_SMA_TO_TBILL | LAG1 | 11.43% | 14.08% | 9.54% | 77.35% | 0.2269 | 1.000 |
| TQQQ_SMA_TO_NASDAQ | LAG1 | 20.71% | 13.46% | 11.37% | 96.20% | 0.0385 | 0.996 |
| TQQQ_SMA_TO_TBILL | LAG1 | 18.71% | 13.46% | 8.31% | 92.65% | 0.0740 | 1.000 |
| UPRO_SMA_TO_SP500 | LAG1 | 16.29% | 13.70% | 11.18% | 94.30% | 0.0575 | 1.000 |
| UPRO_SMA_TO_TBILL | LAG1 | 14.43% | 13.70% | 9.23% | 89.15% | 0.1089 | 1.000 |
| SSO_SMA_TO_SP500 | LAG2 | 13.66% | 14.08% | 11.53% | 93.30% | 0.0675 | 1.000 |
| SSO_SMA_TO_TBILL | LAG2 | 12.28% | 14.08% | 9.51% | 85.75% | 0.1429 | 1.000 |
| TQQQ_SMA_TO_NASDAQ | LAG2 | 20.46% | 13.46% | 11.36% | 96.20% | 0.0385 | 0.996 |
| TQQQ_SMA_TO_TBILL | LAG2 | 18.60% | 13.46% | 8.41% | 92.65% | 0.0740 | 1.000 |
| UPRO_SMA_TO_SP500 | LAG2 | 17.14% | 13.70% | 11.20% | 96.75% | 0.0330 | 0.992 |
| UPRO_SMA_TO_TBILL | LAG2 | 15.73% | 13.70% | 9.21% | 93.55% | 0.0650 | 1.000 |

**0 of 12 strategies are significant at 5% after the multiplicity
correction.**

No headline strategy in this repository survives a correction for the size of
the search that produced it. That does not make the effects zero — most sit in
the upper tail of their own null — but it does mean none of them has been
*established* here. They are candidates, not findings.

The `placebo_beats_benchmark_fraction` column in the CSV is the blunter number:
the share of random-timing rules that beat always-on leverage. Where it is large,
most of the apparent benefit is reduced average exposure rather than timing —
which is exactly what the matched-exposure controls elsewhere in this repository
suspected.

## Edge concentration

A 40-year CAGR gap is a sum of ~10,000 daily log differences. If a few sessions
supply most of it, the gap describes those sessions, not a repeatable edge.

| Strategy | Lag | Total log advantage | Top 1 day | Top 5 | Top 20 | Top month | Month share |
|---|---|---:|---:|---:|---:|---|---:|
| SSO_SMA_TO_SP500 | LAG1 | -0.2959 | 33.32% | 148.27% | 438.09% | 2011-10 | 41.61% |
| SSO_SMA_TO_TBILL | LAG1 | -0.9370 | 22.22% | 98.23% | 287.09% | 2011-10 | 26.84% |
| TQQQ_SMA_TO_NASDAQ | LAG1 | 2.4738 | 17.73% | 63.39% | 174.06% | 2001-02 | 28.30% |
| TQQQ_SMA_TO_TBILL | LAG1 | 1.8094 | 33.31% | 121.32% | 338.90% | 2001-02 | 55.84% |
| UPRO_SMA_TO_SP500 | LAG1 | 0.8973 | 80.96% | 191.70% | 449.02% | 1987-10 | 76.59% |
| UPRO_SMA_TO_TBILL | LAG1 | 0.2562 | 373.20% | 923.27% | 2226.31% | 1987-10 | 335.13% |
| SSO_SMA_TO_SP500 | LAG2 | -0.1468 | 67.19% | 298.98% | 883.43% | 2011-10 | 84.16% |
| SSO_SMA_TO_TBILL | LAG2 | -0.6326 | 32.91% | 145.50% | 425.23% | 2011-10 | 39.88% |
| TQQQ_SMA_TO_NASDAQ | LAG2 | 2.3908 | 13.63% | 56.22% | 164.50% | 2001-02 | 29.28% |
| TQQQ_SMA_TO_TBILL | LAG2 | 1.7709 | 25.74% | 107.20% | 317.98% | 2001-02 | 57.06% |
| UPRO_SMA_TO_SP500 | LAG2 | 1.1906 | 60.80% | 144.25% | 337.74% | 1987-10 | 47.96% |
| UPRO_SMA_TO_TBILL | LAG2 | 0.7048 | 135.29% | 335.22% | 808.11% | 2008-10 | 103.35% |

"Top" means most favorable to the sign of the gap: for a positive advantage the
sessions that produced it, for a negative one the sessions that cost most. Shares
are fractions of the total, so 60% means one session produced 60% of a whole
40-year advantage.

**A share above 100% is the important case, not an error.** It means those few
sessions produced more than the entire gap and the rest of the history was net
negative — the strategy did not beat its benchmark over the other ~10,000
sessions. Every row here has a top-20 share above 100%. None of these
advantages is a property of the strategy across time; each is a property of a
handful of days, and the largest of them cluster in October 1987, February 2001
and October 2008. Do not quote a CAGR gap from this repository without this
column beside it.

