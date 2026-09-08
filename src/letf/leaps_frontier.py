"""Which regions of the LEAPS frontier survive being doubted?

Earlier modules established three things separately: that fixed-budget,
moderately in-the-money LEAPS held against a Treasury sleeve behave like
capital-efficient equity exposure (`letf.hedge_alternatives`), that the annual
roll and the ranking of a handful of structures survive block resampling
(`letf.leaps_robustness`), and that a faster-growing underlying reaches
comparable growth on less option capital (`letf.nasdaq_leaps`). Each looked at
three or four hand-picked cells.

This module asks the question those three leave open. **Where is the frontier,
and which parts of it are still there after the assumptions that produced it are
moved?** A regular lattice replaces the hand-picked cells — fifteen S&P
structures across three strikes and five premium budgets, plus the established
conservative anchor, and twenty-eight Nasdaq structures across four strikes and
seven budgets — and every one of them is carried through the same primary
bootstrap and the same seven sensitivities. A cell that is efficient in the
primary specification and stops being efficient when the option price moves
three volatility points is not a strategy; it is an artifact of a number nobody
has measured.

**Two kinds of uncertainty, and they are not the same size.** *Strategy
uncertainty* — strike, premium budget, roll schedule, the order history
happens in, how much duration the safe sleeve carries — is what the lattice and
the bootstrap measure, and this module measures it thoroughly. *Model
uncertainty* is the price of the options themselves, and this repository holds
no long-dated option price history, no skew surface and no bid/ask record for
either index. The volatility a structure is charged is a trailing realized
proxy plus a flat loading. Every structure below therefore carries its own
measured CAGR-per-volatility-point, and where two candidates differ by less than
one volatility point of it the difference is reported as economically
unresolved rather than as a ranking. That is not a hedge: for the high-budget
structures the pricing sensitivity is several times the tolerance any Monte
Carlo comparison here can resolve, and a reader who ranks those cells on the
simulated medians is reading the assumption, not the strategy.

**What the bootstrap is not.** It resamples the same forty-odd years in blocks.
It cannot produce a crash worse than the ones in the record, a bond regime
unlike the one observed, or a Nasdaq that did not out-grow the S&P — that growth
gap is an *input* to every simulated path, not a finding of any of them. Its
only claim is comparative.

**Early Nasdaq history is a price-only proxy.** Through 1999-03-04 the
Nasdaq-100 total-return series is grossed up from price with an assumed zero
dividend yield. The dot-com bust is outside that era and is deliberately kept:
it is the episode the Nasdaq case has to survive.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
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
from scipy.stats import spearmanr

from .cohorts import cohort_cagrs, cohort_frame, nav_path
from .hedge_alternatives import (CRASHES, EQUITY, IV_PREMIUM, MATURITY_YEARS,
                                 OPTION_SPREAD_BPS, TREASURY, cagr, markdown_table,
                                 max_drawdown)
from .leaps_robustness import (BENCHMARK_INTERVAL, EQUITY_TR, Horizon,
                               PRIMARY_BLOCK, ROLL_INTERVALS, SEED, SLOT as BASE_SLOT,
                               METRICS as BASE_METRICS, TREASURY_R, WARMUP_SESSIONS,
                               build_horizon, build_path, leaps_rule, load, monte_carlo,
                               path_metrics, phrase)
from .nasdaq_leaps import NASDAQ, PROXY_THROUGH, underlying_inputs
from .options import (implied_volatility_proxy, leaps_arrays, roll_schedule,
                      simulate_leaps_arrays)
from .provenance import FLOAT_FORMAT, sha, source_hashes, stable_floats
from .treasury_leverage import FINANCING, daily_reset, treasury_pool

# ----------------------------------------------------------------------------
# The prespecified lattice
# ----------------------------------------------------------------------------
#
# Regular grids, fixed before any path was drawn, and deliberately coarse: no
# intermediate cell is added however well a neighbour performs, because the
# whole point of a lattice is that a good cell has to be surrounded by good
# cells to count. The Nasdaq grid is wider not because more searching is wanted
# there but because the hypothesis under test is that the Nasdaq needs
# materially *less* option capital, which cannot be checked on a grid that does
# not go low enough to find out.
SP_MONEYNESS = (.85, .90, .95)
SP_BUDGETS = (.30, .35, .40, .45, .50)
NDX_MONEYNESS = (.80, .85, .90, .95)
NDX_BUDGETS = (.20, .25, .30, .35, .40, .45, .50)
# The conservative anchor from the earlier work, kept so the lattice has a
# reference point below its own floor.
ANCHOR = ('SPX_80_25', (.80, .25))

ROLL_LABEL, ROLL_YEARS = BENCHMARK_INTERVAL, 1.
BASE_LEVERAGE = 1.


def _cells(prefix: str, moneyness, budgets) -> dict:
    return {f'{prefix}_{round(m * 100)}_{round(b * 100)}': (m, b)
            for m in moneyness for b in budgets}


SP_STRUCTURES = {ANCHOR[0]: ANCHOR[1], **_cells('SPX', SP_MONEYNESS, SP_BUDGETS)}
NDX_STRUCTURES = _cells('NDX', NDX_MONEYNESS, NDX_BUDGETS)
STRUCTURES = {**SP_STRUCTURES, **NDX_STRUCTURES}
UNDERLYING = {**{name: EQUITY for name in SP_STRUCTURES},
              **{name: NASDAQ for name in NDX_STRUCTURES}}
FAMILY = {**{name: 'SP500 LEAPS' for name in SP_STRUCTURES},
          **{name: 'NDX LEAPS' for name in NDX_STRUCTURES}}
INDEX_ROWS = (EQUITY, NASDAQ)

# Cells this repository has already published, under their older names. They are
# recomputed here only to be checked against the committed file: identical rule,
# identical window, identical inputs, so any difference would be a bug in one of
# the two modules rather than a finding.
COMMITTED = {'SPX_80_25': 'LEAPS_80_25_TREASURY',
             'SPX_85_30': 'LEAPS_85_30_TREASURY',
             'SPX_95_50': 'LEAPS_95_50_TREASURY',
             'NDX_80_20': 'NDX_LEAPS_80_20_TREASURY',
             'NDX_80_25': 'NDX_LEAPS_80_25_TREASURY',
             'NDX_85_25': 'NDX_LEAPS_85_25_TREASURY',
             'NDX_85_30': 'NDX_LEAPS_85_30_TREASURY'}
COMMITTED_FILE = 'nasdaq_leaps_historical.csv'
COMMITTED_COLUMNS = ('cagr', 'max_drawdown', 'annualized_volatility',
                     'mean_delta_exposure', 'mean_option_weight',
                     'cohort_30y_median_cagr') + tuple(CRASHES)
COMMITTED_TOLERANCE = 1e-8

# ----------------------------------------------------------------------------
# What the run costs and what it is allowed to resolve
# ----------------------------------------------------------------------------

MC_PATHS = 15000
MC_HORIZONS = (10, 20, 30)
# Every frontier and every classification is read off this horizon. The shorter
# ones are reported because a thirty-year median says nothing about the decade
# an investor actually has to sit through, but they are not what a candidate is
# classified on.
FRONTIER_HORIZON = 30
SENSITIVITY_PATHS = 3000
ROLL_PATHS = 2000

IV_PREMIUMS = (.00, IV_PREMIUM, .06)
ROLL_LABELS = ('9m', BENCHMARK_INTERVAL, '18m')
LEVERAGES = (BASE_LEVERAGE, 1.25)
BLOCK_LENGTHS = (21, PRIMARY_BLOCK, 126)

DRAWDOWN_THRESHOLDS = (.40, .50, .60, .75)
PERCENTILES = (5, 10, 50, 90, 95)

# Differences smaller than these are not differences. They are prespecified, and
# they are deliberately generous relative to the Monte Carlo standard error at
# these path counts, because the thing being guarded against is not sampling
# noise but the temptation to read a lattice as a ranking.
CAGR_TOLERANCE = .0025
DRAWDOWN_TOLERANCE = .02
PROBABILITY_TOLERANCE = .02
# A candidate is "robust" if it holds the frontier in this many of the seven
# prespecified sensitivities. Five of seven is a clear majority and is fixed
# here rather than chosen once the counts were visible.
ROBUST_HOLDS = 5

# Approximate return tiers for the cross-underlying comparison. Nothing is
# optimized to hit them; the nearest candidate in each family is reported along
# with how far away it actually is.
RETURN_TIERS = (.13, .15, .17, .19, .21)
TIER_BAND = .005
# How close a Nasdaq structure has to come to an S&P target before the phrase
# "approaches" is allowed. One CAGR point, prespecified.
APPROACH_BAND = .01

EXTRA_METRICS = ('mean_option_weight', 'treasury_notional', 'gross_notional')
METRICS = tuple(BASE_METRICS) + EXTRA_METRICS
SLOT = {name: index for index, name in enumerate(METRICS)}
BASE_WIDTH = len(BASE_METRICS)


# ----------------------------------------------------------------------------
# Historical analysis
# ----------------------------------------------------------------------------

def structure_path(inputs, market, name: str, premium=None, roll=ROLL_LABEL,
                   sleeve=None):
    """One structure on the realized path, at a stated option price and roll."""
    spot, dividend, riskfree, vol = market[UNDERLYING[name]]
    if premium is not None and premium != IV_PREMIUM:
        vol = implied_volatility_proxy(spot.pct_change().fillna(0.), premium,
                                       MATURITY_YEARS)
    if sleeve is None:
        sleeve = inputs.daily.loc[inputs.ix, TREASURY]
    rule = leaps_rule(*STRUCTURES[name], ROLL_INTERVALS[roll])
    arrays = leaps_arrays(spot, sleeve, dividend, riskfree, vol)
    return simulate_leaps_arrays(*arrays, roll_schedule(spot.index, rule))


def describe(name, returns, inputs, path=None) -> dict:
    """The realized-path record §3 asks for, for one strategy."""
    moneyness, budget = STRUCTURES.get(name, (np.nan, np.nan))
    wealth = nav_path(returns, inputs.calendar)
    ten, twenty, thirty = (cohort_cagrs(wealth, horizon) for horizon in (10, 20, 30))
    notional = ((1 - path.option_weights) * BASE_LEVERAGE if path is not None
                else np.array([np.nan]))
    exposure = float(path.exposures.mean()) if path is not None else 1.
    row = dict(strategy=name, family=FAMILY.get(name, 'index'),
               underlying=UNDERLYING.get(name, name),
               moneyness=moneyness, premium_budget=budget,
               cagr=cagr(returns), annualized_volatility=float(returns.std(ddof=1)
                                                               * np.sqrt(252)),
               max_drawdown=max_drawdown(returns),
               terminal_multiple=float((1 + returns).prod()),
               mean_delta_exposure=exposure,
               mean_option_weight=float(path.option_weights.mean())
               if path is not None else np.nan,
               treasury_notional=float(notional.mean()),
               gross_notional=exposure + float(notional.mean())
               if path is not None else exposure,
               cohort_10y_min_cagr=float(ten.min()),
               cohort_20y_min_cagr=float(twenty.min()),
               cohort_20y_median_cagr=float(np.median(twenty)),
               cohort_30y_min_cagr=float(thirty.min()),
               cohort_30y_median_cagr=float(np.median(thirty)))
    for event, (start, end) in CRASHES.items():
        row[event] = float((1 + returns.loc[start:end]).prod() - 1)
    # Every thirty-year window in this sample opens before the Nasdaq splice, so
    # a thirty-year Nasdaq statistic is partly a statement about proxy data.
    # Twenty-year windows entering after it are not, and are reported beside it.
    modern = cohort_frame(wealth, 20)
    modern = modern[modern.entry_close.astype(str) > PROXY_THROUGH]
    row['cohort_20y_min_cagr_post_proxy'] = (float(modern.cagr.min()) if len(modern)
                                             else np.nan)
    row['cohort_20y_entries_post_proxy'] = int(len(modern))
    return row


def model_uncertainty(inputs, market, name: str, shift=.01) -> dict:
    """How far one volatility point moves this structure's realized CAGR.

    The unit of doubt for an option strategy is a volatility point, not a basis
    point of return, because the volatility is the input nobody here has
    observed. This is measured per structure rather than assumed to be common:
    it is several times larger for a high-budget structure than for a
    conservative one, which is exactly why a single tolerance applied across the
    lattice would flatter the expensive end.
    """
    rates = {}
    for premium in (IV_PREMIUM - shift, IV_PREMIUM, IV_PREMIUM + shift):
        path = structure_path(inputs, market, name, premium=premium)
        spot = market[UNDERLYING[name]][0]
        rates[premium] = cagr(pd.Series(path.navs, index=spot.index).pct_change().dropna())
    return dict(cagr_one_point_cheaper=rates[IV_PREMIUM - shift],
                cagr_one_point_dearer=rates[IV_PREMIUM + shift],
                cagr_per_volatility_point=float((rates[IV_PREMIUM - shift]
                                                 - rates[IV_PREMIUM + shift]) / 2))


def historical_table(inputs, market) -> pd.DataFrame:
    """Every lattice cell and both indices, on the one realized path."""
    rows = [describe(name, inputs.daily.loc[inputs.ix, name], inputs)
            for name in INDEX_ROWS]
    for name in STRUCTURES:
        path = structure_path(inputs, market, name)
        spot = market[UNDERLYING[name]][0]
        returns = pd.Series(path.navs, index=spot.index).pct_change().dropna()
        rows.append({**describe(name, returns, inputs, path=path),
                     **model_uncertainty(inputs, market, name)})
    return pd.DataFrame(rows)


def committed_check(reports: Path, historical: pd.DataFrame) -> pd.DataFrame:
    """The seven cells this repository has already published, re-derived and compared.

    Same rule, same window, same inputs, so the recomputed numbers must equal the
    committed ones exactly. This is the reuse the brief asks for: the overlapping
    Monte Carlo rows cannot be reused, because they were run at a different path
    count and putting a 2,000-path row in a table of 15,000-path rows would make
    the table say something it does not mean — but the realized-path rows are
    deterministic, and agreeing with them is worth more than copying them.
    """
    committed = pd.read_csv(reports / COMMITTED_FILE).set_index('strategy')
    here = historical.set_index('strategy')
    rows = []
    for name, published in COMMITTED.items():
        diffs = {}
        for column in COMMITTED_COLUMNS:
            mine = float(here.loc[name, column])
            theirs = float(committed.loc[published, column])
            diffs[column] = abs(mine - theirs) / max(abs(theirs), 1e-3)
        worst = max(diffs, key=diffs.get)
        rows.append(dict(strategy=name, published_as=published,
                         columns_compared=len(COMMITTED_COLUMNS),
                         worst_column=worst, worst_relative_difference=diffs[worst]))
    frame = pd.DataFrame(rows)
    # The committed file stores ten significant figures, so agreement can only be
    # asserted to the precision it was written at. This is the tolerance
    # `scripts/compare_results.py` holds every other committed result to.
    if frame.worst_relative_difference.max() > COMMITTED_TOLERANCE:
        raise ValueError('Recomputed lattice cells disagree with the committed results')
    return frame


# ----------------------------------------------------------------------------
# Shared-path Monte Carlo
# ----------------------------------------------------------------------------

NDX_PRICE, NDX_TOTAL, NDX_YIELD = (FINANCING + 1, FINANCING + 2, FINANCING + 3)
POOL_WIDTH = FINANCING + 4


def frontier_pool(inputs, market) -> np.ndarray:
    """One pool serving every arm: the base rows, the funding rate, the Nasdaq.

    Built by composing the two pools already in the repository rather than by
    writing a third. Because `letf.leaps_robustness.block_draw` samples row
    *indices*, a shared seed and block length select the same days here as in
    every other study that uses them: the reports look at the same simulated
    worlds, and a difference between them is a difference between strategies.
    """
    spot = market[NASDAQ][0]
    price_return = spot.pct_change().dropna()
    if not price_return.index.equals(inputs.ix):
        raise ValueError('Nasdaq price returns are not aligned to the window')
    pool = np.column_stack([treasury_pool(inputs), price_return,
                            inputs.daily.loc[inputs.ix, NASDAQ],
                            market[NASDAQ][1].loc[inputs.ix]])
    if pool.shape[1] != POOL_WIDTH or not np.isfinite(pool).all():
        raise ValueError('Frontier pool is malformed')
    return pool


@dataclass(frozen=True)
class Arm:
    """One dial setting evaluated on the paths its case draws.

    Every arm of a case sees the *same* resampled worlds, so a sensitivity is a
    paired within-world difference rather than two separate experiments compared
    across sampling noise. Only the arms of a block-length case cannot be paired,
    because the block length is what selects the days.
    """
    name: str
    iv_premium: float = IV_PREMIUM
    roll: str = ROLL_LABEL
    leverage: float = BASE_LEVERAGE


@dataclass(frozen=True)
class Case:
    """One Monte Carlo run: a block length, a path count, and the arms it scores."""
    name: str
    block: int
    paths: int
    arms: tuple
    dial: str


BASELINE = Arm('baseline')
CASES = (
    Case('iv_premium', PRIMARY_BLOCK, SENSITIVITY_PATHS, dial='iv_premium',
         arms=tuple(Arm(f'iv_{round(p * 100)}', iv_premium=p) for p in IV_PREMIUMS)),
    Case('block_21', 21, SENSITIVITY_PATHS, arms=(BASELINE,), dial='block'),
    Case('block_126', 126, SENSITIVITY_PATHS, arms=(BASELINE,), dial='block'),
    Case('block_63_matched', PRIMARY_BLOCK, SENSITIVITY_PATHS, arms=(BASELINE,),
         dial='block'),
    Case('treasury_leverage', PRIMARY_BLOCK, SENSITIVITY_PATHS, dial='treasury_leverage',
         arms=tuple(Arm(f'tsy_{round(x * 100)}', leverage=x) for x in LEVERAGES)),
    Case('roll_interval', PRIMARY_BLOCK, ROLL_PATHS, dial='roll',
         arms=tuple(Arm(f'roll_{label}', roll=label) for label in ROLL_LABELS)),
)
# The seven specifications a candidate has to hold the frontier in to be called
# robust. The matched-baseline arms are not among them: their job is to give the
# sensitivities something to be measured against at the same path count.
SENSITIVITY_ARMS = (('iv_premium', 'iv_0'), ('iv_premium', 'iv_6'),
                    ('block_21', 'baseline'), ('block_126', 'baseline'),
                    ('roll_interval', 'roll_9m'), ('roll_interval', 'roll_18m'),
                    ('treasury_leverage', 'tsy_125'))


def key(arm: str, strategy: str) -> str:
    return f'{arm}|{strategy}'


def run_path(sample: np.ndarray, horizon: Horizon, arms) -> dict:
    """Every arm and every lattice cell on one resampled path.

    Both underlyings are rebuilt inside the path — volatility, the funding rate
    and the dividend level recomputed from the resampled returns rather than
    carried over from the realized calendar, where they would know about crashes
    this path never had. The Nasdaq's excess growth and its excess volatility
    arrive together because whole daily rows are drawn.
    """
    entry = WARMUP_SESSIONS
    premiums = sorted({arm.iv_premium for arm in arms})
    built = {}
    for index, price_col, yield_col in ((EQUITY, None, None),
                                        (NASDAQ, NDX_PRICE, NDX_YIELD)):
        for premium in premiums:
            price, vol, riskfree, dividend, _ = build_path(
                sample, horizon.calendar, signal=False, price_col=price_col,
                yield_col=yield_col, iv_premium=premium)
            built[(index, premium)] = (price[entry:], dividend[entry:],
                                       riskfree[entry:], vol[entry:])

    index_rows = {}
    for index, column in ((EQUITY, EQUITY_TR), (NASDAQ, NDX_TOTAL)):
        wealth = np.r_[1., np.cumprod(1 + sample[entry:, column])]
        index_rows[index] = np.r_[path_metrics(wealth, horizon)[0],
                                  np.full(len(EXTRA_METRICS), np.nan)]

    treasury, financing = sample[entry:, TREASURY_R], sample[entry:, FINANCING]
    growth = {leverage: np.r_[1., 1 + daily_reset(treasury, financing, leverage)]
              for leverage in sorted({arm.leverage for arm in arms})}

    results = {}
    for arm in arms:
        for index, row in index_rows.items():
            results[key(arm.name, index)] = row
        sleeve = growth[arm.leverage]
        for name in STRUCTURES:
            spot, q, rate, sigma = built[(UNDERLYING[name], arm.iv_premium)]
            path = simulate_leaps_arrays(spot, sleeve, q, rate, sigma, horizon.days,
                                         horizon.schedules[(name, arm.roll)])
            record = np.r_[path_metrics(path.navs, horizon)[0], np.zeros(len(EXTRA_METRICS))]
            exposure = float(path.exposures.mean())
            notional = float((1 - path.option_weights).mean()) * arm.leverage
            record[BASE_SLOT['mean_delta_exposure']] = exposure
            record[SLOT['mean_option_weight']] = float(path.option_weights.mean())
            record[SLOT['treasury_notional']] = notional
            record[SLOT['gross_notional']] = exposure + notional
            results[key(arm.name, name)] = record
    return results


def summarize(store: dict, horizon: Horizon, case: Case, paired=False) -> pd.DataFrame:
    """The distribution §5 asks for, per arm and strategy.

    Every comparison against a rival is paired path by path, because the
    strategies share the simulated world: the probability that one beats another
    is a property of the pair, and computing it from two marginal distributions
    would answer a question nobody asked.
    """
    frames = []
    for arm in case.arms:
        wealth = {name: store[key(arm.name, name)][:, BASE_SLOT['terminal_wealth']]
                  for name in INDEX_ROWS}
        rows = []
        for name in list(INDEX_ROWS) + list(STRUCTURES):
            values = store[key(arm.name, name)]
            rates = values[:, BASE_SLOT['cagr']]
            terminal = values[:, BASE_SLOT['terminal_wealth']]
            drawdown = values[:, BASE_SLOT['max_drawdown']]
            own = UNDERLYING.get(name, name)
            moneyness, budget = STRUCTURES.get(name, (np.nan, np.nan))
            row = dict(case=case.name, arm=arm.name, dial=case.dial,
                       block_days=case.block, iv_premium=arm.iv_premium,
                       roll=arm.roll, treasury_leverage=arm.leverage,
                       horizon_years=horizon.years, paths=len(rates), strategy=name,
                       family=FAMILY.get(name, 'index'), underlying=own,
                       moneyness=moneyness, premium_budget=budget,
                       mean_cagr=float(rates.mean()),
                       max_drawdown_median=float(np.median(drawdown)),
                       worst_max_drawdown=float(drawdown.min()),
                       prob_negative_cagr=float((rates < 0).mean()),
                       prob_below_own_underlying=float((terminal < wealth[own]).mean())
                       if name != own else 0.,
                       prob_below_sp500=float((terminal < wealth[EQUITY]).mean())
                       if name != EQUITY else 0.)
            for level in PERCENTILES:
                row[f'cagr_p{level}'] = float(np.percentile(rates, level))
                row[f'terminal_wealth_p{level}'] = float(np.percentile(terminal, level))
            # Severity percentiles: `p90` is the drawdown only a tenth of paths
            # are worse than. Drawdowns are negative, so that is the tenth
            # percentile of the values themselves.
            for level in (90, 95):
                row[f'max_drawdown_p{level}'] = float(np.percentile(drawdown, 100 - level))
            for threshold in DRAWDOWN_THRESHOLDS:
                row[f'prob_drawdown_worse_than_{round(threshold * 100)}'] = float(
                    (drawdown <= -threshold).mean())
            for column in ('mean_delta_exposure',) + EXTRA_METRICS:
                series = values[:, SLOT[column]]
                row[column] = (float(np.nanmedian(series))
                               if np.isfinite(series).any() else np.nan)
            rows.append(row)
        frame = pd.DataFrame(rows)
        if paired:
            frame = adjacent_comparison(frame, store, arm.name)
        frames.append(frame)
    return pd.concat(frames, ignore_index=True)


# ----------------------------------------------------------------------------
# Frontiers, domination and classification
# ----------------------------------------------------------------------------

def _dominates(better_a, better_b, worse_a, worse_b, better_tol, worse_tol) -> bool:
    """Epsilon-dominance on two axes, both oriented so that higher is better.

    `a` dominates `b` when it is at least as good on one axis beyond the
    tolerance and no worse on the other beyond it. The tolerance is what stops
    a lattice from being read as a ranking: two cells inside it are the same
    cell as far as this module is concerned.
    """
    return ((better_a >= better_b - better_tol and worse_a >= worse_b + worse_tol)
            or (better_a >= better_b + better_tol and worse_a >= worse_b - worse_tol))


def dominators(frame: pd.DataFrame) -> dict:
    """For each dominated cell, the cell that beats it by the widest margin.

    A classification that says "dominated" without saying by what is an
    assertion. This names the rival, on the growth axes, chosen by how much
    shallower its median drawdown is at comparable growth — once over the whole
    lattice and once within the cell's own family.
    """
    cells = frame[frame.strategy.isin(STRUCTURES)]
    points = cells[['cagr_p50', 'max_drawdown_median']].to_numpy()
    names = list(cells.strategy)
    out, same = {}, {}
    for i, name in enumerate(names):
        beaten = [(points[j, 1] - points[i, 1], names[j]) for j in range(len(names))
                  if j != i and _dominates(points[j, 0], points[i, 0], points[j, 1],
                                           points[i, 1], CAGR_TOLERANCE,
                                           DRAWDOWN_TOLERANCE)]
        if beaten:
            out[name] = max(beaten)[1]
        # Whether a cell is beaten by its own family or only by the other
        # underlying is a different question and the one an investor already
        # committed to an index is asking.
        family = [pair for pair in beaten if FAMILY[pair[1]] == FAMILY[name]]
        if family:
            same[name] = max(family)[1]
    return out, same


def frontier_flags(frame: pd.DataFrame, members=None) -> pd.DataFrame:
    """Non-domination on each frontier, over a set of lattice cells.

    The two index rows are excluded from the comparison: they are the reference
    an investor is choosing *away* from, and letting an unlevered index sit at
    the low-risk corner of a LEAPS frontier would define the question away.

    `members` restricts the comparison, and the restriction is not a detail. A
    frontier drawn over both underlyings answers "does the choice of index move
    the frontier"; one drawn within a family answers "given that I am buying
    options on this index, which structures are efficient". Those are different
    questions and the brief asks both, so both are computed.
    """
    cells = frame[frame.strategy.isin(members if members is not None else STRUCTURES)]
    growth = cells[['cagr_p50', 'max_drawdown_median']].to_numpy()
    robust = np.column_stack([cells['cagr_p5'],
                              -cells['prob_drawdown_worse_than_60'].to_numpy()])
    names = list(cells.strategy)
    out = {}
    for label, points, tolerance in (('growth', growth,
                                      (CAGR_TOLERANCE, DRAWDOWN_TOLERANCE)),
                                     ('robust', robust,
                                      (CAGR_TOLERANCE, PROBABILITY_TOLERANCE))):
        flags = []
        for i in range(len(points)):
            dominated = any(_dominates(points[j, 0], points[i, 0], points[j, 1],
                                       points[i, 1], *tolerance)
                            for j in range(len(points)) if j != i)
            flags.append(not dominated)
        out[label] = dict(zip(names, flags))
    frame = frame.copy()
    frame['on_growth_frontier'] = frame.strategy.map(out['growth'])
    frame['on_robust_frontier'] = frame.strategy.map(out['robust'])
    frame['on_frontier'] = (frame.on_growth_frontier.fillna(False)
                            | frame.on_robust_frontier.fillna(False))
    return frame


def adjacent_comparison(frame: pd.DataFrame, store: dict, arm: str) -> pd.DataFrame:
    """P(beat the next-lower-risk frontier candidate), paired path by path.

    The comparison a lattice invites is against a neighbouring cell, which is
    usually not a decision anyone faces. The comparison that matters is against
    the candidate an investor would otherwise hold: the efficient point one step
    down in risk. Both are evaluated in the same world on the same day, so the
    probability is a genuine paired statistic.
    """
    flagged = frontier_flags(frame)
    candidates = flagged[flagged.on_growth_frontier.fillna(False)]
    ranked = candidates.sort_values('max_drawdown_median', ascending=False)
    names, risks = list(ranked.strategy), list(ranked.max_drawdown_median)
    adjacent, probability = {}, {}
    for _, row in flagged.iterrows():
        safer = [(n, r) for n, r in zip(names, risks)
                 if r > row.max_drawdown_median and n != row.strategy]
        if not safer:
            continue
        rival = min(safer, key=lambda pair: pair[1])[0]
        adjacent[row.strategy] = rival
        mine = store[key(arm, row.strategy)][:, BASE_SLOT['terminal_wealth']]
        theirs = store[key(arm, rival)][:, BASE_SLOT['terminal_wealth']]
        probability[row.strategy] = float((mine > theirs).mean())
    flagged['adjacent_lower_risk'] = flagged.strategy.map(adjacent)
    flagged['prob_beat_adjacent_lower_risk'] = flagged.strategy.map(probability)
    return flagged


def _verdict(on_frontier: bool, holds: int) -> str:
    if not on_frontier:
        return 'dominated'
    return 'robust frontier' if holds >= ROBUST_HOLDS else 'conditional frontier'


def _holds(primary, sensitivities, members) -> tuple:
    """Frontier membership in the primary run and in each sensitivity, for one set."""
    base = frontier_flags(primary, members).set_index('strategy')
    held = {name: [] for name in members}
    for case, arm in SENSITIVITY_ARMS:
        piece = sensitivities[(sensitivities.case == case) & (sensitivities.arm == arm)]
        flags = frontier_flags(piece, members).set_index('strategy')
        for name in members:
            if bool(flags.loc[name, 'on_frontier']):
                held[name].append(f'{case}:{arm}')
    return base, held


def classify(primary: pd.DataFrame, sensitivities: pd.DataFrame) -> pd.DataFrame:
    """Robust frontier, conditional frontier, or dominated.

    Frontier membership is recomputed *inside* each sensitivity rather than
    inferred by differencing against the primary run, so a candidate is asked
    the same question seven more times rather than asked whether its numbers
    moved. Which is the question: an efficient point that stops being efficient
    when the option price moves has not been mispriced, it has been misread.
    """
    base, held = _holds(primary, sensitivities, list(STRUCTURES))
    beaten_by, beaten_within = dominators(primary)
    families = {family: _holds(primary, sensitivities, members)
                for family, members in (('SP500 LEAPS', list(SP_STRUCTURES)),
                                        ('NDX LEAPS', list(NDX_STRUCTURES)))}
    rows = []
    for name in STRUCTURES:
        moneyness, budget = STRUCTURES[name]
        holds = held[name]
        verdict = _verdict(bool(base.loc[name, 'on_frontier']), len(holds))
        own_base, own_held = families[FAMILY[name]]
        own = own_held[name]
        rows.append(dict(
            strategy=name, family=FAMILY[name], underlying=UNDERLYING[name],
            moneyness=moneyness, premium_budget=budget, classification=verdict,
            family_classification=_verdict(bool(own_base.loc[name, 'on_frontier']),
                                           len(own)),
            family_sensitivities_held=len(own),
            dominated_by=beaten_by.get(name, ''),
            dominated_by_same_family=beaten_within.get(name, ''),
            on_growth_frontier=bool(base.loc[name, 'on_growth_frontier']),
            on_robust_frontier=bool(base.loc[name, 'on_robust_frontier']),
            sensitivities_held=len(holds), sensitivities_tested=len(SENSITIVITY_ARMS),
            failed_sensitivities=';'.join(sorted(
                f'{c}:{a}' for c, a in SENSITIVITY_ARMS if f'{c}:{a}' not in holds)),
            median_cagr=float(base.loc[name, 'cagr_p50']),
            p5_cagr=float(base.loc[name, 'cagr_p5']),
            median_max_drawdown=float(base.loc[name, 'max_drawdown_median']),
            prob_drawdown_worse_than_60=float(
                base.loc[name, 'prob_drawdown_worse_than_60']),
            mean_delta_exposure=float(base.loc[name, 'mean_delta_exposure'])))
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------
# Prespecified hypotheses
# ----------------------------------------------------------------------------

def resolution(historical: pd.DataFrame, names) -> float:
    """The smallest CAGR gap this comparison is entitled to call a difference.

    One volatility point of modelled CAGR for the more price-sensitive of the
    structures being compared, floored at the prespecified tolerance. For the
    high-budget cells that unit is several times the tolerance, which is the
    whole point: the expensive end of the lattice is where the option-price
    assumption does most of the work, and it is exactly where a reader is most
    tempted to rank cells on their medians.
    """
    unit = max(abs(float(historical.loc[name, 'cagr_per_volatility_point']))
               for name in names)
    return max(unit, CAGR_TOLERANCE)


def _step(frame, historical, high: str, low: str, note: str) -> dict:
    """One move up the lattice: what it gains, what it costs, and whether it is real."""
    gain = float(frame.loc[high, 'cagr_p50'] - frame.loc[low, 'cagr_p50'])
    family, from_budget = FAMILY[low], STRUCTURES[low][1]
    tail = float(frame.loc[high, 'prob_drawdown_worse_than_60']
                 - frame.loc[low, 'prob_drawdown_worse_than_60'])
    deeper = float(frame.loc[low, 'max_drawdown_median']
                   - frame.loc[high, 'max_drawdown_median'])
    bar = resolution(historical, (high, low))
    return dict(step=f'{low} -> {high}', note=note, family=family,
                from_budget=from_budget, from_strategy=low, to_strategy=high,
                median_cagr_gained=gain,
                p5_cagr_gained=float(frame.loc[high, 'cagr_p5'] - frame.loc[low, 'cagr_p5']),
                drawdown_deepened=deeper, tail_probability_added=tail,
                tail_points_per_cagr_point=float(tail / gain) if abs(gain) > 1e-9 else np.nan,
                drawdown_per_cagr_point=float(deeper / gain) if abs(gain) > 1e-9 else np.nan,
                resolution_bar=bar, economically_resolved=bool(abs(gain) > bar))


def budget_ladder(frame, historical, moneyness: float, prefix='SPX') -> pd.DataFrame:
    """Every one-notch budget step at one strike, in order."""
    budgets = SP_BUDGETS if prefix == 'SPX' else NDX_BUDGETS
    names = [f'{prefix}_{round(moneyness * 100)}_{round(b * 100)}' for b in budgets]
    return pd.DataFrame([_step(frame, historical, high, low, f'{prefix} {moneyness:.2f}')
                         for low, high in zip(names[:-1], names[1:])])


def _same(frame, historical, a: str, b: str, note: str) -> dict:
    """Are two cells distinguishable at all, on every axis the frontier uses?"""
    gaps = {'median_cagr': float(frame.loc[a, 'cagr_p50'] - frame.loc[b, 'cagr_p50']),
            'p5_cagr': float(frame.loc[a, 'cagr_p5'] - frame.loc[b, 'cagr_p5']),
            'median_max_drawdown': float(frame.loc[a, 'max_drawdown_median']
                                         - frame.loc[b, 'max_drawdown_median']),
            'prob_drawdown_worse_than_60': float(
                frame.loc[a, 'prob_drawdown_worse_than_60']
                - frame.loc[b, 'prob_drawdown_worse_than_60'])}
    bar = resolution(historical, (a, b))
    bars = {'median_cagr': bar, 'p5_cagr': bar,
            'median_max_drawdown': DRAWDOWN_TOLERANCE,
            'prob_drawdown_worse_than_60': PROBABILITY_TOLERANCE}
    separated = [axis for axis, gap in gaps.items() if abs(gap) > bars[axis]]
    return dict(pair=f'{a} vs {b}', note=note,
                **{f'{axis}_gap': gap for axis, gap in gaps.items()},
                cagr_resolution_bar=bar,
                axes_separated=len(separated),
                separating_axes=';'.join(separated),
                distinct=bool(separated))


def sp_hypotheses(frame, historical) -> tuple:
    """The five prespecified S&P questions, answered from the primary run."""
    ladders = pd.concat(
        [budget_ladder(frame, historical, m) for m in SP_MONEYNESS]
        + [budget_ladder(frame, historical, m, prefix='NDX') for m in NDX_MONEYNESS],
        ignore_index=True)
    steps = [_step(frame, historical, 'SPX_85_40', 'SPX_85_30', 'Q1 enhanced growth'),
             _step(frame, historical, 'SPX_90_40', 'SPX_90_35', 'Q2 into the elbow'),
             _step(frame, historical, 'SPX_90_45', 'SPX_90_40', 'Q2 out of the elbow'),
             _step(frame, historical, 'SPX_90_50', 'SPX_90_40', 'Q3 the aggressive step')]
    pairs = [_same(frame, historical, 'SPX_95_40', 'SPX_90_40', 'Q4 redundancy'),
             _same(frame, historical, 'SPX_95_50', 'SPX_90_50', 'Q5 distinct regimes')]
    return ladders, pd.DataFrame(steps), pd.DataFrame(pairs)


NDX_HYPOTHESIS_MONEYNESS = (.80, .85, .90)
NDX_HYPOTHESIS_BUDGETS = (.20, .25, .30)
SP_TARGETS = ('SPX_90_40', 'SPX_90_50', 'SPX_95_50')


def ndx_hypotheses(primary, historical) -> pd.DataFrame:
    """Can 20-30% of capital on the Nasdaq buy what 40-50% buys on the S&P?

    Growth is compared on the thirty-year median, but the columns that decide
    the question are the short-horizon fifth percentiles and the drawdown
    distribution: a structure that matches on the median and is worse in the bad
    decade has not matched.
    """
    thirty = primary[primary.horizon_years == FRONTIER_HORIZON].set_index('strategy')
    twenty = primary[primary.horizon_years == 20].set_index('strategy')
    ten = primary[primary.horizon_years == 10].set_index('strategy')
    hist = historical.set_index('strategy')
    candidates = [f'NDX_{round(m * 100)}_{round(b * 100)}'
                  for m in NDX_HYPOTHESIS_MONEYNESS for b in NDX_HYPOTHESIS_BUDGETS]
    rows = []
    for candidate in candidates:
        for target in SP_TARGETS:
            gap = float(thirty.loc[candidate, 'cagr_p50'] - thirty.loc[target, 'cagr_p50'])
            bar = resolution(hist, (candidate, target))
            better = dict(
                ten_year_p5=float(ten.loc[candidate, 'cagr_p5'] - ten.loc[target, 'cagr_p5']),
                twenty_year_p5=float(twenty.loc[candidate, 'cagr_p5']
                                     - twenty.loc[target, 'cagr_p5']),
                median_drawdown=float(thirty.loc[candidate, 'max_drawdown_median']
                                      - thirty.loc[target, 'max_drawdown_median']),
                tail_probability=float(thirty.loc[target, 'prob_drawdown_worse_than_60']
                                       - thirty.loc[candidate,
                                                    'prob_drawdown_worse_than_60']),
                negative_decade=float(ten.loc[target, 'prob_negative_cagr']
                                      - ten.loc[candidate, 'prob_negative_cagr']))
            rows.append(dict(
                candidate=candidate, target=target,
                candidate_budget=STRUCTURES[candidate][1],
                target_budget=STRUCTURES[target][1],
                budget_saved=float(STRUCTURES[target][1] - STRUCTURES[candidate][1]),
                median_30y_cagr_gap=gap, resolution_bar=bar,
                approaches=bool(gap >= -APPROACH_BAND),
                matches_or_beats=bool(gap >= -bar),
                **{f'{name}_advantage': value for name, value in better.items()},
                better_on_all_downside_axes=bool(all(v > 0 for v in better.values())),
                candidate_2000_2002=float(hist.loc[candidate, '2000_2002_bust']),
                target_2000_2002=float(hist.loc[target, '2000_2002_bust']),
                candidate_2022=float(hist.loc[candidate, '2022_rates']),
                target_2022=float(hist.loc[target, '2022_rates'])))
    return pd.DataFrame(rows)


def cheapest_reaching(nasdaq: pd.DataFrame) -> dict:
    """The least option capital that reaches each S&P target, or the closest miss.

    Ranked on budget first and the growth gap second, so the answer is the
    cheapest structure that gets there rather than the best-performing one — the
    question is capital efficiency. When nothing reaches a target the nearest
    candidate is returned instead, carrying its own `approaches` flag, so the
    miss is reported rather than the row being absent.
    """
    out = {}
    for target in SP_TARGETS:
        options = nasdaq[nasdaq.target == target]
        reaching = options[options.approaches]
        out[target] = (reaching.sort_values(['candidate_budget', 'median_30y_cagr_gap'],
                                            ascending=[True, False]).iloc[0]
                       if len(reaching) else
                       options.sort_values('median_30y_cagr_gap',
                                           ascending=False).iloc[0])
    return out


def survival(sensitivities, historical, pairs) -> pd.DataFrame:
    """Does a candidate's win over a target survive each sensitivity?

    Asked of the median thirty-year CAGR gap under every arm, against the same
    resolution bar the primary comparison uses. A win that only exists at one
    option price is not a win.
    """
    hist = historical.set_index('strategy')
    rows = []
    for candidate, target in pairs:
        bar = resolution(hist, (candidate, target))
        row = dict(candidate=candidate, target=target, resolution_bar=bar)
        holds = 0
        for case, arm in SENSITIVITY_ARMS:
            piece = sensitivities[(sensitivities.case == case)
                                  & (sensitivities.arm == arm)].set_index('strategy')
            gap = float(piece.loc[candidate, 'cagr_p50'] - piece.loc[target, 'cagr_p50'])
            row[f'{case}:{arm}'] = gap
            holds += gap >= -bar
        row['arms_held'] = holds
        row['arms_tested'] = len(SENSITIVITY_ARMS)
        rows.append(row)
    return pd.DataFrame(rows)


def tier_table(primary, historical) -> pd.DataFrame:
    """For each return level, the lowest-risk candidate each underlying offers.

    Nothing is optimized to hit a tier. The nearest candidate in each family is
    reported with the distance shown, and a tier no family reaches is left
    empty rather than filled with the closest thing to hand.
    """
    thirty = primary[primary.horizon_years == FRONTIER_HORIZON].set_index('strategy')
    ten = primary[primary.horizon_years == 10].set_index('strategy')
    hist = historical.set_index('strategy')
    rows = []
    for tier in RETURN_TIERS:
        for family, members in (('SP500 LEAPS', SP_STRUCTURES),
                                ('NDX LEAPS', NDX_STRUCTURES)):
            near = [n for n in members
                    if abs(float(thirty.loc[n, 'cagr_p50']) - tier) <= TIER_BAND]
            within = bool(near)
            if not near:
                near = [min(members, key=lambda n: abs(float(thirty.loc[n, 'cagr_p50'])
                                                       - tier))]
            pick = min(near, key=lambda n: float(thirty.loc[n, 'prob_drawdown_worse_than_60']))
            rows.append(dict(
                return_tier=tier, family=family, strategy=pick, within_band=within,
                candidates_in_band=len(near) if within else 0,
                premium_budget=STRUCTURES[pick][1], moneyness=STRUCTURES[pick][0],
                median_30y_cagr=float(thirty.loc[pick, 'cagr_p50']),
                distance_from_tier=float(thirty.loc[pick, 'cagr_p50'] - tier),
                p5_30y_cagr=float(thirty.loc[pick, 'cagr_p5']),
                mean_delta_exposure=float(thirty.loc[pick, 'mean_delta_exposure']),
                median_max_drawdown=float(thirty.loc[pick, 'max_drawdown_median']),
                prob_drawdown_worse_than_60=float(
                    thirty.loc[pick, 'prob_drawdown_worse_than_60']),
                prob_negative_decade=float(ten.loc[pick, 'prob_negative_cagr']),
                event_2000_2002=float(hist.loc[pick, '2000_2002_bust']),
                event_2022=float(hist.loc[pick, '2022_rates'])))
    return pd.DataFrame(rows)


def volatility_movement(sensitivities) -> pd.DataFrame:
    """How far three volatility points move each cell, on the same worlds.

    The three option-price arms are scored on one set of resampled paths, so
    these are paired differences rather than two experiments compared across
    sampling noise.
    """
    piece = sensitivities[sensitivities.case == 'iv_premium']
    arms = {arm: piece[piece.arm == arm].set_index('strategy')
            for arm in ('iv_0', 'iv_3', 'iv_6')}
    rows = []
    for name in STRUCTURES:
        row = dict(strategy=name)
        for column, label in (('cagr_p50', 'median_cagr'), ('cagr_p5', 'p5_cagr')):
            base = float(arms['iv_3'].loc[name, column])
            row[f'{label}_at_plus_3'] = base
            row[f'{label}_move_when_dearer'] = float(arms['iv_6'].loc[name, column]) - base
            row[f'{label}_move_when_cheaper'] = float(arms['iv_0'].loc[name, column]) - base
        rows.append(row)
    return pd.DataFrame(rows)


def neighbour_support(classification: pd.DataFrame) -> pd.DataFrame:
    """How much of a cell's own neighbourhood is also efficient.

    A single efficient cell surrounded by dominated ones is a numerical winner,
    not a regime: the lattice is coarse and the surface is noisy, and the only
    protection against reading one is that its neighbours agree. Neighbours are
    one notch in strike or one notch in budget, within the same family. The
    off-grid conservative anchor has none by construction and is left blank
    rather than scored against cells it does not neighbour.
    """
    frontier = dict(zip(classification.strategy, classification.classification != 'dominated'))
    counts, supported = {}, {}
    for name, (moneyness, budget) in STRUCTURES.items():
        prefix = 'SPX' if UNDERLYING[name] == EQUITY else 'NDX'
        strikes = SP_MONEYNESS if prefix == 'SPX' else NDX_MONEYNESS
        budgets = SP_BUDGETS if prefix == 'SPX' else NDX_BUDGETS
        if moneyness not in strikes or budget not in budgets:
            continue
        i, j = strikes.index(moneyness), budgets.index(budget)
        cells = ([(strikes[k], budget) for k in (i - 1, i + 1) if 0 <= k < len(strikes)]
                 + [(moneyness, budgets[k]) for k in (j - 1, j + 1) if 0 <= k < len(budgets)])
        names = [f'{prefix}_{round(m * 100)}_{round(b * 100)}' for m, b in cells]
        names = [n for n in names if n in STRUCTURES]
        counts[name] = len(names)
        supported[name] = sum(frontier[n] for n in names)
    out = classification.copy()
    out['lattice_neighbours'] = out.strategy.map(counts)
    out['neighbours_on_frontier'] = out.strategy.map(supported)
    out['neighbour_support'] = out.neighbours_on_frontier / out.lattice_neighbours
    return out


REGIME_NAMES = ('conservative growth', 'balanced growth', 'enhanced growth',
                'aggressive growth', 'maximum growth')
# A menu is only useful if a person can hold it in their head. Two axes of
# separation rather than one, because a pair that differs only in median
# drawdown and only just is one choice presented twice; five entries at most,
# because the brief asks for a small set an investor chooses between.
REGIME_AXES = 2
MENU_SIZE = len(REGIME_NAMES)


def _thin(kept, cells) -> list:
    """Spread the menu evenly along the risk axis, keeping both ends.

    Applied only when the separation walk leaves more candidates than a menu
    should carry. Deterministic and stated rather than hand-picked: both
    extremes are kept and the interior entries are the candidates nearest to
    evenly spaced points on median max drawdown.
    """
    risks = {name: float(cells.loc[name, 'median_max_drawdown']) for name in kept}
    targets = np.linspace(max(risks.values()), min(risks.values()), MENU_SIZE)
    chosen = []
    for target in targets:
        remaining = [n for n in kept if n not in chosen]
        chosen.append(min(remaining, key=lambda n: abs(risks[n] - target)))
    return sorted(set(chosen), key=lambda n: -risks[n])


def regimes(classification: pd.DataFrame, frame, historical) -> pd.DataFrame:
    """The economically distinct regimes the robust frontier actually contains.

    Walked from the least aggressive robust candidate upward, keeping a
    candidate only when it is separated from the one already kept on at least
    one frontier axis beyond the tolerances. That turns a lattice into a short
    menu, and it is the whole deliverable: neighbouring cells that cannot be
    told apart are one choice, not two.
    """
    robust = classification[classification.classification == 'robust frontier']
    ordered = list(robust.sort_values('median_cagr').strategy)
    kept = []
    for name in ordered:
        if kept and _same(frame, historical, name, kept[-1],
                          'regime walk')['axes_separated'] < REGIME_AXES:
            continue
        kept.append(name)
    walked = len(kept)
    if len(kept) > MENU_SIZE:
        kept = _thin(kept, classification.set_index('strategy'))
    labels = (list(REGIME_NAMES) if len(kept) >= len(REGIME_NAMES)
              else [REGIME_NAMES[0]] + list(REGIME_NAMES[-(len(kept) - 1):])
              if len(kept) > 1 else [REGIME_NAMES[0]])
    rows = []
    for position, name in enumerate(kept):
        row = classification.set_index('strategy').loc[name]
        rows.append(dict(regime=labels[position] if position < len(labels) else 'beyond',
                         strategy=name, walk_size=walked, underlying=row.underlying,
                         moneyness=row.moneyness, premium_budget=row.premium_budget,
                         median_cagr=row.median_cagr, p5_cagr=row.p5_cagr,
                         median_max_drawdown=row.median_max_drawdown,
                         prob_drawdown_worse_than_60=row.prob_drawdown_worse_than_60,
                         mean_delta_exposure=row.mean_delta_exposure,
                         sensitivities_held=row.sensitivities_held,
                         neighbour_support=row.get('neighbour_support', np.nan)))
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------
# Figures
# ----------------------------------------------------------------------------

STYLE = {'robust frontier': dict(color='C2', alpha=.95, zorder=4),
         'conditional frontier': dict(color='C1', alpha=.9, zorder=3),
         'dominated': dict(color='0.72', alpha=.85, zorder=2)}
MARKER = {'SP500 LEAPS': 'o', 'NDX LEAPS': '^'}
SHORT = {EQUITY: 'S&P 500 1x', NASDAQ: 'Nasdaq-100 1x'}


def _scatter(ax, frame, x, y, annotate=True):
    """One frontier panel: shape says underlying, colour says classification."""
    for verdict, style in STYLE.items():
        for family, marker in MARKER.items():
            piece = frame[(frame.classification == verdict) & (frame.family == family)]
            if not len(piece):
                continue
            size = 26 if verdict == 'dominated' else 62
            ax.scatter(piece[x], piece[y], marker=marker, s=size,
                       edgecolors='none' if verdict == 'dominated' else 'k',
                       linewidths=.6, label=f'{family.split()[0]} {verdict}', **style)
    if annotate:
        named = frame[frame.classification != 'dominated']
        for _, row in named.iterrows():
            ax.annotate(row.strategy, (row[x], row[y]), textcoords='offset points',
                        xytext=(6, -3), fontsize=6.5)
    for axis in (ax.xaxis, ax.yaxis):
        axis.set_major_formatter(PercentFormatter(1))


def growth_figure(path: Path, frame: pd.DataFrame, indices: pd.DataFrame):
    fig, axes = plt.subplots(1, 2, figsize=(13.6, 5.6), constrained_layout=True)
    ax = axes[0]
    _scatter(ax, frame, 'median_max_drawdown', 'median_cagr')
    for name, marker in zip(INDEX_ROWS, ('*', 'D')):
        row = indices.loc[name]
        ax.scatter(row.max_drawdown_median, row.cagr_p50, marker=marker, s=90,
                   color='k', zorder=5, label=SHORT[name])
    ax.set_xlabel('median max drawdown')
    ax.set_ylabel(f'median {FRONTIER_HORIZON}-year CAGR')
    ax.legend(fontsize=7, loc='lower left', ncol=2)
    ax.set_title('A. Growth frontier')

    ax = axes[1]
    for family, marker in MARKER.items():
        piece = frame[frame.family == family].sort_values('premium_budget')
        for verdict, style in STYLE.items():
            part = piece[piece.classification == verdict]
            ax.scatter(part.premium_budget, part.mean_delta_exposure, marker=marker,
                       s=44, edgecolors='none' if verdict == 'dominated' else 'k',
                       linewidths=.5, **style)
    ax.set_xlabel('premium budget')
    ax.set_ylabel('mean delta-equivalent equity exposure')
    ax.xaxis.set_major_formatter(PercentFormatter(1))
    ax.set_title('B. What the budget buys')
    fig.savefig(path, dpi=160)
    plt.close(fig)


def robust_figure(path: Path, frame: pd.DataFrame, indices: pd.DataFrame):
    fig, axes = plt.subplots(1, 2, figsize=(13.6, 5.6), constrained_layout=True)
    ax = axes[0]
    _scatter(ax, frame, 'prob_drawdown_worse_than_60', 'p5_cagr')
    for name, marker in zip(INDEX_ROWS, ('*', 'D')):
        row = indices.loc[name]
        ax.scatter(row.prob_drawdown_worse_than_60, row.cagr_p5, marker=marker, s=90,
                   color='k', zorder=5, label=SHORT[name])
    ax.set_xlabel('P(max drawdown worse than 60%)')
    ax.set_ylabel(f'fifth-percentile {FRONTIER_HORIZON}-year CAGR')
    ax.legend(fontsize=7, loc='lower left', ncol=2)
    ax.set_title('A. Robust-growth frontier')

    ax = axes[1]
    _scatter(ax, frame, 'p5_cagr', 'median_cagr', annotate=False)
    ax.set_xlabel(f'fifth-percentile {FRONTIER_HORIZON}-year CAGR')
    ax.set_ylabel(f'median {FRONTIER_HORIZON}-year CAGR')
    ax.set_title('B. Median against the bad case')
    fig.savefig(path, dpi=160)
    plt.close(fig)


# ----------------------------------------------------------------------------
# Driver
# ----------------------------------------------------------------------------

def run(root: Path, workers=None, paths=None, sensitivity_paths=None):
    inputs = load(root)
    market, _ = underlying_inputs(inputs, root)
    reports = root / 'reports'
    historical = historical_table(inputs, market)
    check = committed_check(reports, historical)
    pool = frontier_pool(inputs, market)

    count = paths or MC_PATHS
    primary_case = Case('primary', PRIMARY_BLOCK, count, (BASELINE,), 'none')
    frames = []
    for years in MC_HORIZONS:
        horizon = build_horizon(inputs, years, (ROLL_LABEL,), structures=STRUCTURES)
        store = monte_carlo(pool, horizon, primary_case.arms, count, PRIMARY_BLOCK,
                            SEED, workers, runner=run_path)
        frames.append(summarize(store, horizon, primary_case, paired=True))
        print(f'  primary {years}y: {count} paths, block {PRIMARY_BLOCK}')
    primary = pd.concat(frames, ignore_index=True)

    # Every sensitivity is read at the classification horizon only. The dials
    # move the frontier's *location*, and the frontier is defined at thirty
    # years; running each of them at three horizons would triple the cost to
    # answer a question no part of the brief asks.
    horizon = build_horizon(inputs, FRONTIER_HORIZON, ROLL_LABELS, structures=STRUCTURES)
    frames = []
    for case in CASES:
        number = sensitivity_paths or case.paths
        store = monte_carlo(pool, horizon, case.arms, number, case.block, SEED,
                            workers, runner=run_path)
        frames.append(summarize(store, horizon, replace(case, paths=number)))
        print(f'  {case.name}: {number} paths, block {case.block}, '
              f'{len(case.arms)} arm(s)')
    sensitivities = pd.concat(frames, ignore_index=True)

    thirty = primary[primary.horizon_years == FRONTIER_HORIZON].set_index('strategy')
    classification = neighbour_support(classify(
        primary[primary.horizon_years == FRONTIER_HORIZON], sensitivities))
    classification = classification.merge(volatility_movement(sensitivities),
                                          on='strategy', how='left')
    hist = historical.set_index('strategy')
    ladders, steps, pairs = sp_hypotheses(thirty, hist)
    nasdaq = ndx_hypotheses(primary, historical)
    tiers = tier_table(primary, historical)
    chosen = regimes(classification, thirty, hist)
    held = survival(sensitivities, historical,
                    [(row.candidate, target)
                     for target, row in cheapest_reaching(nasdaq).items()])
    hypotheses = pd.concat([ladders.assign(question='budget ladder'),
                            steps.assign(question='S&P steps')], ignore_index=True)

    outputs = {'leaps_frontier_historical.csv': historical,
               'leaps_frontier_monte_carlo.csv': primary,
               'leaps_frontier_sensitivities.csv': sensitivities,
               'leaps_frontier_classification.csv': classification}
    for name, frame in outputs.items():
        frame.pipe(stable_floats).to_csv(reports / name, index=False,
                                         float_format=FLOAT_FORMAT)
    plotted = classification.merge(
        thirty.reset_index()[['strategy', 'family']], on='strategy', how='left',
        suffixes=('', '_mc'))
    growth_figure(reports / 'leaps_frontier_growth.png', plotted, thirty)
    robust_figure(reports / 'leaps_frontier_robust.png', plotted, thirty)
    report(reports, inputs, historical, check, primary, sensitivities, classification,
           hypotheses, pairs, nasdaq, held, tiers, chosen, count)

    (reports / 'leaps_frontier_manifest.json').write_text(json.dumps({
        'window': [inputs.ix[0].date().isoformat(), inputs.ix[-1].date().isoformat()],
        'observations': int(len(inputs.ix)),
        'sp500_structures': {k: list(v) for k, v in SP_STRUCTURES.items()},
        'nasdaq_structures': {k: list(v) for k, v in NDX_STRUCTURES.items()},
        'conservative_anchor': ANCHOR[0],
        'lattice_is_prespecified': True,
        'committed_cells_rechecked': {k: v for k, v in COMMITTED.items()},
        'committed_source': COMMITTED_FILE,
        'roll': ROLL_LABEL, 'maturity_years': MATURITY_YEARS,
        'iv_premium': IV_PREMIUM, 'iv_premium_arms': list(IV_PREMIUMS),
        'option_spread_bps': OPTION_SPREAD_BPS,
        'treasury_leverage_arms': list(LEVERAGES),
        'roll_arms': list(ROLL_LABELS), 'block_arms': list(BLOCK_LENGTHS),
        'seed': SEED, 'primary_block_days': PRIMARY_BLOCK, 'primary_paths': count,
        'horizons': list(MC_HORIZONS), 'frontier_horizon': FRONTIER_HORIZON,
        'sensitivity_paths': {case.name: (sensitivity_paths or case.paths)
                              for case in CASES},
        'warmup_sessions': WARMUP_SESSIONS,
        'tolerances': {'cagr': CAGR_TOLERANCE, 'max_drawdown': DRAWDOWN_TOLERANCE,
                       'drawdown_probability': PROBABILITY_TOLERANCE,
                       'robust_holds_required': ROBUST_HOLDS,
                       'sensitivities_tested': len(SENSITIVITY_ARMS)},
        'resampled_columns': ['price_return', 'equity_total', 'treasury', 'cash', 'upro',
                              'tmf', 'dividend_yield', 'bond_financing',
                              'nasdaq_price_return', 'nasdaq_total_return',
                              'nasdaq_dividend_yield'],
        'nasdaq_proxy_through': PROXY_THROUGH,
        'nasdaq_proxy_note': 'Nasdaq-100 total returns are grossed up from price with an '
                             'assumed zero dividend yield through that date; every '
                             'thirty-year window opens inside that era, so the '
                             'post-splice twenty-year column is reported beside it',
        'option_prices': 'modelled with Black-Scholes on an assumed implied volatility; '
                         'this repository holds no long-dated option price history, no '
                         'skew surface and no bid/ask record for either index',
        'python': platform.python_version(), 'numpy': np.__version__,
        'pandas': pd.__version__, 'scipy': scipy.__version__,
        'matplotlib': matplotlib.__version__,
        'source_hashes': source_hashes(root, __spec__.name),
        'outputs_sha256': {name: sha(reports / name) for name in outputs},
    }, indent=2) + '\n')
    counts = classification.classification.value_counts()
    print(f'LEAPS frontier: {len(STRUCTURES)} structures; '
          f'{int(counts.get("robust frontier", 0))} robust, '
          f'{int(counts.get("conditional frontier", 0))} conditional, '
          f'{int(counts.get("dominated", 0))} dominated; '
          f'{count} primary paths x {len(MC_HORIZONS)} horizons.')
    return historical, primary, sensitivities, classification


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('--workers', type=int, default=None)
    parser.add_argument('--paths', type=int, default=None)
    parser.add_argument('--sensitivity-paths', type=int, default=None)
    parser.add_argument('--offline', action='store_true', default=True)
    args = parser.parse_args()
    run(args.root, args.workers, args.paths, args.sensitivity_paths)



# ----------------------------------------------------------------------------
# Report
# ----------------------------------------------------------------------------

def arm_frame(sensitivities: pd.DataFrame, case: str, arm: str) -> pd.DataFrame:
    piece = sensitivities[(sensitivities.case == case) & (sensitivities.arm == arm)]
    return piece.set_index('strategy')


def _knee(ladders: pd.DataFrame) -> dict:
    """The budget at which the marginal price of return rises fastest.

    Averaged across strikes so the answer is about the budget rather than about
    one ladder, and read off the *increment* in tail price rather than its level,
    so no threshold has to be chosen: the knee is where the cost of the next
    notch grows most, whatever that cost happens to be.
    """
    out = {}
    for family, group in ladders.groupby('family'):
        by_budget = group.groupby('from_budget').tail_points_per_cagr_point.mean()
        by_budget = by_budget.sort_index()
        if len(by_budget) < 2:
            continue
        steps = by_budget.diff().dropna()
        out[family] = dict(budget=float(steps.idxmax()), rise=float(steps.max()),
                           ladder={float(k): float(v) for k, v in by_budget.items()})
    return out


def _narrative(historical, check, primary, sensitivities, classification, ladders,
               pairs, nasdaq, held, tiers, chosen) -> dict:
    """Every number the prose states, derived from the tables it sits beside."""
    hist = historical.set_index('strategy')
    thirty = primary[primary.horizon_years == FRONTIER_HORIZON].set_index('strategy')
    ten = primary[primary.horizon_years == 10].set_index('strategy')
    cells = classification.set_index('strategy')
    units = hist.loc[list(STRUCTURES), 'cagr_per_volatility_point']

    groups = {verdict: list(classification[classification.classification == verdict].strategy)
              for verdict in ('robust frontier', 'conditional frontier', 'dominated')}
    robust = groups['robust frontier']
    families = {family: [n for n in robust if FAMILY[n] == family]
                for family in ('SP500 LEAPS', 'NDX LEAPS')}
    # The frontier drawn inside one family, which is the question an investor who
    # has already chosen an index is asking. The joint frontier above is the one
    # that answers whether the choice of index matters at all.
    within = {}
    for family in ('SP500 LEAPS', 'NDX LEAPS'):
        piece = classification[classification.family == family]
        within[family] = {verdict: list(
            piece[piece.family_classification == verdict].strategy)
            for verdict in ('robust frontier', 'conditional frontier', 'dominated')}

    # Q13: does a quarter-turn of duration reorder anything? Both arms are scored
    # on the same worlds, so the comparison is paired.
    base_arm = arm_frame(sensitivities, 'treasury_leverage', 'tsy_100')
    levered = arm_frame(sensitivities, 'treasury_leverage', 'tsy_125')
    names = list(STRUCTURES)
    rank = float(spearmanr(base_arm.loc[names, 'cagr_p50'],
                           levered.loc[names, 'cagr_p50']).statistic)
    flags = {arm: set(frontier_flags(frame.reset_index())
                      .query('on_frontier').strategy)
             for arm, frame in (('tsy_100', base_arm), ('tsy_125', levered))}
    leverage_flips = sorted(flags['tsy_100'] ^ flags['tsy_125'])
    leverage_cagr = float((levered.loc[names, 'cagr_p50']
                           - base_arm.loc[names, 'cagr_p50']).mean())
    leverage_tail = float((levered.loc[names, 'prob_drawdown_worse_than_60']
                           - base_arm.loc[names, 'prob_drawdown_worse_than_60']).mean())

    move = classification.set_index('strategy')
    dearer = move['median_cagr_move_when_dearer']
    worst_priced = dearer.idxmin()
    high = [n for n in STRUCTURES if STRUCTURES[n][1] >= .45]
    low = [n for n in STRUCTURES if STRUCTURES[n][1] <= .25]

    # Q7/Q8: the cheapest Nasdaq structure that reaches each S&P target.
    cheapest = cheapest_reaching(nasdaq)

    tier_pairs = []
    for tier, group in tiers.groupby('return_tier'):
        picks = group.set_index('family')
        if not group.within_band.all():
            continue
        sp, ndx = picks.loc['SP500 LEAPS'], picks.loc['NDX LEAPS']
        tier_pairs.append(dict(
            tier=float(tier), sp=sp.strategy, ndx=ndx.strategy,
            budget_saved=float(sp.premium_budget - ndx.premium_budget),
            p5=float(ndx.p5_30y_cagr - sp.p5_30y_cagr),
            tail=float(sp.prob_drawdown_worse_than_60 - ndx.prob_drawdown_worse_than_60),
            drawdown=float(ndx.median_max_drawdown - sp.median_max_drawdown),
            decade=float(sp.prob_negative_decade - ndx.prob_negative_decade),
            bust=float(ndx.event_2000_2002 - sp.event_2000_2002)))
    ndx_cheaper = [t for t in tier_pairs if t['budget_saved'] > 0]
    ndx_better = [t for t in tier_pairs if t['p5'] > 0 and t['tail'] > 0]

    steps = ladders[ladders.question == 'S&P steps'].set_index('note')
    below = int((units.abs() < CAGR_TOLERANCE).sum())
    return dict(
        hist=hist, thirty=thirty, ten=ten, cells=cells, groups=groups, robust=robust,
        families=families, within=within, units=units, sensitivities=sensitivities,
        q1=steps.loc['Q1 enhanced growth'], q2_into=steps.loc['Q2 into the elbow'],
        q2_out=steps.loc['Q2 out of the elbow'],
        q3=steps.loc['Q3 the aggressive step'],
        unit_low=float(units.min()), unit_high=float(units.max()),
        unit_low_name=str(units.idxmin()), unit_high_name=str(units.idxmax()),
        below_tolerance=below, tolerance_phrase=(
            f'not one of the {len(STRUCTURES)} structures has a volatility point smaller '
            'than that' if below == 0 else
            f'{below} of the {len(STRUCTURES)} structures have a volatility point smaller '
            'than that'),
        knee=_knee(ladders[ladders.question == 'budget ladder']),
        rank=rank, leverage_flips=leverage_flips, leverage_cagr=leverage_cagr,
        leverage_tail=leverage_tail, worst_priced=worst_priced,
        worst_priced_move=float(dearer.loc[worst_priced]),
        high_move=float(dearer.loc[high].mean()), low_move=float(dearer.loc[low].mean()),
        cheapest=cheapest, tier_pairs=tier_pairs, ndx_cheaper=ndx_cheaper,
        ndx_better=ndx_better, held=held.set_index(['candidate', 'target']),
        walked=int(chosen.walk_size.iloc[0]) if len(chosen) else 0,
        pairs=pairs.set_index('pair'), chosen=chosen,
        check_worst=float(check.worst_relative_difference.max()))


HIST_COLUMNS = ['strategy', 'premium_budget', 'moneyness', 'cagr',
                'annualized_volatility', 'max_drawdown', 'mean_delta_exposure',
                'mean_option_weight', 'treasury_notional', 'gross_notional',
                'cohort_10y_min_cagr', 'cohort_20y_min_cagr', 'cohort_20y_median_cagr',
                'cohort_30y_min_cagr', 'cohort_30y_median_cagr', '2000_2002_bust',
                '2008_2009_gfc', '2020_covid', '2022_rates',
                'cagr_per_volatility_point']
MC_COLUMNS = ['strategy', 'premium_budget', 'cagr_p5', 'cagr_p10', 'cagr_p50',
              'cagr_p90', 'cagr_p95', 'terminal_wealth_p5', 'terminal_wealth_p50',
              'terminal_wealth_p95', 'max_drawdown_median', 'max_drawdown_p90',
              'max_drawdown_p95', 'prob_drawdown_worse_than_40',
              'prob_drawdown_worse_than_50', 'prob_drawdown_worse_than_60',
              'prob_drawdown_worse_than_75', 'prob_negative_cagr',
              'prob_below_own_underlying', 'prob_below_sp500',
              'adjacent_lower_risk', 'prob_beat_adjacent_lower_risk']


RATIO_COLUMNS = ('mean_delta_exposure', 'gross_notional', 'moneyness',
                 'tail_points_per_cagr_point', 'drawdown_per_cagr_point')


def _formats(frame, columns=None) -> dict:
    """Format spec per column, decided by dtype rather than by a name list.

    Text, booleans and counts are left alone; everything else is a rate unless
    it is a multiple. Reading this off the frame rather than off a hand-kept
    list is what stops a new column from being printed as a percentage of
    nothing.
    """
    out = {}
    for column in (list(frame.columns) if columns is None else columns):
        if column not in frame:
            continue
        series = frame[column]
        if (not pd.api.types.is_numeric_dtype(series)
                or pd.api.types.is_bool_dtype(series)
                or pd.api.types.is_integer_dtype(series)):
            continue
        if 'terminal_wealth' in column:
            out[column] = ',.1f'
        elif column in RATIO_COLUMNS:
            out[column] = '.2f'
        else:
            out[column] = '.2%'
    return out


def _answers(v, historical, nasdaq, tiers) -> str:
    """The fourteen prespecified questions, each answered from the tables above."""
    robust, groups = v['robust'], v['groups']
    sp_robust, ndx_robust = v['families']['SP500 LEAPS'], v['families']['NDX LEAPS']
    sp_within = v['within']['SP500 LEAPS']
    sp_own = sp_within['robust frontier']
    middle = [n for n in sp_own if .30 < STRUCTURES[n][1] < .50]
    knee = v['knee']
    cheapest = v['cheapest']
    tier_pairs, ndx_cheaper, ndx_better = (v['tier_pairs'], v['ndx_cheaper'],
                                           v['ndx_better'])
    held = v['held']

    def survives(target, column):
        row = held.loc[(cheapest[target].candidate, target)]
        return float(row[column]), float(row['resolution_bar'])

    def cell(name):
        row = v['cells'].loc[name]
        return (f'{name} ({row.median_cagr:.2%} median, {row.p5_cagr:.2%} p5, '
                f'{row.median_max_drawdown:.1%} median drawdown, '
                f'P(DD>60%) {row.prob_drawdown_worse_than_60:.1%})')

    lines = []
    lines.append(
        '**1. What is the robust S&P LEAPS frontier?** Drawn inside the S&P family — '
        'the question an investor who has already chosen the index is asking — it is '
        + ((f'{phrase(sp_own)}: {len(sp_own)} of the {len(SP_STRUCTURES)} S&P cells, '
            'each non-dominated by another S&P cell in the primary run and in at least '
            f'{ROBUST_HOLDS} of the {len(SENSITIVITY_ARMS)} sensitivities, with '
            f'{len(sp_within["conditional frontier"])} conditional and '
            f'{len(sp_within["dominated"])} dominated. ')
           if sp_own else
           'empty: no S&P cell holds its own family\'s frontier in enough '
           'sensitivities to qualify, which is itself the answer. ')
        + ('Against the Nasdaq cells as well, '
           + (f'{phrase(sp_robust)} survive{"s" if len(sp_robust) == 1 else ""}'
              if sp_robust else 'not one of them survives')
           + f' — {len(sp_robust)} of {len(SP_STRUCTURES)} S&P cells.'))

    lines.append(
        '**2. Is there a genuine middle-risk regime between 85/30 and 90/50?** '
        + (f'Yes: {phrase([cell(n) for n in middle])}. '
           if middle else
           'Not on the robust frontier. Every S&P cell with a budget strictly between '
           '30% and 50% is either conditional or dominated, so the middle of the S&P '
           'ladder is a continuum of indistinguishable cells rather than a regime an '
           'investor would choose. ')
        + f'The separation test used is the prespecified tolerance set — '
          f'{CAGR_TOLERANCE:.2%} of CAGR, {DRAWDOWN_TOLERANCE:.0%} of drawdown, '
          f'{PROBABILITY_TOLERANCE:.0%} of tail probability — widened to one volatility '
          'point wherever that is larger.')

    step = v['q1']
    lines.append(
        '**3. Does 85/40 survive?** SPX_85_40 is classified '
        f'*{v["cells"].loc["SPX_85_40", "classification"]}*, holding '
        f'{int(v["cells"].loc["SPX_85_40", "sensitivities_held"])} of '
        f'{len(SENSITIVITY_ARMS)} sensitivities. Against 85/30 it gains '
        f'{step["median_cagr_gained"]:+.2%} of median CAGR and adds '
        f'{step["tail_probability_added"]:+.1%} to P(DD>60%), a price of '
        f'{step["tail_points_per_cagr_point"]:.1f} points of tail per point of CAGR. '
        + ('That gain clears the option-pricing bar of '
           f'{step["resolution_bar"]:.2%}, so the extra return is real. '
           if step['economically_resolved'] else
           'That gain does not clear the option-pricing bar of '
           f'{step["resolution_bar"]:.2%}, so 85/40 and 85/30 are economically '
           'unresolved against each other whatever their classifications say. ')
        + (f'What makes 85/40 dominated is not that the return is illusory but that '
           f'{v["cells"].loc["SPX_85_40", "dominated_by"]} offers comparable growth with '
           'a materially shallower median drawdown; '
           + (f'within the S&P family alone it is beaten by '
              f'{v["cells"].loc["SPX_85_40", "dominated_by_same_family"]}.'
              if v['cells'].loc['SPX_85_40', 'dominated_by_same_family']
              else 'no S&P cell beats it, so what dominates it is the underlying rather '
                   'than the structure.')
           if v['cells'].loc['SPX_85_40', 'dominated_by'] else ''))

    into, out = v['q2_into'], v['q2_out']
    lines.append(
        '**4. Does 90/40 survive?** SPX_90_40 is classified '
        f'*{v["cells"].loc["SPX_90_40", "classification"]}*, holding '
        f'{int(v["cells"].loc["SPX_90_40", "sensitivities_held"])} of '
        f'{len(SENSITIVITY_ARMS)}. As an elbow it is '
        + ('genuine: ' if out['tail_points_per_cagr_point']
           > into['tail_points_per_cagr_point'] else 'not genuine: ')
        + f'the step into it (35% to 40%) costs '
          f'{into["tail_points_per_cagr_point"]:.1f} points of tail per CAGR point and '
          'the step out of it (40% to 45%) costs '
          f'{out["tail_points_per_cagr_point"]:.1f}. '
        + ('The price of the next notch rises there, which is what an elbow is.'
           if out['tail_points_per_cagr_point'] > into['tail_points_per_cagr_point']
           else 'The price of the next notch does not rise there, so the ladder is '
                'smooth through 40% and the elbow is an artifact of where the earlier '
                'work happened to sample.'))

    lines.append(
        '**5. At what premium budget does marginal tail risk begin rising '
        'disproportionately?** '
        + phrase([f'{family.split()[0]} on the notch out of {info["budget"]:.0%} '
                  f'(its price rises {info["rise"]:+.1f} points of tail per CAGR point '
                  'over the notch before it, the largest increment on the ladder)'
                  for family, info in knee.items()])
        + '. The knee is read off the *increment* in the price of return rather than '
          'its level, so no threshold had to be chosen.')

    ndx_frontier = len(ndx_robust)
    beaten_across = [n for n in SP_STRUCTURES
                     if v['cells'].loc[n, 'family_classification'] != 'dominated'
                     and v['cells'].loc[n, 'classification'] == 'dominated']
    lines.append(
        '**6. Does Nasdaq shift the frontier outward?** '
        + (f'Yes. On the joint frontier {ndx_frontier} of the {len(NDX_STRUCTURES)} '
           f'Nasdaq cells are robust against {len(sp_robust)} of {len(SP_STRUCTURES)} '
           f'S&P cells, and {len(beaten_across)} S&P cells that are efficient within '
           'their own family stop being efficient once the Nasdaq cells are in the '
           'comparison'
           if ndx_frontier > len(sp_robust) else
           f'On the joint frontier {ndx_frontier} of the {len(NDX_STRUCTURES)} Nasdaq '
           f'cells are robust against {len(sp_robust)} of {len(SP_STRUCTURES)} S&P '
           f'cells, and {len(beaten_across)} S&P cells lose their own family\'s '
           'frontier only to the Nasdaq')
        + (f'. At {len(ndx_cheaper)} of the {len(tier_pairs)} return tiers both '
           'families reach, the Nasdaq candidate gets there on less option capital'
           if tier_pairs else '. No return tier is reached by both families')
        + (f', and at {len(ndx_better)} of them it is also better on both the fifth '
           'percentile and the tail probability. ' if tier_pairs else '. ')
        + '**This is the finding most dependent on an assumption.** The Nasdaq\'s excess '
          'growth over the S&P is an *input* to every resampled path, not a result of '
          'any of them, and the volatility premium charged to Nasdaq options is imported '
          'unchanged from a less volatile market.')

    reach = cheapest['SPX_90_50']
    lines.append(
        '**7. Can Nasdaq achieve S&P 90/50-like growth with only 20-30% option '
        'capital?** '
        + (f'Yes: {reach.candidate} at a {reach.candidate_budget:.0%} budget comes within '
           f'{abs(reach.median_30y_cagr_gap):.2%} of SPX_90_50 on the median '
           f'{FRONTIER_HORIZON}-year CAGR '
           f'({reach.median_30y_cagr_gap:+.2%}), saving {reach.budget_saved:.0%} of '
           'capital, and is better on '
           f'{sum(1 for k in ("ten_year_p5", "twenty_year_p5", "median_drawdown", "tail_probability", "negative_decade") if float(reach[f"{k}_advantage"]) > 0)} '
           'of the five downside axes.'
           if reach.approaches else
           'No. The nearest Nasdaq cell at a budget of 30% or less is '
           f'{reach.candidate}, {abs(reach.median_30y_cagr_gap):.2%} short of SPX_90_50 '
           f'on the median {FRONTIER_HORIZON}-year CAGR, outside the '
           f'{APPROACH_BAND:.0%} band.'))

    lines.append(
        '**8. Does that advantage survive the dot-com bust?** '
        'The bust is inside the sample, not excluded. '
        + (f'{reach.candidate} lost {reach.candidate_2000_2002:.1%} across 2000-2002 '
           f'against SPX_90_50\'s {reach.target_2000_2002:.1%}, '
           + ('so the Nasdaq structure came through the episode better despite being '
              'written on the index that fell furthest — the Treasury sleeve and the '
              'bounded option leg absorbed it.'
              if reach.candidate_2000_2002 > reach.target_2000_2002 else
              'so the Nasdaq structure came through the episode worse, and the '
              'advantage everywhere else is bought with that episode.')
           if reach.approaches else
           f'{reach.candidate}, the nearest Nasdaq cell at 30% of capital or less, '
           f'lost {reach.candidate_2000_2002:.1%} across 2000-2002 against '
           f'SPX_90_50\'s {reach.target_2000_2002:.1%}. It does not reach the target '
           'on growth, so the episode decides nothing on its own.'))

    candidate = reach.candidate
    block_21_gap, block_bar = survives('SPX_90_50', 'block_21:baseline')
    block_126_gap, _ = survives('SPX_90_50', 'block_126:baseline')
    vol_gap, vol_bar = survives('SPX_90_50', 'iv_premium:iv_6')
    lines.append(
        '**9. Does it survive block-bootstrap reordering?** '
        'The whole comparison is made on reordered history — the primary run is a '
        f'{PRIMARY_BLOCK}-session moving-block bootstrap — and {candidate}\'s gap to '
        'SPX_90_50 is also computed at 21 and 126 sessions. At 21 it is '
        f'{block_21_gap:+.2%} against a resolution bar of {block_bar:.2%}; at 126 it is '
        f'{block_126_gap:+.2%}. '
        + ('It holds under both.' if min(block_21_gap, block_126_gap) >= -block_bar
           else 'It does not hold under both.'))

    lines.append(
        '**10. Does it survive +6 vol-point option pricing?** '
        f'At a six-point loading the gap is {vol_gap:+.2%} against the same '
        f'{vol_bar:.2%} bar. '
        + ('It holds. ' if vol_gap >= -vol_bar else 'It does not hold. ')
        + 'The Nasdaq structures are charged the S&P\'s loading in absolute volatility '
          'points, which on a more volatile underlying is proportionally a smaller '
          'charge; this arm is the closest thing here to a correction for that, and it '
          'is not a measurement of real Nasdaq option prices.')

    lines.append(
        '**11. Which strategies remain efficient across most sensitivities?** '
        + (f'{phrase(robust)}. ' if robust else 'None. ')
        + f'These are the cells that are non-dominated in the primary specification and '
          f'in at least {ROBUST_HOLDS} of the {len(SENSITIVITY_ARMS)} sensitivities.')

    conditional = groups['conditional frontier']
    worst = sorted(conditional, key=lambda n: v['cells'].loc[n, 'sensitivities_held'])[:4]
    lines.append(
        '**12. Which apparent winners are conditional on favourable assumptions?** '
        + (phrase([f'{n} (holds {int(v["cells"].loc[n, "sensitivities_held"])} of '
                   f'{len(SENSITIVITY_ARMS)}, failing '
                   f'{v["cells"].loc[n, "failed_sensitivities"].replace(";", ", ")})'
                   for n in worst]) + '.' if worst else
           'None: every cell that is efficient in the primary specification stays '
           'efficient in most sensitivities.'))

    lines.append(
        '**13. Does 1.25x Treasury leverage change any important ranking?** '
        f'On the same {int(arm_frame(v["sensitivities"], "treasury_leverage", "tsy_125").paths.iloc[0]):,} '
        'worlds, a quarter-turn of duration moves median CAGR by '
        f'{v["leverage_cagr"]:+.2%} on average and P(DD>60%) by '
        f'{v["leverage_tail"]:+.1%}, and the rank correlation of median CAGR across the '
        f'{len(STRUCTURES)} cells is {v["rank"]:.4f}. '
        + (f'{len(v["leverage_flips"])} of the {len(STRUCTURES)} cells change frontier '
           f'membership ({phrase(v["leverage_flips"])}). ' if v['leverage_flips'] else
           'No cell changes frontier membership. ')
        + ('No: the cells keep their order by return, and none crosses the frontier '
           'boundary, so the sleeve moves the whole frontier rather than reordering it.'
           if v['rank'] > .99 and not v['leverage_flips'] else
           'The cells keep their order by return, but membership at the boundary is not '
           'stable to it, so the sleeve is a dial that changes which cells are called '
           'efficient without changing which earn more.'
           if v['rank'] > .99 else
           'The ordering itself is not preserved, so the sleeve is not a neutral dial '
           'here.'))

    chosen = v['chosen']
    lines.append(
        '**14. Which 3-5 portfolios represent genuinely distinct risk regimes?** '
        + (phrase([f'**{row.regime}** — {row.strategy} '
                   f'({row.median_cagr:.2%} median, {row.p5_cagr:.2%} p5, '
                   f'{row.median_max_drawdown:.1%} median drawdown)'
                   for _, row in chosen.iterrows()])
           + f'. Each holds at least {ROBUST_HOLDS} of {len(SENSITIVITY_ARMS)} '
             'sensitivities and is separated from the one below it on at least '
             f'{REGIME_AXES} of the four frontier axes beyond the tolerances'
             + (f'; the separation walk left {v["walked"]} such cells and the menu is '
                f'the {len(chosen)} of them spread most evenly along the risk axis, both '
                'ends kept.' if v['walked'] > len(chosen) else '.')
           if len(chosen) else
           'None can be named: the robust frontier is empty, so there is no set of '
           'cells this run is entitled to call distinct regimes.'))
    return '\n\n'.join(lines)


def report(reports: Path, inputs, historical, check, primary, sensitivities,
           classification, hypotheses, pairs, nasdaq, held, tiers, chosen, paths):
    """Write the narrative from the numbers, never alongside them."""
    v = _narrative(historical, check, primary, sensitivities, classification, hypotheses,
                   pairs, nasdaq, held, tiers, chosen)
    thirty = v['thirty']
    lattice = historical[historical.strategy.isin(STRUCTURES)]
    indices = historical[~historical.strategy.isin(STRUCTURES)]
    ladders = hypotheses[hypotheses.question == 'budget ladder']
    steps = hypotheses[hypotheses.question == 'S&P steps']
    mc = thirty.reset_index()
    order = list(STRUCTURES)
    mc = mc.set_index('strategy').loc[order + list(INDEX_ROWS)].reset_index()

    short_horizons = primary[primary.horizon_years != FRONTIER_HORIZON].pivot_table(
        index='strategy', columns='horizon_years',
        values=['cagr_p5', 'cagr_p50', 'prob_negative_cagr'])
    short_horizons.columns = [f'{a}_{b}y' for a, b in short_horizons.columns]
    short_horizons = short_horizons.loc[order].reset_index()

    iv_movement = classification[['strategy', 'premium_budget',
                                  'median_cagr_move_when_cheaper',
                                  'median_cagr_move_when_dearer',
                                  'p5_cagr_move_when_cheaper',
                                  'p5_cagr_move_when_dearer']]
    class_columns = ['strategy', 'family', 'premium_budget', 'moneyness',
                     'classification', 'family_classification', 'sensitivities_held',
                     'family_sensitivities_held', 'neighbour_support',
                     'median_cagr', 'p5_cagr', 'median_max_drawdown',
                     'prob_drawdown_worse_than_60', 'mean_delta_exposure',
                     'dominated_by', 'dominated_by_same_family',
                     'failed_sensitivities']

    arms = sensitivities.groupby(
        ['case', 'arm', 'block_days', 'iv_premium', 'roll', 'treasury_leverage', 'paths'],
        as_index=False).size().rename(columns={'size': 'rows'})
    arms_table = markdown_table(
        arms, list(arms.columns), {'iv_premium': '.0%', 'treasury_leverage': '.2f'})
    step_columns = ['step', 'note', 'median_cagr_gained', 'p5_cagr_gained',
                    'drawdown_deepened', 'tail_probability_added',
                    'tail_points_per_cagr_point', 'resolution_bar',
                    'economically_resolved']
    signed = {'median_cagr_gained': '+.2%', 'p5_cagr_gained': '+.2%',
              'drawdown_deepened': '+.2%', 'tail_probability_added': '+.2%',
              'tail_points_per_cagr_point': '.1f', 'resolution_bar': '.2%'}
    steps_table = markdown_table(steps, step_columns, signed)
    ladder_columns = ['step', 'family', 'median_cagr_gained', 'p5_cagr_gained',
                      'drawdown_deepened', 'tail_probability_added',
                      'tail_points_per_cagr_point', 'economically_resolved']
    ladders_table = markdown_table(ladders, ladder_columns, signed)
    pair_columns = ['pair', 'note', 'median_cagr_gap', 'p5_cagr_gap',
                    'median_max_drawdown_gap', 'prob_drawdown_worse_than_60_gap',
                    'cagr_resolution_bar', 'axes_separated', 'separating_axes']
    pairs_table = markdown_table(pairs, pair_columns, {
        'median_cagr_gap': '+.2%', 'p5_cagr_gap': '+.2%',
        'median_max_drawdown_gap': '+.2%', 'prob_drawdown_worse_than_60_gap': '+.2%',
        'cagr_resolution_bar': '.2%'})
    shown = (nasdaq[nasdaq.approaches] if nasdaq.approaches.any()
             else nasdaq.nlargest(9, 'median_30y_cagr_gap'))
    ndx_columns = ['candidate', 'target', 'budget_saved', 'median_30y_cagr_gap',
                   'resolution_bar', 'ten_year_p5_advantage',
                   'twenty_year_p5_advantage', 'median_drawdown_advantage',
                   'tail_probability_advantage', 'negative_decade_advantage',
                   'candidate_2000_2002', 'target_2000_2002']
    ndx_table = markdown_table(shown, ndx_columns, dict(
        _formats(shown, ndx_columns), budget_saved='.0%',
        **{c: '+.2%' for c in ndx_columns if c.endswith(('_gap', '_advantage'))}))
    arm_columns = [f'{case}:{arm}' for case, arm in SENSITIVITY_ARMS]
    held_table = markdown_table(
        held, ['candidate', 'target', 'resolution_bar'] + arm_columns + ['arms_held'],
        dict({c: '+.2%' for c in arm_columns}, resolution_bar='.2%'))
    tier_columns = ['return_tier', 'family', 'strategy', 'within_band',
                    'distance_from_tier', 'premium_budget', 'mean_delta_exposure',
                    'median_30y_cagr', 'p5_30y_cagr', 'median_max_drawdown',
                    'prob_drawdown_worse_than_60', 'prob_negative_decade',
                    'event_2000_2002', 'event_2022']
    tiers_table = markdown_table(tiers, tier_columns, _formats(tiers, tier_columns))
    regime_columns = ['regime', 'strategy', 'underlying', 'moneyness', 'premium_budget',
                      'median_cagr', 'p5_cagr', 'median_max_drawdown',
                      'prob_drawdown_worse_than_60', 'mean_delta_exposure',
                      'sensitivities_held', 'neighbour_support']
    regime_table = (markdown_table(chosen, regime_columns, _formats(chosen, regime_columns))
                    if len(chosen) else
                    '_The robust frontier is empty, so no menu is offered._')

    text = f"""# The LEAPS efficient frontier, and which of it survives being doubted

