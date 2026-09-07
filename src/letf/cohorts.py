"""One exact-anniversary cohort implementation for the whole repository.

Every long-horizon result in this repo — worst rolling window, cohort
percentiles, reserve summaries — is built from the same construction, so it
lives here once rather than being re-derived per module.

Conventions, fixed for all callers:

- **Entry closes are observed closes.** A cohort's NAV path is prepended with
  a unit close on the last trading session *before* the first return, so the
  first return is earned inside the first cohort rather than defining its
  entry. `nav_path` does this.
- **Month-end cohorts** are the last observed close in each calendar month.
  Daily-entry windows (used for worst-case minima) take every close.
- **Exits are exact calendar anniversaries**: the first close on or after
  `entry + DateOffset(years=n)`. No fill, no lookahead.
- **Terminated sleeves are excluded.** An entry close of zero cannot be bought,
  so entries are filtered to `nav > 0`. Without this a wiped-out sleeve yields
  NaN cohorts that silently poison quantiles.
- CAGR annualizes on 365.25 calendar days per year over the realized window.

Windows overlap. Cohorts are not independent trials and must not be counted as
such — see `docs/experiment_review.md` M3.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

__all__ = ['nav_path', 'month_end_mask', 'cohort_windows', 'cohort_cagrs',
           'cohort_frame', 'worst_cagr', 'cohort_quantiles']


def nav_path(returns: pd.Series, calendar: pd.DatetimeIndex) -> pd.Series:
    """Wealth path starting at 1.0 on the session before the first return.

    `calendar` must be the full trading calendar, not the window `returns` was
    computed over: the entry close is the session *before* the first return, so
    a calendar that starts at that return has nowhere to put it. Passing the
    restricted window would otherwise index `calendar[-1]` and silently produce
    a path that starts after it ends.
    """
    r = returns.dropna()
    start = calendar.get_loc(r.index[0])
    if start < 1:
        raise ValueError('Calendar must contain a session before the first return')
    return pd.concat([pd.Series([1.0], index=[calendar[start - 1]]), (1 + r).cumprod()])


def month_end_mask(index: pd.DatetimeIndex) -> np.ndarray:
    """True on the last observed close of each calendar month."""
    return ~index.to_period('M').duplicated(keep='last')


def cohort_windows(nav: pd.Series, horizon_years: int, month_end: bool = True):
    """Return (starts, finishes) positional arrays of eligible cohort windows.

    Eligible means: the anniversary close exists, the entry close is investable
    (>0), and — when `month_end` — the entry is a month-end close.
    """
    values = nav.to_numpy()
    ends = nav.index.searchsorted(nav.index + pd.DateOffset(years=horizon_years))
    eligible = (ends < len(nav)) & (values > 0)
    if month_end:
        eligible &= month_end_mask(nav.index)
    starts = np.flatnonzero(eligible)
    return starts, ends[starts]


def cohort_cagrs(nav: pd.Series, horizon_years: int, month_end: bool = True) -> np.ndarray:
    starts, finishes = cohort_windows(nav, horizon_years, month_end)
    if not len(starts):
        return np.array([], dtype=float)
    values = nav.to_numpy()
    elapsed = (nav.index[finishes] - nav.index[starts]).days.to_numpy()
    return (values[finishes] / values[starts]) ** (365.25 / elapsed) - 1


def cohort_frame(nav: pd.Series, horizon_years: int, month_end: bool = True) -> pd.DataFrame:
    """Per-cohort rows: entry close, exit close, terminal multiple, CAGR."""
    starts, finishes = cohort_windows(nav, horizon_years, month_end)
    values = nav.to_numpy()
    entry, exit_ = nav.index[starts], nav.index[finishes]
    multiple = values[finishes] / values[starts]
    elapsed = (exit_ - entry).days.to_numpy()
    return pd.DataFrame({
        'horizon_years': np.full(len(starts), horizon_years),
        'entry_close': [d.date().isoformat() for d in entry],
        'exit_close': [d.date().isoformat() for d in exit_],
        'multiple': multiple,
        'cagr': multiple ** (365.25 / elapsed) - 1 if len(starts) else np.array([], dtype=float),
    })


def worst_cagr(nav: pd.Series, horizon_years: int, month_end: bool = False) -> float:
    """Minimum annualized outcome over the horizon; daily entries by default."""
    values = cohort_cagrs(nav, horizon_years, month_end)
    return float(values.min()) if len(values) else np.nan


def cohort_quantiles(nav: pd.Series, horizon_years: int, percentiles,
                     month_end: bool = True):
    """(values, quantiles) — NaN-filled quantiles when no cohort is eligible."""
    values = cohort_cagrs(nav, horizon_years, month_end)
    q = np.quantile(values, percentiles) if len(values) else np.repeat(np.nan, len(percentiles))
    return values, q
