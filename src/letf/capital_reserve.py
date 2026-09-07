"""Prespecified capital-reserve experiment; consumes the verified frozen bundle."""
import argparse
from dataclasses import asdict, replace
import json
from pathlib import Path
import platform

import numpy as np
import pandas as pd

from .analysis import CASH, REGIMES, load_inputs, matched, path, sha
from .falsification import COSTS, PERIODS, evaluate, load_price_signals, subperiod_index
from .signals import level_position
from .model import calendar_days
from .cohorts import cohort_frame, nav_path
from .reserve import ReserveRule, simulate_reserve, full_cycle_accounting, tagged_deployment_value
from .provenance import FLOAT_FORMAT, source_hashes, stable_floats

PRIMARY_RULES = {
    'NO_RESERVE': ReserveRule(),
    'FIXED_90_10': ReserveRule('fixed', fixed=.10),
    'FIXED_85_15': ReserveRule('fixed', fixed=.15),
    'HWM_RESERVE': ReserveRule('hwm'),
    'ASYMMETRIC_RESERVE_BAND': ReserveRule('band', initial=.10, cap=.20),
    'HWM_DRAWDOWN': ReserveRule('hwm', deployment='drawdown'),
    'BAND_DRAWDOWN': ReserveRule('band', deployment='drawdown', initial=.10, cap=.20),
}


def exposure_metrics(a, rule):
    w=a.start_reserve_weight
    e=a.effective_equity_exposure
    return dict(average_reserve_weight=w.mean(), median_reserve_weight=w.median(),
        fraction_reserve_zero=np.isclose(w,0,atol=1e-10).mean(),
        fraction_reserve_at_cap=np.isclose(w,rule.cap,atol=1e-8).mean() if rule.kind in ('hwm','band') else np.nan,
        fraction_reserve_above_cap=(w>rule.cap+1e-8).mean() if rule.kind in ('hwm','band') else np.nan,
        average_risky_exposure=(1-w).mean(), average_effective_equity_exposure=e.mean(),
        average_leverage=e.mean(),
        average_total_safe_asset_weight=(w+(1-w)*(a.sleeve_leverage==0)).mean(), average_risky_sleeve_leverage=a.sleeve_leverage.mean(),
        fraction_at_1x=np.isclose(e,1,atol=1e-8).mean(),
        fraction_at_2x=np.isclose(e,2,atol=1e-8).mean(),
        fraction_at_3x=np.isclose(e,3,atol=1e-8).mean(),
        fraction_sleeve_at_1x=(a.sleeve_leverage==1).mean(),
        fraction_sleeve_at_2x=(a.sleeve_leverage==2).mean(),
        fraction_sleeve_at_3x=(a.sleeve_leverage==3).mean())


def rolling_summary(a, calendar):
    """Month-end cohorts on the running policy; see :mod:`letf.cohorts`.

    These cohorts buy units in an already-running policy, so reserve state is
    inherited. `reserve_cohorts` separately exports fresh-investor cohorts that
    restart policy state at entry.
    """
    nav=nav_path(a.return_net,calendar)
    rows=[]
    for y in (20,30):
        frame=cohort_frame(nav,y)
        ratio,annual=frame.multiple.to_numpy(),frame.cagr.to_numpy()
        rows.append(dict(horizon_years=y,cohorts=len(frame),
            min_terminal_multiple=ratio.min() if len(ratio) else np.nan,
            median_terminal_multiple=np.median(ratio) if len(ratio) else np.nan,
            min_cagr=annual.min() if len(annual) else np.nan,
            median_cagr=np.median(annual) if len(annual) else np.nan,
            cohort_kind='inherited_policy_state'))
    return rows