Generated by `letf.leaps_frontier`. Window {inputs.ix[0].date()} to {inputs.ix[-1].date()}, {len(inputs.ix):,} sessions, on the calendar, financing and option assumptions of `reports/hedge_alternatives_results.md`.

Earlier work here picked three or four option structures by hand and asked whether
they survived. This asks where the frontier *is*. A prespecified regular lattice —
{len(SP_STRUCTURES)} S&P structures ({len(SP_MONEYNESS)} strikes x {len(SP_BUDGETS)} premium budgets, plus the conservative
anchor {ANCHOR[0]}) and {len(NDX_STRUCTURES)} Nasdaq structures ({len(NDX_MONEYNESS)} strikes x {len(NDX_BUDGETS)} budgets) — is carried
through one primary bootstrap and seven sensitivities, and a cell is called
efficient only if it is still efficient after the assumptions that produced it
are moved. No intermediate cell was added at any point, however well a
neighbour performed.

Everything else is inherited and fixed: roughly {MATURITY_YEARS:.0f}-year calls, the annual roll on
listed expiries, residual capital in long Treasuries at {BASE_LEVERAGE:.2f}x, a trailing
realized-volatility proxy over the option's own horizon plus {IV_PREMIUM:.0%} of volatility
premium, the same Black-Scholes framework and the same {OPTION_SPREAD_BPS:.0f}bp spread. The premium
budget is the control variable; nothing targets a delta.

