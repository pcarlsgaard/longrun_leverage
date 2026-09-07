"""Turning a position series into a return series, and charging it.

One switching-cost convention for the whole repository, stated once here.

**The convention.** A trade costs `bps` on the *turnover* it causes, where
turnover is half the L1 change in portfolio weights — the standard definition,
so a full switch out of one 100%-weighted sleeve and into another is turnover
1.0 and costs `bps` once, not twice.

This resolves what looked like two conventions. `apply_costs` with the
turnover implied by a rotation charges `bps` per state change on the whole
portfolio, because a rotation between two fully-invested sleeves *is* turnover
1.0. `letf.reserve.transition_turnover` charges the same `bps` on a smaller
turnover, because a partial transfer between the risky sleeve and bills moves
less than the whole portfolio. Same rule, different portfolios — not two rules.

A round trip therefore compounds: at 50bp, off-and-back costs
`1 - (1 - .005)**2`, not 50bp.
"""
from __future__ import annotations

import pandas as pd

__all__ = ['transitions', 'rotation_turnover', 'apply_costs', 'switching_costs',
           'select_returns']


def transitions(position):
    """True on each state change. Establishing the first allocation is not one."""
    previous = position.shift(1)
    return position.notna() & previous.notna() & position.ne(previous)


def rotation_turnover(position):
    """Turnover of a rotation between fully-invested sleeves: 1.0 per switch."""
    return transitions(position).astype(float)


def apply_costs(returns, turnover, bps):
    """Charge `bps` on `turnover` inside the daily return."""
    if bps < 0 or bps >= 10000 or not returns.index.equals(turnover.index):
        raise ValueError('Invalid cost or mismatched calendars')
    if returns.isna().any() or turnover.isna().any():
        raise ValueError('Incomplete return/turnover history')
    if (turnover < 0).any():
        raise ValueError('Turnover must be non-negative')
    return (1 + returns) * (1 - bps / 10000 * turnover) - 1


def switching_costs(returns, position, bps):
    """Rotation costing: `bps` per state change on the whole portfolio."""
    if not returns.index.equals(position.index) or position.isna().any():
        raise ValueError('Incomplete return/state history')
    return apply_costs(returns, rotation_turnover(position), bps)


def select_returns(daily, position, columns):
    """Pick each session's sleeve return by allocation state.

    Refuses to enter a sleeve after it has terminated (a -1 daily return marks
    the wipe-out); a terminated sleeve cannot be bought back into.
    """
    legs = daily.loc[position.index, list(columns.values())]
    if position.isna().any() or legs.isna().any().any():
        raise ValueError('Incomplete sleeve/state history')
    if not position.isin(columns).all():
        raise ValueError('Unknown allocation state')
    result = pd.Series(0., index=position.index)
    for state, column in columns.items():
        failed = daily[column].eq(-1).cumsum().shift(1, fill_value=0)
        if ((failed.loc[position.index] > 0) & position.eq(state)).any():
            raise ValueError('Cannot enter a terminated sleeve')
        result = result.where(position.ne(state), legs[column])
    return result
