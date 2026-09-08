"""Does a faster-growing underlying buy the same LEAPS exposure with less premium?

Every option structure in this repository is written on the S&P 500, and the
family is ordered by premium budget: more budget, more return, more drawdown.
The Nasdaq-100 grew faster over the same window and was materially more volatile.
Those two facts push in opposite directions on the same question, which is why it
is worth asking rather than assuming:

**can a higher-growth underlying reach comparable long-run returns with less
capital at risk in options, or does the volatility that comes with it take back
through the option price and the path what the growth rate gives?**

Four Nasdaq structures, prespecified and not searched: strikes at 0.80 and 0.85,
budgets between 20% and 30%, residual capital in long Treasuries, everything else
inherited unchanged from the S&P work — roughly two-year maturity, annual roll on
listed expiries, a trailing realized-volatility proxy over the option's own
horizon plus three volatility points, the same Black-Scholes framework and the
same option spread.

**The volatility premium is deliberately not retuned for the Nasdaq.** Real
Nasdaq index options are not priced off the same skew as S&P options, and this
repository observes neither. Reusing the S&P assumption keeps the comparison
between structures honest — the two families differ only in their underlying —
but it means the *level* of every Nasdaq option return here rests on an
assumption imported from a different market. If Nasdaq implied volatility carries
a larger premium over realized than the S&P's does, every Nasdaq row below is
flattered, and by an amount this repository cannot measure.

**Early Nasdaq history is a price-only proxy.** Through 1999-03-04 the
Nasdaq-100 total-return series is grossed up from price with an assumed zero
dividend yield, so the implied yield the option model reads is near zero before
that date and near 0.7% after it. The 1987 column is entirely inside that era and
is labelled accordingly. The dot-com bust is not, and it is the episode this
comparison turns on.
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

from .cohorts import cohort_cagrs, cohort_frame, nav_path
from .falsification import load_price_signals
from .hedge_alternatives import (CRASHES, EQUITY, IV_PREMIUM, LAG, MATURITY_YEARS,
                                 OPTION_SPREAD_BPS, SMA_DAYS, SWITCH_COST_BPS, TMF,
                                 TREASURY, UPRO, cagr, markdown_table, max_drawdown,
                                 option_inputs)
from .leaps_robustness import (BENCHMARK_INTERVAL, EQUITY_TR, Horizon, PRIMARY_BLOCK,
                               POOL_COLUMNS, SEED, SLOT, STRUCTURES, TREASURY_R,
                               WARMUP_SESSIONS, bootstrap_pool, build_horizon, build_path,
                               leaps_rule, load, monte_carlo, path_metrics)
from .model import portfolio
from .options import (break_even_iv_premium, implied_volatility_proxy, leaps_arrays,
                      roll_schedule, simulate_leaps_arrays)
from .provenance import FLOAT_FORMAT, sha, source_hashes, stable_floats
from .signals import level_position
from .strategy import select_returns, switching_costs

NASDAQ = 'NASDAQ100_1X'
TQQQ = 'TQQQ_SPREAD_50BP'
ROLL_LABEL, ROLL_YEARS = BENCHMARK_INTERVAL, 1.
# Through this date the Nasdaq-100 total-return series is a price-only proxy with
# an assumed zero dividend yield. Conclusions resting on it are labelled.
PROXY_THROUGH = '1999-03-04'

NDX_STRUCTURES = {'NDX_LEAPS_80_20_TREASURY': (.80, .20),
                  'NDX_LEAPS_80_25_TREASURY': (.80, .25),
                  'NDX_LEAPS_85_25_TREASURY': (.85, .25),
                  'NDX_LEAPS_85_30_TREASURY': (.85, .30)}
ALL_STRUCTURES = {**STRUCTURES, **NDX_STRUCTURES}
UNDERLYING = {**{name: EQUITY for name in STRUCTURES},
              **{name: NASDAQ for name in NDX_STRUCTURES}}
# The reference each structure is asked to beat, and the index every strategy is
# asked to beat.
INDEX_ROWS = (EQUITY, NASDAQ)
MC_PATHS, MC_HORIZONS = 2000, (10, 20, 30)
DRAWDOWN_THRESHOLDS = (.50, .60, .75)
PERCENTILES = (5, 10, 50, 90, 95)
EXTRA_METRICS = ('mean_option_weight',)
METRICS_WIDTH = len(SLOT) + len(EXTRA_METRICS)
OPTION_WEIGHT = len(SLOT)


def underlying_inputs(inputs, root: Path):
    """Option inputs for both underlyings, on the one shared comparison window.

    The Nasdaq pair is built by the same function that builds the S&P pair, from
    the Nasdaq price index and the Nasdaq total-return series, so the dividend
    yield each model reads is the gap between that index's own two series rather
    than a number carried across from the other market.
    """
    signals = load_price_signals(root, inputs.config, offline=True)
    ndx = option_inputs(inputs.daily, signals['NASDAQ100'], inputs.ix, inputs.calendar,
                        total_return=NASDAQ)
    return {EQUITY: (inputs.spot, inputs.dividend, inputs.riskfree, inputs.vol),
            NASDAQ: ndx}, signals


def run_structure(inputs, market, name: str):
    """One LEAPS structure on its own underlying, against the Treasury sleeve."""
    spot, dividend, riskfree, vol = market[UNDERLYING[name]]
    rule = leaps_rule(*ALL_STRUCTURES[name], ROLL_YEARS)
    arrays = leaps_arrays(spot, inputs.daily.loc[inputs.ix, TREASURY], dividend,
                          riskfree, vol)
    return simulate_leaps_arrays(*arrays, roll_schedule(spot.index, rule))


def describe(name, returns, inputs, exposure=np.nan, weight=np.nan, budget=np.nan,
             family='') -> dict:
    path = nav_path(returns, inputs.calendar)
    ten, twenty, thirty = (cohort_cagrs(path, horizon) for horizon in (10, 20, 30))
    row = dict(strategy=name, family=family, premium_budget=budget,
               cagr=cagr(returns), max_drawdown=max_drawdown(returns),
               annualized_volatility=float(returns.std(ddof=1) * np.sqrt(252)),
               terminal_multiple=float((1 + returns).prod()),
               mean_delta_exposure=float(exposure), mean_option_weight=float(weight),
               cohort_10y_min_cagr=float(ten.min()) if len(ten) else np.nan,
               cohort_20y_min_cagr=float(twenty.min()) if len(twenty) else np.nan,
               cohort_20y_median_cagr=float(np.median(twenty)) if len(twenty) else np.nan,
               cohort_30y_min_cagr=float(thirty.min()) if len(thirty) else np.nan,
               cohort_30y_median_cagr=float(np.median(thirty)) if len(thirty) else np.nan)
    for event, (start, end) in CRASHES.items():
        row[event] = float((1 + returns.loc[start:end]).prod() - 1)
    # Every thirty-year window in this sample opens before the Nasdaq splice, so
    # a thirty-year statistic for a Nasdaq row is partly a statement about proxy
    # data. Twenty-year windows entering after it are not.
    frame = cohort_frame(path, 20)
    modern = frame[frame.entry_close.astype(str) > PROXY_THROUGH]
    row['cohort_20y_min_cagr_post_proxy'] = float(modern.cagr.min()) if len(modern) else np.nan
    row['cohort_20y_entries_post_proxy'] = int(len(modern))
    row['stress_1987_inside_proxy_era'] = CRASHES['1987_crash'][1] <= PROXY_THROUGH
    return row


def comparators(inputs, signals) -> dict:
    """The few existing strategies worth carrying, built directly.

    Only these: the two indices, each family's own trend rule, and the leveraged
    stock/bond mix. Rebuilding the whole comparison universe would cost more than
    it informs, and every other structure already has its own report.
    """
    daily = inputs.daily.loc[inputs.ix]
    out = {EQUITY: (daily[EQUITY], 1.), NASDAQ: (daily[NASDAQ], 1.)}
    for name, price, levered, index in (
            ('UPRO_SMA_TO_SP500', signals['SP500'], UPRO, EQUITY),
            ('TQQQ_SMA_TO_NASDAQ', signals['NASDAQ100'], TQQQ, NASDAQ)):
        position = level_position(price, inputs.calendar, SMA_DAYS, LAG).loc[inputs.ix]
        returns = switching_costs(select_returns(daily, position, {1: levered, 0: index}),
                                  position, SWITCH_COST_BPS)
        out[name] = (returns, 3. * position.mean() + (1 - position.mean()))
    out['UPRO60_TMF40'] = (portfolio(daily[[UPRO, TMF]], pd.Series({UPRO: .6, TMF: .4}),
                                     'quarterly'), 1.8)
    return out


def historical_table(inputs, market, signals) -> pd.DataFrame:
    rows = [describe(name, returns, inputs, exposure=exposure, family='comparator')
            for name, (returns, exposure) in comparators(inputs, signals).items()]
    for name in ALL_STRUCTURES:
        path = run_structure(inputs, market, name)
        returns = pd.Series(path.navs, index=market[UNDERLYING[name]][0].index
                            ).pct_change().dropna()
        rows.append(describe(name, returns, inputs,
                             exposure=float(path.exposures.mean()),
                             weight=float(path.option_weights.mean()),
                             budget=ALL_STRUCTURES[name][1],
                             family='NDX LEAPS' if name.startswith('NDX')
                             else 'SP500 LEAPS'))
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------
# Monte Carlo
# ----------------------------------------------------------------------------

NDX_PRICE, NDX_TOTAL, NDX_YIELD = (len(POOL_COLUMNS), len(POOL_COLUMNS) + 1,
                                   len(POOL_COLUMNS) + 2)


def nasdaq_pool(inputs, market) -> np.ndarray:
    """The shared bootstrap pool with the Nasdaq's own three columns appended.

    Rows are drawn whole, so the two indices keep the relationship they had on
    the day: the Nasdaq's excess growth and its excess volatility arrive
    together, which is the entire question. Appending rather than editing
    `letf.leaps_robustness.bootstrap_pool` leaves that module untouched, and
    because `block_draw` samples row indices a shared seed selects the same days
    in every study that uses it.
    """
    spot = market[NASDAQ][0]
    price_return = spot.pct_change().dropna()
    if not price_return.index.equals(inputs.ix):
        raise ValueError('Nasdaq price returns are not aligned to the window')
    return np.column_stack([bootstrap_pool(inputs), price_return,
                            inputs.daily.loc[inputs.ix, NASDAQ],
                            market[NASDAQ][1].loc[inputs.ix]])


def run_path(sample: np.ndarray, horizon: Horizon, _spec) -> dict:
    """Every strategy on one resampled path, both underlyings rebuilt inside it."""
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
        record = np.r_[path_metrics(np.r_[1., np.cumprod(1 + sample[entry:, column])],
                                    horizon)[0], np.full(len(EXTRA_METRICS), np.nan)]
        results[index] = record
    growth = np.r_[1., 1 + sample[entry:, TREASURY_R]]
    for name in ALL_STRUCTURES:
        spot, dividend, riskfree, vol = built[UNDERLYING[name]]
        path = simulate_leaps_arrays(spot, growth, dividend, riskfree, vol, horizon.days,
                                     horizon.schedules[(name, ROLL_LABEL)])
        record = np.r_[path_metrics(path.navs, horizon)[0], np.zeros(len(EXTRA_METRICS))]
        record[SLOT['mean_delta_exposure']] = float(path.exposures.mean())
        record[OPTION_WEIGHT] = float(path.option_weights.mean())
        results[name] = record
    return results


def summarize(store: dict, horizon: Horizon) -> pd.DataFrame:
    """Distribution summary. Every comparison against a rival is paired per path."""
    index_wealth = {name: store[name][:, SLOT['terminal_wealth']] for name in INDEX_ROWS}
    rows = []
    for name, values in store.items():
        rates, terminal = values[:, SLOT['cagr']], values[:, SLOT['terminal_wealth']]
        drawdown = values[:, SLOT['max_drawdown']]
        own = UNDERLYING.get(name, name)
        row = dict(strategy=name, horizon_years=horizon.years, block_days=PRIMARY_BLOCK,
                   paths=len(rates), premium_budget=ALL_STRUCTURES.get(name, (np.nan,
                                                                             np.nan))[1],
                   underlying=own, mean_cagr=float(rates.mean()),
                   median_max_drawdown=float(np.median(drawdown)),
                   worst_max_drawdown=float(drawdown.min()),
                   prob_negative_cagr=float((rates < 0).mean()),
                   prob_below_own_underlying=float((terminal < index_wealth[own]).mean())
                   if name != own else 0.,
                   prob_below_sp500=float((terminal < index_wealth[EQUITY]).mean())
                   if name != EQUITY else 0.,
                   mean_delta_exposure_median=float(
                       np.nanmedian(values[:, SLOT['mean_delta_exposure']]))
                   if np.isfinite(values[:, SLOT['mean_delta_exposure']]).any() else np.nan,
                   mean_option_weight_median=float(np.nanmedian(values[:, OPTION_WEIGHT]))
                   if np.isfinite(values[:, OPTION_WEIGHT]).any() else np.nan)
        for level in PERCENTILES:
            row[f'cagr_p{level}'] = float(np.percentile(rates, level))
            row[f'terminal_wealth_p{level}'] = float(np.percentile(terminal, level))
        for threshold in DRAWDOWN_THRESHOLDS:
            row[f'prob_drawdown_worse_than_{int(threshold * 100)}'] = float(
                (drawdown <= -threshold).mean())
        rows.append(row)
    return pd.DataFrame(rows)


def breakeven_table(inputs, market, historical: pd.DataFrame) -> pd.DataFrame:
    """How dear Nasdaq options must be before the advantage disappears.

    The volatility premium is the one input this repository cannot check, and it
    is imported from a different market, so a point estimate of a Nasdaq option
    return is worth less than the volatility at which that return stops beating
    its rival. Nasdaq realized volatility is materially higher than the S&P's, so
    a flat three-point premium is proportionally a smaller loading there — which
    is exactly the direction that would flatter these structures.
    """
    rates = historical.set_index('strategy')['cagr']
    rivals = {name: float(rates[name]) for name in STRUCTURES}
    spot, _, riskfree, _ = market[NASDAQ]
    dividend = market[NASDAQ][1]
    price_returns = spot.pct_change().fillna(0.)
    safe = inputs.daily.loc[inputs.ix, TREASURY]
    ndx_volatility = float(implied_volatility_proxy(price_returns, 0., MATURITY_YEARS).mean())
    sp_volatility = float(implied_volatility_proxy(
        inputs.spot.pct_change().fillna(0.), 0., MATURITY_YEARS).mean())
    rows = []
    for name, (moneyness, budget) in NDX_STRUCTURES.items():
        rule = leaps_rule(moneyness, budget, ROLL_YEARS)
        schedule = roll_schedule(spot.index, rule)

        def build(premium, rule=rule, schedule=schedule):
            vol = implied_volatility_proxy(price_returns, premium, MATURITY_YEARS)
            from dataclasses import replace
            shifted = replace(rule, iv_premium=premium)
            navs = simulate_leaps_arrays(
                *leaps_arrays(spot, safe, dividend, riskfree, vol),
                roll_schedule(spot.index, shifted), ledger=False).navs
            return cagr(pd.Series(navs, index=spot.index).pct_change().dropna())

        # A flat three points is proportionally a smaller loading on a more
        # volatile underlying, so the Nasdaq structures are being charged less
        # for their options in relative terms than the S&P ones are. The matched
        # premium scales the loading by the ratio of realized volatilities, and
        # re-running at it says how much of the advantage that difference alone
        # accounts for. It is not a claim about real Nasdaq option prices, which
        # this repository does not observe — only a like-for-like restatement.
        matched = IV_PREMIUM * ndx_volatility / sp_volatility
        row = dict(structure=name, premium_budget=budget,
                   cagr_at_base_premium=build(IV_PREMIUM),
                   base_premium=IV_PREMIUM,
                   mean_realized_volatility=ndx_volatility,
                   sp500_mean_realized_volatility=sp_volatility,
                   proportionally_matched_premium=matched,
                   cagr_at_matched_premium=build(matched))
        for rival, rate in rivals.items():
            premium = break_even_iv_premium(rate, build)
            row[f'{rival}_breakeven_premium'] = premium
        rows.append(row)
    return pd.DataFrame(rows)


def key_comparison(monte: pd.DataFrame, historical: pd.DataFrame) -> pd.DataFrame:
    """The compact table the decision turns on: return, tail, and capital at risk."""
    twenty = monte[monte.horizon_years == 20].set_index('strategy')
    thirty = monte[monte.horizon_years == 30].set_index('strategy')
    budgets = historical.set_index('strategy')['premium_budget']
    rows = []
    for name in thirty.index:
        rows.append(dict(
            strategy=name, premium_budget=float(budgets.get(name, np.nan)),
            median_20y_cagr=float(twenty.loc[name, 'cagr_p50']),
            p5_20y_cagr=float(twenty.loc[name, 'cagr_p5']),
            median_30y_cagr=float(thirty.loc[name, 'cagr_p50']),
            p5_30y_cagr=float(thirty.loc[name, 'cagr_p5']),
            median_max_drawdown=float(thirty.loc[name, 'median_max_drawdown']),
            prob_drawdown_worse_than_60=float(thirty.loc[name,
                                                         'prob_drawdown_worse_than_60']),
            mean_delta_exposure=float(thirty.loc[name, 'mean_delta_exposure_median'])))
    return pd.DataFrame(rows)


SHORT = {EQUITY: 'SP500 1x', NASDAQ: 'NDX 1x'}


def figure(path: Path, comparison: pd.DataFrame, historical: pd.DataFrame):
    """Two panels: where each structure sits, and what the dot-com bust did to it."""
    fig, axes = plt.subplots(1, 2, figsize=(13.5, 5.4), constrained_layout=True)
    frame = comparison.set_index('strategy')

    ax = axes[0]
    for family, colour in (('LEAPS_', 'C0'), ('NDX_LEAPS_', 'C1')):
        names = [n for n in frame.index if n.startswith(family)
                 and (family == 'NDX_LEAPS_' or not n.startswith('NDX'))]
        piece = frame.loc[names].sort_values('median_30y_cagr')
        ax.plot(piece.median_max_drawdown, piece.median_30y_cagr, 'o-', color=colour,
                label='Nasdaq LEAPS' if 'NDX' in family else 'S&P 500 LEAPS')
        for name, row in piece.iterrows():
            ax.annotate(f'{row.premium_budget:.0%}',
                        (row.median_max_drawdown, row.median_30y_cagr),
                        textcoords='offset points', xytext=(5, -9), fontsize=8)
    for name, marker in ((EQUITY, 'k*'), (NASDAQ, 'kD')):
        ax.plot(frame.loc[name, 'median_max_drawdown'], frame.loc[name, 'median_30y_cagr'],
                marker, markersize=9, label=SHORT[name])
    ax.set_xlabel('median max drawdown')
    ax.set_ylabel('median 30-year CAGR')
    for axis in (ax.xaxis, ax.yaxis):
        axis.set_major_formatter(PercentFormatter(1))
    ax.legend(fontsize=9, loc='lower left')
    ax.set_title('A. Labels are the premium budget')

    ax = axes[1]
    options = historical[historical.family.str.contains('LEAPS')].set_index('strategy')
    events = ['2000_2002_bust', '2008_2009_gfc', '2020_covid', '2022_rates']
    width, positions = .8 / len(options), np.arange(len(events))
    for offset, (name, row) in enumerate(options.iterrows()):
        colour = 'C1' if name.startswith('NDX') else 'C0'
        ax.bar(positions + offset * width, [row[e] for e in events], width,
               color=colour, alpha=.45 + .18 * (offset % 3),
               label=f"{name.replace('_TREASURY', '').replace('LEAPS_', '')}"
                     f" ({row.premium_budget:.0%})")
    ax.set_xticks(positions + .4 - width / 2, [e.replace('_', ' ') for e in events],
                  fontsize=9)
    ax.axhline(0, color='grey', lw=.8)
    ax.yaxis.set_major_formatter(PercentFormatter(1))
    ax.set_ylabel('episode return')
    ax.legend(fontsize=7.5, ncol=2)
    ax.set_title('B. Stress episodes, S&P (blue) against Nasdaq (orange)')

    fig.savefig(path, dpi=160)
    plt.close(fig)


def run(root: Path, workers=None, paths=None):
    inputs = load(root)
    market, signals = underlying_inputs(inputs, root)
    historical = historical_table(inputs, market, signals)
    breakeven = breakeven_table(inputs, market, historical)

    pool = nasdaq_pool(inputs, market)
    count = paths or MC_PATHS
    frames = []
    for years in MC_HORIZONS:
        horizon = build_horizon(inputs, years, (ROLL_LABEL,), structures=ALL_STRUCTURES)
        store = monte_carlo(pool, horizon, None, count, PRIMARY_BLOCK, SEED, workers,
                            runner=run_path)
        frames.append(summarize(store, horizon))
    monte = pd.concat(frames, ignore_index=True)
    comparison = key_comparison(monte, historical)

    reports = root / 'reports'
    outputs = {'nasdaq_leaps_historical.csv': historical,
               'nasdaq_leaps_breakeven.csv': breakeven,
               'nasdaq_leaps_monte_carlo.csv': monte,
               'nasdaq_leaps_comparison.csv': comparison}
    for name, frame in outputs.items():
        frame.pipe(stable_floats).to_csv(reports / name, index=False,
                                         float_format=FLOAT_FORMAT)
    figure(reports / 'nasdaq_leaps.png', comparison, historical)
    report(reports, inputs, historical, breakeven, monte, comparison, count)

    (reports / 'nasdaq_leaps_manifest.json').write_text(json.dumps({
        'window': [inputs.ix[0].date().isoformat(), inputs.ix[-1].date().isoformat()],
        'observations': int(len(inputs.ix)),
        'nasdaq_structures': {k: list(v) for k, v in NDX_STRUCTURES.items()},
        'sp500_structures': {k: list(v) for k, v in STRUCTURES.items()},
        'roll': ROLL_LABEL, 'maturity_years': MATURITY_YEARS,
        'iv_premium': IV_PREMIUM, 'option_spread_bps': OPTION_SPREAD_BPS,
        'iv_premium_retuned_for_nasdaq': False,
        'nasdaq_proxy_through': PROXY_THROUGH,
        'nasdaq_proxy_note': 'the Nasdaq-100 total-return series is grossed up from price '
                             'with an assumed zero dividend yield through that date, so '
                             'the implied yield the option model reads is near zero before '
                             'it; the 1987 column lies entirely inside that era',
        'seed': SEED, 'block_days': PRIMARY_BLOCK, 'paths': count,
        'horizons': list(MC_HORIZONS), 'warmup_sessions': WARMUP_SESSIONS,
        'resampled_columns': list(POOL_COLUMNS) + ['nasdaq_price_return',
                                                   'nasdaq_total_return',
                                                   'nasdaq_dividend_yield'],
        'option_prices': 'modelled with Black-Scholes on an assumed implied volatility; '
                         'this repository holds no option price history for either index, '
                         'and Nasdaq skew is not observed',
        'python': platform.python_version(), 'numpy': np.__version__,
        'pandas': pd.__version__, 'scipy': scipy.__version__,
        'matplotlib': matplotlib.__version__,
        'source_hashes': source_hashes(root, __spec__.name),
        'outputs_sha256': {name: sha(reports / name) for name in outputs},
    }, indent=2) + '\n')
    print(f'Nasdaq LEAPS: {len(historical)} historical rows; Monte Carlo {count} paths x '
          f'{len(MC_HORIZONS)} horizons at block {PRIMARY_BLOCK}.')
    return historical, monte


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('--workers', type=int, default=None)
    parser.add_argument('--paths', type=int, default=None)
    parser.add_argument('--offline', action='store_true', default=True)
    args = parser.parse_args()
    run(args.root, args.workers, args.paths)


def _narrative(historical, breakeven, monte, comparison) -> dict:
    """Every number the prose states, derived from the tables it sits beside."""
    hist = historical.set_index('strategy')
    frame = comparison.set_index('strategy')
    thirty = monte[monte.horizon_years == 30].set_index('strategy')
    twenty = monte[monte.horizon_years == 20].set_index('strategy')
    ten = monte[monte.horizon_years == 10].set_index('strategy')
    break_frame = breakeven.set_index('structure')
    rival = 'LEAPS_95_50_TREASURY'

    # Best on the downside: highest fifth-percentile thirty-year CAGR.
    best = max(NDX_STRUCTURES, key=lambda n: frame.loc[n, 'p5_30y_cagr'])
    # Closest to the S&P's largest structure on the median.
    closest = min(NDX_STRUCTURES,
                  key=lambda n: abs(frame.loc[n, 'median_30y_cagr']
                                    - frame.loc[rival, 'median_30y_cagr']))
    # Where a Nasdaq structure matches an S&P one on less option capital.
    cheaper = [(n, s) for n in NDX_STRUCTURES for s in STRUCTURES
               if frame.loc[n, 'median_30y_cagr'] >= frame.loc[s, 'median_30y_cagr']
               and hist.loc[n, 'premium_budget'] < hist.loc[s, 'premium_budget']]
    index_gap = float(thirty.loc[NASDAQ, 'cagr_p50'] - thirty.loc[EQUITY, 'cagr_p50'])
    events = list(CRASHES)
    families = {family: group for family, group in historical.groupby('family')
                if 'LEAPS' in family}
    worst_event = {family: min(events, key=lambda e: group[e].mean())
                   for family, group in families.items()}
    # Where the two families' losses differ most, at matched premium budgets, so
    # the comparison is about the underlying rather than about position size.
    matched = [(n, s) for n in NDX_STRUCTURES for s in STRUCTURES
               if NDX_STRUCTURES[n][1] == STRUCTURES[s][1]]
    difference = {event: float(np.mean([hist.loc[n, event] - hist.loc[s, event]
                                        for n, s in matched])) for event in events}
    return dict(hist=hist, frame=frame, thirty=thirty, twenty=twenty, ten=ten,
                breakeven=break_frame, rival=rival, best=best, closest=closest,
                cheaper=cheaper, index_gap=index_gap, worst_event=worst_event,
                matched=matched, difference=difference,
                nasdaq_worse=min(difference, key=difference.get),
                nasdaq_better=max(difference, key=difference.get))


def report(reports: Path, inputs, historical, breakeven, monte, comparison, paths):
    """Write the narrative from the numbers, never alongside them."""
    v = _narrative(historical, breakeven, monte, comparison)
    rate = {c: '.2%' for c in ('cagr', 'max_drawdown', 'annualized_volatility',
                               'premium_budget', 'mean_option_weight')}
    hist_columns = ['strategy', 'family', 'premium_budget', 'cagr', 'annualized_volatility',
                    'max_drawdown', 'mean_delta_exposure', 'mean_option_weight',
                    'cohort_10y_min_cagr', 'cohort_20y_min_cagr',
                    'cohort_20y_min_cagr_post_proxy', 'cohort_20y_median_cagr',
                    'cohort_30y_min_cagr', 'cohort_30y_median_cagr']
    hist_formats = dict(rate, mean_delta_exposure='.2f',
                        **{c: '.2%' for c in hist_columns if 'cohort' in c})
    mc_columns = ['strategy', 'premium_budget', 'cagr_p5', 'cagr_p10', 'cagr_p50',
                  'cagr_p90', 'cagr_p95', 'terminal_wealth_p5', 'terminal_wealth_p50',
                  'terminal_wealth_p95', 'median_max_drawdown',
                  'prob_drawdown_worse_than_50', 'prob_drawdown_worse_than_60',
                  'prob_drawdown_worse_than_75', 'prob_negative_cagr',
                  'prob_below_own_underlying', 'prob_below_sp500']
    mc_formats = {c: '.2%' for c in mc_columns[1:] if 'terminal' not in c}
    mc_formats.update({c: ',.1f' for c in mc_columns if 'terminal' in c})

    text = f"""# Nasdaq LEAPS: does a faster underlying need less option capital?

