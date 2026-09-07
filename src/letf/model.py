"""Daily-reset economics. All rates/returns are decimal, never percentages."""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.optimize import brentq


def matched(frame):
    """Trim edge coverage only; reject interior holes instead of skipping returns."""
    if not frame.index.is_unique or not frame.index.is_monotonic_increasing:
        raise ValueError('Calendar must be unique and increasing')
    if frame.empty or frame.isna().all().any():
        raise ValueError('No complete common history')
    start = max(s.first_valid_index() for _, s in frame.items())
    end = min(s.last_valid_index() for _, s in frame.items())
    out = frame.loc[start:end]
    if out.empty or out.isna().any().any():
        raise ValueError('Interior gap in matched calendar')
    return out


def calendar_days(index: pd.DatetimeIndex, prior_date: pd.Timestamp) -> pd.Series:
    dates = pd.Series(index, index=index)
    previous = dates.shift(1)
    previous.iloc[0] = prior_date
    days = (dates - previous).dt.days.astype(float)
    if not index.is_unique or not index.is_monotonic_increasing or (days <= 0).any():
        raise ValueError("Dates must be strictly increasing after prior_date")
    return days


def financing_accrual(index, prior_date, annual_rates):
    """ACT/360; each calendar day uses the previous day's observed DFF.

    Sum daily financing across weekends/holidays; never turn a multi-session
    index return into a single leveraged daily return. DFF is a funding proxy,
    not an assertion about an ETF's actual swap terms.
    """
    days = pd.date_range(pd.Timestamp(prior_date) + pd.Timedelta(days=1), index[-1])
    lagged = annual_rates.reindex(days - pd.Timedelta(days=1), method="ffill")
    if lagged.isna().any():
        raise ValueError("Funding history does not cover the requested period")
    cumulative = pd.Series(np.cumsum(lagged.to_numpy() / 360), index=days)
    cumulative.loc[pd.Timestamp(prior_date)] = 0.0
    closes = cumulative.sort_index().reindex(pd.DatetimeIndex([prior_date]).append(index))
    return closes.diff().iloc[1:].set_axis(index)


def simulate(underlying, days, funding, leverage, expense, spread=0.005):
    """Return accounting before compounding; no extra 'volatility decay' fee."""
    if leverage < 1 or expense < 0:
        raise ValueError("This model supports long leverage >=1 and nonnegative fees")
    if not underlying.index.equals(days.index) or not underlying.index.equals(funding.index):
        raise ValueError("Return, day-count and funding calendars must match exactly")
    if pd.concat([underlying, days, funding], axis=1).isna().any().any():
        raise ValueError("Missing observations are not zero-return days")
    raw = leverage * underlying - (leverage - 1) * (funding + spread * days / 360) - expense * days / 365
    # Terminal zero NAV, never negative capital or a subsequent resurrection.
    out = raw.clip(lower=-1).copy()
    failed = np.flatnonzero(raw.to_numpy() <= -1)
    if len(failed):
        out.iloc[failed[0] + 1:] = 0.0
    return out


def wealth(returns, initial=1.0):
    if returns.isna().any() or (returns < -1).any():
        raise ValueError("Invalid returns")
    return initial * (1 + returns).cumprod()


def fit_spread(underlying, actual, days, funding, leverage, expense):
    """Fit one constant residual spread using ONLY the supplied training rows.

    Matches training terminal log growth. This is a diagnostic effective drag,
    not an identified borrowing spread; no fitted leverage or daily corrections.
    """
    def error(spread):
        sim = simulate(underlying, days, funding, leverage, expense, spread)
        if (sim <= -1).any():
            return -1e9
        return float((np.log1p(sim) - np.log1p(actual)).sum())
    return float(brentq(error, -0.05, 0.10))


def metrics(returns, days):
    w = wealth(returns)
    years = float(days.sum() / 365.25)
    peak = np.maximum.accumulate(np.r_[1.0, w.to_numpy()])[1:]
    return {"cagr": float(w.iloc[-1] ** (1 / years) - 1),
            "max_drawdown": float((w / peak - 1).min()),
            "terminal_multiple": float(w.iloc[-1]), "years": years}


def compare(sim, actual, days):
    s, a = metrics(sim, days), metrics(actual, days)
    gap = sim - actual
    # Count-based annualization is explicitly a 252-session convention.
    return {"daily_correlation": float(sim.corr(actual)),
            "daily_rmse_bps": float(np.sqrt(np.mean(gap ** 2)) * 10000),
            "tracking_error_annual": float(gap.std(ddof=1) * np.sqrt(252)),
            "sim_cagr": s["cagr"], "actual_cagr": a["cagr"],
            "cagr_gap_pp": (s["cagr"] - a["cagr"]) * 100,
            "terminal_relative_error": s["terminal_multiple"] / a["terminal_multiple"] - 1,
            "sim_max_drawdown": s["max_drawdown"], "actual_max_drawdown": a["max_drawdown"]}


def portfolio(returns, weights, rebalance="quarterly"):
    """Close-to-close buy-and-hold or calendar rebalancing, no cash flows/taxes.

    Rebalance at the preceding period's final close, affecting the NEXT day's
    return. Daily ETF leverage resets are separate from portfolio rebalancing.
    """
    if rebalance not in {"never", "monthly", "quarterly", "annual"}:
        raise ValueError("Unsupported rebalance frequency")
    w = pd.Series(weights, dtype=float).reindex(returns.columns)
    if w.isna().any() or (w < 0).any() or not np.isclose(w.sum(), 1):
        raise ValueError("Specify nonnegative weights for every column, summing to one")
    if returns.isna().any().any() or (returns < -1).any().any():
        raise ValueError("Select a common complete calendar before combining funds")
    periods = None if rebalance == "never" else returns.index.to_period(
        {"monthly": "M", "quarterly": "Q", "annual": "Y"}[rebalance])
    sleeves = w.to_numpy().copy()
    terminated = np.zeros(len(w), dtype=bool)
    out = []
    for i, row in enumerate(returns.to_numpy()):
        if i and periods is not None and periods[i] != periods[i - 1]:
            if np.any(terminated & (w.to_numpy() > 0)):
                raise ValueError("Cannot rebalance into a fund whose NAV reached zero")
            sleeves = sleeves.sum() * w.to_numpy()
        before = sleeves.sum()
        sleeves *= 1 + row
        terminated |= row <= -1
        out.append(sleeves.sum() / before - 1 if before else 0.0)
    return pd.Series(out, index=returns.index, name="portfolio_return")
