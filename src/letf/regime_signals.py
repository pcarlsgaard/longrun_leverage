"""Prespecified leverage-regime comparison using verified frozen inputs only."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .analysis import CASH, load_inputs, matched
from .falsification import (LAGS, PERIODS, load_price_signals, select_returns,
    switching_costs, transitions, evaluate, economic_components, subperiod_index)
from .signals import level_position, signal_price_return, volatility_position
from .model import calendar_days
from .provenance import FLOAT_FORMAT, stable_floats

SMA = 'UPRO_SMA_TO_SP500'


def regression_quality(price, window=120):
    """OLS log-level trend; t is a descriptive score, not an IID significance test."""
    x = np.arange(window, dtype=float)
    x -= x.mean()
    sxx = x @ x
    def fit(values):
        y = values - values.mean()
        syy = y @ y
        slope = (x @ y) / sxx
        explained = slope * slope * sxx
        if syy <= 1e-24:
            return 0., 0., 0.
        residual = max(0., syy - explained)
        t = (np.copysign(np.inf, slope) if residual <= 1e-12 * syy
             else slope / np.sqrt(residual / (window - 2) / sxx))
        return slope, min(1., explained / syy), t
    logs = np.log(price.to_numpy())
    result = np.full((len(price), 3), np.nan)
    for i in range(window-1, len(price)):
        values = logs[i-window+1:i+1]
        if np.isfinite(values).all():
            result[i] = fit(values)
    return pd.DataFrame(result, index=price.index, columns=['slope', 'r_squared', 'slope_t'])


def signal_features(price, price_return, median_price=None):
    """Features at close t; execution lag is applied once, downstream.

    Every feature reads the unadjusted price index, including the volatility
    ratio. Mixing a price-level trend with a total-return volatility estimate
    was the inconsistency this module carried before the price-only convention
    was adopted repository-wide.
    """
    out = regression_quality(price)
    vol20 = price_return.rolling(20).std(ddof=1)
    vol120 = price_return.rolling(120).std(ddof=1)
    out['relative_volatility'] = vol20 / vol120.replace(0, np.nan)
    distance = price.diff().abs().rolling(60).sum()
    net = price.diff(60)
    out['er'] = (net.abs() / distance.replace(0, np.nan)).where(distance.ne(0), 0.)
    out['ser'] = (net / distance.replace(0, np.nan)).where(distance.ne(0), 0.)
    # A 60-return window contains 59 adjacent pairs. Zero returns do not flip.
    r = price.pct_change(fill_method=None)
    flips = (r * r.shift(1) < 0).astype(float).where(r.notna() & r.shift(1).notna())
    out['flip_rate'] = flips.rolling(59).mean()
    # Exclude current close's flip statistic even before applying execution lag.
    out['flip_median'] = out.flip_rate.expanding(min_periods=1).median().shift(1)
    if median_price is not None:
        out['ao'] = median_price.rolling(5).mean() - median_price.rolling(34).mean()
    return out


def positions(daily, features, price, price_return, lag):
    if lag not in LAGS:
        raise ValueError('Unsupported lag')
    f = features
    def state(condition, required):
        return condition.astype(float).where(f[required].notna().all(axis=1)).shift(lag)
    vol, _ = volatility_position(price_return, lag=lag, binary=True)
    out = {
        SMA: level_position(price, daily.index, 200, lag),
        'VOL_BINARY': (vol-1)/2,
        'REL_VOL': state(f.relative_volatility <= 1., ['relative_volatility']),
        'EFFICIENCY': state((f.ser > 0) & (f.er >= .25), ['ser', 'er']),
        'TREND_QUALITY': state((f.slope > 0) & (f.slope_t >= 2), ['slope', 'slope_t']),
        'LOW_CHURN': state(f.flip_rate < f.flip_median, ['flip_rate', 'flip_median']),
    }
    if 'ao' in f:
        out['AO'] = state(f.ao > 0, ['ao'])
    return pd.DataFrame(out, index=daily.index)


def archived_prices(root, config, calendar):
    price = load_price_signals(root, config, offline=True)['SP500'].reindex(calendar)
    if price.isna().any():
        raise ValueError('Archived S&P close missing a required session')
    obj = json.loads((root/'data/raw/yahoo_GSPC.json').read_text())['chart']['result'][0]
    quote = obj['indicators']['quote'][0]
    if not {'high', 'low'} <= quote.keys():
        return price, None, 'AO omitted: frozen archive lacks high/low inputs.'
    index = pd.to_datetime(obj['timestamp'], unit='s', utc=True).tz_convert('America/New_York').tz_localize(None).normalize()
    if index.duplicated().any():
        raise ValueError('Duplicate high/low dates')
    hl = pd.DataFrame({k: quote[k] for k in ('high','low')}, index=index).reindex(calendar)
    if hl.isna().any().any() or (hl <= 0).any().any() or (hl.high < hl.low).any():
        return price, None, 'AO omitted: frozen high/low inputs fail completeness/validity checks.'
    return price, (hl.high+hl.low)/2, 'AO included: complete positive high/low observations in verified frozen S&P archive; no downloads.'


def matched_control(underlying, leveraged, days, expense, state):
    """Existing reserve experiment: inferred funding, full fund fee, constant L."""
    fee = expense * days / 365
    funding = (3*underlying-leveraged-fee)/2
    exposure = 1+2*state.mean()
    control = exposure*underlying-(exposure-1)*funding-fee
    # Companion equalizes total simple fee spend, keeping the fee rate constant.
    fee_fraction = (fee*state).sum()/fee.sum()
    fee_equal = control + fee*(1-fee_fraction)
    if (control <= -1).any() or (fee_equal <= -1).any():
        raise ValueError('Matched control reaches insolvency')
    return control, fee_equal


def metric_row(r, state, daily, config, calendar, cohorts):
    u, l = daily.loc[r.index, 'SP500_1X'], daily.loc[r.index, 'UPRO_BASE']
    days = calendar_days(r.index, calendar[calendar.get_loc(r.index[0])-1])
    control, neutral = matched_control(u, l, days, config['funds']['UPRO']['expense'], state)
    m = evaluate(r, daily[CASH], calendar, state, cohorts=cohorts)
    cm = evaluate(control, daily[CASH], calendar, cohorts=cohorts)
    nm = evaluate(neutral, daily[CASH], calendar, cohorts=False)
    m.update(average_equity_exposure=1+2*state.mean(),
        terminal_wealth_10000=10000*m['terminal_multiple'],
        timing_cagr_difference=m['cagr']-cm['cagr'],
        timing_terminal_ratio=m['terminal_multiple']/cm['terminal_multiple'],
        fee_equal_control_cagr=nm['cagr'],
        fee_equal_timing_cagr_difference=m['cagr']-nm['cagr'])
    m.update({f'matched_{k}': v for k,v in cm.items()})
    return m


def run(root):
    daily, config = load_inputs(root, offline=True)
    calendar = daily.index
    price, median, ao_note = archived_prices(root, config, calendar)
    prices = load_price_signals(root, config, offline=True)
    returns = signal_price_return(price)
    features = signal_features(price, returns, median)
    states = {lag: positions(daily, features, price, returns, lag) for lag in LAGS}
    # Same comparison window as every other price-signal battery. It is identical
    # to the total-return-matched window this module used before, so the
    # signal-independent comparators below are still directly checkable.
    prior_positions = [level_position(prices[u], calendar, 250, 2)
                       for u in ('SP500', 'NASDAQ100')]
    ix = matched(pd.concat([daily[['SP500_1X','NASDAQ100_1X','LONG_TREASURY_1X',CASH]],
                            *prior_positions, *states.values()], axis=1)).index
    metrics, subs, conditional = [], [], []
    days = calendar_days(ix, calendar[calendar.get_loc(ix[0])-1])
    comp = economic_components(daily.loc[ix,'SP500_1X'], daily.loc[ix,'UPRO_BASE'],
                               days, 3, config['funds']['UPRO']['expense'])
    for lag, frame in states.items():
        frame = frame.loc[ix]
        for name in frame:
            p = frame[name]
            for s, label in [(1,'favorable'), (0,'unfavorable')]:
                mask = p.eq(s)
                u, l = daily.loc[ix,'SP500_1X'][mask], daily.loc[ix,'UPRO_BASE'][mask]
                c = comp.loc[mask]
                conditional.append(dict(series=name, lag=f'LAG{lag}', state=label,
                    days=int(mask.sum()), fraction_days=mask.mean(),
                    mean_sp500_daily_return=u.mean(), mean_sp500_daily_log_return=np.log1p(u).mean(),
                    mean_upro_daily_return=l.mean(), mean_upro_daily_log_return=np.log1p(l).mean(),
                    annualized_sp500_log_return=252*np.log1p(u).mean(),
                    annualized_upro_log_return=252*np.log1p(l).mean(),
                    realized_volatility=u.std(ddof=1)*np.sqrt(252),
                    annualized_approx_path_drag=252*c.approx_path_drag.mean(),
                    annualized_exact_path_drag_log=252*c.path_drag_log.mean(),
                    annualized_financing_drag_log=252*c.financing_drag_log.mean()))
        frame = frame.assign(UPRO_ALWAYS=1., SP500_1X=0.)
        for name in frame:
            p = frame[name]
            gross = select_returns(daily, p, {0:'SP500_1X', 1:'UPRO_BASE'})
            for cost in (0,25):
                r = switching_costs(gross,p,cost)
                meta = dict(series=name, lag=f'LAG{lag}', execution=LAGS[lag], switch_cost_bps=cost)
                metrics.append({**meta, **metric_row(r,p,daily,config,calendar,True)})
                for period,(start,end) in PERIODS.items():
                    if period == '2010_latest': continue
                    si = subperiod_index(ix,start,end)
                    row = metric_row(r.loc[si],p.loc[si],daily,config,calendar,False)
                    # Keep boundary switch and its cost from the continuous strategy.
                    row['switch_count'] = int(transitions(p).loc[si].sum())
                    row['switches_per_year'] = row['switch_count']/row['years']
                    subs.append({**meta, 'period':period, **row})
    metrics, subs, conditional = map(pd.DataFrame,(metrics,subs,conditional))
    # Cross-check the signal-independent comparators against the committed
    # falsification battery, rather than rerunning it. UPRO_ALWAYS and SP500_1X
    # depend only on the comparison window, which is unchanged, so they must
    # still match exactly; a drift here means the window or the economics moved.
    # VOL_BINARY and the SMA are deliberately NOT checked: they now read the
    # price index rather than total-return levels, so they are expected to
    # differ from the legacy CSV. That difference is the correction.
    old = pd.read_csv(root/'reports/volatility_target_comparison.csv')
    checks = 0
    for _, row in metrics[metrics.series.isin(['UPRO_ALWAYS','SP500_1X'])].iterrows():
        ref = old[(old.series==row.series)&(old.lag==row.lag)&(old.switch_cost_bps==row.switch_cost_bps)].iloc[0]
        for key in ('cagr','terminal_multiple','average_equity_exposure','switch_count'):
            if not np.isclose(row[key], ref[key], rtol=2e-9, atol=1e-10):
                raise ValueError(f'Prior comparator changed: {row.series} {key}')
        checks += 1
    agree = []
    for lag, frame in states.items():
        frame = frame.loc[ix]
        for a in frame:
            for b in frame:
                agree.append(dict(lag=f'LAG{lag}',signal_a=a,signal_b=b,days=len(ix),
                                  agreement_fraction=frame[a].eq(frame[b]).mean()))
    out = root/'reports'
    for suffix, data in [('metrics',metrics),('subperiods',subs),('states',conditional),
                         ('agreement',pd.DataFrame(agree))]:
        data.pipe(stable_floats).to_csv(out/f'regime_signal_{suffix}.csv', index=False, float_format=FLOAT_FORMAT)
    from .regime_signal_report import write_report
    write_report(root, metrics, subs, conditional, pd.DataFrame(agree), ao_note, checks)
    print(f'Regime comparison: {len(metrics)} full-history rows, {len(subs)} subperiod rows; '
          f'{ix[0].date()}–{ix[-1].date()}; {checks} prior comparator rows verified.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path.cwd())
    args = parser.parse_args()
    run(args.root)


if __name__ == '__main__': main()