def stress_cycles(a, baseline, trades, underlying_nav, calendar, leverage):
    rows=[]
    previous_market_recovery=None
    prior=calendar[calendar.get_loc(a.index[0])-1]
    nav=path(a.return_net,prior); no=path(baseline.return_net,prior)
    for event,(start,end) in REGIMES.items():
        segment=underlying_nav.loc[start:end]
        if len(segment)<2: continue
        dd=segment/underlying_nav.cummax().loc[segment.index]-1
        trough=dd.idxmin()
        peak=underlying_nav.loc[:trough].idxmax()
        if peak<prior: continue
        low=a.loc[trough]
        target=nav.loc[:trough].max()
        recovered=nav.loc[trough:][nav.loc[trough:]>=target]
        no_target=no.loc[:trough].max()
        no_recovered=no.loc[trough:][no.loc[trough:]>=no_target]
        recovered_date=recovered.index[0] if len(recovered) else pd.NaT
        no_date=no_recovered.index[0] if len(no_recovered) else pd.NaT
        market_recovered=underlying_nav.loc[trough:][underlying_nav.loc[trough:]>=underlying_nav.loc[peak]]
        market_date=market_recovered.index[0] if len(market_recovered) else pd.NaT
        cycle_start=previous_market_recovery if previous_market_recovery is not None and previous_market_recovery<peak else prior
        previous_market_recovery=market_date if pd.notna(market_date) else None
        local_scale=nav.loc[cycle_start]/no.loc[cycle_start]
        base=dict(cycle_start=cycle_start,event=event,underlying_peak=peak,underlying_trough=trough,
            underlying_drawdown=dd.min(), pre_no_reserve_wealth=no.loc[peak],pre_reserve_wealth=nav.loc[peak],
            cumulative_gains_diverted=a.accumulation.loc[:peak].sum(),
            cumulative_cash_income=a.reserve_cash_income.loc[:peak].sum(),
            cumulative_pre_drawdown_log_growth_foregone=np.log(no.loc[peak]/nav.loc[peak]),
            trough_risky_capital=low.risky_wealth,trough_reserve_capital=low.reserve_wealth,
            trough_total_capital=low.wealth,trough_reserve_weight=low.reserve_weight,
            dry_powder_ratio=low.reserve_wealth/low.risky_wealth,
            trough_capital_preservation=low.wealth/no.loc[trough],
            maximum_additional_equity_notional_at_1x=low.reserve_wealth,
            maximum_additional_equity_notional_at_favorable_leverage=leverage*low.reserve_wealth,
            reserve_portfolio_recovery_date=recovered_date,no_reserve_recovery_date=no_date,
            reserve_recovery_days=(recovered_date-trough).days if pd.notna(recovered_date) else np.nan,
            no_reserve_recovery_days=(no_date-trough).days if pd.notna(no_date) else np.nan,
            underlying_recovery_date=market_date)
        points={f'{y}y':trough+pd.DateOffset(years=y) for y in (1,3,5)}
        points.update(prior_portfolio_high=recovered_date,underlying_recovery=market_date,
                      end_of_regime=segment.index[-1])
        for label,date in points.items():
            if pd.isna(date) or date>nav.index[-1]:
                rows.append({**base,'horizon':label,'available':False})
                continue
            date=nav.index[nav.index.searchsorted(date)]
            # End-of-regime may precede the 1/3/5y recovery checkpoints.
            d=trades[(trades.reason=='deployment') & (trades.execution_close>=peak)
                     & (trades.execution_close<date)] if len(trades) else trades
            deployed=d.net_deployed.sum() if len(d) else 0.
            tagged=sum(tagged_deployment_value(a,t,a.index.get_loc(date)) for _,t in d.iterrows()) if len(d) else 0.
            cash_cf=0.
            for _,t in d.iterrows():
                # This is gross interest-only alternative for each deployed lot.
                cash_cf += t.net_deployed*np.prod(1+a.attrs['cash'].iloc[int(t.row):a.index.get_loc(date)+1])
            notional=sum(t.net_deployed*(leverage if t.favorable else 1) for _,t in d.iterrows()) if len(d) else 0.
            rows.append({**base,'horizon':label,'available':True,'evaluation_date':date,
                'reserve_wealth_after':nav.loc[date],'no_reserve_wealth_after':no.loc[date],
                **full_cycle_accounting(no.loc[peak],nav.loc[peak],no.loc[date],nav.loc[date]),
                **{'cycle_local_'+k:v for k,v in full_cycle_accounting(no.loc[peak]*local_scale,nav.loc[peak],
                    no.loc[date]*local_scale,nav.loc[date]).items()},
                'deployed_capital':deployed,'gross_reconstructed_equity_notional':notional,
                'remaining_tagged_deployed_risky_wealth':tagged,
                'deployed_lots_cash_counterfactual':cash_cf,
                'remaining_tagged_wealth_less_cash_counterfactual':tagged-cash_cf,
                'deployment_count':len(d)})
    return rows


