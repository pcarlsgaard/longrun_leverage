"""Ways to buy crash protection while staying leveraged, compared on one window.

The signal batteries in this repository all ask the same question — does trend
timing help? — and `letf.null_model` answers it: not measurably, and what
advantage there is comes from about twenty sessions. That reframes the problem.
A rule that underperforms for 99.8% of its history and is paid in crashes is not
a timing strategy, it is a synthetic put bought on instalments. The question
worth asking next is a pricing question: is there a cheaper way to buy the same
convexity?

This module compares four families on one window and one financing basis:

* the trend rule itself, rotating out of leverage on a price SMA;
* static leveraged stock/bond mixes, which buy convexity through rebalancing
  and a correlation that is assumed rather than triggered;
* simply holding less leverage, which is the only hedge that cannot fail;
* rolling long-dated calls, which buy convexity contractually.

**The option family is modelled, not measured.** See `letf.options` for why, and
read the break-even volatility table rather than its CAGR. Every other number
here is arithmetic on realized prices, as everywhere else in this repository.

Note what cannot be run here. `letf.null_model` permutes a position series, so
it applies only to the rule that has one; a static mix and a roll schedule have
no timing to randomize. Edge concentration applies to everything and is reported
for everything, because it is the diagnostic that killed the original result.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import platform

import numpy as np
import pandas as pd
import scipy

from .analysis import CASH, load_inputs
from .cohorts import cohort_cagrs, nav_path
from .diagnostics import edge_concentration
from .falsification import load_price_signals
from .model import calendar_days, matched, portfolio
from .options import (LeapsRule, break_even_iv_premium, implied_volatility_proxy,
                      simulate_leaps_portfolio, trailing_dividend_yield, trailing_riskfree)
from .provenance import FLOAT_FORMAT, sha, source_hashes, stable_floats
from .signals import level_position
from .strategy import select_returns, switching_costs

SMA_DAYS = 200
SPREAD_BPS = 50
SWITCH_COST_BPS = 25
LAG = 2
TARGET_EXPOSURE = 3.
MATURITY_YEARS = 2.
IV_PREMIUM = .03
OPTION_SPREAD_BPS = 100.

UPRO, SSO, TMF = 'UPRO_SPREAD_50BP', 'SSO_SPREAD_50BP', 'TMF_SPREAD_50BP'
EQUITY, TREASURY = 'SP500_1X', 'LONG_TREASURY_1X'

SUBPERIODS = {'1987_1999': ('1986-09-29', '1999-12-31'),
              '2000_2009': ('2000-01-01', '2009-12-31'),
              '2010_2019': ('2010-01-01', '2019-12-31'),
              '2020_2021': ('2020-01-01', '2021-12-31'),
              '2022': ('2022-01-01', '2022-12-31'),
              '2023_latest': ('2023-01-01', '2026-09-02')}
CRASHES = {'1987_crash': ('1987-08-25', '1987-12-04'),
           '2000_2002_bust': ('2000-03-24', '2002-10-09'),
           '2008_2009_gfc': ('2007-10-09', '2009-03-09'),
           '2020_covid': ('2020-02-19', '2020-03-23'),
           '2022_rates': ('2022-01-03', '2022-10-12')}

def cagr(returns: pd.Series) -> float:
    years = (returns.index[-1] - returns.index[0]).days / 365.25
    return float((1 + returns).prod() ** (1 / years) - 1)


def max_drawdown(returns: pd.Series) -> float:
    wealth = (1 + returns).cumprod()
    return float((wealth / wealth.cummax() - 1).min())


def describe(name: str, returns: pd.Series, calendar, exposure=np.nan) -> dict:
    nav = nav_path(returns, calendar)
    twenty, thirty = cohort_cagrs(nav, 20), cohort_cagrs(nav, 30)
    return dict(series=name, cagr=cagr(returns), max_drawdown=max_drawdown(returns),
                annualized_volatility=float(returns.std(ddof=1) * np.sqrt(252)),
                terminal_multiple=float((1 + returns).prod()),
                mean_delta_exposure=float(exposure),
                cohort_20y_min_cagr=float(twenty.min()),
                cohort_20y_median_cagr=float(np.median(twenty)),
                cohort_30y_min_cagr=float(thirty.min()),
                cohort_30y_median_cagr=float(np.median(thirty)))


def leaps_returns(price_with_entry, safe_returns, dividend, riskfree, vol, rule):
    """Daily returns of a rolling-call portfolio, aligned to the safe sleeve."""
    nav, exposure, rolls = simulate_leaps_portfolio(
        price_with_entry, safe_returns, dividend, riskfree, vol, rule)
    return nav.pct_change().dropna(), exposure, rolls


def build_candidates(daily, price, ix, calendar):
    """Every structure under comparison, on one window and one financing basis.

    Exposure is reported as delta-equivalent equity per dollar of portfolio so
    the families are comparable: a 3x fund held always is 3.0, a 60/40 mix of a
    3x fund and a 3x bond fund is 1.8 in equity, and an option sleeve is
    whatever its delta implies. Equal exposure is not equal risk and these
    numbers are descriptive, not a matching.
    """
    entry = calendar[calendar.get_loc(ix[0]) - 1]
    closes = pd.DatetimeIndex([entry]).append(ix)
    spot = price.reindex(closes)
    if spot.isna().any():
        raise ValueError('Price index does not cover the comparison window')
    d = daily.loc[ix]
    position = level_position(price, calendar, SMA_DAYS, LAG).loc[ix]

    def timed(off):
        return switching_costs(select_returns(d, position, {1: UPRO, 0: off}),
                               position, SWITCH_COST_BPS)

    def mix(weights):
        return portfolio(d[list(weights)], pd.Series(weights), 'quarterly')

    levered = position.mean() * TARGET_EXPOSURE
    candidates = {
        'SP500_1X': (d[EQUITY], 1.),
        'UPRO_ALWAYS_3X': (d[UPRO], 3.),
        'SSO_ALWAYS_2X': (d[SSO], 2.),
        'UPRO_SMA_TO_SP500': (timed(EQUITY), levered + (1 - position.mean())),
        'UPRO_SMA_TO_TBILL': (timed(CASH), levered),
        'UPRO60_TMF40': (mix({UPRO: .6, TMF: .4}), 1.8),
        'UPRO55_TMF45': (mix({UPRO: .55, TMF: .45}), 1.65),
        'UPRO50_LT50': (mix({UPRO: .5, TREASURY: .5}), 1.5),
    }
    # Hedge and trend together: the mix while the trend is up, 1x while it is down.
    blended = pd.Series(np.where(position == 1, candidates['UPRO60_TMF40'][0], d[EQUITY]),
                        index=ix, name='UPRO60_TMF40_SMA')
    candidates['UPRO60_TMF40_SMA'] = (
        switching_costs(blended, position, SWITCH_COST_BPS), 1.8 * position.mean())

    dividend = trailing_dividend_yield(spot, daily[EQUITY].reindex(closes))
    riskfree = trailing_riskfree(daily[CASH].reindex(closes))
    vol = implied_volatility_proxy(spot.pct_change().fillna(0.), IV_PREMIUM, MATURITY_YEARS)
    options = {
        'LEAPS_ATM_50_TBILL': dict(moneyness=1., premium_budget=.5, safe=CASH),
        'LEAPS_ATM_40_TBILL': dict(moneyness=1., premium_budget=.4, safe=CASH),
        'LEAPS_ATM_50_TREASURY': dict(moneyness=1., premium_budget=.5, safe=TREASURY),
        'LEAPS_ATM_40_TREASURY': dict(moneyness=1., premium_budget=.4, safe=TREASURY),
        'LEAPS_ITM_30_TREASURY': dict(moneyness=.9, premium_budget=.3, safe=TREASURY),
        'LEAPS_RESTRUCK_3X': dict(moneyness=.9, premium_budget=None, safe=CASH),
    }
    ledgers = {}
    for name, spec in options.items():
        rule = LeapsRule(moneyness=spec['moneyness'], premium_budget=spec['premium_budget'],
                         maturity_years=MATURITY_YEARS, target_exposure=TARGET_EXPOSURE,
                         iv_premium=IV_PREMIUM, spread_bps=OPTION_SPREAD_BPS)
        returns, exposure, rolls = leaps_returns(
            spot, d[spec['safe']], dividend, riskfree, vol, rule)
        candidates[name] = (returns, float(exposure.mean()))
        ledgers[name] = rolls
    return candidates, ledgers, (spot, dividend, riskfree)


RESET_PERIODS = {'daily': 'D', 'weekly': 'W', 'monthly': 'M',
                 'quarterly': 'Q', 'annual': 'Y'}


def implied_financing(underlying, levered, leverage, expense, days):
    """Cost of one borrowed dollar per session, recovered from the fund identity.

    `letf.model.simulate` builds a leveraged series as
    `L*u - (L-1)*(funding + spread*days/360) - expense*days/365`, so given the
    unleveraged return, the committed leveraged series and the expense ratio,
    the bracketed financing term is determined. Recovering it this way rather
    than rebuilding it keeps the reset ladder on exactly the funding and spread
    assumptions every other result here uses, and needs no input the repository
    does not already have — the overnight-rate history is not among its cached
    sources.
    """
    return (leverage * underlying - levered - expense * days / 365) / (leverage - 1)


def constant_leverage(underlying, financing, leverage, period):
    """Constant `leverage`, restored only at `period` boundaries.

    Within a period the exposure is left alone: `L` dollars of index and `L-1`
    of debt both compound untouched, so a fall raises the effective leverage
    instead of triggering a sale. That is the whole point of the comparison —
    it isolates reset frequency from every other difference between a
    daily-reset fund and a rolled option.

    Wipeout is absorbing, as it is in `letf.model.simulate`. It has to be
    modelled explicitly here because, unlike a daily reset, a slow reset really
    can put the debt above the assets: the loan does not shrink as the
    collateral falls.
    """
    if not underlying.index.equals(financing.index):
        raise ValueError('Underlying and financing calendars differ')
    if leverage < 1:
        raise ValueError('This comparison covers long leverage only')
    u, f = underlying.to_numpy(), financing.to_numpy()
    codes = underlying.index.to_period(period)
    starts = np.flatnonzero(np.r_[True, codes[1:] != codes[:-1]])
    values, wealth, dead = np.empty(len(u)), 1., False
    for k, begin in enumerate(starts):
        stop = starts[k + 1] if k + 1 < len(starts) else len(u)
        if dead:
            values[begin:stop] = 0.
            continue
        held = (leverage * wealth * np.cumprod(1 + u[begin:stop])
                - (leverage - 1) * wealth * np.cumprod(1 + f[begin:stop]))
        bust = np.flatnonzero(held <= 0)
        if len(bust):
            held[bust[0]:], dead = 0., True
        values[begin:stop] = held
        wealth = float(held[-1])
    previous = np.r_[1., values[:-1]]
    returns = np.where(previous > 0, values / np.where(previous > 0, previous, 1.) - 1., 0.)
    return pd.Series(returns, index=underlying.index, name='return')


def reset_ladder(daily, ix, calendar, expense, leverages=(2., 3.)):
    """Does restoring leverage less often help? Measured, with no option in sight.

    The option family's advantage over a daily-reset fund is usually explained
    by variance drag: a daily reset sells into declines and buys into rallies,
    and an annual roll does not. That explanation is testable without any option
    at all, by varying only the reset frequency. It is worth testing because it
    is the one claim about the option structures that does not depend on a
    modelled premium.
    """
    entry = calendar[calendar.get_loc(ix[0]) - 1]
    days = calendar_days(ix, entry)
    financing = implied_financing(daily.loc[ix, EQUITY], daily.loc[ix, UPRO], 3., expense, days)
    rows = []
    for leverage in leverages:
        for label, period in RESET_PERIODS.items():
            returns = constant_leverage(daily.loc[ix, EQUITY], financing, leverage, period)
            terminal = float((1 + returns).prod())
            rows.append(dict(leverage=leverage, reset=label,
                             cagr=cagr(returns) if terminal > 0 else -1.,
                             max_drawdown=max_drawdown(returns),
                             terminal_multiple=terminal,
                             wiped_out=bool(terminal <= 0)))
    return pd.DataFrame(rows), financing


def concentration_table(candidates, benchmarks):
    """Edge concentration of every candidate against several benchmarks.

    Reported against more than one benchmark on purpose. Concentration measured
    against a benchmark that itself loses almost everything in a crash is partly
    an artifact of that benchmark: any survivor's advantage over a strategy that
    falls 98% is bound to arrive in the crash. The unleveraged index is the
    control that shows how much of the concentration is the candidate's own.
    """
    rows = []
    for benchmark in benchmarks:
        base = candidates[benchmark][0]
        for name, (returns, _) in candidates.items():
            if name == benchmark:
                continue
            row = dict(series=name, benchmark=benchmark)
            row.update(edge_concentration(returns, base))
            total = row['total_log_advantage']
            share = row['top20_day_share']
            row['advantage_excluding_top_20_days'] = total * (1 - share)
            row['wealth_ratio_excluding_top_20_days'] = float(np.exp(total * (1 - share)))
            rows.append(row)
    return pd.DataFrame(rows)


def window_table(candidates, windows, annualize):
    rows = []
    for name, (returns, _) in candidates.items():
        row = dict(series=name)
        for label, (start, end) in windows.items():
            piece = returns.loc[start:end]
            row[label] = cagr(piece) if annualize else float((1 + piece).prod() - 1)
        rows.append(row)
    return pd.DataFrame(rows)


def leaps_grid(spot, daily, ix, dividend, riskfree, premiums=(0., .03, .06, .09)):
    """Sensitivity of the option family to every choice made building it."""
    rows = []
    for premium in premiums:
        vol = implied_volatility_proxy(spot.pct_change().fillna(0.), premium, MATURITY_YEARS)
        for moneyness in (.7, .8, .9, 1., 1.05, 1.1):
            for budget in (.3, .4, .5, .6):
                for safe in (CASH, TREASURY):
                    rule = LeapsRule(moneyness=moneyness, premium_budget=budget,
                                     maturity_years=MATURITY_YEARS, iv_premium=premium,
                                     spread_bps=OPTION_SPREAD_BPS)
                    nav = simulate_leaps_portfolio(spot, daily.loc[ix, safe], dividend,
                                                   riskfree, vol, rule, ledger=False)
                    returns = nav.pct_change().dropna()
                    rows.append(dict(iv_premium=premium, moneyness=moneyness,
                                     premium_budget=budget, safe_asset=safe,
                                     mean_implied_vol=float(vol.mean()),
                                     cagr=cagr(returns),
                                     max_drawdown=max_drawdown(returns)))
    return pd.DataFrame(rows)


def breakeven_table(spot, daily, ix, dividend, riskfree, rivals, structures):
    """Volatility the options must carry before each structure loses to each rival.

    This is the honest headline for the option family. The implied volatility
    that produced their returns is an assumption this repository cannot check,
    so the useful statement is not "they earned X" but "they stop winning above
    Y", which a reader can compare against option prices they do know.
    """
    price_returns = spot.pct_change().fillna(0.)
    rows = []
    for label, spec in structures.items():
        def build(premium, spec=spec):
            vol = implied_volatility_proxy(price_returns, premium, MATURITY_YEARS)
            rule = LeapsRule(moneyness=spec['moneyness'], premium_budget=spec['premium_budget'],
                             maturity_years=MATURITY_YEARS, iv_premium=premium,
                             spread_bps=OPTION_SPREAD_BPS)
            nav = simulate_leaps_portfolio(spot, daily.loc[ix, spec['safe']], dividend,
                                           riskfree, vol, rule, ledger=False)
            return cagr(nav.pct_change().dropna())

        row = dict(structure=label, cagr_at_base_premium=build(IV_PREMIUM))
        for rival, rival_cagr in rivals.items():
            premium = break_even_iv_premium(rival_cagr, build)
            row[f'{rival}_breakeven_premium'] = premium
            row[f'{rival}_breakeven_mean_iv'] = (
                float(implied_volatility_proxy(price_returns, premium, MATURITY_YEARS).mean())
                if np.isfinite(premium) else np.nan)
        rows.append(row)
    return pd.DataFrame(rows)


BREAKEVEN_STRUCTURES = {
    'LEAPS_ATM_50_TBILL': dict(moneyness=1., premium_budget=.5, safe=CASH),
    'LEAPS_ATM_40_TBILL': dict(moneyness=1., premium_budget=.4, safe=CASH),
    'LEAPS_ATM_50_TREASURY': dict(moneyness=1., premium_budget=.5, safe=TREASURY),
    'LEAPS_ATM_40_TREASURY': dict(moneyness=1., premium_budget=.4, safe=TREASURY),
    'LEAPS_ITM_30_TREASURY': dict(moneyness=.9, premium_budget=.3, safe=TREASURY),
}
BENCHMARKS = ('UPRO_ALWAYS_3X', 'SP500_1X')
RIVALS = ('UPRO_SMA_TO_SP500', 'UPRO60_TMF40', 'UPRO_ALWAYS_3X')


def run(root: Path):
    daily, config = load_inputs(root, offline=True)
    calendar = daily.index
    price = load_price_signals(root, config, offline=True)['SP500']
    # The comparison window is inherited from the falsification and null-model
    # batteries, including their 250-day Nasdaq warm-up, which binds first. It is
    # kept even though nothing here trades the Nasdaq, so that every CAGR in this
    # report is directly comparable to the ones those reports already publish.
    nasdaq = load_price_signals(root, config, offline=True)['NASDAQ100']
    ix = matched(pd.concat([
        daily[[EQUITY, TREASURY, CASH, UPRO, SSO, TMF, 'NASDAQ100_1X']],
        level_position(price, calendar, 250, LAG),
        level_position(nasdaq, calendar, 250, LAG),
        level_position(price, calendar, SMA_DAYS, LAG),
    ], axis=1)).index

    candidates, ledgers, (spot, dividend, riskfree) = build_candidates(daily, price, ix, calendar)
    metrics = pd.DataFrame([describe(name, returns, calendar, exposure)
                            for name, (returns, exposure) in candidates.items()])
    concentration = concentration_table(candidates, BENCHMARKS)
    subperiods = window_table(candidates, SUBPERIODS, annualize=True)
    crashes = window_table(candidates, CRASHES, annualize=False)
    grid = leaps_grid(spot, daily, ix, dividend, riskfree)
    ladder, _ = reset_ladder(daily, ix, calendar, config['funds']['UPRO']['expense'])
    rivals = {name: float(metrics.set_index('series').loc[name, 'cagr']) for name in RIVALS}
    breakeven = breakeven_table(spot, daily, ix, dividend, riskfree, rivals,
                                BREAKEVEN_STRUCTURES)
    rolls = pd.concat([frame.assign(structure=name) for name, frame in ledgers.items()],
                      ignore_index=True)

    reports = root / 'reports'
    outputs = {'hedge_alternatives_metrics.csv': metrics,
               'hedge_alternatives_concentration.csv': concentration,
               'hedge_alternatives_subperiods.csv': subperiods,
               'hedge_alternatives_crashes.csv': crashes,
               'hedge_alternatives_leaps_grid.csv': grid,
               'hedge_alternatives_breakeven.csv': breakeven,
               'hedge_alternatives_rolls.csv': rolls,
               'hedge_alternatives_reset_ladder.csv': ladder}
    for name, frame in outputs.items():
        frame.pipe(stable_floats).to_csv(reports / name, index=False, float_format=FLOAT_FORMAT)

    report(reports, metrics, concentration, subperiods, crashes, grid, breakeven,
           ladder, ix, rivals)
    (reports / 'hedge_alternatives_manifest.json').write_text(json.dumps({
        'window': [ix[0].date().isoformat(), ix[-1].date().isoformat()],
        'observations': int(len(ix)), 'sma_days': SMA_DAYS, 'lag': LAG,
        'spread_bps': SPREAD_BPS, 'switch_cost_bps': SWITCH_COST_BPS,
        'option_spread_bps': OPTION_SPREAD_BPS, 'iv_premium': IV_PREMIUM, 'maturity_years': MATURITY_YEARS,
        'target_exposure': TARGET_EXPOSURE, 'leaps_grid_rows': int(len(grid)),
        'option_prices': 'modelled with Black-Scholes on an assumed implied volatility; '
                         'this repository holds no option price history',
        'python': platform.python_version(), 'numpy': np.__version__,
        'pandas': pd.__version__, 'scipy': scipy.__version__,
        'source_hashes': source_hashes(root, __spec__.name),
        'outputs_sha256': {name: sha(reports / name) for name in outputs},
    }, indent=2) + '\n')
    print(f'Hedge alternatives: {len(metrics)} structures, {len(grid)} option grid rows; '
          f'{ix[0].date()}-{ix[-1].date()}.')
    return metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('--offline', action='store_true', default=True)
    run(parser.parse_args().root)



def _table(frame, columns, formats, index=None):
    """Markdown table from a frame, formatting each column by name."""
    header = '| ' + ' | '.join(columns) + ' |'
    align = '|' + '|'.join('---:' if formats.get(c) else '---' for c in columns) + '|'
    lines = [header, align]
    for _, row in frame.iterrows():
        cells = []
        for column in columns:
            spec = formats.get(column)
            value = row[column]
            cells.append(format(value, spec) if spec and pd.notna(value)
                         else ('' if pd.isna(value) else str(value)))
        lines.append('| ' + ' | '.join(cells) + ' |')
    return '\n'.join(lines)


def report(reports, metrics, concentration, subperiods, crashes, grid, breakeven,
           ladder, ix, rivals):
    """Write the narrative from the numbers, never alongside them."""
    m = metrics.set_index('series')
    sma = m.loc['UPRO_SMA_TO_SP500']
    mix = m.loc['UPRO60_TMF40']
    always = m.loc['UPRO_ALWAYS_3X']
    two = m.loc['SSO_ALWAYS_2X']
    restruck = m.loc['LEAPS_RESTRUCK_3X']

    versus_levered = concentration[concentration.benchmark == 'UPRO_ALWAYS_3X'].set_index('series')
    versus_index = concentration[concentration.benchmark == 'SP500_1X'].set_index('series')
    cheapest = breakeven.set_index('structure')['UPRO60_TMF40_breakeven_mean_iv'].min()
    dearest = breakeven.set_index('structure')['UPRO60_TMF40_breakeven_mean_iv'].max()
    versus_always = breakeven.set_index('structure')['UPRO_ALWAYS_3X_breakeven_mean_iv']
    base_iv = float(grid.loc[grid.iv_premium == IV_PREMIUM, 'mean_implied_vol'].iloc[0])
    beats_mix = int((breakeven.cagr_at_base_premium > mix.cagr).sum())
    beats_always = int((breakeven.cagr_at_base_premium > always.cagr).sum())

    # Which structure cushioned each crash best and worst, taken from the table
    # rather than asserted, so the narrative cannot drift from the numbers.
    leveraged = crashes[crashes.series != 'SP500_1X'].set_index('series')
    best_worst = [dict(crash=name, best=leveraged[name].idxmax(),
                       best_return=leveraged[name].max(),
                       worst=leveraged[name].idxmin(), worst_return=leveraged[name].min())
                  for name in CRASHES]
    most_wins = max(pd.Series([row['best'] for row in best_worst]).value_counts())
    mix_crash = leveraged.loc['UPRO60_TMF40']
    always_crash = leveraged.loc['UPRO_ALWAYS_3X']

    rungs = ladder[ladder.leverage == 3.].set_index('reset')
    survivors = rungs[~rungs.wiped_out]
    daily_rung = rungs.loc['daily']
    best_rung = survivors.loc[survivors.cagr.idxmax()]
    ruined = list(rungs[rungs.wiped_out].index)
    best_option = m.loc[[i for i in m.index if i.startswith('LEAPS_')
                         and i != 'LEAPS_RESTRUCK_3X']].cagr.max()

    metric_columns = ['series', 'cagr', 'max_drawdown', 'annualized_volatility',
                      'mean_delta_exposure', 'cohort_20y_min_cagr', 'cohort_30y_min_cagr']
    metric_formats = {c: '.2%' for c in metric_columns[1:]}
    metric_formats['mean_delta_exposure'] = '.2f'

    text = f"""# Buying crash protection while staying leveraged

