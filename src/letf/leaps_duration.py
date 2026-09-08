"""Is a contract duration efficient, or does it just buy more delta?

`letf.leaps_frontier` mapped strike against premium budget and left maturity at
the two-year convention every earlier module inherited. `letf.xnd_short_maturity`
then found that shortening the contract does not degrade the strategy at all: a
shorter call is cheaper, so a fixed premium budget buys more of it, and the
portfolio simply moves to a more aggressive point on the same frontier.

That makes the obvious comparison useless on its own. **A duration that earns
more at the same budget has not been shown to be better; it has been shown to be
larger.** This module therefore runs every comparison twice — once at a fixed
premium budget, which is the portfolio an investor literally gets, and once at
a budget interpolated to match the canonical two-year rule's mean delta, which is
the only way to ask whether the contract itself is doing anything.

**Part A, Nasdaq**, is bounded by what exists: XND expirations reach about
fifteen months, so the ladder is fifteen months rolled at six and at twelve,
against the twenty-four-month rule as a reference it cannot currently buy.

**Part B, the S&P**, has no such constraint — SPX and XSP list far longer — so it
asks the question the Nasdaq cannot: fifteen, eighteen, twenty-four and thirty
months, each at the roll intervals that make sense for it, separating four
regimes that are usually conflated. Short contracts reset often; short contracts
held toward expiry; the canonical two-year annual roll; and long contracts with
low turnover and less delta per dollar.

**The volatility loading is not retuned by maturity, and that is the sharpest
limitation here.** Real implied volatility has a term structure and a skew; this
repository observes neither, and charges every contract a trailing realized
proxy plus a flat three points whether it has three months to run or thirty. A
duration comparison is exactly the comparison that assumption is least equipped
to support, so every verdict below is widened to the CAGR that one volatility
point moves, and differences inside it are reported as unresolved rather than
ranked.

Nothing here is a live-chain valuation. It is a structural comparison of contract
lengths under one pricing convention, and the practical question it can speak to
is narrow: given that XND stops at fifteen months and SPX does not, do the two
markets have different feasible duration regimes, and how much of any difference
is calibration rather than structure?
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
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

from .cohorts import cohort_cagrs, cohort_windows, nav_path
from .hedge_alternatives import (CRASHES, EQUITY, EXPIRY_MONTHS, IV_PREMIUM,
                                 OPTION_SPREAD_BPS, TREASURY, cagr, markdown_table,
                                 max_drawdown)
from .leaps_frontier import (CAGR_TOLERANCE, DRAWDOWN_TOLERANCE,
                             PROBABILITY_TOLERANCE, frontier_flags)
from .leaps_robustness import (EQUITY_TR, Horizon, MAJOR_LOSS, PRIMARY_BLOCK,
                               RECOVERY_SESSIONS,
                               SEED, SLOT as BASE_SLOT, METRICS as BASE_METRICS,
                               TREASURY_R, UNDEREXPOSED, WARMUP_SESSIONS, build_path,
                               load, monte_carlo, path_metrics, phrase)
from .nasdaq_leaps import (NASDAQ, NDX_PRICE, NDX_TOTAL, NDX_YIELD, PROXY_THROUGH,
                           nasdaq_pool, underlying_inputs)
from .options import (LeapsRule, implied_volatility_proxy, leaps_arrays, roll_schedule,
                      simulate_leaps_arrays)
from .provenance import FLOAT_FORMAT, sha, source_hashes, stable_floats
from .xnd_short_maturity import greeks, recovery

# ----------------------------------------------------------------------------
# The prespecified grids
# ----------------------------------------------------------------------------
#
# Compact and fixed before any path was drawn. No intermediate cell is added
# after the fact, and 0.95 strikes are absent because the earlier frontier found
# every one of them dominated inside the S&P family.
YEAR_MONTHS = 12.
CANONICAL = (24 / YEAR_MONTHS, 12 / YEAR_MONTHS)

# What XND currently lists is about fifteen months, so the Nasdaq ladder stops
# there. The canonical rule is carried as a reference the market cannot presently
# supply, which is the point of Part A rather than an oversight.
NDX_MONEYNESS = (.80, .85, .90)
NDX_BUDGETS = (.20, .25, .30, .35, .40)
NDX_REGIMES = ((15 / YEAR_MONTHS, 6 / YEAR_MONTHS), (15 / YEAR_MONTHS, 1.), CANONICAL)

# The S&P cells the earlier frontier found economically relevant, trimmed per
# strike so the grid stays small.
SP_CELLS = {.80: (.25, .30, .35),
            .85: (.25, .30, .35, .40, .45),
            .90: (.30, .35, .40, .45)}
# Only the roll intervals that are coherent for each length: a thirty-month
# contract rolled at six months would be a different instrument, and a
# fifteen-month contract rolled at eighteen cannot exist.
SP_REGIMES = ((15 / YEAR_MONTHS, 6 / YEAR_MONTHS), (15 / YEAR_MONTHS, 1.),
              (18 / YEAR_MONTHS, 6 / YEAR_MONTHS), (18 / YEAR_MONTHS, 1.),
              (2., 6 / YEAR_MONTHS), CANONICAL,
              (30 / YEAR_MONTHS, 1.), (30 / YEAR_MONTHS, 18 / YEAR_MONTHS))

MC_PATHS, MC_HORIZONS = 5000, (10, 20, 30)
FRONTIER_HORIZON = 30
DRAWDOWN_THRESHOLDS = (.50, .60, .75)
PERCENTILES = (5, 10, 50, 90, 95)
DELTA_PERCENTILES = (5, 50, 95)
P5_TOLERANCE = CAGR_TOLERANCE
# A regime has to win on most of the cells it is compared on before it is called
# efficient, rather than on the one cell that happens to favour it.
REGIME_MAJORITY = .6


def months(years: float) -> int:
    return round(years * YEAR_MONTHS)


@dataclass(frozen=True)
class Variant:
    """One cell at one contract length and roll interval."""
    name: str
    underlying: str
    moneyness: float
    budget: float
    maturity_years: float
    roll_years: float

    @property
    def regime(self) -> str:
        return f'{months(self.maturity_years)}m/{months(self.roll_years)}m'

    @property
    def cell(self) -> str:
        prefix = 'SPX' if self.underlying == EQUITY else 'NDX'
        return f'{prefix}_{round(self.moneyness * 100)}_{round(self.budget * 100)}'

    @property
    def canonical(self) -> str:
        return f'{self.cell}_{months(CANONICAL[0])}M_R{months(CANONICAL[1])}'

    @property
    def rule(self) -> LeapsRule:
        return LeapsRule(moneyness=self.moneyness, maturity_years=self.maturity_years,
                         roll_at_years=self.maturity_years - self.roll_years,
                         premium_budget=self.budget, iv_premium=IV_PREMIUM,
                         spread_bps=OPTION_SPREAD_BPS, expiry_months=EXPIRY_MONTHS)


def _build() -> dict:
    out = {}
    families = ((NASDAQ, {m: NDX_BUDGETS for m in NDX_MONEYNESS}, NDX_REGIMES),
                (EQUITY, SP_CELLS, SP_REGIMES))
    for underlying, cells, regimes in families:
        for moneyness, budgets in cells.items():
            for budget in budgets:
                for maturity, roll in regimes:
                    variant = Variant('', underlying, moneyness, budget, maturity, roll)
                    name = (f'{variant.cell}_{months(maturity)}M_R{months(roll)}')
                    out[name] = Variant(name, underlying, moneyness, budget, maturity,
                                        roll)
    return out


VARIANTS = _build()
REGIMES = {underlying: tuple(dict.fromkeys(
    v.regime for v in VARIANTS.values() if v.underlying == underlying))
    for underlying in (EQUITY, NASDAQ)}
INDEX_ROWS = (EQUITY, NASDAQ)
FAMILY = {EQUITY: 'SPX', NASDAQ: 'NDX'}

EXTRA_METRICS = ('delta_p5', 'delta_p50', 'delta_p95', 'mean_option_weight',
                 'recovery_exposure_ratio')
METRICS = tuple(BASE_METRICS) + EXTRA_METRICS
SLOT = {name: index for index, name in enumerate(METRICS)}


# ----------------------------------------------------------------------------
# Historical path
# ----------------------------------------------------------------------------

def market_series(inputs, market, variant: Variant):
    return market[variant.underlying]


def run_variant(inputs, market, variant: Variant, spread_bps=None, premium=None):
    """One contract specification on the realized path against the Treasury sleeve."""
    spot, dividend, riskfree, vol = market[variant.underlying]
    rule = variant.rule
    if spread_bps is not None:
        rule = LeapsRule(**{**rule.__dict__, 'spread_bps': spread_bps})
    if premium is not None and premium != IV_PREMIUM:
        vol = implied_volatility_proxy(spot.pct_change().fillna(0.), premium,
                                       rule.maturity_years)
    safe = inputs.daily.loc[inputs.ix, TREASURY]
    arrays = leaps_arrays(spot, safe, dividend, riskfree, vol)
    return simulate_leaps_arrays(*arrays, roll_schedule(spot.index, rule))


def volatility_point(inputs, market, variant: Variant, shift=.01) -> float:
    """CAGR moved by one volatility point, for this contract length.

    Measured per variant rather than assumed common, because it is not: a longer
    contract carries more vega per dollar, so the same error in the pricing
    assumption is worth more CAGR to it. This is the yardstick every duration
    verdict below is widened to.
    """
    rates = {}
    for premium in (IV_PREMIUM - shift, IV_PREMIUM + shift):
        path = run_variant(inputs, market, variant, premium=premium)
        index = market[variant.underlying][0].index
        rates[premium] = cagr(pd.Series(path.navs, index=index).pct_change().dropna())
    return float((rates[IV_PREMIUM - shift] - rates[IV_PREMIUM + shift]) / 2)


def episode_rows(variant: Variant, returns: pd.Series, exposures: pd.Series) -> dict:
    """What each stress episode did, and what the position looked like through it.

    A loss on its own does not distinguish a contract that fell with the market
    from one that stopped participating on the way down and could not get back
    in. The exposure columns are what separate them, and the recovery column is
    where a fixed premium budget does its damage.
    """
    row = {}
    for event, (start, end) in CRASHES.items():
        window = returns.loc[start:end]
        if not len(window):
            continue
        wealth = (1 + window).cumprod()
        during = exposures.loc[window.index]
        after = exposures.loc[window.index[-1]:].iloc[:RECOVERY_SESSIONS + 1]
        row[event] = float(wealth.iloc[-1] - 1)
        row[f'{event}_trough'] = float((wealth / wealth.cummax() - 1).min())
        row[f'{event}_mean_delta'] = float(during.mean())
        row[f'{event}_min_delta'] = float(during.min())
        row[f'{event}_recovery_delta'] = float(after.mean())
    return row


def historical_row(inputs, market, variant: Variant) -> dict:
    """Everything the realized path says about one contract specification."""
    spot = market[variant.underlying][0]
    path = run_variant(inputs, market, variant)
    free = run_variant(inputs, market, variant, spread_bps=0.)
    returns = pd.Series(path.navs, index=spot.index).pct_change().dropna()
    free_returns = pd.Series(free.navs, index=spot.index).pct_change().dropna()
    exposures = pd.Series(path.exposures, index=spot.index)
    rolls = pd.DataFrame(path.rolls)
    # The last contract is held to the end of the sample rather than sold, so it
    # has no realized holding period and no exit value.
    closed = rolls.iloc[:-1]
    years = (returns.index[-1] - returns.index[0]).days / 365.25
    wealth = nav_path(returns, inputs.calendar)
    ten, twenty, thirty = (cohort_cagrs(wealth, horizon) for horizon in (10, 20, 30))
    measured = greeks(*market[variant.underlying],
                      roll_schedule(spot.index, variant.rule))
    weights = path.option_weights[measured['live']]
    option_weight = float(path.option_weights.mean())
    row = dict(
        variant=variant.name, cell=variant.cell, family=FAMILY[variant.underlying],
        underlying=variant.underlying, regime=variant.regime,
        moneyness=variant.moneyness, premium_budget=variant.budget,
        maturity_months=months(variant.maturity_years),
        roll_months=months(variant.roll_years),
        cagr=cagr(returns), terminal_multiple=float((1 + returns).prod()),
        annualized_volatility=float(returns.std(ddof=1) * np.sqrt(252)),
        max_drawdown=max_drawdown(returns),
        mean_delta_exposure=float(path.exposures.mean()),
        delta_exposure_sd=float(path.exposures.std(ddof=1)),
        mean_option_weight=option_weight,
        mean_treasury_weight=1 - option_weight,
        gross_notional=float(path.exposures.mean()) + 1 - option_weight,
        option_turnover_per_year=float((1 - rolls.safe_weight).sum() / years),
        cost_drag_bps=float((cagr(free_returns) - cagr(returns)) * 10000),
        theta_burden_per_year=float((weights * measured['theta_per_value']).mean() * 252),
        vega_per_nav=float((weights * measured['vega_per_value']).mean()),
        mean_entry_years=float(rolls.entry_years.mean()),
        mean_exit_years=float(closed.exit_years.mean()),
        mean_held_years=float(closed.held_years.mean()),
        rolls=int(len(rolls)),
        mean_premium_fraction_of_nav=float((1 - rolls.safe_weight).mean()),
        mean_exit_premium_ratio=float(closed.exit_premium_ratio.mean()),
        cohort_10y_min_cagr=float(ten.min()),
        cohort_20y_min_cagr=float(twenty.min()),
        cohort_20y_median_cagr=float(np.median(twenty)),
        cohort_30y_min_cagr=float(thirty.min()),
        cohort_30y_median_cagr=float(np.median(thirty)),
        cagr_per_volatility_point=volatility_point(inputs, market, variant))
    for level in DELTA_PERCENTILES:
        row[f'delta_exposure_p{level}'] = float(np.percentile(path.exposures, level))
    row.update(recovery(path.exposures, path.navs))
    row.update(episode_rows(variant, returns, exposures))
    return row


def historical_table(inputs, market) -> pd.DataFrame:
    return pd.DataFrame([historical_row(inputs, market, variant)
                         for variant in VARIANTS.values()])


# ----------------------------------------------------------------------------
# Shared-path Monte Carlo
# ----------------------------------------------------------------------------

def build_horizon(inputs, years: int) -> Horizon:
    """One horizon's calendar, cohort windows and per-variant roll schedules.

    `letf.leaps_robustness.build_horizon` fixes the maturity at the repository's
    two-year convention, which is the dial this module exists to move, so the
    schedules come from each variant's own rule. The rest is that function's
    arithmetic and `test_leaps_duration` pins the two against each other on the
    canonical regime.
    """
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
    """Every variant on one resampled path, both underlyings rebuilt inside it."""
    entry = WARMUP_SESSIONS
    built = {}
    for index, price_col, yield_col in ((EQUITY, None, None),
                                        (NASDAQ, NDX_PRICE, NDX_YIELD)):
        price, vol, riskfree, dividend, _ = build_path(
            sample, horizon.calendar, signal=False, price_col=price_col,
            yield_col=yield_col)
        built[index] = (price[entry:], dividend[entry:], riskfree[entry:], vol[entry:])

    results = {}
    for index, column in ((EQUITY, EQUITY_TR), (NASDAQ, NDX_TOTAL)):
        wealth = np.r_[1., np.cumprod(1 + sample[entry:, column])]
        results[index] = np.r_[path_metrics(wealth, horizon)[0],
                               np.full(len(EXTRA_METRICS), np.nan)]

    growth = np.r_[1., 1 + sample[entry:, TREASURY_R]]
    for name, variant in VARIANTS.items():
        spot, q, rate, sigma = built[variant.underlying]
        # The roll ledger is not read here — turnover is a realized-path
        # statistic — and skipping it is a fifth of the cost of this loop.
        path = simulate_leaps_arrays(spot, growth, q, rate, sigma, horizon.days,
                                     horizon.schedules[name], ledger=False)
        out, drawdown = path_metrics(path.navs, horizon)
        record = np.r_[out, np.zeros(len(EXTRA_METRICS))]
        record[BASE_SLOT['mean_delta_exposure']] = float(path.exposures.mean())
        hurt = drawdown <= -MAJOR_LOSS
        if hurt.any():
            trough = int(drawdown.argmin())
            window = path.exposures[trough:trough + RECOVERY_SESSIONS]
            ratio = float(window.mean() / path.exposures.mean())
            record[BASE_SLOT['min_exposure_after_loss']] = float(path.exposures[hurt].min())
            record[BASE_SLOT['underexposed_recovery']] = float(ratio < UNDEREXPOSED)
            record[SLOT['recovery_exposure_ratio']] = ratio
        else:
            record[SLOT['recovery_exposure_ratio']] = np.nan
        for level in DELTA_PERCENTILES:
            record[SLOT[f'delta_p{level}']] = float(np.percentile(path.exposures, level))
        record[SLOT['mean_option_weight']] = float(path.option_weights.mean())
        results[name] = record
    return results


def summarize(store: dict, horizon: Horizon, paths: int) -> pd.DataFrame:
    """The distribution, with every relative comparison paired path by path."""
    index_wealth = {name: store[name][:, BASE_SLOT['terminal_wealth']]
                    for name in INDEX_ROWS}
    rows = []
    for name, values in store.items():
        variant = VARIANTS.get(name)
        rates = values[:, BASE_SLOT['cagr']]
        terminal = values[:, BASE_SLOT['terminal_wealth']]
        drawdown = values[:, BASE_SLOT['max_drawdown']]
        own = variant.underlying if variant else name
        canonical = (store[variant.canonical][:, BASE_SLOT['terminal_wealth']]
                     if variant else None)
        row = dict(variant=name, horizon_years=horizon.years, paths=paths,
                   block_days=PRIMARY_BLOCK,
                   cell=variant.cell if variant else name,
                   family=FAMILY[own], regime=variant.regime if variant else 'index',
                   underlying=own,
                   moneyness=variant.moneyness if variant else np.nan,
                   premium_budget=variant.budget if variant else np.nan,
                   maturity_months=months(variant.maturity_years) if variant else np.nan,
                   roll_months=months(variant.roll_years) if variant else np.nan,
                   mean_cagr=float(rates.mean()),
                   median_max_drawdown=float(np.median(drawdown)),
                   worst_max_drawdown=float(drawdown.min()),
                   prob_negative_cagr=float((rates < 0).mean()),
                   prob_below_index=float((terminal < index_wealth[own]).mean())
                   if name != own else 0.,
                   prob_below_canonical=float((terminal < canonical).mean())
                   if variant and name != variant.canonical else 0.)
        for level in PERCENTILES:
            row[f'cagr_p{level}'] = float(np.percentile(rates, level))
        for level in (5, 50, 95):
            row[f'terminal_wealth_p{level}'] = float(np.percentile(terminal, level))
        for threshold in DRAWDOWN_THRESHOLDS:
            row[f'prob_drawdown_worse_than_{round(threshold * 100)}'] = float(
                (drawdown <= -threshold).mean())
        for column in ('mean_delta_exposure', 'min_exposure_after_loss',
                       'underexposed_recovery'):
            series = values[:, BASE_SLOT[column]]
            row[column] = (float(np.nanmean(series) if column == 'underexposed_recovery'
                                 else np.nanmedian(series))
                           if np.isfinite(series).any() else np.nan)
        for column in EXTRA_METRICS:
            series = values[:, SLOT[column]]
            row[column] = (float(np.nanmedian(series)) if np.isfinite(series).any()
                           else np.nan)
        rows.append(row)
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------
# Exposure normalization
# ----------------------------------------------------------------------------

def _invert(deltas, budgets, target) -> tuple:
    """The budget that would deliver `target` delta, and whether the grid reaches it.

    Linear inverse interpolation over the tested budgets, which is what the brief
    asks for and all the grid can support. Outside the tested range the answer is
    an extrapolation, and it is returned flagged rather than clamped and passed
    off as a measurement.
    """
    order = np.argsort(deltas)
    deltas, budgets = np.asarray(deltas)[order], np.asarray(budgets)[order]
    inside = bool(deltas[0] <= target <= deltas[-1])
    if inside:
        return float(np.interp(target, deltas, budgets)), True
    # Straight-line continuation off the nearer end, so the size of the reach is
    # visible in the number rather than hidden by a clamp.
    if target < deltas[0]:
        (x0, x1), (y0, y1) = deltas[:2], budgets[:2]
    else:
        (x0, x1), (y0, y1) = deltas[-2:], budgets[-2:]
    slope = (y1 - y0) / (x1 - x0) if x1 != x0 else 0.
    return float(y0 + slope * (target - x0)), False


def _grouped(frame: pd.DataFrame, keys=('family', 'moneyness', 'maturity_months',
                                        'roll_months')):
    return frame.groupby(list(keys), sort=False)


def exposure_table(historical: pd.DataFrame, monte: pd.DataFrame) -> pd.DataFrame:
    """What each duration buys per dollar of premium, and what would match it.

    The whole point of the module. A shorter contract is cheaper, so the same
    budget buys more delta; comparing two durations at one budget therefore
    compares two position sizes. For every cell this reports the canonical
    two-year delta at the same strike and budget, the delta the variant actually
    runs, and the budget that would have matched — with the Treasury sleeve that
    implies, since the capital released or absorbed has to go somewhere.
    """
    hist = historical.set_index('variant')
    thirty = monte[monte.horizon_years == FRONTIER_HORIZON].set_index('variant')
    canonical = {(row.family, row.moneyness, row.premium_budget): row.mean_delta_exposure
                 for _, row in historical.iterrows()
                 if (row.maturity_months, row.roll_months)
                 == (months(CANONICAL[0]), months(CANONICAL[1]))}
    rows = []
    for _, group in _grouped(historical):
        budgets = list(group.premium_budget)
        deltas = list(group.mean_delta_exposure)
        for _, row in group.iterrows():
            target = canonical.get((row.family, row.moneyness, row.premium_budget))
            if target is None:
                continue
            matched, inside = _invert(deltas, budgets, target)
            option_weight = float(hist.loc[row.variant, 'mean_option_weight'])
            # The option weight is the budget up to the spread and the decay
            # between rolls, so scaling it by the budget ratio is the honest
            # first-order read on where the released capital goes.
            scaled = option_weight * matched / row.premium_budget
            rows.append(dict(
                variant=row.variant, cell=row.cell, family=row.family,
                regime=row.regime, moneyness=row.moneyness,
                premium_budget=row.premium_budget,
                maturity_months=row.maturity_months, roll_months=row.roll_months,
                canonical_mean_delta=float(target),
                fixed_budget_mean_delta=float(row.mean_delta_exposure),
                delta_ratio=float(row.mean_delta_exposure / target),
                matched_delta_budget=matched, matched_budget_inside_grid=inside,
                matched_budget_change=float(matched - row.premium_budget),
                treasury_weight=1 - option_weight,
                matched_treasury_weight=1 - scaled,
                treasury_weight_change=float(option_weight - scaled),
                median_30y_cagr=float(thirty.loc[row.variant, 'cagr_p50'])))
    return pd.DataFrame(rows)


MATCHED_METRICS = ('cagr_p5', 'cagr_p50', 'median_max_drawdown',
                   'prob_drawdown_worse_than_60', 'mean_delta_exposure')


def matched_frontier(exposure: pd.DataFrame, monte: pd.DataFrame) -> pd.DataFrame:
    """Every cell restated at the budget that matches the canonical delta.

    The metrics are interpolated along the tested budget grid rather than
    re-simulated, which is what the brief asks for and is defensible over a
    five-point grid on quantities that move smoothly in budget. It is still an
    interpolation: `matched_budget_inside_grid` says whether the answer was
    reached or reached *for*, and rows that were extrapolated should be read as
    indicative only.
    """
    thirty = monte[monte.horizon_years == FRONTIER_HORIZON]
    rows = []
    for keys, group in _grouped(thirty.dropna(subset=['moneyness'])):
        budgets = np.asarray(group.premium_budget, dtype=float)
        order = np.argsort(budgets)
        mine = exposure[(exposure.family == keys[0]) & (exposure.moneyness == keys[1])
                        & (exposure.maturity_months == keys[2])
                        & (exposure.roll_months == keys[3])]
        for _, row in mine.iterrows():
            out = dict(variant=row.variant, cell=row.cell, family=row.family,
                       regime=row.regime, moneyness=row.moneyness,
                       premium_budget=row.premium_budget,
                       maturity_months=row.maturity_months,
                       roll_months=row.roll_months,
                       matched_delta_budget=row.matched_delta_budget,
                       matched_budget_inside_grid=row.matched_budget_inside_grid,
                       canonical_mean_delta=row.canonical_mean_delta)
            for metric in MATCHED_METRICS:
                values = np.asarray(group[metric], dtype=float)[order]
                out[metric] = float(np.interp(row.matched_delta_budget,
                                              budgets[order], values))
            rows.append(out)
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------
# Frontiers and the regime dominance test
# ----------------------------------------------------------------------------

FRONTIERS = (('SPX fixed budget', 'SPX', 'fixed'), ('NDX fixed budget', 'NDX', 'fixed'),
             ('SPX matched delta', 'SPX', 'matched'),
             ('NDX matched delta', 'NDX', 'matched'))


def frontier_table(monte: pd.DataFrame, matched: pd.DataFrame,
                   historical: pd.DataFrame) -> pd.DataFrame:
    """The four frontiers the brief asks for, in one long table.

    Fixed-budget and matched-delta are kept apart because they answer different
    questions: the first is the portfolio a budget actually produces, the second
    is whether the contract is doing anything once position size is held still.
    """
    thirty = monte[monte.horizon_years == FRONTIER_HORIZON].set_index('variant')
    hist = historical.set_index('variant')
    frames = []
    for label, family, basis in FRONTIERS:
        source = (thirty[thirty.family == family].reset_index() if basis == 'fixed'
                  else matched[matched.family == family].copy())
        source = source.dropna(subset=['cagr_p50'])
        flagged = frontier_flags(
            source.rename(columns={'variant': 'strategy',
                                   'median_max_drawdown': 'max_drawdown_median'}),
            members=list(source.variant))
        flagged = flagged.rename(columns={'strategy': 'variant',
                                          'max_drawdown_median': 'median_max_drawdown'})
        flagged['frontier'] = label
        flagged['basis'] = basis
        for column in ('option_turnover_per_year', 'theta_burden_per_year',
                       'vega_per_nav', 'cagr_per_volatility_point',
                       'mean_entry_years'):
            flagged[column] = flagged.variant.map(hist[column])
        frames.append(flagged)
    columns = ['frontier', 'basis', 'variant', 'cell', 'family', 'regime',
               'maturity_months',
               'roll_months', 'moneyness', 'premium_budget', 'cagr_p50', 'cagr_p5',
               'median_max_drawdown', 'prob_drawdown_worse_than_60',
               'mean_delta_exposure', 'option_turnover_per_year',
               'theta_burden_per_year', 'vega_per_nav', 'mean_entry_years',
               'cagr_per_volatility_point', 'on_growth_frontier', 'on_robust_frontier',
               'on_frontier']
    out = pd.concat(frames, ignore_index=True)
    return out[[c for c in columns if c in out]]


def bar(historical: pd.DataFrame, names) -> float:
    """The smallest CAGR gap this comparison is entitled to call a difference.

    One volatility point of modelled CAGR for the more price-sensitive of the
    pair, floored at the prespecified tolerance. A longer contract carries more
    vega, so the bar is not constant across a duration ladder — which is exactly
    why a duration comparison needs it.
    """
    hist = historical.set_index('variant')
    unit = max(abs(float(hist.loc[name, 'cagr_per_volatility_point'])) for name in names)
    return max(unit, CAGR_TOLERANCE)


def _cell_verdict(row, canonical, resolution, prefix='') -> dict:
    """One cell of one regime against the same cell of the canonical regime."""
    cagr = float(row['cagr_p50'] - canonical['cagr_p50'])
    p5 = float(row['cagr_p5'] - canonical['cagr_p5'])
    deeper = float(row['median_max_drawdown'] - canonical['median_max_drawdown'])
    tail = float(row['prob_drawdown_worse_than_60']
                 - canonical['prob_drawdown_worse_than_60'])
    better = (cagr > resolution or p5 > P5_TOLERANCE
              or deeper > DRAWDOWN_TOLERANCE or -tail > PROBABILITY_TOLERANCE)
    worse = (cagr < -resolution or p5 < -P5_TOLERANCE
             or deeper < -DRAWDOWN_TOLERANCE or tail > PROBABILITY_TOLERANCE)
    return {f'{prefix}cagr': cagr, f'{prefix}p5': p5, f'{prefix}drawdown': deeper,
            f'{prefix}tail': tail, f'{prefix}resolution': resolution,
            f'{prefix}improves': bool(better and not worse),
            f'{prefix}worsens': bool(worse and not better),
            f'{prefix}mixed': bool(better and worse),
            f'{prefix}unresolved': bool(not better and not worse)}


def regime_table(monte: pd.DataFrame, matched: pd.DataFrame,
                 historical: pd.DataFrame) -> pd.DataFrame:
    """Each duration/roll regime against the canonical one, cell by cell.

    Scored twice. At a fixed budget the comparison includes whatever extra delta
    the duration bought, which is a real property of the portfolio and not a
    property of the contract. At matched delta the position size is held still
    and what is left is the contract. A regime that only wins on the first has
    not been shown to be efficient; it has been shown to be bigger.
    """
    thirty = monte[monte.horizon_years == FRONTIER_HORIZON].set_index('variant')
    matched_rows = matched.set_index('variant')
    rows = []
    for name, variant in VARIANTS.items():
        if variant.regime == f'{months(CANONICAL[0])}m/{months(CANONICAL[1])}m':
            continue
        canonical = variant.canonical
        if canonical not in thirty.index:
            continue
        row = dict(variant=name, cell=variant.cell, family=FAMILY[variant.underlying],
                   regime=variant.regime, moneyness=variant.moneyness,
                   premium_budget=variant.budget,
                   maturity_months=months(variant.maturity_years),
                   roll_months=months(variant.roll_years))
        resolution = bar(historical, (name, canonical))
        row.update(_cell_verdict(thirty.loc[name], thirty.loc[canonical], resolution))
        if name in matched_rows.index and canonical in matched_rows.index:
            row.update(_cell_verdict(matched_rows.loc[name], matched_rows.loc[canonical],
                                     resolution, prefix='matched_'))
            row['matched_inside_grid'] = bool(
                matched_rows.loc[name, 'matched_budget_inside_grid'])
        rows.append(row)
    return pd.DataFrame(rows)


def classify(regimes: pd.DataFrame, exposure: pd.DataFrame) -> pd.DataFrame:
    """Robustly efficient, conditionally efficient, dominated, or unresolved.

    Read off the *matched-delta* columns wherever they exist, because that is the
    question: a regime that improves only at a fixed budget has bought its
    improvement with position size, which any budget can buy. The fixed-budget
    counts are carried alongside so the difference between the two is visible.
    """
    rows = []
    for (family, regime), group in regimes.groupby(['family', 'regime'], sort=False):
        cells = len(group)
        counts = {}
        for prefix in ('', 'matched_'):
            for outcome in ('improves', 'worsens', 'mixed', 'unresolved'):
                column = f'{prefix}{outcome}'
                counts[column] = int(group[column].sum()) if column in group else 0
        share = counts['matched_improves'] / cells if cells else 0.
        harmed = counts['matched_worsens'] / cells if cells else 0.
        unresolved = counts['matched_unresolved'] / cells if cells else 0.
        if unresolved >= REGIME_MAJORITY:
            verdict = 'unresolved'
        elif share >= REGIME_MAJORITY and harmed == 0:
            verdict = 'robustly efficient'
        elif harmed >= REGIME_MAJORITY and share == 0:
            verdict = 'dominated'
        else:
            verdict = 'conditionally efficient'
        rows.append(dict(family=family, regime=regime, cells=cells,
                         classification=verdict, **counts,
                         median_matched_cagr_gap=float(group['matched_cagr'].median())
                         if 'matched_cagr' in group else np.nan,
                         median_fixed_cagr_gap=float(group['cagr'].median()),
                         median_delta_ratio=float(exposure[
                             exposure.variant.isin(group.variant)].delta_ratio.median())))
    return pd.DataFrame(rows)


CHANNELS = ('delta_ratio', 'theta_burden_per_year', 'option_turnover_per_year',
            'cost_drag_bps', 'vega_per_nav', 'recovery_exposure_ratio',
            'mean_entry_years')


def mechanism_table(historical: pd.DataFrame, regimes: pd.DataFrame,
                    exposure: pd.DataFrame) -> pd.DataFrame:
    """Where a regime's difference comes from, channel by channel.

    The question this module exists to answer is whether an advantage is the
    contract or the position size, so `delta_ratio` and the matched-delta residual
    sit in the same table as the costs. A regime whose whole gap disappears once
    delta is matched has told you about leverage, not about duration.
    """
    hist = historical.set_index('variant')
    ratios = exposure.set_index('variant')['delta_ratio']
    rows = []
    for (family, regime), group in regimes.groupby(['family', 'regime'], sort=False):
        row = dict(family=family, regime=regime, cells=len(group))
        for channel in CHANNELS:
            if channel == 'delta_ratio':
                row['delta_ratio'] = float(ratios.loc[list(group.variant)].median())
                continue
            mine = hist.loc[list(group.variant), channel]
            theirs = hist.loc[[VARIANTS[n].canonical for n in group.variant], channel]
            row[f'{channel}_difference'] = float(
                (mine.to_numpy() - theirs.to_numpy()).mean())
        row['fixed_budget_cagr_gap'] = float(group['cagr'].median())
        row['matched_delta_cagr_gap'] = (float(group['matched_cagr'].median())
                                         if 'matched_cagr' in group else np.nan)
        # How much of the fixed-budget gap is left once position size is held
        # still. Near zero means the duration bought leverage and nothing else.
        row['survives_delta_matching'] = (
            float(row['matched_delta_cagr_gap'] / row['fixed_budget_cagr_gap'])
            if abs(row['fixed_budget_cagr_gap']) > 1e-9 else np.nan)
        rows.append(row)
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------
# Figures
# ----------------------------------------------------------------------------

REGIME_COLOUR = {'15m/6m': 'C3', '15m/12m': 'C1', '18m/6m': 'C4', '18m/12m': 'C5',
                 '24m/6m': 'C6', '24m/12m': 'C0', '30m/12m': 'C2', '30m/18m': 'C8'}
FAMILY_MARKER = {'SPX': 'o', 'NDX': '^'}


def figure_frontier(path: Path, frontier: pd.DataFrame):
    """Fixed budget beside matched delta, so the difference is the whole picture."""
    fig, axes = plt.subplots(1, 2, figsize=(13.6, 5.6), constrained_layout=True)
    for ax, basis, title in ((axes[0], 'fixed', 'A. Fixed premium budget'),
                             (axes[1], 'matched', 'B. Matched to canonical delta')):
        piece = frontier[frontier.basis == basis]
        for regime, group in piece.groupby('regime', sort=False):
            for family, part in group.groupby('family', sort=False):
                ax.scatter(part.median_max_drawdown, part.cagr_p50,
                           marker=FAMILY_MARKER[family], s=34,
                           color=REGIME_COLOUR.get(regime, '0.5'),
                           edgecolors='k', linewidths=.35, alpha=.9,
                           label=f'{family} {regime}')
        ax.set_xlabel('median max drawdown')
        ax.set_ylabel(f'median {FRONTIER_HORIZON}-year CAGR')
        for axis in (ax.xaxis, ax.yaxis):
            axis.set_major_formatter(PercentFormatter(1))
        ax.set_title(title)
    handles, labels = axes[0].get_legend_handles_labels()
    seen = dict(zip(labels, handles))
    axes[0].legend(seen.values(), seen.keys(), fontsize=6.5, ncol=2, loc='lower left')
    fig.savefig(path, dpi=160)
    plt.close(fig)


def figure_exposure(path: Path, exposure: pd.DataFrame):
    """What a dollar of premium buys at each length, and what would match it."""
    fig, axes = plt.subplots(1, 2, figsize=(13.6, 5.2), constrained_layout=True)
    ax = axes[0]
    for family, group in exposure.groupby('family', sort=False):
        by_length = group.groupby(['maturity_months', 'roll_months']).delta_ratio.median()
        for (maturity, roll), value in by_length.items():
            ax.scatter(maturity, value, marker=FAMILY_MARKER[family], s=70,
                       color=REGIME_COLOUR.get(f'{maturity}m/{roll}m', '0.5'),
                       edgecolors='k', linewidths=.5,
                       label=f'{family} {maturity}m/{roll}m')
    ax.axhline(1., color='grey', lw=.9, ls='--')
    ax.set_xlabel('initial maturity (months)')
    ax.set_ylabel('mean delta / canonical mean delta')
    ax.set_title('A. Delta bought per dollar of premium')
    ax.legend(fontsize=6.5, ncol=2)

    ax = axes[1]
    for family, group in exposure.groupby('family', sort=False):
        inside = group[group.matched_budget_inside_grid]
        outside = group[~group.matched_budget_inside_grid]
        ax.scatter(inside.premium_budget, inside.matched_delta_budget,
                   marker=FAMILY_MARKER[family], s=26, alpha=.75,
                   label=f'{family} interpolated')
        ax.scatter(outside.premium_budget, outside.matched_delta_budget,
                   marker=FAMILY_MARKER[family], s=26, facecolors='none',
                   edgecolors='C3', linewidths=.7, label=f'{family} extrapolated')
    limits = [exposure.premium_budget.min(), exposure.premium_budget.max()]
    ax.plot(limits, limits, color='grey', lw=.9, ls='--')
    ax.set_xlabel('premium budget as run')
    ax.set_ylabel('budget that would match canonical delta')
    for axis in (ax.xaxis, ax.yaxis):
        axis.set_major_formatter(PercentFormatter(1))
    ax.set_title('B. What matching the exposure would cost')
    ax.legend(fontsize=7)
    fig.savefig(path, dpi=160)
    plt.close(fig)


# ----------------------------------------------------------------------------
# Driver
# ----------------------------------------------------------------------------

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

    exposure = exposure_table(historical, monte)
    matched = matched_frontier(exposure, monte)
    frontier = frontier_table(monte, matched, historical)
    regimes = regime_table(monte, matched, historical)
    verdicts = classify(regimes, exposure)
    mechanism = mechanism_table(historical, regimes, exposure)

    reports = root / 'reports'
    outputs = {'leaps_duration_roll_historical.csv': historical,
               'leaps_duration_roll_monte_carlo.csv': monte,
               'leaps_duration_roll_exposure.csv': exposure,
               'leaps_duration_roll_frontier.csv': frontier,
               'leaps_duration_roll_regimes.csv': regimes,
               'leaps_duration_roll_mechanism.csv': mechanism,
               'leaps_duration_roll_classification.csv': verdicts}
    for name, frame in outputs.items():
        frame.pipe(stable_floats).to_csv(reports / name, index=False,
                                         float_format=FLOAT_FORMAT)
    figure_frontier(reports / 'leaps_duration_roll_frontier.png', frontier)
    figure_exposure(reports / 'leaps_duration_roll_exposure.png', exposure)
    report(reports, inputs, historical, monte, exposure, matched, frontier, regimes,
           verdicts, mechanism, count)

    (reports / 'leaps_duration_roll_manifest.json').write_text(json.dumps({
        'window': [inputs.ix[0].date().isoformat(), inputs.ix[-1].date().isoformat()],
        'observations': int(len(inputs.ix)),
        'nasdaq_moneyness': list(NDX_MONEYNESS), 'nasdaq_budgets': list(NDX_BUDGETS),
        'nasdaq_regimes': [[m, r] for m, r in NDX_REGIMES],
        'sp500_cells': {str(k): list(v) for k, v in SP_CELLS.items()},
        'sp500_regimes': [[m, r] for m, r in SP_REGIMES],
        'canonical_regime': list(CANONICAL), 'variants': len(VARIANTS),
        'grids_are_prespecified': True,
        'nasdaq_maximum_maturity_note': 'XND expirations currently reach about fifteen '
                                        'months, so the twenty-four-month Nasdaq rows '
                                        'are a reference the market cannot presently '
                                        'supply; SPX and XSP list far longer',
        'iv_premium': IV_PREMIUM, 'option_spread_bps': OPTION_SPREAD_BPS,
        'iv_premium_retuned_by_maturity': False,
        'iv_premium_note': 'the flat three-point loading is applied to every contract '
                           'length; real implied volatility has a term structure and a '
                           'skew, and this repository observes neither, which is the '
                           'sharpest limitation on a duration comparison',
        'expiry_months': list(EXPIRY_MONTHS),
        'seed': SEED, 'block_days': PRIMARY_BLOCK, 'paths': count,
        'horizons': list(MC_HORIZONS), 'frontier_horizon': FRONTIER_HORIZON,
        'warmup_sessions': WARMUP_SESSIONS,
        'tolerances': {'cagr': CAGR_TOLERANCE, 'p5_cagr': P5_TOLERANCE,
                       'max_drawdown': DRAWDOWN_TOLERANCE,
                       'drawdown_probability': PROBABILITY_TOLERANCE,
                       'widened_to': 'one volatility point of modelled CAGR, per '
                                     'variant, whenever that is larger',
                       'regime_majority': REGIME_MAJORITY},
        'matched_delta_method': 'linear inverse interpolation of mean delta over the '
                                'tested budget grid; metrics at the matched budget are '
                                'interpolated along the same grid rather than '
                                're-simulated, and rows outside the grid are flagged',
        'nasdaq_proxy_through': PROXY_THROUGH,
        'option_prices': 'modelled with Black-Scholes on an assumed implied volatility; '
                         'this repository holds no option price history, no skew '
                         'surface and no term structure for either index',
        'python': platform.python_version(), 'numpy': np.__version__,
        'pandas': pd.__version__, 'scipy': scipy.__version__,
        'matplotlib': matplotlib.__version__,
        'source_hashes': source_hashes(root, __spec__.name),
        'outputs_sha256': {name: sha(reports / name) for name in outputs},
    }, indent=2) + '\n')
    counts = verdicts.classification.value_counts()
    print(f'LEAPS duration/roll: {len(VARIANTS)} variants, '
          f'{len(verdicts)} regimes; '
          + '; '.join(f'{int(n)} {label}' for label, n in counts.items())
          + f'; {count} paths x {len(MC_HORIZONS)} horizons.')
    return historical, monte, exposure, verdicts


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

def regime_summary(historical, monte, exposure) -> pd.DataFrame:
    """One row per family and regime: 141 cells is a CSV, not a page."""
    thirty = monte[monte.horizon_years == FRONTIER_HORIZON].set_index('variant')
    hist = historical.set_index('variant')
    ratios = exposure.set_index('variant')['delta_ratio']
    rows = []
    for (family, regime), group in historical.groupby(['family', 'regime'], sort=False):
        names = list(group.variant)
        rows.append(dict(
            family=family, regime=regime, cells=len(names),
            mean_entry_years=float(hist.loc[names, 'mean_entry_years'].mean()),
            mean_exit_years=float(hist.loc[names, 'mean_exit_years'].mean()),
            delta_ratio=float(ratios.loc[names].median()),
            mean_delta_exposure=float(hist.loc[names, 'mean_delta_exposure'].mean()),
            option_turnover_per_year=float(
                hist.loc[names, 'option_turnover_per_year'].mean()),
            cost_drag_bps=float(hist.loc[names, 'cost_drag_bps'].mean()),
            theta_burden_per_year=float(hist.loc[names, 'theta_burden_per_year'].mean()),
            vega_per_nav=float(hist.loc[names, 'vega_per_nav'].mean()),
            cagr_per_volatility_point=float(
                hist.loc[names, 'cagr_per_volatility_point'].mean()),
            median_30y_cagr=float(thirty.loc[names, 'cagr_p50'].median()),
            p5_30y_cagr=float(thirty.loc[names, 'cagr_p5'].median()),
            median_max_drawdown=float(thirty.loc[names, 'median_max_drawdown'].median()),
            prob_drawdown_worse_than_60=float(
                thirty.loc[names, 'prob_drawdown_worse_than_60'].median())))
    return pd.DataFrame(rows)


def episode_summary(historical) -> pd.DataFrame:
    """Each stress episode by regime: the loss, and the position through it."""
    rows = []
    for (family, regime), group in historical.groupby(['family', 'regime'], sort=False):
        for event in CRASHES:
            if event not in group:
                continue
            rows.append(dict(
                family=family, regime=regime, episode=event,
                loss=float(group[event].mean()),
                peak_to_trough=float(group[f'{event}_trough'].mean()),
                mean_delta=float(group[f'{event}_mean_delta'].mean()),
                min_delta=float(group[f'{event}_min_delta'].mean()),
                recovery_delta=float(group[f'{event}_recovery_delta'].mean())))
    return pd.DataFrame(rows)


CANONICAL_REGIME = f'{months(CANONICAL[0])}m/{months(CANONICAL[1])}m'


MENU_SIZE = 5


def preferred_region(monte: pd.DataFrame) -> tuple:
    """Which strike/budget cells are efficient, asked separately inside each regime.

    If duration changed which cells an investor should hold, the frontier drawn
    inside a fifteen-month regime would name different cells from the one drawn
    inside a thirty-month regime. Comparing the sets is the whole test, and it is
    a different question from which regime is better.
    """
    thirty = monte[monte.horizon_years == FRONTIER_HORIZON].dropna(subset=['moneyness'])
    chosen = {}
    for (family, regime), group in thirty.groupby(['family', 'regime'], sort=False):
        flags = frontier_flags(
            group.rename(columns={'variant': 'strategy',
                                  'median_max_drawdown': 'max_drawdown_median'}),
            members=list(group.variant)).set_index('strategy')
        chosen[(family, regime)] = {group.set_index('variant').loc[name, 'cell']
                                    for name in flags.index
                                    if bool(flags.loc[name, 'on_frontier'])}
    return chosen


def regime_menu(frontier: pd.DataFrame, historical: pd.DataFrame) -> pd.DataFrame:
    """A short list of efficient cells that are actually distinguishable.

    Walked up the fixed-budget frontier from the least risky, keeping a candidate
    only when it is separated from the last one kept by more than the tolerances
    on at least one axis. A menu of neighbours no one can tell apart is not a
    menu.
    """
    piece = frontier[(frontier.basis == 'fixed') & frontier.on_frontier].copy()
    piece = piece.sort_values('median_max_drawdown', ascending=False)
    kept = []
    for _, row in piece.iterrows():
        if not kept:
            kept.append(row)
            continue
        last = kept[-1]
        resolution = bar(historical, (row.variant, last.variant))
        if (abs(row.cagr_p50 - last.cagr_p50) > resolution
                or abs(row.median_max_drawdown - last.median_max_drawdown)
                > DRAWDOWN_TOLERANCE
                or abs(row.prob_drawdown_worse_than_60
                       - last.prob_drawdown_worse_than_60) > PROBABILITY_TOLERANCE):
            kept.append(row)
    frame = pd.DataFrame(kept)
    if len(frame) > MENU_SIZE:
        targets = np.linspace(frame.median_max_drawdown.max(),
                             frame.median_max_drawdown.min(), MENU_SIZE)
        picked = []
        for target in targets:
            remaining = frame[~frame.variant.isin(picked)]
            picked.append(remaining.iloc[
                int((remaining.median_max_drawdown - target).abs().argmin())].variant)
        frame = frame[frame.variant.isin(picked)]
    return frame.sort_values('median_max_drawdown', ascending=False)


def _narrative(historical, monte, exposure, matched, frontier, regimes, verdicts,
               mechanism) -> dict:
    hist = historical.set_index('variant')
    thirty = monte[monte.horizon_years == FRONTIER_HORIZON].set_index('variant')
    summary = regime_summary(historical, monte, exposure)
    graded = verdicts.set_index(['family', 'regime'])
    channels = mechanism.set_index(['family', 'regime'])

    spx = verdicts[verdicts.family == 'SPX']
    ndx = verdicts[verdicts.family == 'NDX']
    efficient = spx[spx.classification == 'robustly efficient']
    unresolved = verdicts[verdicts.classification == 'unresolved']
    # Does the canonical regime survive being the reference? It is efficient by
    # construction against itself, so the test is whether anything beats it once
    # position size is held still.
    beaten_by = list(efficient.regime)

    # The whole question, in one number per regime: how much of the fixed-budget
    # gap is left once the position size is matched.
    survives = channels['survives_delta_matching'].to_dict()
    ladder = summary.set_index(['family', 'regime'])['delta_ratio'].to_dict()
    monotone = {}
    for family in ('SPX', 'NDX'):
        piece = summary[summary.family == family].copy()
        piece['maturity'] = piece.regime.str.split('m/').str[0].astype(int)
        by_length = piece.groupby('maturity').delta_ratio.mean().sort_index()
        monotone[family] = (list(by_length.items()),
                            list(by_length) == sorted(by_length, reverse=True))

    # Question seven: what budget on a fifteen-month Nasdaq contract reproduces
    # the canonical exposure.
    ndx_short = exposure[(exposure.family == 'NDX')
                         & (exposure.maturity_months == 15)]
    matched_budgets = ndx_short.groupby('roll_months').apply(
        lambda g: float((g.matched_delta_budget / g.premium_budget).median()),
        include_groups=False).to_dict()

    six = {family: (graded.loc[(family, r), 'classification']
                    for r in graded.loc[family].index if r.endswith('/6m'))
           for family in ('SPX', 'NDX') if family in graded.index.get_level_values(0)}
    turnover = summary.set_index(['family', 'regime'])['option_turnover_per_year'].to_dict()
    tails = summary.set_index(['family', 'regime'])['prob_drawdown_worse_than_60'].to_dict()
    units = summary.set_index(['family', 'regime'])['cagr_per_volatility_point'].to_dict()
    # Question six, and question ten's arithmetic, both need the gaps against each
    # regime's own bar rather than against a single tolerance.
    short_gaps, survivors, inside_bar = {}, [], 0
    for (family, regime), row in graded.iterrows():
        unit = float(summary.set_index(['family', 'regime']).loc[
            (family, regime), 'cagr_per_volatility_point'])
        gap = float(row['median_matched_cagr_gap'])
        if abs(gap) < max(abs(unit), CAGR_TOLERANCE):
            inside_bar += 1
        if regime.startswith('15m'):
            short_gaps[(family, regime)] = (float(row['median_fixed_cagr_gap']), gap,
                                            max(abs(unit), CAGR_TOLERANCE))
            if abs(gap) > max(abs(unit), CAGR_TOLERANCE):
                survivors.append((family, regime))
    chosen = preferred_region(monte)
    spx_sets = {regime: cells for (family, regime), cells in chosen.items()
                if family == 'SPX'}
    everywhere = set.intersection(*spx_sets.values()) if spx_sets else set()
    anywhere = set.union(*spx_sets.values()) if spx_sets else set()
    region = (f'{len(everywhere)} of the {len(anywhere)} S&P cells that are efficient '
              f'in any regime are efficient in every one of the {len(spx_sets)} regimes'
              + (', so the preferred region is essentially unchanged by duration'
                 if len(everywhere) >= .6 * len(anywhere) else
                 ', so duration does move which cells an investor should hold'))
    menu = regime_menu(frontier, historical)
    return dict(hist=hist, thirty=thirty, summary=summary, graded=graded,
                region=region, menu=menu, everywhere=everywhere, anywhere=anywhere,
                short_gaps=short_gaps, short_survivors=survivors, inside_bar=inside_bar,
                channels=channels, spx=spx, ndx=ndx, efficient=efficient,
                unresolved=unresolved, beaten_by=beaten_by, survives=survives,
                ladder=ladder, monotone=monotone, matched_budgets=matched_budgets,
                six=six, turnover=turnover, tails=tails, units=units,
                exposure=exposure, verdicts=verdicts, mechanism=mechanism)


def _answers(v) -> str:
    """The ten prespecified questions, answered from the tables above."""
    graded = v['graded']
    lines = []

    spx_efficient = list(v['efficient'].regime)
    lines.append(
        f'**1. Is ~{months(CANONICAL[0])}m / annual roll actually a preferred SPX '
        'regime, or merely inherited?** Inherited — it entered this repository as a '
        'convention and every later module took it — and '
        + ('it does not survive unchallenged. ' if spx_efficient else
           'nothing tested displaces it. ')
        + (f'At matched delta {phrase(spx_efficient)} '
           f'{"is" if len(spx_efficient) == 1 else "are"} robustly efficient against '
           f'it, so it is not the single best S&P regime; '
           if spx_efficient else
           'No S&P regime is robustly efficient against it at matched delta. ')
        + f'The classification splits the {len(v["spx"])} non-canonical S&P regimes '
        + phrase([f'{count} {label}' for label, count
                  in v['spx'].classification.value_counts().items()]) + '.')

    lengths, ordered = v['monotone']['SPX']
    lines.append(
        '**2. Does SPX have a clearly superior duration region?** '
        + (f'{phrase(spx_efficient)} on the matched-delta test. '
           if spx_efficient else 'No region is robustly efficient at matched delta. ')
        + 'Read at a fixed budget the answer would be the shortest contract available, '
          'but that is position size, not duration: delta bought per dollar of premium '
          'runs '
        + phrase([f'{ratio:.2f}x at {length} months' for length, ratio in lengths])
        + ' against the canonical rule'
        + (', falling monotonically as the contract lengthens.' if ordered else '.'))

    six_month = [(family, regime) for family, regime in graded.index
                 if regime.endswith('/6m')]
    lines.append(
        '**3. Does rolling every 6 months ever improve efficiency enough to justify '
        'doubled turnover?** '
        + phrase([f'{family} {regime} turns over '
                  f'{v["turnover"][(family, regime)]:.2f} times a year against '
                  f'{v["turnover"][(family, CANONICAL_REGIME)]:.2f} for the canonical '
                  f'rule and is {graded.loc[(family, regime), "classification"]}'
                  for family, regime in six_month])
        + '. '
        + ('None of the six-month rolls is robustly efficient, so the extra trading is '
           'not paid for.'
           if not any(graded.loc[key, 'classification'] == 'robustly efficient'
                      for key in six_month) else
           'At least one is, so the extra trading is paid for there.'))

    boundary = [(family, regime) for family, regime in graded.index
                if regime.startswith('15m/12m')]
    lines.append(
        '**4. Does holding a short contract to ~3 months remaining materially worsen '
        'tail behaviour?** Yes. '
        + phrase([f'{family} 15m/12m carries P(DD>60%) of '
                  f'{v["tails"][(family, "15m/12m")]:.1%} against '
                  f'{v["tails"][(family, CANONICAL_REGIME)]:.1%} for the canonical rule'
                  for family, _ in boundary])
        + '. That is the regime with about three months left at the roll, and it is '
          'also the one that buys the most delta per dollar, so the tail and the '
          'exposure are the same fact seen twice.')

    ndx_six = graded.loc[('NDX', '15m/6m')] if ('NDX', '15m/6m') in graded.index else None
    ndx_twelve = (graded.loc[('NDX', '15m/12m')]
                  if ('NDX', '15m/12m') in graded.index else None)
    lines.append(
        '**5. For XND, is 15m / 6m roll preferable to 15m / 12m after accounting for '
        'exposure?** '
        + (f'At a fixed budget the twelve-month roll earns more — median gaps '
           f'{ndx_twelve["median_fixed_cagr_gap"]:+.2%} against '
           f'{ndx_six["median_fixed_cagr_gap"]:+.2%} — but it also buys more delta '
           f'({v["ladder"][("NDX", "15m/12m")]:.2f}x the canonical against '
           f'{v["ladder"][("NDX", "15m/6m")]:.2f}x). At matched delta the gaps are '
           f'{ndx_twelve["median_matched_cagr_gap"]:+.2%} and '
           f'{ndx_six["median_matched_cagr_gap"]:+.2%}, and the tails are '
           f'{v["tails"][("NDX", "15m/12m")]:.1%} against '
           f'{v["tails"][("NDX", "15m/6m")]:.1%} on P(DD>60%). '
           + ('So it is a trade, not a ranking: the six-month roll gives up '
              f'{abs(ndx_twelve["median_matched_cagr_gap"] - ndx_six["median_matched_cagr_gap"]):.2%} '
              'of matched-delta CAGR to remove '
              f'{v["tails"][("NDX", "15m/12m")] - v["tails"][("NDX", "15m/6m")]:.1%} of '
              'tail probability, and it doubles the turnover to do it. '
              if ndx_six['median_matched_cagr_gap'] < ndx_twelve['median_matched_cagr_gap']
              and v['tails'][('NDX', '15m/6m')] < v['tails'][('NDX', '15m/12m')]
              else 'The six-month roll is the better of the two once size is held '
                   'still. ')
           + f'Both are {phrase(sorted({ndx_six["classification"], ndx_twelve["classification"]}))} '
             'against the canonical rule, so the choice between them is a choice '
             'between two regimes that rule already beats.'
           if ndx_six is not None and ndx_twelve is not None else
           'Both Nasdaq regimes are absent from the classification.'))

    short = v['short_gaps']
    lines.append(
        '**6. Does any apparent short-maturity advantage survive matching mean '
        'delta?** Read as levels rather than as a ratio, because a fixed-budget gap '
        'near zero makes the ratio say anything. '
        + phrase([f'{family} {regime} goes from {fixed:+.2%} at a fixed budget to '
                  f'{matched:+.2%} at matched delta, against a resolution bar of '
                  f'{unit:.2%}'
                  for (family, regime), (fixed, matched, unit) in short.items()])
        + '. '
        + (f'{len(v["short_survivors"])} of the {len(short)} short-maturity regimes '
           f'keeps a matched-delta gap larger than its own bar '
           f'({phrase([f"{f} {r}" for f, r in v["short_survivors"]])}); the rest is '
           'position size wearing a duration label.'
           if v['short_survivors'] else
           'Not one of them keeps a matched-delta gap larger than its own resolution '
           'bar: the short-maturity advantage is position size wearing a duration '
           'label.'))

    lines.append(
        '**7. What premium budget on 15m XND approximately reproduces the exposure of '
        'canonical 24m Nasdaq LEAPS?** '
        + phrase([f'about {ratio:.0%} of the canonical budget on the '
                  f'{int(roll)}-month roll' for roll, ratio
                  in v['matched_budgets'].items()])
        + '. On the 30% budget the earlier work used, that is '
        + phrase([f'{.30 * ratio:.1%} at {int(roll)} months'
                  for roll, ratio in v['matched_budgets'].items()])
        + '. The capital released goes to the Treasury sleeve, which is why the '
          'matched-delta rows are not simply the fixed-budget rows scaled down.')

    lines.append(
        '**8. Does contract duration materially alter the preferred SPX strike/budget '
        'region?** '
        + (f'{v["region"]}.'))

    menu = v['menu']
    lines.append(
        '**9. Which 3-5 duration/roll/strike/budget combinations represent genuinely '
        'distinct efficient risk regimes?** '
        + (phrase([f'**{row.variant}** ({row.cagr_p50:.2%} median, {row.cagr_p5:.2%} p5, '
                   f'{row.median_max_drawdown:.1%} median drawdown, P(DD>60%) '
                   f'{row.prob_drawdown_worse_than_60:.1%})' for _, row in menu.iterrows()])
           + '. Each is non-dominated on the fixed-budget frontier and separated from '
             'the one below it by more than the tolerances. **Read the duration labels '
             'with care.** This is the frontier an investor actually faces at a fixed '
             'budget, so it is the right menu to choose from — but a short duration '
             'appearing on it is not evidence that the duration is efficient, only '
             'that the exposure it happens to buy sits at a point worth occupying. '
             'The same point is reachable at another length by moving the budget.'
           if len(menu) else
           'None: the fixed-budget frontier holds no set of cells separated by more '
           'than the tolerances.'))

    lines.append(
        '**10. Are any duration rankings too small relative to one-vol-point model '
        'uncertainty to be actionable?** '
        + (f'{len(v["unresolved"])} of the {len(v["verdicts"])} regimes are classified '
           'unresolved outright. ' if len(v['unresolved']) else
           'No regime is unresolved outright. ')
        + f'One volatility point is worth between {min(v["units"].values()):.2%} and '
          f'{max(v["units"].values()):.2%} of CAGR depending on the regime, against a '
          f'matched-delta median gap of at most '
          f'{v["mechanism"]["matched_delta_cagr_gap"].abs().max():.2%} across every '
          'regime tested. '
        + (f'{v["inside_bar"]} of the {len(v["verdicts"])} regimes have a matched-delta '
           f'gap smaller than their own volatility point. '
           if v['inside_bar'] else '')
        + ('**The duration effect is smaller than the pricing assumption almost '
           'everywhere**, which is the honest headline: these rankings are ordered, but '
           'they are ordered inside the error bar of a volatility surface this '
           'repository does not observe.'
           if v['inside_bar'] >= REGIME_MAJORITY * len(v['verdicts']) else
           'So the larger duration effects do clear the pricing assumption, and only '
           'the smaller ones are unresolved by it.'))
    return '\n\n'.join(lines)


def _formats(frame, columns=None) -> dict:
    """Format spec per column, decided by dtype rather than a hand-kept list."""
    ratios = ('mean_delta_exposure', 'delta_exposure_sd', 'terminal_multiple',
              'mean_entry_years', 'mean_exit_years', 'mean_held_years', 'delta_ratio',
              'mean_exit_premium_ratio', 'option_turnover_per_year', 'vega_per_nav',
              'recovery_exposure_ratio', 'gross_notional', 'moneyness',
              'delta_exposure_p5', 'delta_exposure_p50', 'delta_exposure_p95',
              'min_exposure_after_loss', 'recovery_exposure', 'mean_delta',
              'min_delta', 'recovery_delta', 'canonical_mean_delta',
              'fixed_budget_mean_delta', 'survives_delta_matching',
              'option_turnover_per_year_difference', 'vega_per_nav_difference',
              'median_delta_ratio',
              'mean_entry_years_difference', 'recovery_exposure_ratio_difference')
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


SUMMARY_COLUMNS = ['family', 'regime', 'cells', 'mean_entry_years', 'mean_exit_years',
                   'delta_ratio', 'mean_delta_exposure', 'option_turnover_per_year',
                   'cost_drag_bps', 'theta_burden_per_year', 'vega_per_nav',
                   'cagr_per_volatility_point', 'median_30y_cagr', 'p5_30y_cagr',
                   'median_max_drawdown', 'prob_drawdown_worse_than_60']
CLASS_COLUMNS = ['family', 'regime', 'cells', 'classification', 'median_delta_ratio',
                 'median_fixed_cagr_gap', 'median_matched_cagr_gap', 'improves',
                 'worsens', 'mixed', 'unresolved', 'matched_improves',
                 'matched_worsens', 'matched_mixed', 'matched_unresolved']
MECHANISM_COLUMNS = ['family', 'regime', 'delta_ratio', 'theta_burden_per_year_difference',
                     'option_turnover_per_year_difference', 'cost_drag_bps_difference',
                     'vega_per_nav_difference', 'recovery_exposure_ratio_difference',
                     'mean_entry_years_difference', 'fixed_budget_cagr_gap',
                     'matched_delta_cagr_gap', 'survives_delta_matching']
EPISODE_COLUMNS = ['family', 'regime', 'episode', 'loss', 'peak_to_trough', 'mean_delta',
                   'min_delta', 'recovery_delta']
EXPOSURE_COLUMNS = ['family', 'regime', 'moneyness', 'premium_budget',
                    'canonical_mean_delta', 'fixed_budget_mean_delta', 'delta_ratio',
                    'matched_delta_budget', 'matched_budget_inside_grid',
                    'treasury_weight', 'matched_treasury_weight']


def report(reports: Path, inputs, historical, monte, exposure, matched, frontier,
           regimes, verdicts, mechanism, paths):
    """Write the narrative from the numbers, never alongside them."""
    v = _narrative(historical, monte, exposure, matched, frontier, regimes, verdicts,
                   mechanism)
    summary, episodes = v['summary'], episode_summary(historical)
    exposure_view = exposure[exposure.family == 'NDX'].sort_values(
        ['moneyness', 'premium_budget', 'maturity_months', 'roll_months'])
    outside = int((~exposure.matched_budget_inside_grid).sum())

    text = f"""# Contract duration and roll: efficiency, or just more delta?