## The two uncertainties are not the same size

*Strategy uncertainty* is what the lattice and the bootstrap measure. *Model
uncertainty* is the price of the options, and this repository holds no long-dated
option price history, no skew surface and no bid/ask record for either index.

Each structure therefore carries its own measured CAGR-per-volatility-point,
and it is not a constant across the lattice: it runs from {v['unit_low']:.2%} for
{v['unit_low_name']} to {v['unit_high']:.2%} for {v['unit_high_name']}, so one volatility point of
doubt is worth {v['unit_high'] / max(v['unit_low'], 1e-9):.1f} times as much CAGR at the expensive end of the
lattice as at the cheap end. The prespecified comparison tolerance is
{CAGR_TOLERANCE:.2%} of CAGR, and {v['tolerance_phrase']}, so **the binding limit on what can
be resolved is the option-price assumption rather than the Monte Carlo**. Every
pairwise verdict here widens its bar to whichever is larger.

## The seven cells this repository has already published

Same rule, same window, same inputs, recomputed and compared against the
committed file rather than copied from it. Worst relative disagreement across
{len(COMMITTED_COLUMNS)} columns and {len(COMMITTED)} cells: {v['check_worst']:.2e}.

{markdown_table(check, ['strategy', 'published_as', 'worst_column', 'worst_relative_difference'], {'worst_relative_difference': '.2e'})}

