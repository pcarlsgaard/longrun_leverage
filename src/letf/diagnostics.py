"""Is an advantage a strategy property, or a handful of sessions?

A 40-year CAGR gap is a sum of ~10,000 daily log differences. If a large share
of it comes from a few sessions, the gap describes those sessions, not a
repeatable edge — and the confidence interval an investor should attach to it
is far wider than the point estimate suggests.

This is not hypothetical for this repository. For UPRO SMA(200, LAG2) versus
always-on, October 1987 supplies ~41% of the total 40-year log advantage and
1987-10-19 alone supplies ~39%. Reporting the gap without that share
overstates what the signal is known to do. See `docs/experiment_review.md` M2.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

__all__ = ['log_advantage', 'edge_concentration', 'TOP_DAYS']

TOP_DAYS = (1, 5, 20)


def log_advantage(strategy: pd.Series, benchmark: pd.Series) -> pd.Series:
    """Per-session log return difference. Sums to the total log advantage.

    A terminated sleeve (-100%) has no finite log return; those sessions are
    dropped, which is the right treatment here because the comparison ends at
    termination anyway.
    """
    if not strategy.index.equals(benchmark.index):
        raise ValueError('Strategy and benchmark calendars must match exactly')
    diff = np.log1p(strategy) - np.log1p(benchmark)
    return diff.replace([np.inf, -np.inf], np.nan).dropna()


def edge_concentration(strategy: pd.Series, benchmark: pd.Series,
                       top_days=TOP_DAYS) -> dict:
    """Share of the total log advantage from the largest few sessions/months.

    Shares are signed fractions of the *total* advantage, so they are directly
    comparable to it: 0.39 means those sessions produced 39% of the whole gap.
    When the total advantage is ~0 the shares are undefined and returned NaN,
    because a ratio to zero says nothing.
    """
    diff = log_advantage(strategy, benchmark)
    total = float(diff.sum())
    row = {'sessions': len(diff), 'total_log_advantage': total}
    if not len(diff) or abs(total) < 1e-12:
        row.update({f'top{n}_day_share': np.nan for n in top_days})
        row.update({'best_day': '', 'best_day_share': np.nan,
                    'top_month': '', 'top_month_share': np.nan,
                    'advantage_excluding_top_month': np.nan})
        return row

    ordered = diff.reindex(diff.abs().sort_values(ascending=False).index)
    for n in top_days:
        row[f'top{n}_day_share'] = float(ordered.iloc[:n].sum() / total)
    best = ordered.index[0]
    row['best_day'] = best.date().isoformat()
    row['best_day_share'] = float(ordered.iloc[0] / total)

    monthly = diff.groupby(diff.index.to_period('M')).sum()
    top_month = monthly.abs().idxmax()
    row['top_month'] = str(top_month)
    row['top_month_share'] = float(monthly.loc[top_month] / total)
    row['advantage_excluding_top_month'] = total - float(monthly.loc[top_month])
    return row
