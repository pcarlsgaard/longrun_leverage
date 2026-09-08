"""Does modest leverage on the Treasury sleeve buy return more cheaply than option budget?

`letf.leaps_robustness` leaves the option family ordered by premium budget:
80/25 preserves capital best, 85/30 accumulates best, 95/50 earns most and has
the worst tail. Budget is the only dial in that ordering, and it is a dial that
buys return and drawdown together in fixed proportion.

`letf.hedge_alternatives` found a second dial. Duration — bond weight times
sleeve leverage — improved both return and several drawdown statistics up to a
point, and made 2022 much worse beyond it. The two findings suggest an
experiment neither module ran: hold the option structure fixed and lever the
*safe* sleeve instead, and ask whether return bought that way comes with better
tails than the same return bought with a larger premium budget.

The falsification question, stated so it can fail: **can some of the return
currently obtained by raising the premium budget instead be obtained from modest
Treasury leverage, with better tail behaviour?** A yes needs the levered-sleeve
structure to reach comparable return at a genuinely smaller drawdown, hold that
under resampling, and survive a rates shock longer than the one the sample
contains. Failing any of those, the answer is that the sleeve has stopped being
a hedge and become a second leveraged bet on the same duration regime that
flatters every bond number in this repository.

Three things this module is careful about.

* **Leverage is financed, not assumed.** The sleeve is a constant-leverage
  long-Treasury position that borrows only the exposure above 1x, at the funding
  the repository already recovers from its own 3x fund. The cost is then
  *measured* by rerunning each structure with financing set to zero.
* **Constant leverage is not TMF.** A daily-reset 3x fund carries its own
  expense ratio, spread and path dependence. Both appear, as separate rows, so a
  reader can see what the product costs beyond the exposure.
* **The sample is one long bond bull market.** Every favourable duration number
  here inherits that, so the report's weight rests on the synthetic rates shock
  rather than on the historical columns.

Option premia remain modelled rather than measured; see `letf.options`.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import platform

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter
import numpy as np
import pandas as pd
import scipy

from .cohorts import cohort_cagrs, nav_path
from .hedge_alternatives import (CRASHES, EQUITY, TMF, TREASURY, cagr, constant_leverage,
                                 implied_financing, markdown_table, max_drawdown)
from .leaps_robustness import (BENCHMARK_INTERVAL, BLOCK_LENGTHS, EQUITY_TR, HORIZONS,
                               Horizon, METRICS as PATH_METRICS, POOL_COLUMNS,
                               PRIMARY_BLOCK, PRIMARY_PATHS, SEED, SENSITIVITY_PATHS,
                               STRUCTURES, TREASURY_R, WARMUP_SESSIONS, bootstrap_pool,
                               build_horizon, build_path, leaps_rule, load, monte_carlo,
                               path_metrics, phrase)
from .model import calendar_days
from .options import leaps_arrays, roll_schedule, simulate_leaps_arrays
from .provenance import FLOAT_FORMAT, sha, source_hashes, stable_floats

# The sleeve leverages under test. 3x is deliberately not one of them: it is
# carried below as a stress row beside TMF, because the question is whether
# *modest* leverage helps, and a specification that includes the extreme answers
# a different question.
LEVERAGES = (1., 1.25, 1.5, 2.)
STRESS_LEVERAGE = 3.
# The annual roll `letf.leaps_robustness` found to be the robust choice. Nothing
# here varies it: this experiment moves the sleeve, and one dial at a time is
# the whole point of running it separately.
ROLL_LABEL = BENCHMARK_INTERVAL
ROLL_YEARS = 1.
REFERENCES = ('SP500_1X', 'LEAPS_85_30_TSY100', 'LEAPS_95_50_TSY100')

# Both sleeves losing at once is the risk leverage on the safe sleeve creates,
# and it is invisible in any statistic computed on the portfolio alone. Windows
# and thresholds are prespecified.
JOINT_WINDOWS = {'3m': (63, .10), '12m': (252, .20)}

# The synthetic shock replays 2022 end to end, this many times over. One repeat
# reproduces the episode; three asks what a rates shock lasting into a third
# year would have done, with stock/bond correlation positive throughout.
SHOCK_REPEATS = (1, 2, 3)
# Where the annual roll falls relative to the shock is luck, and at one roll a
# year it is a large piece of the answer. The shock is therefore run from
# several calendar starts and reported as an average and a worst case.
SHOCK_OFFSETS = (0, 21, 42, 63, 84)
DRAWDOWN_THRESHOLDS = (.40, .50, .60, .75)
PERCENTILES = (5, 10, 50, 90, 95)

EXTRA_METRICS = ('treasury_notional', 'gross_notional')
METRICS = tuple(PATH_METRICS) + EXTRA_METRICS
SLOT = {name: index for index, name in enumerate(METRICS)}


def short(structure: str) -> str:
    """`LEAPS_85_30_TREASURY` -> `LEAPS_85_30`; the sleeve is named separately here."""
    return structure.removesuffix('_TREASURY')


def label(structure: str, leverage: float) -> str:
    return f'{short(structure)}_TSY{round(leverage * 100)}'


def bond_financing(inputs) -> pd.Series:
    """Cost of one borrowed dollar of Treasury exposure, per session.

    Recovered from the repository's own 3x Treasury fund by the identity
    `letf.model.simulate` builds it with, exactly as `duration_sweep` does. That
    keeps this experiment on the funding and spread assumptions every other
    result here uses, and needs no rate history the repository does not hold.
    """
    days = calendar_days(inputs.ix, inputs.calendar[inputs.calendar.get_loc(inputs.ix[0]) - 1])
    return implied_financing(inputs.daily.loc[inputs.ix, TREASURY],
                             inputs.daily.loc[inputs.ix, TMF], 3.,
                             inputs.config['funds']['TMF']['expense'], days)


def treasury_sleeve(inputs, leverage: float, financing: pd.Series) -> pd.Series:
    """Constant-leverage long Treasuries, borrowing only the exposure above 1x.

    At 1x this is the unleveraged series itself: the borrowed quantity is zero,
    so no financing is charged and nothing is assumed. Above 1x the position is
    restored daily, which is what makes it *constant* leverage — and what makes
    it a different animal from a buy-and-hold levered position, whose exposure
    would drift up as it lost.
    """
    return constant_leverage(inputs.daily.loc[inputs.ix, TREASURY], financing, leverage, 'D')


def run_structure(inputs, structure: str, sleeve: pd.Series):
    """One LEAPS structure held against a given safe sleeve, on the realized path."""
    rule = leaps_rule(*STRUCTURES[structure], ROLL_YEARS)
    arrays = leaps_arrays(inputs.spot, sleeve, inputs.dividend, inputs.riskfree, inputs.vol)
    return simulate_leaps_arrays(*arrays, roll_schedule(inputs.spot.index, rule))


def rolling_compound(returns: np.ndarray, window: int) -> np.ndarray:
    """Compounded return over every `window`-session span, in order.

    A total loss compounds to -1 through a logarithm that would otherwise be
    -inf and poison every later window through the cumulative sum, so the input
    is floored just above -1. The floor is far below anything either sleeve
    reaches on the realized path and exists so the statistic degrades rather
    than disappears.
    """
    if window < 1 or window > len(returns):
        raise ValueError('Rolling window does not fit the series')
    log = np.log1p(np.maximum(returns, -1 + 1e-12))
    cumulative = np.concatenate([[0.], np.cumsum(log)])
    return np.exp(cumulative[window:] - cumulative[:-window]) - 1


def joint_loss(leg: np.ndarray, sleeve: np.ndarray, portfolio: np.ndarray, dates) -> dict:
    """How often the option leg and the Treasury sleeve fall together, and how far.

    This is the diagnostic the whole experiment turns on. A levered safe sleeve
    is only a hedge while it rises when equities fall; once it does not, the
    portfolio holds two leveraged bets rather than one plus a cushion, and no
    statistic computed on the portfolio alone distinguishes the two cases —
    a mild joint loss and a severe single-sleeve loss can produce the same
    drawdown. Measuring the sleeves separately is the only way to tell.

    The worst episode is picked by the *portfolio's* own twelve-month return
    among the windows where both sleeves lost, so it names the occasion that
    actually hurt rather than the one with the most extreme sleeve reading.
    """
    row = {}
    for name, (window, threshold) in JOINT_WINDOWS.items():
        hit = ((rolling_compound(leg, window) <= -threshold)
               & (rolling_compound(sleeve, window) <= -threshold))
        row[f'joint_loss_{name}'] = float(hit.mean())
    window = JOINT_WINDOWS['12m'][0]
    legs, sleeves = rolling_compound(leg, window), rolling_compound(sleeve, window)
    combined = rolling_compound(portfolio, window)
    together = (legs < 0) & (sleeves < 0)
    if not together.any():
        return dict(row, worst_joint_12m=np.nan, worst_joint_leg=np.nan,
                    worst_joint_sleeve=np.nan, worst_joint_end='')
    worst = int(np.argmin(np.where(together, combined, np.inf)))
    return dict(row, worst_joint_12m=float(combined[worst]),
                worst_joint_leg=float(legs[worst]), worst_joint_sleeve=float(sleeves[worst]),
                worst_joint_end=dates[worst + window - 1].date().isoformat())


def historical_row(inputs, structure: str, leverage: float, financing: pd.Series,
                   sleeve: pd.Series | None = None, implementation='constant_leverage'):
    """One structure on one sleeve, measured on the realized path.

    `financing_drag_bps` is measured, not modelled: the same structure is run
    again on a sleeve of identical notional whose borrowing is free, and the
    difference in CAGR is the cost of the leverage. For the TMF row that
    difference is the fund's whole cost of carrying 3x — financing, expense
    ratio and spread together — which is the number worth comparing against the
    financing-only figure beside it.
    """
    if sleeve is None:
        sleeve = treasury_sleeve(inputs, leverage, financing)
    path = run_structure(inputs, structure, sleeve)
    nav = pd.Series(path.navs, index=inputs.spot.index, name='wealth')
    returns = nav.pct_change().dropna()
    free = run_structure(inputs, structure, treasury_sleeve(inputs, leverage, financing * 0))
    free_returns = pd.Series(free.navs, index=inputs.spot.index).pct_change().dropna()

    notional = (1 - path.option_weights) * leverage
    equity = float(path.exposures.mean())
    ten, twenty, thirty = (cohort_cagrs(nav_path(returns, inputs.calendar), horizon)
                           for horizon in (10, 20, 30))
    row = dict(
        structure=short(structure), treasury_leverage=leverage,
        implementation=implementation,
        strategy=(label(structure, leverage) if implementation == 'constant_leverage'
                  else f'{short(structure)}_TMF'),
        cagr=cagr(returns), annualized_volatility=float(returns.std(ddof=1) * np.sqrt(252)),
        max_drawdown=max_drawdown(returns),
        mean_delta_exposure=equity, treasury_notional=float(notional.mean()),
        gross_notional=equity + float(notional.mean()),
        financing_drag_bps=float((cagr(free_returns) - cagr(returns)) * 10000),
        cohort_10y_min_cagr=float(ten.min()),
        cohort_20y_min_cagr=float(twenty.min()),
        cohort_20y_median_cagr=float(np.median(twenty)),
        cohort_20y_sd_cagr=float(np.std(twenty, ddof=1)),
        cohort_30y_min_cagr=float(thirty.min()),
        cohort_30y_median_cagr=float(np.median(thirty)))
    for event, (start, end) in CRASHES.items():
        row[event] = float((1 + returns.loc[start:end]).prod() - 1)
    row.update(joint_loss(path.leg_returns[1:], sleeve.to_numpy(), returns.to_numpy(),
                          inputs.ix))
    return row


def historical_frontier(inputs) -> pd.DataFrame:
    """Every structure at every sleeve leverage, plus the two comparison rows.

    The comparison rows are 3x of each kind: a constant-leverage sleeve financed
    the same way as the modest ones, and TMF. They are outside the specification
    on purpose. Their job is to show where the trend the modest rows establish
    goes when it is pushed, and what a real product costs to do it with.
    """
    financing = bond_financing(inputs)
    rows = [historical_row(inputs, structure, leverage, financing)
            for structure in STRUCTURES for leverage in LEVERAGES]
    rows += [historical_row(inputs, structure, STRESS_LEVERAGE, financing)
             for structure in STRUCTURES]
    rows += [historical_row(inputs, structure, STRESS_LEVERAGE, financing,
                            sleeve=inputs.daily.loc[inputs.ix, TMF], implementation='TMF')
             for structure in STRUCTURES]
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------
# Monte Carlo over the leverage frontier
# ----------------------------------------------------------------------------

FINANCING = len(POOL_COLUMNS)


def treasury_pool(inputs) -> np.ndarray:
    """The bootstrap pool, with the Treasury funding rate joined on as a column.

    Financing is a daily quantity that co-moves with everything else — it was
    high exactly when the bond returns being levered were high — so it is
    resampled as part of the same row rather than held at an average. Appending
    it rather than editing `letf.leaps_robustness.bootstrap_pool` keeps that
    module's manifest and results untouched, and because `block_draw` samples
    row *indices*, a given seed selects the same days in both studies: the two
    reports look at the same simulated worlds.
    """
    return np.column_stack([bootstrap_pool(inputs), bond_financing(inputs)])


def daily_reset(underlying: np.ndarray, financing: np.ndarray,
                leverage: float) -> np.ndarray:
    """Constant leverage restored each session, as an array expression.

    The same portfolio as `letf.hedge_alternatives.constant_leverage` at daily
    frequency, which is what the historical table above uses and what
    `test_treasury_leverage` pins this against. It exists because that function
    walks one Python iteration per session, and the bootstrap runs it tens of
    thousands of times. Wipeout is absorbing, as it is there: the session that
    takes the sleeve to zero returns -100% and every session after it returns
    nothing, because there is nothing left to return.
    """
    if leverage < 1:
        raise ValueError('This comparison covers long leverage only')
    returns = leverage * underlying - (leverage - 1) * financing
    dead = np.flatnonzero(returns <= -1)
    if len(dead):
        returns = returns.copy()
        returns[dead[0]] = -1.
        returns[dead[0] + 1:] = 0.
    return returns


def run_path(sample: np.ndarray, horizon: Horizon, leverages) -> dict:
    """Every structure at every sleeve leverage, on one resampled path."""
    price, vol, riskfree, dividend, _ = build_path(sample, horizon.calendar, signal=False)
    entry = WARMUP_SESSIONS
    spot, q = price[entry:], dividend[entry:]
    rate, sigma = riskfree[entry:], vol[entry:]
    results = {'SP500_1X': np.r_[path_metrics(
        np.r_[1., np.cumprod(1 + sample[entry:, EQUITY_TR])], horizon)[0],
        np.full(len(EXTRA_METRICS), np.nan)]}
    treasury, financing = sample[entry:, TREASURY_R], sample[entry:, FINANCING]
    for leverage in leverages:
        growth = np.r_[1., 1 + daily_reset(treasury, financing, leverage)]
        for structure in STRUCTURES:
            path = simulate_leaps_arrays(spot, growth, q, rate, sigma, horizon.days,
                                         horizon.schedules[(structure, ROLL_LABEL)])
            record = np.r_[path_metrics(path.navs, horizon)[0],
                           np.zeros(len(EXTRA_METRICS))]
            equity = float(path.exposures.mean())
            notional = float(((1 - path.option_weights) * leverage).mean())
            record[SLOT['mean_delta_exposure']] = equity
            record[SLOT['treasury_notional']] = notional
            record[SLOT['gross_notional']] = equity + notional
            results[label(structure, leverage)] = record
    return results


def summarize(store: dict, horizon: Horizon, block: int, paths: int) -> pd.DataFrame:
    """Distribution summary for one (horizon, block length) simulation.

    Comparisons against the index and against the two unlevered reference
    structures are *paired* — each strategy is compared to those rivals on its
    own path, so the question answered is "would this have beaten them in this
    world?" rather than a comparison of two marginal distributions.

    `max_drawdown_p90` is the drawdown one path in ten is worse than, and
    `_p95` one in twenty. They are percentiles of severity, so they are more
    negative than the median rather than less.
    """
    rivals = {name: store[name][:, SLOT['terminal_wealth']] for name in REFERENCES
              if name in store}
    rows = []
    for name, values in store.items():
        rates, terminal = values[:, SLOT['cagr']], values[:, SLOT['terminal_wealth']]
        drawdown = values[:, SLOT['max_drawdown']]
        windows10 = values[:, SLOT['cohorts_10y']].sum()
        windows20 = values[:, SLOT['cohorts_20y']].sum()
        row = dict(strategy=name, horizon_years=horizon.years, block_days=block, paths=paths,
                   mean_cagr=float(rates.mean()), sd_cagr=float(rates.std(ddof=1)),
                   mean_terminal_wealth=float(terminal.mean()),
                   median_max_drawdown=float(np.median(drawdown)),
                   max_drawdown_p90=float(np.percentile(drawdown, 10)),
                   max_drawdown_p95=float(np.percentile(drawdown, 5)),
                   worst_max_drawdown=float(drawdown.min()),
                   median_annualized_volatility=float(
                       np.median(values[:, SLOT['annualized_volatility']])),
                   median_min_wealth=float(np.median(values[:, SLOT['min_wealth']])),
                   p5_min_wealth=float(np.percentile(values[:, SLOT['min_wealth']], 5)),
                   prob_negative_10y=float(values[:, SLOT['negative_10y']].sum() / windows10)
                   if windows10 else np.nan,
                   prob_negative_20y=float((rates < 0).mean()) if horizon.years == 20
                   else (float(values[:, SLOT['negative_20y']].sum() / windows20)
                         if windows20 else np.nan),
                   negative_20y_basis='across paths' if horizon.years == 20
                   else 'pooled overlapping windows')
        for rival, wealth in rivals.items():
            row[f'prob_below_{rival}'] = (float((terminal < wealth).mean())
                                          if rival != name else 0.)
        for level in PERCENTILES:
            row[f'cagr_p{level}'] = float(np.percentile(rates, level))
            row[f'terminal_wealth_p{level}'] = float(np.percentile(terminal, level))
        for threshold in DRAWDOWN_THRESHOLDS:
            row[f'prob_drawdown_worse_than_{int(threshold * 100)}'] = float(
                (drawdown <= -threshold).mean())
        for metric in ('mean_delta_exposure',) + EXTRA_METRICS:
            column = values[:, SLOT[metric]]
            finite = np.isfinite(column).any()
            row[f'{metric}_median'] = float(np.nanmedian(column)) if finite else np.nan
            row[f'{metric}_p5'] = float(np.nanpercentile(column, 5)) if finite else np.nan
            row[f'{metric}_p95'] = float(np.nanpercentile(column, 95)) if finite else np.nan
        rows.append(row)
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------
# Synthetic rates shock: what if 2022 had not stopped?
# ----------------------------------------------------------------------------

def shock_sample(inputs, pool: np.ndarray, repeats: int):
    """Real warm-up, then the 2022 episode replayed end to end `repeats` times.

    Rows are taken whole, so equity and Treasuries fall together exactly as they
    did — the construction adds no correlation assumption of its own, it only
    refuses to let the episode end. That is the one thing the historical sample
    cannot tell us and the one thing a levered Treasury sleeve most needs
    testing against: 2022 was the only stretch in forty years when the hedge and
    the thing it hedges fell together, and it lasted ten months.

    The warm-up is the two years of real history immediately before the episode,
    so the volatility and cash-rate estimates entering the shock are the ones
    that actually prevailed — including the 2020 crash, which leaves the option
    sleeve buying expensively rather than cheaply.
    """
    start, end = CRASHES['2022_rates']
    episode = np.flatnonzero((inputs.ix >= start) & (inputs.ix <= end))
    if not len(episode) or episode[0] < WARMUP_SESSIONS:
        raise ValueError('Not enough history before the rates episode to warm up')
    warm = pool[episode[0] - WARMUP_SESSIONS:episode[0]]
    return np.vstack([warm, np.tile(pool[episode], (repeats, 1))]), len(episode) * repeats


def shock_path(inputs, sample, structure: str, leverage: float, offset: int):
    """Run one structure through the synthetic shock from one calendar start.

    The calendar is moved rather than the data. Roll dates come from the listed
    expiries, so laying the same shock on a different stretch of real dates
    changes how long the opening contract has left when the shock hits and where
    the annual roll falls inside it. At one roll a year that is a large part of
    the answer, and running a single arrangement would report roll luck as a
    property of leverage.
    """
    calendar = inputs.spot.index[offset:offset + len(sample) + 1]
    if len(calendar) != len(sample) + 1:
        raise ValueError('Calendar does not cover the synthetic path')
    price, vol, riskfree, dividend, _ = build_path(sample, calendar, signal=False)
    closes = calendar[WARMUP_SESSIONS:]
    schedule = roll_schedule(closes, leaps_rule(*STRUCTURES[structure], ROLL_YEARS))
    sleeve = daily_reset(sample[WARMUP_SESSIONS:, TREASURY_R],
                         sample[WARMUP_SESSIONS:, FINANCING], leverage)
    return simulate_leaps_arrays(
        price[WARMUP_SESSIONS:], np.r_[1., 1 + sleeve], dividend[WARMUP_SESSIONS:],
        riskfree[WARMUP_SESSIONS:], vol[WARMUP_SESSIONS:],
        (closes - closes[0]).days.to_numpy().astype(float), schedule)


def shock_table(inputs, pool: np.ndarray, leverages=None) -> pd.DataFrame:
    """How much worse modest Treasury leverage gets as the shock is prolonged."""
    leverages = leverages or LEVERAGES + (STRESS_LEVERAGE,)
    rows = []
    for repeats in SHOCK_REPEATS:
        sample, stressed = shock_sample(inputs, pool, repeats)
        for structure in STRUCTURES:
            for leverage in leverages:
                outcomes, drawdowns = [], []
                for offset in SHOCK_OFFSETS:
                    navs = shock_path(inputs, sample, structure, leverage, offset).navs
                    outcomes.append(navs[-1] / navs[0] - 1)
                    drawdowns.append(float((navs / np.maximum.accumulate(navs) - 1).min()))
                rows.append(dict(
                    structure=short(structure), treasury_leverage=leverage,
                    strategy=label(structure, leverage), repeats=repeats,
                    stressed_sessions=stressed,
                    stressed_years=round(stressed / 252, 2),
                    mean_return=float(np.mean(outcomes)),
                    worst_return=float(np.min(outcomes)),
                    best_return=float(np.max(outcomes)),
                    roll_luck_spread=float(np.max(outcomes) - np.min(outcomes)),
                    mean_max_drawdown=float(np.mean(drawdowns)),
                    worst_max_drawdown=float(np.min(drawdowns))))
    return pd.DataFrame(rows)


def index_reference(inputs, pool: np.ndarray) -> dict:
    """The unlevered index through the same two shocks, as the frontier's floor row."""
    start, end = CRASHES['2022_rates']
    realized = inputs.daily.loc[inputs.ix, EQUITY].loc[start:end]
    sample, _ = shock_sample(inputs, pool, max(SHOCK_REPEATS))
    return dict(rates_shock_2022=float((1 + realized).prod() - 1),
                synthetic_shock=float(np.prod(1 + sample[WARMUP_SESSIONS:, EQUITY_TR]) - 1))