The overlapping *Monte Carlo* rows are deliberately not reused: they were run at
2,000 paths and these are run at {paths:,}, and putting the two in one table would
make the table say something it does not mean.

## Historical record, one realized path

{markdown_table(indices, [c for c in HIST_COLUMNS if c in indices], _formats(indices, HIST_COLUMNS))}

{markdown_table(lattice, HIST_COLUMNS, _formats(lattice, HIST_COLUMNS))}

Nasdaq total returns are a price-only proxy through {PROXY_THROUGH}, grossed up with
an assumed zero dividend yield. Every thirty-year window in this sample opens
inside that era, so `cohort_30y_*` for a Nasdaq row is partly a statement about
proxy data; `cohort_20y_min_cagr_post_proxy` in the CSV is the same statistic
restricted to windows entering after the splice. The 1987 column lies entirely
inside the proxy era. The dot-com bust does not, and it is kept.

## Primary Monte Carlo

{paths:,} paths on the joint {PRIMARY_BLOCK}-session moving-block bootstrap, at {phrase([f'{y}' for y in MC_HORIZONS])} years,
one fixed seed, **the same simulated worlds for every strategy**. Whole daily
rows are drawn, so the two indices, the leveraged funds, the Treasury sleeve, the
funding rate and the dividend levels keep the relationships they had on the day;
what is destroyed is the order of days beyond one block. Every comparison against
a rival below is therefore paired path by path.

