"""Every timing signal in the repository, in one place.

**Which SMA constructor is canonical.** `level_position` is. It reads an
unadjusted price index — the series an investor actually watches — and is the
convention adopted by the price-only revision. `sma_position` reads a
*total-return* level, which no published index quotes intraday and which
crosses its moving average on different sessions than the price index does.
That difference is not cosmetic: under LAG2 it changes the position held on
1987-10-19 and, through that single session, roughly 2.3pp of the 40-year CAGR
(see `docs/experiment_review.md` M2, M4). `sma_position` is retained only as
the legacy comparator that earlier reports were built on; new work uses
`level_position`.

Every constructor here is shift-only — a signal observed at close *t* can
govern no return ending before *t+lag*. Warm-up is enforced with
`min_periods`, so no position is assigned before the lookback is complete.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .model import matched, wealth

LAGS = {1: 'IMMEDIATE_NEXT_RETURN', 2: 'WAIT_ONE_TRADING_DAY'}

__all__ = ['LAGS', 'level_position', 'sma_position', 'discrete_exposure',
           'volatility_position', 'signal_price_return']


def level_position(levels, calendar, length=200, lag=1):
    """Canonical SMA signal: close of an unadjusted price index vs its own SMA.

    `lag` counts return-end sessions, warm-up included. Equality is risk-off.
    """
    if lag not in LAGS or length < 2:
        raise ValueError('Unsupported lag/lookback')
    levels = matched(levels.loc[calendar[0]:calendar[-1]].to_frame()).iloc[:, 0]
    expected = calendar[(calendar >= levels.index[0]) & (calendar <= levels.index[-1])]
    if not levels.index.equals(expected) or (levels <= 0).any():
        raise ValueError('Signal levels must cover every trading close and be positive')
    mean = levels.rolling(length, min_periods=length).mean()
    return (levels > mean).astype(float).where(mean.notna()).shift(lag).reindex(calendar).rename(None)


def sma_position(underlying, calendar, length=200):
    """LEGACY total-return-level SMA. Superseded by `level_position`.

    Kept so the earlier reports remain reproducible and so the price-versus-
    total-return comparison stays runnable. Do not use it for new results.
    """
    r = matched(underlying.to_frame()).iloc[:, 0]
    expected = calendar[(calendar >= r.index[0]) & (calendar <= r.index[-1])]
    if not r.index.equals(expected):
        raise ValueError('Missing trading session in signal history')
    prior = calendar[calendar.get_loc(r.index[0]) - 1]
    nav = pd.concat([pd.Series([1.], index=[prior]), wealth(r)])
    sma = nav.rolling(length, min_periods=length).mean()
    signal = (nav > sma).astype(float).where(sma.notna())
    return signal.shift(1).reindex(calendar)


def signal_price_return(level):
    """Canonical signal return: unadjusted price-index close-to-close return."""
    return level.pct_change(fill_method=None).rename('signal_price_return')


def discrete_exposure(desired):
    """Round a desired exposure onto the tradeable 1x/2x/3x ladder."""
    return pd.Series(np.select([desired < 1.5, desired < 2.5], [1., 2.], default=3.),
                     index=desired.index).where(desired.notna())


def volatility_position(returns, window=20, lag=1, binary=False, target=.20):
    """Realized-volatility exposure ladder; returns (state, lagged vol).

    `returns` may be total-return or price-index returns — the caller decides
    which convention it is running. Both the state and the volatility estimate
    are lagged, so neither uses the return they govern.
    """
    if lag not in LAGS:
        raise ValueError('Unsupported lag')
    vol = returns.rolling(window, min_periods=window).std(ddof=1) * np.sqrt(252)
    if binary:
        state = pd.Series(np.where(vol < target, 3., 1.), index=vol.index)
    else:
        state = discrete_exposure((target / vol).clip(1, 3))
    return state.where(vol.notna()).shift(lag), vol.shift(lag)
