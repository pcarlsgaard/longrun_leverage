"""Historical rolling-cohort percentile distributions for canonical price-only signal strategies."""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from .analysis import CASH, FAMILIES, load_inputs, matched
from .cohorts import cohort_quantiles, nav_path
from .falsification import LAGS, level_position, load_price_signals, select_returns, switching_costs
from .provenance import FLOAT_FORMAT

HORIZONS = (10, 20, 30)
PERCENTILES = (0.01, 0.10, 0.25, 0.50, 0.75, 0.90, 0.99)
COSTS = (0, 25)
SMA_DAYS = 200
SPREAD_BPS = 50



LABELS = {'SP500_1X': 'SP500 1x', 'NASDAQ100_1X': 'Nasdaq-100 1x',
          'SSO_ALWAYS': 'SSO always', 'SSO_SMA_TO_SP500': 'SSO SMA to SP500',
          'SSO_SMA_TO_TBILL': 'SSO SMA to T-bill',
          'UPRO_ALWAYS': 'UPRO always', 'UPRO_SMA_TO_SP500': 'UPRO SMA to SP500',
          'UPRO_SMA_TO_TBILL': 'UPRO SMA to T-bill',
          'TQQQ_ALWAYS': 'TQQQ always', 'TQQQ_SMA_TO_NASDAQ': 'TQQQ SMA to Nasdaq',
          'TQQQ_SMA_TO_TBILL': 'TQQQ SMA to T-bill',
          'TMF_ALWAYS': 'TMF always', 'TMF_SMA_TO_LONG_TREASURY': 'TMF SMA to long Treasury',
          'TMF_SMA_TO_TBILL': 'TMF SMA to T-bill',
          'LONG_TREASURY_1X': 'Long Treasury 1x'}


def write_summary(root, out):
    """Render the primary view. Previously committed with no generator at all."""
    def p(x): return '-' if pd.isna(x) else f'{100 * x:.1f}%'
    primary = out[(out.lag == 'LAG1') & (out.switch_cost_bps == 0)]
    order = [s for s in LABELS if s in set(primary.series)]
    lines = [
        '# Price-signal cohort outcome distributions', '',
        'Canonical price-only signal convention; strategy wealth uses total returns. The',
        f'primary view below uses the {SMA_DAYS}-day price SMA, {SPREAD_BPS} bp financing spread, LAG1',
        'execution and 0 bp switching cost. Cohorts enter at month-end closes and exit on',
        'exact calendar anniversaries.', '',
        '**These percentiles are not a probability distribution.** The windows overlap',
        'heavily — a 30-year percentile over a 40-year history is built from windows that',
        'share almost all of their data — so they describe what happened once, not what is',
        'likely to happen again. Read them alongside `signal_null_model_results.md`, which',
        'tests whether the timing is distinguishable from chance at all.', '',
    ]
    for horizon in HORIZONS:
        block = primary[primary.horizon_years == horizon].set_index('series')
        if not len(block):
            continue
        count = int(block.cohort_count.max())
        lines += [f'## {horizon}-year CAGR distribution ({count} cohorts)', '',
                  '| Strategy | P1 | P10 | P25 | P50 | P75 | P90 | P99 |',
                  '|---|---:|---:|---:|---:|---:|---:|---:|']
        for series in order:
            r = block.loc[series]
            lines.append(f'| {LABELS[series]} | ' + ' | '.join(
                p(r[f'p{q}']) for q in (1, 10, 25, 50, 75, 90, 99)) + ' |')
        lines.append('')
    lines += [
        '## Interpretation', '',
        'The distribution view shows state-dependent leverage acting mainly on the left',
        'tail: at every horizon the SMA variants lift the low percentiles far more than',
        'they lift the median, which is what a rule that sits out drawdowns should do.',
        'TQQQ is the extreme case and the least trustworthy — its always-on 20-year',
        'distribution is dominated by whether a cohort spans 2000-2002, and its early',
        'history is proxy data.', '',
        'What this view cannot show is whether the improvement is timing or simply lower',
        'average exposure; the matched-exposure controls and the null model address that,',
        'and neither is settled by the percentiles here.', '',
        f'The generated CSV also contains LAG2 and {COSTS[-1]} bp switching-cost versions for the same',
        f'{"/".join(str(h) for h in HORIZONS)}-year horizons.', '',
    ]
    (root / 'reports' / 'price_signal_cohort_distribution_summary.md').write_text('\n'.join(lines) + '\n')


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
    out.to_csv(path, index=False, float_format=FLOAT_FORMAT)
    write_summary(root, out)
    print(f'Wrote {len(out)} cohort-distribution rows to {path}')
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.cwd())
    args = parser.parse_args()
    run(args.root)


if __name__ == '__main__':
    main()