`reports/signal_null_model_results.md` establishes that the trend rule's
advantage is not distinguishable from chance once the search is accounted for,
and that essentially all of it arrives in about twenty sessions. A structure
that loses to its benchmark on the other ~{len(ix) - 20:,} sessions and is repaid in
crashes is a synthetic put bought on instalments. This report asks the pricing
question that follows: is there a cheaper way to buy that convexity?

Window **{ix[0].date()}** to **{ix[-1].date()}** ({len(ix):,} sessions), {SPREAD_BPS} bp financing
spread, {SWITCH_COST_BPS} bp switching cost, {SMA_DAYS}-day price SMA at LAG{LAG}. Identical
assumptions across every structure, so the columns are comparable to each other
and to the null-model report.

## What is modelled rather than measured

Every number in this repository except the `LEAPS_` rows is arithmetic on
realized prices. The `LEAPS_` rows are not. This repository holds no option
prices and no implied-volatility history, and the environment that produced it
has no network access to obtain them, so premia are Black-Scholes values on an
**assumed** implied volatility: trailing exponentially-weighted realized
volatility plus a flat premium of {IV_PREMIUM * 100:.0f} volatility points, averaging
**{base_iv:.1%}** over the window.

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

{_table(metrics.sort_values('cagr', ascending=False), metric_columns, metric_formats)}

