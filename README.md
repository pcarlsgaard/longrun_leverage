# Long-run leverage

Daily synthetic histories for **UPRO (3× S&P 500), SSO (2× S&P 500),
TQQQ (3× Nasdaq-100), and TMF (3× long US Treasuries)**, with unleveraged
comparators. The purpose is to measure long-horizon outcomes, including daily
compounding, financing costs, drawdowns and entry-date dependence.

**First run: data through September 2, 2026.** See the
[initial results and charts](reports/initial_results.md) and
[methodology](docs/methodology.md). Download the [initial CSV snapshot](data/snapshots/longrun_leverage_2026-09-02.zip).
This is a research reconstruction, not an
assertion that a leveraged ETF is suitable for any particular investor.

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
python -m unittest discover -s tests -v
longrun-leverage
```

Alternatively, without installing the package: `PYTHONPATH=src python -m letf.pipeline`.
An internet connection is needed for the initial source downloads. The pipeline
caches raw responses and hashes them in `reports/source_manifest.json`.
Use `longrun-leverage --offline` to rebuild from that verified cache. Change
`as_of` in `config.json` and use `--refresh` for a different endpoint. Raw source
data can be revised by their providers; a fresh download need not match an older
snapshot exactly. The initial runtime versions are recorded in the manifest.

The **Build time series** GitHub Actions workflow can also be run manually. It
produces a downloadable artifact with all generated CSVs and reports. Large
raw source files and working generated CSVs are excluded from Git; source code,
parameters, checksums and compact result tables/charts are committed, together
with a compressed initial data snapshot in `data/snapshots/`.

## What is generated

| File | Contents |
| --- | --- |
| `data/processed/daily_returns.csv` | 40 dated return series, in decimal units; missing history remains blank |
| `data/processed/wealth_indices.csv` | $1 wealth indices, including the initial entry close |
| `data/processed/daily_source_labels.csv` | Per-day source and proxy flags |
| `data/processed/daily_cost_components.csv` | Calendar-day financing, spread and fee accruals |
| `data/processed/rolling_20_30_year_cohorts.csv` | Monthly entry cohorts, exact calendar horizons |
| `reports/validation.csv` | Training, held-out and full-overlap tracking diagnostics |
| `reports/annual_validation.csv` | Year-by-year synthetic versus observed returns |
| `reports/compounding_decomposition.csv` | Exact log-growth compounding/cost decomposition |
| `reports/hedge_alternatives_*.csv` | Hedge families, option grid, break-even volatility, duration and safe-sleeve sweeps |
| `reports/leaps_roll_frequency.csv` | Every LEAPS structure at every roll interval, with realized maturities and measured cost drag |
| `reports/leaps_monte_carlo_{20y,30y}.csv` | Bootstrap distribution summaries per horizon |
| `reports/leaps_monte_carlo_{percentiles,drawdowns,ranks,blocks}.csv` | Percentile tables, drawdown probabilities, rank stability, block-length sensitivity |
| `reports/leaps_treasury_leverage_historical.csv` | Every structure at every sleeve leverage, with measured financing drag and the joint-loss diagnostic |
| `reports/leaps_treasury_leverage_{monte_carlo,stress,frontier}.csv` | Bootstrap distributions, the prolonged rates shock, and the compact frontier |
| `reports/lflr_reproduction{,_decomposition,_transitions}.csv` | The reproducibility ladder, its decomposition, and every dated crossover |
| `reports/nasdaq_leaps_{historical,breakeven,monte_carlo,comparison}.csv` | Nasdaq against S&P LEAPS, with break-even volatility premiums |
| `reports/leaps_frontier_{historical,monte_carlo,sensitivities,classification}.csv` | The 44-cell S&P and Nasdaq LEAPS lattice, its bootstrap distributions, seven sensitivities and the robust Pareto classification |
| `reports/xnd_short_maturity_{historical,monte_carlo,diagnostics,comparison}.csv` | The XND-length Nasdaq structures against their two-year controls, the five implementation channels, and the equivalence verdicts |
| `reports/leaps_duration_roll_{historical,monte_carlo,exposure,frontier}.csv` | 141 contract-length and roll variants, what each buys per dollar of premium, and the four frontiers at fixed budget and at matched delta |

Suffixes distinguish `BASE` (unfitted 50 bp spread), `TRAINED` (one spread fitted
through 2018), `SPREAD_0BP/50BP/100BP` (funding sensitivity), `NAV` and `MARKET`
(observations), and `HYBRID` (synthetic before actual fund observations, actual
afterward). **Use BASE for the default economic reconstruction.** HYBRID is not
an independent validation series. None of these is a historical tradeable ETF
before its actual inception.

The `1X` comparator columns are gross index returns or grossed-up fund proxies.
Observed `SPY_OBSERVED`, `QQQ_OBSERVED`, and `TLT_OBSERVED` columns retain the funds'
actual net adjusted returns. Nasdaq means **Nasdaq-100**, not Nasdaq Composite.

## Historical coverage

| Family | Initial entry close | Important qualification |
| --- | --- | --- |
| S&P 500 / UPRO / SSO | 1980-01-02 | VFINX proxy through 1988-01-04, then S&P 500 total-return index |
| Nasdaq-100 / TQQQ | 1985-10-01 | Price-only proxy through 1999-03-04; also exports a 0.5% dividend-yield sensitivity |
| Long Treasuries / TMF | 1986-05-19 | VUSTX before TLT coverage in July 2002; different duration/holdings |

## Portfolio and 200-day SMA analysis

See [portfolio and SMA results](reports/portfolio_and_sma_results.md) for the
prespecified 14 static portfolios and SSO/UPRO/TQQQ rotations into either the
corresponding 1× equity index or accrued 3-month Treasury bills. All main tables
use matched dates; quarterly rebalancing and 200 sessions are primary. Monthly /
annual rebalancing, 150/250 sessions, and 0/50/100 bp financing are sensitivities.

```bash
PYTHONPATH=src python -m letf.analysis --offline
# Or after installation:
longrun-leverage-analysis --offline
```

This analysis-only command reuses the original daily histories and does not
rebuild underlying series. A verified, self-contained input bundle is committed
at `data/snapshots/portfolio_sma_inputs.zip`, enabling offline analysis from a
clean clone. It contains the unchanged daily-return CSV and cached FRED DTB3.
Missing inputs are restored; existing inputs must match recorded hashes.
The original full-series pipeline still requires its full raw cache offline.

To update, first regenerate daily histories with the existing pipeline and
updated configuration, then run `longrun-leverage-analysis --refresh` to refresh
DTB3 and archive the analysis inputs. The analysis adds cash provenance to
`reports/source_manifest.json` and its own input/configuration/bundle hashes to
`reports/portfolio_sma_manifest.json`.

Compact metrics, rolling summaries, regime comparisons and sensitivities are
under `reports/`. Daily strategy returns, positions, T-bill returns and exact
individual cohorts are written to `data/processed/`. The report documents all
metric conventions and limitations, including early index proxies and the
idealized close-to-close execution assumption.

## SMA robustness and falsification

See [falsification results](reports/sma_falsification_results.md) for the execution-delay,
switching-cost, historical-subperiod, price-signal, volatility and attribution tests.
**The UPRO-to-1× result survives modest costs and a full-session delay separately,
but loses its CAGR advantage with delay plus 50 bp per switch.** Performance also
depends on regime: the 2000–2009 decade contributes most of the net benefit.

```bash
PYTHONPATH=src python -m letf.falsification --offline
# Or after installation:
longrun-leverage-falsification --offline
```

This command verifies/restores the original frozen daily returns and DTB3, plus
`data/snapshots/sma_price_inputs.zip` for price-only signal comparisons. Original
histories, source manifests and validation outputs are untouched. The new bundle
and raw source hashes are in `reports/sma_price_input_manifest.json`; run settings
and dependency versions are in `reports/sma_falsification_manifest.json`.
Online mode only obtains missing price-signal sources, retaining the fixed endpoint
and the original verified histories. Updating the original snapshot requires a
separate, deliberate refresh of the price bundle to the same endpoint.

All eight requested compact CSVs and the full prespecified sensitivity grid are
committed under `reports/`, with four figures. Daily returns and positions are
generated under `data/processed/`. Tests cover symmetric lagging, transition-only
costs, volatility buckets, source calendars, boundaries, attribution and stress dates.

## Combining funds

The portfolio helper supports fixed initial weights with no further rebalancing,
or monthly, quarterly and annual rebalancing. ETF daily leverage resets occur
inside each sleeve regardless of the portfolio's rebalance rule.

```python
import pandas as pd
from letf.model import portfolio, wealth

