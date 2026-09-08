"""Can the XND contracts that actually exist implement the Nasdaq LEAPS result?

`letf.nasdaq_leaps` and `letf.leaps_frontier` both buy roughly two-year calls on
the Nasdaq-100 and roll them annually. That is a modelling convention inherited
from the S&P work, and for the Nasdaq-100 index option it is currently not
buyable: XND expirations extend to about December 2027, so the longest contract
available is nearer fifteen months than twenty-four.

This module asks whether that gap matters. **Is the favourable Nasdaq LEAPS
result a property of the structure, or a property of having two-year contracts
to build it from?** Two structures — the conservative 80/25 and the balanced
85/30, the two an implementation would actually reach for — are run at a
fifteen-month initial maturity against their existing two-year selves, at three
prespecified roll intervals, with every other assumption held fixed: same
strikes, same premium budgets, same three-point volatility premium, same
hundred-basis-point spread, same unlevered long-Treasury sleeve, same listed
expiry calendar.

**The twelve-month roll on a fifteen-month contract is a boundary condition,
not a candidate.** It leaves about three months to expiry at the roll, which is
where a fixed-budget rule stops behaving like leverage and starts behaving like
a lottery ticket. It is included to show where the regime breaks, and reading it
as a recommendation would be reading this module backwards.

**What shortening the contract can do, and how each channel is measured.** A
shorter call decays faster (theta), carries less volatility exposure per dollar
(vega), holds a delta that moves more as spot moves (delta instability), is
replaced more often (turnover and spread drag), and — the channel that matters
most for a fixed premium budget — buys back less exposure after a fall, because
the same budget purchases a contract whose delta is more sensitive to being out
of the money. Each is measured directly rather than inferred from returns.

Theta and vega are finite differences of the *same* Black-Scholes pricer the
simulation uses, not a second closed form. A closed-form vega would need its own
proof of consistency with the pricer; a central difference of the pricer is
consistent by construction, and at a diagnostic's precision the difference is
invisible.

A QQQ-shaped bridge is carried alongside: the same Nasdaq economics at a
2.25-year maturity, which is roughly what QQQ LEAPS offer today. It models no
part of American exercise or of the ETF's own tracking, and exists only to price
the maturity axis — the question of whether a longer American contract is worth
more than a shorter cash-settled European one.

Everything `letf.options` says about modelled premia applies unchanged. This
repository holds no option price history for the Nasdaq-100, no skew surface and
no bid/ask record, and a shorter contract sits on a different part of the term
structure than the one the three-point loading was ever meant to describe.
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

from .cohorts import cohort_cagrs, nav_path
from .hedge_alternatives import (CRASHES, EXPIRY_MONTHS, IV_PREMIUM, MATURITY_YEARS,
                                 OPTION_SPREAD_BPS, TREASURY, cagr, markdown_table,
                                 max_drawdown)
from .leaps_robustness import (Horizon, MAJOR_LOSS, PRIMARY_BLOCK,
                               RECOVERY_SESSIONS, SEED, SLOT as BASE_SLOT,
                               METRICS as BASE_METRICS, TREASURY_R, UNDEREXPOSED,
                               WARMUP_SESSIONS, build_path, load, monte_carlo,
                               path_metrics, leaps_extras, phrase)
from .nasdaq_leaps import (NASDAQ, NDX_PRICE, NDX_TOTAL, NDX_YIELD, PROXY_THROUGH,
                           nasdaq_pool, underlying_inputs)
from .options import (LeapsRule, YEAR, black_scholes_call, leaps_arrays, roll_schedule,
                      simulate_leaps_arrays)
from .provenance import FLOAT_FORMAT, sha, source_hashes, stable_floats

# The two structures an implementation would actually reach for, and the only two
# tested. Nothing here searches strike or budget; both are inherited.
STRUCTURES = {'NDX_LEAPS_80_25_TREASURY': (.80, .25),
              'NDX_LEAPS_85_30_TREASURY': (.85, .30)}

CONTROL_MATURITY = MATURITY_YEARS
CONTROL_ROLL = 1.
# What XND currently offers: expirations out to roughly December 2027, so about
# fifteen months rather than twenty-four.
SHORT_MATURITY = 15 / 12
SHORT_ROLLS = (.5, .75, 1.)
# Roughly what QQQ LEAPS reach today. A maturity axis only — no American
# exercise, no ETF tracking.
BRIDGE_MATURITY = 2.25
BRIDGE_ROLL = 1.

MC_PATHS, MC_HORIZONS = 3000, (10, 20, 30)
DRAWDOWN_THRESHOLDS = (.50, .60, .75)
PERCENTILES = (5, 10, 50, 90, 95)
DELTA_PERCENTILES = (5, 50, 95)
# One session of decay, which is what "per session" means for a daily series.
THETA_STEP = 1 / 252
# Central difference in volatility. Small enough that the second-order term is
# invisible, large enough to stay far above double precision noise.
VEGA_STEP = 1e-4

# The prespecified decision rule, in CAGR points. Below `EQUIVALENT` the
# implementation is practically the same portfolio; above `DIFFERENT` it is not
# the strategy that was validated.
EQUIVALENT, DIFFERENT = .005, .01
# Tail metrics have to move too before "acceptable but different" is allowed to
# become "not equivalent" on its own.
TAIL_TOLERANCE = .05
P5_TOLERANCE = .005


@dataclass(frozen=True)
class Variant:
    """One contract specification: what is bought, and how often it is replaced."""
    name: str
    structure: str
    maturity_years: float
    roll_years: float
    family: str

    @property
    def rule(self) -> LeapsRule:
        moneyness, budget = STRUCTURES[self.structure]
        return LeapsRule(moneyness=moneyness, premium_budget=budget,
                         maturity_years=self.maturity_years,
                         roll_at_years=self.maturity_years - self.roll_years,
                         iv_premium=IV_PREMIUM, spread_bps=OPTION_SPREAD_BPS,
                         expiry_months=EXPIRY_MONTHS)


def _months(years: float) -> int:
    return round(years * 12)


def variants() -> dict:
    """Every specification under test, control first so tables read against it."""
    out = {}
    for structure in STRUCTURES:
        stem = structure.replace('NDX_LEAPS_', 'NDX_').replace('_TREASURY', '')
        out[f'{stem}_24M_ROLL12M'] = Variant(
            f'{stem}_24M_ROLL12M', structure, CONTROL_MATURITY, CONTROL_ROLL, 'control')
        for roll in SHORT_ROLLS:
            name = f'{stem}_15M_ROLL{_months(roll)}M'
            out[name] = Variant(name, structure, SHORT_MATURITY, roll, 'XND 15m')
        name = f'{stem}_27M_ROLL12M'
        out[name] = Variant(name, structure, BRIDGE_MATURITY, BRIDGE_ROLL, 'QQQ bridge')
    return out


VARIANTS = variants()
CONTROL = {v.structure: f"{v.name}" for v in VARIANTS.values() if v.family == 'control'}
CONTROL_OF = {name: CONTROL[v.structure] for name, v in VARIANTS.items()}


# ----------------------------------------------------------------------------
# Historical path
# ----------------------------------------------------------------------------

def run_variant(market, safe: pd.Series, variant: Variant, spread_bps=None):
    """One specification on the realized Nasdaq path against the Treasury sleeve."""
    spot, dividend, riskfree, vol = market[NASDAQ]
    rule = variant.rule if spread_bps is None else replace(variant.rule,
                                                           spread_bps=spread_bps)
    arrays = leaps_arrays(spot, safe, dividend, riskfree, vol)
    return simulate_leaps_arrays(*arrays, roll_schedule(spot.index, rule))


def held_contract(schedule, spot: np.ndarray, days: np.ndarray):
    """Strike and expiry of the contract held on each close.

    Reconstructed from the schedule rather than returned by the simulator,
    because the simulator's job is wealth and this is a diagnostic. On a roll
    session two contracts touch the same close; the one bought there wins, since
    it is the one carried forward and the one whose decay the next year pays.
    """
    starts = schedule.starts
    ends = np.r_[starts[1:], len(spot) - 1]
    strike = np.full(len(spot), np.nan)
    expiry = np.full(len(spot), np.nan)
    for index, begin in enumerate(starts):
        stop = int(ends[index]) + 1
        strike[begin:stop] = schedule.rule.moneyness * spot[begin]
        expiry[begin:stop] = schedule.expiry_days[index]
    return strike, expiry


def greeks(market, schedule) -> dict:
    """Theta and vega of the position actually held, per dollar of NAV.

    Both are finite differences of the pricer the simulation uses. `theta` is the
    value the contract loses over one session with spot, volatility and rates
    unchanged — the decay a holder pays for time alone, which is the channel a
    shorter contract is supposed to worsen. `vega` is the value it gains per unit
    of volatility, which a shorter contract carries less of.

    Both are scaled to the portfolio by the option leg's weight rather than by a
    contract count: the position is `weight * NAV / value` contracts, so
    `weight * greek / value` is the portfolio's exposure per dollar of NAV
    whatever the position size.
    """
    spot, dividend, riskfree, vol = (series.to_numpy() for series in market[NASDAQ])
    closes = market[NASDAQ][0].index
    days = (closes - closes[0]).days.to_numpy().astype(float)
    strike, expiry = held_contract(schedule, spot, days)
    live = np.isfinite(strike)
    remaining = np.maximum((expiry[live] - days[live]) / YEAR, 0.)
    args = (spot[live], strike[live], riskfree[live], dividend[live])
    value = black_scholes_call(*args, vol[live], remaining)
    decayed = black_scholes_call(*args, vol[live], np.maximum(remaining - THETA_STEP, 0.))
    up = black_scholes_call(*args, vol[live] + VEGA_STEP, remaining)
    down = black_scholes_call(*args, np.maximum(vol[live] - VEGA_STEP, 1e-8), remaining)
    positive = value > 0
    theta = np.where(positive, (decayed - value) / np.where(positive, value, 1.), 0.)
    vega = np.where(positive, (up - down) / (2 * VEGA_STEP)
                    / np.where(positive, value, 1.), 0.)
    return dict(live=live, remaining=remaining, theta_per_value=theta,
                vega_per_value=vega)


def recovery(exposures: np.ndarray, navs: np.ndarray) -> dict:
    """What the position looks like after a major fall, and through the year after.

    The question a fixed premium budget raises is not how far the portfolio falls
    but what it owns on the way back up: the same budget buys a smaller delta
    once spot has moved away from the strike, and a shorter contract buys a
    smaller one still. Thresholds are `letf.leaps_robustness`'s, unchanged.
    """
    drawdown = navs / np.maximum.accumulate(navs) - 1
    hurt = drawdown <= -MAJOR_LOSS
    mean = float(exposures.mean())
    if not hurt.any():
        return dict(sessions_after_major_loss=0, min_exposure_after_loss=np.nan,
                    mean_exposure_after_loss=np.nan, recovery_exposure=np.nan,
                    recovery_exposure_ratio=np.nan, underexposed_recovery=False)
    trough = int(drawdown.argmin())
    window = exposures[trough:trough + RECOVERY_SESSIONS]
    return dict(sessions_after_major_loss=int(hurt.sum()),
                min_exposure_after_loss=float(exposures[hurt].min()),
                mean_exposure_after_loss=float(exposures[hurt].mean()),
                recovery_exposure=float(window.mean()),
                recovery_exposure_ratio=float(window.mean() / mean),
                underexposed_recovery=bool(window.mean() < UNDEREXPOSED * mean))


def historical_row(inputs, market, variant: Variant) -> dict:
    """Everything the realized path says about one specification."""
    safe = inputs.daily.loc[inputs.ix, TREASURY]
    path = run_variant(market, safe, variant)
    free = run_variant(market, safe, variant, spread_bps=0.)
    closes = market[NASDAQ][0].index
    returns = pd.Series(path.navs, index=closes).pct_change().dropna()
    free_returns = pd.Series(free.navs, index=closes).pct_change().dropna()
    rolls = pd.DataFrame(path.rolls)
    # The final contract is held to the end of the sample rather than sold, so it
    # has no realized holding period and no exit value; averaging it in would
    # flatter both.
    closed = rolls.iloc[:-1]
    years = (returns.index[-1] - returns.index[0]).days / 365.25
    wealth = nav_path(returns, inputs.calendar)
    ten, twenty, thirty = (cohort_cagrs(wealth, horizon) for horizon in (10, 20, 30))
    measured = greeks(market, roll_schedule(closes, variant.rule))
    weights = path.option_weights[measured['live']]
    row = dict(
        variant=variant.name, structure=variant.structure, family=variant.family,
        moneyness=STRUCTURES[variant.structure][0],
        premium_budget=STRUCTURES[variant.structure][1],
        maturity_months=_months(variant.maturity_years),
        roll_months=_months(variant.roll_years),
        cagr=cagr(returns), terminal_multiple=float((1 + returns).prod()),
        annualized_volatility=float(returns.std(ddof=1) * np.sqrt(252)),
        max_drawdown=max_drawdown(returns),
        mean_delta_exposure=float(path.exposures.mean()),
        delta_exposure_sd=float(path.exposures.std(ddof=1)),
        mean_option_weight=float(path.option_weights.mean()),
        option_turnover_per_year=float((1 - rolls.safe_weight).sum() / years),
        cost_drag_bps=float((cagr(free_returns) - cagr(returns)) * 10000),
        cohort_10y_min_cagr=float(ten.min()),
        cohort_20y_min_cagr=float(twenty.min()),
        cohort_20y_median_cagr=float(np.median(twenty)),
        cohort_30y_min_cagr=float(thirty.min()),
        cohort_30y_median_cagr=float(np.median(thirty)),
        rolls=int(len(rolls)),
        mean_entry_years=float(rolls.entry_years.mean()),
        mean_exit_years=float(closed.exit_years.mean()),
        mean_held_years=float(closed.held_years.mean()),
        mean_premium_fraction_of_nav=float((rolls.premium / rolls.wealth).mean()),
        mean_exit_premium_ratio=float(closed.exit_premium_ratio.mean()),
        # Annualized, and signed as a drag: the fraction of NAV the position
        # loses per year to the passage of time alone.
        theta_burden_per_year=float((weights * measured['theta_per_value']).mean() * 252),
        vega_per_nav=float((weights * measured['vega_per_value']).mean()),
        mean_remaining_years=float(measured['remaining'].mean()))
    for level in DELTA_PERCENTILES:
        row[f'delta_exposure_p{level}'] = float(np.percentile(path.exposures, level))
    row.update(recovery(path.exposures, path.navs))
    for event, (start, end) in CRASHES.items():
        row[event] = float((1 + returns.loc[start:end]).prod() - 1)
    return row


def historical_table(inputs, market) -> pd.DataFrame:
    return pd.DataFrame([historical_row(inputs, market, variant)
                         for variant in VARIANTS.values()])


# ----------------------------------------------------------------------------
# Shared-path Monte Carlo
# ----------------------------------------------------------------------------

EXTRA_METRICS = ('delta_p5', 'delta_p50', 'delta_p95', 'mean_option_weight',
                 'turnover_per_year')
METRICS = tuple(BASE_METRICS) + EXTRA_METRICS
SLOT = {name: index for index, name in enumerate(METRICS)}


def build_horizon(inputs, years: int) -> Horizon:
    """One horizon's calendar, cohort windows and per-variant roll schedules.

    `letf.leaps_robustness.build_horizon` fixes the maturity at the repository's
    two-year convention, which is the one thing this study varies, so the
    schedules are built from each variant's own rule. Everything else — the
    calendar, the warm-up, the cohort windows — is that function's arithmetic,
    and `test_xnd_short_maturity` pins the two against each other on the control.
    """
    from .cohorts import cohort_windows
    closes_all = inputs.spot.index
    entry = WARMUP_SESSIONS
    end = closes_all.searchsorted(closes_all[entry] + pd.DateOffset(years=years))
    if end >= len(closes_all):
        raise ValueError('Comparison window is too short for this horizon plus warm-up')
    calendar = closes_all[:end + 1]
    closes = calendar[entry:]
    schedules = {name: roll_schedule(closes, variant.rule)
                 for name, variant in VARIANTS.items()}
    unit = pd.Series(np.ones(len(closes)), index=closes)
    cohorts = {}
    for horizon in (10, 20):
        starts, finishes = cohort_windows(unit, horizon)
        elapsed = (closes[finishes] - closes[starts]).days.to_numpy().astype(float)
        cohorts[horizon] = (starts, finishes, elapsed)
    codes = closes[1:].to_period('Q')
    quarters = np.flatnonzero(np.r_[True, codes[1:] != codes[:-1]])
    return Horizon(years, calendar, closes,
                   (closes - closes[0]).days.to_numpy().astype(float), quarters,
                   schedules, cohorts, float((closes[-1] - closes[0]).days))


def run_path(sample: np.ndarray, horizon: Horizon, _spec) -> dict:
    """Every variant on one resampled Nasdaq path, against the same sleeve."""
    price, vol, riskfree, dividend, _ = build_path(
        sample, horizon.calendar, signal=False, price_col=NDX_PRICE, yield_col=NDX_YIELD)
    entry = WARMUP_SESSIONS
    spot, q = price[entry:], dividend[entry:]
    rate, sigma = riskfree[entry:], vol[entry:]
    growth = np.r_[1., 1 + sample[entry:, TREASURY_R]]
    years = horizon.elapsed_days / 365.25

    wealth = np.r_[1., np.cumprod(1 + sample[entry:, NDX_TOTAL])]
    results = {NASDAQ: np.r_[path_metrics(wealth, horizon)[0],
                             np.full(len(EXTRA_METRICS), np.nan)]}
    for name in VARIANTS:
        path = simulate_leaps_arrays(spot, growth, q, rate, sigma, horizon.days,
                                     horizon.schedules[name])
        out, drawdown = path_metrics(path.navs, horizon)
        leaps_extras(out, drawdown, path.exposures, path.rolls)
        record = np.r_[out, np.zeros(len(EXTRA_METRICS))]
        for level in DELTA_PERCENTILES:
            record[SLOT[f'delta_p{level}']] = float(np.percentile(path.exposures, level))
        record[SLOT['mean_option_weight']] = float(path.option_weights.mean())
        record[SLOT['turnover_per_year']] = float(
            sum(1 - roll['safe_weight'] for roll in path.rolls) / years)
        results[name] = record
    return results


def summarize(store: dict, horizon: Horizon, paths: int) -> pd.DataFrame:
    """The distribution, with every relative comparison paired path by path.

    Each variant is scored on the same resampled worlds as its own control, so
    "probability of trailing the two-year rule" is a property of the pair rather
    than of two marginal distributions that happen to have been drawn separately.
    """
    index = store[NASDAQ][:, BASE_SLOT['terminal_wealth']]
    rows = []
    for name, values in store.items():
        rates = values[:, BASE_SLOT['cagr']]
        terminal = values[:, BASE_SLOT['terminal_wealth']]
        drawdown = values[:, BASE_SLOT['max_drawdown']]
        variant = VARIANTS.get(name)
        control = (store[CONTROL_OF[name]][:, BASE_SLOT['terminal_wealth']]
                   if name in CONTROL_OF else None)
        row = dict(variant=name, horizon_years=horizon.years, paths=paths,
                   block_days=PRIMARY_BLOCK,
                   structure=variant.structure if variant else NASDAQ,
                   family=variant.family if variant else 'index',
                   maturity_months=_months(variant.maturity_years) if variant else np.nan,
                   roll_months=_months(variant.roll_years) if variant else np.nan,
                   mean_cagr=float(rates.mean()),
                   median_max_drawdown=float(np.median(drawdown)),
                   worst_max_drawdown=float(drawdown.min()),
                   prob_negative_cagr=float((rates < 0).mean()),
                   prob_below_nasdaq=float((terminal < index).mean())
                   if name != NASDAQ else 0.,
                   prob_below_control=float((terminal < control).mean())
                   if control is not None and name != CONTROL_OF.get(name) else 0.,
                   underexposed_recovery=float(np.nanmean(
                       values[:, BASE_SLOT['underexposed_recovery']]))
                   if np.isfinite(values[:, BASE_SLOT['underexposed_recovery']]).any()
                   else np.nan)
        for column in ('mean_delta_exposure', 'min_exposure_after_loss'):
            series = values[:, BASE_SLOT[column]]
            row[column] = (float(np.nanmedian(series)) if np.isfinite(series).any()
                           else np.nan)
        for column in EXTRA_METRICS:
            series = values[:, SLOT[column]]
            row[column] = (float(np.nanmedian(series)) if np.isfinite(series).any()
                           else np.nan)
        for level in PERCENTILES:
            row[f'cagr_p{level}'] = float(np.percentile(rates, level))
        for level in (5, 50, 95):
            row[f'terminal_wealth_p{level}'] = float(np.percentile(terminal, level))
        for threshold in DRAWDOWN_THRESHOLDS:
            row[f'prob_drawdown_worse_than_{round(threshold * 100)}'] = float(
                (drawdown <= -threshold).mean())
        rows.append(row)
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------
# The primary comparison and the decision rule
# ----------------------------------------------------------------------------

COMPARED = ('cagr_p50', 'cagr_p5', 'median_max_drawdown', 'prob_drawdown_worse_than_60',
            'turnover_per_year', 'mean_delta_exposure', 'underexposed_recovery')


def comparison(monte: pd.DataFrame, historical: pd.DataFrame) -> pd.DataFrame:
    """Short maturity minus its own two-year control, on the same worlds.

    Signed so that a negative CAGR difference is return given up and a positive
    drawdown difference is a shallower loss, which is the direction a reader
    expects a "difference from the control" to run.
    """
    hist = historical.set_index('variant')
    rows = []
    for name, variant in VARIANTS.items():
        if variant.family == 'control':
            continue
        control = CONTROL_OF[name]
        row = dict(variant=name, control=control, structure=variant.structure,
                   family=variant.family,
                   maturity_months=_months(variant.maturity_years),
                   roll_months=_months(variant.roll_years),
                   cost_drag_difference_bps=float(hist.loc[name, 'cost_drag_bps']
                                                  - hist.loc[control, 'cost_drag_bps']),
                   turnover_ratio=float(hist.loc[name, 'option_turnover_per_year']
                                        / hist.loc[control, 'option_turnover_per_year']),
                   historical_cagr_difference=float(hist.loc[name, 'cagr']
                                                    - hist.loc[control, 'cagr']))
        for horizon in MC_HORIZONS:
            piece = monte[monte.horizon_years == horizon].set_index('variant')
            for column in COMPARED:
                row[f'{column}_{horizon}y'] = float(piece.loc[name, column]
                                                    - piece.loc[control, column])
        rows.append(row)
    return pd.DataFrame(rows)


def verdict(row, historical) -> tuple:
    """The prespecified three-way classification, with the reasons it fired.

    Read off the worse of the twenty- and thirty-year horizons throughout: an
    implementation that matches over thirty years and not over twenty has not
    matched, because thirty years is not the holding period anyone has.
    """
    lost = -min(row['cagr_p50_20y'], row['cagr_p50_30y'])
    p5_lost = -min(row['cagr_p5_20y'], row['cagr_p5_30y'])
    tail = max(row['prob_drawdown_worse_than_60_20y'],
               row['prob_drawdown_worse_than_60_30y'])
    deeper = -min(row['median_max_drawdown_20y'], row['median_max_drawdown_30y'])
    unstable = max(row['underexposed_recovery_20y'], row['underexposed_recovery_30y'])
    hist = historical.set_index('variant')
    ratio = float(hist.loc[row['variant'], 'recovery_exposure_ratio'])
    reasons = []
    if lost > DIFFERENT:
        reasons.append(f'median CAGR falls {lost:.2%}')
    if p5_lost > P5_TOLERANCE:
        reasons.append(f'fifth percentile falls {p5_lost:.2%}')
    if tail > TAIL_TOLERANCE:
        reasons.append(f'P(DD>60%) rises {tail:.1%}')
    if deeper > TAIL_TOLERANCE:
        reasons.append(f'median drawdown deepens {deeper:.1%}')
    if unstable > TAIL_TOLERANCE or (np.isfinite(ratio) and ratio < UNDEREXPOSED):
        reasons.append('exposure after a major loss is unstable')
    if reasons:
        return 'not implementation-equivalent', '; '.join(reasons)
    moved = []
    if lost > EQUIVALENT:
        moved.append(f'median CAGR falls {lost:.2%}')
    if row['turnover_ratio'] > 1.25:
        moved.append(f'turnover rises {row["turnover_ratio"]:.2f}x')
    if abs(row['cost_drag_difference_bps']) > 10:
        moved.append(f'spread drag moves {row["cost_drag_difference_bps"]:+.0f}bp')
    if tail > TAIL_TOLERANCE / 2:
        moved.append(f'P(DD>60%) rises {tail:.1%}')
    if moved:
        return 'acceptable but different', '; '.join(moved)
    return 'practically equivalent', 'inside every prespecified tolerance'


def classify(compared: pd.DataFrame, historical: pd.DataFrame) -> pd.DataFrame:
    verdicts = [verdict(row, historical) for _, row in compared.iterrows()]
    out = compared.copy()
    out['classification'] = [v for v, _ in verdicts]
    out['reasons'] = [r for _, r in verdicts]
    return out


# ----------------------------------------------------------------------------
# The five implementation channels
# ----------------------------------------------------------------------------

CHANNELS = {
    'theta': ('theta_burden_per_year',),
    'vega': ('vega_per_nav',),
    'delta instability': ('mean_delta_exposure', 'delta_exposure_sd', 'delta_exposure_p5',
                          'delta_exposure_p50', 'delta_exposure_p95',
                          'min_exposure_after_loss'),
    'roll frequency': ('rolls', 'option_turnover_per_year', 'cost_drag_bps',
                       'mean_premium_fraction_of_nav', 'mean_exit_premium_ratio'),
    'recovery': ('recovery_exposure', 'recovery_exposure_ratio'),
}
CHANNEL_COLUMNS = tuple(column for columns in CHANNELS.values() for column in columns)


def diagnostics_table(historical: pd.DataFrame, monte: pd.DataFrame) -> pd.DataFrame:
    """One row per variant per channel, against its own control.

    Laid out by channel rather than by metric because the question is not how
    much the answer moved but *through what*. A variant that loses return
    entirely through theta is a different implementation problem from one that
    loses it through exposure it cannot buy back after a fall.
    """
    hist = historical.set_index('variant')
    recovery_probability = {
        horizon: monte[monte.horizon_years == horizon].set_index(
            'variant')['underexposed_recovery'] for horizon in MC_HORIZONS}
    rows = []
    for name, variant in VARIANTS.items():
        control = CONTROL_OF[name]
        for channel, columns in CHANNELS.items():
            for column in columns:
                value = float(hist.loc[name, column])
                against = float(hist.loc[control, column])
                rows.append(dict(
                    variant=name, structure=variant.structure, family=variant.family,
                    maturity_months=_months(variant.maturity_years),
                    roll_months=_months(variant.roll_years), channel=channel,
                    metric=column, value=value, control_value=against,
                    difference=value - against,
                    ratio=value / against if against else np.nan))
        for horizon in MC_HORIZONS:
            series = recovery_probability[horizon]
            rows.append(dict(
                variant=name, structure=variant.structure, family=variant.family,
                maturity_months=_months(variant.maturity_years),
                roll_months=_months(variant.roll_years), channel='recovery',
                metric=f'prob_underexposed_recovery_{horizon}y',
                value=float(series.loc[name]), control_value=float(series.loc[control]),
                difference=float(series.loc[name] - series.loc[control]),
                ratio=float(series.loc[name] / series.loc[control])
                if series.loc[control] else np.nan))
    return pd.DataFrame(rows)


SHORT = {'NDX_LEAPS_80_25_TREASURY': '80/25', 'NDX_LEAPS_85_30_TREASURY': '85/30'}
FAMILY_STYLE = {'control': ('C0', 'o'), 'XND 15m': ('C3', '^'), 'QQQ bridge': ('C2', 's')}


def figure(path: Path, monte: pd.DataFrame, historical: pd.DataFrame):
    """Two panels: what the maturity costs, and what it does to the delta."""
    fig, axes = plt.subplots(1, 2, figsize=(13.4, 5.4), constrained_layout=True)
    thirty = monte[monte.horizon_years == 30].set_index('variant')

    ax = axes[0]
    for family, (colour, marker) in FAMILY_STYLE.items():
        piece = thirty[thirty.family == family]
        ax.scatter(piece.median_max_drawdown, piece.cagr_p50, marker=marker, s=70,
                   color=colour, edgecolors='k', linewidths=.6, label=family, zorder=3)
    for name in VARIANTS:
        row = thirty.loc[name]
        ax.annotate(name.replace('NDX_', ''), (row.median_max_drawdown, row.cagr_p50),
                    textcoords='offset points', xytext=(6, -3), fontsize=7)
    index = thirty.loc[NASDAQ]
    ax.scatter(index.median_max_drawdown, index.cagr_p50, marker='D', s=80, color='k',
               label='Nasdaq-100 1x', zorder=4)
    ax.set_xlabel('median max drawdown')
    ax.set_ylabel('median 30-year CAGR')
    for axis in (ax.xaxis, ax.yaxis):
        axis.set_major_formatter(PercentFormatter(1))
    ax.legend(fontsize=8, loc='lower left')
    ax.set_title('A. What the shorter contract costs')

    ax = axes[1]
    rows = historical.set_index('variant').loc[list(VARIANTS)]
    positions = np.arange(len(rows))
    for position, (name, row) in zip(positions, rows.iterrows()):
        colour = FAMILY_STYLE[row.family][0]
        ax.plot([row.delta_exposure_p5, row.delta_exposure_p95], [position, position],
                color=colour, lw=3, alpha=.55, solid_capstyle='butt')
        ax.plot(row.delta_exposure_p50, position, 'o', color=colour, markersize=6)
        ax.plot(row.min_exposure_after_loss, position, 'x', color='k', markersize=7,
                markeredgewidth=1.4)
    ax.set_yticks(positions, [n.replace('NDX_', '') for n in rows.index], fontsize=8)
    ax.invert_yaxis()
    ax.set_xlabel('delta-equivalent equity exposure')
    ax.set_title('B. p5-p95 range, median dot, worst after a 30% loss (x)')
    ax.grid(axis='x', alpha=.25)
    fig.savefig(path, dpi=160)
    plt.close(fig)


def run(root: Path, workers=None, paths=None):
    inputs = load(root)
    market, _ = underlying_inputs(inputs, root)
    historical = historical_table(inputs, market)
    pool = nasdaq_pool(inputs, market)
    count = paths or MC_PATHS

    frames = []
    for years in MC_HORIZONS:
        horizon = build_horizon(inputs, years)
        store = monte_carlo(pool, horizon, None, count, PRIMARY_BLOCK, SEED, workers,
                            runner=run_path)
        frames.append(summarize(store, horizon, count))
        print(f'  {years}y: {count} paths, block {PRIMARY_BLOCK}, '
              f'{len(VARIANTS)} variants')
    monte = pd.concat(frames, ignore_index=True)
    compared = classify(comparison(monte, historical), historical)
    diagnostics = diagnostics_table(historical, monte)

    reports = root / 'reports'
    outputs = {'xnd_short_maturity_historical.csv': historical,
               'xnd_short_maturity_monte_carlo.csv': monte,
               'xnd_short_maturity_diagnostics.csv': diagnostics,
               'xnd_short_maturity_comparison.csv': compared}
    for name, frame in outputs.items():
        frame.pipe(stable_floats).to_csv(reports / name, index=False,
                                         float_format=FLOAT_FORMAT)
    figure(reports / 'xnd_short_maturity.png', monte, historical)
    report(reports, inputs, historical, monte, compared, diagnostics, count)

    (reports / 'xnd_short_maturity_manifest.json').write_text(json.dumps({
        'window': [inputs.ix[0].date().isoformat(), inputs.ix[-1].date().isoformat()],
        'observations': int(len(inputs.ix)),
        'structures': {k: list(v) for k, v in STRUCTURES.items()},
        'variants': {name: dict(structure=v.structure, family=v.family,
                                maturity_years=v.maturity_years,
                                roll_years=v.roll_years)
                     for name, v in VARIANTS.items()},
        'control_maturity_years': CONTROL_MATURITY,
        'short_maturity_years': SHORT_MATURITY,
        'short_roll_years': list(SHORT_ROLLS),
        'bridge_maturity_years': BRIDGE_MATURITY,
        'bridge_note': 'a maturity axis only; no American exercise and no ETF tracking '
                       'is modelled, so it prices the length of the contract and '
                       'nothing else about QQQ',
        'expiry_months': list(EXPIRY_MONTHS),
        'iv_premium': IV_PREMIUM, 'option_spread_bps': OPTION_SPREAD_BPS,
        'iv_premium_retuned_for_short_maturity': False,
        'iv_premium_note': 'the three-point loading is the repository-wide assumption '
                           'and is not re-estimated for a shorter contract, which sits '
                           'on a different part of a term structure this repository '
                           'does not observe',
        'seed': SEED, 'block_days': PRIMARY_BLOCK, 'paths': count,
        'horizons': list(MC_HORIZONS), 'warmup_sessions': WARMUP_SESSIONS,
        'greeks': 'theta and vega are central differences of the same Black-Scholes '
                  'pricer the simulation uses, not a second closed form',
        'theta_step_years': THETA_STEP, 'vega_step': VEGA_STEP,
        'decision_rule': {'practically_equivalent_below': EQUIVALENT,
                          'not_equivalent_above': DIFFERENT,
                          'p5_tolerance': P5_TOLERANCE,
                          'tail_tolerance': TAIL_TOLERANCE,
                          'underexposed_threshold': UNDEREXPOSED},
        'nasdaq_proxy_through': PROXY_THROUGH,
        'option_prices': 'modelled with Black-Scholes on an assumed implied volatility; '
                         'this repository holds no Nasdaq-100 option price history, no '
                         'skew surface and no bid/ask record',
        'python': platform.python_version(), 'numpy': np.__version__,
        'pandas': pd.__version__, 'scipy': scipy.__version__,
        'matplotlib': matplotlib.__version__,
        'source_hashes': source_hashes(root, __spec__.name),
        'outputs_sha256': {name: sha(reports / name) for name in outputs},
    }, indent=2) + '\n')
    counts = compared.classification.value_counts()
    print(f'XND short maturity: {len(VARIANTS)} variants; '
          + '; '.join(f'{int(n)} {label}' for label, n in counts.items())
          + f'; {count} paths x {len(MC_HORIZONS)} horizons.')
    return historical, monte, compared


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('--workers', type=int, default=None)
    parser.add_argument('--paths', type=int, default=None)
    parser.add_argument('--offline', action='store_true', default=True)
    args = parser.parse_args()
    run(args.root, args.workers, args.paths)


# ----------------------------------------------------------------------------
# Report
# ----------------------------------------------------------------------------

def _channels(historical, compared) -> dict:
    """How large each channel is, control-relative, for every short variant.

    Not an additive decomposition and not offered as one: theta is offset by
    drift, and exposure and turnover interact. These are the *sizes* of the
    channels in comparable units, which is what "which channel explains it"
    can honestly be answered with.
    """
    hist = historical.set_index('variant')
    out = {}
    for _, row in compared.iterrows():
        name, control = row['variant'], row['control']
        lost = -min(row['cagr_p50_20y'], row['cagr_p50_30y']) * 10000
        spread = row['cost_drag_difference_bps']
        theta = -(hist.loc[name, 'theta_burden_per_year']
                  - hist.loc[control, 'theta_burden_per_year']) * 10000
        out[name] = dict(
            lost_bps=lost, spread_bps=spread, theta_bps=theta,
            spread_share=spread / lost if lost > 0 else np.nan,
            theta_share=theta / lost if lost > 0 else np.nan,
            delta_median=float(hist.loc[name, 'delta_exposure_p50']
                               - hist.loc[control, 'delta_exposure_p50']),
            delta_after_loss=float(hist.loc[name, 'min_exposure_after_loss']
                                   - hist.loc[control, 'min_exposure_after_loss']),
            recovery=float(hist.loc[name, 'recovery_exposure_ratio']
                           - hist.loc[control, 'recovery_exposure_ratio']),
            vega=float(hist.loc[name, 'vega_per_nav']
                       - hist.loc[control, 'vega_per_nav']),
            turnover=float(row['turnover_ratio']))
    return out


def _narrative(historical, monte, compared, diagnostics) -> dict:
    """Every number the prose states, derived from the tables it sits beside."""
    hist = historical.set_index('variant')
    thirty = monte[monte.horizon_years == 30].set_index('variant')
    twenty = monte[monte.horizon_years == 20].set_index('variant')
    ten = monte[monte.horizon_years == 10].set_index('variant')
    channels = _channels(historical, compared)
    short = compared[compared.family == 'XND 15m'].set_index('variant')
    bridge = compared[compared.family == 'QQQ bridge'].set_index('variant')

    verdicts = dict(zip(compared.variant, compared.classification))
    worst = short.index[np.argmax([-min(short.loc[n, 'cagr_p50_20y'],
                                        short.loc[n, 'cagr_p50_30y'])
                                   for n in short.index])]
    best = short.index[np.argmin([-min(short.loc[n, 'cagr_p50_20y'],
                                       short.loc[n, 'cagr_p50_30y'])
                                  for n in short.index])]
    by_roll = {}
    for months in (_months(r) for r in SHORT_ROLLS):
        names = [n for n in short.index if short.loc[n, 'roll_months'] == months]
        by_roll[months] = dict(
            variants=names,
            median=float(np.mean([min(short.loc[n, 'cagr_p50_20y'],
                                      short.loc[n, 'cagr_p50_30y']) for n in names])),
            turnover=float(np.mean([short.loc[n, 'turnover_ratio'] for n in names])),
            verdicts={verdicts[n] for n in names})
    bust = {n: float(hist.loc[n, '2000_2002_bust'] - hist.loc[CONTROL_OF[n], '2000_2002_bust'])
            for n in short.index}
    return dict(
        hist=hist, thirty=thirty, twenty=twenty, ten=ten, channels=channels,
        short=short, bridge=bridge, verdicts=verdicts, worst=worst, best=best,
        by_roll=by_roll, bust=bust,
        equivalent=[n for n in short.index
                    if verdicts[n] == 'practically equivalent'],
        acceptable=[n for n in short.index
                    if verdicts[n] == 'acceptable but different'],
        broken=[n for n in short.index
                if verdicts[n] == 'not implementation-equivalent'],
        bridge_verdicts={n: verdicts[n] for n in bridge.index})


HIST_COLUMNS = ['variant', 'family', 'maturity_months', 'roll_months', 'cagr',
                'terminal_multiple', 'annualized_volatility', 'max_drawdown',
                'mean_delta_exposure', 'delta_exposure_sd', 'mean_option_weight',
                'option_turnover_per_year', 'cost_drag_bps', 'cohort_10y_min_cagr',
                'cohort_20y_min_cagr', 'cohort_20y_median_cagr', 'cohort_30y_min_cagr',
                'cohort_30y_median_cagr']
ROLL_COLUMNS = ['variant', 'rolls', 'mean_entry_years', 'mean_exit_years',
                'mean_held_years', 'mean_premium_fraction_of_nav',
                'mean_exit_premium_ratio', 'mean_remaining_years']
STRESS_COLUMNS = ['variant', 'family'] + list(CRASHES)
MC_COLUMNS = ['variant', 'cagr_p5', 'cagr_p10', 'cagr_p50', 'cagr_p90', 'cagr_p95',
              'terminal_wealth_p5', 'terminal_wealth_p50', 'terminal_wealth_p95',
              'median_max_drawdown', 'prob_drawdown_worse_than_50',
              'prob_drawdown_worse_than_60', 'prob_drawdown_worse_than_75',
              'prob_negative_cagr', 'prob_below_nasdaq', 'prob_below_control']
COMPARE_COLUMNS = ['variant', 'roll_months', 'cagr_p50_20y', 'cagr_p5_20y',
                   'cagr_p50_30y', 'cagr_p5_30y', 'median_max_drawdown_30y',
                   'prob_drawdown_worse_than_60_30y', 'turnover_ratio',
                   'cost_drag_difference_bps', 'classification', 'reasons']


def _formats(frame, columns=None) -> dict:
    """Format spec per column, decided by dtype rather than a hand-kept list."""
    ratios = ('mean_delta_exposure', 'delta_exposure_sd', 'terminal_multiple',
              'mean_entry_years', 'mean_exit_years', 'mean_held_years',
              'mean_exit_premium_ratio', 'mean_remaining_years', 'turnover_ratio',
              'option_turnover_per_year', 'vega_per_nav', 'ratio',
              'recovery_exposure_ratio', 'delta_exposure_p5', 'delta_exposure_p50',
              'delta_exposure_p95', 'min_exposure_after_loss', 'recovery_exposure',
              'mean_exposure_after_loss')
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
        elif 'bps' in column:
            out[column] = ',.0f'
        elif column in ratios:
            out[column] = '.2f'
        else:
            out[column] = '.2%'
    return out


def _answers(v) -> str:
    """The eight prespecified questions, answered from the tables above."""
    channels, short, hist = v['channels'], v['short'], v['hist']
    lines = []

    losses = {n: -min(short.loc[n, 'cagr_p50_20y'], short.loc[n, 'cagr_p50_30y'])
              for n in short.index}
    lines.append(
        '**1. How much return is lost by shortening initial maturity from ~2 years to '
        '~15 months?** Between '
        f'{min(losses.values()):.2%} and {max(losses.values()):.2%} of median CAGR, '
        'read as the worse of the twenty- and thirty-year horizons: '
        + phrase([f'{n.replace("NDX_", "")} gives up {losses[n]:.2%}'
                  for n in short.index])
        + '. On the realized path the same shortening costs '
        + phrase([f'{short.loc[n, "historical_cagr_difference"]:+.2%}'
                  for n in short.index]) + '.')

    example = v['worst']
    channel = channels[example]
    lines.append(
        '**2. Which channel explains the difference?** Not the trading. For '
        f'{example.replace("NDX_", "")}, the worst case, the measured option-spread '
        f'drag rises by {channel["spread_bps"]:,.0f}bp against a {channel["lost_bps"]:,.0f}bp '
        f'loss — {channel["spread_share"]:.0%} of it — even though turnover runs at '
        f'{channel["turnover"]:.2f}x the control. '
        f'Theta accounts for {channel["theta_bps"]:,.0f}bp, {channel["theta_share"]:.0%}. '
        'What is left is exposure: the median delta '
        + ('falls' if channel['delta_median'] < 0 else 'rises')
        + f' by {abs(channel["delta_median"]):.2f} and the worst delta after a 30% loss '
        + ('falls' if channel['delta_after_loss'] < 0 else 'rises')
        + f' by {abs(channel["delta_after_loss"]):.2f}. '
        'These are the sizes of the channels in comparable units, not an additive '
        'decomposition — theta is offset by drift and exposure and turnover interact.')

    rolls = v['by_roll']
    lines.append(
        '**3. Does 6m, 9m or 12m rolling materially change the result?** '
        + phrase([f'{months}-month rolling gives up {-info["median"]:.2%} at '
                  f'{info["turnover"]:.2f}x the control\'s turnover'
                  for months, info in rolls.items()])
        + '. '
        + ('The three are not interchangeable: '
           if len({tuple(sorted(i['verdicts'])) for i in rolls.values()}) > 1 else
           'All three land on the same verdict: ')
        + phrase([f'{months}m is {phrase(sorted(info["verdicts"]))}'
                  for months, info in rolls.items()])
        + '. The twelve-month roll leaves about '
        f'{hist.loc[v["worst"] if rolls[12]["variants"][0] not in hist.index else rolls[12]["variants"][0], "mean_exit_years"]:.2f} '
        'years to expiry at the roll and is a boundary condition, not a candidate.')

    bust = v['bust']
    worse = [n for n, gap in bust.items() if gap < 0]
    lines.append(
        '**4. Does the shorter maturity materially worsen the dot-com episode?** '
        + phrase([f'{n.replace("NDX_", "")} {gap:+.1%}' for n, gap in bust.items()])
        + ' against its own control over 2000-2002. '
        + (f'{len(worse)} of the {len(bust)} short variants lose more; '
           if worse else 'None of the short variants loses more; ')
        + ('the shorter contract has less time value to give up in the fall, which is '
           'the one place a shorter maturity helps.'
           if len(worse) < len(bust) else
           'the shorter contract is rebought more often into a falling market, which is '
           'where a fixed budget hurts most.'))

    recovery = {n: channels[n]['recovery'] for n in short.index}
    lines.append(
        '**5. Does it worsen recovery after major drawdowns?** The mean delta over the '
        'twelve months after the worst trough, as a fraction of the variant\'s own mean '
        'exposure, moves '
        + phrase([f'{n.replace("NDX_", "")} {gap:+.2f}' for n, gap in recovery.items()])
        + '. '
        + (f'{sum(1 for g in recovery.values() if g < 0)} of {len(recovery)} recover '
           'with less exposure than the two-year rule does. '
           if any(g < 0 for g in recovery.values()) else
           'None recovers with less exposure than the two-year rule does. ')
        + 'In the bootstrap the probability of being materially underexposed through a '
          'recovery moves '
        + phrase([f'{n.replace("NDX_", "")} '
                  f'{v["thirty"].loc[n, "underexposed_recovery"] - v["thirty"].loc[CONTROL_OF[n], "underexposed_recovery"]:+.1%}'
                  for n in short.index]) + ' at thirty years.')

    p5 = {n: -min(short.loc[n, 'cagr_p5_20y'], short.loc[n, 'cagr_p5_30y'])
          for n in short.index}
    breached = [n for n, gap in p5.items() if gap > P5_TOLERANCE]
    lines.append(
        '**6. Does it materially change p5 20y or 30y CAGR?** The fifth percentile '
        'gives up '
        + phrase([f'{n.replace("NDX_", "")} {gap:.2%}' for n, gap in p5.items()])
        + f' against a {P5_TOLERANCE:.2%} tolerance. '
        + (f'{phrase([n.replace("NDX_", "") for n in breached])} '
           f'{"breach" if len(breached) > 1 else "breaches"} it.'
           if breached else 'None breaches it.'))

    lines.append(
        '**7. Is current XND availability a credible implementation of the validated '
        'Nasdaq LEAPS architecture?** '
        + (f'{phrase([n.replace("NDX_", "") for n in v["equivalent"]])} '
           f'{"are" if len(v["equivalent"]) > 1 else "is"} practically equivalent; '
           if v['equivalent'] else '')
        + (f'{phrase([n.replace("NDX_", "") for n in v["acceptable"]])} '
           f'{"are" if len(v["acceptable"]) > 1 else "is"} acceptable but different; '
           if v['acceptable'] else '')
        + (f'{phrase([n.replace("NDX_", "") for n in v["broken"]])} '
           f'{"are" if len(v["broken"]) > 1 else "is"} not implementation-equivalent'
           if v['broken'] else 'nothing falls outside the tolerances')
        + '. '
        + ('So yes, at the longer rolls — the contract set can carry the strategy, '
           'provided the roll is not pushed toward expiry.'
           if v['equivalent'] or v['acceptable'] else
           'So no: at every roll interval tested the fifteen-month contract changes the '
           'economics beyond the prespecified tolerances.'))

    bridge = v['bridge']
    gaps = {n: min(bridge.loc[n, 'cagr_p50_20y'], bridge.loc[n, 'cagr_p50_30y'])
            for n in bridge.index}
    lines.append(
        '**8. Is the maturity advantage of QQQ likely more important than XND\'s '
        'cleaner cash-settled European structure?** A 2.25-year contract on the same '
        'economics is worth '
        + phrase([f'{gap:+.2%}' for gap in gaps.values()])
        + ' of median CAGR against the two-year control, against the '
        + f'{min(losses.values()):.2%} to {max(losses.values()):.2%} the fifteen-month '
        'contract gives up. '
        + ('The maturity axis is therefore worth more than the structural one over the '
           'range tested — but this bridge models the *length* of a QQQ contract and '
           'nothing else about it: no American exercise, no early assignment, no ETF '
           'tracking error, no difference in spread or tax treatment. It bounds the '
           'question rather than answering it.'))
    return '\n\n'.join(lines)


def report(reports: Path, inputs, historical, monte, compared, diagnostics, paths):
    """Write the narrative from the numbers, never alongside them."""
    v = _narrative(historical, monte, compared, diagnostics)
    order = list(VARIANTS)
    hist = historical.set_index('variant').loc[order].reset_index()
    thirty = monte[monte.horizon_years == 30].set_index('variant')
    mc = thirty.loc[order + [NASDAQ]].reset_index()
    short_horizons = monte[monte.horizon_years != 30].pivot_table(
        index='variant', columns='horizon_years',
        values=['cagr_p5', 'cagr_p50', 'prob_drawdown_worse_than_60'])
    short_horizons.columns = [f'{a}_{b}y' for a, b in short_horizons.columns]
    short_horizons = short_horizons.loc[order].reset_index()
    channel_view = diagnostics[diagnostics.family != 'control'].copy()

    text = f"""# Can the XND contracts that exist implement the Nasdaq LEAPS strategy?