Exposure is delta-equivalent equity per dollar of portfolio, descriptive only:
equal exposure is not equal risk. Cohort columns are overlapping windows and
are historical outcomes, not independent draws.

## Concentration: is the advantage twenty days again?

{_table(versus_levered.reset_index().sort_values('total_log_advantage', ascending=False),
        ['series', 'total_log_advantage', 'top1_day_share', 'top20_day_share',
         'wealth_ratio_excluding_top_20_days', 'best_day'],
        {'total_log_advantage': '.3f', 'top1_day_share': '.1%', 'top20_day_share': '.1%',
         'wealth_ratio_excluding_top_20_days': '.2f'})}

Against always-on 3x leverage, **{(versus_levered.top20_day_share > 1).sum()} of {len(versus_levered)}**
structures still show a top-20 share above 100%. That is largely a property of
the benchmark rather than of the candidates: a strategy that falls
{abs(always.max_drawdown):.0%} at its worst can only be beaten in the crash. The unleveraged
index is the control:

{_table(versus_index.reset_index().sort_values('total_log_advantage', ascending=False),
        ['series', 'total_log_advantage', 'top1_day_share', 'top20_day_share',
         'wealth_ratio_excluding_top_20_days'],
        {'total_log_advantage': '.3f', 'top1_day_share': '.1%', 'top20_day_share': '.1%',
         'wealth_ratio_excluding_top_20_days': '.2f'})}