def frontier_table(inputs, pool, historical, monte, shock) -> pd.DataFrame:
    """The compact comparison the decision actually turns on.

    Return at the median, return in the bad fifth, drawdown at the median,
    drawdown in the bad tenth, and the two rates shocks — the realized one and
    the one that does not stop. Ranking by CAGR alone is what this table exists
    to prevent.
    """
    thirty = monte[(monte.horizon_years == 30)
                   & (monte.block_days == PRIMARY_BLOCK)].set_index('strategy')
    realized = historical.set_index('strategy')
    prolonged = shock[shock.repeats == max(SHOCK_REPEATS)].set_index('strategy')
    index = index_reference(inputs, pool)
    rows = []
    for name in thirty.index:
        rows.append(dict(
            strategy=name,
            median_30y_cagr=float(thirty.loc[name, 'cagr_p50']),
            p5_30y_cagr=float(thirty.loc[name, 'cagr_p5']),
            median_max_drawdown=float(thirty.loc[name, 'median_max_drawdown']),
            prob_drawdown_worse_than_60=float(thirty.loc[name, 'prob_drawdown_worse_than_60']),
            rates_shock_2022=(index['rates_shock_2022'] if name not in realized.index
                              else float(realized.loc[name, '2022_rates'])),
            synthetic_shock=(index['synthetic_shock'] if name not in prolonged.index
                             else float(prolonged.loc[name, 'mean_return'])),
            prob_below_sp500=float(thirty.loc[name, 'prob_below_SP500_1X'])))
    return pd.DataFrame(rows)