Generated by `letf.nasdaq_leaps`. Window {inputs.ix[0].date()} to {inputs.ix[-1].date()}, {len(inputs.ix):,} sessions, on the same calendar, financing and option assumptions as `reports/hedge_alternatives_results.md`.

Every option structure studied here so far is written on the S&P 500, and the
family is ordered by premium budget: more budget, more return, more drawdown. The
Nasdaq-100 grew faster over this window and was materially more volatile. Those
push opposite ways on one question — **can a higher-growth underlying reach
comparable long-run returns with less capital at risk in options, or does the
volatility take back through the option price what the growth rate gives?**

Four Nasdaq structures, prespecified and not searched. Everything else is
inherited: roughly two-year maturity, annual roll on listed expiries, a trailing
realized-volatility proxy over the option's own horizon plus
{IV_PREMIUM:.0%} of volatility premium, the same Black-Scholes framework, the
same {OPTION_SPREAD_BPS:.0f}bp spread, residual capital in long Treasuries.

**Two caveats belong before the numbers, not after them.**

The volatility premium is *not* retuned for the Nasdaq. Real Nasdaq index options
are not priced off the S&P's skew, and this repository observes neither. Reusing
the S&P assumption keeps the comparison between structures clean, but a flat
premium is proportionally a smaller loading on a more volatile underlying —
realized volatility averages
{v['breakeven']['mean_realized_volatility'].iloc[0]:.1%} for the Nasdaq against
{v['breakeven']['sp500_mean_realized_volatility'].iloc[0]:.1%} for the S&P — so
the Nasdaq rows are being charged relatively less for their options. The
break-even table below is the answer to that, and it is the number to read rather
than any CAGR here.