Against the index, **{(versus_index.top20_day_share > 1).sum()} of {len(versus_index)}**
exceed 100%. Concentration is therefore a statement about a pair, not about a
strategy, and the original finding should be read that way too: the trend rule's
edge over *always-on leverage* is twenty days; its edge over the *index* is not.

## Where each structure fails

Subperiod CAGR:

{_table(subperiods, ['series'] + list(SUBPERIODS), {k: '.1%' for k in SUBPERIODS})}

Total return through each crash:

{_table(crashes, ['series'] + list(CRASHES), {k: '.1%' for k in CRASHES})}

Best and worst leveraged structure in each crash, from the table above:

{_table(pd.DataFrame(best_worst), ['crash', 'best', 'best_return', 'worst', 'worst_return'],
        {'best_return': '.1%', 'worst_return': '.1%'})}

The hedges fail in different regimes, and that is the most useful thing here.
No structure is best in more than {most_wins} of the {len(CRASHES)} crashes, and the one
that cushions a liquidity crash is not the one that cushions a rates shock: in
2022 duration and equity fell together and the bond mixes did worse than holding
no hedge at all ({mix_crash['2022_rates']:.1%} for `UPRO60_TMF40` against
{always_crash['2022_rates']:.1%} for always-on 3x). Neither family is a
general-purpose hedge, and the whole window is one secular declining-rate
regime, so the bond leg's contribution is itself a single-regime bet in exactly
the way October 1987 is for the trend rule.