Generated by `letf.xnd_short_maturity`. Window {inputs.ix[0].date()} to {inputs.ix[-1].date()}, {len(inputs.ix):,} sessions, on the calendar, financing and option assumptions of `reports/nasdaq_leaps_comparison.md`.

Every Nasdaq LEAPS result in this repository buys roughly two-year calls and rolls
them annually. That maturity is inherited from the S&P work, and for the
Nasdaq-100 index option it is currently not buyable: XND expirations reach only to
about December 2027, so the longest contract available is nearer fifteen months
than twenty-four.

**Is the favourable Nasdaq result a property of the structure, or of having
two-year contracts to build it from?** The two structures an implementation would
reach for — {phrase([SHORT[s] for s in STRUCTURES])} — are run at a fifteen-month
initial maturity against their existing two-year selves, at {phrase([f'{_months(r)}-month' for r in SHORT_ROLLS])}
roll intervals. Everything else is held fixed: same strikes, same premium budgets,
same {IV_PREMIUM:.0%} volatility premium, same {OPTION_SPREAD_BPS:.0f}bp spread, same unlevered long-Treasury
sleeve, same listed expiry calendar.

**The twelve-month roll on a fifteen-month contract is a boundary condition, not a
candidate.** It leaves roughly three months to expiry at the roll, which is where a
fixed-budget rule stops behaving like leverage. It is here to show where the
regime breaks.