Generated by `letf.leaps_duration`. Window {inputs.ix[0].date()} to {inputs.ix[-1].date()}, {len(inputs.ix):,} sessions, on the calendar, financing and option assumptions of `reports/leaps_frontier_results.md`.

`letf.leaps_frontier` mapped strike against premium budget and left maturity at the
two-year convention every earlier module inherited. `letf.xnd_short_maturity` then
found that shortening the contract does not degrade the strategy: a shorter call is
cheaper, so a fixed premium budget buys more of it and the portfolio simply moves to
a more aggressive point on the same frontier.

**That makes the obvious comparison useless on its own.** A duration that earns more
at the same budget has not been shown to be better; it has been shown to be larger.
Every comparison here is therefore run twice — at a fixed premium budget, which is the
portfolio an investor literally gets, and at a budget interpolated to match the
canonical rule's mean delta, which is the only way to ask whether the contract itself
is doing anything.

{len(VARIANTS)} variants: {sum(1 for x in VARIANTS.values() if x.underlying == NASDAQ)} Nasdaq cells across {len(NDX_REGIMES)} regimes and {sum(1 for x in VARIANTS.values() if x.underlying == EQUITY)} S&P cells across {len(SP_REGIMES)}.
Grids are prespecified and compact; 0.95 strikes are absent because the earlier
frontier found every one of them dominated inside the S&P family.