daily = pd.read_csv("data/processed/daily_returns.csv", index_col="date", parse_dates=True)
# Illustrative weights only; no portfolio optimization has been performed.
legs = daily[["UPRO_BASE", "TMF_BASE"]]
start = max(legs[col].first_valid_index() for col in legs)
legs = legs.loc[start:]  # preserve any interior missing rows so validation catches them
result = portfolio(legs, {"UPRO_BASE": 0.55, "TMF_BASE": 0.45}, rebalance="quarterly")
growth = wealth(result, initial=10_000)
```

Compare portfolios over identical dates. A later analysis can test matched
20–30-year entry cohorts, contributions, rebalancing policies and real returns.
Longer holding periods do not remove financing costs or recover money lost by
a fund that reaches zero. The core simulator includes no investor taxes, new contributions, withdrawals
or inflation adjustment. Switching costs are tested separately in the falsification extension.

## Capital-reserve experiment

**No reserve remains the best-supported growth baseline.** Small UPRO gains
under delayed execution do not generalize to SSO or fresh-investor cohorts;
the staged rules often exhaust reserves before the eventual crisis trough.

See [capital-reserve results](reports/capital_reserve_results.md) for prespecified
high-water-mark gain harvesting, asymmetric reserve bands, staged trend-recovery
deployment, and separate drawdown deployment. Fixed 90/10 and 85/15 allocations,
matched-average cash and effective-leverage controls distinguish timing from
ordinary lower exposure. Both UPRO/SSO, LAG1/LAG2 and 0/10/25/50-bp costs are tested.

```bash
PYTHONPATH=src python -m letf.capital_reserve --offline
PYTHONPATH=src python -m letf.reserve_cohorts
```

Both commands use the existing verified offline snapshot. The second restarts
reserve/HWM state for monthly fresh-investor 20/30-year cohorts at 0/25 bp; the
first also reports the original running-policy cohort convention at all costs.
Detailed ledgers and fresh cohorts go to `data/processed/`; compact CSVs, five
figures, assumptions and full-cycle attribution are committed under `reports/`.
`STATIC_RESERVE_10/15` are aliases for the quarterly `FIXED_90_10/85_15` controls.

## Regime-signal comparison

The [regime-signal report](reports/regime_signal_results.md) compares SMA, absolute
and relative volatility, efficiency, regression quality, sign churn and Awesome
Oscillator for 3×/1× S&P leverage management, including matched-exposure controls.
Reproduce the five compact reports offline with `PYTHONPATH=src python -m letf.regime_signals`.

## Price-only signal convention

Every timing signal in this repository reads an **unadjusted price index**, the
series an investor actually watches. Earlier work used total-return levels,
which no index quotes intraday and which cross a moving average on different
sessions. The difference is not cosmetic: under LAG2 it changes the position
held on 1987-10-19 and, through that one session, roughly 2.3pp of the 40-year
CAGR. `letf.signals.level_position` is canonical; `letf.signals.sma_position`
is retained only as the legacy comparator earlier reports were built on, and
is used nowhere else.

See [price-only revision results](reports/price_signal_revision_results.md) and
[cohort distributions](reports/price_signal_cohort_distribution_summary.md).

```bash
PYTHONPATH=src python -m letf.price_signal_revision
PYTHONPATH=src python -m letf.cohort_distributions
```

## Cross-index signal

Whether one broad equity-risk signal can govern leverage in another index:
TQQQ driven by the S&P 500 price SMA versus the Nasdaq-100's own. See
[cross-index results](reports/cross_index_tqqq_sma_results.md). The full-grid
win rate reported there is one comparison re-scored under nuisance parameters,
not independent evidence; the subperiod table is the part that varies, and it
reverses after 2020.

```bash
PYTHONPATH=src python -m letf.cross_index_signal
```

## Does any of it beat chance?

**Read this before quoting a CAGR advantage from anywhere else in the
repository.** The batteries above vary nuisance parameters — SMA length, lag,
spread, switching cost, subperiod. None of them tests the prior question:
whether a rule with the same trading profile but no timing information would
have done as well.

[`letf.null_model`](reports/signal_null_model_results.md) runs that test. It
cuts each realized position series into episodes and reshuffles their lengths,
holding the switch count, the episode-length distribution and the fraction of
leveraged sessions fixed, so only the dates move. It reports the uncorrected
permutation p-value and a Šidák correction for the size of the grid that
produced the result.

It also reports **edge concentration**: the share of a strategy's total log
advantage contributed by its largest 1, 5 and 20 sessions. For UPRO
SMA(200, LAG2) versus always-on, October 1987 supplies about 41% of the entire
40-year advantage and 1987-10-19 alone about 39%. A gap that concentrated
describes those sessions, not a repeatable edge.

```bash
PYTHONPATH=src python -m letf.null_model
```

## If the edge is twenty days, what should be bought instead?

A rule that trails its benchmark on ~10,000 sessions and is repaid in crashes is
a synthetic put bought on instalments. That makes the next question a pricing
question, not a signal question, and
[`letf.hedge_alternatives`](reports/hedge_alternatives_results.md) asks it. Four
families on one window and one financing basis: the trend rule itself; static
leveraged stock/bond mixes; simply holding less leverage; and rolling long-dated
calls against a fixed safe sleeve.

Three things it reports that the batteries above do not:

* **The hedges fail in different regimes.** No structure cushions more than
  three of the five crashes best, and the bond mixes did *worse than no hedge*
  in 2022, when duration and equity fell together.
* **Concentration is a property of a pair, not of a strategy.** Measured against
  always-on 3x leverage almost everything looks like a twenty-day effect, because
  a benchmark that falls 98% can only be beaten in a crash. Measured against the
  unleveraged index, most of it does not.
* **A bounded loss per contract is not a bounded loss per portfolio.** An option
  re-struck to a constant multiple of *current* wealth compounds losses across
  rolls and lands worse than the daily-reset fund it was meant to improve on.
* **The bond sleeve is one asset and one bet.** Every hedged structure buys its
  protection from long Treasuries, and they differ only in how much duration
  they take. More of it improves every tail column and worsens 2022, which is
  the same fact twice: the window is one long decline in yields.
* **Cutting duration at a fixed return is not de-risking.** Holding the return
  constant while draining the safe sleeve of Treasuries forces the option budget
  up, and that costs more drawdown than the duration cut saves.

**The option rows are modelled, not measured, and are the weakest evidence in
this repository.** There are no option prices here and no network access to
obtain any, so premia are Black-Scholes values on an assumed implied volatility.
The report's headline for that family is therefore a **break-even volatility** —
how expensive options must have been for the structure to lose — which puts the
unmeasured input in the output where a reader can apply their own view. On that
measure the option structures beat daily-reset leverage by about four volatility
points, and do **not** reliably beat the hedged alternatives: break-even there
sits within the assumption's own error bar.

```bash
PYTHONPATH=src python -m letf.hedge_alternatives
```

## Does the roll interval matter, and does the ranking survive reordering?

The option work above fixes two things it never varied: it buys roughly two-year
calls, rolls when about a year is left, and measures everything on the one path
history actually took.
[`letf.leaps_robustness`](reports/leaps_roll_monte_carlo_results.md) attacks
both, on the three structures the grid singled out (80/25, 85/30, 95/50, all
against a Treasury sleeve) and without re-searching any of them.

**Part A** moves only the roll interval — 6, 9, 12, 13 and 18 months — mapping
each onto an expiry that is actually listed and reporting the maturity realized
rather than the one intended. **Part B** resamples the daily record in joint
moving blocks, rebuilding the volatility proxy, the cash rate and the trend
signal inside every path, and asks whether the ranking of strategies is a
property of the strategies or of the order in which crashes arrived.

* **Nine-month rolling cannot be bought.** With three long-dated expiries listed
  a year out, asking for nine months delivers about ten and a half. Twelve and
  thirteen months are the same portfolio on a real expiry calendar.
* **Differences are quoted in volatility points.** One point of assumed implied
  volatility is worth 0.3-1.4 points of CAGR depending on structure, and that is
  the scale a roll-interval gap has to clear before it means anything.
* **The realized path prefers an eighteen-month roll; the bootstrap does not.**
  Across resampled orderings the interval is worth roughly a tenth of what the
  choice of structure is worth. Annual rolling is not optimal on the path that
  happened — it is the interval whose answer changes least when the path
  changes.
* **A shorter roll does control exposure drift, and does not pay for itself.**
  It lifts the delta floor after a major loss and tightens exposure dispersion,
  at a cost in CAGR several times the drift it removes.
* **The trend rule is the result that moves.** It ranked second on the realized
  path and finishes below the unlevered index on about half of resampled
  orderings, with a modal rank of last. That agrees with the null model, but it
  is not independent evidence: a moving block destroys structure longer than the
  block, and a 200-day signal is handicapped by construction. The block-length
  table measures that handicap rather than waving at it.
* **95/50 keeps its rank and keeps its tail.** It is the most rank-stable
  strategy here and still draws down more than 75% on about three paths in ten,
  with a fifth-percentile outcome below the unlevered index.

**The bootstrap does not make the modelled option prices measured**, and it is
not a forecast: it resamples the same forty years, so it produces a distribution
over *orderings*, never over futures.

```bash
PYTHONPATH=src python -m letf.leaps_robustness            # ~1.5 min on four cores
PYTHONPATH=src python -m letf.leaps_robustness --paths 500  # a quicker look
```

## Can levering the safe sleeve buy return more cheaply than premium budget?

The option family is ordered by premium budget, and budget buys return and
drawdown together in fixed proportion. Duration is a second dial:
`letf.hedge_alternatives` found it improved return and several drawdown
statistics up to a point and made 2022 much worse past it.
[`letf.treasury_leverage`](reports/leaps_treasury_leverage_results.md) holds the
option structure fixed and levers the *safe* sleeve instead — 1.0x, 1.25x, 1.5x,
2.0x, with 3x and TMF as comparison rows rather than specifications — and asks
whether return bought that way comes with better tails than the same return
bought with a bigger budget.

The sleeve is constant-leverage long Treasuries, borrowing only the exposure
above 1x at funding recovered from the repository's own 3x fund. The cost is
measured, not modelled: each structure is run again on a sleeve of identical
notional whose borrowing is free.

* **Both dials are priced in the same units.** Points of median drawdown paid
  per point of median CAGR bought, for a step of sleeve leverage and for a step
  of premium budget. A sleeve step that costs more than a budget step is not a
  new frontier, only a worse way along the old one.
* **The sleeves have to be measured separately.** A levered safe sleeve is a
  hedge only while it rises when equities fall. The report counts rolling
  windows in which the option leg and the Treasury sleeve *both* lose heavily —
  a statistic computed on the portfolio alone cannot distinguish a joint loss
  from a severe single-sleeve one.
* **In forty years there is exactly one joint-loss episode.** Every row's worst
  simultaneous loss is the twelve months ending in November 2022. Whether to
  lever the sleeve is a question about how often that recurs, which is precisely
  what a sample containing one instance cannot answer.
* **So the weight rests on a synthetic shock.** The 2022 episode is replayed end
  to end, whole daily rows, so stock/bond correlation stays positive far longer
  than the record allows. Each case is run from several calendar starts, because
  at one roll a year, where the roll falls relative to the shock is luck rather
  than leverage.
* **Modest leverage cannot substitute for budget.** 85/30 with the most sleeve
  leverage tested still falls well short of 95/50's return and still trails it
  on most bootstrap paths, while spending part of the tail advantage that was
  the reason to prefer 85/30.
* **Constant leverage is not TMF.** At matched 3x notional the fund costs
  materially more than financing alone, and the two are conceptually different
  instruments — one defined by the exposure it holds, the other by a daily rule
  that produces it plus a path dependence.

```bash
PYTHONPATH=src python -m letf.treasury_leverage             # ~3 min on four cores
PYTHONPATH=src python -m letf.treasury_leverage --paths 500 # a quicker look
```

## Does our SMA implementation actually disagree with the published paper?

The post-COVID revision of *Leverage for the Long Run* reports roughly 35.4% for
buying and holding UPRO to the end of 2020 and roughly 24.2% for rotating between
UPRO and Treasury bills on a 200-day average.
[`letf.lflr_reproduction`](reports/lflr_reproduction.md) asks whether this
repository's lower numbers are a disagreement about the data or about the method,
by varying one assumption at a time on the paper's own window.

* **Both published figures reproduce**, to within a basis point on buy-and-hold
  and a few on the rotation. The reconstruction this repository uses before the
  fund existed is indistinguishable from the fund over the years both cover, so
  the leveraged series is not the explanation.
* **Our preferred implementation lands within half a point of the paper** over
  the paper's own window — the disagreement is a longer-window phenomenon, not a
  2009-2020 one.
* **The decomposition does not add up, and that is the finding.** One session of
  execution timing is worth −0.85% on a total-return signal and +5.87% on a price
  signal. The same assumption, opposite sign. No ordering of the steps is
  privileged, so the report gives both the sequential walk and the
  one-at-a-time effects and quotes ranges rather than point estimates.
* Every crossover is dated three ways — signal close, execution close, first
  affected return — and saved, because prior work here established that a
  handful of sessions can carry decades.

```bash
PYTHONPATH=src python -m letf.lflr_reproduction
```

## Does a faster-growing underlying need less option capital?

[`letf.nasdaq_leaps`](reports/nasdaq_leaps_comparison.md) writes the same LEAPS
architecture on the Nasdaq-100 instead of the S&P 500 — four prespecified
structures at 80-85% strikes and 20-30% premium budgets, nothing searched, the
volatility methodology deliberately not retuned.

* **A Nasdaq structure at 30% premium reaches within a point of the S&P's 50%
  structure**, with roughly a third of its probability of a 60% drawdown and a
  higher fifth percentile at every horizon measured.
* **It survived the dot-com bust better than the S&P structure of comparable
  return**, which is the sharpest available test and the strongest thing in the
  report.
* **The whole advantage is the underlying's growth advantage passed through**, and
  the bootstrap cannot test whether that persists — every path is resampled from
  days on which it did.
* **The volatility premium is imported from a different market.** Nasdaq realized
  volatility is materially higher, so a flat three-point premium is
  proportionally a smaller loading; the report gives break-even premiums and a
  proportionally matched rerun rather than trusting the point estimate.
* Nasdaq total returns are a price-only proxy through 1999-03-04. The 1987 column
  sits entirely inside that era and is labelled; twenty-year cohorts entering
  after it are reported separately.

```bash
PYTHONPATH=src python -m letf.nasdaq_leaps        # ~1 min on four cores
```

## Where is the frontier, and which of it survives being doubted?

[`letf.leaps_frontier`](reports/leaps_frontier_results.md) replaces the
hand-picked structures above with a prespecified regular lattice — 16 S&P cells
(three strikes x five premium budgets, plus the `SPX_80_25` anchor) and 28
Nasdaq cells (four strikes x seven budgets) — and carries every one of them
through a 15,000-path bootstrap at 10, 20 and 30 years plus seven sensitivities:
option price at +0/+3/+6 volatility points, block length at 21/63/126, roll at
9/12/18 months, and a 1.25x Treasury sleeve. A cell is called efficient only if
it is still efficient after the assumptions that produced it are moved.

* **Two frontiers, because there are two questions.** Drawn over all 44 cells it
  answers whether the choice of underlying matters; drawn inside a family it
  answers which structures are efficient once the index is chosen. A cell can be
  efficient in its family and dominated jointly, and that gap is the Nasdaq
  result.
* **On the S&P the strike matters more than the budget, and the strike is 0.85.**
  All five 0.85 cells are robust within the S&P family across every budget from
  30% to 50%; every 0.95 cell is dominated and every 0.90 cell except 90/50 is
  too. 90/40 is a genuine elbow — 19.7 points of tail per CAGR point going in
  against 27.4 coming out — but it is an elbow on a ladder that is already
  dominated.
* **The resolution limit is the option price, not the Monte Carlo.** One
  volatility point of modelled CAGR runs from 0.28% at NDX_80_20 to 1.39% at
  SPX_95_50, wider than the 0.25-point comparison tolerance at every cell in the
  lattice, so every pairwise verdict widens its bar to whichever is larger and
  differences inside it are reported as economically unresolved.
* **Nasdaq buys the same growth on less option capital, but not without limit.**
  NDX_90_30 reaches SPX_90_40 on 30% of capital against 40%, and holds that under
  every sensitivity; it falls 1.16% short of SPX_90_50 and does not hold against
  it under block reordering. The dot-com bust is not uniformly kind either — the
  Nasdaq cell lost more than SPX_90_40 over 2000-2002, and only beat the
  higher-budget S&P cells.
* **A dearer option flatters the Nasdaq comparison.** A flat premium is
  proportionally a smaller loading on a more volatile underlying, so the +6-point
  arm costs the high-budget S&P cells more than it costs these. That is a
  property of the imported assumption, not evidence of robustness.
* **1.25x on the Treasury sleeve reorders nothing** — rank correlation 0.9996 on
  median CAGR — but moves six cells across the frontier boundary. It relocates
  the frontier without changing which cells earn more.
* **The menu the run supports**, each holding at least five of seven
  sensitivities and separated from its neighbour on at least two frontier axes:

  | regime | cell | budget | median 30y CAGR | p5 | median max DD | P(DD>60%) |
  |---|---|---:|---:|---:|---:|---:|
  | conservative growth | `SPX_80_25` | 25% | 12.00% | 6.94% | -36.3% | 0.7% |
  | balanced growth | `NDX_80_25` | 25% | 13.74% | 7.32% | -41.0% | 3.0% |
  | enhanced growth | `NDX_85_30` | 30% | 15.37% | 7.46% | -48.1% | 12.7% |
  | aggressive growth | `NDX_90_35` | 35% | 16.77% | 7.20% | -56.0% | 35.2% |
  | maximum growth | `NDX_90_40` | 40% | 17.66% | 6.96% | -61.6% | 56.3% |

* The Nasdaq's excess growth over the S&P is an **input** to every resampled
  path, so no amount of resampling tests it, and pre-1999 Nasdaq history is the
  same price-only proxy flagged above.

```bash
PYTHONPATH=src python -m letf.leaps_frontier                 # ~26 min on four cores
PYTHONPATH=src python -m letf.leaps_frontier --paths 1000 \
    --sensitivity-paths 500                                  # a quicker look