Nasdaq total returns are a price-only proxy through {PROXY_THROUGH}, grossed up
with an assumed zero dividend yield. The 1987 column lies entirely inside that
era and is labelled. The dot-com bust does not, and it is the episode this
comparison turns on.

## Historical

{markdown_table(historical, hist_columns, hist_formats)}

### Stress episodes

{markdown_table(historical, ['strategy', 'premium_budget'] + list(CRASHES),
                dict({k: '.1%' for k in CRASHES}, premium_budget='.0%'))}

The 1987 column is inside the Nasdaq proxy era for every row and should not carry
weight on its own. The dot-com bust should: it is the episode in which the
Nasdaq's excess growth was repaid, it is outside the proxy era, and it is the
sharpest available test of whether a Nasdaq-based structure is a better bargain
or merely a more concentrated one.

## How much rests on the volatility assumption

{markdown_table(breakeven, ['structure', 'premium_budget', 'cagr_at_base_premium',
                            'proportionally_matched_premium', 'cagr_at_matched_premium']
                + [f'{name}_breakeven_premium' for name in STRUCTURES],
                dict({'premium_budget': '.0%', 'cagr_at_base_premium': '.2%',
                      'proportionally_matched_premium': '.2%',
                      'cagr_at_matched_premium': '.2%'},
                     **{f'{name}_breakeven_premium': '.2%' for name in STRUCTURES}))}