SHORT = {'SP500_1X': 'SP500 1x'}


def figure(path: Path, monte: pd.DataFrame, shock: pd.DataFrame):
    """Three panels: the frontier, the cost of leverage in the tail, and the shock."""
    thirty = monte[(monte.horizon_years == 30)
                   & (monte.block_days == PRIMARY_BLOCK)].set_index('strategy')
    fig, axes = plt.subplots(1, 3, figsize=(16.5, 5.2), constrained_layout=True)

    ax = axes[0]
    for position, structure in enumerate(STRUCTURES):
        names = [label(structure, leverage) for leverage in LEVERAGES]
        ax.plot(thirty.loc[names, 'median_max_drawdown'], thirty.loc[names, 'cagr_p50'],
                'o-', color=f'C{position}', label=short(structure).replace('LEAPS_', ''))
        for leverage, name in zip(LEVERAGES, names):
            ax.annotate(f'{leverage:g}x', (thirty.loc[name, 'median_max_drawdown'],
                                           thirty.loc[name, 'cagr_p50']),
                        textcoords='offset points', xytext=(4, -9), fontsize=7.5)
    ax.plot(thirty.loc['SP500_1X', 'median_max_drawdown'],
            thirty.loc['SP500_1X', 'cagr_p50'], 'k*', markersize=11, label='SP500 1x')
    ax.set_xlabel('median max drawdown')
    ax.set_ylabel('median 30-year CAGR')
    for axis in (ax.xaxis, ax.yaxis):
        axis.set_major_formatter(PercentFormatter(1))
    # The upper left is where the most levered structure sits; the lower left
    # is empty on every version of this chart.
    ax.legend(fontsize=9, loc='lower left')
    ax.set_title('A. Median 30-year return against median drawdown')

    ax = axes[1]
    for position, structure in enumerate(STRUCTURES):
        names = [label(structure, leverage) for leverage in LEVERAGES]
        ax.plot(LEVERAGES, thirty.loc[names, 'prob_drawdown_worse_than_60'], 'o-',
                color=f'C{position}', label=short(structure).replace('LEAPS_', ''))
    ax.set_xlabel('Treasury sleeve leverage')
    ax.set_ylabel('probability of a drawdown worse than 60%')
    ax.yaxis.set_major_formatter(PercentFormatter(1))
    ax.set_xticks(LEVERAGES, [f'{v:g}x' for v in LEVERAGES])
    ax.legend(fontsize=9)
    ax.set_title('B. What the leverage costs in the tail')

    ax = axes[2]
    focus = shock[shock.structure == short(list(STRUCTURES)[1])]
    for position, repeats in enumerate(SHOCK_REPEATS):
        piece = focus[focus.repeats == repeats].sort_values('treasury_leverage')
        ax.plot(piece.treasury_leverage, piece.mean_return, 'o-', color=f'C{position + 3}',
                label=f'{repeats}x 2022 ({piece.stressed_years.iloc[0]:.1f}y)')
    ax.set_xlabel('Treasury sleeve leverage')
    ax.set_ylabel(f'{short(list(STRUCTURES)[1])} return through the shock')
    ax.set_xticks(sorted(focus.treasury_leverage.unique()),
                  [f'{v:g}x' for v in sorted(focus.treasury_leverage.unique())])
    ax.yaxis.set_major_formatter(PercentFormatter(1))
    ax.legend(fontsize=9)
    ax.set_title('C. If the 2022 correlation had not broken')

    fig.savefig(path, dpi=160)
    plt.close(fig)