def bull_episodes(a, baseline, calendar):
    p=a.favorable.to_numpy()
    bounds=np.r_[0,np.flatnonzero(p[1:]!=p[:-1])+1,len(p)]
    rows=[]
    for first,last in zip(bounds[:-1],bounds[1:]):
        if not p[first] or last-first<60: continue
        r=a.return_net.iloc[first:last]; b=baseline.return_net.iloc[first:last]
        prior=calendar[calendar.get_loc(r.index[0])-1]
        years=(r.index[-1]-prior).days/365.25
        previous=a.wealth.shift(1,fill_value=1).iloc[first:last]
        gains=(a.wealth.iloc[first:last]-previous).clip(lower=0).sum()
        rows.append(dict(start=prior,end=r.index[-1],sessions=len(r),
            reserve_cagr=(1+r).prod()**(1/years)-1,
            no_reserve_total_return=(1+b).prod()-1,
            no_reserve_cagr=(1+b).prod()**(1/years)-1,
            cumulative_log_growth_foregone=np.log1p(b).sum()-np.log1p(r).sum(),
            transferred_to_reserve=a.accumulation.iloc[first:last].sum(),
            fraction_positive_portfolio_gains_transferred=a.accumulation.iloc[first:last].sum()/gains if gains else np.nan,
            new_hwm_gains_recognized=a.hwm_observed.diff().fillna(0).iloc[first:last].sum()))
    return rows