## Options: the break-even volatility

{_table(breakeven, ['structure', 'cagr_at_base_premium'] +
        [f'{r}_breakeven_mean_iv' for r in RIVALS],
        dict({'cagr_at_base_premium': '.2%'},
             **{f'{r}_breakeven_mean_iv': '.1%' for r in RIVALS}))}

Each column is the average implied volatility at which that structure's return
falls to the named rival's. Blank means it never does across the bracket
searched.

At the assumed **{base_iv:.1%}**, {beats_mix} of the {len(breakeven)} option structures beat
`UPRO60_TMF40` and {beats_always} beat always-on 3x. They stop beating the hedged
alternatives between **{cheapest:.1%}** and **{dearest:.1%}** average implied volatility —
a margin over the assumption of only **{(cheapest - base_iv) * 100:+.1f}** to
**{(dearest - base_iv) * 100:+.1f}** volatility points, which is smaller than the
uncertainty in the assumption itself. Long-dated index options plausibly traded
inside that band over this window, so **this report does not establish that
options beat the hedged alternatives.**

Against always-on 3x the margin is wider — break-even from **{versus_always.min():.1%}**
to **{versus_always.max():.1%}**, or **{(versus_always.min() - base_iv) * 100:+.1f}** to
**{(versus_always.max() - base_iv) * 100:+.1f}** points over the assumption. It is the
strongest claim the option family supports, and the next section tests the
mechanism behind it without pricing a single option.