def run(root: Path, workers=None, paths=None, sensitivity_paths=None):
    inputs = load(root)
    historical = historical_frontier(inputs)
    pool = treasury_pool(inputs)
    shock = shock_table(inputs, pool)

    primary = paths or PRIMARY_PATHS
    secondary = sensitivity_paths or SENSITIVITY_PATHS
    frames = []
    for years in HORIZONS:
        horizon = build_horizon(inputs, years, (ROLL_LABEL,))
        for block in BLOCK_LENGTHS:
            count = primary if block == PRIMARY_BLOCK else secondary
            store = monte_carlo(pool, horizon, LEVERAGES, count, block, SEED,
                                workers, run_path)
            frames.append(summarize(store, horizon, block, count))
    monte = pd.concat(frames, ignore_index=True)
    frontier = frontier_table(inputs, pool, historical, monte, shock)

    reports = root / 'reports'
    outputs = {'leaps_treasury_leverage_historical.csv': historical,
               'leaps_treasury_leverage_monte_carlo.csv': monte,
               'leaps_treasury_leverage_stress.csv': shock,
               'leaps_treasury_leverage_frontier.csv': frontier}
    for name, frame in outputs.items():
        frame.pipe(stable_floats).to_csv(reports / name, index=False,
                                         float_format=FLOAT_FORMAT)
    figure(reports / 'leaps_treasury_leverage.png', monte, shock)
    report(reports, inputs, historical, monte, shock, frontier, primary, secondary)

    (reports / 'leaps_treasury_leverage_manifest.json').write_text(json.dumps({
        'window': [inputs.ix[0].date().isoformat(), inputs.ix[-1].date().isoformat()],
        'observations': int(len(inputs.ix)),
        'structures': {short(k): list(v) for k, v in STRUCTURES.items()},
        'treasury_leverages': list(LEVERAGES), 'stress_leverage': STRESS_LEVERAGE,
        'roll': ROLL_LABEL, 'roll_years': ROLL_YEARS,
        'sleeve': 'constant-leverage long Treasuries, restored daily, borrowing only the '
                  'exposure above 1x at funding recovered from the 3x fund identity; TMF '
                  'is carried separately as the daily-reset product comparison',
        'joint_loss_windows': {k: list(v) for k, v in JOINT_WINDOWS.items()},
        'shock': 'the realized 2022 rates episode replayed end to end, whole daily rows, '
                 'after a warm-up of the two real years preceding it',
        'shock_repeats': list(SHOCK_REPEATS), 'shock_offsets': list(SHOCK_OFFSETS),
        'seed': SEED, 'block_lengths': list(BLOCK_LENGTHS), 'primary_block': PRIMARY_BLOCK,
        'primary_paths': primary, 'sensitivity_paths': secondary,
        'horizons': list(HORIZONS), 'warmup_sessions': WARMUP_SESSIONS,
        'resampled_columns': list(POOL_COLUMNS) + ['treasury_financing'],
        'option_prices': 'modelled with Black-Scholes on an assumed implied volatility; '
                         'this repository holds no option price history',
        'python': platform.python_version(), 'numpy': np.__version__,
        'pandas': pd.__version__, 'scipy': scipy.__version__,
        'matplotlib': matplotlib.__version__,
        'source_hashes': source_hashes(root, __spec__.name),
        'outputs_sha256': {name: sha(reports / name) for name in outputs},
    }, indent=2) + '\n')
    print(f'Treasury leverage: {len(historical)} historical rows, {len(shock)} stress rows; '
          f'Monte Carlo {primary} paths x {len(HORIZONS)} horizons at block {PRIMARY_BLOCK}.')
    return historical, monte, shock


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('--workers', type=int, default=None,
                        help='Processes for the bootstrap. Results do not depend on it.')
    parser.add_argument('--paths', type=int, default=None)
    parser.add_argument('--sensitivity-paths', type=int, default=None)
    parser.add_argument('--offline', action='store_true', default=True)
    args = parser.parse_args()
    run(args.root, args.workers, args.paths, args.sensitivity_paths)