A **QQQ-shaped bridge** at {BRIDGE_MATURITY:.2f} years is carried alongside. It models the
*length* of a contract and nothing else about QQQ — no American exercise, no early
assignment, no ETF tracking, no difference in spread or tax treatment — and exists
only to price the maturity axis against the structural one.

## What the listed calendar actually delivers

A fifteen-month target is not a fifteen-month contract. Only a few expiries are
listed that far out, so the maturity actually bought drifts, and the roll dates
are not evenly spaced. This is measured rather than assumed.

{markdown_table(hist, ROLL_COLUMNS, _formats(hist, ROLL_COLUMNS))}

## Historical record, one realized path

{markdown_table(hist, HIST_COLUMNS, _formats(hist, HIST_COLUMNS))}

### Stress episodes

{markdown_table(hist, STRESS_COLUMNS, _formats(hist, STRESS_COLUMNS))}

The dot-com bust is the episode this comparison turns on and it is kept in full.
Nasdaq total returns before {PROXY_THROUGH} are a price-only proxy grossed up with an
assumed zero dividend yield; the 1987 column lies entirely inside that era.

## The five implementation channels

Shortening a contract can change the strategy through more than one route, and
which route it takes decides whether the change is a cost or a different
portfolio. Each is measured directly.

Theta and vega are central differences of the *same* Black-Scholes pricer the
simulation uses, not a second closed form: a closed form would need its own proof
of consistency, and a difference of the pricer is consistent by construction.
`theta_burden_per_year` is the fraction of NAV the position loses per year to the
passage of time alone, with spot, volatility and rates held still.