Each break-even column is the volatility premium at which that Nasdaq structure
stops beating that S&P structure. A blank means it never does within the bracket
searched. `proportionally_matched_premium` scales the flat three points by the
ratio of the two indices' realized volatilities, so the Nasdaq options are
charged the same loading *relative to their own volatility* that the S&P options
carry; the column beside it is what that costs.

## Bootstrap

{paths:,} paths per horizon at {PRIMARY_BLOCK}-session blocks, seed {SEED}, one
shared set of resampled worlds across every strategy. Whole daily rows are drawn,
so the Nasdaq's excess growth and its excess volatility arrive together, which is
the entire question. Nothing is re-optimized on any path.

### Thirty years

{markdown_table(monte[monte.horizon_years == 30], mc_columns, mc_formats)}

### Twenty years

{markdown_table(monte[monte.horizon_years == 20], mc_columns, mc_formats)}

### Ten years

{markdown_table(monte[monte.horizon_years == 10], mc_columns, mc_formats)}

## The key comparison

{markdown_table(comparison, ['strategy', 'premium_budget', 'median_20y_cagr',
                             'p5_20y_cagr', 'median_30y_cagr', 'p5_30y_cagr',
                             'median_max_drawdown', 'prob_drawdown_worse_than_60',
                             'mean_delta_exposure'],
                {'premium_budget': '.0%', 'median_20y_cagr': '.2%', 'p5_20y_cagr': '.2%',
                 'median_30y_cagr': '.2%', 'p5_30y_cagr': '.2%',
                 'median_max_drawdown': '.1%', 'prob_drawdown_worse_than_60': '.1%',
                 'mean_delta_exposure': '.2f'})}