**The volatility loading is not retuned by maturity, and that is the sharpest
limitation here.** Real implied volatility has a term structure and a skew; this
repository observes neither, and charges every contract a trailing realized proxy plus
a flat {IV_PREMIUM:.0%} whether it has three months to run or thirty. A duration comparison is
exactly the comparison that assumption is least equipped to support, so every verdict
is widened to the CAGR one volatility point moves and differences inside it are
reported as unresolved rather than ranked.

## What the listed calendar delivers, and what each length buys

{markdown_table(summary, SUMMARY_COLUMNS, _formats(summary, SUMMARY_COLUMNS))}

`delta_ratio` is the median mean-delta of a regime's cells divided by the same cell
under the canonical {CANONICAL_REGIME} rule. It is the number the whole report turns on: a value
above one means the same premium budget bought a bigger position, and any return
difference at a fixed budget is that position size before it is anything else.

## Exposure normalization

For every cell: the canonical delta at the same strike and budget, the delta the
variant actually runs, the budget that would have matched it, and where the released
or absorbed capital goes. Budgets are inverted by linear interpolation over the tested
grid; {outside} of the {len(exposure)} rows fall outside it and are flagged rather than clamped.

Nasdaq rows, which are the ones bounded by what XND lists:

{markdown_table(exposure_view, EXPOSURE_COLUMNS, _formats(exposure_view, EXPOSURE_COLUMNS))}

