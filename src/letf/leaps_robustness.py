"""Is the annual roll a real design choice, and does the ranking survive reordering?

`letf.hedge_alternatives` found that fixed-budget, moderately in-the-money
LEAPS held against a Treasury sleeve look like attractive capital-efficient
equity exposure. Two objections to that finding are not answered there, and
both are answered here.

The first is a free-parameter objection. That module buys roughly two-year
calls and rolls when about a year is left, and nothing was ever varied. One
year is a convention, not a result. **Part A** holds every other choice fixed
and moves only the roll interval, across 6, 9, 12, 13 and 18 months, mapping
each desired roll onto an expiry that is actually listed and reporting the
maturity realized rather than the maturity intended.

The second is a sequence objection, and it is the more serious one. Every
number in this repository comes from a single realized path: one 1987, one
dot-com bust, one 2008, one 2020, one 2022, in that order and no other, with
the whole of it sitting inside one long decline in bond yields. A ranking of
strategies computed on that path might be a property of the strategies or a
property of the ordering. **Part B** resamples the historical record in blocks
that keep volatility clustering and crash persistence intact, rebuilds each
strategy inside every resampled path, and asks whether the ranking holds.

**What the Monte Carlo is not.** It is not a forecast, and no number in it
should be read as an expected return. It resamples the same 40 years, so it
cannot produce a crash worse than 1987 or a bond regime unlike the one
observed; the distribution it makes is a distribution of *orderings* of the
history we have, not a distribution of futures. Its only claim is comparative:
if a structure's advantage over its rivals survives thousands of reorderings,
that advantage is less likely to be an artifact of one sequence; if it does
not, it was.

Everything `letf.options` says about modelled premia applies unchanged and
compounds here — a bootstrap of an assumption is still an assumption. The
historical realized-path analysis in `letf.hedge_alternatives` remains the
primary evidence, and this module is a robustness layer over it.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
import platform

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter
import numpy as np
import pandas as pd
import scipy

from .analysis import CASH, load_inputs
from .cohorts import cohort_cagrs, cohort_windows, nav_path
from .falsification import load_price_signals
from .hedge_alternatives import (CRASHES, EQUITY, EXPIRY_MONTHS, IV_PREMIUM, LAG,
                                 MATURITY_YEARS, OPTION_SPREAD_BPS, SMA_DAYS,
                                 SWITCH_COST_BPS, TMF, TREASURY, UPRO, cagr,
                                 comparison_window, markdown_table, max_drawdown,
                                 option_inputs)
from .model import portfolio
from .options import (LeapsRule, implied_volatility_proxy, leaps_arrays, roll_schedule,
                      simulate_leaps_arrays, trailing_riskfree)
from .provenance import FLOAT_FORMAT, sha, source_hashes, stable_floats
from .signals import level_position
from .strategy import select_returns, switching_costs

# The three structures the historical grid singled out. They are inputs to this
# module, not outputs of it: nothing below searches strike or budget, because a
# robustness test that re-optimizes on each resampled path measures the search,
# not the structure.
STRUCTURES = {'LEAPS_80_25_TREASURY': (.80, .25),
              'LEAPS_85_30_TREASURY': (.85, .30),
              'LEAPS_95_50_TREASURY': (.95, .50)}

# Roll interval in years, as the nominal gap between purchases. The rule is
# expressed to the simulator as time *remaining* at the roll, so a 2-year
# contract rolled after 12 months is one with 12 months left.
ROLL_INTERVALS = {'6m': .5, '9m': .75, '12m': 1., '13m': 13 / 12, '18m': 1.5}
BENCHMARK_INTERVAL = '12m'
# The benchmark plus one interval either side, fixed before any path was drawn.
# The neighbours are chosen for what they can distinguish, not for what they
# earn: 13m is the same schedule as 12m on a real expiry calendar and carries no
# information, so the nearest *distinct* shorter interval is 9m, and 18m is the
# only longer one Part A offers. Picking by realized CAGR would have chosen 18m
# too, which is why the reason is recorded here rather than inferred.
MONTE_CARLO_INTERVALS = ('9m', '12m', '18m')

BLOCK_LENGTHS = (21, 63, 126)
PRIMARY_BLOCK = 63
SEED = 20260907
PRIMARY_PATHS = 5000
SENSITIVITY_PATHS = 2000
HORIZONS = (20, 30)
# Long enough for the two-year volatility estimate and the 200-day average to be
# fully warm before the first option is bought, so no path is evaluated on a
# back-filled input. Discarded afterwards.
WARMUP_SESSIONS = 504
DRAWDOWN_THRESHOLDS = (.40, .50, .60, .75)
# A session is "after a major loss" once the path is 30% below its own high.
MAJOR_LOSS = .30
RECOVERY_SESSIONS = 252
# An option leg that is worth less than a quarter of the premium paid by the
# time it is replaced has stopped being leverage and become a lottery ticket.
DEPLETED = .25
POOR_ROLL = .50
# "Materially underexposed" has to be measured against the structure's own
# exposure, not against 1x. The 80/25 structure runs a delta near 0.9 by design,
# so a fixed 1x threshold would score it underexposed on almost every path and
# would be reporting its design rather than any drift. The reference is the
# path's own mean delta exposure.
UNDEREXPOSED = .75
PERCENTILES = (5, 10, 50, 90, 95)

MC_STRATEGIES = ('SP500_1X', 'UPRO_SMA_TO_SP500', 'UPRO60_TMF40') + tuple(STRUCTURES)


def leaps_rule(moneyness, budget, interval_years):
    """The prespecified rule, with only the roll interval varying."""
    return LeapsRule(moneyness=moneyness, premium_budget=budget,
                     maturity_years=MATURITY_YEARS,
                     roll_at_years=MATURITY_YEARS - interval_years,
                     iv_premium=IV_PREMIUM, spread_bps=OPTION_SPREAD_BPS,
                     expiry_months=EXPIRY_MONTHS)


@dataclass(frozen=True)
class Inputs:
    """Everything both parts read, loaded once."""
    daily: pd.DataFrame
    config: dict
    calendar: pd.DatetimeIndex
    price: pd.Series
    ix: pd.DatetimeIndex
    spot: pd.Series
    dividend: pd.Series
    riskfree: pd.Series
    vol: pd.Series


def load(root: Path) -> Inputs:
    daily, config = load_inputs(root, offline=True)
    calendar = daily.index
    signals = load_price_signals(root, config, offline=True)
    ix = comparison_window(daily, calendar, signals['SP500'], signals['NASDAQ100'])
    spot, dividend, riskfree, vol = option_inputs(daily, signals['SP500'], ix, calendar)
    return Inputs(daily, config, calendar, signals['SP500'], ix, spot,
                  dividend, riskfree, vol)


def historical_leaps(inputs: Inputs, rule: LeapsRule, safe=TREASURY):
    """One rolling-call structure on the realized path. Wealth, exposure, weight, ledger."""
    closes = inputs.spot.index
    arrays = leaps_arrays(inputs.spot, inputs.daily.loc[inputs.ix, safe],
                          inputs.dividend, inputs.riskfree, inputs.vol)
    navs, exposures, weights, rolls = simulate_leaps_arrays(
        *arrays, roll_schedule(closes, rule))
    return (pd.Series(navs, index=closes, name='wealth'), exposures, weights,
            pd.DataFrame(rolls))


def roll_frequency_row(name, label, interval, inputs, moneyness, budget):
    """One structure at one roll interval, on the realized path.

    The cost of trading is *measured* rather than approximated: the same
    structure is run again with the option spread set to zero and the
    difference in CAGR is the drag. That is exact, and it avoids the usual
    sleight of hand of quoting a turnover figure and leaving the reader to
    guess what it costs.
    """
    rule = leaps_rule(moneyness, budget, interval)
    nav, exposures, weights, rolls = historical_leaps(inputs, rule)
    returns = nav.pct_change().dropna()
    free = historical_leaps(inputs, replace(rule, spread_bps=0.))[0]
    path = nav_path(returns, inputs.calendar)
    ten, twenty, thirty = (cohort_cagrs(path, h) for h in (10, 20, 30))
    years = (returns.index[-1] - returns.index[0]).days / 365.25
    # The last contract is held to the end of the sample rather than rolled, so
    # it has no realized holding period and no sale; averaging it in would
    # understate both.
    closed = rolls.iloc[:-1]
    row = dict(
        structure=name, roll=label, nominal_interval_years=interval,
        moneyness=moneyness, premium_budget=budget,
        cagr=cagr(returns), terminal_multiple=float((1 + returns).prod()),
        annualized_volatility=float(returns.std(ddof=1) * np.sqrt(252)),
        max_drawdown=max_drawdown(returns),
        mean_delta_exposure=float(exposures.mean()),
        # Exposure drift is what a shorter roll is bought to control. A fixed
        # premium budget re-strikes the delta only at rolls, so between them the
        # realized exposure wanders — and it wanders down after a fall, which is
        # when an investor would least want it to.
        delta_exposure_sd=float(exposures.std(ddof=1)),
        delta_exposure_p5=float(np.percentile(exposures, 5)),
        mean_option_weight=float(weights.mean()),
        rolls=int(len(rolls)),
        mean_entry_years=float(rolls.entry_years.mean()),
        min_entry_years=float(rolls.entry_years.min()),
        max_entry_years=float(rolls.entry_years.max()),
        mean_exit_years=float(closed.exit_years.mean()),
        mean_held_years=float(closed.held_years.mean()),
        option_turnover_per_year=float((1 - rolls.safe_weight).sum() / years),
        cost_drag_bps=float((cagr(free.pct_change().dropna()) - cagr(returns)) * 10000),
        cohort_10y_min_cagr=float(ten.min()),
        cohort_20y_min_cagr=float(twenty.min()),
        cohort_20y_median_cagr=float(np.median(twenty)),
        cohort_20y_sd_cagr=float(np.std(twenty, ddof=1)),
        cohort_30y_min_cagr=float(thirty.min()),
        cohort_30y_median_cagr=float(np.median(thirty)))
    for event, (start, end) in CRASHES.items():
        row[event] = float((1 + returns.loc[start:end]).prod() - 1)
    return row


def roll_frequency_table(inputs: Inputs) -> pd.DataFrame:
    """Part A: vary the roll interval and nothing else."""
    return pd.DataFrame([roll_frequency_row(name, label, interval, inputs, *spec)
                         for name, spec in STRUCTURES.items()
                         for label, interval in ROLL_INTERVALS.items()])


DISPERSION_COLUMNS = ('cagr', 'max_drawdown', 'cohort_20y_min_cagr',
                      'cohort_30y_min_cagr', 'mean_delta_exposure',
                      'delta_exposure_sd', 'cost_drag_bps')


def model_uncertainty(inputs: Inputs, shift=.01) -> pd.DataFrame:
    """How far the option-price assumption moves each structure’s CAGR.

    This is the yardstick the roll-interval comparison is read against, and it
    exists because "materially different" needs a scale that is not the reader’s
    intuition. The premium the options are priced at is the one input this
    repository cannot check, so the natural unit of doubt is a volatility point.
    A difference between two roll intervals smaller than the difference one
    volatility point makes is not a finding about roll intervals; it is inside
    the noise of the pricing assumption that produced both numbers.
    """
    closes = inputs.spot.index
    price_returns = inputs.spot.pct_change().fillna(0.)
    rows = []
    for name, (moneyness, budget) in STRUCTURES.items():
        rule = leaps_rule(moneyness, budget, ROLL_INTERVALS[BENCHMARK_INTERVAL])
        rates = {}
        for premium in (IV_PREMIUM - shift, IV_PREMIUM, IV_PREMIUM + shift):
            vol = implied_volatility_proxy(price_returns, premium, MATURITY_YEARS)
            shifted = replace(rule, iv_premium=premium)
            arrays = leaps_arrays(inputs.spot, inputs.daily.loc[inputs.ix, TREASURY],
                                  inputs.dividend, inputs.riskfree, vol)
            navs = simulate_leaps_arrays(*arrays, roll_schedule(closes, shifted),
                                         ledger=False)[0]
            rates[premium] = cagr(pd.Series(navs, index=closes).pct_change().dropna())
        rows.append(dict(structure=name, volatility_shift=shift,
                         cagr_at_base=rates[IV_PREMIUM],
                         cagr_one_point_cheaper=rates[IV_PREMIUM - shift],
                         cagr_one_point_dearer=rates[IV_PREMIUM + shift],
                         cagr_per_volatility_point=float(
                             (rates[IV_PREMIUM - shift] - rates[IV_PREMIUM + shift]) / 2)))
    return pd.DataFrame(rows)


def roll_dispersion(frame: pd.DataFrame) -> pd.DataFrame:
    """How much each structure's answer moves when only the roll interval moves.

    `spread` is max minus min across the five intervals. It is the number that
    decides the falsification question: a structure whose 40-year CAGR moves by
    less across every roll interval than a plausible error in the option-price
    assumption moves it is not sensitive to the roll interval in any sense a
    reader should act on.
    """
    rows = []
    for structure, group in frame.groupby('structure', sort=False):
        row = {'structure': structure}
        for column in DISPERSION_COLUMNS:
            values = group[column].to_numpy()
            row[f'{column}_min'] = float(values.min())
            row[f'{column}_max'] = float(values.max())
            row[f'{column}_spread'] = float(values.max() - values.min())
            row[f'{column}_sd'] = float(values.std(ddof=1))
            row[f'{column}_at_benchmark'] = float(
                group.loc[group.roll == BENCHMARK_INTERVAL, column].iloc[0])
        rows.append(row)
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------
# Part B: joint moving-block bootstrap
# ----------------------------------------------------------------------------

POOL_COLUMNS = ('price_return', 'equity_total', 'treasury', 'cash', 'upro', 'tmf',
                'dividend_yield')
PRICE, EQUITY_TR, TREASURY_R, CASH_R, UPRO_R, TMF_R, YIELD = range(len(POOL_COLUMNS))


def bootstrap_pool(inputs: Inputs) -> np.ndarray:
    """The daily rows the bootstrap draws from, resampled as whole rows.

    Sampling rows rather than columns is the point of the exercise. Every
    contemporaneous relationship the historical record contains — that the
    leveraged fund falls three times as far on the day the index falls, that
    long Treasuries usually rallied on those days and in 2022 did not, that the
    bill rate was high exactly when the dividend yield was — survives, because
    a draw takes the whole day or none of it. What is destroyed, deliberately,
    is the *order* of the days beyond one block.

    The dividend yield enters as a level rather than being recomputed from the
    resampled total-return and price series. Recomputing it would be defensible
    but would import this repository's pre-1988 splice problem into every path:
    the fund proxy that stands in for the early total-return index drives the
    implied yield negative, and `letf.options.trailing_dividend_yield` repairs
    that on the realized calendar in a way that has no meaning once the days are
    shuffled. Resampling the repaired level keeps the fix and keeps the yield
    joint with the returns of the same day.
    """
    d = inputs.daily.loc[inputs.ix]
    price_return = inputs.spot.pct_change().dropna()
    if not price_return.index.equals(inputs.ix):
        raise ValueError('Price returns are not aligned to the comparison window')
    pool = np.column_stack([price_return, d[EQUITY], d[TREASURY], d[CASH], d[UPRO],
                            d[TMF], inputs.dividend.loc[inputs.ix]])
    if not np.isfinite(pool).all() or (pool[:, :TMF_R + 1] <= -1).any():
        raise ValueError('Bootstrap pool has non-finite or terminating returns')
    return pool


def block_draw(rng, pool_rows: int, needed: int, block: int) -> np.ndarray:
    """Positional indices of one moving-block resample.

    Overlapping blocks, drawn with replacement from every possible start, which
    is the standard moving-block scheme. The last block is truncated rather than
    wrapped: wrapping would join the end of the sample to its beginning and
    manufacture a transition that never happened.
    """
    if block > pool_rows:
        raise ValueError('Block longer than the history it resamples')
    count = -(-needed // block)
    starts = rng.integers(0, pool_rows - block + 1, count)
    return (starts[:, None] + np.arange(block)).ravel()[:needed]


@dataclass(frozen=True)
class Horizon:
    """One simulated horizon: its calendar, its roll schedules, its cohort windows.

    All three depend on the calendar and the rules alone, so they are built once
    and shared by every path. That the roll schedule is path-independent is not
    an optimization detail — it is why a rolled-option strategy can be simulated
    thousands of times at all, and why an investor could follow one.
    """
    years: int
    calendar: pd.DatetimeIndex
    closes: pd.DatetimeIndex
    days: np.ndarray
    quarters: np.ndarray
    schedules: dict
    cohorts: dict
    elapsed_days: float


def build_horizon(inputs: Inputs, years: int, intervals) -> Horizon:
    closes_all = inputs.spot.index
    entry = WARMUP_SESSIONS
    end = closes_all.searchsorted(closes_all[entry] + pd.DateOffset(years=years))
    if end >= len(closes_all):
        raise ValueError('Comparison window is too short for this horizon plus warm-up')
    calendar = closes_all[:end + 1]
    closes = calendar[entry:]
    schedules = {(name, label): roll_schedule(closes, leaps_rule(*STRUCTURES[name],
                                                                 ROLL_INTERVALS[label]))
                 for name in STRUCTURES for label in intervals}
    unit = pd.Series(np.ones(len(closes)), index=closes)
    cohorts = {}
    for horizon in (10, 20):
        starts, finishes = cohort_windows(unit, horizon)
        elapsed = (closes[finishes] - closes[starts]).days.to_numpy().astype(float)
        cohorts[horizon] = (starts, finishes, elapsed)
    codes = closes[1:].to_period('Q')
    quarters = np.flatnonzero(np.r_[True, codes[1:] != codes[:-1]])
    return Horizon(years, calendar, closes, (closes - closes[0]).days.to_numpy().astype(float),
                   quarters, schedules, cohorts,
                   float((closes[-1] - closes[0]).days))


def rebalanced(legs: np.ndarray, starts: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """Calendar-rebalanced sleeves, block-wise. Matches `letf.model.portfolio`.

    Within a rebalance period each sleeve compounds untouched, so the period is
    one cumulative product rather than a loop over sessions. `test_leaps_robustness`
    pins this against the canonical implementation on the realized data; it is a
    faster spelling of the same portfolio, not a second convention.
    """
    values = np.empty(len(legs))
    total = 1.
    for k, begin in enumerate(starts):
        stop = starts[k + 1] if k + 1 < len(starts) else len(legs)
        values[begin:stop] = np.cumprod(1 + legs[begin:stop], axis=0) @ (total * weights)
        total = float(values[stop - 1])
    return values / np.r_[1., values[:-1]] - 1


def build_path(sample: np.ndarray, calendar: pd.DatetimeIndex):
    """Rebuild every model input inside one resampled path, by the same rules.

    Volatility, the risk-free level and the trend signal are *recomputed* from
    the resampled returns rather than resampled themselves. That is the whole
    point: an implied volatility carried over from the realized calendar would
    know about crashes the simulated path never had, and a trend signal
    resampled as a state would be right about a future it cannot see.
    """
    price_return = np.r_[0., sample[:, PRICE]]
    price = pd.Series(np.cumprod(1 + price_return), index=calendar, name='price')
    returns = pd.Series(price_return, index=calendar)
    vol = implied_volatility_proxy(returns, IV_PREMIUM, MATURITY_YEARS)
    # The entry close carries no resampled return, so it is given a zero accrual
    # for the rolling cash-rate estimate. That lands 504 sessions before any
    # position is opened and is discarded with the rest of the warm-up.
    riskfree = trailing_riskfree(pd.Series(np.r_[0., sample[:, CASH_R]], index=calendar))
    dividend = np.r_[sample[0, YIELD], sample[:, YIELD]]
    position = level_position(price, calendar, SMA_DAYS, LAG)
    return price.to_numpy(), vol.to_numpy(), riskfree.to_numpy(), dividend, position


BASE_METRICS = ('cagr', 'terminal_wealth', 'max_drawdown', 'annualized_volatility',
                'min_wealth', 'negative_10y', 'cohorts_10y', 'negative_20y', 'cohorts_20y')
LEAPS_METRICS = ('mean_delta_exposure', 'min_exposure_after_loss', 'consecutive_poor_rolls',
                 'depleted_roll_fraction', 'underexposed_recovery', 'rolls')
METRICS = BASE_METRICS + LEAPS_METRICS
SLOT = {name: i for i, name in enumerate(METRICS)}


def path_metrics(wealth: np.ndarray, horizon: Horizon):
    """One path's outcome, and its drawdown series for the option diagnostics."""
    if not (wealth > 0).all():
        raise ValueError('Simulated wealth reached zero; cohort eligibility is invalid')
    returns = wealth[1:] / wealth[:-1] - 1
    drawdown = wealth / np.maximum.accumulate(wealth) - 1
    out = np.full(len(METRICS), np.nan)
    out[0] = wealth[-1] ** (365.25 / horizon.elapsed_days) - 1
    out[1] = wealth[-1]
    out[2] = drawdown.min()
    out[3] = returns.std(ddof=1) * np.sqrt(252)
    out[4] = wealth.min()
    for slot, window in ((SLOT['negative_10y'], 10), (SLOT['negative_20y'], 20)):
        starts, finishes, elapsed = horizon.cohorts[window]
        if len(starts):
            rates = (wealth[finishes] / wealth[starts]) ** (365.25 / elapsed) - 1
            out[slot], out[slot + 1] = float((rates < 0).sum()), float(len(rates))
        else:
            out[slot], out[slot + 1] = 0., 0.
    return out, drawdown