Percentiles marked `p90`/`p95` on drawdown are *severity* percentiles: the
drawdown only a tenth, or a twentieth, of paths are worse than.

### {FRONTIER_HORIZON}-year horizon

{markdown_table(mc, MC_COLUMNS, _formats(mc, MC_COLUMNS))}

### 10- and 20-year horizons

{markdown_table(short_horizons, list(short_horizons.columns), _formats(short_horizons))}

## The two frontiers

Both are computed over the {len(STRUCTURES)} lattice cells alone. The unlevered indices are
drawn on the figures for reference but are excluded from the domination test:
letting an unlevered index sit at the low-risk corner of a LEAPS frontier would
define the question away.

- **Growth frontier** — median {FRONTIER_HORIZON}-year CAGR against median max drawdown: `reports/leaps_frontier_growth.png`, panel A. Panel B is what each premium budget buys in delta-equivalent equity.
- **Robust-growth frontier** — fifth-percentile {FRONTIER_HORIZON}-year CAGR against P(max drawdown worse than 60%): `reports/leaps_frontier_robust.png`, panel A. Panel B is the median against the bad case.

Domination is epsilon-domination on the prespecified tolerances: {CAGR_TOLERANCE:.2%} of CAGR,
{DRAWDOWN_TOLERANCE:.0%} of drawdown, {PROBABILITY_TOLERANCE:.0%} of tail probability. Two cells inside those are the
same cell.