```

## Can the XND contracts that exist implement any of this?

[`letf.xnd_short_maturity`](reports/xnd_short_maturity_results.md) asks the
implementation question the Nasdaq work leaves open. Every result above buys
roughly two-year calls; XND expirations reach only to about December 2027, so
the longest contract actually available is nearer fifteen months. The 80/25 and
85/30 structures are run at fifteen months on 6-, 9- and 12-month rolls against
their two-year controls, with a 2.25-year bridge that prices the length of a QQQ
contract and nothing else about it.

* **Shortening the contract does not cost return — it adds it**, between 0.33
  and 1.09 points of median CAGR. That is not a free lunch: a shorter call is
  cheaper, so a fixed premium budget buys more of it. Mean delta exposure runs
  0.83 at fifteen months against 0.74 at twenty-four and 0.71 at twenty-seven.
  **The fifteen-month version is a levered-up portfolio, not a degraded one.**
* **So it is not implementation-equivalent, and none of it fails by earning
  less.** Five of the six short specifications breach the prespecified tolerances
  on drawdown probability or the fifth percentile; only 80/25 on a six-month roll
  stays inside, and only as "acceptable but different". An investor holding the
  validated budgets on XND would have to cut them to get back to the validated
  portfolio.
* **The maturity axis runs opposite to the usual intuition.** The 2.25-year
  bridge earns *less* than the two-year control and draws down less, so there is
  no QQQ length advantage to weigh against XND's cash settlement — the available
  lengths are points on one exposure-for-budget trade. What is left is what this
  module does not model: American exercise, early assignment, ETF tracking,
  spreads and tax.
* **The nine-month roll is the one to avoid**, and not for its nominal maturity:
  the listed calendar cannot hit fifteen months while keeping six in hand, so it
  buys anything from twelve to eighteen. That drift, not the maturity, is what
  costs it 11.5-11.8 points more than its control across 2000-2002.
* Theta and vega are central differences of the same pricer the simulation uses,
  pinned against the closed form. Decay is a drag on average but not on every
  session: a deep in-the-money European call gains value as expiry nears whenever
  the dividend forgone outweighs the interest on the strike not yet paid, which
  is most of the zero-rate era.
* The three-point volatility premium is **not** re-estimated for a shorter
  contract, and a fifteen-month option sits on a different part of a term
  structure this repository does not observe.

```bash
PYTHONPATH=src python -m letf.xnd_short_maturity   # ~4 min on four cores
```

## Is a contract duration efficient, or does it just buy more delta?

[`letf.leaps_duration`](reports/leaps_duration_roll_results.md) closes the loop the
two modules above open. The frontier work left maturity at the inherited two-year
convention; the XND work found that a shorter contract is cheaper, so a fixed
premium budget buys more of it. **A duration that earns more at the same budget has
therefore not been shown to be better, only larger** — so every comparison here runs
twice, once at a fixed budget and once at a budget interpolated to match the
canonical rule's mean delta.

141 variants: 45 Nasdaq cells across the three regimes XND can approximately supply,
96 S&P cells across eight maturity/roll regimes spanning 15 to 30 months.

* **Delta bought per dollar of premium falls monotonically with length** — 1.13x at
  15 months, 1.07x at 18, 0.99x at 24, 0.93x at 30, against the canonical rule. That
  single fact explains most of what a fixed-budget duration comparison shows.
* **No regime is robustly efficient against the canonical rule at matched delta.**
  Six of the nine are dominated, two unresolved, one conditionally efficient. The
  two-year annual roll was inherited rather than chosen, and nothing tested
  displaces it.
* **The short-maturity advantage does not survive matching the exposure.** Nasdaq
  15m/12m goes from +1.04% of median CAGR at a fixed budget to +0.03% at matched
  delta; the S&P equivalent from +0.85% to +0.08%. Not one short regime keeps a gap
  larger than its own resolution bar.
* **Rolling every six months is never paid for**: every six-month regime is
  dominated, at roughly double the turnover.
* **Holding a short contract to ~3 months remaining is the worst tail behaviour in
  the lattice** — P(DD>60%) of 32.5% on the Nasdaq against 12.2% canonical — and it
  is also the regime that buys the most delta, which is the same fact twice.
* **About 85-88% of the canonical budget on 15-month XND reproduces canonical
  24-month exposure**, so roughly 25.5-26.5% in place of a 30% budget.
* **The honest headline is question ten.** One volatility point is worth 0.52-0.79%
  of CAGR here, and every regime's matched-delta gap is smaller than its own
  volatility point. These rankings are ordered, but ordered inside the error bar of
  a term structure this repository does not observe — and the loading is not retuned
  by maturity, which is the assumption a duration comparison leans on hardest.

```bash
PYTHONPATH=src python -m letf.leaps_duration   # ~14 min on four cores
```

This is the most expensive module here by a wide margin: the primary run is
15,000 paths at three horizons and the sensitivities add ~30,000 more
strategy-paths, so `scripts/check_reproducible.sh` now takes roughly half an
hour longer than it used to.

## Reproducing every committed result

```bash
scripts/regenerate.sh          # rebuild the results whose manifests no longer prove them
scripts/regenerate.sh --full   # rebuild everything, about an hour
scripts/check_reproducible.sh  # regenerate, then assert nothing changed
scripts/stale_modules.py --why # what would rebuild, and why
```

**Only what needs rebuilding is rebuilt.** `scripts/stale_modules.py` asks, per
step, whether the manifest beside its results still proves them: every source
file it hashed unchanged *and* the import graph unchanged, every input unchanged,
and every committed output still hashing to what the manifest recorded. All three
have to hold, so a hand-edited result fails the last check and is rebuilt and
compared like any other change — nothing is taken on trust, and "skipped" means
"already proven by the run that wrote this manifest". Six steps record no source
hashes and therefore always run. CI does this on every push; a nightly job
rebuilds everything from nothing, which is what the fast path leans on.

The second script is a CI job. Every committed `.csv` and `.md` under
`reports/` must be what the code produces: text exactly, numbers within 1e-8
relative. Numbers get a tolerance because byte identity is unachievable for
them — these results accumulate arithmetic over ~10,000 sessions and different
numpy builds order it differently, moving the last digit by ~2e-10 whatever
output precision is chosen. Manifests are checked on the fields that answer "what
inputs and what code made this?" — input hashes, source hashes, windows,
parameter grids — ignoring recorded runtime versions and digests of generated
files, both of which are environmental. `*.png` is excluded entirely, since
matplotlib output is not reproducible across platforms.

**Any change under `src/` needs `scripts/regenerate.sh` before committing**,
even one that cannot move a number. Manifests record the SHA-256 of every
source file in each result's import graph, so an edited comment makes them
describe code that is no longer what ran. That is strict on purpose: a
provenance record that is approximately true is not one.

**No committed report may be edited by hand** — a generator writes the same
path and will silently discard the edit. Analysis that belongs in a report
belongs in the generator.

## Known limitations

Collected rather than scattered, because they bound every result above:

- **Overlapping cohorts are not independent trials.** A 30-year percentile over
  a 40-year history is built from windows sharing almost all their data.
- **Multiplicity.** Thousands of specification rows are evaluated. Nothing here
  survives a correction for the size of that search; see the null model.
- **Edge concentration.** Much of the SMA advantage is a handful of sessions.
- **Ex-post controls.** Matched-exposure comparators set exposure from
  full-sample averages. They are diagnostics, not implementable strategies.
- **Proxy history.** Early Nasdaq-100 is price-only proxy data; VFINX and VUSTX
  stand in for index and Treasury history before their coverage.
- **Idealized execution.** Close-to-close, no market impact, no taxes, no
  contributions, withdrawals or inflation adjustment.
- **Modelled option prices.** The `LEAPS_` rows in the hedge comparison are the
  only results here not computed from realized prices. No option price or
  implied-volatility history is available to this repository, so premia are
  Black-Scholes values on an assumed volatility, and the break-even table exists
  because a point estimate would not be honest.
- **One rate regime.** The whole window is a secular decline in yields, so the
  leveraged-Treasury leg of any hedged structure is itself a single-regime bet,
  in exactly the way October 1987 is for the trend rule.
- **The Nasdaq advantage is a growth-regime bet.** Every Nasdaq LEAPS result
  inherits the Nasdaq's historical excess growth over the S&P, which is an input
  to every simulated path rather than a finding of any of them.
- **One joint-loss episode.** The whole case for a levered Treasury sleeve rests
  on how often equities and duration fall together. The record contains one such
  stretch, in 2022, lasting ten months — which is why the synthetic shock that
  prolongs it carries more weight than any historical column.
- **The bootstrap is not a forecast, and is not neutral.** It resamples the same
  forty years, so it cannot produce a crash worse than 1987 or a bond regime
  unlike the one observed; it distributes *orderings*, not futures. It also
  destroys dependence beyond one block, which handicaps a 200-day trend signal
  by construction — bounded by the block-length table, not removed by it.

A full methodological and code review of the experiments is in
[docs/experiment_review.md](docs/experiment_review.md).