def leaps_extras(out, drawdown, exposures, rolls):
    """The diagnostics that only a rolled option has.

    Exposure drift is the structural cost of rolling infrequently: a fixed
    premium budget buys a delta that then wanders, and it wanders *down* after a
    loss, exactly when an investor would want it up. `min_exposure_after_loss`
    and `underexposed_recovery` measure that directly rather than inferring it
    from returns. Both are undefined on a path that never fell 30%, and are left
    NaN there rather than counted as passes.
    """
    mean_exposure = float(exposures.mean())
    out[SLOT['mean_delta_exposure']] = mean_exposure
    hurt = drawdown <= -MAJOR_LOSS
    if hurt.any():
        out[SLOT['min_exposure_after_loss']] = float(exposures[hurt].min())
        trough = int(drawdown.argmin())
        recovery = float(exposures[trough:trough + RECOVERY_SESSIONS].mean())
        out[SLOT['underexposed_recovery']] = float(
            recovery < UNDEREXPOSED * mean_exposure)
    # The final contract is held to the end of the horizon rather than sold, so
    # it has no realized outcome and cannot be scored.
    ratios = np.array([roll['exit_premium_ratio'] for roll in rolls[:-1]])
    if len(ratios):
        out[SLOT['depleted_roll_fraction']] = float((ratios < DEPLETED).mean())
        poor = ratios < POOR_ROLL
        out[SLOT['consecutive_poor_rolls']] = float(
            bool(len(poor) > 1 and (poor[:-1] & poor[1:]).any()))
    out[SLOT['rolls']] = float(len(rolls))