def run(root):
    daily,config=load_inputs(root,offline=True)
    calendar=daily.index
    prices=load_price_signals(root,config,offline=True)
    positions={lag:level_position(prices['SP500'],calendar,200,lag) for lag in (1,2)}
    # Same primary date window as prior falsification; no Nasdaq strategy tested.
    common=matched(pd.concat([daily[['SP500_1X','NASDAQ100_1X','LONG_TREASURY_1X',CASH]],
        level_position(prices['SP500'],calendar,250,2),
        level_position(prices['NASDAQ100'],calendar,250,2)],axis=1)).index
    underlying=path(daily.SP500_1X.dropna(),calendar[0])
    raw_dd=underlying/underlying.cummax()-1
    reports=root/'reports'; processed=root/'data/processed'
    metrics=[]; sensitivities=[]; exposures=[]; costs=[]; rolls=[]; cycles=[]; deployments=[]; bulls=[]
    saved={}; matched_rows=[]; subperiods=[]
    def record(a,t,rule,meta,is_primary=True,baseline=None,zero=None):
        if zero is None:
            zero=a if meta['cost_bps']==0 else simulate_reserve(risky,cash,p,dd,calendar,rule,lev,lag,0)[0]
        m={**meta,**evaluate(a.return_net,daily[CASH],calendar,cohorts=True),**exposure_metrics(a,rule)}
        cost=dict(reserve_accumulation_trades=int((a.accumulation>1e-15).sum()),
            reserve_deployment_trades=int((a.deployment>1e-15).sum()),
            reserve_rebalance_sell_trades=int(((a.transfer_to_reserve< -1e-15)&(a.deployment==0)).sum()),
            leverage_state_trades=int(a.state_switch.sum()),
            traded_transition_days=int((a.total_turnover>1e-15).sum()),
            total_charged_transitions=int((a.cost_fraction>0).sum()),
            reserve_turnover=a.reserve_turnover.sum(),risky_sleeve_turnover=a.risky_sleeve_turnover.sum(),
            net_asset_turnover=a.total_turnover.sum(),sum_dollar_costs=a.transaction_cost.sum(),
            direct_log_cost_drag=-np.log1p(-a.cost_fraction).sum(),
            direct_wealth_cost_drag=1-np.prod(1-a.cost_fraction),
            total_trading_cost_drag=1-a.wealth.iloc[-1]/zero.wealth.iloc[-1] if zero is not None else np.nan,
            cost_cagr_drag=(zero.wealth.iloc[-1]**(1/m['years'])-1-m['cagr']) if zero is not None else np.nan)
        m.update(cost)
        rs=rolling_summary(a,calendar)
        for row in rs:
            for field in ('min_terminal_multiple','median_terminal_multiple'):
                m[f'rolling_{row["horizon_years"]}y_{field}']=row[field]
        if is_primary:
            metrics.append(m); exposures.append({**meta,**exposure_metrics(a,rule)})
            costs.append({**meta,**cost}); rolls.extend({**meta,**v} for v in rs)
            if baseline is not None:
                for period,(start,end) in PERIODS.items():
                    ix=subperiod_index(a.index,start,end)
                    subperiods.append({**meta,'period':period,
                        **evaluate(a.return_net.loc[ix],daily[CASH],calendar,cohorts=False),
                        'terminal_ratio_vs_no_reserve':np.prod(1+a.return_net.loc[ix])/np.prod(1+baseline.return_net.loc[ix])})
                a.attrs['cash']=daily.loc[a.index,CASH]
                cycles.extend({**meta,**v} for v in stress_cycles(a,baseline,t,underlying,calendar,3 if meta['fund']=='UPRO' else 2))
                bulls.extend({**meta,**v} for v in bull_episodes(a,baseline,calendar))
            if len(t):
                d=t[t.reason=='deployment'].copy()
                for k,v in meta.items(): d[k]=v
                d['asset']=np.where(d.favorable,meta['fund'],'SP500_1X')
                d['remaining_tagged_risky_wealth_at_sample_end']=[tagged_deployment_value(a,v,len(a)-1) for _,v in d.iterrows()]
                deployments.extend(d.to_dict('records'))
        else: sensitivities.append(m)
        return m
    for fund in ('UPRO','SSO'):
        lev=config['funds'][fund]['leverage']
        for lag in (1,2):
            print(f'Reserve comparisons: {fund} LAG{lag}',flush=True)
            p=positions[lag].loc[common]
            risky=daily.loc[common,f'{fund}_BASE'].where(p.eq(1),daily.loc[common,'SP500_1X'])
            cash=daily.loc[common,CASH]; dd=raw_dd.shift(lag).loc[common]
            zero={}; all_runs={}
            def sim(rule,cost,returns=risky,state=p,off=1):
                return simulate_reserve(returns,cash,state,dd,calendar,rule,lev,lag,cost,off)
            for cost in COSTS:
                for name,rule in PRIMARY_RULES.items():
                    a,t=sim(rule,cost); all_runs[(name,cost)]=(a,t)
                    if cost==0: zero[name]=a
                    base=all_runs[('NO_RESERVE',cost)][0]
                    meta=dict(series=name,fund=fund,lag=f'LAG{lag}',cost_bps=cost,
                              deployment=rule.deployment,cap=rule.cap,harvest=rule.harvest,
                              rebalance=rule.frequency if rule.kind=='fixed' else 'rule')
                    record(a,t,rule,meta,baseline=base,zero=zero[name])
                    if fund=='UPRO' and cost==0: saved[(name,lag)]=a
                # Necessary underlying/leverage-rule benchmarks share exact dates.
                for name,r,state,off in [
                    ('SP500_1X',daily.loc[common,'SP500_1X'],p*0,1),
                    (f'{fund}_ALWAYS',daily.loc[common,f'{fund}_BASE'],p*0+1,1),
                    (f'{fund}_SMA_TO_TBILL',daily.loc[common,f'{fund}_BASE'].where(p.eq(1),cash),p,0)]:
                    a,t=sim(ReserveRule(),cost,r,state,off)
                    if cost==0: zero[name]=a
                    record(a,t,ReserveRule(),dict(series=name,fund=fund,lag=f'LAG{lag}',cost_bps=cost,
                           deployment='none',cap=0,harvest=0,rebalance='none'),zero=zero[name])
                for name in ('HWM_RESERVE','ASYMMETRIC_RESERVE_BAND','HWM_DRAWDOWN','BAND_DRAWDOWN'):
                    a,_=all_runs[(name,cost)]
                    weight=float(np.floor(a.start_reserve_weight.mean()/.05+.5)*.05)
                    rule=ReserveRule('fixed',fixed=weight)
                    f,t=sim(rule,cost)
                    exact_fixed,_=sim(ReserveRule('fixed',fixed=float(a.start_reserve_weight.mean())),cost)
                    effective=float(a.effective_equity_exposure.mean())
                    # Infer archived funding; no downloads or core series rebuild.
                    days=calendar_days(common,calendar[calendar.get_loc(common[0])-1])
                    fee=config['funds'][fund]['expense']*days/365
                    funding=(lev*daily.loc[common,'SP500_1X']-daily.loc[common,f'{fund}_BASE']-fee)/(lev-1)
                    const=effective*daily.loc[common,'SP500_1X']-(effective-1)*funding-fee
                    cm=evaluate(const,cash,calendar)
                    fm=evaluate(f.return_net,cash,calendar)
                    matched_rows.append(dict(series=name,fund=fund,lag=f'LAG{lag}',cost_bps=cost,
                        average_reserve=a.start_reserve_weight.mean(),rounded_fixed_reserve=weight,
                        fixed_actual_average_reserve=f.start_reserve_weight.mean(),
                        dynamic_cagr=evaluate(a.return_net,cash,calendar,cohorts=False)['cagr'],
                        fixed_cagr=fm['cagr'],
                        exact_average_fixed_cagr=evaluate(exact_fixed.return_net,cash,calendar,cohorts=False)['cagr'],
                        exact_average_fixed_actual_reserve=exact_fixed.start_reserve_weight.mean(),
                        dynamic_to_exact_average_fixed_terminal_ratio=a.wealth.iloc[-1]/exact_fixed.wealth.iloc[-1],
                        dynamic_to_fixed_terminal_ratio=a.wealth.iloc[-1]/f.wealth.iloc[-1],
                        fixed_max_drawdown=fm['max_drawdown'],
                        dynamic_average_effective_exposure=effective,
                        fixed_average_effective_exposure=f.effective_equity_exposure.mean(),
                        constant_leverage_cagr=cm['cagr'],constant_leverage_max_drawdown=cm['max_drawdown'],
                        constant_leverage_terminal_multiple=cm['terminal_multiple']))
                    record(f,t,rule,dict(series=f'MATCHED_FIXED_FOR_{name}',fund=fund,lag=f'LAG{lag}',
                           cost_bps=cost,deployment='none',cap=weight,harvest=0,rebalance='quarterly'),False)
                for freq in ('monthly','annual'):
                    for weight in (.10,.15):
                        rule=ReserveRule('fixed',fixed=weight,frequency=freq)
                        a,t=sim(rule,cost)
                        record(a,t,rule,dict(series=f'FIXED_{round(100*(1-weight))}_{round(100*weight)}',
                            fund=fund,lag=f'LAG{lag}',cost_bps=cost,deployment='none',cap=weight,harvest=0,rebalance=freq),False)
                # Small 3x3 grid, staged HWM only. No rank-based selection.
                for cap in (.10,.15,.20):
                    for harvest in (.05,.10,.15):
                        rule=ReserveRule('hwm',cap=cap,harvest=harvest)
                        a,t=sim(rule,cost)
                        record(a,t,rule,dict(series='HWM_GRID',fund=fund,lag=f'LAG{lag}',cost_bps=cost,
                            deployment='staged',cap=cap,harvest=harvest,rebalance='rule'),False)
                # Hold-only control isolates the value of using accumulated bills.
                for name in ('HWM_RESERVE','ASYMMETRIC_RESERVE_BAND'):
                    rule=replace(PRIMARY_RULES[name],deployment='hold')
                    a,t=sim(rule,cost)
                    record(a,t,rule,dict(series=name+'_HOLD_ONLY',fund=fund,lag=f'LAG{lag}',cost_bps=cost,
                        deployment='hold',cap=rule.cap,harvest=rule.harvest,rebalance='rule'),False)
    # Link each favorable episode to the next named drawdown cycle; repeated
    # references are explicitly not additive profits of independent bull runs.
    for b in bulls:
        eligible=[c for c in cycles if all(c[k]==b[k] for k in ('series','fund','lag','cost_bps'))
                  and c.get('horizon')=='3y' and c.get('available') and c['underlying_peak']>=b['start']]
        if eligible:
            c=eligible[0]
            b.update(next_named_stress=c['event'],
                next_stress_3y_post_drawdown_relative_growth_benefit=c['cycle_local_incremental_post_drawdown_wealth'],
                next_stress_3y_carried_opportunity_cost=c['cycle_local_carried_opportunity_cost'],
                next_stress_3y_payback_ratio=c['cycle_local_reserve_payback_ratio'])
    frames=dict(metrics=metrics,sensitivity=sensitivities,stress_cycles=cycles,deployments=deployments,
                costs=costs,exposure=exposures,rolling_summary=rolls,matched_controls=matched_rows,bull_episodes=bulls,subperiods=subperiods)
    for label,rows in frames.items():
        pd.DataFrame(rows).pipe(stable_floats).to_csv(reports/f'capital_reserve_{label}.csv',index=False,float_format=FLOAT_FORMAT)
    for lag in (1,2):
        for field in ('wealth','reserve_wealth','reserve_weight','effective_equity_exposure','return_net'):
            pd.DataFrame({name:a[field] for (name,l),a in saved.items() if l==lag}).pipe(stable_floats).to_csv(
                processed/f'capital_reserve_{field}_lag{lag}.csv',index_label='date',float_format=FLOAT_FORMAT)
    manifest=dict(as_of=config['as_of'],entry_close=str(calendar[calendar.get_loc(common[0])-1].date()),
        input_hashes={rel:sha(root/rel) for rel in ('config.json','data/processed/daily_returns.csv',
            'data/raw/fred_DTB3.csv','data/snapshots/portfolio_sma_inputs.zip')},
        source_hashes=source_hashes(root, __name__),
        rules={name:asdict(rule) for name,rule in PRIMARY_RULES.items()},
        lags=[1,2],cost_bps=list(COSTS),cap_grid=[.10,.15,.20],harvest_grid=[.05,.10,.15],
        fixed_matching='Ex-post diagnostic: full-sample average rounded half-up to nearest 5%; not optimized',
        runtime=dict(python=platform.python_version(),numpy=np.__version__,pandas=pd.__version__))
    (reports/'capital_reserve_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    from .reserve_report import write_report
    write_report(root,saved,manifest)
    print('Capital reserve reports complete',flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path.cwd())
    parser.add_argument('--offline',action='store_true',help='Inputs are always frozen/offline')
    args=parser.parse_args(); run(args.root)

if __name__=='__main__': main()