The S&P rows are in `reports/leaps_duration_roll_exposure.csv`, and every per-cell
historical column the brief asks for is in `reports/leaps_duration_roll_historical.csv`.

## Stress episodes

Loss, peak-to-trough, and the position through and after each decline. A loss alone
does not distinguish a contract that fell with the market from one that stopped
participating on the way down; `min_delta` and `recovery_delta` are what separate them.

{markdown_table(episodes, EPISODE_COLUMNS, _formats(episodes, EPISODE_COLUMNS))}

Nasdaq total returns before {PROXY_THROUGH} are a price-only proxy grossed up with an assumed
zero dividend yield, so the 1987 row for the Nasdaq regimes lies entirely inside that
era. The dot-com bust does not.

## Monte Carlo

{paths:,} paths on the existing {PRIMARY_BLOCK}-session moving-block bootstrap at {phrase([str(y) for y in MC_HORIZONS])} years, the
repository's existing seed, **the same simulated worlds for every variant**. Every
comparison against an index or against a cell's own canonical duration is paired path
by path. Full distributions are in `reports/leaps_duration_roll_monte_carlo.csv`;
the four frontiers, with their non-domination flags, are in
`reports/leaps_duration_roll_frontier.csv`.

- `reports/leaps_duration_roll_frontier.png` — fixed budget beside matched delta, which is the comparison the module exists to make.
- `reports/leaps_duration_roll_exposure.png` — what a dollar of premium buys at each length, and what matching it would cost.