def run_path(sample: np.ndarray, horizon: Horizon, intervals) -> dict:
    """Every strategy on one resampled path."""
    price, vol, riskfree, dividend, position = build_path(sample, horizon.calendar)
    entry = WARMUP_SESSIONS
    state = position.to_numpy()[entry + 1:]
    if np.isnan(state).any():
        raise ValueError('Trend signal is not warm at the start of the evaluation window')
    equity, upro = sample[entry:, EQUITY_TR], sample[entry:, UPRO_R]
    # The trend rule, charged the same way `letf.strategy` charges it: a state
    # change is turnover 1.0 and costs the spread once.
    timed = np.where(state == 1, upro, equity)
    turnover = np.r_[0., (state[1:] != state[:-1]).astype(float)]
    timed = (1 + timed) * (1 - SWITCH_COST_BPS / 10000 * turnover) - 1
    mix = rebalanced(np.column_stack([upro, sample[entry:, TMF_R]]),
                     horizon.quarters, np.array([.6, .4]))

    results = {}
    for name, returns in (('SP500_1X', equity), ('UPRO_SMA_TO_SP500', timed),
                          ('UPRO60_TMF40', mix)):
        results[name], _ = path_metrics(np.r_[1., np.cumprod(1 + returns)], horizon)

    spot, growth = price[entry:], np.r_[1., 1 + sample[entry:, TREASURY_R]]
    q, rate, sigma = dividend[entry:], riskfree[entry:], vol[entry:]
    for name in STRUCTURES:
        for label in intervals:
            navs, exposures, _, rolls = simulate_leaps_arrays(
                spot, growth, q, rate, sigma, horizon.days,
                horizon.schedules[(name, label)])
            out, drawdown = path_metrics(navs, horizon)
            leaps_extras(out, drawdown, exposures, rolls)
            results[label_of(name, label)] = out
    return results


def label_of(structure: str, interval: str) -> str:
    """Strategy key. The benchmark roll carries no suffix so it reads as the base case."""
    return structure if interval == BENCHMARK_INTERVAL else f'{structure}_ROLL_{interval.upper()}'


def _run_chunk(args):
    pool, horizon, intervals, block, seeds = args
    needed = len(horizon.calendar) - 1
    rows = []
    for spawned in seeds:
        rng = np.random.default_rng(spawned)
        rows.append(run_path(pool[block_draw(rng, len(pool), needed, block)],
                             horizon, intervals))
    return rows


def monte_carlo(pool, horizon: Horizon, intervals, paths: int, block: int, seed: int,
                workers: int | None = None):
    """Resample `paths` times and score every strategy on each.

    Each path draws from its own spawned seed rather than from one shared
    stream. That is what makes the result independent of how the work is
    divided: a path's outcome depends on its index and nothing else — not on how
    many paths were run, not on the order they finished in, and not on how many
    processes ran them. `test_leaps_robustness` pins that equivalence rather
    than asserting it here, because a parallel result that silently differed
    from the serial one would invalidate every number in the report.
    """
    seeds = np.random.SeedSequence(seed).spawn(paths)
    workers = max(1, min(workers or (os.cpu_count() or 1), paths))
    if workers == 1:
        chunks = [seeds]
    else:
        # More chunks than workers so a slow one cannot hold up the tail.
        chunks = [c for c in np.array_split(np.array(seeds, dtype=object), workers * 4)
                  if len(c)]
    payloads = [(pool, horizon, intervals, block, list(chunk)) for chunk in chunks]
    if workers == 1:
        collected = [_run_chunk(payloads[0])]
    else:
        with ProcessPoolExecutor(workers) as pool_executor:
            collected = list(pool_executor.map(_run_chunk, payloads))

    store, index = None, 0
    for rows in collected:
        for result in rows:
            if store is None:
                store = {name: np.empty((paths, len(METRICS))) for name in result}
            for name, row in result.items():
                store[name][index] = row
            index += 1
    if index != paths:
        raise ValueError('Monte Carlo lost paths while collecting results')
    return store