The option grid searched {len(grid):,} rows before these were selected. That is a
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

{_table(ladder, ['leverage', 'reset', 'cagr', 'max_drawdown', 'terminal_multiple', 'wiped_out'],
        {'leverage': '.0f', 'cagr': '.2%', 'max_drawdown': '.1%', 'terminal_multiple': ',.1f'})}

**Half the explanation survives and half of it does not.**

Slowing the reset does pay. At 3x it is worth {best_rung.cagr - daily_rung.cagr:.2%} a year going
from daily to {best_rung.name}, measured on realized prices with nothing modelled. So
variance drag is real and it is roughly the size the option structures imply.

Read that as a statement about volatility, not about patience. What a daily
reset pays for is oscillation — it sells after falls and buys after rises — so
the saving only exists where there is volatility to harvest. On a smoothly
rising path a slow reset earns *less*, because a gain dilutes the leverage while
the loan stays put. Both directions are pinned by tests.

But the benefit is not monotone, and past the turn it is not a penalty, it is
ruin: at 3x the {' and '.join(ruined)} rungs are **wiped out entirely**. A margin loan does
not shrink as its collateral falls, so a long enough gap between rebalances lets
the debt overtake the assets. At 2x no rung is destroyed, which is the same
point from the other side — the cliff is a function of leverage, not of patience.

