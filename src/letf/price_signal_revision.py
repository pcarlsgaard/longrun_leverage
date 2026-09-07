"""Price-only signal correction; investment returns remain distribution-inclusive total returns."""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from .analysis import CASH, FAMILIES, REGIMES, load_inputs, matched, sma_position
from .falsification import (
    COSTS, LAGS, PERIODS, attribution, economic_components, evaluate, level_position,
    load_price_signals, select_returns, stress_detail, subperiod_index, switching_costs,
    transitions,
)
from .model import calendar_days
from .signals import signal_price_return as price_return, volatility_position as price_volatility_position
from .regime_signals import archived_prices, matched_control, signal_features

SMA_LENGTHS = (150, 200, 250)
SPREADS = (0, 50, 100)
PRIMARY_COST = 25


def alternative_states(daily, signal_price, signal_price_return, median_price, lag):
    features = signal_features(signal_price, signal_price_return, median_price)
    def state(condition, required):
        return condition.astype(float).where(features[required].notna().all(axis=1)).shift(lag)
    vol, _ = price_volatility_position(signal_price_return, lag=lag, binary=True)
    out = {
        'UPRO_SMA_TO_SP500': level_position(signal_price, daily.index, 200, lag),
        'VOL_BINARY': (vol - 1) / 2,
        'REL_VOL': state(features.relative_volatility <= 1., ['relative_volatility']),
        'EFFICIENCY': state((features.ser > 0) & (features.er >= .25), ['ser', 'er']),
        'TREND_QUALITY': state((features.slope > 0) & (features.slope_t >= 2), ['slope', 'slope_t']),
        'LOW_CHURN': state(features.flip_rate < features.flip_median, ['flip_rate', 'flip_median']),
    }
    if 'ao' in features:
        out['AO'] = state(features.ao > 0, ['ao'])
    return pd.DataFrame(out, index=daily.index)


def strategy_metrics(r, cash, calendar, p=None, cohorts=True):
    return evaluate(r.rename('strategy_total_return'), cash, calendar, p, cohorts=cohorts)