def summarize(store: dict, horizon: Horizon, block: int, paths: int) -> pd.DataFrame:
    """Distribution summary for one (horizon, block length) simulation.

    Two conventions worth stating because they change what the numbers mean.

    *Negative-horizon probabilities are pooled over overlapping windows.* Within
    one path the ten-year windows overlap heavily, so the effective sample is far
    smaller than the count of windows and these are not binomial proportions with
    the precision the denominator suggests. On the horizon that equals the path
    length there is exactly one window, so that probability is the honest
    across-path frequency and is computed that way instead.

    *Comparisons against the index are paired.* "Below SP500" compares each
    strategy to the index **on its own path**, not to the index's median, so it
    answers the question an investor has — would this have beaten simply holding
    the index, in this world? — rather than comparing two marginal distributions.
    """
    index_terminal = store['SP500_1X'][:, SLOT['terminal_wealth']]
    rows = []
    for name, values in store.items():
        rates, terminal = values[:, SLOT['cagr']], values[:, SLOT['terminal_wealth']]
        windows10, windows20 = values[:, SLOT['cohorts_10y']].sum(), values[:, SLOT['cohorts_20y']].sum()
        row = dict(strategy=name, horizon_years=horizon.years, block_days=block, paths=paths,
                   mean_cagr=float(rates.mean()), sd_cagr=float(rates.std(ddof=1)),
                   mean_terminal_wealth=float(terminal.mean()),
                   median_terminal_wealth=float(np.median(terminal)),
                   median_max_drawdown=float(np.median(values[:, SLOT['max_drawdown']])),
                   worst_max_drawdown=float(values[:, SLOT['max_drawdown']].min()),
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
                   else 'pooled overlapping windows',
                   prob_below_sp500=float((terminal < index_terminal).mean()))
        for level in PERCENTILES:
            row[f'cagr_p{level}'] = float(np.percentile(rates, level))
            row[f'terminal_wealth_p{level}'] = float(np.percentile(terminal, level))
        for threshold in DRAWDOWN_THRESHOLDS:
            row[f'prob_drawdown_worse_than_{int(threshold * 100)}'] = float(
                (values[:, SLOT['max_drawdown']] <= -threshold).mean())
        for metric in LEAPS_METRICS:
            column = values[:, SLOT[metric]]
            row[f'{metric}_median'] = float(np.nanmedian(column)) if np.isfinite(column).any() else np.nan
            row[f'{metric}_mean'] = float(np.nanmean(column)) if np.isfinite(column).any() else np.nan
        column = values[:, SLOT['mean_delta_exposure']]
        row['mean_delta_exposure_p5'] = (float(np.nanpercentile(column, 5))
                                         if np.isfinite(column).any() else np.nan)
        # How many paths the loss-conditioned diagnostics could be scored on.
        row['paths_with_major_loss'] = int(
            np.isfinite(values[:, SLOT['underexposed_recovery']]).sum())
        rows.append(row)
    return pd.DataFrame(rows)


def percentile_table(store: dict, horizon: Horizon, block: int) -> pd.DataFrame:
    rows = []
    for name, values in store.items():
        for metric in ('cagr', 'terminal_wealth'):
            column = values[:, SLOT[metric]]
            for level in PERCENTILES:
                rows.append(dict(strategy=name, horizon_years=horizon.years,
                                 block_days=block, metric=metric,
                                 statistic=f'p{level}',
                                 value=float(np.percentile(column, level))))
            rows.append(dict(strategy=name, horizon_years=horizon.years, block_days=block,
                             metric=metric, statistic='mean', value=float(column.mean())))
    return pd.DataFrame(rows)


def drawdown_table(store: dict, horizon: Horizon, block: int) -> pd.DataFrame:
    rows = []
    for name, values in store.items():
        column = values[:, SLOT['max_drawdown']]
        for threshold in DRAWDOWN_THRESHOLDS:
            rows.append(dict(strategy=name, horizon_years=horizon.years, block_days=block,
                             threshold=threshold,
                             probability=float((column <= -threshold).mean())))
    return pd.DataFrame(rows)


def historical_reference(inputs: Inputs, rolls: pd.DataFrame) -> pd.DataFrame:
    """The realized-path outcome for every strategy the Monte Carlo simulates.

    The three non-option strategies are built here with the repository's own
    helpers — `letf.strategy` for the trend rule's costing, `letf.model.portfolio`
    for the rebalanced mix — rather than with the faster spellings the simulation
    loop uses. The realized path is the thing the simulation is judged against,
    so it is computed by the canonical code and not by this module's shortcuts.
    """
    d = inputs.daily.loc[inputs.ix]
    position = level_position(inputs.price, inputs.calendar, SMA_DAYS, LAG).loc[inputs.ix]
    timed = switching_costs(select_returns(d, position, {1: UPRO, 0: EQUITY}),
                            position, SWITCH_COST_BPS)
    mix = portfolio(d[[UPRO, TMF]], pd.Series({UPRO: .6, TMF: .4}), 'quarterly')
    rows = [dict(strategy=name, cagr=cagr(returns), max_drawdown=max_drawdown(returns),
                 terminal_multiple=float((1 + returns).prod()))
            for name, returns in (('SP500_1X', d[EQUITY]), ('UPRO_SMA_TO_SP500', timed),
                                  ('UPRO60_TMF40', mix))]
    benchmark = rolls[rolls.roll == BENCHMARK_INTERVAL].set_index('structure')
    rows += [dict(strategy=name, cagr=float(benchmark.loc[name, 'cagr']),
                  max_drawdown=float(benchmark.loc[name, 'max_drawdown']),
                  terminal_multiple=float(benchmark.loc[name, 'terminal_multiple']))
             for name in STRUCTURES]
    frame = pd.DataFrame(rows)
    frame['historical_rank'] = frame.cagr.rank(ascending=False).astype(int)
    return frame


def rank_table(store: dict, reference: pd.DataFrame, horizon: Horizon,
               block: int) -> pd.DataFrame:
    """How often each strategy lands where the realized path put it.

    This is the sharpest form of the sequence question. A ranking that holds in
    almost every reordering of the history is a statement about the strategies;
    one that holds only in the realized order is a statement about 1987, 2008
    and 2022 arriving when they did. Ranks are taken on terminal wealth within
    each path, so every comparison is between strategies that lived through the
    same simulated world.
    """
    names = list(MC_STRATEGIES)
    historical = reference.set_index('strategy').loc[names, 'historical_rank']
    terminal = np.column_stack([store[name][:, SLOT['terminal_wealth']] for name in names])
    ranks = (-terminal).argsort(axis=1).argsort(axis=1) + 1
    rows = []
    for column, name in enumerate(names):
        own = ranks[:, column]
        rows.append(dict(strategy=name, horizon_years=horizon.years, block_days=block,
                         historical_rank=int(historical[name]),
                         mean_rank=float(own.mean()),
                         modal_rank=int(np.bincount(own).argmax()),
                         prob_at_historical_rank=float((own == historical[name]).mean()),
                         prob_top_two=float((own <= 2).mean()),
                         prob_bottom_two=float((own >= len(names) - 1).mean())))
    return pd.DataFrame(rows)


SHORT = {'SP500_1X': 'SP500 1x', 'UPRO_SMA_TO_SP500': 'UPRO SMA', 'UPRO60_TMF40': 'UPRO60/TMF40',
         'LEAPS_80_25_TREASURY': '80/25', 'LEAPS_85_30_TREASURY': '85/30',
         'LEAPS_95_50_TREASURY': '95/50'}


def figure(path: Path, rolls: pd.DataFrame, store: dict, horizon: Horizon):
    """Three panels: the roll-interval sweep, and the two Monte Carlo views."""
    fig, axes = plt.subplots(1, 3, figsize=(16.5, 5.2), constrained_layout=True)
    order = list(ROLL_INTERVALS)
    positions = np.arange(len(order))

    ax, twin = axes[0], axes[0].twinx()
    for position, (name, group) in enumerate(rolls.groupby('structure', sort=False)):
        colour, indexed = f'C{position % 10}', group.set_index('roll').loc[order]
        ax.plot(positions, indexed.cagr, 'o-', color=colour, label=SHORT[name])
        twin.plot(positions, indexed.max_drawdown, 's--', color=colour, alpha=.45)
    ax.set_xticks(positions, order)
    ax.set_xlabel('roll interval')
    ax.set_ylabel('CAGR (solid)')
    twin.set_ylabel('max drawdown (dashed)')
    for axis in (ax, twin):
        axis.yaxis.set_major_formatter(PercentFormatter(1))
    ax.legend(loc='center left', fontsize=9)
    ax.set_title('A. Realized path: only the roll interval varies')

    names = list(MC_STRATEGIES)
    ax = axes[1]
    ax.boxplot([store[name][:, SLOT['cagr']] for name in names], whis=(5, 95), showfliers=False,
               medianprops=dict(color='C3'))
    ax.set_xticks(np.arange(1, len(names) + 1), [SHORT[n] for n in names], rotation=30,
                  ha='right', fontsize=9)
    ax.axhline(0, color='grey', lw=.8)
    ax.yaxis.set_major_formatter(PercentFormatter(1))
    ax.set_ylabel(f'{horizon.years}-year CAGR')
    ax.set_title('B. Bootstrap CAGR, box 25-75, whiskers 5-95')

    ax = axes[2]
    for position, name in enumerate(names):
        terminal = store[name][:, SLOT['terminal_wealth']]
        low, high = np.percentile(terminal, [5, 95])
        inner = np.percentile(terminal, [10, 90])
        ax.plot([position, position], [low, high], color='C0', lw=1.4)
        ax.plot([position, position], inner, color='C0', lw=4.5, alpha=.5)
        ax.plot(position, np.median(terminal), 'o', color='C3', zorder=3)
    ax.set_yscale('log')
    ax.axhline(1, color='grey', lw=.8)
    ax.set_xticks(np.arange(len(names)), [SHORT[n] for n in names], rotation=30,
                  ha='right', fontsize=9)
    ax.set_ylabel(f'terminal wealth per $1, {horizon.years}y (log)')
    ax.set_title('C. Terminal wealth: 5-95, 10-90, median')

    fig.savefig(path, dpi=160)
    plt.close(fig)