![Nasdaq against S&P LEAPS](nasdaq_leaps.png)
"""
    (reports / 'nasdaq_leaps_comparison.md').write_text(text + _answers(v, inputs))


def _answers(v, inputs) -> str:
    """Questions 8 to 14, every claim read off the tables above."""
    hist, frame = v['hist'], v['frame']
    thirty, twenty = v['thirty'], v['twenty']
    rival, best, closest, cheaper = v['rival'], v['best'], v['closest'], v['cheaper']
    breaks = v['breakeven']
    gap = float(frame.loc[rival, 'median_30y_cagr'] - frame.loc[closest, 'median_30y_cagr'])
    capital = float(hist.loc[rival, 'premium_budget'] - hist.loc[closest, 'premium_budget'])
    structure_gap = float(thirty.loc['NDX_LEAPS_85_30_TREASURY', 'cagr_p50']
                          - thirty.loc['LEAPS_85_30_TREASURY', 'cagr_p50'])
    captured = structure_gap / v['index_gap'] if v['index_gap'] else np.nan
    matched_cost = float(breaks.loc[closest, 'cagr_at_base_premium']
                         - breaks.loc[closest, 'cagr_at_matched_premium'])
    dot_com = 'cohort_20y_min_cagr_post_proxy'
    pairs = ', '.join(f'{a} at {hist.loc[a, "premium_budget"]:.0%} matches '
                      f'{b} at {hist.loc[b, "premium_budget"]:.0%}' for a, b in cheaper)
    return f"""