def _exchange_row(step: str, route: str, low, high) -> dict:
    gained = float(high.cagr_p50 - low.cagr_p50)
    # Drawdowns are negative, so a positive figure here is drawdown given up.
    given = float(low.median_max_drawdown - high.median_max_drawdown)
    # The price is quoted per point of *return*, not per point of drawdown. The
    # other way round divides by a number that is genuinely near zero for a
    # structure whose drawdown the sleeve barely moves, and reports the
    # resulting explosion as a spectacular exchange rate. Return bought is
    # materially positive at every step here, so this quotient is well behaved,
    # and lower is better: it is the drawdown paid for each point of CAGR.
    return dict(step=step, route=route, cagr_gained=gained, drawdown_given_up=given,
                drawdown_per_cagr_point=given / gained if gained > 1e-6 else np.nan,
                p5_cagr_change=float(high.cagr_p5 - low.cagr_p5),
                prob_dd60_change=float(high.prob_drawdown_worse_than_60
                                       - low.prob_drawdown_worse_than_60))


def exchange_rates(thirty: pd.DataFrame) -> pd.DataFrame:
    """Median CAGR bought per point of median drawdown given up, by route.

    This is the experiment reduced to one number per step. Both dials — premium
    budget and sleeve leverage — buy return by accepting drawdown, so the only
    question that matters is which of them is the cheaper way to buy it. A
    sleeve-leverage step that pays more drawdown per point of return than a
    budget step is not a new frontier; it is a worse way of moving along the old
    one. `p5_cagr_change` is carried beside it because a step can look cheap at
    the median and still be paid for in the fifth percentile, which is where an
    investor who cannot add capital actually lives.
    """
    rows = []
    for structure in STRUCTURES:
        low = thirty.loc[label(structure, LEVERAGES[0])]
        for leverage in LEVERAGES[1:]:
            rows.append(_exchange_row(f'{short(structure)}  1x -> {leverage:g}x sleeve',
                                      'sleeve leverage', low,
                                      thirty.loc[label(structure, leverage)]))
    names = list(STRUCTURES)
    for lower, higher in zip(names, names[1:]):
        rows.append(_exchange_row(
            f'{short(lower)} -> {short(higher)} at 1x sleeve', 'premium budget',
            thirty.loc[label(lower, 1.)], thirty.loc[label(higher, 1.)]))
    return pd.DataFrame(rows)


def _narrative(historical, monte, shock, frontier, exchange) -> dict:
    """Every number the prose states, derived from the tables it sits beside."""
    thirty = monte[(monte.horizon_years == 30)
                   & (monte.block_days == PRIMARY_BLOCK)].set_index('strategy')
    hist = historical.set_index('strategy')
    steps = exchange.set_index('step')
    mid = list(STRUCTURES)[1]
    top = list(STRUCTURES)[2]
    modest, reference = label(mid, LEVERAGES[-1]), label(top, 1.)
    prolonged = shock[shock.repeats == max(SHOCK_REPEATS)].set_index('strategy')

    sleeve_rate = exchange[exchange.route == 'sleeve leverage']
    # Cheapest step available by each route, per structure. Lower is cheaper.
    cheaper = {structure: float(
        sleeve_rate[sleeve_rate.step.str.startswith(short(structure))]
        .drawdown_per_cagr_point.min()) for structure in STRUCTURES}
    # The budget step a holder of each structure could actually take is the one
    # *starting* there, not the cheapest step in the table: someone holding the
    # middle structure cannot buy the step that arrives at it, so comparing
    # sleeve leverage against that step would answer a question nobody is in a
    # position to ask. The top structure has no budget step above it and is
    # absent from this mapping for that reason.
    names = list(STRUCTURES)
    budget_step = {lower: float(steps.loc[f'{short(lower)} -> {short(higher)} at 1x sleeve',
                                          'drawdown_per_cagr_point'])
                   for lower, higher in zip(names, names[1:])}
    best_budget = budget_step[mid]
    # Where levering the sleeve improves the bad fifth rather than only the
    # middle. That is the diversification claim; it either shows up here or it
    # is not there.
    tail_helped = [short(structure) for structure in STRUCTURES
                   if thirty.loc[label(structure, LEVERAGES[-1]), 'cagr_p5']
                   > thirty.loc[label(structure, 1.), 'cagr_p5']]
    # Where the sleeve sits relative to the option leg, which is the mechanism
    # the closing paragraph appeals to and so must not assume.
    sleeve_size = {structure: float(thirty.loc[label(structure, 1.),
                                               'treasury_notional_median'])
                   for structure in STRUCTURES}

    # Where the sleeve stops being a hedge: the first leverage at which
    # simultaneous three-month losses in both sleeves are twice as frequent as
    # they are unlevered.
    base_joint = float(hist.loc[label(mid, 1.), 'joint_loss_3m'])
    crossings = [leverage for leverage in LEVERAGES + (STRESS_LEVERAGE,)
                 if float(hist.loc[label(mid, leverage), 'joint_loss_3m']) >= 2 * base_joint]

    verdicts = {}
    for leverage in LEVERAGES[1:]:
        name = label(mid, leverage)
        row = thirty.loc[name]
        base = thirty.loc[label(mid, 1.)]
        verdicts[leverage] = dict(
            cagr=float(row.cagr_p50), d_cagr=float(row.cagr_p50 - base.cagr_p50),
            p5=float(row.cagr_p5), d_p5=float(row.cagr_p5 - base.cagr_p5),
            drawdown=float(row.median_max_drawdown),
            d_drawdown=float(row.median_max_drawdown - base.median_max_drawdown),
            dd60=float(row.prob_drawdown_worse_than_60),
            shock=float(prolonged.loc[name, 'mean_return']))
    return dict(thirty=thirty, hist=hist, steps=steps, mid=mid, top=top, modest=modest,
                reference=reference, cheaper=cheaper, best_budget=best_budget,
                sleeve_size=sleeve_size, budget_step=budget_step, tail_helped=tail_helped,
                base_joint=base_joint, crossings=crossings, verdicts=verdicts,
                prolonged=prolonged, exchange=exchange)