def run(root: Path, workers=None, paths=None, sensitivity_paths=None):
    inputs = load(root)
    rolls = roll_frequency_table(inputs)
    dispersion = roll_dispersion(rolls)
    uncertainty = model_uncertainty(inputs)
    reference = historical_reference(inputs, rolls)

    pool = bootstrap_pool(inputs)
    primary = paths or PRIMARY_PATHS
    secondary = sensitivity_paths or SENSITIVITY_PATHS
    summaries, percentiles, drawdowns, ranks, stores = {}, [], [], [], {}
    for years in HORIZONS:
        horizon = build_horizon(inputs, years, MONTE_CARLO_INTERVALS)
        store = monte_carlo(pool, horizon, MONTE_CARLO_INTERVALS, primary,
                            PRIMARY_BLOCK, SEED, workers)
        stores[years] = (horizon, store)
        summaries[years] = summarize(store, horizon, PRIMARY_BLOCK, primary)
        percentiles.append(percentile_table(store, horizon, PRIMARY_BLOCK))
        drawdowns.append(drawdown_table(store, horizon, PRIMARY_BLOCK))
        ranks.append(rank_table(store, reference, horizon, PRIMARY_BLOCK))

    # Block length is a prespecified sensitivity, not a tuning knob: the primary
    # answer is the 63-day one whatever the others say, and they are run only so
    # a reader can see whether it depends on the choice.
    block_rows = []
    for block in BLOCK_LENGTHS:
        for years in HORIZONS:
            horizon, store = stores[years]
            if block == PRIMARY_BLOCK:
                frame, count = summaries[years], primary
            else:
                count = secondary
                frame = summarize(monte_carlo(pool, horizon, (BENCHMARK_INTERVAL,), count,
                                              block, SEED, workers), horizon, block, count)
            block_rows.append(frame[frame.strategy.isin(MC_STRATEGIES)])
    blocks = pd.concat(block_rows, ignore_index=True)

    reports = root / 'reports'
    outputs = {'leaps_roll_frequency.csv': rolls,
               'leaps_roll_dispersion.csv': dispersion,
               'leaps_model_uncertainty.csv': uncertainty,
               'leaps_historical_reference.csv': reference,
               'leaps_monte_carlo_20y.csv': summaries[20],
               'leaps_monte_carlo_30y.csv': summaries[30],
               'leaps_monte_carlo_percentiles.csv': pd.concat(percentiles, ignore_index=True),
               'leaps_monte_carlo_drawdowns.csv': pd.concat(drawdowns, ignore_index=True),
               'leaps_monte_carlo_ranks.csv': pd.concat(ranks, ignore_index=True),
               'leaps_monte_carlo_blocks.csv': blocks}
    for name, frame in outputs.items():
        frame.pipe(stable_floats).to_csv(reports / name, index=False, float_format=FLOAT_FORMAT)
    figure(reports / 'leaps_roll_monte_carlo.png', rolls, stores[30][1], stores[30][0])

    report(reports, inputs, rolls, dispersion, uncertainty, reference, summaries,
           pd.concat(ranks, ignore_index=True), blocks, stores, primary, secondary)
    (reports / 'leaps_roll_monte_carlo_manifest.json').write_text(json.dumps({
        'window': [inputs.ix[0].date().isoformat(), inputs.ix[-1].date().isoformat()],
        'observations': int(len(inputs.ix)),
        'roll_intervals': ROLL_INTERVALS, 'structures': {k: list(v) for k, v in STRUCTURES.items()},
        'benchmark_interval': BENCHMARK_INTERVAL,
        'monte_carlo_intervals': list(MONTE_CARLO_INTERVALS),
        'maturity_years': MATURITY_YEARS, 'expiry_months': list(EXPIRY_MONTHS),
        'iv_premium': IV_PREMIUM, 'option_spread_bps': OPTION_SPREAD_BPS,
        'sma_days': SMA_DAYS, 'lag': LAG, 'switch_cost_bps': SWITCH_COST_BPS,
        'seed': SEED, 'block_lengths': list(BLOCK_LENGTHS), 'primary_block': PRIMARY_BLOCK,
        'primary_paths': primary, 'sensitivity_paths': secondary,
        'horizons': list(HORIZONS), 'warmup_sessions': WARMUP_SESSIONS,
        'bootstrap': 'joint moving-block resample of whole daily rows, drawn with '
                     'replacement from every possible start; last block truncated',
        'resampled_columns': list(POOL_COLUMNS),
        'rebuilt_inside_each_path': ['implied volatility proxy', 'trailing risk-free rate',
                                     'SMA position'],
        'option_prices': 'modelled with Black-Scholes on an assumed implied volatility; '
                         'this repository holds no option price history, and resampling '
                         'a modelled price does not measure it',
        'python': platform.python_version(), 'numpy': np.__version__,
        'pandas': pd.__version__, 'scipy': scipy.__version__,
        'matplotlib': matplotlib.__version__,
        'source_hashes': source_hashes(root, __spec__.name),
        'outputs_sha256': {name: sha(reports / name) for name in outputs},
    }, indent=2) + '\n')
    print(f'LEAPS roll frequency: {len(rolls)} rows; Monte Carlo: {primary} paths x '
          f'{len(HORIZONS)} horizons at block {PRIMARY_BLOCK}, {secondary} paths for '
          f'blocks {[b for b in BLOCK_LENGTHS if b != PRIMARY_BLOCK]}.')
    return rolls, summaries


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


MONTHS = {1: 'January', 2: 'February', 3: 'March', 4: 'April', 5: 'May', 6: 'June',
          7: 'July', 8: 'August', 9: 'September', 10: 'October', 11: 'November',
          12: 'December'}


def _phrase(items, join='and'):
    items = [str(i) for i in items]
    return items[0] if len(items) == 1 else f'{", ".join(items[:-1])} {join} {items[-1]}'


def _count_phrase(count: int, total: int) -> str:
    """"all three", "two of the three", "none of the three" — never "3 of the three"."""
    words = {0: 'none', 1: 'one', 2: 'two', 3: 'three', 4: 'four', 5: 'five'}
    if count == total:
        return f'all {words.get(total, total)} structures'
    return f'{words.get(count, count)} of the {words.get(total, total)} structures'


def _crash_summary(rolls) -> dict:
    """Which roll interval came through each stress window best, and whether it means anything.

    Averaged across structures so the comparison is about the interval rather
    than the budget: a structure holding twice the option weight falls roughly
    twice as far in every window and would otherwise decide the ranking by
    itself.
    """
    events = rolls.groupby('roll', sort=False)[list(CRASHES)].mean()
    counts = events.idxmax().value_counts()
    champion = counts.idxmax()
    best_cagr = rolls.groupby('roll', sort=False)['cagr'].mean().idxmax()
    return dict(
        crash_best_count=int(counts.max()), crash_champion=champion,
        crash_champion_cagr=('it also has the highest CAGR — so the two agree here, on '
                             'five observations' if champion == best_cagr else
                             f'the highest CAGR belongs to {best_cagr} instead'),
        crash_spread=f'{(events.max() - events.min()).max():.1%}')


