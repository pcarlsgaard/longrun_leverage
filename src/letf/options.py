"""Rolling long-dated calls as an alternative to daily-reset leverage.

**This module is modelled, not measured, and that is a weaker kind of evidence.**
Every other result in this repository is arithmetic on realized price history.
This one is not. The repository holds no option prices and no implied-volatility
history, and the environment has no network access to obtain them, so premia
here are Black-Scholes values on an *assumed* implied volatility. Any CAGR this
module produces is a property of that assumption.

The reporting convention that follows from this: prefer
:func:`break_even_iv_premium` to any single return number. It answers "how
expensive would options have to have been for this structure to lose to its
rival?", which turns the unmeasured input into the output and lets a reader
supply their own view of option pricing.

Two known biases, both favouring the option structure, both left in deliberately
so the break-even figure is conservative rather than flattering:

* Implied volatility is proxied by *trailing* realized volatility plus a
  premium. Real implied volatility rises at the onset of a crash, while
  trailing realized volatility only rises afterwards. A roll executed just
  before a crash therefore buys too cheaply here.
* Black-Scholes with a single volatility ignores the equity skew. A deep
  in-the-money call has the same implied volatility as the equally deep
  out-of-the-money put it is equivalent to by put-call parity, and that put is
  the expensive one. Real ITM calls cost more than this module charges.

A third difference is not a bias in a known direction but is worth stating:
because the volatility proxy is *realized*, it stays elevated for months after a
crash, so held options are marked up during recoveries while newly rolled ones
are bought expensively. Real implied volatility mean-reverts faster than
trailing realized volatility does, so both effects here are overstated and they
work against each other.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.special import ndtr

__all__ = ['LeapsRule', 'black_scholes_call', 'call_delta', 'trailing_dividend_yield',
           'trailing_riskfree', 'implied_volatility_proxy', 'simulate_leaps_portfolio',
           'break_even_iv_premium']

TRADING_DAYS = 252
YEAR = 365.25
VOL_FLOOR, VOL_CAP = .05, 1.50
YIELD_CAP = .10


@dataclass(frozen=True)
class LeapsRule:
    """One rolling-call policy.

    `moneyness` is strike/spot at purchase: 0.8 buys a 20% in-the-money call.
    Lower moneyness costs more premium per contract and gives less leverage per
    dollar, but has higher delta and less time value to lose.

    Exactly one of two sizing modes applies. When `premium_budget` is set, that
    fixed fraction of wealth is spent on premium at every roll and the safe
    weight is the constant complement — the structure with a genuine floor.
    Otherwise the position is re-struck to `target_exposure`, the delta-
    equivalent equity exposure as a multiple of portfolio wealth, and the safe
    weight is whatever is left; where the required premium exceeds wealth the
    position is capped at what wealth buys and the ledger records it.
    """
    moneyness: float = .80
    maturity_years: float = 2.
    roll_at_years: float = 1.
    target_exposure: float = 3.
    premium_budget: float | None = None
    iv_premium: float = .03
    spread_bps: float = 100.

    def __post_init__(self):
        if not 0 < self.moneyness <= 2:
            raise ValueError('Moneyness must be a positive fraction of spot')
        if not 0 < self.roll_at_years < self.maturity_years <= 5:
            raise ValueError('Roll must happen strictly before expiry')
        if not 0 < self.target_exposure <= 10:
            raise ValueError('Implausible target exposure')
        if not -.5 <= self.iv_premium <= 1:
            raise ValueError('Implausible volatility premium')
        if not 0 <= self.spread_bps < 5000:
            raise ValueError('Implausible option spread')
        if self.premium_budget is not None and not 0 < self.premium_budget <= 1:
            raise ValueError('Premium budget must be a fraction of wealth')


def black_scholes_call(spot, strike, rate, dividend, vol, years):
    """Call value on a dividend-paying index. Degenerate cases return intrinsic.

    At or past expiry the value is the payoff. At zero volatility the value is
    the discounted forward intrinsic, which is the limit of the formula and not
    the same as spot-minus-strike whenever rates or dividends are non-zero.
    """
    spot, strike, rate, dividend, vol, years = np.broadcast_arrays(
        *(np.asarray(v, dtype=float) for v in (spot, strike, rate, dividend, vol, years)))
    if np.any(spot < 0) or np.any(strike < 0):
        raise ValueError('Negative spot or strike')
    forward = spot * np.exp((rate - dividend) * years)
    discounted = np.exp(-rate * years) * np.maximum(forward - strike, 0.)
    live = (years > 0) & (vol > 0) & (spot > 0) & (strike > 0)
    # Guard the log and the division so the inactive branch cannot emit warnings.
    safe_vol = np.where(live, vol, 1.)
    safe_years = np.where(live, years, 1.)
    safe_ratio = np.where(live, forward / np.where(strike > 0, strike, 1.), 1.)
    sigma = safe_vol * np.sqrt(safe_years)
    d1 = np.log(safe_ratio) / sigma + sigma / 2
    value = np.exp(-rate * safe_years) * (forward * ndtr(d1) - strike * ndtr(d1 - sigma))
    return np.where(live, value, discounted)


def call_delta(spot, strike, rate, dividend, vol, years):
    """Sensitivity to spot, in units of the underlying. Degenerates to 0/1."""
    spot, strike, rate, dividend, vol, years = np.broadcast_arrays(
        *(np.asarray(v, dtype=float) for v in (spot, strike, rate, dividend, vol, years)))
    forward = spot * np.exp((rate - dividend) * years)
    live = (years > 0) & (vol > 0) & (spot > 0) & (strike > 0)
    safe_vol = np.where(live, vol, 1.)
    safe_years = np.where(live, years, 1.)
    safe_ratio = np.where(live, forward / np.where(strike > 0, strike, 1.), 1.)
    sigma = safe_vol * np.sqrt(safe_years)
    d1 = np.log(safe_ratio) / sigma + sigma / 2
    return np.where(live, np.exp(-dividend * safe_years) * ndtr(d1),
                    (forward > strike).astype(float))


def trailing_dividend_yield(price: pd.Series, total_return: pd.Series, window=TRADING_DAYS):
    """Realized dividend yield from the gap between total-return and price indices.

    Before 1988 this repository's 1x total-return series is a fund proxy (VFINX),
    and its tracking difference against the index is large enough to drive the
    implied yield negative — an impossibility that marks the estimate, not the
    dividends, as broken. Implausible values are dropped and filled from the
    nearest clean observation rather than clipped to zero, because a zero yield
    would overstate every call value in the contaminated stretch. The affected
    span is 1986-1987, which contains the single most important event in this
    study, so dropping those years instead is not an option and the fill is
    reported as an assumption.
    """
    if not price.index.equals(total_return.index):
        raise ValueError('Price and total-return calendars differ')
    tri = (1 + total_return).cumprod()
    trailing = (tri / tri.shift(window)) / (price / price.shift(window)) - 1
    implausible = ~((trailing > 0) & (trailing < YIELD_CAP))
    # An estimate is only as good as its whole window. Discarding just the
    # implausible values would keep the ones whose window straddles the broken
    # stretch, and those are the dangerous ones: they are contaminated but land
    # in a plausible range, so nothing flags them. Spreading the rejection
    # across a full window means the fill is taken from the nearest estimate
    # that saw no bad data at all.
    contaminated = implausible.rolling(window, min_periods=1).max().astype(bool)
    filled = trailing.where(~contaminated).bfill().ffill()
    if filled.isna().any():
        raise ValueError('No usable dividend-yield observation in the sample')
    return filled.rename('dividend_yield')


def trailing_riskfree(cash_returns: pd.Series, window=TRADING_DAYS):
    """Continuously-compounded one-year cash rate, from the realized bill accrual.

    Summing daily log accrual over a trailing year gives the rate actually
    earned, which already carries the repository's calendar-day carry convention.
    """
    log_rate = np.log1p(cash_returns).rolling(window).sum()
    return log_rate.bfill().ffill().rename('riskfree')


def implied_volatility_proxy(price_returns: pd.Series, iv_premium: float,
                             horizon_years=2., lag=1):
    """Realized volatility over the option's own horizon, plus a flat premium.

    The estimation window matches the option's tenor, and that choice is
    load-bearing rather than cosmetic. A two-year option carries large vega, so
    pricing it off a fast volatility estimate makes the mark swing on volatility
    rather than on spot: with a two-month exponential estimate this portfolio
    *gained* 64% the session after 1987-10-19 and gained on the worst session of
    the 2020 crash, because a 26-point jump in the estimate outweighed the index
    move. Those are artifacts of the proxy, not convexity. Long-dated implied
    volatility is in reality far steadier than short-dated, so estimating over
    the option's own horizon is both the financially correct choice and the one
    that removes the artifact.

    The estimate is also lagged one session. Volatility that includes session
    t's own return, used to value an option at close t, is look-ahead.

    The premium stands in for the variance risk premium and the skew; it is the
    parameter the sensitivity grid varies and the one
    :func:`break_even_iv_premium` solves for.
    """
    if lag < 0:
        raise ValueError('Volatility estimate cannot be lagged into the future')
    window = max(int(round(horizon_years * TRADING_DAYS)), 2)
    realized = price_returns.rolling(window, min_periods=window // 4).std(ddof=1)
    lagged = realized.shift(lag) * np.sqrt(TRADING_DAYS)
    return (lagged + iv_premium).clip(VOL_FLOOR, VOL_CAP).bfill().rename('implied_vol')


def simulate_leaps_portfolio(price: pd.Series, safe_returns: pd.Series, dividend: pd.Series,
                             riskfree: pd.Series, vol: pd.Series, rule=LeapsRule(), ledger=True):
    """Roll long-dated calls against a safe sleeve; return the wealth path.

    `price` is the underlying index on closes, starting at the entry close.
    `safe_returns` is the unleveraged sleeve, indexed on the return-end dates,
    i.e. `price.index[1:]`. Wealth starts at 1.0 on the entry close.

    Two sizing modes, and the difference between them is the whole experiment.

    With `premium_budget` set, a fixed fraction of wealth is spent on premium at
    each roll and the rest sits in the safe sleeve, so the safe weight is a
    constant and the option leg can lose everything without taking the portfolio
    with it. Realized exposure is then whatever that budget buys.

    With `premium_budget` unset the position is instead re-struck to
    `target_exposure` at each roll and the safe weight is a residual. Note what
    this does after a loss: it re-levers to the target multiple of the *reduced*
    wealth, so a sequence of bad years compounds into a near-total loss even
    though each individual option could only ever lose its premium. Bounded loss
    per contract does not imply bounded loss per portfolio once the position is
    re-levered, which is the trap this mode exists to demonstrate.

    Either way exposure is re-struck only at rolls, never daily. That is the
    structural difference from a daily-reset fund: between rolls the position is
    never forced to trade, and the option's own convexity carries the exposure.
    """
    closes = price.index
    if not isinstance(closes, pd.DatetimeIndex) or not closes.is_monotonic_increasing:
        raise ValueError('Price must be a sorted daily close series')
    if not safe_returns.index.equals(closes[1:]):
        raise ValueError('Safe returns must cover every session after the entry close')
    inputs = pd.concat([price, dividend, riskfree, vol], axis=1)
    if (inputs.isna().any().any() or safe_returns.isna().any()
            or not np.isfinite(inputs.to_numpy()).all() or (price <= 0).any()):
        raise ValueError('Incomplete or non-finite option inputs')

    spot = price.to_numpy()
    q, r, sigma = (s.reindex(closes).to_numpy() for s in (dividend, riskfree, vol))
    safe_growth = np.r_[1., 1 + safe_returns.to_numpy()]
    days = (closes - closes[0]).days.to_numpy().astype(float)
    spread = rule.spread_bps / 10000
    n = len(closes)

    # Roll dates depend only on the calendar and the rule, never on wealth: the
    # position is rolled once `maturity - roll_at` years have elapsed since the
    # last roll. Precomputing them lets each holding period be valued as one
    # vector operation instead of a session-by-session loop.
    hold = (rule.maturity_years - rule.roll_at_years) * YEAR
    starts, i = [0], 0
    while True:
        following = np.flatnonzero(days >= days[i] + hold)
        if not len(following):
            break
        i = int(following[0])
        starts.append(i)

    navs = np.empty(n)
    exposures = np.empty(n)
    wealth, rolls = 1., []
    for k, begin in enumerate(starts):
        # The option bought at `begin` is valued through `last`, the session it
        # is sold on, which is also the session the next one is bought on. That
        # session's wealth and exposure belong to the position being closed, so
        # each segment writes from `begin + 1` and the roll session is written
        # by the segment that held it.
        last = starts[k + 1] if k + 1 < len(starts) else n - 1
        strike = rule.moneyness * spot[begin]
        expiry = days[begin] + rule.maturity_years * YEAR
        premium = float(black_scholes_call(spot[begin], strike, r[begin], q[begin],
                                           sigma[begin], rule.maturity_years))
        entry_delta = float(call_delta(spot[begin], strike, r[begin], q[begin],
                                       sigma[begin], rule.maturity_years))
        if premium <= 0 or entry_delta <= 0:
            raise ValueError('Degenerate option at roll; check volatility inputs')
        afford = wealth / (premium * (1 + spread))
        if rule.premium_budget is not None:
            contracts, capped = afford * rule.premium_budget, False
        else:
            want = rule.target_exposure * wealth / (entry_delta * spot[begin])
            contracts, capped = min(want, afford), want > afford
        bills = wealth - contracts * premium * (1 + spread)
        if bills < -1e-12:
            raise ValueError('Roll spent more than available wealth')
        bills = max(bills, 0.)
        if ledger:
            rolls.append(dict(close=closes[begin], capped=capped, strike=strike,
                              contracts=contracts, spot=spot[begin], wealth=wealth,
                              premium=premium, safe_weight=bills / wealth,
                              implied_vol=sigma[begin], entry_delta=entry_delta))

        window = slice(begin, last + 1)
        years = np.maximum((expiry - days[window]) / YEAR, 0.)
        value = black_scholes_call(spot[window], strike, r[window], q[window],
                                   sigma[window], years)
        delta = call_delta(spot[window], strike, r[window], q[window], sigma[window], years)
        # The safe sleeve compounds from the roll close onward; the roll session
        # itself is already priced into the wealth being reinvested, so its
        # growth factor must not be applied a second time.
        carry = np.cumprod(np.r_[1., safe_growth[begin + 1:last + 1]])
        held = contracts * value + bills * carry
        if not np.all(held > 0):
            raise ValueError('Non-positive wealth; option accounting is broken')
        carried = contracts * delta * spot[window] / held
        if k == 0:
            # Wealth is 1.0 at the entry close, before the position is paid for,
            # so the cost of establishing it lands in the first session's return
            # rather than in a path that mysteriously starts below par.
            navs[begin], exposures[begin] = 1., carried[0]
        navs[begin + 1:last + 1] = held[1:]
        exposures[begin + 1:last + 1] = carried[1:]
        wealth = float(contracts * value[-1] * (1 - spread) + bills * carry[-1])

    nav = pd.Series(navs, index=closes, name='wealth')
    exposure = pd.Series(exposures, index=closes, name='delta_exposure')
    if not ledger:
        return nav
    return nav, exposure, pd.DataFrame(rolls)


def break_even_iv_premium(rival_cagr: float, build, low=-.05, high=.60, tolerance=1e-4):
    """Volatility premium at which the option structure exactly matches `rival_cagr`.

    `build(premium)` must return the structure's CAGR at that premium. Higher
    premia buy the same exposure more expensively, so the mapping is decreasing
    and bisection is safe. Returns NaN when the structure loses across the whole
    bracket (it cannot catch the rival at any option price) or wins across it
    (no option price this side of absurd makes it lose), because reporting a
    bracket endpoint as a solution would invent precision that is not there.
    """
    f_low, f_high = build(low) - rival_cagr, build(high) - rival_cagr
    if f_low <= 0 or f_high >= 0:
        return float('nan')
    for _ in range(60):
        mid = (low + high) / 2
        if build(mid) - rival_cagr > 0:
            low = mid
        else:
            high = mid
        if high - low < tolerance:
            break
    return (low + high) / 2