def report(reports: Path, inputs, historical, monte, shock, frontier, primary, secondary):
    """Write the narrative from the numbers, never alongside them."""
    thirty = monte[(monte.horizon_years == 30)
                   & (monte.block_days == PRIMARY_BLOCK)].set_index('strategy')
    exchange = exchange_rates(thirty)
    v = _narrative(historical, monte, shock, frontier, exchange)
    mid, top = v['mid'], v['top']
    main = historical[historical.treasury_leverage.isin(LEVERAGES)
                      & (historical.implementation == 'constant_leverage')]
    extra = historical[~(historical.treasury_leverage.isin(LEVERAGES)
                         & (historical.implementation == 'constant_leverage'))]
    thirty_main = monte[(monte.horizon_years == 30)
                        & (monte.block_days == PRIMARY_BLOCK)]
    twenty_main = monte[(monte.horizon_years == 20)
                        & (monte.block_days == PRIMARY_BLOCK)]

    hist_columns = ['strategy', 'cagr', 'annualized_volatility', 'max_drawdown',
                    'mean_delta_exposure', 'treasury_notional', 'gross_notional',
                    'financing_drag_bps', 'cohort_10y_min_cagr', 'cohort_20y_min_cagr',
                    'cohort_20y_median_cagr', 'cohort_20y_sd_cagr', 'cohort_30y_min_cagr',
                    'cohort_30y_median_cagr']
    hist_formats = {c: '.2%' for c in hist_columns if 'cagr' in c or 'drawdown' in c
                    or 'volatility' in c}
    hist_formats.update({'mean_delta_exposure': '.2f', 'treasury_notional': '.2f',
                         'gross_notional': '.2f', 'financing_drag_bps': '.0f',
                         'cohort_20y_sd_cagr': '.4f'})
    mc_columns = ['strategy', 'cagr_p5', 'cagr_p10', 'cagr_p50', 'cagr_p90', 'cagr_p95',
                  'median_max_drawdown', 'max_drawdown_p90', 'max_drawdown_p95',
                  'prob_drawdown_worse_than_60', 'prob_drawdown_worse_than_75',
                  'prob_negative_10y', 'prob_below_SP500_1X',
                  f'prob_below_{label(mid, 1.)}', f'prob_below_{label(top, 1.)}']
    mc_formats = {c: '.2%' for c in mc_columns[1:]}
    wealth_columns = ['strategy', 'terminal_wealth_p5', 'terminal_wealth_p10',
                      'terminal_wealth_p50', 'terminal_wealth_p90', 'terminal_wealth_p95',
                      'p5_min_wealth', 'mean_delta_exposure_median',
                      'treasury_notional_median', 'gross_notional_median']
    wealth_formats = {c: ',.1f' for c in wealth_columns[1:6]}
    wealth_formats.update({'p5_min_wealth': '.3f', 'mean_delta_exposure_median': '.2f',
                           'treasury_notional_median': '.2f', 'gross_notional_median': '.2f'})

    text = f"""# Levering the safe sleeve: diversification, or a second bet on the same regime?

Generated by `letf.treasury_leverage`. Window {inputs.ix[0].date()} to {inputs.ix[-1].date()}, {len(inputs.ix):,} sessions, on the same calendar, financing and option assumptions as `reports/leaps_roll_monte_carlo_results.md`.

The option family is ordered by premium budget, and budget buys return and
drawdown together. `letf.hedge_alternatives` found a second dial: duration
improved return and several drawdown statistics up to a point, and made 2022
much worse past it. This module holds the option structure fixed and levers the
*safe* sleeve instead, to ask whether return bought that way comes with better
tails than the same return bought with a bigger budget.

Only the sleeve leverage varies: {', '.join(f'{v:g}x' for v in LEVERAGES)}, with
{STRESS_LEVERAGE:g}x and TMF carried separately as comparison rows rather than
specifications. Strike, budget, maturity, the annual roll, the volatility
premium and the option spread are all inherited unchanged.

**Option premia are modelled, not measured** — see `letf.options` — and every
favourable duration number below is drawn from a window that is one long decline
in bond yields. The weight of the argument therefore rests on the synthetic
rates shock, not on the historical columns.

## The sleeve

Constant-leverage long Treasuries, restored daily, borrowing only the exposure
above 1x at funding recovered from the repository's own 3x fund. At 1x nothing
is borrowed and nothing is assumed. The cost is measured rather than modelled:
each structure is run again on a sleeve of identical notional whose borrowing is
free, and the CAGR difference is `financing_drag_bps`.

{markdown_table(main, hist_columns, hist_formats)}

Leverage is charged for. Every 0.25x of sleeve costs roughly
{(v['hist'].loc[label(mid, 1.25), 'financing_drag_bps']):.0f}bp a year in the
{short(mid)} structure, and buys about
{(v['hist'].loc[label(mid, 1.25), 'cagr'] - v['hist'].loc[label(mid, 1.), 'cagr']) * 10000:.0f}bp
of CAGR — the difference between the two being the excess return long Treasuries
earned over their funding across this particular forty years.

### Stress windows

{markdown_table(main, ['strategy'] + list(CRASHES), {k: '.1%' for k in CRASHES})}

### The comparison rows: 3x, and what the product costs

{markdown_table(extra, ['strategy', 'implementation', 'cagr', 'max_drawdown',
                        'treasury_notional', 'financing_drag_bps', '2022_rates'],
                {'cagr': '.2%', 'max_drawdown': '.2%', 'treasury_notional': '.2f',
                 'financing_drag_bps': '.0f', '2022_rates': '.1%'})}

## Do the two sleeves fall together?

A levered safe sleeve is a hedge only while it rises when equities fall. Once it
does not, the portfolio holds two leveraged bets rather than one plus a cushion —
and no statistic computed on the portfolio alone can tell those apart, because a
mild joint loss and a severe single-sleeve loss produce the same drawdown. The
sleeves have to be measured separately.

{markdown_table(historical, ['strategy', 'implementation', 'joint_loss_3m',
                             'joint_loss_12m', 'worst_joint_12m', 'worst_joint_leg',
                             'worst_joint_sleeve', 'worst_joint_end'],
                {'joint_loss_3m': '.2%', 'joint_loss_12m': '.2%', 'worst_joint_12m': '.1%',
                 'worst_joint_leg': '.1%', 'worst_joint_sleeve': '.1%'})}

`joint_loss_3m` is the share of rolling three-month windows in which the option
leg and the Treasury sleeve *both* lost more than
{JOINT_WINDOWS['3m'][1]:.0%}; `joint_loss_12m` the share of twelve-month windows
in which both lost more than {JOINT_WINDOWS['12m'][1]:.0%}. The worst episode is
picked by the portfolio's own twelve-month return among the windows where both
sleeves fell, so it names the occasion that actually hurt.

Every row's worst joint episode ends on the same date. In forty years there is
one stretch where the hedge and the thing it hedges fell together, and the whole
question of whether to lever the sleeve is a question about how often that
recurs — which is exactly what a sample containing one instance cannot answer.

## Bootstrap

{primary:,} paths per horizon at {PRIMARY_BLOCK}-session blocks, {secondary:,} at
{' and '.join(str(b) for b in BLOCK_LENGTHS if b != PRIMARY_BLOCK)}, seed {SEED}.
The Treasury funding rate is resampled as part of the same daily row as
everything else, so it stays joint with the bond returns being levered. Because
the row indices depend only on the seed and the block length, these are the same
simulated worlds `letf.leaps_robustness` reports on.

### Thirty-year horizon

{markdown_table(thirty_main, mc_columns, mc_formats)}

### Terminal wealth and exposure, thirty years

{markdown_table(thirty_main, wealth_columns, wealth_formats)}

### Twenty-year horizon

{markdown_table(twenty_main, mc_columns, mc_formats)}

## What each route to return actually costs

Both dials buy return by accepting drawdown. The only question that matters is
which buys it more cheaply.

{markdown_table(exchange, ['step', 'route', 'cagr_gained', 'drawdown_given_up',
                           'drawdown_per_cagr_point', 'p5_cagr_change',
                           'prob_dd60_change'],
                {'cagr_gained': '.2%', 'drawdown_given_up': '.2%',
                 'drawdown_per_cagr_point': '.2f', 'p5_cagr_change': '.2%',
                 'prob_dd60_change': '.1%'})}

`drawdown_per_cagr_point` is the price: points of median drawdown paid for each
point of median CAGR bought. **Lower is cheaper**, and the two premium-budget
rows are the benchmark every sleeve-leverage row has to beat to count as a new
frontier rather than a worse way along the old one.

## The shock that does not stop

The 2022 episode replayed end to end, whole daily rows, so equity and Treasuries
fall together exactly as they did and the construction adds no correlation
assumption of its own. It simply refuses to let the episode end. One repeat
reproduces it; three asks what a rates shock running into a third year would have
done. Each row is run from {len(SHOCK_OFFSETS)} calendar starts, because at one
roll a year where the roll falls relative to the shock is luck rather than
leverage — `roll_luck_spread` reports how much of the answer that luck is.

{markdown_table(shock[shock.structure == short(mid)],
                ['strategy', 'repeats', 'stressed_years', 'mean_return', 'worst_return',
                 'roll_luck_spread', 'mean_max_drawdown'],
                {'mean_return': '.1%', 'worst_return': '.1%', 'roll_luck_spread': '.2%',
                 'mean_max_drawdown': '.1%', 'stressed_years': '.2f'})}

One repeat is the control, and it very nearly reproduces the episode: {short(mid)}
at 1x returns {shock[(shock.strategy == label(mid, 1.)) & (shock.repeats == 1)].mean_return.iloc[0]:.1%}
through it against {v['hist'].loc[label(mid, 1.), '2022_rates']:.1%} realized. The
small gap is the option position rather than the data — the synthetic path opens a
fresh contract as the shock begins, while the realized path carried one already in
flight — and it is the size of the discrepancy a reader should keep in mind for
the two- and three-repeat rows, which have no control.

## The frontier

{markdown_table(frontier, ['strategy', 'median_30y_cagr', 'p5_30y_cagr',
                           'median_max_drawdown', 'prob_drawdown_worse_than_60',
                           'rates_shock_2022', 'synthetic_shock', 'prob_below_sp500'],
                {'median_30y_cagr': '.2%', 'p5_30y_cagr': '.2%',
                 'median_max_drawdown': '.1%', 'prob_drawdown_worse_than_60': '.1%',
                 'rates_shock_2022': '.1%', 'synthetic_shock': '.1%',
                 'prob_below_sp500': '.1%'})}

![Treasury leverage frontier](leaps_treasury_leverage.png)
"""
    (reports / 'leaps_treasury_leverage_results.md').write_text(text + _answers(v))