def _narrative(rolls, dispersion, uncertainty, reference, summaries, ranks, blocks):
    """Every number the prose states, formatted, derived from the tables beside it.

    The report states conclusions in sentences. Those sentences are assembled
    from this dictionary, so a claim cannot drift away from the table that
    supports it: if the data moves the sentence moves with it, and if a key
    stops existing the report fails to render rather than printing a stale
    number. Formatting happens here rather than inline so the prose below stays
    readable as prose.
    """
    by = rolls.set_index(['structure', 'roll'])
    yard = uncertainty.set_index('structure')['cagr_per_volatility_point']
    thirty = summaries[30].set_index('strategy')
    rank = ranks[ranks.horizon_years == 30].set_index('strategy')
    ref = reference.set_index('strategy')
    first, mid, top = list(STRUCTURES)
    index_sd = thirty.loc['SP500_1X', 'sd_cagr']

    # Realized-path roll-interval gaps, expressed in volatility points of doubt.
    gaps = {name: {label: (by.loc[(name, label), 'cagr']
                           - by.loc[(name, BENCHMARK_INTERVAL), 'cagr']) / yard[name]
                   for label in ROLL_INTERVALS} for name in STRUCTURES}
    relative = (dispersion.set_index('structure')['cagr_spread']
                / dispersion.set_index('structure')['cagr_at_benchmark'])
    amplification = pd.Series({name: thirty.loc[name, 'sd_cagr'] / index_sd
                               for name in MC_STRATEGIES})
    least_interval, least_sequence = relative.idxmin(), amplification[list(STRUCTURES)].idxmin()
    # Whether the realized path really prefers a longer roll in every structure,
    # rather than on average, decides how strongly the prose may put it.
    ordered = sorted(ROLL_INTERVALS, key=ROLL_INTERVALS.get)
    monotone = all(by.loc[(name, short), 'cagr'] < by.loc[(name, long), 'cagr']
                   for name in STRUCTURES for short, long in zip(ordered, ordered[1:]))
    interval_spread = {name: max(thirty.loc[label_of(name, l), 'cagr_p50']
                                 for l in MONTE_CARLO_INTERVALS)
                       - min(thirty.loc[label_of(name, l), 'cagr_p50']
                             for l in MONTE_CARLO_INTERVALS) for name in STRUCTURES}
    structure_spread = (max(thirty.loc[name, 'cagr_p50'] for name in STRUCTURES)
                        - min(thirty.loc[name, 'cagr_p50'] for name in STRUCTURES))
    trend = blocks[(blocks.strategy == 'UPRO_SMA_TO_SP500')
                   & (blocks.horizon_years == 30)].set_index('block_days')['cagr_p50']
    short_block, long_block = min(BLOCK_LENGTHS), max(BLOCK_LENGTHS)
    medians = blocks[blocks.horizon_years == 30].pivot_table(
        index='strategy', columns='block_days', values='cagr_p50')
    block_spread = medians.max(axis=1) - medians.min(axis=1)

    v = dict(
        months=_phrase([MONTHS[m] for m in sorted(EXPIRY_MONTHS)]),
        other_blocks=_phrase([b for b in BLOCK_LENGTHS if b != PRIMARY_BLOCK]),
        pool=_phrase(POOL_COLUMNS),
        first=first, mid=mid, top=top,
        held9=f"{by.loc[(first, '9m'), 'mean_held_years'] * 12:.1f}",
        rolls_benchmark=f"{by.loc[(first, BENCHMARK_INTERVAL), 'rolls']:.0f}",
        rolls_long=f"{by.loc[(first, '18m'), 'rolls']:.0f}",
        gap13=f"{max(abs(g['13m']) for g in gaps.values()):.2f}",
        gap6=f"{max(abs(g['6m']) for g in gaps.values()):.2f}",
        gap18=f"{max(abs(g['18m']) for g in gaps.values()):.2f}",
        gap_any=f"{max(abs(g[l]) for g in gaps.values() for l in ROLL_INTERVALS):.1f}",
        monotone=('always favouring the longer interval' if monotone
                  else 'though not uniformly favouring either direction'),
        monotone_lead=('Longer intervals earned more on this path, monotonically, in every '
                       'structure.' if monotone else
                       'Longer intervals earned more on this path on average, but not '
                       'monotonically and not in every structure.'),
        cost_first=f"{by.loc[(first, '6m'), 'cost_drag_bps'] - by.loc[(first, BENCHMARK_INTERVAL), 'cost_drag_bps']:.0f}",
        sd_benchmark=f"{by.loc[(mid, BENCHMARK_INTERVAL), 'delta_exposure_sd']:.3f}",
        sd_short=f"{by.loc[(mid, '6m'), 'delta_exposure_sd']:.3f}",
        floor_benchmark=f"{thirty.loc[mid, 'min_exposure_after_loss_median']:.2f}",
        floor_short=f"{thirty.loc[label_of(mid, '9m'), 'min_exposure_after_loss_median']:.2f}",
        # Whether a shorter roll actually lifts the post-loss exposure floor is
        # a fact about the simulation, not something the prose may assume.
        floor_direction=('rises' if thirty.loc[label_of(mid, '9m'),
                                               'min_exposure_after_loss_median']
                         > thirty.loc[mid, 'min_exposure_after_loss_median'] else 'falls'),
        floor_long_phrase=_count_phrase(sum(
            thirty.loc[label_of(name, '18m'), 'min_exposure_after_loss_median']
            < thirty.loc[name, 'min_exposure_after_loss_median'] for name in STRUCTURES),
            len(STRUCTURES)),
        floor_long_count=sum(
            thirty.loc[label_of(name, '18m'), 'min_exposure_after_loss_median']
            < thirty.loc[name, 'min_exposure_after_loss_median'] for name in STRUCTURES),
        cost_mid=f"{by.loc[(mid, '6m'), 'cost_drag_bps'] - by.loc[(mid, BENCHMARK_INTERVAL), 'cost_drag_bps']:.0f}",
        cagr_cost=f"{by.loc[(mid, BENCHMARK_INTERVAL), 'cagr'] - by.loc[(mid, '6m'), 'cagr']:.2%}",
        cagr_cost_points=f"{abs(gaps[mid]['6m']):.1f}",
        mc_spread_low=f"{min(interval_spread.values()):.2%}",
        mc_spread_high=f"{max(interval_spread.values()):.2%}",
        mc_spread_points=f"{max(interval_spread[n] / yard[n] for n in STRUCTURES):.1f}",
        structure_over_interval=f"{structure_spread / max(interval_spread.values()):.0f}",
        # Four claims the prose used to assert. Each is a fact about the tables
        # and has to be read off them, not written in advance.
        budget_ordering=('rises with the premium budget — the same ordering the budget '
                         'imposes on every other risk in this family'
                         if list(relative[list(STRUCTURES)]) ==
                         sorted(relative[list(STRUCTURES)])
                         else 'does not order cleanly with the premium budget'),
        shorter_verdict=('No, on this evidence'
                         if by.loc[(mid, BENCHMARK_INTERVAL), 'cagr']
                         > by.loc[(mid, '6m'), 'cagr'] else 'Yes, on this evidence'),
        most_stable=rank['prob_at_historical_rank'].idxmax(),
        # "Moves most" is measured on rank rather than on CAGR: the realized
        # figure spans forty years and the simulated median thirty, so their
        # difference mixes horizon with ordering. Rank is free of both.
        mover=(rank['mean_rank'] - rank['historical_rank']).idxmax(),
        mover_rank_shift=f"{(rank['mean_rank'] - rank['historical_rank']).max():.1f}",
        # Whose answer depends on preserved autocorrelation, measured rather
        # than asserted: the spread of median CAGR across the three block
        # lengths, per strategy.
        block_move_trend=f"{block_spread['UPRO_SMA_TO_SP500']:.2%}",
        block_monotone=(
            'and monotone in block length, which is the signature of a rule that '
            'trades the autocorrelation a longer block preserves'
            if list(medians.loc['UPRO_SMA_TO_SP500', sorted(BLOCK_LENGTHS)])
            == sorted(medians.loc['UPRO_SMA_TO_SP500'])
            else 'though not monotone in block length, so the handicap is not the '
                 'whole of it'),
        block_move_min=f"{min(block_spread[n] for n in STRUCTURES):.2%}",
        block_move_max=f"{max(block_spread[n] for n in STRUCTURES):.2%}",
        block_move_heaviest=max(STRUCTURES, key=lambda n: block_spread[n]),
        # The two lower-budget structures, i.e. every option row except whichever
        # moved most. Leverage amplifies any change to any input, so the most
        # levered structure moving most is arithmetic rather than a finding.
        block_move_rest=f"{sorted(block_spread[n] for n in STRUCTURES)[-2]:.2%}",
        block_move_ratio=f"{block_spread['UPRO_SMA_TO_SP500'] / sorted(block_spread[n] for n in STRUCTURES)[-2]:.0f}",
        same_rolls=('the same number of rolls'
                    if all(by.loc[(n, '12m'), 'rolls'] == by.loc[(n, '13m'), 'rolls']
                           for n in STRUCTURES) else 'nearly the same number of rolls'),
        top_stability_phrase=(
            'the most stable ranking of any strategy here'
            if rank['prob_at_historical_rank'].idxmax() == top
            else f"though {rank['prob_at_historical_rank'].idxmax()} holds its rank "
                 f"more often"),
        top_median_phrase=(
            'the highest median'
            if max(thirty.loc[n, 'cagr_p50'] for n in MC_STRATEGIES)
            == thirty.loc[top, 'cagr_p50']
            else 'a high median, though not the highest'),
        least_interval=least_interval,
        least_interval_spread=f"{relative[least_interval]:.1%}",
        worst_interval_spread=f"{relative.drop(least_interval).max():.1%}",
        least_sequence=least_sequence,
        amplification_least=f"{amplification[least_sequence]:.2f}",
        amplification_top=f"{amplification[top]:.2f}",
        least_sequence_rank=f"{rank.loc[least_sequence, 'prob_at_historical_rank']:.0%}",
        strategies=len(MC_STRATEGIES),
        trend_realized=f"{ref.loc['UPRO_SMA_TO_SP500', 'cagr']:.2%}",
        trend_rank=f"{int(ref.loc['UPRO_SMA_TO_SP500', 'historical_rank'])}",
        trend_median=f"{thirty.loc['UPRO_SMA_TO_SP500', 'cagr_p50']:.2%}",
        trend_modal=f"{int(rank.loc['UPRO_SMA_TO_SP500', 'modal_rank'])}",
        trend_below=f"{thirty.loc['UPRO_SMA_TO_SP500', 'prob_below_sp500']:.0%}",
        trend_short_block=f"{trend.get(short_block, float('nan')):.2%}",
        trend_long_block=f"{trend.get(long_block, float('nan')):.2%}",
        short_block=short_block, long_block=long_block,
        # The handicap predicts a longer block restores some of the signal. State
        # what happened rather than what was predicted.
        trend_direction=('rises with block length, the direction the handicap predicts'
                         if trend.get(long_block, 0) > trend.get(short_block, 0)
                         else 'does not rise with block length, so the handicap does not '
                              'account for the gap'),
        mid_rank=f"{int(ref.loc[mid, 'historical_rank'])}",
        mid_mean_rank=f"{rank.loc[mid, 'mean_rank']:.2f}",
        mid_below=f"{thirty.loc[mid, 'prob_below_sp500']:.0%}",
        mid_median=f"{thirty.loc[mid, 'cagr_p50']:.2%}",
        mid_drawdown=f"{thirty.loc[mid, 'median_max_drawdown']:.1%}",
        index_median=f"{thirty.loc['SP500_1X', 'cagr_p50']:.2%}",
        index_drawdown=f"{thirty.loc['SP500_1X', 'median_max_drawdown']:.1%}",
        # Which way the drawdown comparison goes depends on the structure and on
        # the simulation; the prose must not decide it in advance.
        mid_drawdown_word=('shallower than' if thirty.loc[mid, 'median_max_drawdown']
                           > thirty.loc['SP500_1X', 'median_max_drawdown']
                           else 'deeper than'),
        shallowest=min(STRUCTURES, key=lambda n: -thirty.loc[n, 'median_max_drawdown']),
        shallowest_drawdown=f"{max(thirty.loc[n, 'median_max_drawdown'] for n in STRUCTURES):.1%}",
        index_p5=f"{thirty.loc['SP500_1X', 'cagr_p5']:.2%}",
        top_modal=f"{int(rank.loc[top, 'modal_rank'])}",
        top_two=f"{rank.loc[top, 'prob_top_two']:.0%}",
        top_dd50=f"{thirty.loc[top, 'prob_drawdown_worse_than_50']:.0%}",
        top_dd75=f"{thirty.loc[top, 'prob_drawdown_worse_than_75']:.0%}",
        top_p5=f"{thirty.loc[top, 'cagr_p5']:.2%}",
        top_p5_word=('below' if thirty.loc[top, 'cagr_p5'] < thirty.loc['SP500_1X', 'cagr_p5']
                     else 'above'),
        **_crash_summary(rolls))
    return v