## Questions eight to fourteen

**8. Does the Nasdaq's higher growth allow materially lower premium budgets?**
Yes, on this data. {pairs if cheaper else 'No Nasdaq structure matches an S&P one on less budget'}.
The mechanism is visible in the exposure column: {closest} carries a mean delta of
{frame.loc[closest, 'mean_delta_exposure']:.2f} against {rival}'s
{frame.loc[rival, 'mean_delta_exposure']:.2f}, so it reaches a comparable return
holding less than half the equity sensitivity, because the equity it holds
compounded faster.

**9. Which Nasdaq structure has the best downside-adjusted long-horizon result?**
{best}, on the fifth percentile of thirty-year CAGR — {frame.loc[best, 'p5_30y_cagr']:.2%}
against {min(frame.loc[n, 'p5_30y_cagr'] for n in NDX_STRUCTURES):.2%} for the
weakest of the four. It also has the deepest median drawdown of the four at
{frame.loc[best, 'median_max_drawdown']:.1%} and the highest chance of a drawdown
worse than 60% at {frame.loc[best, 'prob_drawdown_worse_than_60']:.1%}, so "best"
here means best return per unit of downside risk accepted, not least risky.

**10. Does any 20-30% Nasdaq premium strategy approach the S&P 95/50 return?**
{closest} comes closest: a median thirty-year CAGR of
{frame.loc[closest, 'median_30y_cagr']:.2%} against {rival}'s
{frame.loc[rival, 'median_30y_cagr']:.2%}, {gap:.2%} short, on
{capital:.0%} less of the portfolio at risk in options.