## Classification

A cell is **robust** if it is non-dominated in the primary specification and in at
least {ROBUST_HOLDS} of the {len(SENSITIVITY_ARMS)} sensitivities, **conditional** if it is efficient at
baseline but not in most sensitivities, and **dominated** otherwise. Frontier
membership is recomputed inside each sensitivity rather than inferred by
differencing against the primary run: the candidate is asked the same question
seven more times.

Two classifications, because there are two questions. `classification` draws the
frontier over all {len(STRUCTURES)} cells and answers whether the choice of underlying
matters; `family_classification` draws it inside the cell's own family and
answers which structures are efficient given that the index has already been
chosen. A cell can be efficient in its family and dominated jointly, and that
gap is exactly what the Nasdaq comparison is about.

`neighbour_support` is the fraction of a cell's own lattice neighbours — one
notch in strike or budget — that are themselves non-dominated. A single efficient
cell surrounded by dominated ones is a numerical winner, not a regime. The
off-grid anchor {ANCHOR[0]} has no lattice neighbours and is left blank.

{markdown_table(classification[class_columns], class_columns, _formats(classification, class_columns))}

## Sensitivities

Seven prespecified arms, all at the {FRONTIER_HORIZON}-year horizon, which is the horizon the
frontier is defined at. Within a case every arm sees the *same* resampled worlds,
so an option-price or leverage or roll sensitivity is a paired within-world
difference; only the block-length arms cannot be paired, because the block length
is what selects the days. A matched {PRIMARY_BLOCK}-session arm at the same path count is run
beside them so a block result is never compared against a different path count.

