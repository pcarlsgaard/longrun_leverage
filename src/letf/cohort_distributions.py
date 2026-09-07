"""Historical rolling-cohort percentile distributions for canonical price-only signal strategies."""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from .analysis import CASH, FAMILIES, load_inputs, matched
from .cohorts import cohort_quantiles, nav_path
from .falsification import LAGS, level_position, load_price_signals, select_returns, switching_costs

HORIZONS = (10, 20, 30)
PERCENTILES = (0.01, 0.10, 0.25, 0.50, 0.75, 0.90, 0.99)
COSTS = (0, 25)
SMA_DAYS = 200
SPREAD_BPS = 50


def percentile_row(series, family, lag, cost, horizon, returns, calendar):
    values, q = cohort_quantiles(nav_path(returns, calendar), horizon, PERCENTILES)
    row = {
        'series': series,
        'family': family,
        'sma_days': SMA_DAYS,
        'spread_bps': SPREAD_BPS,
        'lag': f'LAG{lag}',
        'switch_cost_bps': cost,
        'horizon_years': horizon,
        'cohort_count': len(values),
        'min': float(values.min()) if len(values) else np.nan,
        'max': float(values.max()) if len(values) else np.nan,
    }
    for p, value in zip((1,10,25,50,75,90,99), q):
        row[f'p{p}'] = float(value)
    return row


def run(root: Path):
    daily, config = load_inputs(root, offline=True)
    calendar = daily.index
    prices = load_price_signals(root, config, offline=True)
    positions = {(u, lag): level_position(prices[u], calendar, SMA_DAYS, lag)
                 for u in set(FAMILIES.values()) for lag in LAGS}
    # Keep the same common price-signal comparison window used by the revision battery.
    ix = matched(pd.concat([
        daily[['SP500_1X','NASDAQ100_1X','LONG_TREASURY_1X',CASH]],
        level_position(prices['SP500'], calendar, 250, 2),
        level_position(prices['NASDAQ100'], calendar, 250, 2),
    ], axis=1)).index

    rows = []
    for lag in LAGS:
        # Underlying 1x baselines are repeated by lag only to simplify comparison tables.
        for under in ('SP500', 'NASDAQ100'):
            r = daily.loc[ix, f'{under}_1X']
            for horizon in HORIZONS:
                rows.append(percentile_row(f'{under}_1X', under, lag, 0, horizon, r, calendar))

        for fund, under in FAMILIES.items():
            p = positions[(under, lag)].loc[ix]
            off_label = 'NASDAQ' if under == 'NASDAQ100' else 'SP500'
            strategy_specs = [
                (f'{fund}_ALWAYS', None),
                (f'{fund}_SMA_TO_{off_label}', f'{under}_1X'),
                (f'{fund}_SMA_TO_TBILL', CASH),
            ]
            for cost in COSTS:
                for name, off in strategy_specs:
                    state = pd.Series(1., index=ix) if off is None else p
                    gross = (daily.loc[ix, f'{fund}_SPREAD_{SPREAD_BPS}BP'] if off is None else
                             select_returns(daily, state, {0: off, 1: f'{fund}_SPREAD_{SPREAD_BPS}BP'}))
                    r = switching_costs(gross, state, cost)
                    for horizon in HORIZONS:
                        rows.append(percentile_row(name, under, lag, cost, horizon, r, calendar))

    out = pd.DataFrame(rows)
    path = root / 'reports' / 'price_signal_cohort_percentiles.csv'
    out.to_csv(path, index=False, float_format='%.12g')
    print(f'Wrote {len(out)} cohort-distribution rows to {path}')
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.cwd())
    args = parser.parse_args()
    run(args.root)


if __name__ == '__main__':
    main()