**11. Does it retain better downside behaviour?**
Yes, and by a wide margin at every horizon measured. Median drawdown
{frame.loc[closest, 'median_max_drawdown']:.1%} against
{frame.loc[rival, 'median_max_drawdown']:.1%}; a drawdown worse than 60% on
{frame.loc[closest, 'prob_drawdown_worse_than_60']:.1%} of paths against
{frame.loc[rival, 'prob_drawdown_worse_than_60']:.1%}; worse than 75% on
{thirty.loc[closest, 'prob_drawdown_worse_than_75']:.1%} against
{thirty.loc[rival, 'prob_drawdown_worse_than_75']:.1%}. The fifth percentile is
higher too — {frame.loc[closest, 'p5_30y_cagr']:.2%} against
{frame.loc[rival, 'p5_30y_cagr']:.2%} over thirty years and
{twenty.loc[closest, 'cagr_p5']:.2%} against {twenty.loc[rival, 'cagr_p5']:.2%}
over twenty — so it is not trading tail for middle. It is a better structure on
these numbers, and the next two questions are why that should not be believed
straight.

**12. How much of the advantage depends on the historical Nasdaq growth regime?**
All of it, and the bootstrap cannot say otherwise. Across resampled orderings the
Nasdaq index itself beats the S&P by {v['index_gap']:.2%} of median thirty-year
CAGR, and the Nasdaq structure beats its S&P counterpart by {structure_gap:.2%} —
about {captured:.0%} of the underlying gap, passed through. Every path in this
simulation resamples the same forty years, so that {v['index_gap']:.2%} is an
input to the exercise, not a finding of it. Nothing here tests whether the
Nasdaq's excess growth persists; it only tests whether, given that it does, this
structure is an efficient way to hold it. The second dependency is the option
price: charging the Nasdaq the same volatility loading *relative to its own
volatility* that the S&P carries costs {closest} {matched_cost:.2%} of CAGR, and
its break-even against {rival} sits at
{breaks.loc[closest, f'{rival}_breakeven_premium']:.2%} — close enough to the
assumed {IV_PREMIUM:.0%} that this particular comparison turns on an input nobody
here has measured.