def run(root: Path):
    daily, config = load_inputs(root, offline=True)
    calendar, cash = daily.index, daily[CASH]
    prices = load_price_signals(root, config, offline=True)
    signal_returns = {u: price_return(prices[u]).reindex(calendar) for u in prices}

    # Canonical price-only SMA states. Keep legacy TR states only for the before/after audit.
    price_pos = {(u, n, lag): level_position(prices[u], calendar, n, lag)
                 for u in set(FAMILIES.values()) for n in SMA_LENGTHS for lag in LAGS}
    legacy_tr_pos = {(u, n, lag): sma_position(daily[f'{u}_1X'], calendar, n).shift(lag-1)
                     for u in set(FAMILIES.values()) for n in SMA_LENGTHS for lag in LAGS}

    ix = matched(pd.concat([
        daily[['SP500_1X','NASDAQ100_1X','LONG_TREASURY_1X',CASH]],
        price_pos[('SP500',250,2)], price_pos[('NASDAQ100',250,2)]
    ], axis=1)).index
    reports = root / 'reports'

    # Full SMA robustness/falsification grid: signal from price, wealth from total return.
    grid_rows, primary, primary_states = [], {}, {}
    for fund, under in FAMILIES.items():
        off_label = 'NASDAQ' if under == 'NASDAQ100' else 'SP500'
        for n in SMA_LENGTHS:
            for spread in SPREADS:
                for lag in LAGS:
                    p = price_pos[(under,n,lag)].loc[ix]
                    for cost in COSTS:
                        for label, off in [('ALWAYS', None), (f'SMA_TO_{off_label}', f'{under}_1X'), ('SMA_TO_TBILL', CASH)]:
                            name = f'{fund}_{label}'
                            state = pd.Series(1., index=ix) if off is None else p
                            gross = daily.loc[ix,f'{fund}_SPREAD_{spread}BP'] if off is None else select_returns(
                                daily, state, {0:off,1:f'{fund}_SPREAD_{spread}BP'})
                            net = switching_costs(gross, state, cost)
                            row = dict(series=name, family=under, sma_days=n, spread_bps=spread,
                                       lag=f'LAG{lag}', execution=LAGS[lag], switch_cost_bps=cost,
                                       signal_source='PRICE_INDEX', strategy_return_source='TOTAL_RETURN')
                            row.update(strategy_metrics(net,cash,calendar,state,True))
                            grid_rows.append(row)
                            if n == 200 and spread == 50:
                                primary[(name,lag,cost)] = net
                                primary_states[(name,lag,cost)] = state
    grid = pd.DataFrame(grid_rows)
    grid.to_csv(reports/'price_signal_revision_sma_grid.csv', index=False, float_format='%.12g')

    # Historical subperiods using live state across boundaries.
    subs=[]
    for fund,under in FAMILIES.items():
        names=[f'{under}_1X',f'{fund}_ALWAYS',f'{fund}_SMA_TO_'+('NASDAQ' if under=='NASDAQ100' else 'SP500'),f'{fund}_SMA_TO_TBILL']
        for lag in LAGS:
            for name in names:
                if name.endswith('_1X'):
                    r=daily.loc[ix,name]; p=None
                else:
                    r=primary[(name,lag,0)]; p=primary_states[(name,lag,0)]
                for period,(start,end) in PERIODS.items():
                    si=subperiod_index(ix,start,end)
                    m=strategy_metrics(r.loc[si],cash,calendar,p.loc[si] if p is not None else None,False)
                    subs.append(dict(series=name,family=under,lag=f'LAG{lag}',period=period,
                                     signal_source='PRICE_INDEX',**m))
    pd.DataFrame(subs).to_csv(reports/'price_signal_revision_subperiods.csv',index=False,float_format='%.12g')

    # Price-return volatility state diagnostics and price-volatility targeting.
    volstates=[]
    state=price_pos[('SP500',200,1)].loc[ix]
    days=calendar_days(ix,calendar[calendar.get_loc(ix[0])-1])
    comp=economic_components(daily.loc[ix,'SP500_1X'],daily.loc[ix,'UPRO_BASE'],days,3,config['funds']['UPRO']['expense'])
    for window in (20,60):
        vol=signal_returns['SP500'].rolling(window).std(ddof=1).shift(1).loc[ix]*np.sqrt(252)
        for s,label in [(1,'above'),(0,'below')]:
            mask=state.eq(s); v=vol[mask]
            volstates.append(dict(window=window,state=label,days=int(mask.sum()),
                mean_signal_price_volatility=v.mean(),median_signal_price_volatility=v.median(),
                mean_strategy_total_return=daily.loc[ix,'UPRO_BASE'][mask].mean(),
                annualized_upro_log_return=252*np.log1p(daily.loc[ix,'UPRO_BASE'][mask]).mean(),
                annualized_sp500_total_return_log=252*np.log1p(daily.loc[ix,'SP500_1X'][mask]).mean(),
                annualized_exact_path_drag_log=252*comp.path_drag_log[mask].mean()))
    pd.DataFrame(volstates).to_csv(reports/'price_signal_revision_volatility_states.csv',index=False,float_format='%.12g')

    volrows=[]
    for lag in LAGS:
        for binary in (False,True):
            name='VOL_BINARY' if binary else 'VOL_TARGET_20'
            p,_=price_volatility_position(signal_returns['SP500'],lag=lag,binary=binary)
            p=p.loc[ix]
            gross=select_returns(daily,p,{1:'SP500_1X',2:'SSO_BASE',3:'UPRO_BASE'})
            for cost in COSTS:
                r=switching_costs(gross,p,cost)
                m=strategy_metrics(r,cash,calendar,p,True)
                volrows.append(dict(series=name,lag=f'LAG{lag}',switch_cost_bps=cost,
                                    signal_source='PRICE_RETURN',strategy_return_source='TOTAL_RETURN',
                                    average_equity_exposure=p.mean(),fraction_1x=p.eq(1).mean(),
                                    fraction_2x=p.eq(2).mean(),fraction_3x=p.eq(3).mean(),**m))
    pd.DataFrame(volrows).to_csv(reports/'price_signal_revision_volatility_targets.csv',index=False,float_format='%.12g')

    # Alternative regime signals, all driven by price/index inputs.
    sp_price, median_price, ao_note = archived_prices(root,config,calendar)
    alt_rows=[]; alt_sub=[]; alt_states=[]
    for lag in LAGS:
        frame=alternative_states(daily,sp_price,signal_returns['SP500'],median_price,lag).loc[ix]
        frame=frame.assign(UPRO_ALWAYS=1.,SP500_1X=0.)
        for name in frame:
            p=frame[name]
            gross=select_returns(daily,p,{0:'SP500_1X',1:'UPRO_BASE'})
            for cost in (0,25):
                r=switching_costs(gross,p,cost)
                m=strategy_metrics(r,cash,calendar,p,True)
                # Matched average leverage control; fees equalized as in prior experiment.
                u,l=daily.loc[ix,'SP500_1X'],daily.loc[ix,'UPRO_BASE']
                ctl,neutral=matched_control(u,l,days,config['funds']['UPRO']['expense'],p)
                cm=strategy_metrics(ctl,cash,calendar,None,False); nm=strategy_metrics(neutral,cash,calendar,None,False)
                alt_rows.append(dict(series=name,lag=f'LAG{lag}',switch_cost_bps=cost,
                    signal_source='PRICE_ONLY',strategy_return_source='TOTAL_RETURN',average_equity_exposure=1+2*p.mean(),
                    timing_cagr_difference=m['cagr']-cm['cagr'],fee_equal_timing_cagr_difference=m['cagr']-nm['cagr'],**m))
                for period,(start,end) in PERIODS.items():
                    if period=='2010_latest': continue
                    si=subperiod_index(ix,start,end)
                    sm=strategy_metrics(r.loc[si],cash,calendar,p.loc[si],False)
                    alt_sub.append(dict(series=name,lag=f'LAG{lag}',switch_cost_bps=cost,period=period,**sm))
            for s,label in [(1,'favorable'),(0,'unfavorable')]:
                mask=p.eq(s); pr=signal_returns['SP500'].loc[ix][mask]
                alt_states.append(dict(series=name,lag=f'LAG{lag}',state=label,days=int(mask.sum()),fraction_days=mask.mean(),
                    signal_price_volatility=pr.std(ddof=1)*np.sqrt(252),
                    sp500_total_return_log=252*np.log1p(daily.loc[ix,'SP500_1X'][mask]).mean(),
                    upro_total_return_log=252*np.log1p(daily.loc[ix,'UPRO_BASE'][mask]).mean()))
    pd.DataFrame(alt_rows).to_csv(reports/'price_signal_revision_regime_metrics.csv',index=False,float_format='%.12g')
    pd.DataFrame(alt_sub).to_csv(reports/'price_signal_revision_regime_subperiods.csv',index=False,float_format='%.12g')
    pd.DataFrame(alt_states).to_csv(reports/'price_signal_revision_regime_states.csv',index=False,float_format='%.12g')

    # Attribution uses price-only state but actual total-return investment economics.
    attrs=[]
    for fund in ('SSO','UPRO'):
        spec=config['funds'][fund]
        c=economic_components(daily.loc[ix,'SP500_1X'],daily.loc[ix,f'{fund}_BASE'],days,spec['leverage'],spec['expense'])
        for lag in LAGS:
            p=price_pos[('SP500',200,lag)].loc[ix]
            for kind,label in [('1x','SP500'),('tbill','TBILL')]:
                for row in attribution(c,p,cash,spec['leverage'],kind):
                    attrs.append(dict(series=f'{fund}_SMA_TO_{label}',lag=f'LAG{lag}',signal_source='PRICE_INDEX',**row))
    pd.DataFrame(attrs).to_csv(reports/'price_signal_revision_attribution.csv',index=False,float_format='%.12g')

    # Before/after audit for canonical UPRO -> SP500 rule and stress timing.
    audit=[]
    stress=[]
    for lag in LAGS:
        old=legacy_tr_pos[('SP500',200,lag)].loc[ix]
        new=price_pos[('SP500',200,lag)].loc[ix]
        mismatch=old.ne(new).mean()
        differing=int(transitions(old).ne(transitions(new)).sum())
        for source,p in [('TOTAL_RETURN_SIGNAL_LEGACY',old),('PRICE_ONLY_SIGNAL_REVISED',new)]:
            gross=select_returns(daily,p,{0:'SP500_1X',1:'UPRO_BASE'})
            r=switching_costs(gross,p,0)
            m=strategy_metrics(r,cash,calendar,p,True)
            audit.append(dict(lag=f'LAG{lag}',source=source,signal_disagreement_fraction=mismatch,
                              differing_transition_dates=differing,fraction_days_leveraged=p.mean(),**m))
        raw_old=old.shift(-lag); raw_new=new.shift(-lag)
        for event,(start,end) in REGIMES.items():
            ro=stress_detail(select_returns(daily,old,{0:'SP500_1X',1:'UPRO_BASE'}),old,raw_old,calendar,start,end,lag)
            rn=stress_detail(select_returns(daily,new,{0:'SP500_1X',1:'UPRO_BASE'}),new,raw_new,calendar,start,end,lag)
            stress.append(dict(lag=f'LAG{lag}',event=event,
                legacy_first_riskoff_signal=ro['first_below_sma_signal_close'],revised_first_riskoff_signal=rn['first_below_sma_signal_close'],
                legacy_max_drawdown=ro['episode_max_drawdown'],revised_max_drawdown=rn['episode_max_drawdown'],
                legacy_episode_end_value=ro['episode_end_value'],revised_episode_end_value=rn['episode_end_value']))
    audit=pd.DataFrame(audit); stress=pd.DataFrame(stress)
    audit.to_csv(reports/'price_signal_revision_audit.csv',index=False,float_format='%.12g')
    stress.to_csv(reports/'price_signal_revision_stress_audit.csv',index=False,float_format='%.12g')

    # Compact interpretation report.
    l1=audit[audit.lag.eq('LAG1')].set_index('source'); l2=audit[audit.lag.eq('LAG2')].set_index('source')
    def pct(x): return f'{100*x:.2f}%'
    def mult(x): return f'{x:,.1f}×'
    def line(label,old,new): return f'| {label} | {old} | {new} |'
    old1,new1=l1.loc['TOTAL_RETURN_SIGNAL_LEGACY'],l1.loc['PRICE_ONLY_SIGNAL_REVISED']
    old2,new2=l2.loc['TOTAL_RETURN_SIGNAL_LEGACY'],l2.loc['PRICE_ONLY_SIGNAL_REVISED']
    material=[]
    for _,r in stress.iterrows():
        if r.legacy_first_riskoff_signal != r.revised_first_riskoff_signal:
            material.append(f"- {r['event']} {r['lag']}: legacy first risk-off {r.legacy_first_riskoff_signal or 'none'}; price-only {r.revised_first_riskoff_signal or 'none'}.")
    regime=pd.DataFrame(alt_rows)
    leaders=(regime[(regime.lag=='LAG2')&(regime.switch_cost_bps==25)]
             .sort_values('fee_equal_timing_cagr_difference',ascending=False)[['series','cagr','fee_equal_timing_cagr_difference']].head(4))
    report = [
        '# Price-only signal revision', '',
        '**Methodological correction:** all trend, SMA, realized-volatility, volatility-target, momentum, trend-quality and path/choppiness signals in this revision use unadjusted price-index data only. Strategy wealth, CAGR, drawdown, Sharpe and portfolio volatility continue to use distribution-inclusive total returns.', '',
        '- S&P signal input: S&P 500 price index close.',
        '- Nasdaq signal input: Nasdaq-100 price index close.',
        '- `signal_price_return`: close-to-close price-index return, excluding distributions.',
        '- `strategy_total_return`: actual simulated investment total return, including distributions.', '',
        '## Primary UPRO 200-day SMA before/after audit', '',
        '| Metric | Legacy total-return signal | Revised price-only signal |','|---|---:|---:|',
        line('LAG1 CAGR',pct(old1.cagr),pct(new1.cagr)),
        line('LAG1 terminal wealth',mult(old1.terminal_multiple),mult(new1.terminal_multiple)),
        line('LAG1 max drawdown',pct(old1.max_drawdown),pct(new1.max_drawdown)),
        line('LAG1 Sharpe',f'{old1.sharpe:.3f}',f'{new1.sharpe:.3f}'),
        line('LAG1 fraction leveraged',pct(old1.fraction_days_leveraged),pct(new1.fraction_days_leveraged)),
        line('LAG1 switches/year',f'{old1.switches_per_year:.2f}',f'{new1.switches_per_year:.2f}'),
        line('LAG1 worst rolling 20y CAGR',pct(old1.worst_rolling_20y_cagr),pct(new1.worst_rolling_20y_cagr)),
        line('LAG1 worst rolling 30y CAGR',pct(old1.worst_rolling_30y_cagr),pct(new1.worst_rolling_30y_cagr)),
        line('LAG2 CAGR',pct(old2.cagr),pct(new2.cagr)),
        line('LAG2 terminal wealth',mult(old2.terminal_multiple),mult(new2.terminal_multiple)), '',
        f"Price and legacy total-return SMA states differ on **{pct(old1.signal_disagreement_fraction)}** of matched trading days (LAG1); transition-state flags differ on **{int(old1.differing_transition_dates)}** dates.", '',
        '## Stress-event timing differences', '',
        *(material or ['No named stress event had a different first risk-off signal date.']), '',
        '## Revised regime-signal ranking (LAG2, 25 bp)', '',
        '| Signal | CAGR | Equal-fee matched-leverage timing Δ |','|---|---:|---:|',
        *[f"| {r.series} | {pct(r.cagr)} | {100*r.fee_equal_timing_cagr_difference:+.2f} pp |" for _,r in leaders.iterrows()], '',
        '## Interpretation', '',
        'This is a methodological correction, not an optimized model. The conclusion should be judged by whether price-only signals retain the same direction of leverage-management benefit across SMA lengths, execution lags, costs, financing spreads, subperiods and long cohorts. The structured CSVs contain the complete corrected battery.', '',
        f'AO note: {ao_note}',
    ]
    (reports/'price_signal_revision_results.md').write_text('\n'.join(report)+'\n')
    print(f'Price-only revision complete: {ix[0].date()}–{ix[-1].date()}, {len(grid)} SMA grid rows.')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=Path.cwd())
    args=p.parse_args(); run(args.root)

if __name__=='__main__': main()
