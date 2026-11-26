"""Prespecified secular return/rate stress and causal quarterly allocation.

Separate research module: does not replace the frozen histories or reports.
Run python -m letf.secular_regime_stress --paths 100 --years 20.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import json

import numpy as np
import pandas as pd

from .leaps_robustness import (
    PRICE, EQUITY_TR, TREASURY_R, CASH_R, WARMUP_SESSIONS,
    block_draw, build_horizon, build_path, leaps_rule, load,
)
from .leaps_frontier import frontier_pool, NDX_PRICE, NDX_TOTAL, NDX_YIELD
from .nasdaq_leaps import underlying_inputs
from .options import roll_schedule, simulate_leaps_arrays
from .treasury_leverage import FINANCING
from .provenance import sha

SEED = 20261126
INFLATION = .025
SPREAD = .005
STRUCTURES = {'80_25': (.80, .25), '85_30': (.85, .30),
              '90_35': (.90, .35), '90_40': (.90, .40)}
# Explicit assumptions, not estimated forecasts. All returns are real
# geometric targets; relative Nasdaq growth is a log-return increment.
@dataclass(frozen=True)
class Regime:
    name: str
    equity: float
    cash: float
    bond: float
    vol: float
    ndx_excess: float
    correlation: float

FAVORABLE = Regime('favorable', .08, .01, .035, .16, .025, -.25)
NORMAL = Regime('normalization', .055, .02, .02, .17, 0., -.10)
SLOW = Regime('slowdown', .035, .02, .015, .18, 0., .10)
STAGNATION = Regime('secular_stagnation', .02, .005, .01, .16, 0., .10)
FISCAL = Regime('fiscal_stress', .03, .03, -.01, .22, -.01, .40)
AI = Regime('ai_boom', .09, .02, .025, .18, .025, -.10)
AI_EXPENSIVE = Regime('ai_expensive_capital', .09, .04, .02, .22, 0., .20)
BOOM = Regime('exceptional_productivity', .12, .03, .03, .20, .025, -.10)
REGIMES = (FAVORABLE, NORMAL, SLOW, STAGNATION, FISCAL, AI, AI_EXPENSIVE, BOOM)
# The transition shock is a separate price-level loss, not a permanent
# reduction in the post-transition return target. Its duration is one year.
TRANSITIONS = (
    ('boom_to_slow', FAVORABLE, SLOW, -.20),
    ('boom_to_fiscal', FAVORABLE, FISCAL, -.25),
    ('slow_to_ai', SLOW, AI, 0.),
    ('ai_to_normal', AI, NORMAL, -.15),
)
def nominal_log(real):
    return np.log1p(real) + np.log1p(INFLATION)

def transform(sample, pool, regime):
    """Apply population-level targets to the joint daily innovations.

    No per-path recentering: an unlucky path remains unlucky. Cash and financing
    shift together. Long-bond return/correlation are explicit reduced-form
    assumptions, not a yield-curve forecast. Preserve the observed bond residual
    and its volatility. Price and total return share the same adjustment.
    """
    out = sample.copy()
    p = np.log1p(pool[:, PRICE])
    x = np.log1p(sample[:, PRICE])
    scale = regime.vol / (p.std(ddof=0) * np.sqrt(252))
    total = np.log1p(pool[:, EQUITY_TR])
    delta = (scale - 1.) * (x - p.mean()) + nominal_log(regime.equity)/252 - total.mean()
    out[:, PRICE] = np.expm1(x + delta)
    out[:, EQUITY_TR] = np.expm1(np.log1p(sample[:, EQUITY_TR]) + delta)

    p_ndx = np.log1p(pool[:, NDX_PRICE])
    n = np.log1p(sample[:, NDX_PRICE])
    total_ndx = np.log1p(pool[:, NDX_TOTAL])
    ndelta = ((scale - 1.) * (n - p_ndx.mean())
              + (nominal_log(regime.equity) + regime.ndx_excess)/252
              - total_ndx.mean())
    out[:, NDX_PRICE] = np.expm1(n + ndelta)
    out[:, NDX_TOTAL] = np.expm1(np.log1p(sample[:, NDX_TOTAL]) + ndelta)

    cash_delta = nominal_log(regime.cash)/252 - np.log1p(pool[:, CASH_R]).mean()
    out[:, CASH_R] = np.expm1(np.log1p(sample[:, CASH_R]) + cash_delta)
    # Existing financing includes the baseline spread. Adjust its level only.
    out[:, FINANCING] += cash_delta

    b = np.log1p(pool[:, TREASURY_R])
    bx = np.log1p(sample[:, TREASURY_R])
    z = (x - p.mean()) / p.std(ddof=0)
    zp = (p - p.mean()) / p.std(ddof=0)
    beta = np.mean(zp * (b - b.mean()))
    residual = b - b.mean() - beta * zp
    rz = (bx - b.mean() - beta * z) / residual.std(ddof=0)
    rho = regime.correlation
    bond = (rho * z + np.sqrt(1-rho*rho) * rz) * b.std(ddof=0)
    out[:, TREASURY_R] = np.expm1(nominal_log(regime.bond)/252 + bond)
    return out

def desired_state(premium, variance):
    """0 red, 1 amber, 2 green: 2x and 3x vs 1x growth hurdles."""
    return np.where(premium > 1.5*variance + SPREAD, 2,
                    np.where(premium > variance + SPREAD, 1, 0))

def true_state(regime):
    premium = nominal_log(regime.equity) - nominal_log(regime.cash)
    return int(desired_state(premium, regime.vol**2))

def quarterly_states(price, total, cash, calendar, entry,
                     confirm=2, method='combined', lag=2):
    """State at each close, known before the next return. No future regime label.

    Quarterly close t observed, execution close t+lag-1, new risk affects
    return t+lag. Two consecutive quarterly recommendations by default.
    Forecast is intentionally simple: fixed 8% nominal geometric
    return anchor, optionally 25% weight on trailing total log return.
    Cash carry uses trailing 63 sessions; variance uses trailing 126.
    Trend gate only forbids green when price is below its trailing 200-day SMA.
    """
    if confirm < 1 or lag not in (1, 2):
        raise ValueError('Invalid confirmation or execution lag')
    price = pd.Series(price, index=calendar)
    r = pd.Series(np.r_[0., np.log1p(total)], index=calendar)
    c = pd.Series(np.r_[0., np.log1p(cash)], index=calendar)
    vol2 = np.log(price).diff().rolling(126).var() * 252
    trailing = r.rolling(252).sum()
    funding = c.rolling(63).sum() * 4
    anchor = np.log1p(.08)
    premium = anchor - funding
    if method == 'combined':
        premium = .75*anchor + .25*trailing - funding
    elif method != 'rate_vol':
        raise ValueError('Unknown method')
    raw = desired_state(premium.to_numpy(), vol2.to_numpy())
    trend = (price > price.rolling(200).mean()).to_numpy()
    if method == 'combined':
        raw = np.where(trend, raw, np.minimum(raw, 1))
    quarters = calendar.to_period('Q')
    decision = np.flatnonzero(quarters[1:] != quarters[:-1])
    state = np.ones(len(calendar), dtype=int)
    current, candidate, streak = 1, -1, 0
    for t in decision:
        if t < entry or t + lag - 1 >= len(calendar):
            continue
        new = int(raw[t])
        if new == candidate:
            streak += 1
        else:
            candidate, streak = new, 1
        if streak >= confirm and candidate != current:
            current = candidate
            state[t+lag-1:] = current
    return state[entry:]

def nav(returns):
    """Absorbing total loss (no negative wealth, no resurrection)."""
    return np.r_[1., np.cumprod(np.maximum(1. + returns, 0.))]

def leveraged(total, funding, leverage, days, expense):
    return np.maximum(-1., leverage*total - (leverage-1)*funding
                      - np.where(leverage > 1, expense*days/365., 0.))

def adaptive_equity(total, funding, days, state, expense, cost=.0025):
    lev = np.array([1., 2., 3.])[state[:-1]]
    ret = leveraged(total, funding, lev, days, expense)
    switches = np.r_[False, state[1:-1] != state[:-2]]
    return nav((1 + ret)*(1 - cost*switches) - 1)

@lru_cache(maxsize=512)
def schedule(calendar_bytes, key, begin):
    # Build on the full remaining calendar, not the future switch endpoint.
    # A switch cannot retroactively change the contract initially purchased.
    calendar = pd.DatetimeIndex(np.frombuffer(calendar_bytes, dtype='datetime64[ns]'))
    return roll_schedule(calendar[begin:], leaps_rule(*STRUCTURES[key], 1.))

def option_segment(calendar, spot, q, rate, iv, safe, key, begin, end,
                   liquidate=False):
    from dataclasses import replace
    full = schedule(calendar.to_numpy(dtype='datetime64[ns]').tobytes(), key, begin)
    use = full.starts < end-begin
    s = replace(full, starts=full.starts[use], expiry_days=full.expiry_days[use])
    sl = slice(begin, end+1)
    days = (calendar[sl] - calendar[begin]).days.to_numpy(dtype=float)
    path = simulate_leaps_arrays(spot[sl], np.r_[1., 1+safe[begin:end]],
                                 q[sl], rate[sl], iv[sl], days, s, ledger=False)
    result = path.navs.copy()
    if liquidate:
        result[-1] *= 1 - path.option_weights[-1]*s.rule.spread_bps/10000
    return result

def adaptive_options(calendar, spot, q, rate, iv, total, cash, state,
                     red_cash=False, cost=.0025, safe=None, amber='80_25'):
    """Switch at confirmed state changes, sell actual marked call, re-open.

    Never splice the daily returns of a continuously running rival portfolio.
    The existing options engine prices every actual holding segment.
    """
    n = len(calendar)
    safe = cash if safe is None else safe
    starts = np.r_[0, np.flatnonzero(state[1:-1] != state[:-2])+1]
    result = np.ones(n)
    for begin, end in zip(starts, np.r_[starts[1:], n-1]):
        begin, end = int(begin), int(end)
        if state[begin] == 0:
            segment = nav((cash if red_cash else total)[begin:end])
        else:
            key = amber if state[begin] == 1 else '90_40'
            segment = option_segment(calendar, spot, q, rate, iv, safe, key,
                                     begin, end, liquidate=end < n-1)
        if begin:
            segment[1:] *= 1-cost
        result[begin+1:end+1] = result[begin]*segment[1:]
    return result

def duration_weights(sample, calendar, entry, lag=2):
    """Quarterly duration risk gate, using realized 126-session co-movement.

    Long bonds qualify for half of the reserve only if correlation with
    equities is negative AND trailing return exceeds cash. Not a structural
    forecast; tests whether duration should be managed independently.
    Close t observations affect returns ending t+lag or later.
    """
    b = pd.Series(np.r_[0., np.log1p(sample[:, TREASURY_R])], index=calendar)
    e = pd.Series(np.r_[0., np.log1p(sample[:, EQUITY_TR])], index=calendar)
    c = pd.Series(np.r_[0., np.log1p(sample[:, CASH_R])], index=calendar)
    eligible = ((b.rolling(126).corr(e) < 0) &
                ((b-c).rolling(126).sum() > 0)).to_numpy()
    quarter = calendar.to_period('Q')
    decision = np.flatnonzero(quarter[1:] != quarter[:-1])
    weights = np.zeros(len(calendar))
    for t in decision:
        if t >= entry and t+lag-1 < len(calendar):
            weights[t+lag-1:] = .5*eligible[t]
    return weights[entry:]

def mix_reserve(cash, bond, close_weights, cost=.0005):
    """Idealized daily target-weight reserve, with charge on weight changes."""
    w = close_weights[:-1]
    turnover = np.abs(np.diff(w, prepend=w[0]))
    return (1 + (1-w)*cash + w*bond)*(1-cost*turnover)-1

def metrics(w, years):
    peak = np.maximum.accumulate(w)
    return ((w[-1] ** (1/years))/(1+INFLATION)-1,
            float(np.min(w/peak-1)), float(w[-1]/(1+INFLATION)**years))

def run_path(sample, horizon, config, truth, transition=None):
    entry = WARMUP_SESSIONS
    cal = horizon.closes
    days = np.diff(horizon.days)
    cash = sample[entry:, CASH_R]
    bond = sample[entry:, TREASURY_R]
    funding = sample[entry:, FINANCING]
    duration = duration_weights(sample, horizon.calendar, entry)
    mixed = mix_reserve(cash,bond,np.full(len(cal),.5))
    dynamic = mix_reserve(cash,bond,duration)
    out = {'TBILL': nav(cash), 'LONG_TREASURY': nav(bond),
           'RESERVE_50_50': nav(mixed), 'RESERVE_DURATION_GATE': nav(dynamic)}
    states = {}
    for method in ('combined', 'rate_vol'):
        for confirm in (1, 2, 4):
            label = f'{method.upper()}_Q{confirm}'
            states[label] = None
    # Same broad-market signal governs both indices. Oracle is a deliberately
    # infeasible diagnostic using assumed population parameters.
    broad = np.r_[1., np.cumprod(1 + sample[:, PRICE])]
    for name in list(states):
        method, qcount = name.rsplit('_Q', 1)
        states[name] = quarterly_states(broad, sample[:, EQUITY_TR],
                                      sample[:, CASH_R], horizon.calendar, entry,
                                      int(qcount), method.lower())
    states['ORACLE'] = truth.copy()
    for prefix, pricecol, totalcol, yieldcol, fund in (
            ('SPX', None, EQUITY_TR, None, 'UPRO'),
            ('NDX', NDX_PRICE, NDX_TOTAL, NDX_YIELD, 'TQQQ')):
        spot, iv, rate, q, sma = build_path(sample, horizon.calendar, signal=True,
                                            price_col=pricecol, yield_col=yieldcol)
        spot, iv, rate, q = (a[entry:] for a in (spot, iv, rate, q))
        total = sample[entry:, totalcol]
        expense = config['funds'][fund]['expense']
        out[prefix+'_1X'] = nav(total)
        for l in (2, 3):
            fee = config['funds']['SSO']['expense'] if prefix == 'SPX' and l == 2 else expense
            out[f'{prefix}_{l}X'] = nav(leveraged(total, funding, l, days, fee))
        # Canonical SMA state is indexed by return-end session, already lagged.
        lev = np.where(np.asarray(sma)[entry+1:] > .5, 3., 1.)
        ret = leveraged(total, funding, lev, days, expense)
        switch = np.r_[False, lev[1:] != lev[:-1]]
        out[prefix+'_SMA_3_1'] = nav((1+ret)*(1-.0025*switch)-1)
        out[prefix+'_SMA_3_BILLS'] = nav((1+np.where(lev==3,ret,cash))*(1-.0025*switch)-1)
        for key in STRUCTURES:
            for sleeve, safe in (('BILLS', cash), ('LONG', bond),
                                 ('MIXED', mixed), ('DURATION_GATE', dynamic)):
                out[f'{prefix}_{key}_{sleeve}'] = option_segment(
                    cal, spot, q, rate, iv, safe, key, 0, len(cal)-1)
        for name, state in states.items():
            out[f'{prefix}_ADAPT_ETF_{name}'] = adaptive_equity(
                total, funding, days, state, expense)
            out[f'{prefix}_ADAPT_CALL_{name}'] = adaptive_options(
                cal, spot, q, rate, iv, total, cash, state)
        if prefix == 'NDX':
            for label, safe in (('MIXED',mixed),('DURATION_GATE',dynamic),('LONG',bond)):
                out[f'{prefix}_ADAPT_CALL_COMBINED_Q2_{label}'] = adaptive_options(
                    cal, spot, q, rate, iv, total, cash, states['COMBINED_Q2'],
                    safe=safe, amber='85_30')
            out['NDX_ADAPT_CALL_COMBINED_Q2_BILLS_85_30'] = adaptive_options(
                cal, spot, q, rate, iv, total, cash, states['COMBINED_Q2'], amber='85_30')
            # Additional NDX-specific signal versus the common SPX signal.
            ndx_state = quarterly_states(np.r_[1.,np.cumprod(1+sample[:,NDX_PRICE])],
                sample[:,NDX_TOTAL],sample[:,CASH_R],horizon.calendar,entry,2,'combined')
            out['NDX_ADAPT_CALL_OWN_Q2_DURATION_GATE'] = adaptive_options(
                cal,spot,q,rate,iv,total,cash,ndx_state,safe=dynamic,amber='85_30')
        out[f'{prefix}_ADAPT_CALL_COMBINED_Q2_RED_CASH'] = adaptive_options(
            cal, spot, q, rate, iv, total, cash, states['COMBINED_Q2'], red_cash=True)
    years = horizon.elapsed_days / 365.25
    rows = {name: metrics(w, years) for name,w in out.items()}
    detection = {}
    for name, state in states.items():
        switches = int(np.count_nonzero(np.diff(state)))
        delay = np.nan
        already = np.nan
        changed = transition is not None and truth[transition-1] != truth[transition]
        if changed:
            already = float(state[transition-1] == truth[transition])
            hits = np.flatnonzero(state[transition:] == truth[transition])
            if len(hits) and not already:
                delay = hits[0]/63.
        detection[name] = (np.mean(state == truth), switches, delay,
                           np.mean(state == 2), np.mean(state == 0),
                           np.mean(state > truth), np.mean(state < truth),
                           already, float(changed))
    return rows, detection

def run(root, paths=100, years=20, block=63, seed=SEED, output=None):
    inputs = load(root)
    market, _ = underlying_inputs(inputs, root)
    pool = frontier_pool(inputs, market)
    horizon = build_horizon(inputs, years, ('12m',), structures=STRUCTURES)
    n = len(horizon.calendar)-1
    e = WARMUP_SESSIONS
    # Transition occurs five years after portfolio entry, not after warm-up.
    t = horizon.closes.searchsorted(horizon.closes[0]+pd.DateOffset(years=5))
    cases = [(r.name, r, None, 0.) for r in REGIMES] + list(TRANSITIONS)
    cases += [('historical_bootstrap', None, None, 0.)]
    if paths < 1 or block < 1 or t >= len(horizon.closes):
        raise ValueError('Invalid path count, block or transition date')
    draws = [block_draw(np.random.default_rng(seed+i), len(pool), n, block)
             for i in range(paths)]
    rows, detectors = [], []
    for label, before, after, shock in cases:
        for i, draw in enumerate(draws):
            raw = pool[draw]
            if before is None:
                sample = raw.copy()
                # No assumed latent regime for the unmodified historical control.
                truth = np.ones(len(horizon.closes), dtype=int)
            else:
                sample = transform(raw, pool, before)
                truth = np.full(len(horizon.closes), true_state(before), dtype=int)
            if after is not None:
                sample[e+t:] = transform(raw[e+t:], pool, after)
                truth[t:] = true_state(after)
                span = min(252, n-e-t)
                if shock and span:
                    for col in (PRICE, EQUITY_TR, NDX_PRICE, NDX_TOTAL):
                        sample[e+t:e+t+span,col] = np.expm1(
                            np.log1p(sample[e+t:e+t+span,col])+np.log1p(shock)/span)
            result, det = run_path(sample, horizon, inputs.config, truth,
                                   t if after is not None else None)
            for name, (cagr, dd, wealth) in result.items():
                if before is None and 'ORACLE' in name:
                    continue
                rows.append(dict(regime=label, path=i, strategy=name,
                                 real_cagr=cagr, drawdown=dd, real_wealth=wealth))
            if before is not None:
                for name, (accuracy, switches, delay, green, red, over, under, already, changed) in det.items():
                    detectors.append(dict(regime=label, path=i, signal=name,
                        state_match=accuracy, switches=switches, first_match_quarters=delay,
                        green_fraction=green, red_fraction=red, over_risk_fraction=over,
                        under_risk_fraction=under, already_at_new_state=already,
                        true_state_changed=changed))
        print(f'{label}: {paths} paired paths', flush=True)
    frame = pd.DataFrame(rows)
    base = frame.pivot(index=['regime','path'], columns='strategy', values='real_cagr')
    frame = frame.join(base[['SPX_1X','TBILL']].rename(columns={
        'SPX_1X':'spx_cagr','TBILL':'bill_cagr'}), on=['regime','path'])
    frame['below_spx'] = frame.real_cagr < frame.spx_cagr
    frame['below_bills'] = frame.real_cagr < frame.bill_cagr
    frame = frame.join(base[['NDX_1X']].rename(columns={'NDX_1X':'ndx_cagr'}),on=['regime','path'])
    frame['below_ndx'] = frame.real_cagr < frame.ndx_cagr
    frame['dd60'] = frame.drawdown < -.60
    frame['dd50'] = frame.drawdown < -.50
    frame['dd75'] = frame.drawdown < -.75
    summary = frame.groupby(['regime','strategy']).agg(
        median_cagr=('real_cagr','median'),
        p5_cagr=('real_cagr',lambda x: x.quantile(.05)),
        median_drawdown=('drawdown','median'),
        p5_drawdown=('drawdown',lambda x: x.quantile(.05)),
        p_below_spx=('below_spx','mean'), p_below_bills=('below_bills','mean'),
        p_below_ndx=('below_ndx','mean'),
        p_dd50=('dd50','mean'), p_dd60=('dd60','mean'), p_dd75=('dd75','mean'),
        median_real_wealth=('real_wealth','median')).reset_index()
    # Oracle is diagnostic, not a feasible frontier member.
    feasible = ~summary.strategy.str.contains('ORACLE')
    summary['pareto'] = False
    summary['median_regret'] = np.nan
    for _, sub in summary[feasible].groupby('regime'):
        for idx, row in sub.iterrows():
            dominates = ((sub.median_cagr >= row.median_cagr) &
                         (sub.p5_cagr >= row.p5_cagr) &
                         (sub.p_dd60 <= row.p_dd60) &
                         ((sub.median_cagr > row.median_cagr) |
                          (sub.p5_cagr > row.p5_cagr) |
                          (sub.p_dd60 < row.p_dd60)))
            summary.loc[idx,'pareto'] = not dominates.any()
            summary.loc[idx,'median_regret'] = sub.median_cagr.max()-row.median_cagr
    out = output or root/'data'/'processed'/'secular_regime_stress'
    out.mkdir(parents=True, exist_ok=True)
    summary.to_csv(out/'summary.csv', index=False)
    frame.to_csv(out/'paths.csv', index=False)
    detection = pd.DataFrame(detectors)
    detection.to_csv(out/'detection.csv', index=False)
    detection['missed'] = np.where(
        (detection.true_state_changed == 1) & (detection.already_at_new_state == 0),
        detection.first_match_quarters.isna().astype(float), np.nan)
    detection.groupby(['regime','signal']).agg(
        state_match=('state_match','mean'), mean_switches=('switches','mean'),
        over_risk_fraction=('over_risk_fraction','mean'),
        under_risk_fraction=('under_risk_fraction','mean'),
        median_detection_quarters=('first_match_quarters','median'),
        already_at_new_state=('already_at_new_state','mean'),
        missed_shift_fraction=('missed','mean'),
        green_fraction=('green_fraction','mean'),
        red_fraction=('red_fraction','mean')).reset_index().to_csv(
            out/'detection_summary.csv', index=False)
    summary[summary.strategy.str.startswith('NDX') & feasible].to_csv(out/'nasdaq_decision.csv',index=False)
    summary[feasible].groupby('strategy').agg(
        worst_regime_median_cagr=('median_cagr','min'),
        worst_regime_p5_cagr=('p5_cagr','min'),
        worst_regime_p_dd60=('p_dd60','max'),
        max_median_regret=('median_regret','max'),
        pareto_fraction=('pareto','mean')).reset_index().to_csv(
            out/'cross_regime.csv', index=False)
    (out/'manifest.json').write_text(json.dumps(dict(
        paths=paths, years=years, block=block, seed=seed,
        inflation=INFLATION, regimes=[r.__dict__ for r in REGIMES],
        transitions=[(n,a.name,b.name,s) for n,a,b,s in TRANSITIONS],
        module_sha=sha(Path(__file__))), indent=2))
    return summary

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=Path.cwd())
    p.add_argument('--paths', type=int, default=100)
    p.add_argument('--years', type=int, choices=(10,20,30), default=20)
    p.add_argument('--block', type=int, default=63)
    p.add_argument('--seed', type=int, default=SEED)
    p.add_argument('--output', type=Path)
    a = p.parse_args()
    if a.paths < 1 or a.block < 1:
        p.error('paths and block must be positive')
    run(a.root,a.paths,a.years,a.block,a.seed,a.output)

if __name__ == '__main__':
    main()