That reframes what the options are doing. They are not merely a slow reset,
because a slow reset at 3x is fatal. They obtain the slow-reset benefit
*and survive it*, because a call's loss is capped at its premium while a loan's
is not. The best option structure reaches {best_option:.2%} against the best surviving
rung's {best_rung.cagr:.2%}, and it never dies. **The convexity is not a bonus on top
of the drag saving; it is what makes the drag saving reachable at this
leverage.**

The modelled premium is still doing work in that {best_option:.2%}. What this section
establishes without any model is narrower and worth stating on its own: reset
frequency matters, it matters by percentage points a year, and the frequency
that would capture most of it cannot be held with borrowed money.

## Re-levering defeats the bounded loss

`LEAPS_RESTRUCK_3X` re-strikes to {TARGET_EXPOSURE:.0f}x of *current* wealth at each roll
instead of spending a fixed budget. It reaches {restruck.cagr:.2%} with a
{restruck.max_drawdown:.1%} drawdown and a worst 20-year cohort of
{restruck.cohort_20y_min_cagr:.2%}.

An individual call cannot lose more than its premium. That does not bound the
portfolio, because re-striking to a constant multiple of reduced wealth
compounds the losses across rolls: three bad years in succession each take most
of a fresh premium. The bounded-loss property people expect from options is a
property of a **fixed budget**, not of options. `LEAPS_ATM_40_TREASURY` and its
siblings keep a constant safe weight and get the floor; this row does not.