### Option price: does the frontier survive plausible pricing error?

Movement per three volatility points, on one set of {arm_frame(sensitivities, 'iv_premium', 'iv_3').paths.iloc[0]:,.0f} shared worlds.

{markdown_table(iv_movement.sort_values('premium_budget'), list(iv_movement.columns), _formats(iv_movement))}

Averaged over the lattice, three points dearer costs a {IV_PREMIUMS[-1]:.0%}-loading portfolio
{v['high_move']:.2%} of median CAGR at budgets of 45% or more against {v['low_move']:.2%} at budgets of
25% or less. The worst-affected cell is {v['worst_priced']} at {v['worst_priced_move']:.2%}.

### Everything else

{arms_table}

Full per-strategy summaries for every arm are in
`reports/leaps_frontier_sensitivities.csv`.

## The five prespecified S&P questions

{steps_table}

{pairs_table}

**1. Does 85/40 remain superior to 85/30 on return gained per tail risk?** It buys
{v['q1']['median_cagr_gained']:+.2%} of median CAGR for {v['q1']['tail_probability_added']:+.2%} of P(DD>60%),
{v['q1']['tail_points_per_cagr_point']:.1f} points of tail per CAGR point, against a resolution bar of
{v['q1']['resolution_bar']:.2%} — {'a resolved difference' if v['q1']['economically_resolved'] else 'inside the option-pricing noise that produced both numbers'}.