def report(reports: Path, inputs: Inputs, rolls, dispersion, uncertainty, reference,
           summaries, ranks, blocks, stores, primary, secondary):
    """Write the narrative from the numbers, never alongside them."""
    v = _narrative(rolls, dispersion, uncertainty, reference, summaries, ranks, blocks)
    percent = {c: '.2%' for c in ('cagr', 'max_drawdown', 'cohort_20y_min_cagr',
                                  'cohort_30y_min_cagr')}
    roll_columns = ['structure', 'roll', 'rolls', 'mean_entry_years', 'mean_held_years',
                    'cagr', 'max_drawdown', 'mean_delta_exposure', 'delta_exposure_sd',
                    'option_turnover_per_year', 'cost_drag_bps', 'cohort_20y_min_cagr',
                    'cohort_30y_min_cagr']
    roll_formats = dict(percent, mean_entry_years='.2f', mean_held_years='.2f',
                        mean_delta_exposure='.2f', delta_exposure_sd='.3f',
                        option_turnover_per_year='.3f', cost_drag_bps='.0f')
    summary_columns = ['strategy', 'cagr_p5', 'cagr_p50', 'cagr_p95', 'sd_cagr',
                       'median_annualized_volatility', 'median_max_drawdown',
                       'prob_negative_10y', 'prob_negative_20y', 'prob_below_sp500',
                       'prob_drawdown_worse_than_50', 'prob_drawdown_worse_than_75']
    summary_formats = {c: '.2%' for c in summary_columns[1:]} | {'sd_cagr': '.3f'}
    wealth_columns = ['strategy', 'terminal_wealth_p5', 'terminal_wealth_p10',
                      'median_terminal_wealth', 'mean_terminal_wealth',
                      'terminal_wealth_p90', 'terminal_wealth_p95', 'p5_min_wealth']
    wealth_formats = {c: ',.1f' for c in wealth_columns[1:-1]} | {'p5_min_wealth': '.3f'}
    leaps_rows = summaries[30][summaries[30].strategy.str.startswith('LEAPS')]
    # Averaged across structures so the column reports the interval, not the
    # budget: a structure with twice the option weight falls twice as far in
    # every window, which would otherwise decide the ranking on its own.
    events = rolls.groupby('roll', sort=False)[list(CRASHES)].mean()
    crash_best = pd.DataFrame([dict(event=event, best_roll=events[event].idxmax(),
                                    worst_roll=events[event].idxmin(),
                                    spread=float(events[event].max() - events[event].min()))
                               for event in CRASHES])

    text = f"""# Rolling long-dated calls: does the roll interval matter, and does the ranking survive reordering?

Generated by `letf.leaps_robustness`. Window {inputs.ix[0].date()} to {inputs.ix[-1].date()}, {len(inputs.ix):,} sessions, on the same calendar and financing basis as `reports/hedge_alternatives_results.md`.

**The option premia here are modelled, not measured.** `letf.options` explains why
at length, and nothing below softens it: this repository holds no option price
history, so every LEAPS number is a property of an assumed implied volatility.
Resampling a modelled price thousands of times does not measure it. What the
resampling tests is whether a *ranking* computed under that assumption depends on
the order in which history arrived.

Two questions, both attempts to break the earlier result rather than improve it.

1. `letf.hedge_alternatives` buys roughly two-year calls and rolls when about a
   year is left. Nothing there ever varied that. **Part A** moves only the roll
   interval, across {_phrase(list(ROLL_INTERVALS))}.
2. Every number in this repository comes from one realized path. **Part B**
   resamples that path in blocks and asks whether the ranking is a property of
   the strategies or of the sequence.

## The scale a difference is read against

"Materially different" needs a unit, and the honest unit here is the one input
that cannot be checked. Shifting the assumed volatility premium by one point
moves each structure's 40-year CAGR by:

{markdown_table(uncertainty, ['structure', 'cagr_at_base', 'cagr_one_point_cheaper',
                              'cagr_one_point_dearer', 'cagr_per_volatility_point'],
                {c: '.2%' for c in ('cagr_at_base', 'cagr_one_point_cheaper',
                                    'cagr_one_point_dearer', 'cagr_per_volatility_point')})}

A gap between two roll intervals smaller than one of those numbers is not a
finding about roll intervals; it is inside the noise of the pricing assumption
that produced both sides of the comparison. Differences below are quoted in these
units as well as in percentage points.

## Part A — roll frequency on the realized path

Strike, premium budget, safe sleeve, two-year target maturity, the
{IV_PREMIUM:.0%} volatility premium and the {OPTION_SPREAD_BPS:.0f}bp option
spread are all held fixed. Only the interval moves.

The interval is *nominal*. Long-dated index options are listed on a handful of
expiries — modelled here as the third Fridays of {v['months']} — so a desired roll
date is mapped onto the nearest listed expiry still long enough, and the maturity
actually bought drifts. The realized figures are in the table, and they are not
the nominal ones:

{markdown_table(rolls, roll_columns, roll_formats)}

The nine-month interval is the clearest case: it cannot be bought. With three
expiries listed two years out, asking to roll every nine months produces a
realized holding period of {v['held9']} months. A practical schedule has about
{v['rolls_benchmark']} rolls over four decades at the benchmark and
{v['rolls_long']} at eighteen months, whatever calendar is in the investor's head.

Twelve and thirteen months are, on this expiry calendar, very nearly the same
portfolio: {v['same_rolls']}, and at most {v['gap13']} volatility points of CAGR
between them across the three structures. That distinction is not one an
investor can act on.

### Stress windows

{markdown_table(rolls, ['structure', 'roll'] + list(CRASHES), {k: '.1%' for k in CRASHES})}

Which interval came through each window best, averaged across the three
structures:

{markdown_table(crash_best, ['event', 'best_roll', 'worst_roll', 'spread'],
                {'spread': '.1%'})}

No interval wins more than {v['crash_best_count']} of the five windows.
{v['crash_champion']} wins most, and {v['crash_champion_cagr']}. With five events
and a spread of up to {v['crash_spread']} between the best and worst interval in a
single window, this table cannot separate a property of the roll interval from
where a crash happened to fall relative to a roll date — which is exactly the
kind of question the bootstrap below exists to answer instead.

### How far the answer moves

{markdown_table(dispersion, ['structure'] + [f'{c}_{s}' for c in
                ('cagr', 'max_drawdown', 'delta_exposure_sd')
                for s in ('at_benchmark', 'spread')],
                {'cagr_at_benchmark': '.2%', 'cagr_spread': '.2%',
                 'max_drawdown_at_benchmark': '.2%', 'max_drawdown_spread': '.2%',
                 'delta_exposure_sd_at_benchmark': '.3f',
                 'delta_exposure_sd_spread': '.3f'})}

{v['monotone_lead']}
Part of that is cost — the six-month roll pays {v['cost_first']}bp a year more
than the benchmark in {v['first']} — but cost does not explain all of it, and Part
B is better placed than this table to judge what is left.

## Part B — joint moving-block bootstrap

{primary:,} paths per horizon at the primary block length of {PRIMARY_BLOCK}
sessions, {secondary:,} paths for the {v['other_blocks']}-session sensitivity
checks, seed {SEED}. Whole daily rows are resampled together — {v['pool']} — so
every contemporaneous relationship survives and only the order of the days is
destroyed beyond one block. Inside each path the implied-volatility proxy, the
trailing cash rate and the 200-day trend signal are **recomputed from the
resampled returns** by the same rules the historical model uses, after a
{WARMUP_SESSIONS}-session warm-up that is then discarded. Nothing is re-optimized
on any path, and no path chooses its own roll interval.

Three roll intervals are simulated: the {BENCHMARK_INTERVAL} benchmark and one
either side. The neighbours were fixed before any path was drawn and chosen for
what they can distinguish rather than what they earned — 13m is the same
schedule as 12m on a real expiry calendar, so the nearest distinct shorter
interval is 9m, and 18m is the only longer one Part A offers.

### What this cannot do, stated before the results

* It resamples the same forty years. It cannot produce a crash worse than 1987
  or a bond regime unlike the one observed. It is a distribution over
  *orderings*, not over futures, and no number in it is a forecast.
* **It is not neutral between the strategies.** A moving block of
  {PRIMARY_BLOCK} sessions preserves dependence within about a quarter and
  destroys it beyond. A rule with a 200-day lookback trades structure longer
  than the block, so resampling removes part of the signal it exists to exploit.
  The trend rule is handicapped here by construction, and the block-length table
  is the check on how much.
* Overlapping windows inside a path are not independent trials, so the pooled
  ten- and twenty-year probabilities carry far less precision than their
  denominators suggest.

### Thirty-year horizon

{markdown_table(summaries[30], summary_columns, summary_formats)}

### Terminal wealth per dollar, thirty years

{markdown_table(summaries[30], wealth_columns, wealth_formats)}

### Twenty-year horizon

{markdown_table(summaries[20], summary_columns, summary_formats)}

On this horizon the whole path *is* the twenty-year window, so
`prob_negative_20y` is the plain across-path frequency rather than a pooled
count over overlapping windows; the `negative_20y_basis` column in the CSV
records which of the two each row used.

### Where each strategy lands, against where the realized path put it

{markdown_table(ranks[ranks.horizon_years == 30],
                ['strategy', 'historical_rank', 'mean_rank', 'modal_rank',
                 'prob_at_historical_rank', 'prob_top_two', 'prob_bottom_two'],
                {'mean_rank': '.2f', 'prob_at_historical_rank': '.1%',
                 'prob_top_two': '.1%', 'prob_bottom_two': '.1%'})}

{v['mover']} loses the most ground — {v['mover_rank_shift']} places of mean rank
against where the realized path put it, and the only large deterioration in the
table. It ranked {v['trend_rank']} of {v['strategies']} on the realized path,
earning {v['trend_realized']} there against a simulated median of
{v['trend_median']} (not a like-for-like pair: the first spans forty years and the
second thirty, which is why rank rather than CAGR is the comparison above). Its
modal rank is {v['trend_modal']}, and it finishes below the unlevered index on
{v['trend_below']} of paths. That agrees
with `letf.null_model`, which found the same advantage indistinguishable from a
permutation placebo and concentrated in about twenty sessions — but it is *not*
independent evidence of it, and the handicap above is real. The block-length
comparison is the place to look: the median is {v['trend_short_block']} at
{v['short_block']} sessions and {v['trend_long_block']} at {v['long_block']}, which
{v['trend_direction']}, and either way falls far short of the
{v['trend_realized']} it realized.

### The option-specific diagnostics

{markdown_table(leaps_rows, ['strategy', 'mean_delta_exposure_median',
                'mean_delta_exposure_p5', 'min_exposure_after_loss_median',
                'consecutive_poor_rolls_mean', 'depleted_roll_fraction_mean',
                'underexposed_recovery_mean', 'paths_with_major_loss'],
                {'mean_delta_exposure_median': '.2f', 'mean_delta_exposure_p5': '.2f',
                 'min_exposure_after_loss_median': '.2f',
                 'consecutive_poor_rolls_mean': '.1%',
                 'depleted_roll_fraction_mean': '.1%',
                 'underexposed_recovery_mean': '.1%'})}

`min_exposure_after_loss` is the median across paths of the lowest delta reached
while the portfolio was more than {MAJOR_LOSS:.0%} below its own high — how far
the position deleverages exactly when it should not. `underexposed_recovery` is
the share of paths whose mean delta over the {RECOVERY_SESSIONS} sessions after
the worst trough was below {UNDEREXPOSED:.0%} of that path's own average; it is
measured against the structure's own level because these three do not target the
same exposure, and a fixed 1x threshold would score the {v['first']} structure as
permanently underexposed by design. `paths_with_major_loss` is the denominator for
both.

### Block length

{markdown_table(blocks[blocks.horizon_years == 30],
                ['strategy', 'block_days', 'paths', 'cagr_p5', 'cagr_p50', 'sd_cagr',
                 'prob_below_sp500'],
                {'cagr_p5': '.2%', 'cagr_p50': '.2%', 'sd_cagr': '.3f',
                 'prob_below_sp500': '.1%'})}

This table separates the strategies by how much they depend on the resampling
choice rather than on the resampled data. Across the three block lengths the
trend rule's median CAGR moves {v['block_move_trend']} — the largest movement in
the table, {v['block_monotone']}. The option structures move
{v['block_move_min']} to {v['block_move_max']}; the largest of those is
{v['block_move_heaviest']}, and a structure that levers every input more will
move more on any change to any of them, so that is arithmetic rather than a
second finding. Setting it aside, the remaining option rows move at most
{v['block_move_rest']}, roughly {v['block_move_ratio']} times less than the trend
rule.

![Roll interval and bootstrap distributions](leaps_roll_monte_carlo.png)

## The seven questions

**1. Is 12-13 month rolling materially different from 6- or 18-month rolling?**
Twelve and thirteen months are not distinguishable from each other: at most
{v['gap13']} volatility points apart, on an expiry calendar that gives them the
same rolls. Against six months the gap is real — up to {v['gap6']} volatility
points, {v['monotone']} — and against eighteen months up to
{v['gap18']} points, again favouring the longer one. But those are realized-path
gaps. In the bootstrap the median CAGR moves by only {v['mc_spread_low']} to
{v['mc_spread_high']} across the three simulated intervals — at most
{v['mc_spread_points']} volatility points — so most of the realized-path
preference for a long roll is sequence-specific rather than structural.

**2. Does a shorter roll interval reduce exposure drift enough to justify its cost?**
{v['shorter_verdict']}. A shorter roll does what it is meant to: in {v['mid']} the
standard deviation of delta exposure falls from {v['sd_benchmark']} at the
benchmark to {v['sd_short']} at six months, and the median exposure floor after a
major loss {v['floor_direction']} from {v['floor_benchmark']} to
{v['floor_short']} at nine months. It is paid for twice — {v['cost_mid']}bp a year of extra spread, and
{v['cagr_cost']} of CAGR in total, about {v['cagr_cost_points']} volatility points.
The drift removed is real but small; the cost is real and larger. The one case
for paying it is not in the averages: it is that the exposure floor after a loss
is the thing a shorter roll protects, and an investor who cannot add capital in a
drawdown may value that more than the CAGR it costs.

**3. Which LEAPS structure is least sensitive to roll interval?**
{v['least_interval']}, whose CAGR spread across all five intervals is
{v['least_interval_spread']} of its own return, against
{v['worst_interval_spread']} for the worst. Sensitivity to the roll interval
{v['budget_ordering']}.

**4. Which LEAPS structure is least sensitive to simulated market sequence?**
{v['least_sequence']}. Its CAGR dispersion across resampled orderings is
{v['amplification_least']} times the unlevered index's, the smallest of the three,
against {v['amplification_top']} for {v['top']}. It also holds its historical rank
on {v['least_sequence_rank']} of paths.

**5. Does the historical attractiveness of 85/30 survive alternative path ordering?**
Yes, and it improves in relative terms. On the realized path it ranked
{v['mid_rank']} of {v['strategies']}; across orderings its mean rank is
{v['mid_mean_rank']}, it finishes below the unlevered index on only
{v['mid_below']} of paths, and its median CAGR of {v['mid_median']} beats the
index's {v['index_median']} at a median drawdown {v['mid_drawdown_word']} the
index's ({v['mid_drawdown']} against {v['index_drawdown']}). Two caveats. It gains
rank mostly because the trend rule loses it, which is a weaker compliment than it
first appears; and it is not the structure that dominates the index outright —
{v['shallowest']} does, at a median drawdown of {v['shallowest_drawdown']} for a
median CAGR still above the index's.

**6. Does 95/50 remain attractive once sequence risk is considered?**
Its return ranking survives — modal rank {v['top_modal']}, top-two on
{v['top_two']} of paths, {v['top_stability_phrase']}. Surviving does not make its
risk acceptable. A drawdown worse than 50% occurs on {v['top_dd50']} of paths
and worse than 75% on {v['top_dd75']}, and its fifth-percentile CAGR of
{v['top_p5']} is {v['top_p5_word']} the unlevered index's {v['index_p5']}. It pairs
{v['top_median_phrase']} with one of the worst tails: attractive on average,
punishing in exactly the orderings that matter most to someone who cannot add
capital.

**7. Is annual rolling a genuinely robust design choice, or merely a convenient convention?**
Robust, for a better reason than convention. No structure's CAGR moves by more
than {v['gap_any']} volatility points of pricing doubt across the five intervals;
twelve and thirteen months are the same portfolio on a real expiry calendar; and
in the bootstrap the choice of structure is worth about
{v['structure_over_interval']} times as much as the choice of interval. The
realized path prefers eighteen months, but that preference does not survive
resampling, and in {v['floor_long_phrase']} it is bought with a lower exposure
floor after losses. Annual
rolling is not optimal on the realized path. It is the interval whose answer
changes least when the path changes, which is the property worth having in a
parameter nobody can tune in advance.

## Limitations

* Option premia are modelled, not observed, and every LEAPS row inherits the
  biases listed in `letf.options`: a trailing volatility proxy that buys too
  cheaply just before a crash, and no equity skew, which makes deep in-the-money
  calls cheaper here than they are. Both flatter the option structures.
* The bootstrap resamples one realized history and preserves dependence only
  within a block. It handicaps the 200-day trend rule by construction; the
  block-length table bounds that effect without removing it.
* The dividend yield is resampled as a level rather than recomputed, so it jumps
  at block joins. Its effect on a two-year call is small beside the volatility
  assumption, but it is not zero.
* Simulated paths share one synthetic calendar taken from the front of the real
  one, so every path sees the same expiry dates and holiday structure.
* Cohort windows inside a path overlap heavily. Pooled probabilities are not
  binomial proportions and should not be given confidence intervals.
* The three structures are inputs from the earlier grid search. Nothing here
  re-searches strike, budget, roll interval, block length or volatility premium,
  which is what keeps this a falsification exercise — and also means it cannot
  discover a better structure than the ones it was handed.

"""
    (reports / 'leaps_roll_monte_carlo_results.md').write_text(text)


if __name__ == '__main__':
    main()