{markdown_table(channel_view, ['variant', 'channel', 'metric', 'value', 'control_value', 'difference'], _formats(channel_view, ['value', 'control_value', 'difference']))}

## Monte Carlo

{paths:,} paths on the existing {PRIMARY_BLOCK}-session moving-block bootstrap, at {phrase([str(y) for y in MC_HORIZONS])} years,
the repository's existing seed, **the same simulated worlds for every variant**.
Whole daily rows are drawn, so the Nasdaq, the Treasury sleeve and the dividend
level keep the relationships they had on the day. Every comparison against the
index or against a variant's own control is paired path by path.

### 30-year horizon

{markdown_table(mc, MC_COLUMNS, _formats(mc, MC_COLUMNS))}

### 10- and 20-year horizons

{markdown_table(short_horizons, list(short_horizons.columns), _formats(short_horizons))}

## Primary comparison and the decision rule

Short maturity minus its own two-year control, on the same worlds. A negative CAGR
difference is return given up; a negative drawdown difference is a deeper loss.

The classification is prespecified: **practically equivalent** when long-horizon
median CAGR moves by less than {EQUIVALENT:.1%} and the tails hold, **acceptable but
different** when it moves by less than {DIFFERENT:.1%} but turnover or tail behaviour
changes meaningfully, and **not implementation-equivalent** when median CAGR falls
by more than {DIFFERENT:.1%}, the fifth percentile falls by more than {P5_TOLERANCE:.1%}, drawdown
probabilities worsen by more than {TAIL_TOLERANCE:.0%}, or exposure after a major loss becomes
unstable. Every threshold is read off the *worse* of the twenty- and thirty-year
horizons, because an implementation that matches over thirty years and not over
twenty has not matched.

{markdown_table(compared, COMPARE_COLUMNS, _formats(compared, COMPARE_COLUMNS))}

## Conclusions

{_answers(v)}

## What would change these answers

The three-point volatility premium is the repository-wide assumption and is **not**
re-estimated for a shorter contract. That matters more here than in any earlier
module: a fifteen-month option sits on a different part of the implied-volatility
term structure than a two-year one, and this repository observes neither. If
short-dated Nasdaq options carry a larger premium over realized volatility than
long-dated ones — the usual shape — then every fifteen-month row above is
flattered, and the direction of that error is against the conclusion the
comparison reaches.

Nothing here models American exercise, early assignment, pin risk, or the
difference between settling in cash and settling in shares. The QQQ bridge is a
maturity axis only. And the bootstrap resamples one history, so it cannot produce
a term structure, a crash, or a dividend regime unlike the ones observed.
"""
    (reports / 'xnd_short_maturity_results.md').write_text(text)


if __name__ == '__main__':
    main()