**2. Does 90/40 represent a genuine frontier elbow?** Into it, {v['q2_into']['tail_points_per_cagr_point']:.1f} points of
tail per CAGR point; out of it, {v['q2_out']['tail_points_per_cagr_point']:.1f}.

**3. Does 90/40 to 90/50 produce a disproportionate increase in tail risk?** That
move costs {v['q3']['tail_points_per_cagr_point']:.1f} points of tail per CAGR point against a lattice-wide
mean of {ladders.tail_points_per_cagr_point.mean():.1f} for a single notch.

**4. Is 95/40 meaningfully different from 90/40?** Separated on
{int(v['pairs'].loc['SPX_95_40 vs SPX_90_40', 'axes_separated'])} of the four frontier axes.

**5. Are 90/50 and 95/50 genuinely distinct?** Separated on
{int(v['pairs'].loc['SPX_95_50 vs SPX_90_50', 'axes_separated'])} of the four.

### Budget ladders

{ladders_table}

## The Nasdaq hypothesis

Can 20-30% of capital on the Nasdaq buy what 40-50% buys on the S&P? Nine
candidates against three S&P targets, on the same worlds.

{ndx_table}

Does each advantage survive every arm? The gap in median {FRONTIER_HORIZON}-year CAGR under each,
against the same resolution bar:

{held_table}

## Cross-underlying capital efficiency

For each approximate return level, the lowest-tail candidate each family offers.
Nothing is optimized to hit a tier; `distance_from_tier` shows how far the nearest
candidate actually is, and `within_band` says whether the family reaches it at all.

{tiers_table}

## The regime menu

{regime_table}

## Conclusions

{_answers(v, historical, nasdaq, tiers)}

## What would change these answers

Nothing here observes an option price. The volatility every structure is charged
is a trailing realized proxy plus a flat loading, and the sensitivity arms move
that loading by three points in each direction because three points is a plausible
error, not because it is a measured one. A real skew surface would change the
level of every number above and could change the ordering of any pair whose gap
is inside its resolution bar — which, given the size of the volatility unit at the
expensive end of the lattice, is most adjacent pairs.

The bootstrap resamples one history. It cannot produce a crash worse than the
ones in the record, a bond regime unlike the one observed, or a Nasdaq that did
not out-grow the S&P over this window. That growth gap is an input to every path,
so no amount of resampling can test it, and it is the single assumption the
cross-underlying result rests on.
"""
    (reports / 'leaps_frontier_results.md').write_text(text)


if __name__ == '__main__':
    main()