## What this supports

1. **Less leverage is the hedge that cannot fail.** `SSO_ALWAYS_2X` returns
   {two.cagr:.2%} against {always.cagr:.2%} for 3x, with a {two.max_drawdown:.1%}
   drawdown against {always.max_drawdown:.1%} and a worst 20-year cohort of
   {two.cohort_20y_min_cagr:.2%} against {always.cohort_20y_min_cagr:.2%}. No signal, no
   counterparty, no regime assumption.
2. **The static mix matches the trend rule without a signal.** {mix.cagr:.2%} against
   {sma.cagr:.2%}, with a better drawdown ({mix.max_drawdown:.1%} against
   {sma.max_drawdown:.1%}) and a better worst 20-year cohort
   ({mix.cohort_20y_min_cagr:.2%} against {sma.cohort_20y_min_cagr:.2%}) — and zero
   switches. Its exposure to a 2022-style rates shock is the price.
3. **Options plausibly dominate daily-reset funds, and half the mechanism is
   measured.** The break-even against always-on 3x is the widest margin in the
   table ({(versus_always.max() - base_iv) * 100:+.1f} volatility points at most). Slowing a
   reset really is worth {best_rung.cagr - daily_rung.cagr:.2%} a year with no option
   involved — but only up to a point, and past it a margin position is destroyed
   outright. The option's contribution is surviving the frequency that kills the
   loan.
4. **Options do not clearly dominate the hedged alternatives.** Break-even sits
   {(cheapest - base_iv) * 100:+.1f} to {(dearest - base_iv) * 100:+.1f} points from the
   assumption — inside its own error bar. This report cannot settle that and
   should not be read as settling it.
5. **A bounded loss per contract is not a bounded loss per portfolio.** The
   fixed-budget structures keep a floor; `LEAPS_RESTRUCK_3X` does not, and lands
   at a {restruck.max_drawdown:.1%} drawdown and a {restruck.cohort_20y_min_cagr:.2%}
   worst 20-year cohort — worse on both than the daily-reset fund it was meant
   to improve on.

## What would change any of this

Option price history, which would replace the modelled premia with measured
ones and make the break-even table unnecessary. Failing that, out-of-sample
data, or a rates regime that is not the forty-year bond bull market this window
consists of. Nothing here is established; these are candidates ranked by how
much has to be assumed to believe them.
"""
    (reports / 'hedge_alternatives_results.md').write_text(text)

if __name__ == '__main__':
    main()