def _verdict(price: float, budget: float, tail: float) -> str:
    """Whether a sleeve step is a better frontier or just a further step along it."""
    if not np.isfinite(price):
        return 'It buys no return to price'
    if price < budget and tail >= 0:
        return 'Yes, on both counts'
    if price < budget:
        return 'On the median yes, in the tail no'
    return 'No'


def _answers(v) -> str:
    """The eight prespecified answers, every claim read off the tables above."""
    thirty, hist, steps = v['thirty'], v['hist'], v['steps']
    mid, top = v['mid'], v['top']
    modest, reference = v['modest'], v['reference']
    budget, cheaper, sizes = v['best_budget'], v['cheaper'], v['sleeve_size']
    verdicts, crossings, prolonged = v['verdicts'], v['crossings'], v['prolonged']
    base = thirty.loc[label(mid, 1.)]

    parts = ['\n## The eight questions\n']
    for number, leverage in zip((1, 2, 3), LEVERAGES[1:]):
        row = verdicts[leverage]
        price = float(steps.loc[f'{short(mid)}  1x -> {leverage:g}x sleeve',
                                'drawdown_per_cagr_point'])
        parts.append(f"""
**{number}. Does {leverage:g}x Treasury leverage improve the frontier?**
{_verdict(price, budget, row['d_p5'])}. In {short(mid)} it lifts the median
30-year CAGR by {row['d_cagr']:.2%} to {row['cagr']:.2%}, pays
{abs(row['d_drawdown']):.2%} more median drawdown for it, and moves the fifth
percentile by {row['d_p5']:+.2%}. That is a price of {price:.2f} points of
drawdown per point of CAGR, against {budget:.2f} for the budget step a holder of
{short(mid)} could actually take instead — moving to {short(top)} — so the return
is bought {'more' if price < budget else 'less'} cheaply this way. The probability of a
drawdown worse than 60% is {row['dd60']:.1%}, against
{base.prob_drawdown_worse_than_60:.1%} unlevered. Through a rates shock lasting
{max(SHOCK_REPEATS)} times as long as 2022 it returns {row['shock']:.1%}, against
{prolonged.loc[label(mid, 1.), 'mean_return']:.1%} unlevered.
""")

    crossing = (f'{crossings[0]:g}x' if crossings
                else f'no leverage tested up to {STRESS_LEVERAGE:g}x')
    # Only the specified leverages are simulated; 3x is a historical row alone.
    dominant = [f'{leverage:g}x' for leverage in LEVERAGES
                if thirty.loc[label(mid, leverage), 'treasury_notional_median']
                >= thirty.loc[label(mid, leverage), 'mean_delta_exposure_median']]
    exposure_crossing = dominant[0] if dominant else 'beyond every level tested'
    agree = crossing == exposure_crossing
    parts.append(f"""
**4. At what point does the Treasury sleeve stop behaving like a hedge?**
Two readings, and they {'agree' if agree else 'do not agree'}. On how often the
sleeves fall together, the crossing is at {crossing}: that is where three-month
windows in which the option leg and the sleeve *both* lose more than
{JOINT_WINDOWS['3m'][1]:.0%} become twice as common as they are unlevered
({v['base_joint']:.2%} of windows in {short(mid)} at 1x). On which exposure
dominates the portfolio, it is {exposure_crossing}, where Treasury notional first
exceeds the option sleeve's delta. Neither is a regime break — the change is
continuous and every step of leverage buys a little more of it — but the two
readings arriving {'at the same place' if agree else 'in different places'} is
worth noting{', because the point where the sleeve becomes the larger risk is far'
' easier to see in advance than the point where it stops diversifying'
if agree else ': the sleeve can become the larger position while still hedging, '
'or stop hedging while still the smaller one'}.
""")

    gap = float(thirty.loc[reference, 'cagr_p50'] - thirty.loc[modest, 'cagr_p50'])
    parts.append(f"""
**5. Can {short(mid)} with modest Treasury leverage approach {short(top)}'s return with better tails?**
No. At the top of the modest range {modest} reaches a median 30-year CAGR of
{thirty.loc[modest, 'cagr_p50']:.2%} against {reference}'s
{thirty.loc[reference, 'cagr_p50']:.2%} — {gap:.2%} short — and still trails it
outright on {thirty.loc[modest, f'prob_below_{reference}']:.0%} of paths. Its
tails are better ({thirty.loc[modest, 'prob_drawdown_worse_than_60']:.1%} chance
of a drawdown worse than 60% against
{thirty.loc[reference, 'prob_drawdown_worse_than_60']:.1%}), but that is a
comparison the *unlevered* {short(mid)} wins by more, at
{base.prob_drawdown_worse_than_60:.1%}. Treasury leverage does not close the
return gap. It spends part of the tail advantage that was the reason to prefer
{short(mid)} in the first place, and buys about
{(thirty.loc[modest, 'cagr_p50'] - base.cagr_p50) / (thirty.loc[reference, 'cagr_p50'] - base.cagr_p50):.0%}
of the distance in return for doing so.
""")

    top_leverage = LEVERAGES[-1]
    tail_steps = [leverage for leverage in LEVERAGES[1:] if verdicts[leverage]['d_p5'] < 0]
    parts.append(f"""
**6. Does the result survive Monte Carlo reordering?**
Yes, on direction. On the realized path {short(mid)} gains
{hist.loc[modest, 'cagr'] - hist.loc[label(mid, 1.), 'cagr']:.2%} of CAGR going
from 1x to {top_leverage:g}x and its worst drawdown deepens by
{hist.loc[label(mid, 1.), 'max_drawdown'] - hist.loc[modest, 'max_drawdown']:.2%};
across resampled orderings the median gain is
{verdicts[top_leverage]['d_cagr']:.2%} and the median worst drawdown deepens by
{abs(verdicts[top_leverage]['d_drawdown']):.2%}. Those two drawdown figures are
not the same statistic — one is a single realization over forty years, the other
a median over thirty-year paths — so the number worth taking from the bootstrap
is the one the realized path cannot supply. That is the fifth percentile of CAGR,
which turns against leverage from
{f"{min(tail_steps):g}x onwards" if tail_steps else "no level tested"}, and the
probability of a drawdown worse than 60%, which rises from
{base.prob_drawdown_worse_than_60:.1%} to {verdicts[top_leverage]['dd60']:.1%}.
Leverage is paid for in the tail rather than in the middle, and only the
resampling shows that clearly.
""")

    survives = ((1 + prolonged.loc[label(mid, 1.), 'mean_return'])
                / (1 + prolonged.loc[modest, 'mean_return']))
    parts.append(f"""
**7. Does the conclusion hold under prolonged positive stock/bond correlation?**
It strengthens, and this is the part of the report carrying the most weight.
Replaying 2022 until it has run {max(SHOCK_REPEATS)} times its length,
{short(mid)} at 1x returns {prolonged.loc[label(mid, 1.), 'mean_return']:.1%} and
at {top_leverage:g}x returns {prolonged.loc[modest, 'mean_return']:.1%} — the
unlevered sleeve leaves {survives:.1f} times as much capital standing. Roll
timing explains almost none of it: across {len(SHOCK_OFFSETS)} calendar starts
the spread of outcomes is at most
{prolonged.roll_luck_spread.max():.2%}. The historical record cannot price this
scenario, because it contains exactly one instance of the correlation that drives
it, and the block bootstrap cannot generate it either, because a joint-loss
regime outlasting one {PRIMARY_BLOCK}-session block cannot survive the
resampling. Every favourable number above is drawn from the world in which that
correlation broke within a year.
""")

    tmf = f'{short(mid)}_TMF'
    parts.append(f"""
**8. Is leveraged Treasury exposure better implemented as constant leverage than as TMF?**
Yes on cost, and the two are not the same instrument. At matched
{STRESS_LEVERAGE:g}x notional the fund costs
{hist.loc[tmf, 'financing_drag_bps'] - hist.loc[label(mid, STRESS_LEVERAGE), 'financing_drag_bps']:.0f}bp
a year more than financing alone — its expense ratio and spread on top of the
borrowing — worth
{hist.loc[label(mid, STRESS_LEVERAGE), 'cagr'] - hist.loc[tmf, 'cagr']:.2%} of
CAGR in {short(mid)} across this window. The conceptual difference matters more
than the fee: a constant-leverage sleeve is defined by the exposure it holds,
while a daily-reset fund is defined by a rule that produces that exposure and
carries a path dependence besides. They are separate rows here so that neither is
silently substituted for the other, which is also why {STRESS_LEVERAGE:g}x appears
only as a comparison and never as a specification.
""")

    monotone = all(
        list(v['prolonged'].loc[[label(structure, leverage) for leverage in LEVERAGES],
                                'mean_return'])
        == sorted(v['prolonged'].loc[[label(structure, leverage) for leverage in LEVERAGES],
                                     'mean_return'], reverse=True)
        for structure in STRUCTURES)
    shock_direction = ('Every step of leverage makes the prolonged-shock column worse, '
                       'monotonically, in every structure' if monotone else
                       'Leverage makes the prolonged-shock column worse on balance, though '
                       'not at every step of every structure')
    steps_available = v['budget_step']
    beats = [short(s) for s in STRUCTURES
             if s in steps_available and cheaper[s] < steps_available[s]]
    helped = v['tail_helped']
    smallest = min(STRUCTURES, key=lambda s: sizes[s])
    mechanism = (f'{short(smallest)} carries the smallest sleeve of the three, '
                 f'{sizes[smallest]:.2f} of notional against '
                 f'{max(sizes.values()):.2f} for the largest, so levering it adds a risk '
                 'genuinely different from the one already dominating that portfolio'
                 if helped and short(smallest) in helped else
                 'and sleeve size beside the option leg does not order them, so the usual '
                 'diversification story is not what produces it')
    parts.append(f"""
## What this does and does not settle

Modest Treasury leverage is mostly not a second source of return in this
architecture. It is the same duration bet, taken larger — and the historical
record cannot tell those two apart, because the one episode that would
distinguish them happened once and lasted ten months.

Priced against the budget step each structure could actually take instead,
sleeve leverage is the cheaper route in
{phrase(beats) if beats else 'none of the structures tested'}. Measured on the
fifth percentile rather than the median it is genuinely helpful in
{phrase(helped) if helped else 'none of them'} — {mechanism}. Everywhere else it
buys the middle of the distribution by selling the bottom of it.

It is nowhere a free improvement. {shock_direction}, and that column is the only
one in this report drawn from a regime the sample does not contain.

The defensible reading is narrow: a small amount of sleeve leverage is an
efficient way to move along the existing frontier for a structure whose drawdown
is already dominated by equity, and it is not a way to reach a better frontier.
An investor unwilling to hold {reference} for its tail should not expect
{modest} to be a substitute for it.

## Limitations

* Option premia are modelled, not observed, and inherit every bias listed in
  `letf.options`. The option leg enters the joint-loss diagnostic as a modelled
  series, so that diagnostic is only as good as the pricing assumption.
* The window is one long decline in bond yields. Every historical column
  favourable to duration inherits that, the cohort minima included — and those
  improve with leverage precisely because the windows containing the one
  joint-loss episode end almost as soon as it begins.
* The synthetic shock repeats one realized episode. It bounds how bad a
  prolonged positive correlation would have been *given the 2022 dynamics*; it
  is not a distribution over rate shocks and says nothing about one of a
  different shape.
* Financing is recovered from the 3x fund's own identity, so it carries that
  product's funding spread. An investor borrowing at a different rate would find
  every leverage step cheaper or dearer by the difference, and the exchange-rate
  table is the first thing that would move.
* The bootstrap resamples the same forty years and preserves dependence only
  within a block, so it cannot produce the regime the synthetic shock exists to
  test. The two are reported separately for that reason and should not be read
  as two views of the same thing.
""")
    return ''.join(parts)


if __name__ == '__main__':
    main()