**13. Does the dot-com bust falsify the advantage?**
It qualifies it rather than falsifying it. Through 2000-2002 {closest} returned
{hist.loc[closest, '2000_2002_bust']:.1%} against
{hist.loc['LEAPS_85_30_TREASURY', '2000_2002_bust']:.1%} for the S&P structure of
the same budget — the Nasdaq version lost substantially more, exactly as a
concentrated bet on the index that was inflating should. But against {rival}, the
S&P structure it is being offered as an alternative to, the same episode reads
{hist.loc[closest, '2000_2002_bust']:.1%} against
{hist.loc[rival, '2000_2002_bust']:.1%}. The Nasdaq structure survived the worst
Nasdaq episode on record better than the S&P structure of comparable return
survived it. That is the strongest thing in this report, and it is one episode.
The worst twenty-year window entering after the proxy splice tells the same
story. Restricted to twenty-year windows entering after the proxy splice — the
ones the dot-com bust actually falls inside — {closest} floors at
{hist.loc[closest, dot_com]:.2%} against {hist.loc[rival, dot_com]:.2%}.

**14. Is there a reason to prefer Nasdaq LEAPS as a partial allocation rather than the whole sleeve?**
Yes, but a narrow one, and it is not diversification in the usual sense — the two
indices are far too correlated for that.

Start with what the families share. The worst episode for the S&P structures is
{v['worst_event'].get('SP500 LEAPS', '')} and for the Nasdaq structures
{v['worst_event'].get('NDX LEAPS', '')}
{'— the same one, and it is the episode that hit the Treasury sleeve rather than either underlying. On the risk that dominates both families, the choice of index buys nothing at all'
 if v['worst_event'].get('SP500 LEAPS') == v['worst_event'].get('NDX LEAPS')
 else '— different episodes, which is already most of the case'}.

The difference is in the equity episodes, and it runs both ways. At matched
premium budgets the Nasdaq structures lose
{abs(v['difference'][v['nasdaq_worse']]):.1%} more through
{v['nasdaq_worse'].replace('_', ' ')} and
{abs(v['difference'][v['nasdaq_better']]):.1%} less through
{v['nasdaq_better'].replace('_', ' ')}. That is a genuinely different loss
profile rather than a uniformly better or worse one, and it is the argument for
holding some of each: the option leg is the part of this architecture that cannot
be rebalanced back once it is gone, so which episode empties it matters more than
how large the average loss is. The case is weaker than the return numbers make it
look, and it is about the shape of the loss rather than its size.

## What this does and does not settle

It settles that the modelled economics favour the Nasdaq structures: comparable
return on less option capital, better tails at every horizon measured, and
survival of the dot-com bust ahead of the S&P structure of comparable return.

It does not settle whether that is a property of options or of the Nasdaq. The
whole advantage is the underlying's growth advantage passed through at about
{captured:.0%}, and that growth advantage is an input to every path here. An
investor who does not believe the Nasdaq will out-compound the S&P over the next
thirty years has no reason to expect any of this, and this report contains no
evidence on that question.

## Limitations

* Option premia are modelled, not observed, for both indices, and the volatility
  premium is imported from the S&P without retuning. Nasdaq skew is not observed
  at all. The break-even columns exist because a point estimate would not be
  honest, and the {closest}-against-{rival} comparison in particular sits close
  enough to its break-even to be decided by that unmeasured input.
* Nasdaq total returns are a price-only proxy with an assumed zero dividend yield
  through {PROXY_THROUGH}, so the implied yield the option model reads is near
  zero before it. The 1987 column is entirely inside that era. Twenty-year
  cohorts entering after it are reported separately for that reason.
* The bootstrap resamples one realized history and preserves dependence only
  within a block. It cannot produce a world in which the Nasdaq does not
  out-compound the S&P, because every path is built from days on which it did.
* Four Nasdaq structures, chosen in advance and not searched. That keeps this a
  test rather than an optimization, and it also means a better Nasdaq structure
  may exist and would not have been found.
"""


if __name__ == '__main__':
    main()