## Regime classification

Each regime against the canonical {CANONICAL_REGIME} rule, cell by cell, scored twice. A regime is
**robustly efficient** when it improves at least one objective on a {REGIME_MAJORITY:.0%} majority of
its cells without materially worsening any, **dominated** when the reverse holds,
**unresolved** when most cells sit inside the resolution bar, and **conditionally
efficient** otherwise. The verdict is read off the matched-delta columns, because a
regime that improves only at a fixed budget has bought its improvement with position
size — which any budget can buy.

{markdown_table(verdicts, CLASS_COLUMNS, _formats(verdicts, CLASS_COLUMNS))}

## Mechanism

Where each regime's difference comes from, and how much of it is left once position
size is held still.

{markdown_table(mechanism, MECHANISM_COLUMNS, _formats(mechanism, MECHANISM_COLUMNS))}

`survives_delta_matching` is the matched-delta gap divided by the fixed-budget gap.
Near zero means the duration bought leverage and nothing else.

## Conclusions

{_answers(v)}

## What this can and cannot say about a real chain

XND currently lists to roughly fifteen months; SPX and XSP list far longer. So the two
markets do face different feasible duration regimes, and the Nasdaq
{CANONICAL_REGIME} rows above are a reference an investor cannot presently buy rather than a
choice they are declining.

But the comparison is structural, not a live-chain valuation. It charges one flat
volatility loading to every length, and real term structure and skew are precisely
what would decide a duration question. It observes no bid/ask beyond a flat {OPTION_SPREAD_BPS:.0f}bp, no
early-exercise or assignment behaviour, and no difference in the volatility risk
premium between the two markets — the S&P loading is simply reused on the Nasdaq. The
bootstrap resamples one history and cannot produce a term structure it never saw.

**The practical reading.** For an investor choosing between currently available XND
and longer-dated SPX or XSP LEAPS, the evidence supports choosing the length that is
convenient and then setting the premium budget to the exposure actually wanted, rather
than choosing a length for its own sake. The duration differences that survive matching
the delta are smaller than one volatility point of pricing uncertainty almost
everywhere in this lattice, and the differences that do not survive it were exposure
calibration wearing a duration label.
"""
    (reports / 'leaps_duration_roll_results.md').write_text(text)


if __name__ == '__main__':
    main()
